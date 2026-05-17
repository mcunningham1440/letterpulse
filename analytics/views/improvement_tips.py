"""
Improvement Tips endpoints: list posts available for analysis, kick off
a tips task, poll its status, and download the annotated HTML once done.
"""

import json
import logging

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_GET, require_POST

from ..models import PendingImprovementTips, Post, Publication
from ..utils import run_improvement_tips_background, spawn_background
from ._helpers import (
    get_user_api_credentials,
    require_valid_api_credentials,
    sanitize_filename,
)

logger = logging.getLogger(__name__)


@login_required
@require_GET
@require_valid_api_credentials
def improvement_tips_posts(request):
    """Return list of all posts for the improvement tips dropdown."""
    try:
        _, beehiiv_pub_id, _ = get_user_api_credentials(request.user)
        try:
            publication = Publication.objects.get(pub_id=beehiiv_pub_id)
        except Publication.DoesNotExist:
            return JsonResponse({'success': True, 'posts': []})

        all_posts = Post.objects.filter(
            user=request.user, publication=publication
        ).order_by('-creation_date')

        posts = []
        for post in all_posts:
            # Use creation_date uniformly: this dropdown spans Drafts/Scheduled/Published,
            # so a single field keeps the relative date ("1w ago") consistent across statuses.
            date_val = post.creation_date
            posts.append({
                'post_id': post.post_id,
                'title': post.title or '',
                'date_iso': date_val.date().isoformat() if date_val else None,
                'status': post.status or 'Published',
            })

        return JsonResponse({'success': True, 'posts': posts})
    except Exception:
        logger.exception("improvement_tips_posts failed")
        return JsonResponse({'success': False}, status=500)


@login_required
@require_POST
@require_valid_api_credentials
def run_improvement_tips(request):
    """Start an improvement tips background task."""
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    post_id = data.get('post_id')
    if not post_id:
        return JsonResponse({'success': False, 'error': 'post_id required'}, status=400)

    # Look up the post
    try:
        post = Post.objects.get(post_id=post_id, user=request.user)
    except Post.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Post not found'}, status=404)

    # Pre-flight credit check: surface the friendly toast before we create a
    # Pending* row. The actual charge happens inside spawn_background ->
    # task.claim() so a process crash between view and thread-start can't leak
    # credits.
    usage = request.user.usage_account
    usage.ensure_current_period()
    if usage.used_this_period + settings.CREDITS_PER_IMPROVEMENT_TIPS > usage.monthly_quota:
        return JsonResponse({'success': False, 'error': 'Not enough credits'}, status=400)

    # Get publication
    _, beehiiv_pub_id, _ = get_user_api_credentials(request.user)
    try:
        publication = Publication.objects.get(pub_id=beehiiv_pub_id)
    except Publication.DoesNotExist:
        publication = None

    # Create task
    task = PendingImprovementTips.objects.create(
        user=request.user,
        publication=publication,
        post=post,
    )

    spawn_background(
        PendingImprovementTips, task.task_id, run_improvement_tips_background,
    )

    return JsonResponse({'success': True, 'task_id': str(task.task_id)})


@login_required
@require_GET
def poll_improvement_tips(request, task_id):
    """Poll the status of an improvement tips task."""
    try:
        task = PendingImprovementTips.objects.get(task_id=task_id, user=request.user)
    except PendingImprovementTips.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Task not found'}, status=404)

    if task.status in PendingImprovementTips.SWEEPABLE_STATUSES:
        task.touch_heartbeat()

    usage = request.user.usage_account
    resp = {
        'success': True,
        'status': task.status,
        'credits_used': usage.used_this_period,
        'credits_quota': usage.monthly_quota,
    }

    if task.status == 'complete':
        resp['download_ready'] = True
        if settings.ENVIRONMENT == 'local' and task.dev_panel_data:
            resp['dev_panel'] = task.dev_panel_data
    elif task.status == 'error':
        resp['error_message'] = task.error_message

    return JsonResponse(resp)


@login_required
@require_GET
def download_improvement_tips(request, task_id):
    """Download the annotated HTML from a completed improvement tips task."""
    try:
        task = PendingImprovementTips.objects.get(task_id=task_id, user=request.user)
    except PendingImprovementTips.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Task not found'}, status=404)

    if task.status != 'complete' or not task.result_html:
        return JsonResponse({'success': False, 'error': 'Tips not ready'}, status=400)

    safe_title = sanitize_filename(task.post.title or 'post')[:50]
    filename = f"improvement_tips_{safe_title}.html"

    response = HttpResponse(task.result_html, content_type='text/html')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response

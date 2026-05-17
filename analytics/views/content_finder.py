"""
Content Finder endpoints: list available source posts, kick off a search
(plan → dispatch → search), confirm the plan, poll status, and capture
thumbs-up/down feedback on individual returned links.
"""

import json
import logging

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from ..models import (
    ContentSearchFeedback,
    PendingContentSearch,
    Post,
    ProcessedPost,
    Publication,
    Section,
)
from ..utils import run_content_finder_background, spawn_background
from ._helpers import get_user_api_credentials, require_valid_api_credentials

logger = logging.getLogger(__name__)


@login_required
@require_GET
@require_valid_api_credentials
def content_finder_posts(request):
    """Return list of processed posts for the content finder dropdown."""
    try:
        _, beehiiv_pub_id, _ = get_user_api_credentials(request.user)
        try:
            publication = Publication.objects.get(pub_id=beehiiv_pub_id)
        except Publication.DoesNotExist:
            return JsonResponse({'success': True, 'posts': []})

        processed = ProcessedPost.objects.filter(
            user=request.user, publication=publication
        ).select_related('post')

        posts = []
        for pp in processed:
            post = pp.post
            posts.append({
                'post_id': post.post_id,
                'title': post.title or '',
                'publish_date_iso': post.publish_date.date().isoformat() if post.publish_date else None,
                'publish_date_ts': post.publish_date.timestamp() if post.publish_date else 0,
            })

        posts.sort(key=lambda p: p['publish_date_ts'], reverse=True)
        for p in posts:
            p.pop('publish_date_ts', None)

        return JsonResponse({'success': True, 'posts': posts})
    except Exception:
        logger.exception("content_finder_posts failed")
        return JsonResponse({'success': False}, status=500)


@login_required
@require_POST
@require_valid_api_credentials
def run_content_finder(request):
    """Start a content finder background task (Stage 1: planning)."""
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    post_id = data.get('post_id')

    if not post_id:
        return JsonResponse({'success': False, 'error': 'post_id required'}, status=400)

    if not settings.PERPLEXITY_API_KEY:
        return JsonResponse({'success': False, 'error': 'Content Finder is not configured. Please contact support.'}, status=500)

    try:
        post = Post.objects.get(post_id=post_id, user=request.user)
    except Post.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Post not found'}, status=404)

    if not Section.objects.filter(post=post, user=request.user).exists():
        return JsonResponse({'success': False, 'error': 'No sections found for this post'}, status=400)

    # Pre-flight credit check: surface the friendly toast at the view layer
    # before we create a Pending* row. The actual charge happens atomically
    # inside spawn_background -> task.claim().
    usage = request.user.usage_account
    usage.ensure_current_period()
    if usage.used_this_period + settings.CREDITS_PER_CONTENT_SEARCH > usage.monthly_quota:
        return JsonResponse({'success': False, 'error': 'Not enough credits'}, status=400)

    _, beehiiv_pub_id, _ = get_user_api_credentials(request.user)
    try:
        publication = Publication.objects.get(pub_id=beehiiv_pub_id)
    except Publication.DoesNotExist:
        publication = None

    task = PendingContentSearch.objects.create(
        user=request.user,
        publication=publication,
        post=post,
    )

    spawn_background(
        PendingContentSearch,
        task.task_id,
        run_content_finder_background,
        running_status='planning',
    )

    return JsonResponse({'success': True, 'task_id': str(task.task_id)})


@login_required
@require_POST
@require_valid_api_credentials
def confirm_content_finder_plan(request, task_id):
    """Stage 2/3 kickoff: record the user's plan feedback and spawn dispatch + search."""
    try:
        task = PendingContentSearch.objects.get(task_id=task_id, user=request.user)
    except PendingContentSearch.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Task not found'}, status=404)

    if task.status != 'awaiting_feedback':
        return JsonResponse({'success': False, 'error': f'Task not ready for confirmation (status={task.status})'}, status=400)

    try:
        data = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    feedback = (data.get('feedback') or '').strip()
    task.user_feedback = feedback
    task.status = 'dispatching'
    task.last_heartbeat = timezone.now()
    task.save(update_fields=['user_feedback', 'status', 'last_heartbeat', 'updated_at'])

    # Re-spawn; the work fn branches on status and the wrapper's claim() is a
    # no-op since the task is past 'pending' (credits were already charged on
    # the initial claim into 'planning').
    spawn_background(
        PendingContentSearch,
        task.task_id,
        run_content_finder_background,
    )

    return JsonResponse({'success': True})


@login_required
@require_GET
def poll_content_finder(request, task_id):
    """Poll the status of a content finder task."""
    try:
        task = PendingContentSearch.objects.get(task_id=task_id, user=request.user)
    except PendingContentSearch.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Task not found'}, status=404)

    if task.status in PendingContentSearch.SWEEPABLE_STATUSES:
        task.touch_heartbeat()

    usage = request.user.usage_account
    resp = {
        'success': True,
        'status': task.status,
        'credits_used': usage.used_this_period,
        'credits_quota': usage.monthly_quota,
    }

    if task.status == 'awaiting_feedback':
        resp['plan_text'] = task.plan_text
    elif task.status == 'complete':
        resp['result_data'] = task.result_data
        if settings.ENVIRONMENT == 'local' and task.dev_panel_data:
            resp['dev_panel'] = task.dev_panel_data
    elif task.status == 'error':
        resp['error_message'] = task.error_message

    return JsonResponse(resp)


@login_required
@require_POST
def submit_content_search_feedback(request):
    """Save thumbs-up / thumbs-down feedback on a content finder link."""
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    url = data.get('url', '').strip()
    feedback_val = data.get('feedback', '').strip()

    if not url or feedback_val not in ('up', 'down'):
        return JsonResponse({'success': False, 'error': 'url and feedback (up/down) required'}, status=400)

    _, beehiiv_pub_id, _ = get_user_api_credentials(request.user)
    try:
        publication = Publication.objects.get(pub_id=beehiiv_pub_id)
    except Publication.DoesNotExist:
        publication = None

    ContentSearchFeedback.objects.update_or_create(
        user=request.user,
        publication=publication,
        url=url[:2000],
        defaults={
            'title': data.get('title', '')[:500],
            'source': data.get('source', '')[:255],
            'pub_date': data.get('date', '')[:100],
            'description': data.get('description', '')[:4096],
            'relevance': data.get('relevance', '')[:4096],
            'feedback': feedback_val,
        },
    )

    return JsonResponse({'success': True})

"""
Learning / Updating flow endpoints: start the initial 'Learning Your
Audience' task, start the per-page-load incremental update task, and
poll either of them.
"""

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST

from ..models import PendingLearningTask
from ..utils import run_initial_learning_task, run_update_task, spawn_background
from ._helpers import (
    _resolve_publication,
    get_user_api_credentials,
    require_valid_api_credentials,
)


@login_required
@require_POST
@require_valid_api_credentials
def start_learning_task(request):
    """
    Start the initial "Learning Your Audience" task: full Beehiiv fetch then
    process top-k eligible posts. Refuses if one is already running for this
    user/publication.
    """
    PendingLearningTask.sweep_stale()

    _, beehiiv_pub_id, _ = get_user_api_credentials(request.user)
    publication = _resolve_publication(request.user, beehiiv_pub_id)
    if publication is None:
        return JsonResponse(
            {'success': False, 'error': 'Publication not found.'}, status=400,
        )

    # Refuse if an initial task is already pending/running for this pub
    existing = PendingLearningTask.objects.filter(
        user=request.user,
        publication=publication,
        kind='initial',
        status__in=PendingLearningTask.RUNNING_STATUSES,
    ).first()
    if existing:
        return JsonResponse({
            'success': True,
            'task_id': str(existing.task_id),
            'already_running': True,
        })

    task = PendingLearningTask.objects.create(
        user=request.user,
        publication=publication,
        kind='initial',
    )

    spawn_background(
        PendingLearningTask, task.task_id, run_initial_learning_task,
    )

    return JsonResponse({'success': True, 'task_id': str(task.task_id)})


@login_required
@require_POST
@require_valid_api_credentials
def start_update_task(request):
    """
    Start the "Updating Your Posts" incremental task. The 60s TTL is enforced
    client-side via localStorage; here we only enforce "no duplicate running
    task" as defense-in-depth.
    """
    PendingLearningTask.sweep_stale()

    _, beehiiv_pub_id, _ = get_user_api_credentials(request.user)
    publication = _resolve_publication(request.user, beehiiv_pub_id)
    if publication is None:
        return JsonResponse(
            {'success': False, 'error': 'Publication not found.'}, status=400,
        )

    existing = PendingLearningTask.objects.filter(
        user=request.user,
        publication=publication,
        kind='update',
        status__in=PendingLearningTask.RUNNING_STATUSES,
    ).first()
    if existing:
        return JsonResponse({
            'success': True,
            'task_id': str(existing.task_id),
            'already_running': True,
        })

    task = PendingLearningTask.objects.create(
        user=request.user,
        publication=publication,
        kind='update',
    )

    spawn_background(
        PendingLearningTask, task.task_id, run_update_task,
    )

    return JsonResponse({'success': True, 'task_id': str(task.task_id)})


@login_required
@require_GET
def poll_learning_task(request, task_id):
    """
    Poll the status of a PendingLearningTask. Bumps last_heartbeat — while a
    client is actively polling, the sweep treats the task as live. Once
    polling stops (tab closed, navigation), the heartbeat ages out and the
    sweep marks the task errored (with the kind='initial' wipe via on_error).
    """
    try:
        task = PendingLearningTask.objects.get(task_id=task_id, user=request.user)
    except PendingLearningTask.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Task not found'}, status=404)

    if task.status in PendingLearningTask.RUNNING_STATUSES:
        task.touch_heartbeat()

    usage = request.user.usage_account
    return JsonResponse({
        'success': True,
        'kind': task.kind,
        'status': task.status,
        'phase': task.phase,
        'target_process_count': task.target_process_count,
        'posts_processed_count': task.posts_processed_count,
        'error_message': task.error_message,
        'credits_used': usage.used_this_period,
        'credits_quota': usage.monthly_quota,
    })

"""
Monetize page: hero/profile/stats rendering, the one-shot niche-analysis
task spawn, and the polling endpoint that surfaces its result.
"""

from asgiref.sync import async_to_sync
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from ..models import PendingNicheAnalysis, ProcessedPost, Publication
from ..utils import (
    fetch_publication_stats,
    run_niche_analysis_background,
    spawn_background,
)
from ._helpers import get_user_api_credentials, require_valid_api_credentials


@login_required
@require_valid_api_credentials
def monetize_view(request):
    """
    Frontend skeleton for the Monetize / sponsor-outreach campaign tab.
    Resolves the current publication's name for the hero copy and pulls
    live publication stats (subscribers / open rate / click rate) from
    Beehiiv to display in the Newsletter profile card.
    """
    beehiiv_token, beehiiv_pub_id, _ = get_user_api_credentials(request.user)

    # Resolve the newsletter name from the Publication row. The
    # @require_valid_api_credentials decorator guarantees a pub_id exists, and
    # account_view creates Publication rows at credentials-validation time, so
    # the fallback ('your newsletter') is only reached if the row is somehow
    # missing — flagged as a silent fallback per global instructions.
    newsletter_name = 'your newsletter'
    try:
        newsletter_name = Publication.objects.get(pub_id=beehiiv_pub_id).name
    except Publication.DoesNotExist:
        pass

    # Fetch publication stats from Beehiiv on every page load. No caching —
    # the call is fast and the page is low-traffic. fetch_publication_stats
    # returns None for any field that's missing or when the request fails;
    # we render a "—" placeholder in those cases (silent fallback flagged
    # per global instructions — a stats outage shouldn't break the page).
    stats = async_to_sync(fetch_publication_stats)(beehiiv_token, beehiiv_pub_id)
    subs = stats.get('active_subscriptions')
    open_rate = stats.get('average_open_rate')
    click_rate = stats.get('average_click_rate')

    # Beehiiv returns average_open_rate / average_click_rate as percentage
    # points (e.g. 51.16 == 51.16%), not as a 0-1 fraction — verified
    # empirically against a live publication. We render directly without
    # multiplying.
    subscriber_count_display = f"{subs:,}" if subs is not None else "—"
    open_rate_display = f"{open_rate:.1f}%" if open_rate is not None else "—"
    click_rate_display = f"{click_rate:.1f}%" if click_rate is not None else "—"

    # --- Niche analysis state (one-shot LLM call; results cached on the row) ---
    # We look for the most recent PendingNicheAnalysis for this (user, publication).
    # - 'complete' → render the stored niche/content_types as the defaults
    # - 'pending'/'running' → render placeholders + a task_id for the page to poll
    # - error or missing AND user has at least one processed post → kick off a
    #   new task (background thread) and return a task_id for polling
    # - missing AND no processed posts → render the static placeholders. Flagged
    #   as a soft fallback per global instructions: the analysis silently
    #   doesn't run rather than erroring, since a brand-new user with no
    #   processing finished can still see the Monetize tab.
    try:
        publication = Publication.objects.get(pub_id=beehiiv_pub_id)
    except Publication.DoesNotExist:
        publication = None

    niche_value = ''
    content_types_value = []
    niche_task_id = ''
    niche_status = 'unavailable'  # complete, running, unavailable

    latest_niche = None
    if publication is not None:
        latest_niche = PendingNicheAnalysis.objects.filter(
            user=request.user, publication=publication,
        ).order_by('-created_at').first()

    if latest_niche and latest_niche.status == 'complete':
        niche_value = latest_niche.niche or ''
        content_types_value = latest_niche.content_types or []
        niche_status = 'complete'
        niche_task_id = str(latest_niche.task_id)
    elif latest_niche and latest_niche.status in ('pending', 'running'):
        niche_status = 'running'
        niche_task_id = str(latest_niche.task_id)
    elif publication is not None:
        # No usable row — try to kick one off if the user has any processed posts.
        has_processed = ProcessedPost.objects.filter(
            user=request.user, publication=publication,
        ).exists()
        if has_processed:
            new_task = PendingNicheAnalysis.objects.create(
                user=request.user,
                publication=publication,
            )
            spawn_background(
                PendingNicheAnalysis,
                new_task.task_id,
                run_niche_analysis_background,
            )
            niche_status = 'running'
            niche_task_id = str(new_task.task_id)

    return render(request, 'analytics/monetize.html', {
        'newsletter_name': newsletter_name,
        'subscriber_count_display': subscriber_count_display,
        'open_rate_display': open_rate_display,
        'click_rate_display': click_rate_display,
        'niche_value': niche_value,
        'content_types_value': content_types_value,
        'niche_status': niche_status,
        'niche_task_id': niche_task_id,
    })


@login_required
@require_GET
def poll_niche_analysis(request, task_id):
    """
    Poll the status of a Monetize-tab niche analysis task. Returns the niche
    string and content_types list once the task completes.
    """
    try:
        task = PendingNicheAnalysis.objects.get(task_id=task_id, user=request.user)
    except PendingNicheAnalysis.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Task not found'}, status=404)

    if task.status in PendingNicheAnalysis.SWEEPABLE_STATUSES:
        task.touch_heartbeat()

    resp = {
        'success': True,
        'status': task.status,
    }
    if task.status == 'complete':
        resp['niche'] = task.niche or ''
        resp['content_types'] = task.content_types or []
        if settings.ENVIRONMENT == 'local' and task.dev_panel_data:
            resp['dev_panel'] = task.dev_panel_data
    elif task.status == 'error':
        resp['error_message'] = task.error_message
    return JsonResponse(resp)

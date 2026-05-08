"""
Views for the analytics app.
"""

import re
import logging
from django.shortcuts import render, redirect
from django.urls import reverse
from django.http import JsonResponse, HttpResponse
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.csrf import csrf_exempt

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.conf import settings
import json
from asgiref.sync import async_to_sync

from .models import Post, UsageAccount, ProcessedPost, LinkData, Section, PendingContentSearch, PendingImprovementTips, ContentSearchFeedback, PendingLearningTask, PendingNicheAnalysis

logger = logging.getLogger(__name__)


# =============================================================================
# Security Helpers
# =============================================================================

def sanitize_filename(filename: str) -> str:
    """
    Sanitize a filename to prevent HTTP header injection and path traversal.

    Removes or replaces characters that could be used for:
    - HTTP header injection (newlines, carriage returns)
    - Path traversal (slashes, backslashes)
    - Shell injection (quotes, semicolons)
    """
    if not filename:
        return "download"

    # Remove any path components
    filename = filename.replace('/', '_').replace('\\', '_')

    # Remove characters that could cause header injection or other issues
    # Keep only alphanumeric, spaces, hyphens, underscores, and periods
    sanitized = re.sub(r'[^\w\s\-.]', '', filename)

    # Collapse multiple spaces/underscores
    sanitized = re.sub(r'[\s_]+', '_', sanitized)

    # Remove leading/trailing underscores and periods
    sanitized = sanitized.strip('_.')

    # Ensure we have a valid filename
    if not sanitized:
        return "download"

    # Limit length to prevent issues
    return sanitized[:200]


from .utils import (
    NotEnoughCredits,
    charge_credits,
    validate_beehiiv_api_key,
    run_initial_learning_task,
    run_update_task,
    wipe_user_publication_data,
    fetch_publication_stats,
)


import functools


def get_user_api_credentials(user):
    """
    Get the Beehiiv API credentials for a user.
    Returns (token, pub_id, is_valid) tuple.
    """
    try:
        usage = UsageAccount.objects.get(user=user)
        if usage.has_api_credentials:
            return usage.beehiiv_token, usage.beehiiv_pub_id, usage.api_key_valid
    except UsageAccount.DoesNotExist:
        pass
    return None, None, False


def require_valid_api_credentials(view_func):
    """
    Decorator that checks for valid API credentials before allowing access.
    Redirects to account page with error message if credentials are missing or invalid.
    """
    @functools.wraps(view_func)
    def wrapper(request, *args, **kwargs):
        token, pub_id, is_valid = get_user_api_credentials(request.user)

        if not token or not pub_id:
            return redirect(reverse('analytics:account') + '?setup=configure')

        if not is_valid:
            return redirect(reverse('analytics:account') + '?setup=invalid')

        return view_func(request, *args, **kwargs)

    return wrapper


def index(request):
    """Show about page for unauthenticated users, redirect to Write for authenticated users"""
    if request.user.is_authenticated:
        return redirect('analytics:insights')
    return render(request, 'analytics/about.html')


def mobile_notice(request):
    """Show a notice that the app is not optimized for mobile"""
    return render(request, 'analytics/mobile.html')


@login_required
def account_view(request):
    """
    Display and manage user account settings including API credentials and usage.
    """
    from .models import Publication

    # Get or create usage account
    usage, created = UsageAccount.objects.get_or_create(
        user=request.user,
        defaults={'period_start': timezone.now().date().replace(day=1)}
    )
    usage.ensure_current_period()

    if request.method == 'POST':
        action = request.POST.get('action')
        old_pub_id = usage.beehiiv_pub_id  # Track for clearing session data on change

        if action == 'update_credentials':
            beehiiv_token = request.POST.get('beehiiv_token', '').strip()
            beehiiv_pub_id = request.POST.get('beehiiv_pub_id', '').strip()

            if beehiiv_token:
                # Validate the API key against Beehiiv
                is_valid, result = async_to_sync(validate_beehiiv_api_key)(beehiiv_token)

                if is_valid:
                    # Store token and publications list
                    usage.beehiiv_token = beehiiv_token
                    usage.api_key_valid = True
                    usage.available_publications = result  # List of publication dicts

                    # Validate selected publication is in list
                    valid_pub_ids = [p["id"] for p in result]
                    if beehiiv_pub_id and beehiiv_pub_id in valid_pub_ids:
                        usage.beehiiv_pub_id = beehiiv_pub_id
                    elif result:
                        # Default to first publication if none selected or invalid
                        usage.beehiiv_pub_id = result[0]["id"]
                    else:
                        usage.beehiiv_pub_id = ''

                    usage.save()

                    # Ensure Publication record exists for the selected pub
                    if usage.beehiiv_pub_id:
                        pub_data = next((p for p in result if p["id"] == usage.beehiiv_pub_id), None)
                        if pub_data:
                            Publication.objects.update_or_create(
                                pub_id=usage.beehiiv_pub_id,
                                defaults={
                                    'name': pub_data.get('name', 'Unknown'),
                                    'organization_name': pub_data.get('organization_name', '')
                                }
                            )

                    # Clear extracted items if publication changed
                    if usage.beehiiv_pub_id != old_pub_id:
                        request.session.pop('extracted_items', None)

                    messages.success(request, "API credentials validated and saved!")
                else:
                    # Invalid key
                    usage.beehiiv_token = beehiiv_token  # Keep token so user can see it
                    usage.api_key_valid = False
                    usage.available_publications = []
                    usage.save()
                    # Coach mark overlay on page reload will inform the user
            else:
                # Clear credentials and extracted items
                usage.beehiiv_token = ''
                usage.beehiiv_pub_id = ''
                usage.api_key_valid = False
                usage.available_publications = []
                usage.save()
                request.session.pop('extracted_items', None)
                messages.info(request, "API credentials cleared.")

            return redirect('analytics:account')

        elif action == 'update_timezone':
            # Handle timezone update
            from .utils import TIMEZONE_CHOICES
            new_timezone = request.POST.get('timezone', '').strip()
            valid_timezones = [tz[0] for tz in TIMEZONE_CHOICES]
            if new_timezone in valid_timezones:
                usage.timezone = new_timezone
                usage.save(update_fields=['timezone'])
                messages.success(request, "Timezone updated successfully!")
            else:
                messages.error(request, "Invalid timezone selected.")
            return redirect('analytics:account')

        elif action == 'switch_publication':
            # Handle publication switching
            new_pub_id = request.POST.get('beehiiv_pub_id', '').strip()
            valid_pub_ids = [p["id"] for p in usage.available_publications]

            if new_pub_id in valid_pub_ids and new_pub_id != old_pub_id:
                usage.beehiiv_pub_id = new_pub_id
                usage.save()

                # Clear extracted items since they belong to old publication
                request.session.pop('extracted_items', None)

                # Ensure Publication record exists
                pub_data = next((p for p in usage.available_publications if p["id"] == new_pub_id), None)
                if pub_data:
                    Publication.objects.update_or_create(
                        pub_id=new_pub_id,
                        defaults={
                            'name': pub_data.get('name', 'Unknown'),
                            'organization_name': pub_data.get('organization_name', '')
                        }
                    )

                messages.success(request, "Publication switched successfully!")
            elif new_pub_id == old_pub_id:
                pass  # No change needed
            else:
                messages.error(request, "Invalid publication selected.")

            # Picking a publication (from coach or main selector) is the
            # explicit confirmation we wait for before showing the
            # "Go to Write" coach.
            request.session['publication_coach_dismissed'] = True
            return redirect('analytics:account')

    from .utils import TIMEZONE_CHOICES
    has_posts = Post.objects.filter(user=request.user).exists() if usage.api_key_valid else False

    publication = None
    has_processed_data = False
    last_fetch_at = None
    most_recent_published_post = None
    most_recent_processed_post = None
    total_posts_count = None
    processed_posts_count = None

    if has_posts and usage.api_key_valid:
        _, beehiiv_pub_id, _ = get_user_api_credentials(request.user)
        try:
            publication = Publication.objects.get(pub_id=beehiiv_pub_id)
        except Publication.DoesNotExist:
            publication = None

        if publication is not None:
            has_processed_data = Section.objects.filter(
                user=request.user, publication=publication
            ).exists()

            # Post-fetching tab context
            pub_posts = Post.objects.filter(user=request.user, publication=publication)

            last_fetch_at = pub_posts.order_by('-updated_at').values_list(
                'updated_at', flat=True
            ).first()

            recent_published = pub_posts.filter(
                status='Published', publish_date__isnull=False,
            ).order_by('-publish_date').values('title', 'publish_date').first()
            if recent_published:
                most_recent_published_post = {
                    'title': recent_published['title'] or '(untitled)',
                    'publish_date': recent_published['publish_date'],
                }

            if settings.ENVIRONMENT == 'local':
                processed_marker = ProcessedPost.objects.filter(
                    user=request.user, post__publication=publication,
                    post__publish_date__isnull=False,
                ).select_related('post').order_by('-post__publish_date').first()
                if processed_marker:
                    most_recent_processed_post = {
                        'title': processed_marker.post.title or '(untitled)',
                        'publish_date': processed_marker.post.publish_date,
                    }
                total_posts_count = pub_posts.count()
                processed_posts_count = ProcessedPost.objects.filter(
                    user=request.user, post__publication=publication,
                ).count()

    # Show "API Key Validated — Go to Write" coach when creds are valid but the
    # initial post scan hasn't been run for the current publication (and no task
    # is already running).
    show_api_validated_coach = False
    if usage.api_key_valid and usage.beehiiv_pub_id:
        initial_done = usage.beehiiv_pub_id in (usage.initial_fetched_pub_ids or [])
        task_running = PendingLearningTask.objects.filter(
            user=request.user,
            publication=publication,
            kind='initial',
            status__in=('pending', 'running'),
        ).exists() if publication is not None else False
        show_api_validated_coach = (not initial_done) and (not task_running)

    # When the user has more than one publication, prompt them to confirm
    # which one to use before sending them to Write. Suppressed once they
    # explicitly pick (or re-confirm) a pub via the coach or main selector
    # (session flag set in the switch_publication action / dismiss endpoint).
    show_select_publication_coach = (
        show_api_validated_coach
        and len(usage.available_publications or []) > 1
        and not request.session.get('publication_coach_dismissed', False)
    )

    context = {
        'usage': usage,
        'timezone_choices': TIMEZONE_CHOICES,
        'has_posts': has_posts,
        'has_processed_data': has_processed_data,
        'last_fetch_at': last_fetch_at,
        'most_recent_published_post': most_recent_published_post,
        'most_recent_processed_post': most_recent_processed_post,
        'total_posts_count': total_posts_count,
        'processed_posts_count': processed_posts_count,
        'show_api_validated_coach': show_api_validated_coach,
        'show_select_publication_coach': show_select_publication_coach,
    }

    return render(request, 'analytics/account.html', context)


@login_required
@require_POST
def dismiss_publication_coach(request):
    """
    Mark the multi-publication selection coach as dismissed for this session.
    Called via fetch when the user clicks Confirm in the coach without
    changing publication (the change-pub case goes through switch_publication,
    which sets the same flag).
    """
    request.session['publication_coach_dismissed'] = True
    return JsonResponse({'success': True})


# Import timezone for account_view
from django.utils import timezone
from datetime import timedelta


def _resolve_publication(user, beehiiv_pub_id):
    """
    Return the Publication for the user's selected Beehiiv pub, creating the
    row from UsageAccount.available_publications if it's missing. Returns
    None if no metadata for the pub_id is available (caller should 400).
    """
    from .models import Publication
    pub = Publication.objects.filter(pub_id=beehiiv_pub_id).first()
    if pub is not None:
        return pub
    try:
        usage = UsageAccount.objects.get(user=user)
    except UsageAccount.DoesNotExist:
        return None
    pub_data = next(
        (p for p in (usage.available_publications or [])
         if p.get('id') == beehiiv_pub_id),
        None,
    )
    if not pub_data:
        return None
    pub, _ = Publication.objects.update_or_create(
        pub_id=beehiiv_pub_id,
        defaults={
            'name': pub_data.get('name', 'Unknown'),
            'organization_name': pub_data.get('organization_name', ''),
        },
    )
    return pub


def _sweep_stale_learning_tasks(user):
    """
    Mark any pending or running PendingLearningTask whose last_heartbeat is
    older than settings.LEARNING_TASK_STALE_SECONDS as abandoned. For kind
    ='initial' we also wipe the pub so the user restarts cleanly; update
    tasks have no cleanup. Runs for both kinds so a dead update thread
    can't block future auto-updates.
    """
    cutoff = timezone.now() - timedelta(
        seconds=getattr(settings, 'LEARNING_TASK_STALE_SECONDS', 15)
    )
    stale = PendingLearningTask.objects.filter(
        user=user,
        status__in=('pending', 'running'),
        last_heartbeat__lt=cutoff,
    )
    for task in stale:
        task.status = 'abandoned'
        task.abandoned = True
        task.save(update_fields=['status', 'abandoned'])
        if task.kind == 'initial' and task.publication is not None:
            try:
                wipe_user_publication_data(user, task.publication.pub_id)
            except Exception:
                logger.exception("Stale-sweep wipe failed")



@login_required
@require_valid_api_credentials
def insights_view(request):
    """
    Display the insights page with section data table.
    """
    from .models import Publication

    # Sweep stale initial Learning tasks first so the gating below uses fresh state.
    _sweep_stale_learning_tasks(request.user)

    # Get current publication for filtering
    _, beehiiv_pub_id, _ = get_user_api_credentials(request.user)

    # Check if user has any posts loaded
    has_posts = Post.objects.filter(user=request.user).exists()

    # Check for processed data
    try:
        publication = Publication.objects.get(pub_id=beehiiv_pub_id)
        has_processed_data = Section.objects.filter(
            user=request.user, publication=publication
        ).exists()
    except Publication.DoesNotExist:
        publication = None
        has_processed_data = False

    from .models import Feedback
    write_post_feedback = Feedback.objects.filter(
        user=request.user, feature='write_post'
    ).first()

    # Per-feature usage tracking for coach marks
    used_features = set(
        Feedback.objects.filter(
            user=request.user,
            feature__in=['used_content_finder', 'seen_write_post_poll', 'used_write_post_poll', 'used_post_improvement']
        ).values_list('feature', flat=True)
    )

    # --- Learning / Update flow gating ---
    usage = UsageAccount.objects.get(user=request.user)
    initial_done = beehiiv_pub_id in (usage.initial_fetched_pub_ids or [])

    active_initial_task = PendingLearningTask.objects.filter(
        user=request.user,
        publication=publication,
        kind='initial',
        status__in=('pending', 'running'),
    ).order_by('-created_at').first()

    active_update_task = PendingLearningTask.objects.filter(
        user=request.user,
        publication=publication,
        kind='update',
        status__in=('pending', 'running'),
    ).order_by('-created_at').first()

    # Show the Learning coach if the user has valid creds but initial flow
    # hasn't completed and there is no task running.
    show_learning_coach = (not initial_done) and (active_initial_task is None)

    # If an initial task is already running, jump straight into the modal.
    show_learning_modal = active_initial_task is not None

    # The Updating-Your-Posts modal fires on every eligible page load (initial
    # flow already done AND no running task). The 60s TTL check happens in JS
    # via localStorage so rapid reloads don't spam Beehiiv.
    show_update_modal = (
        initial_done
        and active_initial_task is None
        and active_update_task is None
    )

    context = {
        'has_posts': has_posts,
        'has_processed_data': has_processed_data,
        'credits_per_search': settings.CREDITS_PER_CONTENT_SEARCH,
        'credits_per_improvement_tips': settings.CREDITS_PER_IMPROVEMENT_TIPS,
        'write_post_feedback_response': write_post_feedback.response if write_post_feedback else '',
        'has_used_content_finder': 'used_content_finder' in used_features,
        'has_seen_write_post_poll': ('seen_write_post_poll' in used_features) or ('used_write_post_poll' in used_features),
        'has_used_write_post_poll': 'used_write_post_poll' in used_features,
        'has_used_post_improvement': 'used_post_improvement' in used_features,
        'beehiiv_pub_id': beehiiv_pub_id,
        'show_learning_coach': show_learning_coach,
        'show_learning_modal': show_learning_modal,
        'show_update_modal': show_update_modal,
        'active_learning_task_id': str(active_initial_task.task_id) if active_initial_task else '',
        'active_update_task_id': str(active_update_task.task_id) if active_update_task else '',
    }

    return render(request, 'analytics/insights.html', context)


@login_required
@require_valid_api_credentials
def monetize_view(request):
    """
    Frontend skeleton for the Monetize / sponsor-outreach campaign tab.
    Resolves the current publication's name for the hero copy and pulls
    live publication stats (subscribers / open rate / click rate) from
    Beehiiv to display in the Newsletter profile card.
    """
    from .models import Publication

    usage = UsageAccount.objects.get(user=request.user)
    beehiiv_token, beehiiv_pub_id, _ = get_user_api_credentials(request.user)

    # Resolve the newsletter name. We try the cached available_publications
    # list first (matches what the user picked in the Account page), then
    # fall back to the Publication row. The @require_valid_api_credentials
    # decorator guarantees a pub_id exists, so the ultimate fallback
    # ('your newsletter') is only reached if the Publication row is somehow
    # missing — flagged as a silent fallback per global instructions.
    newsletter_name = 'your newsletter'
    pub = next(
        (p for p in (usage.available_publications or [])
         if p.get('id') == beehiiv_pub_id),
        None,
    )
    if pub and pub.get('name'):
        newsletter_name = pub['name']
    else:
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
            import threading
            from .utils import run_niche_analysis_background
            new_task = PendingNicheAnalysis.objects.create(
                user=request.user,
                publication=publication,
                status='pending',
            )
            threading.Thread(
                target=run_niche_analysis_background,
                args=(new_task.task_id,),
                daemon=True,
            ).start()
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
def load_processed_data(request):
    """
    Load all Section data for the current user and publication.
    Returns section fields for display in the Insights table.
    """
    from .models import Publication

    try:
        _, beehiiv_pub_id, _ = get_user_api_credentials(request.user)

        try:
            publication = Publication.objects.get(pub_id=beehiiv_pub_id)
        except Publication.DoesNotExist:
            return JsonResponse({'success': True, 'items': []})

        sections = Section.objects.filter(
            user=request.user, publication=publication
        ).select_related('post')

        items = []
        for sec in sections:
            post = sec.post
            post_date = post.publish_date
            post_date_display = '-'
            post_date_sortable = ''
            if post_date:
                try:
                    post_date_display = post_date.strftime('%b %d, %Y')
                    post_date_sortable = post_date.strftime('%Y-%m-%d')
                except Exception:
                    post_date_display = str(post_date)
                    post_date_sortable = str(post_date)

            items.append({
                'post_title': post.title or '',
                'post_date_display': post_date_display,
                'post_date_sortable': post_date_sortable,
                'section_name': sec.section_name,
                'section_title': sec.section_title or '',
                'start_line': sec.start_line,
                'end_line': sec.end_line,
            })

        return JsonResponse({
            'success': True,
            'items': items,
        })

    except Exception:
        logger.exception("load_processed_data failed")
        return JsonResponse({'success': False}, status=500)


@login_required
def load_link_data(request):
    """
    Load all LinkData for the current user and publication.
    Returns link fields for display in the Insights link table.
    """
    from .models import Publication

    try:
        _, beehiiv_pub_id, _ = get_user_api_credentials(request.user)

        try:
            publication = Publication.objects.get(pub_id=beehiiv_pub_id)
        except Publication.DoesNotExist:
            return JsonResponse({'success': True, 'items': []})

        links = LinkData.objects.filter(
            user=request.user, publication=publication
        ).select_related('post')

        items = []
        for link in links:
            post = link.post
            post_date = post.publish_date
            post_date_display = '-'
            post_date_sortable = ''
            if post_date:
                try:
                    post_date_display = post_date.strftime('%b %d, %Y')
                    post_date_sortable = post_date.strftime('%Y-%m-%d')
                except Exception:
                    post_date_display = str(post_date)
                    post_date_sortable = str(post_date)

            items.append({
                'post_title': post.title or '',
                'post_date_display': post_date_display,
                'post_date_sortable': post_date_sortable,
                'section_name': link.section_name,
                'raw_url': link.raw_url,
                'description': link.description,
                'rank_in_section': link.rank_in_section,
                'mean_ctr': link.mean_ctr,
                'mean_clicks': link.mean_clicks,
            })

        return JsonResponse({
            'success': True,
            'items': items,
        })

    except Exception:
        logger.exception("load_link_data failed")
        return JsonResponse({'success': False}, status=500)


# =============================================================================
# Learning / Updating flow views
# =============================================================================

@login_required
@require_POST
@require_valid_api_credentials
def start_learning_task(request):
    """
    Start the initial "Learning Your Audience" task: full Beehiiv fetch then
    process top-k eligible posts. Refuses if one is already running for this
    user/publication.
    """
    import threading

    _sweep_stale_learning_tasks(request.user)

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
        status__in=('pending', 'running'),
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
        status='pending',
    )

    threading.Thread(
        target=run_initial_learning_task,
        args=(task.task_id,),
        daemon=True,
    ).start()

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
    import threading

    _sweep_stale_learning_tasks(request.user)

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
        status__in=('pending', 'running'),
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
        status='pending',
    )

    threading.Thread(
        target=run_update_task,
        args=(task.task_id,),
        daemon=True,
    ).start()

    return JsonResponse({'success': True, 'task_id': str(task.task_id)})


@login_required
@require_GET
def poll_learning_task(request, task_id):
    """
    Poll the status of a PendingLearningTask. Does NOT touch last_heartbeat
    — that's maintained by the runner's own heartbeat thread so stale-sweep
    measures runner liveness, not client polling.
    """
    try:
        task = PendingLearningTask.objects.get(task_id=task_id, user=request.user)
    except PendingLearningTask.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Task not found'}, status=404)

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


@csrf_exempt
@login_required
@require_POST
def abandon_learning_task(request, task_id):
    """
    Called via `navigator.sendBeacon` on pagehide. Marks the task abandoned;
    the wipe (for kind='initial') is performed by the runner's own
    `_is_abandoned` check — doing it inline races against in-flight writes
    from the runner thread. If the runner thread is dead, the stale-sweep
    does the wipe when `last_heartbeat` ages out.

    CSRF-exempt because `sendBeacon` cannot set custom headers. We instead
    enforce that the request's Origin header matches one of our trusted
    origins; sendBeacon always sends Origin, so legit beacons still pass.
    """
    origin = request.META.get('HTTP_ORIGIN', '')
    if origin not in settings.CSRF_TRUSTED_ORIGINS:
        return JsonResponse({'success': False, 'error': 'Forbidden'}, status=403)

    try:
        task = PendingLearningTask.objects.get(task_id=task_id, user=request.user)
    except PendingLearningTask.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Task not found'}, status=404)

    if task.status in ('complete', 'abandoned', 'error'):
        return JsonResponse({'success': True, 'already_finished': True})

    task.abandoned = True
    task.save(update_fields=['abandoned'])

    return JsonResponse({'success': True})



# =============================================================================
# Content Finder Views
# =============================================================================

@login_required
@require_GET
@require_valid_api_credentials
def content_finder_posts(request):
    """Return list of processed posts for the content finder dropdown."""
    from .models import Publication

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
    import threading
    from .models import Publication
    from .utils import charge_credits, NotEnoughCredits, run_content_finder_background

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

    try:
        charge_credits(request.user, settings.CREDITS_PER_CONTENT_SEARCH)
    except NotEnoughCredits:
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
        status='planning',
    )

    threading.Thread(
        target=run_content_finder_background,
        args=(task.task_id,),
        daemon=True,
    ).start()

    return JsonResponse({'success': True, 'task_id': str(task.task_id)})


@login_required
@require_POST
@require_valid_api_credentials
def confirm_content_finder_plan(request, task_id):
    """Stage 2/3 kickoff: record the user's plan feedback and spawn dispatch + search."""
    import threading
    from .utils import run_content_finder_background

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
    task.save(update_fields=['user_feedback', 'status'])

    threading.Thread(
        target=run_content_finder_background,
        args=(task.task_id,),
        daemon=True,
    ).start()

    return JsonResponse({'success': True})


@login_required
@require_GET
def poll_content_finder(request, task_id):
    """Poll the status of a content finder task."""
    try:
        task = PendingContentSearch.objects.get(task_id=task_id, user=request.user)
    except PendingContentSearch.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Task not found'}, status=404)

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
    from .models import Publication
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


@login_required
@require_GET
@require_valid_api_credentials
def improvement_tips_posts(request):
    """Return list of all posts for the improvement tips dropdown."""
    from .models import Publication

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
    import threading
    from .models import Publication, PendingImprovementTips
    from .utils import charge_credits, NotEnoughCredits, run_improvement_tips_background

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

    # Charge credits
    try:
        charge_credits(request.user, settings.CREDITS_PER_IMPROVEMENT_TIPS)
    except NotEnoughCredits:
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

    # Spawn background thread
    threading.Thread(
        target=run_improvement_tips_background,
        args=(task.task_id,),
        daemon=True,
    ).start()

    return JsonResponse({'success': True, 'task_id': str(task.task_id)})


@login_required
@require_GET
def poll_improvement_tips(request, task_id):
    """Poll the status of an improvement tips task."""
    from .models import PendingImprovementTips

    try:
        task = PendingImprovementTips.objects.get(task_id=task_id, user=request.user)
    except PendingImprovementTips.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Task not found'}, status=404)

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
    from .models import PendingImprovementTips

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


SUBMITTABLE_FEEDBACK_FEATURES = {'write_post', 'seen_write_post_poll'}


@login_required
@require_POST
def submit_feedback(request):
    """Save a user feedback response."""
    from .models import Feedback
    try:
        data = json.loads(request.body)
        feature = data.get('feature', '')
        response = data.get('response', '')
        if not feature or not response:
            return JsonResponse({'success': False, 'error': 'Missing fields'}, status=400)
        if feature not in SUBMITTABLE_FEEDBACK_FEATURES:
            return JsonResponse({'success': False, 'error': 'Invalid feature'}, status=400)

        Feedback.objects.update_or_create(
            user=request.user,
            feature=feature,
            defaults={'response': str(response)[:255]},
        )
        if feature == 'write_post':
            Feedback.objects.get_or_create(
                user=request.user, feature='used_write_post_poll',
                defaults={'response': 'completed'}
            )
        return JsonResponse({'success': True})
    except Exception:
        logger.exception("submit_feedback failed")
        return JsonResponse({'success': False}, status=500)

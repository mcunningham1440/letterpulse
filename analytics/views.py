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
import pandas as pd
import json
from asgiref.sync import async_to_sync

from .models import Post, ContentSet, Report, UsageAccount, SurveyResponse, ProcessedPost, LinkData, Section, PendingContentSearch, PendingImprovementTips, ContentSearchFeedback, PendingLearningTask

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
    load_posts_from_db,
    fetch_posts_html_and_clicks_parallel,
    process_posts_sections_sequential,
    refresh_posts_data,
    incremental_refresh_posts_data,
    save_posts_to_db,
    NotEnoughCredits,
    charge_credits,
    validate_beehiiv_api_key,
    run_initial_learning_task,
    run_update_task,
    wipe_user_publication_data,
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

                    # Reset click viz window on every successful validation
                    if usage.auto_click_viz_email:
                        usage.auto_click_viz_enabled_at = timezone.now()

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

        elif action == 'toggle_click_viz_email':
            if usage.api_key_valid:
                if usage.auto_click_viz_email:
                    # Disabling
                    usage.auto_click_viz_email = False
                    usage.auto_click_viz_enabled_at = None
                    usage.save(update_fields=['auto_click_viz_email', 'auto_click_viz_enabled_at'])
                else:
                    # Enabling
                    usage.auto_click_viz_email = True
                    usage.auto_click_viz_enabled_at = timezone.now()
                    usage.save(update_fields=['auto_click_viz_email', 'auto_click_viz_enabled_at'])
            return redirect('analytics:account')

        elif action == 'test_click_viz_email':
            if usage.api_key_valid:
                try:
                    from .utils import (
                        fetch_recent_published_posts,
                        fetch_posts_html_and_clicks_parallel,
                        generate_click_visualization_html,
                        build_click_viz_email_html,
                    )
                    from django.core.mail import EmailMessage as DjangoEmailMessage

                    # Fetch most recent published post
                    recent_posts = async_to_sync(fetch_recent_published_posts)(
                        usage.beehiiv_token, usage.beehiiv_pub_id, max_pages=1
                    )
                    if not recent_posts:
                        messages.error(request, "No published posts found to generate a test email.")
                        return redirect('analytics:account')

                    post_data = recent_posts[0]
                    post_id = post_data['id']
                    post_title = post_data.get('title', 'Untitled')

                    # Fetch HTML and clicks
                    htmls, clicks_by_id = async_to_sync(fetch_posts_html_and_clicks_parallel)(
                        [post_id], usage.beehiiv_token, usage.beehiiv_pub_id
                    )

                    html_filename = f"{post_id}.html"
                    if html_filename not in htmls or post_id not in clicks_by_id:
                        messages.error(request, "Failed to fetch post data from Beehiiv.")
                        return redirect('analytics:account')

                    stats = post_data.get('stats', {}).get('email', {})
                    unique_email_opens = stats.get('unique_opens', 0)

                    viz_html = generate_click_visualization_html(
                        htmls[html_filename], clicks_by_id[post_id], unique_email_opens
                    )

                    site_url = getattr(settings, 'SITE_URL', 'https://letterpulse.com')
                    email_html = build_click_viz_email_html(viz_html, post_title, site_url)

                    bcc = [settings.SIGNUP_NOTIFICATION_EMAIL] if getattr(settings, 'SIGNUP_NOTIFICATION_EMAIL', '') else []
                    email = DjangoEmailMessage(
                        subject=f"[TEST] Click Visualization: {post_title}",
                        body=email_html,
                        from_email=settings.DEFAULT_FROM_EMAIL,
                        to=[request.user.email],
                        bcc=bcc,
                    )
                    email.content_subtype = 'html'
                    email.send()

                    messages.success(request, f"Test email sent to {request.user.email}.")
                except Exception as e:
                    logger.error(f"Test click viz email failed: {e}", exc_info=True)
                    messages.error(request, f"Failed to send test email: {str(e)}")
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

            return redirect('analytics:account')

    from .utils import TIMEZONE_CHOICES
    has_posts = Post.objects.filter(user=request.user).exists() if usage.api_key_valid else False

    publication = None
    has_processed_data = False
    last_fetch_at = None
    most_recent_published_post = None
    most_recent_processed_post = None

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

    context = {
        'usage': usage,
        'timezone_choices': TIMEZONE_CHOICES,
        'has_posts': has_posts,
        'has_processed_data': has_processed_data,
        'last_fetch_at': last_fetch_at,
        'most_recent_published_post': most_recent_published_post,
        'most_recent_processed_post': most_recent_processed_post,
    }

    return render(request, 'analytics/account.html', context)


# Import timezone for account_view
from django.utils import timezone
from datetime import timedelta


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
            feature__in=['used_content_finder', 'used_write_post_poll', 'used_post_improvement']
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

    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


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

    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


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
    from .models import Publication

    _sweep_stale_learning_tasks(request.user)

    _, beehiiv_pub_id, _ = get_user_api_credentials(request.user)
    try:
        publication = Publication.objects.get(pub_id=beehiiv_pub_id)
    except Publication.DoesNotExist:
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
    from .models import Publication

    _sweep_stale_learning_tasks(request.user)

    _, beehiiv_pub_id, _ = get_user_api_credentials(request.user)
    try:
        publication = Publication.objects.get(pub_id=beehiiv_pub_id)
    except Publication.DoesNotExist:
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

    CSRF-exempt because `sendBeacon` cannot set custom headers; the view is
    still session-authenticated and can only affect the caller's own tasks.
    """
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
            date_display = '-'
            if post.publish_date:
                try:
                    date_display = post.publish_date.strftime('%b %d, %Y')
                except Exception:
                    date_display = str(post.publish_date)
            posts.append({
                'post_id': post.post_id,
                'title': post.title or '',
                'publish_date': date_display,
            })

        posts.sort(key=lambda p: p['publish_date'], reverse=True)

        return JsonResponse({'success': True, 'posts': posts})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@login_required
@require_GET
@require_valid_api_credentials
def content_finder_sections(request):
    """Return section names for a given post."""
    post_id = request.GET.get('post_id')
    if not post_id:
        return JsonResponse({'success': False, 'error': 'post_id required'}, status=400)

    sections = Section.objects.filter(
        post__post_id=post_id, user=request.user
    ).order_by('start_line').values_list('section_name', flat=True)

    return JsonResponse({'success': True, 'sections': list(sections)})


@login_required
@require_POST
@require_valid_api_credentials
def run_content_finder(request):
    """Start a content finder background task."""
    import threading
    from .models import Publication
    from .utils import charge_credits, NotEnoughCredits, run_content_finder_background

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    post_id = data.get('post_id')
    mode = data.get('mode', 'auto')
    selected_sections = data.get('selected_sections', [])

    if not post_id:
        return JsonResponse({'success': False, 'error': 'post_id required'}, status=400)
    if mode not in ('auto', 'manual'):
        return JsonResponse({'success': False, 'error': 'mode must be auto or manual'}, status=400)

    if not settings.PERPLEXITY_API_KEY:
        return JsonResponse({'success': False, 'error': 'Content Finder is not configured. Please contact support.'}, status=500)

    # Look up the post
    try:
        post = Post.objects.get(post_id=post_id, user=request.user)
    except Post.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Post not found'}, status=404)

    # Verify sections exist
    section_qs = Section.objects.filter(post=post, user=request.user)
    if not section_qs.exists():
        return JsonResponse({'success': False, 'error': 'No sections found for this post'}, status=400)

    if mode == 'manual':
        if not selected_sections:
            return JsonResponse({'success': False, 'error': 'Select at least one section'}, status=400)
        existing = set(section_qs.values_list('section_name', flat=True))
        invalid = set(selected_sections) - existing
        if invalid:
            return JsonResponse({'success': False, 'error': f'Invalid sections: {", ".join(invalid)}'}, status=400)

    # Charge credits
    try:
        charge_credits(request.user, settings.CREDITS_PER_CONTENT_SEARCH)
    except NotEnoughCredits:
        return JsonResponse({'success': False, 'error': 'Not enough credits'}, status=400)

    # Get publication
    _, beehiiv_pub_id, _ = get_user_api_credentials(request.user)
    try:
        publication = Publication.objects.get(pub_id=beehiiv_pub_id)
    except Publication.DoesNotExist:
        publication = None

    # Create task
    task = PendingContentSearch.objects.create(
        user=request.user,
        publication=publication,
        post=post,
        mode=mode,
        selected_sections=selected_sections if mode == 'manual' else [],
    )

    # Spawn background thread
    threading.Thread(
        target=run_content_finder_background,
        args=(task.task_id,),
        daemon=True,
    ).start()

    return JsonResponse({'success': True, 'task_id': str(task.task_id)})


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

    if task.status == 'complete':
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
        url=url,
        defaults={
            'title': data.get('title', '')[:500],
            'source': data.get('source', '')[:255],
            'pub_date': data.get('date', '')[:100],
            'description': data.get('description', ''),
            'relevance': data.get('relevance', ''),
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
            date_display = '-'
            date_val = post.publish_date or post.creation_date
            if date_val:
                try:
                    date_display = date_val.strftime('%b %d, %Y')
                except Exception:
                    date_display = str(date_val)
            posts.append({
                'post_id': post.post_id,
                'title': post.title or '',
                'date': date_display,
                'status': post.status or 'Published',
            })

        return JsonResponse({'success': True, 'posts': posts})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


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

        Feedback.objects.update_or_create(
            user=request.user,
            feature=feature,
            defaults={'response': response},
        )
        # Track write post poll usage for coach marks
        if feature == 'write_post':
            Feedback.objects.get_or_create(
                user=request.user, feature='used_write_post_poll',
                defaults={'response': 'completed'}
            )
        return JsonResponse({'success': True})
    except Exception:
        return JsonResponse({'success': False}, status=500)


@login_required
@require_POST
def submit_survey(request):
    """
    Submit the signup survey response.
    """
    try:
        # Parse the response
        beehiiv_inadequate = request.POST.get('beehiiv_inadequate')
        missing_features = request.POST.get('missing_features', '').strip()
        other_tools = request.POST.get('other_tools', '').strip()

        # Convert yes/no to boolean
        if beehiiv_inadequate == 'yes':
            beehiiv_inadequate_bool = True
        elif beehiiv_inadequate == 'no':
            beehiiv_inadequate_bool = False
        else:
            beehiiv_inadequate_bool = None

        # Create or update survey response
        SurveyResponse.objects.update_or_create(
            user=request.user,
            defaults={
                'beehiiv_analytics_inadequate': beehiiv_inadequate_bool,
                'missing_features': missing_features,
                'other_tools': other_tools,
            }
        )

        # Mark survey as completed in UsageAccount
        try:
            usage = UsageAccount.objects.get(user=request.user)
            usage.survey_completed = True
            usage.save(update_fields=['survey_completed'])
        except UsageAccount.DoesNotExist:
            pass

        return JsonResponse({
            'success': True,
            'message': 'Survey submitted successfully!'
        })

    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)




@login_required
def cron_status(request):
    """
    Status page showing recent cron runs and click viz email logs.
    Login-required so only authenticated users can view.
    Only superusers can see all data; regular users see only their own.
    """
    from .models import CronRunLog, ClickVizEmailLog

    is_superuser = request.user.is_superuser

    # Recent cron runs (last 20) — superusers only
    cron_runs = []
    if is_superuser:
        for run in CronRunLog.objects.all()[:20]:
            cron_runs.append({
                'command': run.command,
                'started_at': run.started_at.isoformat(),
                'finished_at': run.finished_at.isoformat() if run.finished_at else None,
                'duration_ms': run.duration_ms,
                'users_processed': run.users_processed,
                'emails_sent': run.emails_sent,
                'errors': run.errors,
                'success': run.success,
                'triggered_by': run.triggered_by,
                'output': run.output,
            })

    # Recent email logs (last 50)
    email_log_qs = ClickVizEmailLog.objects.select_related('user', 'publication')
    if not is_superuser:
        email_log_qs = email_log_qs.filter(user=request.user)

    email_logs = []
    for log in email_log_qs[:50]:
        email_logs.append({
            'user': log.user.email,
            'post_id': log.post_id,
            'post_title': log.post_title,
            'sent_at': log.sent_at.isoformat(),
            'success': log.success,
            'error_message': log.error_message,
        })

    # Eligible users summary (superusers only)
    eligible_users = []
    if is_superuser:
        for ua in UsageAccount.objects.filter(
            auto_click_viz_email=True,
            api_key_valid=True,
            auto_click_viz_enabled_at__isnull=False,
        ).select_related('user'):
            eligible_users.append({
                'email': ua.user.email,
                'enabled_at': ua.auto_click_viz_enabled_at.isoformat(),
            })

    return JsonResponse({
        'cron_runs': cron_runs,
        'email_logs': email_logs,
        'eligible_users': eligible_users,
    }, json_dumps_params={'indent': 2})

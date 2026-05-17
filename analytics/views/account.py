"""
Account-page views: settings, API credentials, publication selection, and
the multi-publication selection coach.
"""

from asgiref.sync import async_to_sync
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from ..models import (
    PendingLearningTask,
    Post,
    ProcessedPost,
    Publication,
    Section,
    UsageAccount,
    UserPublication,
)
from ..utils import TIMEZONE_CHOICES, validate_beehiiv_api_key
from ._helpers import get_user_api_credentials


@login_required
def account_view(request):
    """
    Display and manage user account settings including API credentials and usage.
    """
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
                    usage.beehiiv_token = beehiiv_token
                    usage.api_key_valid = True

                    # Mirror Beehiiv's pub list into Publication / UserPublication
                    _sync_user_publications(request.user, result)

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

                    # Clear extracted items if publication changed
                    if usage.beehiiv_pub_id != old_pub_id:
                        request.session.pop('extracted_items', None)

                    messages.success(request, "API credentials validated and saved!")
                else:
                    # Invalid key — drop any UserPublication access this user had,
                    # mirroring the legacy behavior of clearing available_publications.
                    usage.beehiiv_token = beehiiv_token  # Keep token so user can see it
                    usage.api_key_valid = False
                    UserPublication.objects.filter(user=request.user).delete()
                    usage.save()
                    # Coach mark overlay on page reload will inform the user
            else:
                # Clear credentials and extracted items
                usage.beehiiv_token = ''
                usage.beehiiv_pub_id = ''
                usage.api_key_valid = False
                UserPublication.objects.filter(user=request.user).delete()
                usage.save()
                request.session.pop('extracted_items', None)
                messages.info(request, "API credentials cleared.")

            return redirect('analytics:account')

        elif action == 'update_timezone':
            # Handle timezone update
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
            user_has_access = UserPublication.objects.filter(
                user=request.user, publication__pub_id=new_pub_id,
            ).exists()

            if user_has_access and new_pub_id != old_pub_id:
                usage.beehiiv_pub_id = new_pub_id
                usage.save()

                # Clear extracted items since they belong to old publication
                request.session.pop('extracted_items', None)

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

    # Publications the user currently has access to (drives both selector
    # dropdowns in account.html).
    available_publications = list(
        UserPublication.objects.filter(user=request.user)
        .select_related('publication')
        .order_by('publication__name')
        .values_list('publication__pub_id', 'publication__name')
    )
    available_publications = [
        {'id': pid, 'name': name} for pid, name in available_publications
    ]

    # Show "API Key Validated — Go to Write" coach when creds are valid but the
    # initial post scan hasn't been run for the current publication (and no task
    # is already running).
    show_api_validated_coach = False
    if usage.api_key_valid and usage.beehiiv_pub_id:
        initial_done = UserPublication.objects.filter(
            user=request.user,
            publication__pub_id=usage.beehiiv_pub_id,
            initial_fetch_done_at__isnull=False,
        ).exists()
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
        and len(available_publications) > 1
        and not request.session.get('publication_coach_dismissed', False)
    )

    context = {
        'usage': usage,
        'available_publications': available_publications,
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


def _sync_user_publications(user, beehiiv_pubs):
    """
    Mirror the Beehiiv API's pub list into Publication + UserPublication for
    the given user. `beehiiv_pubs` is the list of dicts returned by
    validate_beehiiv_api_key: [{'id', 'name', 'organization_name'}, ...].

    Creates/updates Publication rows for each entry, ensures a UserPublication
    row exists for (user, pub), and removes UserPublication rows for pubs no
    longer present in the response. Existing initial_fetch_done_at timestamps
    are preserved on UserPublications that persist.
    """
    pub_ids_in_response = set()
    for entry in (beehiiv_pubs or []):
        pid = (entry or {}).get('id')
        if not pid:
            continue
        pub_ids_in_response.add(pid)
        pub, _ = Publication.objects.update_or_create(
            pub_id=pid,
            defaults={
                'name': entry.get('name', 'Unknown') or 'Unknown',
                'organization_name': entry.get('organization_name', '') or '',
            },
        )
        UserPublication.objects.get_or_create(user=user, publication=pub)

    UserPublication.objects.filter(user=user).exclude(
        publication__pub_id__in=pub_ids_in_response
    ).delete()

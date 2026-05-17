"""
Insights / Write page: the main page view plus the AJAX endpoints that
back the Section and LinkData tables.
"""

import logging

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render

from ..models import (
    Feedback,
    LinkData,
    PendingLearningTask,
    Post,
    Publication,
    Section,
    UserPublication,
)
from ._helpers import get_user_api_credentials, require_valid_api_credentials

logger = logging.getLogger(__name__)


@login_required
@require_valid_api_credentials
def insights_view(request):
    """
    Display the insights page with section data table.
    """
    # Drop any stuck learning rows so the active-task gating below reflects
    # tasks whose clients are still actually polling.
    PendingLearningTask.sweep_stale()

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
    initial_done = UserPublication.objects.filter(
        user=request.user,
        publication__pub_id=beehiiv_pub_id,
        initial_fetch_done_at__isnull=False,
    ).exists()

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
def load_processed_data(request):
    """
    Load all Section data for the current user and publication.
    Returns section fields for display in the Insights table.
    """
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

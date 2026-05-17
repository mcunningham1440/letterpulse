"""
Per-feature user feedback endpoint and the allow-list of features that
the frontend is permitted to submit.
"""

import json
import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from ..models import Feedback

logger = logging.getLogger(__name__)

SUBMITTABLE_FEEDBACK_FEATURES = {'write_post', 'seen_write_post_poll'}


@login_required
@require_POST
def submit_feedback(request):
    """Save a user feedback response."""
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

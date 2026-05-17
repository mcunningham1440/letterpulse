"""
Views package for the analytics app.

This file re-exports every public view function so `urls.py`'s
`from . import views` keeps resolving `views.foo` for every endpoint —
without needing to know which submodule actually defines it.
"""

from ._helpers import (
    get_user_api_credentials,
    require_valid_api_credentials,
    sanitize_filename,
)
from .account import account_view, dismiss_publication_coach
from .content_finder import (
    confirm_content_finder_plan,
    content_finder_posts,
    poll_content_finder,
    run_content_finder,
    submit_content_search_feedback,
)
from .feedback import submit_feedback
from .improvement_tips import (
    download_improvement_tips,
    improvement_tips_posts,
    poll_improvement_tips,
    run_improvement_tips,
)
from .insights import insights_view, load_link_data, load_processed_data
from .learning import poll_learning_task, start_learning_task, start_update_task
from .monetize import monetize_view, poll_niche_analysis
from .public import index, mobile_notice

"""
Utility functions for the Django analytics app.

This module is a package: each submodule owns a focused slice of what was
previously a single 3,300-line ``utils.py``. The names re-exported here are
the public surface that views and tests import; internal helpers stay in
their submodules.
"""

from ._constants import TIMEZONE_CHOICES

from .credits import NotEnoughCredits, charge_credits, refund_credits

from .background import (
    recover_stuck_tasks,
    refresh_db_connection,
    spawn_background,
)

from .llm import OPENAI_API_KEY, llm_call

from .text import html_to_text_with_links, truncate_url

from .beehiiv_api import (
    fetch_all_posts,
    fetch_post_clicks,
    fetch_post_html,
    fetch_posts_page,
    fetch_publication_stats,
    fetch_subscriber_count,
    incremental_fetch_posts,
    validate_beehiiv_api_key,
)

from .post_selection import (
    select_posts_for_initial_learning,
    select_posts_for_update,
    wipe_user_publication_data,
)

from .links import (
    AllLinkDescriptions,
    LinkDescription,
    allocate_links_to_sections,
    format_link_history,
    match_links_with_clicks,
    process_post_links,
    select_top_bottom,
)

from .sections import (
    AllSections,
    SectionItem,
    auto_section,
    build_sections_desc,
)

from .posts import (
    incremental_refresh_posts_data,
    process_post_full,
    process_posts_data,
    process_posts_sections_sequential,
    refresh_posts_data,
    save_posts_to_db,
)

from .content_finder import (
    ContentFinderAllLinks,
    ContentFinderDispatchList,
    ContentFinderLink,
    build_all_sections_user_prompt,
    build_content_finder_user_prompt,
    perplexity_search,
    run_all_searches,
    run_content_finder_background,
    run_dispatch_stage,
    run_plan_stage,
    run_search_agent,
)

from .improvement_tips import (
    AllImprovementTips,
    ContentTip,
    ProofreadingTip,
    generate_improvement_tips_html,
    run_improvement_tips_background,
)

from .learning import (
    run_initial_learning_task,
    run_update_task,
)

from .niche import (
    NicheAnalysisResult,
    run_niche_analysis_background,
)

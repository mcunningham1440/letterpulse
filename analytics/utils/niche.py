import logging
from typing import List

from django.conf import settings
from pydantic import BaseModel

from analytics.prompts import NICHE_ANALYSIS_PROMPT

from .llm import llm_call
from .text import html_to_text_with_links, truncate_url

logger = logging.getLogger(__name__)


class NicheAnalysisResult(BaseModel):
    niche: str
    content_types: List[str]


def _build_niche_analysis_prompt(user, publication):
    """
    Gather the inputs for the niche analysis LLM call:
      - Plain text of the most recent N processed posts (sections concatenated
        in `start_line` order, with link URLs inlined after their anchor text).
      - Best-performing links per section across the most recent M issues, by
        section_name, with each link's CTR shown relative to the section's
        average over the same window.

    Returns the user-message string (or None if there's not enough data — the
    caller should treat that as "skip the analysis" rather than erroring).
    """
    from analytics.models import Post, ProcessedPost, Section, LinkData

    recent_n = settings.NICHE_ANALYSIS_RECENT_POSTS
    history_m = settings.NICHE_ANALYSIS_LINK_HISTORY_ISSUES
    top_links = settings.NICHE_ANALYSIS_TOP_LINKS_PER_SECTION

    # --- Recent processed posts: get text via section_html ---
    recent_processed_post_ids = list(
        ProcessedPost.objects.filter(user=user, publication=publication)
        .select_related('post')
        .filter(post__publish_date__isnull=False)
        .order_by('-post__publish_date')
        .values_list('post_id', flat=True)[:recent_n]
    )

    if not recent_processed_post_ids:
        return None

    recent_posts = list(
        Post.objects.filter(pk__in=recent_processed_post_ids)
        .order_by('-publish_date')
    )

    post_blocks = []
    for post in recent_posts:
        sections = list(
            Section.objects.filter(post=post, user=user).order_by('start_line')
        )
        if not sections:
            continue
        section_blocks = []
        for sec in sections:
            text = html_to_text_with_links(sec.section_html or "", max_url_len=75)
            if not text.strip():
                continue
            label = sec.section_title or sec.section_name
            section_blocks.append(f"### {label}\n{text}")
        if not section_blocks:
            continue
        date_str = post.publish_date.strftime('%Y-%m-%d') if post.publish_date else 'unknown date'
        post_blocks.append(
            f"<post title=\"{post.title}\" date=\"{date_str}\">\n" +
            "\n\n".join(section_blocks) +
            "\n</post>"
        )

    if not post_blocks:
        return None

    posts_section_str = "\n\n".join(post_blocks)

    # --- Best links per section over the last M issues ---
    history_post_ids = list(
        Post.objects.filter(
            user=user, publication=publication,
            publish_date__isnull=False,
        )
        .order_by('-publish_date')
        .values_list('pk', flat=True)[:history_m]
    )

    section_link_blocks = []
    if history_post_ids:
        history_links = list(
            LinkData.objects.filter(
                user=user, publication=publication,
                post_id__in=history_post_ids,
            ).select_related('post')
        )

        # Group by section_name
        by_section = {}
        for link in history_links:
            by_section.setdefault(link.section_name, []).append(link)

        for section_name in sorted(by_section.keys()):
            links = by_section[section_name]
            if not links:
                continue
            section_avg = sum(l.mean_ctr for l in links) / len(links)
            links_sorted = sorted(links, key=lambda l: l.mean_ctr, reverse=True)[:top_links]
            entries = []
            for i, link in enumerate(links_sorted, start=1):
                if section_avg > 0:
                    rel = f"{link.mean_ctr / section_avg:.1f}x avg"
                else:
                    rel = "N/A"
                desc = (link.description or "").strip() or truncate_url(link.raw_url, 75)
                entries.append(f"  {i}. [{rel}] {desc}")
            section_link_blocks.append(
                f"<section name=\"{section_name}\">\n" + "\n".join(entries) + "\n</section>"
            )

    link_history_str = "\n\n".join(section_link_blocks) if section_link_blocks else "(no link history available)"

    return (
        "<recent_posts>\n"
        f"{posts_section_str}\n"
        "</recent_posts>\n\n"
        "<best_links_by_section>\n"
        f"{link_history_str}\n"
        "</best_links_by_section>"
    )


async def _run_niche_analysis_llm(user_prompt):
    """Single LLM call for niche analysis. Returns a parsed NicheAnalysisResult."""
    messages = [
        {"role": "system", "content": NICHE_ANALYSIS_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    response = await llm_call(
        "niche_analysis",
        messages,
        settings.NICHE_ANALYSIS_MODEL,
        settings.NICHE_ANALYSIS_REASONING,
        response_format=NicheAnalysisResult,
        timeout=90.0,
    )
    parsed = response.output[-1].content[0].parsed
    return parsed


def run_niche_analysis_background(task_id):
    """
    Background thread entry point for the Monetize-tab niche analysis. Loads
    the PendingNicheAnalysis row, gathers post text + per-section link history,
    runs the LLM, and writes the result back to the row.
    """
    from asgiref.sync import async_to_sync
    from django.db import connection, close_old_connections
    from analytics.models import PendingNicheAnalysis
    from analytics.llm_tracker import start_tracking, finish_tracking, set_llm_context

    try:
        task = PendingNicheAnalysis.objects.select_related('user', 'publication').get(task_id=task_id)
        task.status = 'running'
        task.save(update_fields=['status'])

        set_llm_context(
            user_id=task.user_id,
            publication_id=task.publication_id,
            task_id=task.task_id,
            task_kind='niche_analysis',
        )
        if settings.ENVIRONMENT == 'local':
            start_tracking()

        user_prompt = _build_niche_analysis_prompt(task.user, task.publication)
        if user_prompt is None:
            # No processed posts yet — caller should not have spawned us, but be
            # defensive. Flagged as a soft fallback per global instructions: we
            # complete the task with empty results rather than erroring, which
            # would surface a generic error toast to the user.
            task.status = 'complete'
            task.niche = ''
            task.content_types = []
            if settings.ENVIRONMENT == 'local':
                task.dev_panel_data = finish_tracking() or {}
                task.save(update_fields=['status', 'niche', 'content_types', 'dev_panel_data'])
            else:
                task.save(update_fields=['status', 'niche', 'content_types'])
            return

        parsed = async_to_sync(_run_niche_analysis_llm)(user_prompt)
        close_old_connections()

        # Cap content_types at 5 entries. Falling short is acceptable — we
        # render whatever the model returned. Going over is silently truncated
        # (flagged as soft fallback per global instructions).
        types = [t.strip() for t in (parsed.content_types or []) if t and t.strip()][:5]

        task.status = 'complete'
        task.niche = (parsed.niche or '').strip()[:255]
        task.content_types = types
        if settings.ENVIRONMENT == 'local':
            task.dev_panel_data = finish_tracking() or {}
            task.save(update_fields=['status', 'niche', 'content_types', 'dev_panel_data'])
        else:
            task.save(update_fields=['status', 'niche', 'content_types'])

    except Exception as e:
        logger.exception("Niche analysis background task failed")
        try:
            close_old_connections()
            task = PendingNicheAnalysis.objects.get(task_id=task_id)
            task.status = 'error'
            task.error_message = str(e)
            task.save(update_fields=['status', 'error_message'])
        except Exception:
            logger.exception("Failed to save error status for niche analysis task")
    finally:
        connection.close()

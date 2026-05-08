import math
from typing import List, Optional

from bs4 import BeautifulSoup
from pydantic import BaseModel

from .llm import llm_call
from .text import html_to_text_with_links


def build_sections_desc(user, publication, post, n_examples=5):
    """Build a human-readable sections description string for the LLM prompt.

    Uses the N posts whose publish_date is closest to the target post as
    "typical" examples for each known section name.

    Args:
        user: Django User object.
        publication: Publication model instance.
        post: Target Post model instance.
        n_examples: Number of closest posts to use as examples.

    Returns:
        A formatted string describing each section with typical descriptions,
        line positions, and first/last HTML lines from nearby posts.
    """
    from analytics.models import Section as SectionModel

    target_date = post.publish_date
    if not target_date:
        return ""

    # All sections for this user/publication, excluding the target post
    sections_qs = SectionModel.objects.filter(
        user=user, publication=publication
    ).exclude(post=post).select_related('post')

    # Group by section_name
    by_name = {}
    for sec in sections_qs:
        if not sec.post.publish_date:
            continue
        by_name.setdefault(sec.section_name, []).append(sec)

    if not by_name:
        return ""

    # Find the 10 closest posts (by publish_date proximity) to determine frequency threshold
    nearby_post_ids = set()
    all_sections_by_proximity = sorted(
        [sec for secs in by_name.values() for sec in secs],
        key=lambda s: abs((s.post.publish_date - target_date).total_seconds()),
    )
    for sec in all_sections_by_proximity:
        nearby_post_ids.add(sec.post_id)
        if len(nearby_post_ids) >= 10:
            break

    n_nearby = len(nearby_post_ids)
    min_appearances = max(1, math.ceil(n_nearby * 0.15))

    # Only include sections that appear in at least 15% of recent posts
    filtered_by_name = {}
    for section_name, rows in by_name.items():
        posts_with_section = {r.post_id for r in rows} & nearby_post_ids
        if len(posts_with_section) >= min_appearances:
            filtered_by_name[section_name] = rows

    if not filtered_by_name:
        return ""

    output_parts = []

    for section_name, rows in sorted(filtered_by_name.items()):
        # Sort by temporal proximity to target post
        rows_sorted = sorted(
            rows,
            key=lambda r: abs((r.post.publish_date - target_date).total_seconds()),
        )
        examples = rows_sorted[:n_examples]

        MAX_CHARS = 500
        example_parts = []
        for i, ex in enumerate(examples):
            total = ex.post_html_length
            start_pct = round(ex.start_line / total * 100) if total else 0
            end_pct = round(ex.end_line / total * 100) if total else 0

            html_lines = ex.section_html.splitlines() if ex.section_html else []
            first_line = html_lines[0].strip() if html_lines else ''
            last_line = html_lines[-1].strip() if html_lines else ''

            full_text = html_to_text_with_links(ex.section_html)
            if len(full_text) > MAX_CHARS:
                half = MAX_CHARS // 2
                full_text = full_text[:half] + " [...] " + full_text[-half:]

            example_parts.append(
                f'<lines={ex.start_line}-{ex.end_line} '
                f'(relative position in HTML={start_pct}%-{end_pct}%)>\n'
                f'<first_html_line>{first_line}</first_html_line>\n'
                f'<last_html_line>{last_line}</last_html_line>\n'
                f'<text_content>{full_text}</text_content>\n'
                f'</example>'
            )

        part = (
            f'<section name="{section_name}">\n'
            + "\n".join(example_parts) + "\n"
            f'</section>'
        )
        output_parts.append(part)

    return "\n".join(output_parts)


class SectionItem(BaseModel):
    name: str
    title: Optional[str]
    start_line: int
    end_line: int


class AllSections(BaseModel):
    sections: List[SectionItem]


async def auto_section(html, user, publication, post, n_examples=5, pretty_html=None):
    """Identify sections in newsletter HTML via a single structured-output LLM call.

    Args:
        html: The newsletter HTML string (raw, not line-numbered).
        user: Django User object.
        publication: Publication model instance.
        post: Target Post model instance.
        n_examples: Number of nearby-post examples per section.
        pretty_html: Optional pre-prettified HTML. If provided, skips internal
            prettify to avoid double-prettifying when the caller already has it.

    Returns:
        List of section dicts with keys: name, title, description,
        start_line, end_line, section_html, post_html_length.
    """
    from asgiref.sync import sync_to_async
    from analytics.prompts import AUTO_SECTION_PROMPT

    if pretty_html is None:
        pretty_html = BeautifulSoup(html, 'html.parser').prettify()
    html_lines = pretty_html.split('\n')
    post_html_length = len(html_lines)
    numbered_html = "\n".join(f"{i+1}: {line}" for i, line in enumerate(html_lines))

    sections_prompt = await sync_to_async(build_sections_desc)(
        user, publication, post, n_examples
    )

    system_content = AUTO_SECTION_PROMPT
    if sections_prompt:
        system_content += f"\nSECTIONS FROM NEARBY POSTS\n{sections_prompt}"
    else:
        system_content += "\nNo other issues processed yet."

    input_messages = [
        {
            "role": "system",
            "content": system_content,
        },
        {
            "role": "user",
            "content": numbered_html,
        }
    ]

    response = await llm_call(
        "auto_section", input_messages, "gpt-5.4", "low",
        response_format=AllSections, user=user
    )

    parsed = response.output_parsed

    # Enrich each section with section_html and post_html_length
    results = []
    for sec in parsed.sections:
        start = max(1, sec.start_line)
        end = min(post_html_length, sec.end_line)
        section_html = "\n".join(html_lines[start - 1:end])
        results.append({
            "name": sec.name,
            "title": sec.title,
            "start_line": start,
            "end_line": end,
            "section_html": section_html,
            "post_html_length": post_html_length,
        })

    return results

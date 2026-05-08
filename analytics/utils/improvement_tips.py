import asyncio
import difflib
import html as html_module
import logging
import re
from typing import List

import aiohttp
from bs4 import BeautifulSoup, NavigableString
from django.conf import settings
from pydantic import BaseModel

from analytics.prompts import IMPROVEMENT_TIP_PROMPT

from .beehiiv_api import fetch_post_html
from .links import format_link_history
from .llm import llm_call

logger = logging.getLogger(__name__)


class ProofreadingTip(BaseModel):
    start_line: int
    end_line: int
    suggestion: str


class ContentTip(BaseModel):
    start_line: int
    end_line: int
    suggestion: str
    old_text: str
    new_text: str
    why: str


class AllImprovementTips(BaseModel):
    proofreading_tips: List[ProofreadingTip]
    content_tips: List[ContentTip]


_INLINE_TAGS_FOR_ANCHOR = frozenset({
    'a', 'span', 'strong', 'em', 'b', 'i', 'u', 'code', 'kbd', 'mark',
    'small', 'sub', 'sup', 'abbr', 'cite', 'q', 'time', 'img', 'br',
    'wbr', 's', 'del', 'ins', 'font', 'label',
})


def _insert_tip_anchor(soup, line, anchor_tag):
    """Insert an inline anchor tag at ~the given prettified sourceline.

    For block elements, the anchor is placed at the start of the block's text
    flow so the block renders as a single paragraph. For inline elements, the
    anchor is placed immediately before them.
    """
    best = None
    best_line = -1
    for el in soup.find_all(True):
        sl = el.sourceline
        if sl is None or sl > line:
            continue
        if sl > best_line:
            best = el
            best_line = sl
    if best is None:
        (soup.body or soup).append(anchor_tag)
        return
    if best.name in _INLINE_TAGS_FOR_ANCHOR:
        best.insert_before(anchor_tag)
        return
    first_text = next(
        (c for c in best.descendants
         if isinstance(c, NavigableString) and c.strip()),
        None,
    )
    if first_text is not None:
        first_text.insert_before(anchor_tag)
    else:
        best.insert(0, anchor_tag)


def _render_new_text_with_diff(old_text: str, new_text: str) -> str:
    """HTML-escape new_text and wrap word-level insertions/replacements vs old_text in <mark class="diff-new">."""
    new_tokens = re.split(r'(\s+)', new_text)
    if not old_text:
        return html_module.escape(new_text)
    old_tokens = re.split(r'(\s+)', old_text)
    matcher = difflib.SequenceMatcher(a=old_tokens, b=new_tokens, autojunk=False)
    parts = []
    for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
        segment = ''.join(new_tokens[j1:j2])
        if not segment:
            continue
        escaped = html_module.escape(segment)
        if tag == 'equal':
            parts.append(escaped)
        elif tag in ('replace', 'insert'):
            parts.append(f'<mark class="diff-new">{escaped}</mark>')
    return ''.join(parts)


async def generate_improvement_tips_html(post, user, publication, beehiiv_token, beehiiv_pub_id):
    """
    Generate annotated two-column HTML with improvement tips for a post.

    Fetches post HTML from Beehiiv, builds numbered text with HTML line mapping,
    gathers link history context, calls LLM for tips, and renders a two-column
    layout with tip cards connected to their target content via SVG connectors.

    Args:
        post: Post model instance
        user: Django User instance
        publication: Publication model instance
        beehiiv_token: Beehiiv API token
        beehiiv_pub_id: Beehiiv publication ID

    Returns:
        Complete annotated HTML string
    """
    from analytics.models import Section

    # --- 1) Fetch post HTML from Beehiiv API ---
    sem = asyncio.Semaphore(5)
    async with aiohttp.ClientSession() as session:
        _, html = await fetch_post_html(session, post.post_id, sem, beehiiv_token, beehiiv_pub_id)
    if not html:
        raise RuntimeError(f"Failed to fetch HTML for post {post.post_id}")

    # --- 2) Build numbered HTML for the LLM ---
    pretty_html = BeautifulSoup(html, 'html.parser').prettify()
    html_lines = pretty_html.split('\n')
    post_html_length = len(html_lines)
    numbered_html = "\n".join(f"{i+1}: {line}" for i, line in enumerate(html_lines))

    # --- 3) Link history with sample titles for every section ---
    def _build_link_history():
        all_section_names = list(
            Section.objects.filter(user=user, publication=publication)
            .order_by('section_name')
            .values_list('section_name', flat=True)
            .distinct()
        )

        ref_date = post.publish_date or post.creation_date

        parts = []
        for sname in sorted(all_section_names):
            representative = Section.objects.filter(
                user=user, publication=publication, section_name=sname
            ).first()
            formatted, count = format_link_history(representative)
            if count > 0:
                nearby_sections = list(
                    Section.objects.filter(
                        user=user,
                        publication=publication,
                        section_name=sname,
                        section_title__isnull=False,
                    )
                    .exclude(section_title='')
                    .exclude(post=post)
                    .select_related('post')
                )
                if ref_date:
                    nearby_sections = sorted(
                        nearby_sections,
                        key=lambda s: abs((s.post.publish_date or s.post.creation_date or ref_date) - ref_date)
                    )
                sample_titles = []
                seen = set()
                for s in nearby_sections:
                    if s.section_title not in seen:
                        seen.add(s.section_title)
                        sample_titles.append(s.section_title)
                    if len(sample_titles) >= 5:
                        break

                titles_str = ""
                if sample_titles:
                    titles_list = "\n".join(f"  - {t}" for t in sample_titles)
                    titles_str = f"\nSample titles from recent issues:\n{titles_list}\n"

                parts.append(
                    f"<section name=\"{sname}\">{titles_str}\n{formatted}\n</section>"
                )

        return "\n\n".join(parts)

    from asgiref.sync import sync_to_async
    link_history_str = await sync_to_async(_build_link_history)()

    # --- 4) Call LLM for tips ---
    messages = [
        {"role": "user", "content": f"<link_history>\n{link_history_str}\n</link_history>"},
        {"role": "user", "content": f"<post title=\"{post.title}\">\n{numbered_html}\n</post>"},
        {"role": "system", "content": IMPROVEMENT_TIP_PROMPT},
    ]

    response = await llm_call(
        "generate_improvement_tips",
        messages,
        settings.IMPROVEMENT_TIPS_MODEL,
        settings.IMPROVEMENT_TIPS_REASONING,
        response_format=AllImprovementTips,
        user=user,
    )
    tips = response.output[-1].content[0].parsed

    # --- 5) Build two-column annotated HTML ---
    tip_type_to_header = {
        "content": "\U0001f4f0 Content Tip",
        "proofreading": "\u270d\ufe0f Proofreading",
    }
    tip_type_to_header_color = {
        "content": "#E65100",
        "proofreading": "#0D47A1",
    }

    # Tag each tip with its kind so rendering can dispatch on it.
    typed_tips = (
        [('proofreading', t) for t in tips.proofreading_tips]
        + [('content', t) for t in tips.content_tips]
    )

    # Tips' start_line/end_line now refer directly to HTML lines. Take the midpoint
    # so the anchor lands in the body of a multi-line span, not at its start.
    tips_with_anchors = []
    for tip_kind, t in typed_tips:
        lo, hi = sorted((t.start_line, t.end_line))
        lo = max(1, lo)
        hi = min(post_html_length, hi)
        if hi < lo:
            continue
        mid = (lo + hi) // 2
        tips_with_anchors.append((tip_kind, t, mid))

    tips_with_anchors.sort(key=lambda tup: tup[2])

    # Insert anchor spans via DOM manipulation so logically-continuous paragraphs
    # that are physically split across many prettified lines (by inline elements
    # like <a>) stay grouped in the rendered output.
    soup_render = BeautifulSoup(pretty_html, 'html.parser')
    for i, (tip_kind, tip, mid) in enumerate(tips_with_anchors):
        marker_id = f"tip-target-{i}"
        anchor = soup_render.new_tag("span", attrs={
            "id": marker_id,
            "data-tip-anchor": "true",
            "data-tip-type": tip_kind,
        })
        _insert_tip_anchor(soup_render, mid, anchor)

    newsletter_html = str(soup_render)

    copy_icon_svg = (
        '<svg class="copy-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>'
        '<path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>'
        '</svg>'
        '<svg class="check-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" '
        'style="display:none;">'
        '<polyline points="20 6 9 17 4 12"></polyline>'
        '</svg>'
    )

    # Build tip card divs
    tip_cards = []
    for i, (tip_kind, tip, _mid) in enumerate(tips_with_anchors):
        header = tip_type_to_header[tip_kind]
        header_color = tip_type_to_header_color[tip_kind]
        safe_suggestion = html_module.escape(tip.suggestion)

        change_block = ""
        why_block = ""
        if tip_kind == 'content':
            has_old = bool(tip.old_text and tip.old_text.strip())
            has_new = bool(tip.new_text and tip.new_text.strip())
            if has_old or has_new:
                old_block = (
                    f'<div class="old-box"><div class="old-text">{html_module.escape(tip.old_text)}</div></div>'
                    if has_old else ''
                )
                copy_btn = (
                    f'<button type="button" class="copy-btn" title="Copy to clipboard" '
                    f'aria-label="Copy suggested text">{copy_icon_svg}</button>'
                ) if has_new else ''
                new_rendered = _render_new_text_with_diff(tip.old_text or '', tip.new_text)
                new_block = (
                    f'<div class="new-box">'
                    f'<div class="new-text copy-source">{new_rendered}</div>'
                    f'{copy_btn}'
                    f'</div>'
                ) if has_new else ''
                change_block = f"""
        <div style="font-weight: bold; margin-bottom: 4px; margin-top: 10px; color: {header_color};">Suggested change</div>
        <div class="suggested-change">{old_block}{new_block}</div>"""
            if tip.why and tip.why.strip() and tip.why.lower() != 'none':
                safe_why = html_module.escape(tip.why)
                why_block = f"""
        <div style="font-weight: bold; margin-bottom: 4px; margin-top: 10px; color: {header_color};">Why?</div>
        <div>{safe_why}</div>"""

        tip_cards.append(f"""
    <div class="tip-card {tip_kind}" id="tip-card-{i}" data-target="tip-target-{i}" data-tip-type="{tip_kind}">
        <div style="font-weight: bold; margin-bottom: 4px; color: {header_color};">{header}</div>
        <div style="margin-bottom: 10px;">{safe_suggestion}</div>{change_block}{why_block}
    </div>""")

    tip_cards_html = '\n'.join(tip_cards)

    result_html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
    body {{
        margin: 0;
        padding: 0;
        background: #f5f5f5;
    }}
    .annotated-wrapper {{
        display: flex;
        max-width: 1400px;
        margin: 0 auto;
        position: relative;
    }}
    .tips-column {{
        flex: 0 0 30%;
        max-width: 30%;
        position: relative;
        min-height: 100%;
    }}
    .newsletter-column {{
        flex: 0 0 70%;
        max-width: 70%;
        background: white;
        box-shadow: 0 1px 4px rgba(0,0,0,0.1);
        position: relative;
    }}
    .tip-card {{
        position: absolute;
        width: calc(100% - 32px);
        right: 0;
        background-color: #FFFDE7;
        border-right: 4px solid #F9A825;
        padding: 12px 14px;
        font-family: Arial, sans-serif;
        font-size: 13px;
        line-height: 1.5;
        color: #333;
        border-radius: 4px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        transition: box-shadow 0.2s;
    }}
    .tip-card.proofreading {{
        background-color: #E3F2FD;
        border-right-color: #1976D2;
    }}
    .tip-card:hover {{
        box-shadow: 0 2px 8px rgba(0,0,0,0.2);
    }}
    svg.connectors {{
        position: absolute;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        pointer-events: none;
        z-index: 10;
    }}
    [data-tip-anchor] {{
        background-color: #FFF9C4;
        outline: 2px solid #F9A825;
        outline-offset: 2px;
        border-radius: 2px;
    }}
    [data-tip-anchor][data-tip-type="proofreading"] {{
        background-color: #BBDEFB;
        outline-color: #1976D2;
    }}
    .top-banner {{
        position: absolute;
        top: 16px;
        right: 0;
        width: calc(100% - 32px);
        background-color: #FFFDE7;
        border-right: 4px solid #F9A825;
        padding: 12px 14px;
        font-family: Arial, sans-serif;
        font-size: 13px;
        line-height: 1.5;
        color: #333;
        border-radius: 4px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.1);
    }}
    .suggested-change {{
        display: flex;
        flex-direction: column;
        gap: 6px;
    }}
    .old-box {{
        background-color: rgba(0,0,0,0.04);
        border-radius: 4px;
        padding: 8px 10px;
    }}
    .new-box {{
        display: flex;
        align-items: flex-start;
        gap: 8px;
        background-color: rgba(46,125,50,0.08);
        border-left: 3px solid #2e7d32;
        border-radius: 4px;
        padding: 8px 10px;
    }}
    .old-text {{
        text-decoration: line-through;
        color: #888;
        white-space: pre-wrap;
        word-break: break-word;
    }}
    .new-text {{
        flex: 1 1 auto;
        min-width: 0;
        white-space: pre-wrap;
        word-break: break-word;
    }}
    .new-text mark.diff-new {{
        background-color: #FFF59D;
        color: inherit;
        padding: 0 2px;
        border-radius: 2px;
    }}
    .copy-btn {{
        flex: 0 0 auto;
        background: transparent;
        border: 1px solid rgba(0,0,0,0.15);
        border-radius: 3px;
        padding: 3px 6px;
        cursor: pointer;
        color: #555;
        line-height: 0;
        transition: background 0.15s, color 0.15s, border-color 0.15s;
    }}
    .copy-btn:hover {{
        background: rgba(0,0,0,0.06);
        color: #000;
    }}
    .copy-btn.copied {{
        color: #2e7d32;
        border-color: #2e7d32;
    }}
</style>
</head>
<body>
<div class="annotated-wrapper" id="annotated-wrapper">
    <div class="tips-column" id="tips-column">
        <div class="top-banner">Suggested content changes appear below.</div>
        {tip_cards_html}
    </div>
    <div class="newsletter-column" id="newsletter-column">
        {newsletter_html}
    </div>
    <svg class="connectors" id="connectors-svg"></svg>
</div>
<script>
    function positionCardsAndDrawConnectors() {{
        const wrapper = document.getElementById('annotated-wrapper');
        const svg = document.getElementById('connectors-svg');
        const tipsCol = document.getElementById('tips-column');
        const wrapperRect = wrapper.getBoundingClientRect();

        svg.setAttribute('width', wrapperRect.width);
        svg.setAttribute('height', wrapperRect.height);
        svg.style.width = wrapperRect.width + 'px';
        svg.style.height = wrapperRect.height + 'px';
        svg.innerHTML = '';

        const cards = Array.from(document.querySelectorAll('.tip-card'));
        let minNextTop = 80;

        cards.forEach(function(card) {{
            const targetId = card.getAttribute('data-target');
            const target = document.getElementById(targetId);
            if (!target) return;

            const targetRect = target.getBoundingClientRect();
            const desiredTop = targetRect.top + targetRect.height / 2 - wrapperRect.top - 20;
            const actualTop = Math.max(desiredTop, minNextTop);
            card.style.top = actualTop + 'px';

            const cardHeight = card.getBoundingClientRect().height;
            minNextTop = actualTop + cardHeight + 12;

            const cardRect = card.getBoundingClientRect();
            const x1 = cardRect.right - wrapperRect.left;
            const y1 = cardRect.top + 20 - wrapperRect.top;
            const x2 = targetRect.left - wrapperRect.left;
            const y2 = targetRect.top + targetRect.height / 2 - wrapperRect.top;

            const midX = (x1 + x2) / 2;
            const connectorColor = card.classList.contains('proofreading') ? '#1976D2' : '#F9A825';
            const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
            path.setAttribute('d', 'M ' + x1 + ' ' + y1 + ' C ' + midX + ' ' + y1 + ' ' + midX + ' ' + y2 + ' ' + x2 + ' ' + y2);
            path.setAttribute('stroke', connectorColor);
            path.setAttribute('stroke-width', '2');
            path.setAttribute('fill', 'none');
            path.setAttribute('stroke-dasharray', '6,3');

            const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
            circle.setAttribute('cx', x2);
            circle.setAttribute('cy', y2);
            circle.setAttribute('r', '4');
            circle.setAttribute('fill', connectorColor);

            svg.appendChild(path);
            svg.appendChild(circle);
        }});

        const lastCard = cards[cards.length - 1];
        if (lastCard) {{
            const lastBottom = parseFloat(lastCard.style.top) + lastCard.getBoundingClientRect().height + 20;
            tipsCol.style.minHeight = lastBottom + 'px';
        }}
    }}

    window.addEventListener('load', positionCardsAndDrawConnectors);
    window.addEventListener('resize', positionCardsAndDrawConnectors);

    function fallbackCopy(text) {{
        try {{
            const ta = document.createElement('textarea');
            ta.value = text;
            ta.style.position = 'fixed';
            ta.style.top = '0';
            ta.style.left = '0';
            ta.style.opacity = '0';
            document.body.appendChild(ta);
            ta.focus();
            ta.select();
            const ok = document.execCommand('copy');
            document.body.removeChild(ta);
            return ok;
        }} catch (e) {{
            return false;
        }}
    }}

    function flashCopied(btn, ok) {{
        btn.classList.toggle('copied', ok);
        btn.setAttribute('title', ok ? 'Copied!' : 'Copy failed');
        const copyIcon = btn.querySelector('.copy-icon');
        const checkIcon = btn.querySelector('.check-icon');
        if (ok && copyIcon && checkIcon) {{
            copyIcon.style.display = 'none';
            checkIcon.style.display = '';
        }}
        setTimeout(function() {{
            btn.classList.remove('copied');
            btn.setAttribute('title', 'Copy to clipboard');
            if (copyIcon && checkIcon) {{
                copyIcon.style.display = '';
                checkIcon.style.display = 'none';
            }}
        }}, 1500);
    }}

    document.addEventListener('click', function(e) {{
        const btn = e.target.closest('.copy-btn');
        if (!btn) return;
        const card = btn.closest('.tip-card');
        if (!card) return;
        const src = card.querySelector('.copy-source');
        if (!src) return;
        const text = src.innerText;
        if (navigator.clipboard && navigator.clipboard.writeText) {{
            navigator.clipboard.writeText(text).then(
                function() {{ flashCopied(btn, true); }},
                function() {{ flashCopied(btn, fallbackCopy(text)); }}
            );
        }} else {{
            flashCopied(btn, fallbackCopy(text));
        }}
    }});
</script>
</body>
</html>"""

    return result_html


def run_improvement_tips_background(task_id):
    """
    Background thread entry point for generating improvement tips.
    Loads the PendingImprovementTips task, generates annotated HTML, saves result.
    """
    from asgiref.sync import async_to_sync
    from django.db import connection, close_old_connections
    from analytics.models import PendingImprovementTips, UsageAccount

    try:
        task = PendingImprovementTips.objects.get(task_id=task_id)
        task.status = 'running'
        task.save(update_fields=['status'])

        from analytics.llm_tracker import start_tracking, finish_tracking, set_llm_context
        set_llm_context(
            user_id=task.user_id,
            publication_id=task.publication_id,
            task_id=task.task_id,
            task_kind='improvement_tips',
        )
        if settings.ENVIRONMENT == 'local':
            start_tracking()

        post = task.post
        user = task.user
        publication = task.publication

        try:
            usage = UsageAccount.objects.get(user=user)
            beehiiv_token = usage.beehiiv_token
            beehiiv_pub_id = usage.beehiiv_pub_id
        except UsageAccount.DoesNotExist:
            raise RuntimeError("No API credentials configured")

        result_html = async_to_sync(generate_improvement_tips_html)(
            post, user, publication, beehiiv_token, beehiiv_pub_id
        )

        # Long-running LLM calls can leave the DB connection stale (RDS-side
        # idle timeout). Drop any unhealthy connection so the next ORM call
        # opens a fresh one instead of raising SSL SYSCALL EOF.
        close_old_connections()

        task.status = 'complete'
        task.result_html = result_html
        if settings.ENVIRONMENT == 'local':
            task.dev_panel_data = finish_tracking() or {}
            task.save(update_fields=['status', 'result_html', 'dev_panel_data'])
        else:
            task.save(update_fields=['status', 'result_html'])

        # Mark that the user has used post improvement
        from analytics.models import Feedback
        Feedback.objects.get_or_create(
            user=task.user, feature='used_post_improvement',
            defaults={'response': 'completed'}
        )

    except Exception as e:
        logger.exception("Improvement tips background task failed")
        try:
            close_old_connections()
            task = PendingImprovementTips.objects.get(task_id=task_id)
            task.status = 'error'
            task.error_message = str(e)
            task.save(update_fields=['status', 'error_message'])
        except Exception:
            logger.exception("Failed to save error status for improvement tips task")
    finally:
        connection.close()

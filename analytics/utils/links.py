import asyncio
import logging
import math
from collections import Counter
from typing import List

from bs4 import BeautifulSoup
from django.conf import settings
from Levenshtein import distance as levenshtein_distance
from pydantic import BaseModel

from .beehiiv_api import fetch_post_clicks
from .llm import llm_call
from .text import truncate_url

logger = logging.getLogger(__name__)


def match_links_with_clicks(html_links_raw, clicks_dict):
    """
    Match click report URLs to HTML links using normalized Levenshtein distance.

    Each click URL is matched to the HTML URL(s) with the smallest normalized
    Levenshtein distance. If multiple HTML URLs share the same minimum distance,
    the click URL's clicks are added to each of them. Each HTML URL's final click
    count is the sum of clicks from all click URLs that include it among their
    closest matches.

    When a processed URL (stripped of &_bhlid= tracking params) appears multiple
    times in the HTML, the click count is averaged across all occurrences, since
    the Beehiiv API cannot distinguish which occurrence was clicked.

    Args:
        html_links_raw: List of raw HTML link hrefs (as-is from the HTML)
        clicks_dict: Dictionary mapping click report URLs to click counts

    Returns:
        Tuple of (link_to_clicks, duplicate_raw_urls) where:
        - link_to_clicks: Dictionary mapping raw HTML links to their click counts
          (averaged for URLs whose processed form appears multiple times)
        - duplicate_raw_urls: Set of raw URLs whose processed form appears more
          than once in the HTML
    """
    if not clicks_dict or not html_links_raw:
        return {}, set()

    # Process HTML links by stripping &_bhlid= and everything after (for matching only)
    html_links_processed = [link.split("&_bhlid=")[0] for link in html_links_raw]

    # Count occurrences of each processed URL
    processed_counts = Counter(html_links_processed)

    # Identify raw URLs whose processed form appears more than once
    duplicate_raw_urls = {
        html_links_raw[i]
        for i in range(len(html_links_raw))
        if processed_counts[html_links_processed[i]] > 1
    }

    # Result: raw HTML link -> click count
    link_to_clicks = {}

    for click_url, click_count in clicks_dict.items():
        # Compute normalized Levenshtein distance to each processed HTML URL
        distances = []
        for processed_url in html_links_processed:
            dist = levenshtein_distance(click_url, processed_url)
            max_len = max(len(click_url), len(processed_url))
            distances.append(dist / max_len if max_len > 0 else 0)

        min_dist = min(distances)

        # Add clicks to every HTML URL tied at the minimum distance
        for i, dist in enumerate(distances):
            if dist == min_dist:
                raw_url = html_links_raw[i]
                link_to_clicks[raw_url] = link_to_clicks.get(raw_url, 0) + click_count

    # Average clicks for URLs whose processed form appears multiple times
    for raw_url in duplicate_raw_urls:
        if raw_url in link_to_clicks:
            processed = raw_url.split("&_bhlid=")[0]
            link_to_clicks[raw_url] /= processed_counts[processed]

    return link_to_clicks, duplicate_raw_urls


def allocate_links_to_sections(section_link_counts, top_n):
    """Divide top_n link slots fairly across sections.

    Each section receives either an equal share or all its links, whichever is
    lesser.  Surplus from capped sections is redistributed iteratively.

    Args:
        section_link_counts: dict mapping section_name -> number of available links.
        top_n: Total link budget to distribute.

    Returns:
        dict mapping section_name -> allocated count.
    """
    if not section_link_counts or top_n <= 0:
        return {name: 0 for name in section_link_counts}

    total_available = sum(section_link_counts.values())
    if top_n >= total_available:
        return dict(section_link_counts)

    allocation = {name: 0 for name in section_link_counts}
    remaining_budget = top_n
    uncapped = {name: count for name, count in section_link_counts.items() if count > 0}

    while remaining_budget > 0 and uncapped:
        equal_share = remaining_budget // len(uncapped)
        if equal_share == 0:
            # Distribute remainder one-by-one to sections with the most links
            for name, _ in sorted(uncapped.items(), key=lambda x: -x[1]):
                if remaining_budget <= 0:
                    break
                allocation[name] += 1
                remaining_budget -= 1
            break

        newly_capped = []
        for name, count in uncapped.items():
            give = min(equal_share, count - allocation[name])
            allocation[name] += give
            remaining_budget -= give
            if allocation[name] >= count:
                newly_capped.append(name)

        for name in newly_capped:
            del uncapped[name]

    return allocation


def select_top_bottom(link_stats, n):
    """Select the top and bottom links by CTR from a sorted list.

    Args:
        link_stats: List of link dicts, sorted by CTR descending.
        n: Number of links to select.

    Returns:
        List of selected link dicts (top ceil(n/2) + bottom floor(n/2)).
    """
    if n >= len(link_stats):
        return list(link_stats)
    if n <= 0:
        return []
    if n == 1:
        return [link_stats[0]]
    top_half = math.ceil(n / 2)
    bottom_half = n - top_half
    return link_stats[:top_half] + link_stats[-bottom_half:]


def format_link_history(section, max_links=60, max_url_len=50):
    """
    Build a formatted string of historical link performance for a section_name.
    Queries ALL LinkData matching the section_name for this user/publication,
    ordered by CTR descending. If count exceeds max_links, shows top half and
    bottom half with a truncation note. CTR is shown relative to the section average.

    Returns (formatted_string, total_link_count).
    """
    from analytics.models import LinkData

    links = LinkData.objects.filter(
        user=section.user,
        publication=section.publication,
        section_name=section.section_name,
    ).order_by('-mean_ctr')

    total = links.count()
    if total == 0:
        return "", 0

    all_links = list(links)
    avg_ctr = sum(l.mean_ctr for l in all_links) / total

    if total > max_links:
        half = max_links // 2
        selected = all_links[:half] + all_links[-half:]
        truncation_note = (
            f"Note: Only the top {half} and bottom {half} links by CTR are shown "
            f"({total - max_links} middle links omitted).\n\n"
        )
    else:
        selected = all_links
        truncation_note = ""

    lines = [truncation_note] if truncation_note else []
    for i, link in enumerate(selected, 1):
        if avg_ctr > 0:
            relative = link.mean_ctr / avg_ctr
            rel_str = f"{relative:.1f}x avg"
        else:
            rel_str = "N/A"
        lines.append(
            f"{i}. [{rel_str}] {link.description}\n"
            f"   URL: {truncate_url(link.raw_url, max_url_len)}"
        )

    return "\n".join(lines), total


class LinkDescription(BaseModel):
    tag_id: int
    description: str


class AllLinkDescriptions(BaseModel):
    links: List[LinkDescription]


async def process_post_links(session, post_id, user, beehiiv_token, beehiiv_pub_id,
                             sections, pretty_html):
    """
    Extract, describe, and score clicked links from a single post, grouped by section.

    Click matching is performed at the post level. Links are then assigned to
    sections based on their line position in the prettified HTML. Each section
    receives an allocation from LINK_PROCESS_TOP_N; if a section has more links
    than its allocation, the top and bottom links by CTR are selected.

    Args:
        session: aiohttp.ClientSession
        post_id: Beehiiv post ID
        user: Django user object
        beehiiv_token: Beehiiv API bearer token
        beehiiv_pub_id: Beehiiv publication ID
        sections: List of section dicts from auto_section (name, start_line, end_line, ...)
        pretty_html: The prettified HTML string used by auto_section

    Returns:
        List of dicts with keys: post_id, raw_url, description, section_name,
        rank_in_section, mean_ctr, mean_clicks
    """
    from analytics.models import Post

    post = await asyncio.to_thread(
        lambda: Post.objects.get(post_id=post_id, user=user)
    )
    unique_opens = post.unique_email_opens or 0

    if not sections:
        return []

    # --- Fetch clicks from Beehiiv API ---
    semaphore = asyncio.Semaphore(5)
    _, clicks_dict = await fetch_post_clicks(session, post_id, semaphore, beehiiv_token, beehiiv_pub_id)
    if clicks_dict is None:
        clicks_dict = {}

    # --- Extract links from prettified HTML using BeautifulSoup ---
    soup = BeautifulSoup(pretty_html, 'html.parser')
    html_links = soup.find_all('a', href=True)

    line_links = []  # list of (1-based line number, raw_url)
    for link in html_links:
        line_links.append((link.sourceline, link['href']))

    if not line_links:
        return []

    # --- Match clicks at post level ---
    all_raw_urls = [url for _, url in line_links]
    link_to_clicks, _ = match_links_with_clicks(all_raw_urls, clicks_dict)

    # --- Build section range lookup ---
    section_ranges = []
    for sec in sections:
        section_ranges.append((sec['start_line'], sec['end_line'], sec['name']))

    def find_section(line_num):
        for start, end, name in section_ranges:
            if start <= line_num <= end:
                return name
        return None

    # --- Assign links to sections and deduplicate per section ---
    # Key: (section_name, processed_url) -> {ctrs, clicks}
    section_groups = {}
    for line_num, raw_url in line_links:
        sec_name = find_section(line_num)
        if sec_name is None:
            continue
        processed = raw_url.split("&_bhlid=")[0]
        key = (sec_name, processed)
        clicks = link_to_clicks.get(raw_url, 0)
        ctr = (clicks / unique_opens * 100) if unique_opens > 0 else 0
        if key not in section_groups:
            section_groups[key] = {'ctrs': [], 'clicks': []}
        section_groups[key]['ctrs'].append(ctr)
        section_groups[key]['clicks'].append(clicks)

    # --- Build per-section link stats ---
    # section_name -> sorted list of link stat dicts (CTR desc)
    links_by_section = {}
    for (sec_name, url), data in section_groups.items():
        mean_ctr = sum(data['ctrs']) / len(data['ctrs'])
        mean_clicks = sum(data['clicks']) / len(data['clicks'])
        links_by_section.setdefault(sec_name, []).append({
            'url': url,
            'mean_ctr': mean_ctr,
            'mean_clicks': mean_clicks,
        })

    # Sort each section by CTR descending and assign rank_in_section (all links)
    # Filter to links with clicks > 0
    for sec_name in links_by_section:
        links_by_section[sec_name].sort(key=lambda x: x['mean_ctr'], reverse=True)
        for rank, ls in enumerate(links_by_section[sec_name], start=1):
            ls['rank_in_section'] = rank
        links_by_section[sec_name] = [
            ls for ls in links_by_section[sec_name] if ls['mean_ctr'] > 0
        ]

    # --- Allocate and select ---
    section_link_counts = {
        sec_name: len(stats) for sec_name, stats in links_by_section.items()
    }
    top_n = settings.LINK_PROCESS_TOP_N
    allocation = allocate_links_to_sections(section_link_counts, top_n)

    selected_links = []  # list of (section_name, link_stat_dict)
    for sec_name, stats in links_by_section.items():
        n = allocation.get(sec_name, 0)
        chosen = select_top_bottom(stats, n)
        for ls in chosen:
            selected_links.append((sec_name, ls))

    if not selected_links:
        return []

    # Sort all selected links by CTR descending so the LLM_TAG_N indices line
    # up with global CTR rank when we tag links in the prettified HTML below.
    selected_links.sort(key=lambda x: x[1]['mean_ctr'], reverse=True)

    url_to_tag = {}
    for i, (sec_name, ls) in enumerate(selected_links, start=1):
        url_to_tag[ls['url']] = i

    # Tag links in prettified HTML
    soup_tagged = BeautifulSoup(pretty_html, 'html.parser')
    for link in soup_tagged.find_all('a', href=True):
        processed = link['href'].split("&_bhlid=")[0]
        if processed in url_to_tag:
            link['data-tag'] = f"LINK_TAG_{url_to_tag[processed]}"

    tagged_html = str(soup_tagged)
    n_links = len(selected_links)

    tag_summary = "\n".join(
        f"  LINK_TAG_{i}: URL={ls['url'][:120]}"
        for i, (_, ls) in enumerate(selected_links, start=1)
    )

    messages = [
        {"role": "user", "content": f"""Below is the HTML of a newsletter post. Certain links have been tagged with a data-tag attribute, e.g. <a data-tag="LINK_TAG_1" href="...">.

For each tagged link, give a brief, specific description of what likely appears at that URL based on the surrounding context in the newsletter. Be specific — for example, "The GitHub repo for LangChain, an open-source framework for building AI agents" rather than "A GitHub link".

Some URLs may appear multiple times; these will be given the same tag. In this case, you may use all of their contexts to infer what the URL is.

There are exactly {n_links} tagged links. Return exactly {n_links} descriptions, one per tag ID.

Tagged links:
{tag_summary}

Newsletter HTML:
{tagged_html}"""}
    ]

    max_retries = settings.LINK_PROCESS_MAX_RETRIES
    for attempt in range(1, max_retries + 2):
        response = await llm_call("process_post_links", messages, "gpt-5.4-mini", "low",
                                   response_format=AllLinkDescriptions, user=user)
        parsed = response.output[-1].content[0].parsed
        if len(parsed.links) == n_links:
            break
        if attempt <= max_retries:
            logger.warning(
                f"[{post.title}] Expected {n_links} descriptions, got {len(parsed.links)} — "
                f"retrying (attempt {attempt}/{max_retries})"
            )
        else:
            raise ValueError(
                f"[{post.title}] Expected {n_links} descriptions, got {len(parsed.links)} "
                f"after {max_retries} retries"
            )

    desc_by_tag = {ld.tag_id: ld.description for ld in parsed.links}

    rows = []
    for i, (sec_name, ls) in enumerate(selected_links, start=1):
        rows.append({
            'post_id': post_id,
            'raw_url': ls['url'],
            'description': desc_by_tag.get(i, ''),
            'section_name': sec_name,
            'rank_in_section': ls['rank_in_section'],
            'mean_ctr': round(ls['mean_ctr'], 2),
            'mean_clicks': round(ls['mean_clicks'], 1),
        })
    return rows

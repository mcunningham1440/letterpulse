"""
Unit tests for analytics.utils.niche._build_niche_analysis_prompt.

The prompt builder is pure-ish: it queries the ORM for the user's last N
processed posts, concatenates their section text in `start_line` order, and
appends per-section link history with CTR ranked relative to each section's
average. We don't exercise the LLM call itself — just the prompt assembly.

Edge cases:
- Returns None when there are no processed posts (defensive guard).
- Returns None when processed posts exist but contain no usable section text.
- Falls back to "(no link history available)" when no LinkData exists in
  the history window.
- Respects NICHE_ANALYSIS_TOP_LINKS_PER_SECTION cap and relative-CTR labels.
- Uses section_title when present, else section_name, for the section
  heading inside post text.
"""
from __future__ import annotations

from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from analytics.tests.factories import (
    make_link_data,
    make_post,
    make_processed_post,
    make_publication,
    make_section,
    make_user,
)
from analytics.utils.niche import _build_niche_analysis_prompt


def _hours_ago(h):
    return timezone.now() - timedelta(hours=h)


class BuildNichePromptTests(TestCase):

    def test_returns_none_when_no_processed_posts(self):
        user, _ = make_user()
        pub = make_publication()
        # An unprocessed post should not trigger prompt build.
        make_post(user, pub, post_id="np", publish_date=_hours_ago(72))
        self.assertIsNone(_build_niche_analysis_prompt(user, pub))

    def test_returns_none_when_processed_posts_have_no_sections(self):
        # ProcessedPost rows exist, but no Section rows -> nothing usable to
        # send to the model. Guard returns None (caller treats as "skip").
        user, _ = make_user()
        pub = make_publication()
        p = make_post(user, pub, post_id="p1", publish_date=_hours_ago(72))
        make_processed_post(p)
        self.assertIsNone(_build_niche_analysis_prompt(user, pub))

    def test_returns_none_when_only_empty_section_html(self):
        # ProcessedPost + Section, but section_html stringifies to no text.
        user, _ = make_user()
        pub = make_publication()
        p = make_post(user, pub, post_id="p1", publish_date=_hours_ago(72))
        make_processed_post(p)
        make_section(p, section_name="Empty", section_html="   ")
        self.assertIsNone(_build_niche_analysis_prompt(user, pub))

    def test_post_block_wraps_section_text_with_title_label(self):
        user, _ = make_user()
        pub = make_publication()
        p = make_post(user, pub, post_id="p1", title="Hello World",
                      publish_date=_hours_ago(72))
        make_processed_post(p)
        make_section(
            p,
            section_name="Main Essay",
            section_title="The Headline",
            section_html="<p>Body content here.</p>",
        )
        out = _build_niche_analysis_prompt(user, pub)
        self.assertIsNotNone(out)
        # Section heading uses section_title (preferred over section_name).
        self.assertIn("### The Headline", out)
        self.assertIn("Body content here", out)
        # Post-wrapper tag carries title + ISO date.
        self.assertIn("<post title=\"Hello World\"", out)
        # ISO date format YYYY-MM-DD from publish_date.
        self.assertIn(p.publish_date.strftime("%Y-%m-%d"), out)
        # Outer envelope tags present.
        self.assertIn("<recent_posts>", out)
        self.assertIn("<best_links_by_section>", out)

    def test_section_heading_falls_back_to_section_name(self):
        # No section_title set -> use section_name as the heading label.
        user, _ = make_user()
        pub = make_publication()
        p = make_post(user, pub, post_id="p1", publish_date=_hours_ago(72))
        make_processed_post(p)
        make_section(p, section_name="News Roundup", section_title=None,
                     section_html="<p>News here</p>")
        out = _build_niche_analysis_prompt(user, pub)
        self.assertIn("### News Roundup", out)

    def test_sections_ordered_by_start_line(self):
        # Mixed start_line order in DB; prompt block must concatenate by
        # ascending start_line so the model reads the issue top-to-bottom.
        user, _ = make_user()
        pub = make_publication()
        p = make_post(user, pub, post_id="p1", publish_date=_hours_ago(72))
        make_processed_post(p)
        make_section(p, section_name="Late", start_line=50, end_line=60,
                     section_html="<p>LATE_MARKER</p>")
        make_section(p, section_name="Early", start_line=1, end_line=10,
                     section_html="<p>EARLY_MARKER</p>")
        out = _build_niche_analysis_prompt(user, pub)
        self.assertLess(out.index("EARLY_MARKER"), out.index("LATE_MARKER"))

    @override_settings(NICHE_ANALYSIS_RECENT_POSTS=2)
    def test_recent_posts_cap_respected(self):
        # 3 processed posts, but cap=2 -> only the two most recent feed the
        # prompt. The oldest post's marker should not appear.
        user, _ = make_user()
        pub = make_publication()
        recent = make_post(user, pub, post_id="recent",
                           publish_date=_hours_ago(24))
        middle = make_post(user, pub, post_id="middle",
                           publish_date=_hours_ago(72))
        oldest = make_post(user, pub, post_id="oldest",
                           publish_date=_hours_ago(240))
        for p, marker in [(recent, "RECENT"), (middle, "MIDDLE"), (oldest, "OLDEST")]:
            make_processed_post(p)
            make_section(p, section_name="Body", section_title=None,
                         start_line=1, end_line=5,
                         section_html=f"<p>{marker}</p>")

        out = _build_niche_analysis_prompt(user, pub)
        self.assertIn("RECENT", out)
        self.assertIn("MIDDLE", out)
        self.assertNotIn("OLDEST", out)

    def test_no_link_history_falls_back_to_placeholder(self):
        # Processed post with section text, but no LinkData -> the prompt
        # still builds, with the link-history block replaced by a "(no link
        # history available)" placeholder.
        user, _ = make_user()
        pub = make_publication()
        p = make_post(user, pub, post_id="p1", publish_date=_hours_ago(72))
        make_processed_post(p)
        make_section(p, section_name="Main", section_html="<p>text</p>")
        out = _build_niche_analysis_prompt(user, pub)
        self.assertIn("(no link history available)", out)

    @override_settings(NICHE_ANALYSIS_TOP_LINKS_PER_SECTION=2)
    def test_link_history_caps_top_links_per_section(self):
        user, _ = make_user()
        pub = make_publication()
        p = make_post(user, pub, post_id="p1", publish_date=_hours_ago(72))
        make_processed_post(p)
        make_section(p, section_name="Main", section_html="<p>text</p>")

        # 4 links in one section, varying CTR. Only top 2 should render.
        ctrs_and_descs = [(1.0, "low"), (5.0, "high"), (3.0, "mid"), (4.5, "near-high")]
        for ctr, desc in ctrs_and_descs:
            make_link_data(
                p, raw_url=f"https://e{desc}.example.com",
                section_name="Main", mean_ctr=ctr, description=desc,
            )

        out = _build_niche_analysis_prompt(user, pub)
        # "high" (5.0) and "near-high" (4.5) should appear; "mid" and "low" not.
        self.assertIn("high", out)
        self.assertIn("near-high", out)
        self.assertNotIn("low", out.split("<best_links_by_section>")[1])
        self.assertNotIn(" mid", out.split("<best_links_by_section>")[1])

    def test_link_history_renders_relative_ctr_multiplier(self):
        # Two links in one section, CTRs 1.0 and 5.0 -> avg=3.0. The high
        # link should render with "~1.7x avg" relative label, and ranking
        # should put 5.0 before 1.0.
        user, _ = make_user()
        pub = make_publication()
        p = make_post(user, pub, post_id="p1", publish_date=_hours_ago(72))
        make_processed_post(p)
        make_section(p, section_name="Main", section_html="<p>x</p>")
        make_link_data(p, raw_url="https://hi.example.com",
                       section_name="Main", mean_ctr=5.0, description="hi")
        make_link_data(p, raw_url="https://lo.example.com",
                       section_name="Main", mean_ctr=1.0, description="lo")

        out = _build_niche_analysis_prompt(user, pub)
        # high-CTR appears first inside the section block.
        section_block = out.split("<best_links_by_section>")[1]
        self.assertLess(section_block.index("hi"), section_block.index("lo"))
        # avg=3.0 -> 5.0/3.0 ≈ 1.7
        self.assertIn("1.7x avg", section_block)

    def test_link_history_zero_avg_ctr_renders_n_a(self):
        # All links have 0 CTR -> div-by-zero guard renders "N/A" instead.
        user, _ = make_user()
        pub = make_publication()
        p = make_post(user, pub, post_id="p1", publish_date=_hours_ago(72))
        make_processed_post(p)
        make_section(p, section_name="Main", section_html="<p>x</p>")
        make_link_data(p, raw_url="https://z.example.com",
                       section_name="Main", mean_ctr=0.0, description="zero")
        out = _build_niche_analysis_prompt(user, pub)
        self.assertIn("N/A", out.split("<best_links_by_section>")[1])

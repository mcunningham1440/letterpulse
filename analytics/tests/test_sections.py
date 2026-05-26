"""
Unit tests for analytics.utils.sections.

Covers:
- build_sections_desc: 15%-frequency threshold, nearby-post proximity ranking
  by abs(publish_date - target), per-section example formatting.
- auto_section: line-number clamping (max(1, start) / min(end, total)),
  section_html slice correctness, prettified-line-count consistency.

`auto_section` is async and calls `llm_call`; we patch that boundary and
return a canned `AllSections` Pydantic object so the test exercises the
slicing/clamping code without spending a real model call.
"""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest
from django.test import TestCase
from django.utils import timezone

from analytics.tests.factories import (
    make_post,
    make_publication,
    make_section,
    make_user,
)
from analytics.utils.sections import (
    AllSections,
    SectionItem,
    auto_section,
    build_sections_desc,
)


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "sample_post_pretty.html"


def _hours_ago(h):
    return timezone.now() - timedelta(hours=h)


class BuildSectionsDescTests(TestCase):

    def test_returns_empty_when_target_has_no_publish_date(self):
        user, _ = make_user()
        pub = make_publication()
        target = make_post(user, pub, post_id="t", publish_date=None)
        # Even with sections present, no target date -> "".
        other = make_post(user, pub, post_id="o", publish_date=_hours_ago(72))
        make_section(other, section_name="Main")
        self.assertEqual(build_sections_desc(user, pub, target), "")

    def test_returns_empty_when_no_sections_exist(self):
        user, _ = make_user()
        pub = make_publication()
        target = make_post(user, pub, post_id="t", publish_date=_hours_ago(48))
        self.assertEqual(build_sections_desc(user, pub, target), "")

    def test_excludes_target_post_sections_from_examples(self):
        user, _ = make_user()
        pub = make_publication()
        target = make_post(user, pub, post_id="t", publish_date=_hours_ago(48))
        make_section(target, section_name="Main")
        # Only the target has any sections -> output should be empty.
        self.assertEqual(build_sections_desc(user, pub, target), "")

    def test_includes_section_above_15pct_frequency_threshold(self):
        # 10 nearby posts; "Main" appears in 3 of them -> >= ceil(10*0.15)=2.
        user, _ = make_user()
        pub = make_publication()
        target = make_post(user, pub, post_id="t", publish_date=_hours_ago(0))
        for i in range(10):
            p = make_post(user, pub, post_id=f"p{i}",
                          publish_date=_hours_ago((i + 1) * 24))
            if i < 3:
                make_section(p, section_name="Main", start_line=1, end_line=5,
                             post_html_length=100,
                             section_html="<p>hello</p>")
        out = build_sections_desc(user, pub, target)
        self.assertIn("<section name=\"Main\">", out)
        # Each rendered example carries the lines=...-... preface.
        self.assertIn("<lines=1-5", out)

    def test_excludes_section_below_15pct_threshold(self):
        # We need n_nearby >= 7 to get min_appearances >= 2 (ceil(0.15*7)=2).
        # Give every nearby post a "Common" section so all 10 post_ids feed
        # the nearby set; "Rare" appears in just 1 of them and must be
        # filtered out.
        user, _ = make_user()
        pub = make_publication()
        target = make_post(user, pub, post_id="t", publish_date=_hours_ago(0))
        for i in range(10):
            p = make_post(user, pub, post_id=f"p{i}",
                          publish_date=_hours_ago((i + 1) * 24))
            make_section(p, section_name="Common", start_line=1, end_line=5,
                         post_html_length=100, section_html="<p>c</p>")
            if i == 5:
                make_section(p, section_name="Rare", start_line=10, end_line=15,
                             post_html_length=100, section_html="<p>r</p>")
        out = build_sections_desc(user, pub, target)
        self.assertIn("Common", out)
        self.assertNotIn("Rare", out)

    def test_long_section_text_collapsed_with_ellipsis(self):
        # MAX_CHARS is 500 inside build_sections_desc. Provide an example
        # whose plain-text length exceeds it; the rendered example should
        # contain " [...] " in the middle.
        user, _ = make_user()
        pub = make_publication()
        target = make_post(user, pub, post_id="t", publish_date=_hours_ago(0))
        long_html = "<p>" + ("x" * 1000) + "</p>"
        for i in range(3):
            p = make_post(user, pub, post_id=f"p{i}",
                          publish_date=_hours_ago((i + 1) * 24))
            make_section(p, section_name="Main", start_line=1, end_line=5,
                         post_html_length=100, section_html=long_html)
        out = build_sections_desc(user, pub, target)
        self.assertIn(" [...] ", out)

    def test_nearby_post_proximity_ordering(self):
        # Three "Main" sections at +24h, +480h, +720h relative to target.
        # The closest (24h) example should appear first in the rendered list.
        user, _ = make_user()
        pub = make_publication()
        target = make_post(user, pub, post_id="t",
                           publish_date=timezone.now())
        ages = [24, 480, 720]
        closer = make_post(user, pub, post_id="closer",
                           publish_date=_hours_ago(24))
        mid = make_post(user, pub, post_id="mid", publish_date=_hours_ago(480))
        far = make_post(user, pub, post_id="far", publish_date=_hours_ago(720))
        make_section(closer, section_name="Main", start_line=10, end_line=20,
                     section_html="<p>closer text</p>", post_html_length=100)
        make_section(mid, section_name="Main", start_line=30, end_line=40,
                     section_html="<p>mid text</p>", post_html_length=100)
        make_section(far, section_name="Main", start_line=50, end_line=60,
                     section_html="<p>far text</p>", post_html_length=100)
        # Need 15% threshold; 3 of 3 nearby posts have Main -> all included.
        out = build_sections_desc(user, pub, target)
        # The first example block should be the "closer" one (10-20).
        self.assertLess(out.index("10-20"), out.index("30-40"))
        self.assertLess(out.index("30-40"), out.index("50-60"))


class AutoSectionTests(TestCase):
    """auto_section is async and calls llm_call; patch that boundary."""

    def _build_canned_response(self, sections):
        """Wrap parsed SectionItem list as the NormalizedResponse contract."""
        parsed = AllSections(sections=sections)
        # llm_call returns an object with .output_parsed attribute.
        return SimpleNamespace(output_parsed=parsed, output_text="", usage=None)

    @pytest.mark.asyncio
    async def test_line_clamping_on_realistic_post(self):
        from asgiref.sync import sync_to_async
        user, _ = await sync_to_async(make_user)()
        pub = await sync_to_async(make_publication)()
        post = await sync_to_async(make_post)(user, pub, post_id="p_real")

        pretty = FIXTURE.read_text()
        # Mirror auto_section's exact line-count math (split, not count+1)
        # so the test catches a real off-by-one rather than papering over it.
        html_lines = pretty.split("\n")
        total_lines = len(html_lines)

        # Model returns one section overshooting both edges; clamp to [1, N].
        canned = self._build_canned_response([
            SectionItem(name="Whole", title=None, start_line=-50,
                        end_line=total_lines + 9999),
            SectionItem(name="Inner", title="Headline",
                        start_line=100, end_line=200),
        ])

        with mock.patch(
            "analytics.utils.sections.llm_call",
            new=mock.AsyncMock(return_value=canned),
        ):
            result = await auto_section(
                pretty, user=user, publication=pub, post=post,
                pretty_html=pretty,
            )

        self.assertEqual(len(result), 2)
        whole = result[0]
        self.assertEqual(whole["start_line"], 1)
        self.assertEqual(whole["end_line"], total_lines)
        self.assertEqual(whole["post_html_length"], total_lines)
        # section_html is the joined slice — bytes should round-trip exactly.
        self.assertEqual(whole["section_html"], "\n".join(html_lines))

        inner = result[1]
        self.assertEqual(inner["start_line"], 100)
        self.assertEqual(inner["end_line"], 200)
        # The slice is 101 inclusive lines (100..200) -> 100 newlines.
        self.assertEqual(inner["section_html"].count("\n"), 100)
        # And it matches the corresponding raw slice of the fixture.
        self.assertEqual(inner["section_html"], "\n".join(html_lines[99:200]))

    @pytest.mark.asyncio
    async def test_internal_prettify_runs_when_not_provided(self):
        # When pretty_html=None, auto_section should prettify the raw html
        # itself. This means slicing uses the prettified line count, not
        # the raw input.
        from asgiref.sync import sync_to_async
        user, _ = await sync_to_async(make_user)()
        pub = await sync_to_async(make_publication)()
        post = await sync_to_async(make_post)(user, pub, post_id="p")
        raw = "<html><body><p>One</p><p>Two</p></body></html>"

        canned = self._build_canned_response([
            SectionItem(name="Body", title=None, start_line=1, end_line=99),
        ])
        with mock.patch(
            "analytics.utils.sections.llm_call",
            new=mock.AsyncMock(return_value=canned),
        ):
            result = await auto_section(
                raw, user=user, publication=pub, post=post,
            )

        # Internal prettify should yield more than the raw single-line html
        # had — post_html_length should equal the prettified line count.
        from bs4 import BeautifulSoup
        pretty_lines = BeautifulSoup(raw, "html.parser").prettify().split("\n")
        self.assertEqual(result[0]["post_html_length"], len(pretty_lines))
        # end_line=99 gets clamped to len(pretty_lines).
        self.assertEqual(result[0]["end_line"], len(pretty_lines))

"""
Unit tests for analytics.utils.links.

Covers:
- match_links_with_clicks: Levenshtein normalization, _bhlid stripping,
  duplicate averaging, multi-match at min distance, empty-input guards.
- allocate_links_to_sections: equal share + surplus redistribution.
- select_top_bottom: half-top + half-bottom selection math.
- format_link_history: ordering, truncation note, div-by-zero on avg_ctr.
- Integration with the real Zuckerberg click fixture (sanity check).
"""
from __future__ import annotations

import json
from pathlib import Path

from django.test import TestCase

from analytics.tests.factories import (
    make_link_data,
    make_post,
    make_publication,
    make_user,
)
from analytics.utils.links import (
    allocate_links_to_sections,
    format_link_history,
    match_links_with_clicks,
    select_top_bottom,
)


FIXTURES = Path(__file__).resolve().parent / "fixtures"


class MatchLinksWithClicksTests(TestCase):

    def test_exact_match_attributes_full_click_count(self):
        html_links = ["https://example.com/a"]
        clicks = {"https://example.com/a": 10}
        result, dupes = match_links_with_clicks(html_links, clicks)
        self.assertEqual(result, {"https://example.com/a": 10})
        self.assertEqual(dupes, set())

    def test_empty_inputs_return_empty(self):
        self.assertEqual(match_links_with_clicks([], {"u": 1}), ({}, set()))
        self.assertEqual(match_links_with_clicks(["u"], {}), ({}, set()))

    def test_bhlid_param_stripped_for_matching(self):
        # Beehiiv appends &_bhlid=<token> to outbound links. Click report uses
        # the bare URL, so the matcher must strip _bhlid before comparing.
        html = ["https://target.com/post?utm=1&_bhlid=ABC123"]
        clicks = {"https://target.com/post?utm=1": 5}
        result, _ = match_links_with_clicks(html, clicks)
        # Stripped form is an exact match -> distance 0 -> full attribution.
        self.assertEqual(result[html[0]], 5)

    def test_duplicate_raw_urls_average_clicks(self):
        # Realistic Beehiiv pattern: the same target URL is wrapped with two
        # different `_bhlid` tracking tokens, one per occurrence. After
        # stripping, both processed forms are identical, so the click report's
        # single entry can't tell which occurrence got the click — the
        # function averages so each occurrence reports half the total.
        raw_a = "https://example.com/x&_bhlid=AAA"
        raw_b = "https://example.com/x&_bhlid=BBB"
        clicks = {"https://example.com/x": 10}
        result, dupes = match_links_with_clicks([raw_a, raw_b], clicks)
        self.assertAlmostEqual(result[raw_a], 5)
        self.assertAlmostEqual(result[raw_b], 5)
        self.assertEqual(dupes, {raw_a, raw_b})

    def test_tied_minimum_distance_splits_to_all(self):
        # Two HTML URLs equidistant from the click URL: both get the clicks
        # (i.e. clicks are added to each, not divided).
        html = ["https://a.example.com", "https://b.example.com"]
        clicks = {"https://x.example.com": 8}
        result, _ = match_links_with_clicks(html, clicks)
        # Each tied URL is given the full click count.
        self.assertEqual(result[html[0]], 8)
        self.assertEqual(result[html[1]], 8)

    def test_picks_closest_when_one_is_clearly_closer(self):
        html = [
            "https://very-different-domain.org/page",
            "https://example.com/post-about-things",
        ]
        clicks = {"https://example.com/post-about-thing": 4}  # 1 char off
        result, _ = match_links_with_clicks(html, clicks)
        # Closer one gets the clicks; the unrelated one gets 0 (absent).
        self.assertEqual(result.get(html[1]), 4)
        self.assertNotIn(html[0], result)

    def test_realistic_zuckerberg_clicks(self):
        # Smoke test against the real Beehiiv fixture: build the html_links
        # list from the post's actual links and the clicks_dict from the
        # stats response, then assert the matcher doesn't blow up and
        # produces a non-empty mapping.
        from bs4 import BeautifulSoup
        clicks_blob = json.loads((FIXTURES / "beehiiv_post_clicks_zuckerberg.json").read_text())
        html_blob = json.loads((FIXTURES / "beehiiv_post_html_zuckerberg.json").read_text())

        html = html_blob["body"]["data"]["content"]["free"]["email"]
        soup = BeautifulSoup(html, "html.parser")
        html_links = [a["href"] for a in soup.find_all("a", href=True)]

        # Build clicks_dict the way beehiiv_api.fetch_post_clicks does.
        clicks_data = (
            clicks_blob["body"]["data"]["stats"]["clicks"]
        )
        clicks_dict = {}
        for entry in clicks_data:
            url = entry["url"]
            unique = entry["email"]["unique_clicks"]
            if unique > 0 and url != "https://www.beehiiv.com/":
                clicks_dict[url] = max(unique, clicks_dict.get(url, 0))

        result, _ = match_links_with_clicks(html_links, clicks_dict)
        # Every click URL should land somewhere — total attributed should
        # be at least the sum of clicks (it may exceed it because ties
        # attribute to every tied html link).
        self.assertGreater(sum(result.values()), 0)
        self.assertTrue(len(result) > 0)


class AllocateLinksToSectionsTests(TestCase):

    def test_empty_input_returns_empty(self):
        self.assertEqual(allocate_links_to_sections({}, 10), {})

    def test_zero_budget_zeros_every_section(self):
        result = allocate_links_to_sections({"A": 5, "B": 3}, 0)
        self.assertEqual(result, {"A": 0, "B": 0})

    def test_budget_exceeds_supply_returns_supply(self):
        # If we have 8 total available but budget=20, just hand out all 8.
        result = allocate_links_to_sections({"A": 5, "B": 3}, 20)
        self.assertEqual(result, {"A": 5, "B": 3})

    def test_equal_split_when_sections_have_enough(self):
        result = allocate_links_to_sections({"A": 10, "B": 10, "C": 10}, 6)
        self.assertEqual(result, {"A": 2, "B": 2, "C": 2})

    def test_surplus_redistributed_when_section_capped(self):
        # 9-budget across two sections (5 + 100). First pass equal_share=4,
        # so A gets 4 (still has 1 left), B gets 4. Remaining budget=1 -> A
        # gets it -> A=5 (capped), B=4. Then A drops out, second pass gives
        # B nothing because budget=0.
        result = allocate_links_to_sections({"A": 5, "B": 100}, 9)
        self.assertEqual(result["A"] + result["B"], 9)
        # A should be capped at its supply; B should absorb the surplus.
        self.assertLessEqual(result["A"], 5)

    def test_small_budget_remainder_distributed_to_largest(self):
        # Budget=2 split across 3 sections — equal_share would be 0. The
        # branch hands the remainder out one-by-one to the largest sections.
        result = allocate_links_to_sections({"A": 1, "B": 5, "C": 3}, 2)
        # Sum honored; B (largest) should be among the recipients.
        self.assertEqual(sum(result.values()), 2)
        self.assertEqual(result["B"], 1)

    def test_sections_with_zero_links_get_zero(self):
        result = allocate_links_to_sections({"A": 0, "B": 10}, 5)
        # 'A' has no supply, gets 0; budget goes to B.
        self.assertEqual(result["A"], 0)
        self.assertEqual(result["B"], 5)


class SelectTopBottomTests(TestCase):

    def test_n_exceeds_length_returns_all(self):
        items = [{"i": 1}, {"i": 2}]
        self.assertEqual(select_top_bottom(items, 5), items)

    def test_n_zero_returns_empty(self):
        self.assertEqual(select_top_bottom([{"i": 1}], 0), [])

    def test_n_one_returns_top_only(self):
        items = [{"i": i} for i in range(5)]
        self.assertEqual(select_top_bottom(items, 1), [items[0]])

    def test_even_split_half_top_half_bottom(self):
        items = [{"i": i} for i in range(10)]
        result = select_top_bottom(items, 4)
        # ceil(4/2)=2 top + floor(4/2)=2 bottom -> [0,1,8,9]
        self.assertEqual([r["i"] for r in result], [0, 1, 8, 9])

    def test_odd_split_top_gets_extra(self):
        items = [{"i": i} for i in range(10)]
        result = select_top_bottom(items, 5)
        # ceil(5/2)=3 top + floor(5/2)=2 bottom -> [0,1,2,8,9]
        self.assertEqual([r["i"] for r in result], [0, 1, 2, 8, 9])


class FormatLinkHistoryTests(TestCase):

    def setUp(self):
        self.user, _ = make_user()
        self.pub = make_publication()
        self.post = make_post(self.user, self.pub, post_id="p_one")

    def test_no_links_returns_empty(self):
        # The function queries by section.section_name; pass any section row
        # with a name nothing else uses.
        section = make_link_data(
            self.post, raw_url="https://example.com/seed",
            section_name="Empty Section",
        )
        # Now delete the seed so the section has zero links.
        section.delete()
        # Build a fake section-like object that points at the empty section.
        from types import SimpleNamespace
        fake = SimpleNamespace(
            user=self.user, publication=self.pub, section_name="Empty Section",
        )
        formatted, count = format_link_history(fake)
        self.assertEqual(formatted, "")
        self.assertEqual(count, 0)

    def test_orders_by_ctr_descending_with_relative_label(self):
        make_link_data(
            self.post, raw_url="https://a.example.com",
            section_name="Main", mean_ctr=1.0, description="Low CTR",
        )
        make_link_data(
            self.post, raw_url="https://b.example.com",
            section_name="Main", mean_ctr=5.0, description="High CTR",
        )
        from types import SimpleNamespace
        fake = SimpleNamespace(
            user=self.user, publication=self.pub, section_name="Main",
        )
        formatted, count = format_link_history(fake)
        self.assertEqual(count, 2)
        # High-CTR link appears first; rendered with x avg multiplier.
        self.assertLess(formatted.index("High CTR"), formatted.index("Low CTR"))
        self.assertIn("avg", formatted)

    def test_truncation_note_when_over_max_links(self):
        # 5 links, max_links=4 -> half=2, show 2 top + 2 bottom, omit 1.
        for i, ctr in enumerate([10.0, 8.0, 6.0, 4.0, 2.0]):
            make_link_data(
                self.post, raw_url=f"https://e{i}.example.com",
                section_name="Main", mean_ctr=ctr,
                description=f"link-{i}", rank_in_section=i + 1,
            )
        from types import SimpleNamespace
        fake = SimpleNamespace(
            user=self.user, publication=self.pub, section_name="Main",
        )
        formatted, count = format_link_history(fake, max_links=4)
        self.assertEqual(count, 5)
        self.assertIn("1 middle links omitted", formatted)
        # Middle link omitted -> "link-2" (CTR 6.0) should not appear.
        self.assertNotIn("link-2", formatted)

    def test_zero_average_ctr_renders_n_a(self):
        # If every link has 0 CTR, avg_ctr is 0 -> the rel column shows "N/A"
        # instead of dividing by zero.
        make_link_data(
            self.post, raw_url="https://z.example.com",
            section_name="Main", mean_ctr=0.0, description="zero",
        )
        from types import SimpleNamespace
        fake = SimpleNamespace(
            user=self.user, publication=self.pub, section_name="Main",
        )
        formatted, count = format_link_history(fake)
        self.assertEqual(count, 1)
        self.assertIn("N/A", formatted)

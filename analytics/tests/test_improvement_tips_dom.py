"""
Unit tests for the DOM-mutating helpers in analytics.utils.improvement_tips:
- _insert_tip_anchor: pick the element at-or-before a sourceline, then place
  the anchor at the start of its text flow (block) or just before it (inline).
- _render_new_text_with_diff: HTML-escape new_text and wrap word-level
  insertions/replacements vs old_text in <mark class="diff-new">.

Both are tested against the real prettified Zuckerberg post HTML
(`fixtures/sample_post_pretty.html`) — sourceline math against synthetic
fragments doesn't catch the realistic case where prettified Beehiiv markup
splits a single rendered paragraph across many lines.
"""
from __future__ import annotations

from pathlib import Path

from bs4 import BeautifulSoup
from django.test import SimpleTestCase

from analytics.utils.improvement_tips import (
    _insert_tip_anchor,
    _render_new_text_with_diff,
)


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "sample_post_pretty.html"


def _load_pretty_soup():
    pretty = FIXTURE.read_text()
    return BeautifulSoup(pretty, "html.parser"), pretty


class InsertTipAnchorTests(SimpleTestCase):

    def test_anchor_inserted_into_pretty_html_at_realistic_line(self):
        soup, pretty = _load_pretty_soup()
        total_lines = pretty.count("\n") + 1
        # Pick a line in the middle so we exercise the "find element at-or-
        # before this sourceline" branch on a real, deeply-nested document.
        target_line = total_lines // 2

        anchor = soup.new_tag(
            "span",
            attrs={
                "id": "tip-target-0",
                "data-tip-anchor": "true",
                "data-tip-type": "content",
            },
        )
        _insert_tip_anchor(soup, target_line, anchor)

        # The anchor must now exist in the document, with its id intact and
        # placed somewhere inside the body (not orphaned at root).
        found = soup.find(id="tip-target-0")
        self.assertIsNotNone(found, "anchor was not inserted")
        self.assertEqual(found.get("data-tip-type"), "content")
        # Walking up the tree should reach a body or top-level container.
        parents = {p.name for p in found.parents if p.name}
        self.assertTrue(
            parents & {"body", "html", "div", "td", "table", "p"},
            f"anchor parent chain looked wrong: {parents}",
        )

    def test_block_target_places_anchor_at_start_of_text_flow(self):
        # Build a single <p> whose first descendant text is "Hello world".
        # Place an anchor before that first text node, regardless of which
        # exact sourceline we pass (we pass one large enough that the <p>
        # itself is the best match).
        html = "<html><body><p>Hello world</p></body></html>"
        soup = BeautifulSoup(html, "html.parser")
        for el in soup.find_all(True):
            # Force sourceline to a small number so all elements qualify.
            el.sourceline = 1
        anchor = soup.new_tag("span", attrs={"id": "x"})
        _insert_tip_anchor(soup, line=5, anchor_tag=anchor)
        p = soup.find("p")
        # First child of <p> should be the anchor (block insert prepends).
        self.assertEqual(p.contents[0].get("id"), "x")
        self.assertIn("Hello world", p.get_text())

    def test_inline_target_inserts_anchor_before_it(self):
        html = '<html><body><p>Before <a href="x">link</a> after</p></body></html>'
        soup = BeautifulSoup(html, "html.parser")
        for el in soup.find_all(True):
            el.sourceline = 1
        # Force the <a> to win the "best" search by giving it a higher
        # sourceline than its <p> parent.
        soup.find("a").sourceline = 2
        anchor = soup.new_tag("span", attrs={"id": "y"})
        _insert_tip_anchor(soup, line=2, anchor_tag=anchor)
        # Anchor sits as a previous sibling of the <a>, not inside <p>'s
        # first text flow.
        a_tag = soup.find("a")
        self.assertEqual(a_tag.find_previous_sibling("span").get("id"), "y")

    def test_falls_back_to_body_when_no_element_qualifies(self):
        # All elements have sourceline > requested line -> falls back to body.
        html = "<html><body><p>text</p></body></html>"
        soup = BeautifulSoup(html, "html.parser")
        for el in soup.find_all(True):
            el.sourceline = 100  # all later than the request
        anchor = soup.new_tag("span", attrs={"id": "z"})
        _insert_tip_anchor(soup, line=1, anchor_tag=anchor)
        # Anchor was appended to body.
        body = soup.find("body")
        self.assertIs(body.contents[-1], anchor)

    def test_handles_sourceline_none(self):
        # Elements with sourceline=None must be skipped, not crash the search.
        html = "<html><body><p>text</p><div>more</div></body></html>"
        soup = BeautifulSoup(html, "html.parser")
        for el in soup.find_all(True):
            el.sourceline = None
        # No element qualifies (all None) -> fall back to body append.
        anchor = soup.new_tag("span", attrs={"id": "n"})
        _insert_tip_anchor(soup, line=10, anchor_tag=anchor)
        self.assertIsNotNone(soup.find(id="n"))

    def test_multiple_anchors_keep_their_ids(self):
        # Drive a small batch of anchors against the real prettified fixture
        # and assert each lands as a distinct node.
        soup, pretty = _load_pretty_soup()
        total = pretty.count("\n") + 1
        target_lines = [total // 4, total // 2, (3 * total) // 4]
        for i, line in enumerate(target_lines):
            anchor = soup.new_tag(
                "span",
                attrs={
                    "id": f"tip-target-{i}",
                    "data-tip-anchor": "true",
                    "data-tip-type": "content",
                },
            )
            _insert_tip_anchor(soup, line, anchor)
        # All three anchors must be present, with their data attribute intact.
        ids = [
            tag.get("id") for tag in soup.find_all(attrs={"data-tip-anchor": "true"})
        ]
        self.assertEqual(
            sorted(ids),
            ["tip-target-0", "tip-target-1", "tip-target-2"],
        )


class RenderNewTextWithDiffTests(SimpleTestCase):

    def test_empty_old_text_escapes_new_text_with_no_mark(self):
        # When there's no old text to diff against, the whole new_text is
        # rendered HTML-escaped, with no <mark> wrappers.
        out = _render_new_text_with_diff("", "Hello <world> & friends")
        # All HTML-escaped, no mark.
        self.assertNotIn("<mark", out)
        self.assertIn("Hello &lt;world&gt; &amp; friends", out)

    def test_equal_segments_have_no_mark(self):
        out = _render_new_text_with_diff("Hello world", "Hello world")
        self.assertNotIn("<mark", out)
        self.assertEqual(out, "Hello world")

    def test_inserted_word_wrapped_in_mark(self):
        # "Hello world" -> "Hello new world" inserts "new ".
        out = _render_new_text_with_diff("Hello world", "Hello new world")
        self.assertIn("<mark class=\"diff-new\">", out)
        # "new" appears inside a mark tag; raw text still readable.
        self.assertIn("new", out)
        # The equal segments are NOT inside any mark tag.
        self.assertTrue(out.startswith("Hello"))

    def test_replaced_word_wrapped_in_mark(self):
        # "Hello world" -> "Hello earth"; "world" -> "earth" is a replace.
        out = _render_new_text_with_diff("Hello world", "Hello earth")
        self.assertIn("<mark class=\"diff-new\">earth</mark>", out)
        self.assertNotIn("<mark class=\"diff-new\">Hello</mark>", out)

    def test_dangerous_chars_escaped_inside_mark(self):
        # HTML-special characters in inserted text must be escaped so the
        # output is safe to drop into the rendered annotated HTML.
        out = _render_new_text_with_diff("plain", "plain <script>")
        self.assertIn("&lt;script&gt;", out)
        self.assertNotIn("<script>", out)

    def test_whitespace_tokens_preserved(self):
        # Splitter is r'(\s+)', so spaces become their own tokens and must
        # round-trip through opcodes without being mangled.
        out = _render_new_text_with_diff("a b c", "a b c d")
        # The inserted " d" should appear inside a mark.
        self.assertIn("<mark", out)
        # Resulting text (mark-stripped) reads as expected.
        stripped = out.replace('<mark class="diff-new">', '').replace('</mark>', '')
        self.assertEqual(stripped, "a b c d")

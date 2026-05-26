"""
Unit tests for analytics.utils.text: html_to_text_with_links and truncate_url.

These functions feed prompt input for several LLM-driven utilities (niche
analysis, sections), so the URL-truncation off-by-one and the anchor-text
handling matter to downstream prompt quality.
"""
from analytics.utils.text import html_to_text_with_links, truncate_url


class TruncateUrlTests:
    def test_exact_length_url_is_not_truncated(self):
        url = "https://example.com/abc"  # 23 chars
        assert truncate_url(url, max_len=23) == url

    def test_short_url_is_not_truncated(self):
        url = "https://example.com"
        assert truncate_url(url, max_len=50) == url

    def test_one_over_is_truncated_with_ellipsis(self):
        # Length 24 over max 23 -> first 20 chars + "..." = 23 chars.
        url = "https://example.com/abcd"
        result = truncate_url(url, max_len=23)
        assert result == "https://example.com/..."
        assert len(result) == 23

    def test_truncation_produces_max_len_exactly(self):
        url = "https://example.com/" + "x" * 200
        result = truncate_url(url, max_len=30)
        assert len(result) == 30
        assert result.endswith("...")

    def test_empty_url(self):
        assert truncate_url("", max_len=10) == ""


class HtmlToTextWithLinksTests:
    def test_strips_tags_and_collapses_whitespace(self):
        html = "<p>Hello <strong>world</strong>.</p>"
        # get_text(separator='\n', strip=True) splits text nodes by inline tags
        # onto separate lines. We assert presence rather than exact layout.
        result = html_to_text_with_links(html)
        assert "Hello" in result
        assert "world" in result
        assert "<strong>" not in result

    def test_link_text_followed_by_truncated_url(self):
        html = (
            '<p>Visit <a href="https://example.com/a-long-path/with/lots/of/'
            'segments/to-trigger-truncation">our blog</a> today.</p>'
        )
        result = html_to_text_with_links(html, max_url_len=50)
        # Linkified text becomes "our blog (https://...)"; URL should be cut.
        assert "our blog" in result
        assert "(" in result and ")" in result
        # The full URL is >50 chars so the inlined URL must end in "..."
        assert "..." in result

    def test_link_with_empty_text_renders_as_url_only(self):
        html = '<p>See <a href="https://example.com/">https://example.com/</a></p>'
        # When the <a> has visible text, html_to_text uses "text (url)".
        result = html_to_text_with_links(html, max_url_len=50)
        assert "https://example.com/" in result

    def test_anchor_with_no_text_replaced_by_href(self):
        # An anchor whose inner text is empty (e.g. wraps an <img>) gets
        # replaced by the URL itself.
        html = '<p>Logo: <a href="https://example.com/logo.png"><img src="x"/></a></p>'
        result = html_to_text_with_links(html, max_url_len=50)
        assert "https://example.com/logo.png" in result

    def test_anchor_with_empty_href_is_handled(self):
        # An empty href is short enough that no truncation should occur.
        html = '<p>Click <a href="">here</a></p>'
        result = html_to_text_with_links(html, max_url_len=50)
        assert "here" in result
        # No exception, URL portion is empty string.
        assert "()" in result

    def test_nested_inline_tags_collapse(self):
        html = "<p>This is <em>very <strong>important</strong></em>.</p>"
        result = html_to_text_with_links(html)
        # Words preserved regardless of nesting, no tag markup leaks.
        assert "important" in result
        assert "very" in result
        assert "<em>" not in result and "<strong>" not in result

    def test_html_entities_decoded(self):
        # BeautifulSoup decodes &amp; -> &; the function returns plain text.
        html = "<p>Tom &amp; Jerry &lt;3</p>"
        result = html_to_text_with_links(html)
        assert "Tom & Jerry <3" in result

    def test_default_max_url_len_is_50(self):
        # 80-char URL gets truncated to 50 when called without max_url_len.
        long_url = "https://example.com/" + "a" * 70
        html = f'<a href="{long_url}">link</a>'
        result = html_to_text_with_links(html)
        # Find the truncated url part inside parentheses
        assert "link (" in result
        # Expected inlined href is long_url[:47] + "..."
        expected_href = long_url[:47] + "..."
        assert expected_href in result

    def test_empty_input_returns_empty_string(self):
        assert html_to_text_with_links("") == ""

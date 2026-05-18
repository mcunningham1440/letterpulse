"""
Phase 2 tests for analytics/utils/beehiiv_api.py.

Every HTTP call is mocked via aioresponses. Payload shapes match what Beehiiv
actually returned in May 2026 — see analytics/tests/fixtures/_record_phase2.py
to re-record against the live API if it ever drifts.
"""

import asyncio
from datetime import datetime, timezone as dt_timezone

import aiohttp
import pytest
from aioresponses import aioresponses

from analytics.utils.beehiiv_api import (
    INCREMENTAL_FETCH_PAGE_SIZE,
    INCREMENTAL_FETCH_PUBLISH_AGE_SECONDS,
    fetch_all_posts,
    fetch_post_clicks,
    fetch_post_html,
    fetch_publication_stats,
    fetch_subscriber_count,
    incremental_fetch_posts,
    validate_beehiiv_api_key,
)


PUB = "pub_test"
TOKEN = "test-token"


def _publications_url():
    return "https://api.beehiiv.com/v2/publications"


def _pub_detail_url(pub_id=PUB):
    return f"https://api.beehiiv.com/v2/publications/{pub_id}?expand=stats"


def _post_html_url(post_id, pub_id=PUB):
    return f"https://api.beehiiv.com/v2/publications/{pub_id}/posts/{post_id}?expand=free_email_content"


def _post_stats_url(post_id, pub_id=PUB):
    return f"https://api.beehiiv.com/v2/publications/{pub_id}/posts/{post_id}?expand=stats"


def _all_posts_url(page, pub_id=PUB, limit=10):
    return (
        f"https://api.beehiiv.com/v2/publications/{pub_id}/posts"
        f"?expand=stats&status=all&limit={limit}&page={page}"
    )


def _incremental_url(page, order_by, pub_id=PUB):
    return (
        f"https://api.beehiiv.com/v2/publications/{pub_id}/posts"
        f"?expand=stats&status=all&order_by={order_by}&direction=desc"
        f"&limit={INCREMENTAL_FETCH_PAGE_SIZE}&page={page}"
    )


def _make_post(pid, *, publish_date=None, **extras):
    base = {"id": pid, "title": f"title-{pid}", "platform": "email",
            "audience": "free", "status": "confirmed"}
    if publish_date is not None:
        base["publish_date"] = publish_date
    base.update(extras)
    return base


def _ts_now():
    return int(datetime.now(tz=dt_timezone.utc).timestamp())


def _ts_ago(seconds):
    return _ts_now() - seconds


def _auth_header_for(mock, url, method="GET"):
    """
    Return the Authorization header sent for the most recent call matching
    (method, path, query-params-as-set). aioresponses canonicalises URL
    query order in its keys, so we compare on path + sorted query items
    rather than the raw URL string.
    """
    from yarl import URL
    target = URL(url)
    target_path = target.path
    target_query = sorted(target.query.items())
    matches = []
    for (m_method, m_url), calls in mock.requests.items():
        if m_method != method:
            continue
        if m_url.path != target_path:
            continue
        if sorted(m_url.query.items()) != target_query:
            continue
        matches.extend(calls)
    assert matches, f"No recorded request for {method} {url}"
    return matches[-1].kwargs.get("headers", {}).get("Authorization")


@pytest.fixture
async def session_and_sem():
    """Shared aiohttp session + semaphore for per-post fetch helpers."""
    async with aiohttp.ClientSession() as session:
        yield session, asyncio.Semaphore(1)


# -- validate_beehiiv_api_key ----------------------------------------------

class ValidateBeehiivApiKeyTests:

    async def test_success_returns_minimal_pub_dicts(self):
        with aioresponses() as m:
            m.get(_publications_url(), payload={"data": [
                {"id": "pub_a", "name": "A", "organization_name": "Org A",
                 "referral_program_enabled": True, "created": 12345},
                {"id": "pub_b", "name": "B", "organization_name": ""},
            ]})
            ok, pubs = await validate_beehiiv_api_key(TOKEN)
            assert _auth_header_for(m, _publications_url()) == TOKEN
        assert ok is True
        # The extractor strips every key but id/name/organization_name.
        assert pubs == [
            {"id": "pub_a", "name": "A", "organization_name": "Org A"},
            {"id": "pub_b", "name": "B", "organization_name": ""},
        ]

    async def test_success_handles_missing_optional_fields(self):
        with aioresponses() as m:
            m.get(_publications_url(), payload={"data": [{"id": "pub_x"}]})
            ok, pubs = await validate_beehiiv_api_key(TOKEN)
        assert ok is True
        assert pubs == [{"id": "pub_x", "name": "Unnamed Publication", "organization_name": ""}]

    async def test_success_with_empty_data_returns_empty_list(self):
        with aioresponses() as m:
            m.get(_publications_url(), payload={"data": []})
            ok, pubs = await validate_beehiiv_api_key(TOKEN)
        assert ok is True
        assert pubs == []

    async def test_401_returns_first_error_message(self):
        with aioresponses() as m:
            m.get(_publications_url(), status=401, payload={
                "status": 401, "statusText": "unauthorized",
                "errors": [{"message": "The api key is not valid", "code": "INVALID_API_KEY"}],
            })
            ok, msg = await validate_beehiiv_api_key(TOKEN)
        assert ok is False
        assert msg == "The api key is not valid"

    async def test_401_with_empty_errors_uses_fallback_message(self):
        with aioresponses() as m:
            m.get(_publications_url(), status=401, payload={"errors": []})
            ok, msg = await validate_beehiiv_api_key(TOKEN)
        assert ok is False
        assert msg == "Invalid API key"

    async def test_other_status_includes_code_in_message(self):
        with aioresponses() as m:
            m.get(_publications_url(), status=503, payload={})
            ok, msg = await validate_beehiiv_api_key(TOKEN)
        assert ok is False
        assert "503" in msg

    async def test_network_error_returns_friendly_message(self):
        with aioresponses() as m:
            m.get(_publications_url(), exception=aiohttp.ClientConnectionError("boom"))
            ok, msg = await validate_beehiiv_api_key(TOKEN)
        assert ok is False
        assert msg.startswith("Network error")
        assert "boom" in msg

    async def test_unexpected_exception_returns_unexpected_error(self):
        # Covers the bare-Exception fallback (anything not aiohttp.ClientError).
        with aioresponses() as m:
            m.get(_publications_url(), exception=ValueError("weird"))
            ok, msg = await validate_beehiiv_api_key(TOKEN)
        assert ok is False
        assert msg.startswith("Unexpected error")
        assert "weird" in msg


# -- fetch_subscriber_count -----------------------------------------------

class FetchSubscriberCountTests:

    async def test_extracts_active_subscriptions(self):
        with aioresponses() as m:
            m.get(_pub_detail_url(), payload={
                "data": {"id": PUB, "stats": {"active_subscriptions": 4406}}
            })
            n = await fetch_subscriber_count(TOKEN, PUB)
            assert _auth_header_for(m, _pub_detail_url()) == TOKEN
        assert n == 4406

    async def test_missing_stats_returns_zero(self):
        with aioresponses() as m:
            m.get(_pub_detail_url(), payload={"data": {"id": PUB}})
            n = await fetch_subscriber_count(TOKEN, PUB)
        assert n == 0

    async def test_falsey_field_returns_zero(self):
        # Beehiiv returns `false` for stats not enabled on this publication.
        with aioresponses() as m:
            m.get(_pub_detail_url(), payload={
                "data": {"id": PUB, "stats": {"active_subscriptions": False}}
            })
            n = await fetch_subscriber_count(TOKEN, PUB)
        assert n == 0

    async def test_non_200_returns_zero(self):
        with aioresponses() as m:
            m.get(_pub_detail_url(), status=500, payload={})
            n = await fetch_subscriber_count(TOKEN, PUB)
        assert n == 0

    async def test_exception_returns_zero(self):
        with aioresponses() as m:
            m.get(_pub_detail_url(), exception=aiohttp.ClientError("boom"))
            n = await fetch_subscriber_count(TOKEN, PUB)
        assert n == 0


# -- fetch_publication_stats ----------------------------------------------

class FetchPublicationStatsTests:

    async def test_extracts_all_three_fields_as_percentage_points(self):
        # Critical: rates are in percentage points (e.g. 51.16 == 51.16%), NOT
        # 0-1 fractions. The Monetize view formats them directly.
        with aioresponses() as m:
            m.get(_pub_detail_url(), payload={
                "data": {"id": PUB, "stats": {
                    "active_subscriptions": 4406,
                    "average_open_rate": 51.17,
                    "average_click_rate": 5.14,
                    "total_sent": 478732,  # extra fields ignored
                }}
            })
            out = await fetch_publication_stats(TOKEN, PUB)
            assert _auth_header_for(m, _pub_detail_url()) == TOKEN
        assert out == {
            "active_subscriptions": 4406,
            "average_open_rate": 51.17,
            "average_click_rate": 5.14,
        }

    async def test_false_values_become_none(self):
        # When Beehiiv hasn't enabled a metric for this pub it returns `false`.
        with aioresponses() as m:
            m.get(_pub_detail_url(), payload={
                "data": {"id": PUB, "stats": {
                    "active_subscriptions": False,
                    "average_open_rate": False,
                    "average_click_rate": False,
                }}
            })
            out = await fetch_publication_stats(TOKEN, PUB)
        assert out == {
            "active_subscriptions": None,
            "average_open_rate": None,
            "average_click_rate": None,
        }

    async def test_bool_true_is_treated_as_none_not_one(self):
        # Defensive: bool is a subclass of int in Python; the prod code's
        # `not isinstance(v, bool)` guard prevents True from coercing to 1.
        with aioresponses() as m:
            m.get(_pub_detail_url(), payload={
                "data": {"id": PUB, "stats": {"active_subscriptions": True}}
            })
            out = await fetch_publication_stats(TOKEN, PUB)
        assert out["active_subscriptions"] is None

    async def test_missing_stats_object_returns_all_none(self):
        with aioresponses() as m:
            m.get(_pub_detail_url(), payload={"data": {"id": PUB}})
            out = await fetch_publication_stats(TOKEN, PUB)
        assert out == {
            "active_subscriptions": None,
            "average_open_rate": None,
            "average_click_rate": None,
        }

    async def test_non_200_returns_all_none(self):
        with aioresponses() as m:
            m.get(_pub_detail_url(), status=500, payload={})
            out = await fetch_publication_stats(TOKEN, PUB)
        assert out == {
            "active_subscriptions": None,
            "average_open_rate": None,
            "average_click_rate": None,
        }

    async def test_exception_returns_all_none(self):
        with aioresponses() as m:
            m.get(_pub_detail_url(), exception=aiohttp.ClientError("boom"))
            out = await fetch_publication_stats(TOKEN, PUB)
        assert out["active_subscriptions"] is None


# -- fetch_post_html ------------------------------------------------------

class FetchPostHtmlTests:

    async def test_success_returns_email_content_string(self, session_and_sem):
        session, sem = session_and_sem
        with aioresponses() as m:
            m.get(_post_html_url("post_1"), payload={
                "data": {"id": "post_1", "content": {"free": {
                    "email": "<html>hello</html>"}}}
            })
            pid, html = await fetch_post_html(session, "post_1", sem, TOKEN, PUB)
            assert _auth_header_for(m, _post_html_url("post_1")) == TOKEN
        assert pid == "post_1"
        assert html == "<html>hello</html>"

    async def test_missing_content_chain_returns_empty_string(self, session_and_sem):
        # The chained .get('data',{}).get('content',{}).get('free',{}).get('email','')
        # ladder defaults to empty string when any segment is missing.
        session, sem = session_and_sem
        with aioresponses() as m:
            m.get(_post_html_url("post_1"), payload={"data": {"id": "post_1"}})
            pid, html = await fetch_post_html(session, "post_1", sem, TOKEN, PUB)
        assert pid == "post_1"
        assert html == ""

    async def test_404_returns_none(self, session_and_sem):
        session, sem = session_and_sem
        with aioresponses() as m:
            m.get(_post_html_url("post_x"), status=404, payload={})
            pid, html = await fetch_post_html(session, "post_x", sem, TOKEN, PUB)
        assert pid == "post_x"
        assert html is None

    async def test_missing_token_short_circuits_to_none(self, session_and_sem):
        session, sem = session_and_sem
        with aioresponses():  # no mock — should not be called
            pid, html = await fetch_post_html(session, "post_1", sem, "", PUB)
        assert pid == "post_1"
        assert html is None


# -- fetch_post_clicks ----------------------------------------------------

class FetchPostClicksTests:

    async def test_extracts_url_to_unique_clicks_dict(self, session_and_sem):
        session, sem = session_and_sem
        with aioresponses() as m:
            m.get(_post_stats_url("post_1"), payload={
                "data": {"id": "post_1", "stats": {"clicks": [
                    {"url": "https://x.example/a", "email": {"unique_clicks": 5}},
                    {"url": "https://x.example/b", "email": {"unique_clicks": 12}},
                ]}}
            })
            pid, clicks = await fetch_post_clicks(session, "post_1", sem, TOKEN, PUB)
            assert _auth_header_for(m, _post_stats_url("post_1")) == TOKEN
        assert pid == "post_1"
        assert clicks == {"https://x.example/a": 5, "https://x.example/b": 12}

    async def test_dedupes_same_url_taking_max_clicks(self, session_and_sem):
        session, sem = session_and_sem
        with aioresponses() as m:
            m.get(_post_stats_url("post_1"), payload={
                "data": {"stats": {"clicks": [
                    {"url": "https://x/dup", "email": {"unique_clicks": 3}},
                    {"url": "https://x/dup", "email": {"unique_clicks": 8}},
                    {"url": "https://x/dup", "email": {"unique_clicks": 1}},
                ]}}
            })
            _, clicks = await fetch_post_clicks(session, "post_1", sem, TOKEN, PUB)
        assert clicks == {"https://x/dup": 8}

    async def test_filters_zero_click_urls(self, session_and_sem):
        session, sem = session_and_sem
        with aioresponses() as m:
            m.get(_post_stats_url("post_1"), payload={
                "data": {"stats": {"clicks": [
                    {"url": "https://x/zero", "email": {"unique_clicks": 0}},
                    {"url": "https://x/one", "email": {"unique_clicks": 1}},
                ]}}
            })
            _, clicks = await fetch_post_clicks(session, "post_1", sem, TOKEN, PUB)
        assert clicks == {"https://x/one": 1}

    async def test_filters_beehiiv_homepage_url(self, session_and_sem):
        session, sem = session_and_sem
        with aioresponses() as m:
            m.get(_post_stats_url("post_1"), payload={
                "data": {"stats": {"clicks": [
                    {"url": "https://www.beehiiv.com/", "email": {"unique_clicks": 50}},
                    {"url": "https://x.example/keep", "email": {"unique_clicks": 1}},
                ]}}
            })
            _, clicks = await fetch_post_clicks(session, "post_1", sem, TOKEN, PUB)
        assert clicks == {"https://x.example/keep": 1}

    async def test_404_returns_none(self, session_and_sem):
        session, sem = session_and_sem
        with aioresponses() as m:
            m.get(_post_stats_url("post_x"), status=404, payload={})
            _, clicks = await fetch_post_clicks(session, "post_x", sem, TOKEN, PUB)
        assert clicks is None

    async def test_missing_token_short_circuits_to_none(self, session_and_sem):
        # Parity with fetch_post_html: an empty token must short-circuit
        # without ever issuing the request.
        session, sem = session_and_sem
        with aioresponses():  # no mock — should not be called
            pid, clicks = await fetch_post_clicks(session, "post_1", sem, "", PUB)
        assert pid == "post_1"
        assert clicks is None


# -- fetch_all_posts ------------------------------------------------------

class FetchAllPostsTests:
    """
    fetch_all_posts fetches 5 pages in parallel per batch and returns when any
    page in the batch has < 10 posts.
    """

    async def test_single_short_first_page_returns_immediately(self):
        with aioresponses() as m:
            m.get(_all_posts_url(1), payload={"data": [_make_post(f"p{i}") for i in range(3)]})
            # Pages 2-5 are also requested in parallel; they can be empty.
            for p in (2, 3, 4, 5):
                m.get(_all_posts_url(p), payload={"data": []})
            posts = await fetch_all_posts(TOKEN, PUB)
            assert _auth_header_for(m, _all_posts_url(1)) == TOKEN
        # Stops on page 1's short result; pages 2-5 also fetched but contribute nothing.
        assert [p["id"] for p in posts] == ["p0", "p1", "p2"]

    async def test_full_first_batch_then_short_second_batch(self):
        with aioresponses() as m:
            for p in (1, 2, 3, 4, 5):
                m.get(_all_posts_url(p), payload={"data": [_make_post(f"p{p}_{i}") for i in range(10)]})
            # Second batch: page 6 has 4 posts (< 10) -> stops scanning.
            m.get(_all_posts_url(6), payload={"data": [_make_post(f"p6_{i}") for i in range(4)]})
            for p in (7, 8, 9, 10):
                m.get(_all_posts_url(p), payload={"data": []})
            posts = await fetch_all_posts(TOKEN, PUB)
        assert len(posts) == 50 + 4

    async def test_all_empty_batch_breaks_loop(self):
        with aioresponses() as m:
            for p in (1, 2, 3, 4, 5):
                m.get(_all_posts_url(p), payload={"data": []})
            posts = await fetch_all_posts(TOKEN, PUB)
        assert posts == []


# -- incremental_fetch_posts ----------------------------------------------

class IncrementalFetchPostsTests:
    """
    The two-track logic: Track A (publish_date desc + 72h age check),
    Track B (created desc + stop-on-duplicate-only).
    """

    async def test_track_b_stops_immediately_on_known_id(self):
        # Track B: any known post in batch -> stop (no age check).
        with aioresponses() as m:
            # Track A: returns one batch with no known posts then empty.
            m.get(_incremental_url(1, "publish_date"), payload={
                "data": [_make_post("new_a", publish_date=_ts_ago(3600))]
            })
            m.get(_incremental_url(2, "publish_date"), payload={"data": []})
            # Track B: returns a batch with a known id -> stops.
            m.get(_incremental_url(1, "created"), payload={
                "data": [_make_post("new_b"), _make_post("known_x")]
            })
            posts = await incremental_fetch_posts(TOKEN, PUB, existing_post_ids={"known_x"})
            assert _auth_header_for(m, _incremental_url(1, "publish_date")) == TOKEN
            assert _auth_header_for(m, _incremental_url(1, "created")) == TOKEN
        ids = sorted(p["id"] for p in posts)
        assert ids == ["known_x", "new_a", "new_b"]

    async def test_track_a_continues_when_known_post_is_too_recent(self):
        # Track A sees a known post < 72h old -> doesn't stop on first batch.
        # Then page 2 returns nothing -> empty-page exit.
        recent_ts = _ts_ago(60 * 60)  # 1 hour ago
        with aioresponses() as m:
            m.get(_incremental_url(1, "publish_date"), payload={
                "data": [_make_post("known_x", publish_date=recent_ts),
                         _make_post("new_a", publish_date=recent_ts)]
            })
            m.get(_incremental_url(2, "publish_date"), payload={"data": []})
            # Track B: known id on page 1 -> stop immediately.
            m.get(_incremental_url(1, "created"), payload={
                "data": [_make_post("known_x")]
            })
            posts = await incremental_fetch_posts(TOKEN, PUB, existing_post_ids={"known_x"})
        # Even with continuation, only the existing posts come back here.
        ids = sorted(p["id"] for p in posts)
        assert ids == ["known_x", "new_a"]

    async def test_track_a_stops_when_known_post_is_old_enough(self):
        # Track A sees a known post >= 72h old -> stops on this batch.
        old_ts = _ts_ago(INCREMENTAL_FETCH_PUBLISH_AGE_SECONDS + 3600)
        with aioresponses() as m:
            m.get(_incremental_url(1, "publish_date"), payload={
                "data": [_make_post("new_a", publish_date=old_ts),
                         _make_post("known_x", publish_date=old_ts)]
            })
            m.get(_incremental_url(1, "created"), payload={
                "data": [_make_post("known_x")]
            })
            posts = await incremental_fetch_posts(TOKEN, PUB, existing_post_ids={"known_x"})
        ids = sorted(p["id"] for p in posts)
        assert ids == ["known_x", "new_a"]

    async def test_track_a_no_publish_date_falls_back_to_old_enough(self):
        # All drafts in batch -> no publish_date -> treat as old, stop.
        with aioresponses() as m:
            m.get(_incremental_url(1, "publish_date"), payload={
                "data": [_make_post("draft_a"), _make_post("known_x")]
                # neither has publish_date
            })
            m.get(_incremental_url(1, "created"), payload={
                "data": [_make_post("known_x")]
            })
            posts = await incremental_fetch_posts(TOKEN, PUB, existing_post_ids={"known_x"})
        ids = sorted(p["id"] for p in posts)
        assert ids == ["draft_a", "known_x"]

    async def test_deduplicates_across_tracks_track_b_wins(self):
        # Same post id appears in both tracks. The merge does
        # `by_id[pid] = post` over `track_a + track_b`, so Track B's copy
        # ends up in the final result. This pins that merge semantic — if
        # someone later swaps the order, this test catches it.
        with aioresponses() as m:
            m.get(_incremental_url(1, "publish_date"), payload={
                "data": [_make_post("shared", publish_date=_ts_ago(3600),
                                    title="from-track-a")]
            })
            m.get(_incremental_url(2, "publish_date"), payload={"data": []})
            m.get(_incremental_url(1, "created"), payload={
                "data": [_make_post("shared", title="from-track-b")]
            })
            posts = await incremental_fetch_posts(TOKEN, PUB, existing_post_ids=set())
        assert len(posts) == 1
        assert posts[0]["id"] == "shared"
        assert posts[0]["title"] == "from-track-b"

    async def test_short_page_stops_track(self):
        # Page returns < PAGE_SIZE posts -> stop.
        with aioresponses() as m:
            assert INCREMENTAL_FETCH_PAGE_SIZE == 5  # documenting the assumption
            m.get(_incremental_url(1, "publish_date"), payload={
                "data": [_make_post(f"a{i}", publish_date=_ts_ago(3600)) for i in range(3)]
            })
            m.get(_incremental_url(1, "created"), payload={
                "data": [_make_post(f"b{i}") for i in range(2)]
            })
            posts = await incremental_fetch_posts(TOKEN, PUB, existing_post_ids=set())
        ids = sorted(p["id"] for p in posts)
        assert ids == ["a0", "a1", "a2", "b0", "b1"]

    async def test_non_200_page_aborts_track_without_raising(self):
        # _fetch_incremental_track converts non-200 mid-pagination into a
        # None page result, which breaks the loop and cancels pending fetches.
        # The wrapper still returns whatever the other track produced.
        with aioresponses() as m:
            # Track A page 1 errors -> track A yields nothing.
            m.get(_incremental_url(1, "publish_date"), status=500, payload={})
            # Track B returns one new post then empty -> contributes normally.
            m.get(_incremental_url(1, "created"), payload={
                "data": [_make_post("only_b")]
            })
            m.get(_incremental_url(2, "created"), payload={"data": []})
            posts = await incremental_fetch_posts(TOKEN, PUB, existing_post_ids=set())
        assert [p["id"] for p in posts] == ["only_b"]

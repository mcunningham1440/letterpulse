"""
Plain-Python factories for the test suite.

Each helper builds a single row with reasonable defaults and lets the caller
override whatever the test cares about. No factory_boy — defaults are inline
and obvious so tests stay readable.

Usage:
    from analytics.tests.factories import (
        make_user, make_publication, make_user_publication,
        make_post, make_processed_post, make_section, make_link_data,
    )
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone as dt_timezone
from typing import Optional

from django.contrib.auth import get_user_model
from django.utils import timezone

from analytics.models import (
    LinkData,
    PendingContentSearch,
    PendingImprovementTips,
    PendingLearningTask,
    PendingNicheAnalysis,
    Post,
    ProcessedPost,
    Publication,
    Section,
    UserPublication,
)


User = get_user_model()


_EMAIL_COUNTER = {'n': 0}

# Sentinel for kwargs where "None means leave unset"; lets callers pass
# publish_date=None to clear the field without being overridden by a default.
_UNSET = object()


def _unique_email(prefix: str = "user") -> str:
    _EMAIL_COUNTER['n'] += 1
    return f"{prefix}{_EMAIL_COUNTER['n']}@example.com"


def make_user(
    *,
    email: Optional[str] = None,
    date_joined: Optional[datetime] = None,
    monthly_quota: int = 75,
    used_this_period: int = 0,
    period_start: Optional[date] = None,
):
    """
    Create a User + the auto-created UsageAccount, with optional billing
    parameter overrides. Returns (user, usage_account).

    The post_save signal auto-creates a UsageAccount on user creation; we
    fetch and adjust it rather than creating a duplicate.
    """
    if email is None:
        email = _unique_email()
    username = email.split("@")[0]
    user = User.objects.create_user(username=username, email=email, password="x")

    if date_joined is not None:
        user.date_joined = date_joined
        user.save(update_fields=["date_joined"])

    usage = user.usage_account
    usage.monthly_quota = monthly_quota
    usage.used_this_period = used_this_period
    if period_start is not None:
        usage.period_start = period_start
    usage.save()
    return user, usage


def make_publication(
    *,
    pub_id: Optional[str] = None,
    name: str = "Test Publication",
    organization_name: str = "",
) -> Publication:
    if pub_id is None:
        pub_id = f"pub_{uuid.uuid4()}"
    return Publication.objects.create(
        pub_id=pub_id, name=name, organization_name=organization_name,
    )


def make_user_publication(
    user, publication, *, initial_fetch_done_at=None,
) -> UserPublication:
    return UserPublication.objects.create(
        user=user,
        publication=publication,
        initial_fetch_done_at=initial_fetch_done_at,
    )


def make_post(
    user, publication=None, *,
    post_id: Optional[str] = None,
    title: str = "Test Post",
    status: str = "Published",
    platform: Optional[str] = "email",
    creation_date: Optional[datetime] = None,
    publish_date=_UNSET,
    recipients: int = 1000,
    unique_email_opens: int = 500,
) -> Post:
    if post_id is None:
        post_id = f"post_{uuid.uuid4()}"
    # publish_date=_UNSET -> use a sensible default; passing None explicitly
    # leaves the field NULL (some flows test "post not yet published").
    if publish_date is _UNSET:
        publish_date = timezone.now()
    return Post.objects.create(
        post_id=post_id,
        publication=publication,
        user=user,
        title=title,
        status=status,
        platform=platform,
        creation_date=creation_date,
        publish_date=publish_date,
        recipients=recipients,
        unique_email_opens=unique_email_opens,
    )


def make_processed_post(post, user=None, publication=None) -> ProcessedPost:
    return ProcessedPost.objects.create(
        post=post,
        user=user or post.user,
        publication=publication if publication is not None else post.publication,
    )


def make_section(
    post, *,
    user=None,
    publication=None,
    section_name: str = "Main Essay",
    section_title: Optional[str] = None,
    start_line: int = 1,
    end_line: int = 10,
    post_html_length: int = 100,
    section_html: str = "<p>section body</p>",
) -> Section:
    return Section.objects.create(
        post=post,
        user=user or post.user,
        publication=publication if publication is not None else post.publication,
        section_name=section_name,
        section_title=section_title,
        start_line=start_line,
        end_line=end_line,
        post_html_length=post_html_length,
        section_html=section_html,
    )


def make_link_data(
    post, *,
    user=None,
    publication=None,
    raw_url: str = "https://example.com/article",
    description: str = "An example article",
    section_name: str = "Main Essay",
    rank_in_section: int = 1,
    mean_ctr: float = 3.5,
    mean_clicks: float = 35.0,
) -> LinkData:
    return LinkData.objects.create(
        post=post,
        user=user or post.user,
        publication=publication if publication is not None else post.publication,
        raw_url=raw_url,
        description=description,
        section_name=section_name,
        rank_in_section=rank_in_section,
        mean_ctr=mean_ctr,
        mean_clicks=mean_clicks,
    )


def make_pending_learning(
    user, publication, *,
    kind: str = "initial",
    status: str = "pending",
    phase: str = "fetch",
    target_process_count: int = 0,
    posts_processed_count: int = 0,
) -> PendingLearningTask:
    return PendingLearningTask.objects.create(
        user=user,
        publication=publication,
        kind=kind,
        status=status,
        phase=phase,
        target_process_count=target_process_count,
        posts_processed_count=posts_processed_count,
    )


def make_pending_content_search(
    user, publication, post, *,
    status: str = "pending",
) -> PendingContentSearch:
    return PendingContentSearch.objects.create(
        user=user, publication=publication, post=post, status=status,
    )


def make_pending_improvement_tips(
    user, publication, post, *,
    status: str = "pending",
) -> PendingImprovementTips:
    return PendingImprovementTips.objects.create(
        user=user, publication=publication, post=post, status=status,
    )


def make_pending_niche(
    user, publication, *,
    status: str = "pending",
    niche: str = "",
    content_types: Optional[list] = None,
) -> PendingNicheAnalysis:
    return PendingNicheAnalysis.objects.create(
        user=user, publication=publication, status=status,
        niche=niche, content_types=content_types or [],
    )

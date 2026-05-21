import logging
import uuid
from calendar import monthrange
from datetime import timedelta

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone

from .fields import EncryptedCharField

logger = logging.getLogger(__name__)


def get_default_monthly_credits():
    """Get default monthly credits from settings"""
    return getattr(settings, 'DEFAULT_MONTHLY_CREDITS', 100)


class UsageAccount(models.Model):
    """Track AI usage credits and API credentials for each user"""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='usage_account'
    )
    monthly_quota = models.PositiveIntegerField(
        default=get_default_monthly_credits,
        help_text="Credits available per month"
    )
    used_this_period = models.PositiveIntegerField(
        default=0,
        help_text="Credits used in current billing period"
    )
    period_start = models.DateField(help_text="Start of current billing period")

    # Beehiiv API credentials (encrypted at rest)
    beehiiv_token = EncryptedCharField(
        max_length=500,  # Extra space for encryption overhead
        blank=True,
        default='',
        help_text="Beehiiv API token (encrypted)"
    )
    beehiiv_pub_id = models.CharField(
        max_length=255,
        blank=True,
        default='',
        help_text="Beehiiv publication ID"
    )
    api_key_valid = models.BooleanField(
        default=False,
        help_text="Whether the API key has been validated against Beehiiv"
    )
    timezone = models.CharField(
        max_length=50,
        default='America/Chicago',
        help_text="User's preferred timezone for date display"
    )
    newsletter_name = models.CharField(
        max_length=255,
        blank=True,
        default='',
        help_text="Name of the user's newsletter"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Usage Account"
        verbose_name_plural = "Usage Accounts"

    def __str__(self):
        return f"{self.user.email} - {self.used_this_period}/{self.monthly_quota} credits"

    @property
    def remaining(self):
        """Return remaining credits for this period"""
        return max(0, self.monthly_quota - self.used_this_period)

    @property
    def usage_percentage(self):
        """Return usage as a percentage"""
        if self.monthly_quota == 0:
            return 100
        return min(100, round((self.used_this_period / self.monthly_quota) * 100, 1))

    def ensure_current_period(self):
        """
        Lazy reset when billing period rolls over.
        Billing resets on the same day of month as user signup.
        Call this before checking or charging credits.
        """
        today = timezone.now().date()

        # Get the billing day from user's signup date
        billing_day = self.user.date_joined.day

        # Calculate the billing day for the current month
        # Handle months with fewer days (e.g., signup on 31st, current month has 30 days)
        _, days_in_current_month = monthrange(today.year, today.month)
        current_month_billing_day = min(billing_day, days_in_current_month)

        # Determine the current period start date
        if today.day >= current_month_billing_day:
            # We're in a period that started this month
            new_period_start = today.replace(day=current_month_billing_day)
        else:
            # We're in a period that started last month
            if today.month == 1:
                prev_year = today.year - 1
                prev_month = 12
            else:
                prev_year = today.year
                prev_month = today.month - 1
            _, days_in_prev_month = monthrange(prev_year, prev_month)
            prev_month_billing_day = min(billing_day, days_in_prev_month)
            new_period_start = today.replace(year=prev_year, month=prev_month, day=prev_month_billing_day)

        # Reset if we've entered a new period
        if self.period_start != new_period_start:
            self.period_start = new_period_start
            self.used_this_period = 0

    @property
    def next_renewal_date(self):
        """Calculate the next credit renewal date based on user's signup day."""
        today = timezone.now().date()
        billing_day = self.user.date_joined.day

        # Try current month first
        _, days_in_current_month = monthrange(today.year, today.month)
        current_month_billing_day = min(billing_day, days_in_current_month)

        if today.day < current_month_billing_day:
            # Renewal is later this month
            return today.replace(day=current_month_billing_day)
        else:
            # Renewal is next month
            if today.month == 12:
                next_year = today.year + 1
                next_month = 1
            else:
                next_year = today.year
                next_month = today.month + 1
            _, days_in_next_month = monthrange(next_year, next_month)
            next_month_billing_day = min(billing_day, days_in_next_month)
            return today.replace(year=next_year, month=next_month, day=next_month_billing_day)

    @property
    def has_api_credentials(self):
        """Check if Beehiiv API credentials are configured"""
        return bool(self.beehiiv_token and self.beehiiv_pub_id)

    @property
    def masked_token(self):
        """Return masked version of the API token for display"""
        if not self.beehiiv_token:
            return ''
        if len(self.beehiiv_token) <= 8:
            return '****'
        return self.beehiiv_token[:4] + '****' + self.beehiiv_token[-4:]


class Publication(models.Model):
    """Model representing a Beehiiv publication"""

    pub_id = models.CharField(max_length=255, unique=True, help_text="Beehiiv publication ID")
    name = models.CharField(max_length=255)
    organization_name = models.CharField(max_length=255, blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.pub_id[:20]}...)"


class UserPublication(models.Model):
    """
    Per-user access to a Beehiiv Publication, plus per-(user, publication)
    onboarding state. Replaces the legacy UsageAccount.available_publications
    JSON cache and UsageAccount.initial_fetched_pub_ids JSON set.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='user_publications',
    )
    publication = models.ForeignKey(
        Publication,
        on_delete=models.CASCADE,
        related_name='user_publications',
    )
    initial_fetch_done_at = models.DateTimeField(
        null=True, blank=True,
        help_text="When the user's initial Learning fetch completed for this pub; null if not yet completed.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('user', 'publication')]
        indexes = [
            models.Index(fields=['user', 'publication']),
        ]
        ordering = ['publication__name']
        verbose_name = "User Publication"
        verbose_name_plural = "User Publications"

    def __str__(self):
        return f"{self.user.email} ↔ {self.publication.name}"


class Post(models.Model):
    """Model representing a Beehiiv newsletter post"""

    post_id = models.CharField(max_length=255, help_text="Beehiiv post ID")
    publication = models.ForeignKey(
        Publication,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='posts',
        help_text="The publication this post belongs to"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='posts',
        help_text="The user who owns this post data"
    )
    title = models.CharField(max_length=500)
    status = models.CharField(max_length=20, default='Published', help_text="Draft, Scheduled, or Published")
    platform = models.CharField(
        max_length=20, blank=True, null=True,
        help_text="Beehiiv platform value: email, web, or both"
    )
    creation_date = models.DateTimeField(blank=True, null=True, help_text="When the post was first created in Beehiiv")
    publish_date = models.DateTimeField(blank=True, null=True, help_text="When the post was published (stored in UTC)")
    recipients = models.IntegerField(default=0)
    unique_email_opens = models.IntegerField(default=0)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-publish_date']
        indexes = [
            models.Index(fields=['-publish_date']),
            models.Index(fields=['post_id']),
        ]
        unique_together = [['post_id', 'user']]

    def __str__(self):
        return f"{self.title} ({self.publish_date})"
    


class ExecutionLog(models.Model):
    """
    Execution logs for HTTP requests and function calls.
    Uses queue-based async logging for minimal overhead.
    """

    # Timing fields
    ts_start = models.DateTimeField(help_text="When execution started")
    ts_end = models.DateTimeField(help_text="When execution ended")
    duration_ms = models.IntegerField(help_text="Duration in milliseconds")

    # Classification
    KIND_CHOICES = [
        ('request', 'HTTP Request'),
        ('function', 'Function Call'),
    ]
    kind = models.CharField(max_length=20, choices=KIND_CHOICES, help_text="Type of log entry")
    name = models.CharField(max_length=255, help_text="View name or module.function")

    # Status
    success = models.BooleanField(default=True, help_text="Whether execution succeeded")
    error_type = models.CharField(max_length=255, blank=True, default='', help_text="Exception class name")
    error_message = models.TextField(blank=True, default='', help_text="Exception message")
    traceback = models.TextField(blank=True, default='', help_text="Full traceback if error")

    # Context
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='execution_logs',
        help_text="User who triggered the execution"
    )
    request_id = models.CharField(
        max_length=64, blank=True, default='', db_index=True,
        help_text="UUID for request correlation"
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['-created_at']),
            models.Index(fields=['kind', 'name']),
            models.Index(fields=['request_id']),
            models.Index(fields=['success']),
        ]

    def __str__(self):
        status = "OK" if self.success else "ERROR"
        return f"[{self.kind}] {self.name} - {status} ({self.duration_ms}ms)"


class LLMCall(models.Model):
    """
    Per-call record for every LLM invocation made through
    analytics.utils.llm_call. Written asynchronously via the LogSink queue.

    `provider` distinguishes the two backends ('openai' / 'anthropic'). When
    a call fell back from the other provider after a retryable failure, the
    primary's failure is in its own row (success=False) and the successful
    fallback row carries `additional_info['fell_back_from']`.
    """

    ts_start = models.DateTimeField(help_text="When the LLM call was initiated")
    ts_end = models.DateTimeField(help_text="When the LLM call returned or errored")

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='llm_calls',
    )
    publication = models.ForeignKey(
        'Publication',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='llm_calls',
    )

    function_name = models.CharField(max_length=100, help_text="Logical function the call is for (e.g. 'content_finder')")
    model = models.CharField(max_length=100, help_text="Model id (e.g. 'gpt-5.4', 'claude-sonnet-4-6')")
    provider = models.CharField(max_length=20, blank=True, default='', db_index=True,
                                help_text="LLM provider ('openai' or 'anthropic'); empty for pre-multiprovider rows")

    input_tokens_cached = models.PositiveIntegerField(default=0)
    input_tokens_new = models.PositiveIntegerField(default=0)
    output_tokens_reasoning = models.PositiveIntegerField(default=0)
    output_tokens_response = models.PositiveIntegerField(default=0)

    success = models.BooleanField(default=True)
    error_type = models.CharField(max_length=255, blank=True, default='')
    error_message = models.TextField(blank=True, default='')

    task_id = models.CharField(max_length=64, blank=True, default='', db_index=True,
                               help_text="UUID of the Pending* task that spawned this call, if any")
    task_kind = models.CharField(max_length=50, blank=True, default='',
                                 help_text="e.g. 'content_finder', 'improvement_tips', 'learning_initial'")

    additional_info = models.JSONField(default=dict, blank=True,
                                       help_text="Arbitrary call-specific metadata (e.g. section_name)")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-ts_start']
        indexes = [
            models.Index(fields=['-ts_start']),
            models.Index(fields=['function_name']),
            models.Index(fields=['task_id']),
            models.Index(fields=['success']),
            models.Index(fields=['user', '-ts_start']),
        ]

    def __str__(self):
        status = "OK" if self.success else "ERROR"
        dur_ms = int((self.ts_end - self.ts_start).total_seconds() * 1000) if self.ts_end and self.ts_start else 0
        return f"[{self.function_name}] {self.model} - {status} ({dur_ms}ms)"


class ProcessedPost(models.Model):
    """Lightweight marker indicating a post has been processed for link data."""

    post = models.ForeignKey(
        'Post',
        on_delete=models.CASCADE,
        related_name='processed_posts',
        help_text="The post this extraction belongs to"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='processed_posts',
        help_text="The user who owns this processed data"
    )
    publication = models.ForeignKey(
        Publication,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='processed_posts',
        help_text="The publication this processed post belongs to"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        unique_together = [['post', 'user']]
        verbose_name = "Processed Post"
        verbose_name_plural = "Processed Posts"

    def __str__(self):
        return f"{self.post.title} - {self.user.email}"


class LinkData(models.Model):
    """Stores described link data extracted from a processed post."""

    post = models.ForeignKey(
        'Post',
        on_delete=models.CASCADE,
        related_name='link_data',
        help_text="The post this link was extracted from"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='link_data',
        help_text="The user who owns this link data"
    )
    publication = models.ForeignKey(
        Publication,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='link_data',
        help_text="The publication this link data belongs to"
    )
    raw_url = models.URLField(max_length=2048)
    description = models.TextField(blank=True)
    section_name = models.CharField(max_length=255, blank=True, default='')
    rank_in_section = models.PositiveIntegerField(null=True)
    mean_ctr = models.FloatField(help_text="Mean CTR as percentage (e.g. 3.5 = 3.5%)")
    mean_clicks = models.FloatField()

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['post', 'section_name', 'rank_in_section']
        unique_together = [['post', 'user', 'raw_url', 'section_name']]
        indexes = [
            models.Index(fields=['user', 'publication']),
        ]
        verbose_name = "Link Data"
        verbose_name_plural = "Link Data"

    def __str__(self):
        return f"{self.post.title} - {self.section_name} rank {self.rank_in_section} - {self.raw_url[:60]}"


class Section(models.Model):
    """Stores section data extracted from a processed post via agentic GPT loop."""

    post = models.ForeignKey(
        'Post',
        on_delete=models.CASCADE,
        related_name='sections',
        help_text="The post this section was extracted from"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='sections',
        help_text="The user who owns this section data"
    )
    publication = models.ForeignKey(
        Publication,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='sections',
        help_text="The publication this section belongs to"
    )
    section_name = models.CharField(max_length=255)
    section_title = models.CharField(max_length=500, blank=True, null=True,
        help_text="Display title as it appears in the newsletter, or None if untitled")
    start_line = models.PositiveIntegerField()
    end_line = models.PositiveIntegerField()
    post_html_length = models.PositiveIntegerField(
        help_text="Total line count of the post HTML"
    )
    section_html = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['post', 'start_line']
        unique_together = [['post', 'user', 'section_name']]
        indexes = [
            models.Index(fields=['user', 'publication']),
        ]
        verbose_name = "Section"
        verbose_name_plural = "Sections"

    def __str__(self):
        return f"{self.post.title} - {self.section_name}"


class BackgroundTask(models.Model):
    """
    Shared scaffolding for the daemon-thread background tasks in this app
    (content finder, improvement tips, niche analysis, learning).

    Subclasses tune three knobs:
      * RUNNING_STATUSES — every status the task can be in while live work is
        ongoing. Used by views to detect "task already in progress".
      * SWEEPABLE_STATUSES — subset of RUNNING_STATUSES that a stale-heartbeat
        sweep is allowed to error out. Statuses where the task is intentionally
        idle (e.g. content finder's `awaiting_feedback` waiting on a user
        Confirm click) must be excluded.
      * STALE_SECONDS — heartbeat age past which the sweep treats the task as
        dead.

    Override get_credits_cost() on the subclass to charge credits at claim()
    time. The cost reads from `settings.CREDITS_PER_*` so a settings change
    affects new tasks only — in-flight tasks honor whatever they were quoted at
    creation.
    """

    task_id = models.UUIDField(default=uuid.uuid4, unique=True)
    status = models.CharField(max_length=20, default='pending')
    last_heartbeat = models.DateTimeField(default=timezone.now)
    error_message = models.TextField(blank=True, default='')
    credits_charged = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    RUNNING_STATUSES = ('pending', 'running')
    SWEEPABLE_STATUSES = ('pending', 'running')
    STALE_SECONDS = 60

    class Meta:
        abstract = True
        ordering = ['-created_at']

    def get_credits_cost(self) -> int:
        """Credits to deduct on claim(). Override on subclasses that charge."""
        return 0

    def claim(self, running_status: str = 'running') -> bool:
        """
        Atomic transition from 'pending' to `running_status`, charging credits
        if applicable. Returns True if this call claimed the task, False if it
        was already past 'pending' (re-entry — e.g. content finder resuming
        from 'awaiting_feedback' after the user's Confirm click).

        Raises NotEnoughCredits if the user is over quota at claim time.
        """
        from analytics.utils.credits import charge_credits

        cost = self.get_credits_cost()
        with transaction.atomic():
            fresh = type(self).objects.select_for_update().get(pk=self.pk)
            if fresh.status != 'pending':
                for f in ('status', 'credits_charged', 'last_heartbeat'):
                    setattr(self, f, getattr(fresh, f))
                return False

            if cost > 0 and fresh.credits_charged == 0:
                charge_credits(self.user, cost)
                fresh.credits_charged = cost

            fresh.status = running_status
            fresh.last_heartbeat = timezone.now()
            fresh.save(update_fields=[
                'status', 'credits_charged', 'last_heartbeat', 'updated_at',
            ])

        for f in ('status', 'credits_charged', 'last_heartbeat'):
            setattr(self, f, getattr(fresh, f))
        return True

    def touch_heartbeat(self) -> None:
        """Bump last_heartbeat. Called by poll endpoints — while a client is
        still polling, the sweep treats the task as live."""
        type(self).objects.filter(pk=self.pk).update(
            last_heartbeat=timezone.now()
        )

    def mark_complete(self, **extra_fields) -> None:
        """
        Set status='complete' and persist any extra terminal-state fields.
        Idempotent against an already-terminal row (e.g. the sweep beat us to
        it) — in that case we leave the existing status alone.
        """
        with transaction.atomic():
            fresh = type(self).objects.select_for_update().get(pk=self.pk)
            if fresh.status in ('complete', 'error'):
                for f in ('status', 'error_message'):
                    setattr(self, f, getattr(fresh, f))
                return
            for k, v in extra_fields.items():
                setattr(fresh, k, v)
            fresh.status = 'complete'
            fresh.last_heartbeat = timezone.now()
            fresh.save(update_fields=list(extra_fields.keys()) + [
                'status', 'last_heartbeat', 'updated_at',
            ])
        for k, v in extra_fields.items():
            setattr(self, k, v)
        self.status = 'complete'

    def mark_error(self, message: str, *, refund: bool = True) -> None:
        """
        Mark errored and refund any charged credits. Idempotent on terminal
        rows. Subclasses can override on_error() to add cleanup (e.g. wipe
        partial user data for the learning flow).
        """
        from analytics.utils.credits import refund_credits

        with transaction.atomic():
            fresh = type(self).objects.select_for_update().get(pk=self.pk)
            if fresh.status in ('complete', 'error'):
                return
            to_refund = fresh.credits_charged if refund else 0
            fresh.status = 'error'
            fresh.error_message = (message or '')[:5000]
            fresh.credits_charged = 0
            fresh.save(update_fields=[
                'status', 'error_message', 'credits_charged', 'updated_at',
            ])
            if to_refund > 0:
                refund_credits(self.user, to_refund)

        self.status = 'error'
        self.error_message = (message or '')[:5000]
        self.credits_charged = 0
        try:
            self.on_error()
        except Exception:
            logger.exception("on_error hook failed for %s", type(self).__name__)

    def on_error(self) -> None:
        """Subclass hook fired after a task is marked errored."""
        pass

    @classmethod
    def sweep_stale(cls) -> int:
        """
        Mark any task in SWEEPABLE_STATUSES whose last_heartbeat is older than
        STALE_SECONDS as errored, refunding credits. Returns the sweep count.

        Called from AppConfig.ready() at boot (recovers tasks orphaned by a
        prior process crash) and from views before they check for "task
        already running" gating.
        """
        cutoff = timezone.now() - timedelta(seconds=cls.STALE_SECONDS)
        stale = list(cls.objects.filter(
            status__in=cls.SWEEPABLE_STATUSES,
            last_heartbeat__lt=cutoff,
        ))
        for task in stale:
            try:
                task.mark_error("Task abandoned (no client activity).")
            except Exception:
                logger.exception(
                    "sweep_stale failed to mark task %s as errored",
                    task.task_id,
                )
        return len(stale)


class PendingContentSearch(BackgroundTask):
    """Tracks background content finder tasks"""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='pending_content_searches'
    )
    publication = models.ForeignKey(
        Publication,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    post = models.ForeignKey(Post, on_delete=models.CASCADE)
    plan_text = models.TextField(blank=True, default='')
    plan_messages = models.JSONField(default=list, blank=True)
    user_feedback = models.TextField(blank=True, default='')
    dispatch_messages = models.JSONField(default=list, blank=True)
    dispatch_sections = models.JSONField(default=list, blank=True)
    result_data = models.JSONField(default=list, blank=True)
    dev_panel_data = models.JSONField(default=dict, blank=True)

    RUNNING_STATUSES = (
        'pending', 'planning', 'awaiting_feedback', 'dispatching', 'searching',
    )
    # `awaiting_feedback` is intentionally idle — the task is waiting for the
    # user to click Confirm on the plan modal, which may take minutes.
    SWEEPABLE_STATUSES = ('pending', 'planning', 'dispatching', 'searching')
    STALE_SECONDS = 180

    class Meta(BackgroundTask.Meta):
        pass

    def get_credits_cost(self) -> int:
        return settings.CREDITS_PER_CONTENT_SEARCH

    def __str__(self):
        return f"PendingContentSearch {self.task_id} ({self.status})"


class PendingImprovementTips(BackgroundTask):
    """Tracks background improvement tips generation tasks"""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='pending_improvement_tips'
    )
    publication = models.ForeignKey(
        Publication,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    post = models.ForeignKey(Post, on_delete=models.CASCADE)
    result_html = models.TextField(blank=True)
    dev_panel_data = models.JSONField(default=dict, blank=True)

    STALE_SECONDS = 180

    class Meta(BackgroundTask.Meta):
        pass

    def get_credits_cost(self) -> int:
        return settings.CREDITS_PER_IMPROVEMENT_TIPS

    def __str__(self):
        return f"PendingImprovementTips {self.task_id} ({self.status})"


class PendingLearningTask(BackgroundTask):
    """
    Tracks the two onboarding/refresh workflows that replaced the old Posts page:

    - kind='initial' : first-time "Learning Your Audience" flow (full fetch + initial processing).
      Each run wipes any leftover (user, publication) data first so a previous
      abandoned attempt can't poison the new one.
    - kind='update'  : per-page-load "Updating Your Posts" flow (incremental fetch + processing
      of newly-eligible posts). No cleanup on abandon.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='pending_learning_tasks',
    )
    publication = models.ForeignKey(
        Publication,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    kind = models.CharField(
        max_length=10,
        help_text="initial or update",
    )
    phase = models.CharField(
        max_length=10,
        default='fetch',
        help_text="fetch or process",
    )
    target_process_count = models.IntegerField(
        default=0,
        help_text="Number of posts the process phase is expected to handle",
    )
    posts_processed_count = models.IntegerField(default=0)

    STALE_SECONDS = 30

    class Meta(BackgroundTask.Meta):
        indexes = [
            models.Index(fields=['user', 'status']),
        ]

    def on_error(self) -> None:
        """Wipe partial state for an abandoned initial run so the next attempt
        starts clean. Update runs leave their incremental writes in place."""
        if self.kind == 'initial' and self.publication is not None:
            try:
                from analytics.utils.post_selection import (
                    wipe_user_publication_data,
                )
                wipe_user_publication_data(self.user, self.publication.pub_id)
            except Exception:
                logger.exception(
                    "wipe_user_publication_data failed for learning task %s",
                    self.task_id,
                )

    def __str__(self):
        return f"PendingLearningTask {self.task_id} ({self.kind}/{self.status})"


class PendingNicheAnalysis(BackgroundTask):
    """
    Tracks the one-shot Monetize-tab niche analysis: an LLM call that reads the
    text of the user's last 3 processed posts plus the best-performing links per
    section over the last 10 issues, and returns a niche label + 5 highly-clicked
    content types. Result rows act as the cache — the most recent 'complete' row
    for (user, publication) is reused on subsequent visits.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='pending_niche_analyses',
    )
    publication = models.ForeignKey(
        Publication,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    niche = models.CharField(max_length=255, blank=True, default='')
    content_types = models.JSONField(default=list, blank=True)
    dev_panel_data = models.JSONField(default=dict, blank=True)

    STALE_SECONDS = 180

    class Meta(BackgroundTask.Meta):
        indexes = [
            models.Index(fields=['user', 'publication', '-created_at']),
        ]
        verbose_name = "Pending Niche Analysis"
        verbose_name_plural = "Pending Niche Analyses"

    def __str__(self):
        return f"PendingNicheAnalysis {self.task_id} ({self.status})"


class Feedback(models.Model):
    """Captures user feedback on features and product direction"""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='feedback'
    )
    feature = models.CharField(max_length=100, help_text="Feature area (e.g. 'write_post')")
    response = models.CharField(max_length=255, help_text="The option the user selected")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'feature')
        verbose_name = "Feedback"
        verbose_name_plural = "Feedback"

    def __str__(self):
        return f"{self.user} — {self.feature}: {self.response}"


class ContentSearchFeedback(models.Model):
    """Stores user thumbs-up / thumbs-down feedback on content finder results."""

    THUMBS_UP = 'up'
    THUMBS_DOWN = 'down'
    FEEDBACK_CHOICES = [
        (THUMBS_UP, 'Thumbs Up'),
        (THUMBS_DOWN, 'Thumbs Down'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='content_search_feedback',
    )
    publication = models.ForeignKey(
        Publication,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='content_search_feedback',
    )
    title = models.CharField(max_length=500)
    url = models.URLField(max_length=2000)
    source = models.CharField(max_length=255)
    pub_date = models.CharField(max_length=100, blank=True, default='')
    description = models.TextField(blank=True, default='')
    relevance = models.TextField(blank=True, default='')
    feedback = models.CharField(max_length=4, choices=FEEDBACK_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'publication', 'url')
        ordering = ['-created_at']
        verbose_name = "Content Search Feedback"
        verbose_name_plural = "Content Search Feedback"

    def __str__(self):
        return f"{self.user} — {self.feedback} — {self.title[:50]}"

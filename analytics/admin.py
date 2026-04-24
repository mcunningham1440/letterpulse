from django.contrib import admin
from .models import Post, UsageAccount, Publication, ExecutionLog, LLMCall, SurveyResponse, ProcessedPost, LinkData, Section, ClickVizEmailLog, CronRunLog, PendingContentSearch, ContentSearchFeedback, PendingLearningTask


@admin.register(UsageAccount)
class UsageAccountAdmin(admin.ModelAdmin):
    list_display = (
        'user', 'monthly_quota', 'used_this_period', 'period_start',
        'beehiiv_token', 'beehiiv_pub_id', 'api_key_valid', 'survey_completed',
        'auto_click_viz_email', 'created_at', 'updated_at'
    )
    list_filter = ('period_start', 'api_key_valid', 'survey_completed', 'auto_click_viz_email')
    search_fields = ('user__email', 'beehiiv_pub_id')
    ordering = ('-period_start',)
    readonly_fields = ('remaining_display', 'created_at', 'updated_at')

    def remaining_display(self, obj):
        return f"{obj.remaining} credits remaining"
    remaining_display.short_description = 'Credits Remaining'


@admin.register(Publication)
class PublicationAdmin(admin.ModelAdmin):
    list_display = ('name', 'pub_id', 'organization_name', 'created_at', 'updated_at')
    search_fields = ('name', 'pub_id', 'organization_name')
    ordering = ('name',)
    readonly_fields = ('created_at', 'updated_at')


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = (
        'title', 'post_id', 'user', 'publication', 'status', 'creation_date', 'publish_date',
        'recipients', 'delivered', 'email_opens', 'unique_email_opens',
        'email_clicks', 'unique_email_clicks', 'unsubscribes', 'spam_reports',
        'created_at', 'updated_at'
    )
    list_filter = ('status', 'publish_date', 'publication', 'user')
    search_fields = ('title', 'subtitle', 'post_id', 'user__email')
    ordering = ('-publish_date',)
    readonly_fields = ('created_at', 'updated_at')


@admin.register(ExecutionLog)
class ExecutionLogAdmin(admin.ModelAdmin):
    list_display = (
        'created_at', 'kind', 'name', 'success', 'duration_ms',
        'user', 'request_id', 'error_type'
    )
    list_filter = ('kind', 'success', 'created_at', 'user')
    search_fields = ('name', 'request_id', 'error_type', 'error_message', 'user__email')
    ordering = ('-created_at',)
    readonly_fields = (
        'ts_start', 'ts_end', 'duration_ms', 'kind', 'name', 'success',
        'error_type', 'error_message', 'traceback', 'user', 'request_id',
        'parent_id', 'inputs', 'outputs', 'meta', 'created_at'
    )
    date_hierarchy = 'created_at'

    def has_add_permission(self, request):
        # Logs should only be created by the system
        return False

    def has_change_permission(self, request, obj=None):
        # Logs should be immutable
        return False


@admin.register(LLMCall)
class LLMCallAdmin(admin.ModelAdmin):
    list_display = (
        'ts_start', 'function_name', 'model', 'success', 'duration_display',
        'user', 'publication', 'task_kind',
        'input_tokens_new', 'input_tokens_cached',
        'output_tokens_response', 'output_tokens_reasoning',
        'error_type',
    )
    list_filter = ('function_name', 'model', 'task_kind', 'success', 'ts_start')
    search_fields = ('function_name', 'model', 'task_id', 'error_type', 'error_message', 'user__email')
    ordering = ('-ts_start',)
    readonly_fields = (
        'ts_start', 'ts_end', 'user', 'publication', 'function_name', 'model',
        'input_tokens_cached', 'input_tokens_new',
        'output_tokens_reasoning', 'output_tokens_response',
        'success', 'error_type', 'error_message',
        'task_id', 'task_kind', 'additional_info', 'created_at',
    )
    date_hierarchy = 'ts_start'

    def duration_display(self, obj):
        if obj.ts_end and obj.ts_start:
            return f"{int((obj.ts_end - obj.ts_start).total_seconds() * 1000)}ms"
        return '—'
    duration_display.short_description = 'Duration'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(ProcessedPost)
class ProcessedPostAdmin(admin.ModelAdmin):
    list_display = ('post', 'user', 'publication', 'created_at', 'updated_at')
    list_filter = ('publication', 'user', 'created_at')
    search_fields = ('post__title', 'user__email')
    ordering = ('-created_at',)
    readonly_fields = ('created_at', 'updated_at')


@admin.register(LinkData)
class LinkDataAdmin(admin.ModelAdmin):
    list_display = ('post', 'user', 'raw_url', 'description', 'rank_in_post', 'mean_ctr', 'mean_clicks')
    list_filter = ('publication', 'user')
    search_fields = ('post__title', 'user__email', 'raw_url', 'description')
    ordering = ('post', 'rank_in_post')


@admin.register(Section)
class SectionAdmin(admin.ModelAdmin):
    list_display = ('post', 'user', 'section_name', 'start_line', 'end_line', 'created_at')
    list_filter = ('publication', 'user', 'section_name')
    search_fields = ('post__title', 'user__email', 'section_name')
    ordering = ('post', 'start_line')


@admin.register(PendingContentSearch)
class PendingContentSearchAdmin(admin.ModelAdmin):
    list_display = ('task_id', 'user', 'post', 'status', 'dispatch_section_count', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('user__email', 'post__title')
    ordering = ('-created_at',)
    readonly_fields = (
        'task_id', 'created_at', 'plan_text', 'plan_messages',
        'user_feedback', 'dispatch_messages', 'dispatch_sections',
        'result_data', 'dev_panel_data', 'error_message',
    )

    def dispatch_section_count(self, obj):
        return len(obj.dispatch_sections or [])
    dispatch_section_count.short_description = 'dispatched'


@admin.register(PendingLearningTask)
class PendingLearningTaskAdmin(admin.ModelAdmin):
    list_display = ('task_id', 'user', 'publication', 'kind', 'phase', 'status',
                    'target_process_count', 'posts_processed_count', 'abandoned',
                    'last_heartbeat', 'created_at')
    list_filter = ('kind', 'phase', 'status', 'abandoned', 'created_at')
    search_fields = ('user__email', 'publication__name')
    ordering = ('-created_at',)
    readonly_fields = ('task_id', 'created_at', 'updated_at', 'last_heartbeat')


@admin.register(SurveyResponse)
class SurveyResponseAdmin(admin.ModelAdmin):
    list_display = (
        'user', 'beehiiv_analytics_inadequate', 'missing_features_preview',
        'other_tools_preview', 'created_at'
    )
    list_filter = ('beehiiv_analytics_inadequate', 'created_at')
    search_fields = ('user__email', 'missing_features', 'other_tools')
    ordering = ('-created_at',)
    readonly_fields = ('user', 'created_at')

    def missing_features_preview(self, obj):
        if obj.missing_features:
            return obj.missing_features[:50] + '...' if len(obj.missing_features) > 50 else obj.missing_features
        return '-'
    missing_features_preview.short_description = 'Missing Features'

    def other_tools_preview(self, obj):
        if obj.other_tools:
            return obj.other_tools[:50] + '...' if len(obj.other_tools) > 50 else obj.other_tools
        return '-'
    other_tools_preview.short_description = 'Other Tools'


@admin.register(ClickVizEmailLog)
class ClickVizEmailLogAdmin(admin.ModelAdmin):
    list_display = ('user', 'post_id', 'post_title', 'success', 'sent_at')
    list_filter = ('success', 'sent_at', 'publication')
    search_fields = ('user__email', 'post_id', 'post_title')
    ordering = ('-sent_at',)
    readonly_fields = ('user', 'publication', 'post_id', 'post_title', 'sent_at', 'success', 'error_message')


@admin.register(CronRunLog)
class CronRunLogAdmin(admin.ModelAdmin):
    list_display = ('command', 'started_at', 'duration_ms', 'users_processed', 'emails_sent', 'errors', 'success', 'triggered_by')
    list_filter = ('success', 'command', 'triggered_by', 'started_at')
    ordering = ('-started_at',)
    readonly_fields = (
        'command', 'started_at', 'finished_at', 'duration_ms',
        'users_processed', 'emails_sent', 'errors', 'output',
        'success', 'triggered_by',
    )


@admin.register(ContentSearchFeedback)
class ContentSearchFeedbackAdmin(admin.ModelAdmin):
    list_display = ('user', 'publication', 'title', 'feedback', 'created_at')
    list_filter = ('feedback', 'created_at')
    search_fields = ('title', 'url', 'source')
    ordering = ('-created_at',)

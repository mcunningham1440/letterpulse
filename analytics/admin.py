from django.contrib import admin
from .models import Post, UsageAccount, Publication, UserPublication, ExecutionLog, LLMCall, ProcessedPost, LinkData, Section, PendingContentSearch, ContentSearchFeedback, PendingLearningTask, PendingNicheAnalysis, PendingImprovementTips, Feedback


@admin.register(UsageAccount)
class UsageAccountAdmin(admin.ModelAdmin):
    list_display = (
        'user', 'monthly_quota', 'used_this_period', 'period_start',
        'masked_token_display', 'beehiiv_pub_id', 'api_key_valid',
        'created_at', 'updated_at'
    )
    list_filter = ('period_start', 'api_key_valid')
    search_fields = ('user__email', 'beehiiv_pub_id')
    ordering = ('-period_start',)
    exclude = ('beehiiv_token',)
    readonly_fields = ('masked_token_display', 'remaining_display', 'created_at', 'updated_at')

    def masked_token_display(self, obj):
        return obj.masked_token or '—'
    masked_token_display.short_description = 'Beehiiv Token'

    def remaining_display(self, obj):
        return f"{obj.remaining} credits remaining"
    remaining_display.short_description = 'Credits Remaining'


@admin.register(Publication)
class PublicationAdmin(admin.ModelAdmin):
    list_display = ('name', 'pub_id', 'organization_name', 'created_at', 'updated_at')
    search_fields = ('name', 'pub_id', 'organization_name')
    ordering = ('name',)
    readonly_fields = ('created_at', 'updated_at')


@admin.register(UserPublication)
class UserPublicationAdmin(admin.ModelAdmin):
    list_display = ('user', 'publication', 'initial_fetch_done_at', 'created_at', 'updated_at')
    list_filter = ('publication', 'created_at')
    search_fields = ('user__email', 'publication__name', 'publication__pub_id')
    ordering = ('-updated_at',)
    readonly_fields = ('created_at', 'updated_at')


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = (
        'title', 'post_id', 'user', 'publication', 'status', 'creation_date', 'publish_date',
        'recipients', 'unique_email_opens',
        'created_at', 'updated_at'
    )
    list_filter = ('status', 'publish_date', 'publication', 'user')
    search_fields = ('title', 'post_id', 'user__email')
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
        'created_at'
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
    list_display = ('post', 'user', 'raw_url', 'description', 'section_name', 'rank_in_section', 'mean_ctr', 'mean_clicks')
    list_filter = ('publication', 'user')
    search_fields = ('post__title', 'user__email', 'raw_url', 'description')
    ordering = ('post', 'section_name', 'rank_in_section')


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


@admin.register(PendingImprovementTips)
class PendingImprovementTipsAdmin(admin.ModelAdmin):
    list_display = ('task_id', 'user', 'publication', 'post', 'status', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('user__email', 'post__title')
    ordering = ('-created_at',)
    readonly_fields = (
        'task_id', 'created_at', 'result_html', 'dev_panel_data', 'error_message',
    )


@admin.register(PendingLearningTask)
class PendingLearningTaskAdmin(admin.ModelAdmin):
    list_display = ('task_id', 'user', 'publication', 'kind', 'phase', 'status',
                    'target_process_count', 'posts_processed_count', 'abandoned',
                    'last_heartbeat', 'created_at')
    list_filter = ('kind', 'phase', 'status', 'abandoned', 'created_at')
    search_fields = ('user__email', 'publication__name')
    ordering = ('-created_at',)
    readonly_fields = ('task_id', 'created_at', 'updated_at', 'last_heartbeat')


@admin.register(PendingNicheAnalysis)
class PendingNicheAnalysisAdmin(admin.ModelAdmin):
    list_display = ('task_id', 'user', 'publication', 'status', 'niche',
                    'content_type_count', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('user__email', 'publication__name', 'niche')
    ordering = ('-created_at',)
    readonly_fields = (
        'task_id', 'created_at', 'niche', 'content_types',
        'dev_panel_data', 'error_message',
    )

    def content_type_count(self, obj):
        return len(obj.content_types or [])
    content_type_count.short_description = 'types'


@admin.register(ContentSearchFeedback)
class ContentSearchFeedbackAdmin(admin.ModelAdmin):
    list_display = ('user', 'publication', 'title', 'feedback', 'created_at')
    list_filter = ('feedback', 'created_at')
    search_fields = ('title', 'url', 'source')
    ordering = ('-created_at',)


@admin.register(Feedback)
class FeedbackAdmin(admin.ModelAdmin):
    list_display = ('user', 'feature', 'response', 'created_at')
    list_filter = ('feature', 'created_at')
    search_fields = ('user__email', 'feature', 'response')
    ordering = ('-created_at',)
    readonly_fields = ('created_at',)

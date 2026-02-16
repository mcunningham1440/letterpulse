from django.contrib import admin
from .models import Post, ContentSet, Report, UsageAccount, Publication, ExecutionLog, SurveyResponse, ProcessedPost, ProcessingTemplate


@admin.register(UsageAccount)
class UsageAccountAdmin(admin.ModelAdmin):
    list_display = (
        'user', 'monthly_quota', 'used_this_period', 'period_start',
        'beehiiv_token', 'beehiiv_pub_id', 'api_key_valid', 'survey_completed',
        'created_at', 'updated_at'
    )
    list_filter = ('period_start', 'api_key_valid', 'survey_completed')
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


@admin.register(ContentSet)
class ContentSetAdmin(admin.ModelAdmin):
    list_display = ('name', 'user', 'publication', 'description', 'created_at', 'updated_at')
    list_filter = ('publication', 'user', 'created_at')
    search_fields = ('name', 'description', 'user__email')
    ordering = ('-created_at',)
    readonly_fields = ('created_at', 'updated_at')


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    list_display = ('name', 'content_set', 'created_at', 'updated_at')
    list_filter = ('content_set', 'created_at')
    search_fields = ('name', 'content_set__name', 'report_text')
    ordering = ('-created_at',)
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


@admin.register(ProcessedPost)
class ProcessedPostAdmin(admin.ModelAdmin):
    list_display = ('post', 'user', 'publication', 'section_count', 'total_items', 'created_at', 'updated_at')
    list_filter = ('publication', 'user', 'created_at')
    search_fields = ('post__title', 'user__email')
    ordering = ('-created_at',)
    readonly_fields = ('created_at', 'updated_at')

    def section_count(self, obj):
        return obj.get_section_count()
    section_count.short_description = 'Sections'

    def total_items(self, obj):
        return obj.get_total_items_count()
    total_items.short_description = 'Total Items'


@admin.register(ProcessingTemplate)
class ProcessingTemplateAdmin(admin.ModelAdmin):
    list_display = ('name', 'user', 'publication', 'created_at')
    list_filter = ('publication', 'user', 'created_at')
    search_fields = ('name', 'user__email')
    ordering = ('-created_at',)
    readonly_fields = ('created_at', 'updated_at')


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

from django.contrib import admin
from .models import Post, ContentSet, Report, UsageAccount, Publication


@admin.register(UsageAccount)
class UsageAccountAdmin(admin.ModelAdmin):
    list_display = (
        'user', 'monthly_quota', 'used_this_period', 'period_start',
        'beehiiv_token', 'beehiiv_pub_id', 'api_key_valid',
        'created_at', 'updated_at'
    )
    list_filter = ('period_start', 'api_key_valid')
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
        'title', 'post_id', 'publication', 'status', 'creation_date', 'publish_date_cst',
        'recipients', 'delivered', 'email_opens', 'unique_email_opens',
        'email_clicks', 'unique_email_clicks', 'unsubscribes', 'spam_reports',
        'created_at', 'updated_at'
    )
    list_filter = ('status', 'publish_date_cst', 'publication')
    search_fields = ('title', 'subtitle', 'post_id')
    ordering = ('-publish_date_cst',)
    readonly_fields = ('created_at', 'updated_at')


@admin.register(ContentSet)
class ContentSetAdmin(admin.ModelAdmin):
    list_display = ('name', 'publication', 'description', 'created_at', 'updated_at')
    list_filter = ('publication', 'created_at')
    search_fields = ('name', 'description')
    ordering = ('-created_at',)
    readonly_fields = ('created_at', 'updated_at')


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    list_display = ('name', 'content_set', 'created_at', 'updated_at')
    list_filter = ('content_set', 'created_at')
    search_fields = ('name', 'content_set__name', 'report_text')
    ordering = ('-created_at',)
    readonly_fields = ('created_at', 'updated_at')

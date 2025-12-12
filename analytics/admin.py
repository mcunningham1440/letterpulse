from django.contrib import admin
from .models import Post, ContentSet, Report, UsageAccount


@admin.register(UsageAccount)
class UsageAccountAdmin(admin.ModelAdmin):
    list_display = ('user', 'used_this_period', 'monthly_quota', 'period_start')
    list_filter = ('period_start',)
    search_fields = ('user__email',)
    ordering = ('-period_start',)
    readonly_fields = ('remaining_display',)
    fields = ('user', 'monthly_quota', 'used_this_period', 'period_start', 'remaining_display')

    def remaining_display(self, obj):
        return f"{obj.remaining} credits remaining"
    remaining_display.short_description = 'Credits Remaining'


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ('title', 'publish_date_cst', 'recipients', 'unique_email_opens', 'unique_email_clicks')
    list_filter = ('publish_date_cst',)
    search_fields = ('title', 'subtitle')
    ordering = ('-publish_date_cst',)

@admin.register(ContentSet)
class ContentSetAdmin(admin.ModelAdmin):
    list_display = ('name', 'created_at')
    search_fields = ('name',)
    ordering = ('-created_at',)

@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    list_display = ('name', 'content_set', 'created_at')
    list_filter = ('content_set', 'created_at')
    search_fields = ('name', 'content_set__name')
    ordering = ('-created_at',)

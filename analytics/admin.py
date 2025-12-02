from django.contrib import admin
from .models import Post, ContentSet, Report

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

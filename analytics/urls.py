from django.urls import path
from . import views

app_name = 'analytics'

urlpatterns = [
    path('', views.index, name='index'),
    path('mobile/', views.mobile_notice, name='mobile_notice'),
    path('account/', views.account_view, name='account'),
    path('posts/', views.posts_view, name='posts'),
    path('posts/refresh-posts/', views.refresh_posts, name='refresh_posts'),
    path('posts/incremental-refresh/', views.incremental_refresh_posts, name='incremental_refresh_posts'),
    path('posts/process/', views.run_processing, name='run_processing'),
    path('posts/clear-processed/', views.clear_processed_posts, name='clear_processed_posts'),
    path('insights/', views.insights_view, name='insights'),
    path('insights/load-processed-data/', views.load_processed_data, name='load_processed_data'),
    path('insights/load-link-data/', views.load_link_data, name='load_link_data'),
    path('insights/content-finder/posts/', views.content_finder_posts, name='content_finder_posts'),
    path('insights/content-finder/sections/', views.content_finder_sections, name='content_finder_sections'),
    path('insights/content-finder/run/', views.run_content_finder, name='run_content_finder'),
    path('insights/content-finder/status/<uuid:task_id>/', views.poll_content_finder, name='poll_content_finder'),
    path('insights/content-finder/feedback/', views.submit_content_search_feedback, name='submit_content_search_feedback'),
    path('insights/improvement-tips/posts/', views.improvement_tips_posts, name='improvement_tips_posts'),
    path('insights/improvement-tips/run/', views.run_improvement_tips, name='run_improvement_tips'),
    path('insights/improvement-tips/status/<uuid:task_id>/', views.poll_improvement_tips, name='poll_improvement_tips'),
    path('insights/improvement-tips/download/<uuid:task_id>/', views.download_improvement_tips, name='download_improvement_tips'),
    path('feedback/submit/', views.submit_feedback, name='submit_feedback'),
    path('survey/submit/', views.submit_survey, name='submit_survey'),
    path('cron/click-viz-status/', views.cron_status, name='cron_status'),
]

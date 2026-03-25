from django.urls import path
from . import views

app_name = 'analytics'

urlpatterns = [
    path('', views.index, name='index'),
    path('mobile/', views.mobile_notice, name='mobile_notice'),
    path('account/', views.account_view, name='account'),
    path('posts/', views.posts_view, name='posts'),
    path('posts/save/', views.save_content_set, name='save_content_set'),
    path('posts/delete-items/', views.delete_items, name='delete_items'),
    path('posts/refresh-posts/', views.refresh_posts, name='refresh_posts'),
    path('posts/download-click-viz/', views.download_click_visualization, name='download_click_visualization'),
    path('posts/download-annotated/', views.download_annotated_posts, name='download_annotated_posts'),
    path('posts/download-csv/', views.download_extracted_csv, name='download_extracted_csv'),
    path('posts/process/', views.run_processing, name='run_processing'),
    path('posts/clear-processed/', views.clear_processed_posts, name='clear_processed_posts'),
    path('insights/', views.insights_view, name='insights'),
    path('insights/load-processed-data/', views.load_processed_data, name='load_processed_data'),
    path('survey/submit/', views.submit_survey, name='submit_survey'),
    path('cron/click-viz-status/', views.cron_status, name='cron_status'),
]

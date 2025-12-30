from django.urls import path
from . import views

app_name = 'analytics'

urlpatterns = [
    path('', views.index, name='index'),
    path('account/', views.account_view, name='account'),
    path('posts/', views.posts_view, name='posts'),
    path('posts/run/', views.run_extraction, name='run_extraction'),
    path('posts/save/', views.save_content_set, name='save_content_set'),
    path('posts/delete-items/', views.delete_items, name='delete_items'),
    path('posts/refresh-posts/', views.refresh_posts, name='refresh_posts'),
    path('posts/download-click-viz/', views.download_click_visualization, name='download_click_visualization'),
    path('posts/download-annotated/', views.download_annotated_posts, name='download_annotated_posts'),
    path('insights/', views.insights_view, name='insights'),
    path('insights/load-content-set/<str:set_name>/', views.load_content_set, name='load_content_set'),
    path('insights/generate-insights/', views.generate_insights, name='generate_insights'),
    path('insights/download-csv/<str:set_name>/', views.download_csv, name='download_csv'),
    path('insights/rename-set/', views.rename_set, name='rename_set'),
    path('insights/copy-set/', views.copy_set, name='copy_set'),
    path('insights/merge-sets/', views.merge_sets, name='merge_sets'),
    path('insights/delete-items/', views.delete_items_from_set, name='delete_items_from_set'),
    path('insights/delete-set/', views.delete_set, name='delete_set'),
    path('insights/save-report/', views.save_report, name='save_report'),
    path('insights/load-report/<int:report_id>/', views.load_report, name='load_report'),
    path('insights/get-all-reports/', views.get_all_reports, name='get_all_reports'),
    path('insights/delete-report/<int:report_id>/', views.delete_report, name='delete_report'),
]

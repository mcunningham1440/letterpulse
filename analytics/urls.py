from django.urls import path
from . import views

app_name = 'analytics'

urlpatterns = [
    path('', views.index, name='index'),
    path('extract/', views.extract_view, name='extract'),
    path('extract/run/', views.run_extraction, name='run_extraction'),
    path('extract/save/', views.save_content_set, name='save_content_set'),
    path('extract/delete-items/', views.delete_items, name='delete_items'),
    path('extract/refresh-posts/', views.refresh_posts, name='refresh_posts'),
    path('extract/download-click-viz/', views.download_click_visualization, name='download_click_visualization'),
    path('extract/download-annotated/', views.download_annotated_posts, name='download_annotated_posts'),
    path('analyze/', views.analyze_view, name='analyze'),
    path('analyze/load-content-set/<str:set_name>/', views.load_content_set, name='load_content_set'),
    path('analyze/generate-insights/', views.generate_insights, name='generate_insights'),
    path('analyze/download-csv/<str:set_name>/', views.download_csv, name='download_csv'),
    path('analyze/rename-set/', views.rename_set, name='rename_set'),
    path('analyze/copy-set/', views.copy_set, name='copy_set'),
    path('analyze/merge-sets/', views.merge_sets, name='merge_sets'),
    path('analyze/delete-items/', views.delete_items_from_set, name='delete_items_from_set'),
    path('analyze/delete-set/', views.delete_set, name='delete_set'),
    path('analyze/save-report/', views.save_report, name='save_report'),
    path('analyze/load-report/<int:report_id>/', views.load_report, name='load_report'),
    path('analyze/get-all-reports/', views.get_all_reports, name='get_all_reports'),
    path('analyze/delete-report/<int:report_id>/', views.delete_report, name='delete_report'),
]

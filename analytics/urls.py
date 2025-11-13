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
    path('analyze/', views.analyze_view, name='analyze'),
    path('analyze/load-content-set/<str:set_name>/', views.load_content_set, name='load_content_set'),
    path('analyze/generate-insights/', views.generate_insights, name='generate_insights'),
    path('analyze/download-csv/<str:set_name>/', views.download_csv, name='download_csv'),
]

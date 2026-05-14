from django.urls import path
from . import views

app_name = 'analytics'

urlpatterns = [
    path('', views.index, name='index'),
    path('mobile/', views.mobile_notice, name='mobile_notice'),
    path('account/', views.account_view, name='account'),
    path('account/dismiss-publication-coach/', views.dismiss_publication_coach, name='dismiss_publication_coach'),
    path('insights/', views.insights_view, name='insights'),
    # Monetize routes disabled — feature still under development.
    # The view functions (monetize_view, poll_niche_analysis) and their templates/JS remain in the codebase.
    # path('monetize/', views.monetize_view, name='monetize'),
    # path('monetize/niche-analysis/status/<uuid:task_id>/', views.poll_niche_analysis, name='poll_niche_analysis'),
    path('insights/learning/start/', views.start_learning_task, name='start_learning_task'),
    path('insights/learning/update/', views.start_update_task, name='start_update_task'),
    path('insights/learning/status/<uuid:task_id>/', views.poll_learning_task, name='poll_learning_task'),
    path('insights/learning/abandon/<uuid:task_id>/', views.abandon_learning_task, name='abandon_learning_task'),
    path('insights/load-processed-data/', views.load_processed_data, name='load_processed_data'),
    path('insights/load-link-data/', views.load_link_data, name='load_link_data'),
    path('insights/content-finder/posts/', views.content_finder_posts, name='content_finder_posts'),
    path('insights/content-finder/run/', views.run_content_finder, name='run_content_finder'),
    path('insights/content-finder/confirm-plan/<uuid:task_id>/', views.confirm_content_finder_plan, name='confirm_content_finder_plan'),
    path('insights/content-finder/status/<uuid:task_id>/', views.poll_content_finder, name='poll_content_finder'),
    path('insights/content-finder/feedback/', views.submit_content_search_feedback, name='submit_content_search_feedback'),
    path('insights/improvement-tips/posts/', views.improvement_tips_posts, name='improvement_tips_posts'),
    path('insights/improvement-tips/run/', views.run_improvement_tips, name='run_improvement_tips'),
    path('insights/improvement-tips/status/<uuid:task_id>/', views.poll_improvement_tips, name='poll_improvement_tips'),
    path('insights/improvement-tips/download/<uuid:task_id>/', views.download_improvement_tips, name='download_improvement_tips'),
    path('feedback/submit/', views.submit_feedback, name='submit_feedback'),
]

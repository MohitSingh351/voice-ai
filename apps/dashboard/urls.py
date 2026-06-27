from django.urls import path

from apps.dashboard import views

urlpatterns = [
    path("", views.campaign_list, name="campaign_list"),
    path("leads/", views.leads, name="leads"),
    path("leads/<int:pk>/delete/", views.lead_delete, name="lead_delete"),
    path("settings/", views.settings_view, name="settings"),
    path("campaigns/<int:pk>/", views.campaign_detail, name="campaign_detail"),
    path("campaigns/<int:pk>/leads/", views.campaign_leads_partial, name="campaign_leads_partial"),
    path("campaigns/<int:pk>/<str:action>/", views.campaign_action, name="campaign_action"),
    path("calls/<int:pk>/", views.call_detail, name="call_detail"),
]

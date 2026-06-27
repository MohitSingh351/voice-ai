"""Root URL configuration."""
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path(
        "accounts/login/",
        auth_views.LoginView.as_view(template_name="registration/login.html"),
        name="login",
    ),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    # API
    path("api/", include("apps.leads.api")),
    path("api/", include("apps.campaigns.api")),
    path("api/", include("apps.calls.api")),
    # Webhooks (public, secret-verified)
    path("webhooks/", include("apps.webhooks.urls")),
    # Dashboard (server-rendered UI)
    path("", include("apps.dashboard.urls")),
]

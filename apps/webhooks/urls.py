from django.urls import path

from apps.webhooks.views import VapiWebhookView

urlpatterns = [
    path("vapi/", VapiWebhookView.as_view(), name="vapi_webhook"),
]

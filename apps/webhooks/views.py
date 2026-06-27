"""Public Vapi webhook endpoint, authenticated by a shared secret header."""
from __future__ import annotations

import hmac
import logging

from django.conf import settings
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.webhooks.handlers import dispatch_event

logger = logging.getLogger(__name__)


class VapiWebhookView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]

    def _secret_ok(self, request) -> bool:
        expected = settings.VAPI_WEBHOOK_SECRET
        if not expected:
            # Misconfiguration: refuse rather than accept unauthenticated events.
            logger.error("VAPI_WEBHOOK_SECRET is not set; rejecting webhook.")
            return False
        provided = request.headers.get("X-Vapi-Secret", "")
        return hmac.compare_digest(provided, expected)

    def post(self, request):
        if not self._secret_ok(request):
            return Response({"detail": "invalid secret"}, status=status.HTTP_401_UNAUTHORIZED)

        message = (request.data or {}).get("message")
        if not isinstance(message, dict):
            return Response({"detail": "missing message"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            result = dispatch_event(message)
        except Exception:  # noqa: BLE001 - always 200 fast; log for triage
            logger.exception("Error handling Vapi webhook %s", message.get("type"))
            return Response({"status": "error-logged"}, status=status.HTTP_200_OK)

        return Response({"status": result}, status=status.HTTP_200_OK)

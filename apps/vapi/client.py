"""Thin HTTP client for the Vapi REST API.

All Vapi calls go through here - no httpx/requests scattered across views or
tasks. Methods return the parsed JSON dict from Vapi.
"""
from __future__ import annotations

import httpx
from django.conf import settings

from apps.vapi import schemas


class VapiError(Exception):
    """Raised when Vapi returns a non-2xx response."""

    def __init__(self, status_code: int, body: str):
        self.status_code = status_code
        self.body = body
        super().__init__(f"Vapi API error {status_code}: {body}")


class VapiClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 30.0,
    ):
        self.api_key = api_key or settings.VAPI_API_KEY
        self.base_url = (base_url or settings.VAPI_BASE_URL).rstrip("/")
        self.timeout = timeout

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _post(self, path: str, payload: dict) -> dict:
        url = f"{self.base_url}{path}"
        resp = httpx.post(url, json=payload, headers=self._headers(), timeout=self.timeout)
        if resp.status_code >= 400:
            raise VapiError(resp.status_code, resp.text)
        return resp.json()

    def _get(self, path: str) -> dict:
        url = f"{self.base_url}{path}"
        resp = httpx.get(url, headers=self._headers(), timeout=self.timeout)
        if resp.status_code >= 400:
            raise VapiError(resp.status_code, resp.text)
        return resp.json()

    # -- provisioning -------------------------------------------------------
    def create_byo_sip_credential(
        self, *, gateway: str, username: str = "", password: str = ""
    ) -> dict:
        return self._post(
            "/credential",
            schemas.byo_sip_credential_payload(
                gateway=gateway, username=username, password=password
            ),
        )

    def create_phone_number(self, *, credential_id: str, number_e164: str) -> dict:
        return self._post(
            "/phone-number",
            schemas.byo_phone_number_payload(credential_id=credential_id, number_e164=number_e164),
        )

    def create_assistant(self, **kwargs) -> dict:
        return self._post("/assistant", schemas.assistant_payload(**kwargs))

    # -- calls --------------------------------------------------------------
    def place_call(
        self,
        *,
        phone_number_id: str,
        assistant_id: str,
        customer_number: str,
        customer_name: str = "",
        variable_values: dict | None = None,
        voice_provider: str = "",
        voice_id: str = "",
    ) -> dict:
        return self._post(
            "/call",
            schemas.outbound_call_payload(
                phone_number_id=phone_number_id,
                assistant_id=assistant_id,
                customer_number=customer_number,
                customer_name=customer_name,
                variable_values=variable_values,
                voice_provider=voice_provider,
                voice_id=voice_id,
            ),
        )

    def get_call(self, call_id: str) -> dict:
        return self._get(f"/call/{call_id}")

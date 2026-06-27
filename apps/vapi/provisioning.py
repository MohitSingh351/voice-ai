"""Idempotent provisioning of Vapi resources for an Organization.

Creates the BYO-SIP credential, the caller-ID phone number, and the sales
assistant only when the org doesn't already have the corresponding Vapi id.
IDs are persisted back onto the Organization row.
"""
from __future__ import annotations

from django.conf import settings

from apps.organizations.models import Organization
from apps.vapi.client import VapiClient


def webhook_server_url() -> str:
    base = settings.PUBLIC_WEBHOOK_BASE_URL.rstrip("/")
    return f"{base}/webhooks/vapi/" if base else ""


def ensure_org_provisioned(
    org: Organization | None = None,
    *,
    client: VapiClient | None = None,
    force: bool = False,
) -> dict:
    """Provision missing Vapi resources. Returns a summary of actions taken."""
    org = org or Organization.get_default()
    client = client or VapiClient()
    cfg = settings.VAPI_PROVISION
    actions: dict[str, str] = {}

    # 1. BYO-SIP credential
    if force or not org.vapi_credential_id:
        if not cfg["SIP_TRUNK_GATEWAY"]:
            raise ValueError("TWILIO_SIP_TERMINATION_URI is required to create the SIP credential")
        cred = client.create_byo_sip_credential(
            gateway=cfg["SIP_TRUNK_GATEWAY"],
            username=cfg["SIP_TRUNK_USERNAME"],
            password=cfg["SIP_TRUNK_PASSWORD"],
        )
        org.vapi_credential_id = cred["id"]
        org.save(update_fields=["vapi_credential_id", "updated_at"])
        actions["credential"] = f"created {cred['id']}"
    else:
        actions["credential"] = f"exists {org.vapi_credential_id}"

    # 2. BYO phone number (caller ID)
    if force or not org.vapi_phone_number_id:
        if not cfg["CALLER_ID_E164"]:
            raise ValueError("TWILIO_CALLER_ID is required to register the phone number")
        number = client.create_phone_number(
            credential_id=org.vapi_credential_id,
            number_e164=cfg["CALLER_ID_E164"],
        )
        org.vapi_phone_number_id = number["id"]
        org.default_caller_id = cfg["CALLER_ID_E164"]
        org.save(update_fields=["vapi_phone_number_id", "default_caller_id", "updated_at"])
        actions["phone_number"] = f"created {number['id']}"
    else:
        actions["phone_number"] = f"exists {org.vapi_phone_number_id}"

    # 3. Assistant
    if force or not org.vapi_assistant_id:
        assistant = client.create_assistant(
            name=cfg["ASSISTANT_NAME"],
            first_message=cfg["ASSISTANT_FIRST_MESSAGE"],
            system_prompt=cfg["ASSISTANT_SYSTEM_PROMPT"],
            model_provider=cfg["MODEL_PROVIDER"],
            model_name=cfg["MODEL_NAME"],
            voice_provider=cfg["VOICE_PROVIDER"],
            voice_id=cfg["VOICE_ID"],
            transcriber_provider=cfg["TRANSCRIBER_PROVIDER"],
            transcriber_model=cfg["TRANSCRIBER_MODEL"],
            server_url=webhook_server_url(),
            server_secret=settings.VAPI_WEBHOOK_SECRET,
        )
        org.vapi_assistant_id = assistant["id"]
        org.save(update_fields=["vapi_assistant_id", "updated_at"])
        actions["assistant"] = f"created {assistant['id']}"
    else:
        actions["assistant"] = f"exists {org.vapi_assistant_id}"

    return actions

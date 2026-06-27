"""Builders for Vapi API request payloads.

Centralizing payload shapes keeps the BYO-SIP / assistant wiring in one place
and makes it easy to adjust as Vapi's schema evolves. These return plain dicts
that VapiClient POSTs as JSON.
"""
from __future__ import annotations


def byo_sip_credential_payload(
    *, gateway: str, username: str = "", password: str = ""
) -> dict:
    """`POST /credential` body for an outbound Twilio Elastic SIP Trunk (BYO-SIP).

    The gateway is outbound-only: `inboundEnabled` must be False so Vapi accepts
    a hostname termination URI (inbound gateways require a numeric IPv4, which a
    Twilio `*.pstn.twilio.com` host is not). Twilio also requires a leading `+`
    on outbound numbers, hence `outboundLeadingPlusEnabled`.

    Twilio authenticates Vapi by IP ACL by default (whitelist Vapi's SBC IPs on
    the trunk's Termination); username/password is only sent if you configured a
    credential list on the trunk.
    """
    payload: dict = {
        "provider": "byo-sip-trunk",
        "name": "twilio-elastic-sip-trunk",
        "gateways": [
            {"ip": gateway, "inboundEnabled": False, "outboundEnabled": True}
        ],
        "outboundLeadingPlusEnabled": True,
    }
    if username and password:
        payload["outboundAuthenticationPlan"] = {
            "authUsername": username,
            "authPassword": password,
        }
    return payload


def byo_phone_number_payload(*, credential_id: str, number_e164: str) -> dict:
    """`POST /phone-number` body for a BYO number tied to the SIP credential."""
    return {
        "provider": "byo-phone-number",
        "name": f"caller-{number_e164}",
        "number": number_e164,
        "numberE164CheckEnabled": True,
        "credentialId": credential_id,
    }


def assistant_payload(
    *,
    name: str,
    first_message: str,
    system_prompt: str,
    model_provider: str,
    model_name: str,
    voice_provider: str,
    voice_id: str,
    transcriber_provider: str,
    transcriber_model: str,
    server_url: str = "",
    server_secret: str = "",
) -> dict:
    """`POST /assistant` body. `server.url` is where Vapi sends call webhooks."""
    payload: dict = {
        "name": name,
        "firstMessage": first_message,
        # `model` must be a model object (e.g. AnthropicModel); the system prompt
        # goes in model.messages, not at the top level.
        "model": {
            "provider": model_provider,
            "model": model_name,
            "messages": [{"role": "system", "content": system_prompt}],
        },
        "voice": {"provider": voice_provider, "voiceId": voice_id},
        "transcriber": {"provider": transcriber_provider, "model": transcriber_model},
        # Ask Vapi to produce a summary + structured analysis in end-of-call-report.
        "analysisPlan": {
            "summaryPlan": {"enabled": True},
            "successEvaluationPlan": {"enabled": True},
        },
    }
    if server_url:
        payload["server"] = {"url": server_url}
        if server_secret:
            payload["server"]["secret"] = server_secret
    return payload


def outbound_call_payload(
    *,
    phone_number_id: str,
    assistant_id: str,
    customer_number: str,
    customer_name: str = "",
    variable_values: dict | None = None,
    voice_provider: str = "",
    voice_id: str = "",
) -> dict:
    """`POST /call` body for a single outbound call.

    `voice_provider`/`voice_id`, when set, override the assistant's configured
    voice for just this call (no need to maintain a separate assistant).
    """
    customer: dict = {"number": customer_number}
    if customer_name:
        customer["name"] = customer_name
    payload: dict = {
        "phoneNumberId": phone_number_id,
        "assistantId": assistant_id,
        "customer": customer,
    }
    overrides: dict = {}
    if variable_values:
        overrides["variableValues"] = variable_values
    if voice_provider and voice_id:
        overrides["voice"] = {"provider": voice_provider, "voiceId": voice_id}
    if overrides:
        payload["assistantOverrides"] = overrides
    return payload

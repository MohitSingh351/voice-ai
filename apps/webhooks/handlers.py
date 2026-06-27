"""Handlers for Vapi webhook message types.

Vapi POSTs a body like {"message": {"type": ..., "call": {...}, ...}}. We key
off `message.type`. Handlers are idempotent on the Vapi call id because Vapi
may deliver an event more than once.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from django.utils import timezone

from apps.calls.models import Call
from apps.campaigns.models import CampaignLead

logger = logging.getLogger(__name__)

# Vapi endedReason values that indicate the lead never really connected and a
# retry is worthwhile (no answer, busy, voicemail, etc.).
RETRYABLE_ENDED_REASONS = {
    "customer-did-not-answer",
    "customer-busy",
    "voicemail",
    "no-answer",
    "customer-did-not-give-microphone-permission",
    "twilio-failed-to-connect-call",
    "pipeline-error",
}

_STATUS_MAP = {
    "queued": Call.Status.QUEUED,
    "ringing": Call.Status.RINGING,
    "in-progress": Call.Status.IN_PROGRESS,
    "forwarding": Call.Status.IN_PROGRESS,
    "ended": Call.Status.ENDED,
}


def _get_call(message: dict) -> Call | None:
    call_id = (message.get("call") or {}).get("id") or message.get("callId")
    if not call_id:
        return None
    return Call.objects.filter(vapi_call_id=call_id).select_related(
        "campaign_lead__campaign"
    ).first()


def handle_status_update(message: dict) -> str:
    call = _get_call(message)
    if not call:
        return "unknown-call"
    new_status = _STATUS_MAP.get((message.get("status") or "").lower())
    if new_status and call.status != new_status:
        call.status = new_status
        if new_status == Call.Status.IN_PROGRESS and not call.started_at:
            call.started_at = timezone.now()
        call.save(update_fields=["status", "started_at", "updated_at"])
    return "ok"


def handle_end_of_call_report(message: dict) -> str:
    call = _get_call(message)
    if not call:
        return "unknown-call"

    # Idempotency: if we already finalized this call, do nothing.
    already_done = call.status == Call.Status.ENDED and bool(call.raw_end_report)

    artifact = message.get("artifact") or {}
    analysis = message.get("analysis") or {}
    ended_reason = message.get("endedReason", "") or ""

    call.status = Call.Status.ENDED
    call.ended_at = timezone.now()
    call.ended_reason = ended_reason
    call.transcript = message.get("transcript") or artifact.get("transcript") or call.transcript
    call.summary = message.get("summary") or analysis.get("summary") or call.summary
    call.recording_url = (
        message.get("recordingUrl")
        or artifact.get("recordingUrl")
        or call.recording_url
    )
    call.structured_outcome = {
        "successEvaluation": analysis.get("successEvaluation"),
        "structuredData": analysis.get("structuredData"),
    }
    cost = message.get("cost")
    if cost is not None:
        call.cost = cost
    call.raw_end_report = message
    call.save()

    if not already_done:
        _resolve_lead(call.campaign_lead, ended_reason)
        _kick_dispatch(call.campaign_lead.campaign_id)
    return "ok"


def _resolve_lead(cl: CampaignLead, ended_reason: str) -> None:
    """Free the slot: mark the lead done, or schedule a retry if it didn't
    connect and attempts remain."""
    retryable = ended_reason in RETRYABLE_ENDED_REASONS
    if retryable and cl.attempts < cl.campaign.max_attempts:
        cl.status = CampaignLead.Status.FAILED
        cl.next_attempt_at = timezone.now() + timedelta(
            minutes=cl.campaign.retry_delay_minutes
        )
        cl.save(update_fields=["status", "next_attempt_at"])
    elif retryable:
        cl.status = CampaignLead.Status.EXHAUSTED
        cl.save(update_fields=["status"])
    else:
        cl.status = CampaignLead.Status.DONE
        cl.save(update_fields=["status"])


def _kick_dispatch(campaign_id: int) -> None:
    """Refill the freed slot immediately rather than waiting for the beat tick."""
    from apps.calls.tasks import tick_campaigns

    tick_campaigns.delay(campaign_id)


HANDLERS = {
    "status-update": handle_status_update,
    "end-of-call-report": handle_end_of_call_report,
}


def dispatch_event(message: dict) -> str:
    msg_type = message.get("type", "")
    handler = HANDLERS.get(msg_type)
    if not handler:
        logger.info("Ignoring unhandled Vapi event type: %s", msg_type)
        return "ignored"
    return handler(message)

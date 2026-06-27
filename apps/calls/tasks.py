"""Celery tasks: the call dispatch loop.

tick_campaigns  -> for each running campaign, reserve dial-able leads and
                   enqueue a place_call per lead (throttled).
place_call      -> hand one lead to Vapi, record a Call row.
retry_failed    -> move retryable leads back to PENDING when their backoff
                   window has elapsed.
"""
from __future__ import annotations

import logging

from celery import shared_task
from django.utils import timezone

from apps.calls.models import Call
from apps.campaigns.models import Campaign, CampaignLead
from apps.campaigns.services.dispatch import maybe_complete, plan_and_reserve
from apps.vapi.client import VapiClient, VapiError

logger = logging.getLogger(__name__)


@shared_task
def tick_campaigns(campaign_id: int | None = None) -> dict:
    """Reserve and enqueue calls for running campaigns. Safe to run often."""
    qs = Campaign.objects.filter(status=Campaign.Status.RUNNING)
    if campaign_id is not None:
        qs = qs.filter(id=campaign_id)

    dispatched: dict[int, int] = {}
    for campaign in qs:
        reserved = plan_and_reserve(campaign)
        for cl_id in reserved:
            place_call.delay(cl_id)
        dispatched[campaign.id] = len(reserved)
        if not reserved:
            maybe_complete(campaign)
    return dispatched


@shared_task(bind=True, max_retries=0)
def place_call(self, campaign_lead_id: int) -> str:
    """Place a single outbound call for an already-reserved (IN_FLIGHT) lead."""
    cl = (
        CampaignLead.objects.select_related("campaign__organization", "lead")
        .get(id=campaign_lead_id)
    )
    campaign = cl.campaign
    lead = cl.lead
    org = campaign.organization

    phone_number_id = campaign.resolved_phone_number_id()
    assistant_id = campaign.resolved_assistant_id()
    if not phone_number_id or not assistant_id:
        return _fail_lead(cl, "campaign not provisioned (missing assistant/phone number id)")

    try:
        resp = VapiClient().place_call(
            phone_number_id=phone_number_id,
            assistant_id=assistant_id,
            customer_number=lead.phone_e164,
            customer_name=lead.name,
            variable_values=lead.call_variables(),
            voice_provider=org.default_voice_provider if org.default_voice_id else "",
            voice_id=org.default_voice_id,
        )
    except VapiError as exc:
        logger.warning("Vapi place_call failed for lead %s: %s", lead.id, exc)
        return _fail_lead(cl, f"vapi error {exc.status_code}")

    call = Call.objects.create(
        campaign_lead=cl,
        vapi_call_id=resp["id"],
        status=_map_status(resp.get("status", "queued")),
        started_at=timezone.now(),
    )
    return call.vapi_call_id


@shared_task
def retry_failed() -> int:
    """Requeue FAILED leads whose backoff has elapsed and that still have
    attempts left; exhaust the rest."""
    now = timezone.now()
    requeued = 0
    failed = CampaignLead.objects.select_related("campaign").filter(
        status=CampaignLead.Status.FAILED,
        next_attempt_at__lte=now,
    )
    for cl in failed:
        if cl.attempts >= cl.campaign.max_attempts:
            cl.status = CampaignLead.Status.EXHAUSTED
            cl.save(update_fields=["status"])
        else:
            cl.status = CampaignLead.Status.PENDING
            cl.next_attempt_at = None
            cl.save(update_fields=["status", "next_attempt_at"])
            requeued += 1
    return requeued


# -- helpers ----------------------------------------------------------------
def _fail_lead(cl: CampaignLead, reason: str) -> str:
    """Release the slot: exhaust if out of attempts, else schedule a retry."""
    from datetime import timedelta

    if cl.attempts >= cl.campaign.max_attempts:
        cl.status = CampaignLead.Status.EXHAUSTED
        cl.next_attempt_at = None
    else:
        cl.status = CampaignLead.Status.FAILED
        cl.next_attempt_at = timezone.now() + timedelta(
            minutes=cl.campaign.retry_delay_minutes
        )
    cl.save(update_fields=["status", "next_attempt_at"])
    logger.info("Lead %s -> %s (%s)", cl.lead_id, cl.status, reason)
    return cl.status


_STATUS_MAP = {
    "queued": Call.Status.QUEUED,
    "ringing": Call.Status.RINGING,
    "in-progress": Call.Status.IN_PROGRESS,
    "in_progress": Call.Status.IN_PROGRESS,
    "forwarding": Call.Status.IN_PROGRESS,
    "ended": Call.Status.ENDED,
}


def _map_status(vapi_status: str) -> str:
    return _STATUS_MAP.get((vapi_status or "").lower(), Call.Status.QUEUED)

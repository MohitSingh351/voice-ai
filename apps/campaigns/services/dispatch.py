"""Dispatch logic: how many calls to launch for a campaign, and reserving the
leads to call. Pure-ish functions so they're unit-testable; the Celery tasks in
apps.calls.tasks call these and do the actual Vapi I/O.

Concurrency model: a CampaignLead in IN_FLIGHT occupies one slot for its whole
active lifetime — from the moment it's reserved (here) until a webhook or a
failed dispatch flips it to done/failed/exhausted. Counting IN_FLIGHT leads
(rather than Call rows) closes the race where a reserved lead hasn't created
its Call row yet, and keeps the DB the single source of truth.
"""
from __future__ import annotations

from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from apps.campaigns.models import Campaign, CampaignLead
from apps.campaigns.services.throttle import reserve_call_budget


def count_in_flight(campaign: Campaign) -> int:
    return campaign.campaign_leads.filter(status=CampaignLead.Status.IN_FLIGHT).count()


def available_slots(campaign: Campaign) -> int:
    return max(0, campaign.max_concurrent_calls - count_in_flight(campaign))


def reserve_due_leads(campaign: Campaign, limit: int) -> list[int]:
    """Atomically claim up to `limit` leads ready to dial, flipping them to
    IN_FLIGHT (and incrementing attempts) so a concurrent tick can't grab the
    same ones. Returns the claimed CampaignLead ids. Uses SELECT ... FOR UPDATE
    SKIP LOCKED to avoid lock contention between overlapping ticks.
    """
    if limit <= 0:
        return []
    now = timezone.now()
    due = Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now)
    with transaction.atomic():
        ids = list(
            CampaignLead.objects.select_for_update(skip_locked=True)
            .filter(campaign=campaign, status=CampaignLead.Status.PENDING)
            .filter(due)
            .order_by("id")
            .values_list("id", flat=True)[:limit]
        )
        if ids:
            CampaignLead.objects.filter(id__in=ids).update(
                status=CampaignLead.Status.IN_FLIGHT,
                attempts=F("attempts") + 1,
            )
    return ids


def plan_and_reserve(campaign: Campaign) -> list[int]:
    """Compute launchable count (slots ∩ per-minute budget) and reserve leads.
    Returns the reserved CampaignLead ids to dial now.
    """
    slots = available_slots(campaign)
    if slots <= 0:
        return []
    granted = reserve_call_budget(campaign.id, campaign.calls_per_minute, slots)
    return reserve_due_leads(campaign, granted)


def maybe_complete(campaign: Campaign) -> bool:
    """Mark a running campaign completed when no pending/in-flight work remains."""
    has_work = campaign.campaign_leads.filter(
        status__in=[CampaignLead.Status.PENDING, CampaignLead.Status.IN_FLIGHT]
    ).exists()
    if not has_work and campaign.status == Campaign.Status.RUNNING:
        campaign.status = Campaign.Status.COMPLETED
        campaign.completed_at = timezone.now()
        campaign.save(update_fields=["status", "completed_at"])
        return True
    return False

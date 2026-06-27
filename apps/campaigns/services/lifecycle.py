"""Campaign lifecycle helpers shared by the DRF API and the dashboard views."""
from __future__ import annotations

from django.utils import timezone

from apps.campaigns.models import Campaign, CampaignLead


def add_leads(campaign: Campaign, lead_ids) -> int:
    """Attach leads to a campaign as PENDING work. Idempotent (ignores dupes)."""
    existing = set(
        campaign.campaign_leads.values_list("lead_id", flat=True)
    )
    to_create = [
        CampaignLead(campaign=campaign, lead_id=lid)
        for lid in lead_ids
        if lid not in existing
    ]
    if to_create:
        CampaignLead.objects.bulk_create(to_create, ignore_conflicts=True)
    return len(to_create)


def start_campaign(campaign: Campaign) -> None:
    campaign.status = Campaign.Status.RUNNING
    if campaign.started_at is None:
        campaign.started_at = timezone.now()
    campaign.completed_at = None
    campaign.save(update_fields=["status", "started_at", "completed_at"])
    # Kick the dispatcher immediately (beat is only a safety net).
    from apps.calls.tasks import tick_campaigns

    tick_campaigns.delay(campaign.id)


def pause_campaign(campaign: Campaign) -> None:
    campaign.status = Campaign.Status.PAUSED
    campaign.save(update_fields=["status"])


def stop_campaign(campaign: Campaign) -> None:
    campaign.status = Campaign.Status.COMPLETED
    campaign.completed_at = timezone.now()
    campaign.save(update_fields=["status", "completed_at"])

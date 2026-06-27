from django.db import models

from apps.campaigns.models import CampaignLead


class Call(models.Model):
    """One outbound call attempt, mirroring a Vapi call.

    `status` is updated by Vapi webhooks. The active statuses below count
    against a campaign's concurrency cap and are the source of truth for how
    many calls are in flight (so we never rely on a drift-prone counter).
    """

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RINGING = "ringing", "Ringing"
        IN_PROGRESS = "in_progress", "In progress"
        ENDED = "ended", "Ended"
        FAILED = "failed", "Failed"

    # Statuses that mean a call is occupying a concurrency slot.
    ACTIVE_STATUSES = (Status.QUEUED, Status.RINGING, Status.IN_PROGRESS)

    campaign_lead = models.ForeignKey(
        CampaignLead, on_delete=models.CASCADE, related_name="calls"
    )
    vapi_call_id = models.CharField(max_length=100, unique=True, db_index=True)
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.QUEUED, db_index=True
    )

    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    ended_reason = models.CharField(max_length=100, blank=True)

    transcript = models.TextField(blank=True)
    summary = models.TextField(blank=True)
    recording_url = models.URLField(blank=True, max_length=500)
    structured_outcome = models.JSONField(default=dict, blank=True)
    cost = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    raw_end_report = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.vapi_call_id} [{self.status}]"

    @property
    def is_active(self) -> bool:
        return self.status in self.ACTIVE_STATUSES

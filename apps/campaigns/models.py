from django.db import models

from apps.leads.models import Lead
from apps.organizations.models import Organization


class Campaign(models.Model):
    """A batch of outbound calls with throttle + retry policy.

    Vapi runs the actual conversation; this row controls how fast we hand
    leads to Vapi (`max_concurrent_calls`, `calls_per_minute`) and how many
    times we retry a lead that didn't connect.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        RUNNING = "running", "Running"
        PAUSED = "paused", "Paused"
        COMPLETED = "completed", "Completed"

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="campaigns"
    )
    name = models.CharField(max_length=200)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)

    # Vapi resources to use; default to the org's provisioned ids at dispatch.
    assistant_id = models.CharField(max_length=100, blank=True)
    from_phone_number_id = models.CharField(max_length=100, blank=True)

    # Throttle + retry policy.
    max_concurrent_calls = models.PositiveIntegerField(default=5)
    calls_per_minute = models.PositiveIntegerField(default=10)
    max_attempts = models.PositiveIntegerField(default=2)
    retry_delay_minutes = models.PositiveIntegerField(default=30)

    leads = models.ManyToManyField(Lead, through="CampaignLead", related_name="campaigns")

    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name

    def resolved_assistant_id(self) -> str:
        return self.assistant_id or self.organization.vapi_assistant_id

    def resolved_phone_number_id(self) -> str:
        return self.from_phone_number_id or self.organization.vapi_phone_number_id

    def counts(self) -> dict:
        """Status breakdown of this campaign's leads (for the dashboard)."""
        rows = self.campaign_leads.values("status").annotate(n=models.Count("id"))
        out = {s.value: 0 for s in CampaignLead.Status}
        for r in rows:
            out[r["status"]] = r["n"]
        out["total"] = sum(out.values())
        return out


class CampaignLead(models.Model):
    """Through row = the per-lead dispatch work queue for a campaign."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        IN_FLIGHT = "in_flight", "In flight"
        DONE = "done", "Done"
        FAILED = "failed", "Failed (retryable)"
        EXHAUSTED = "exhausted", "Exhausted"

    campaign = models.ForeignKey(
        Campaign, on_delete=models.CASCADE, related_name="campaign_leads"
    )
    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="campaign_leads")
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    # statuses that count against the concurrency cap / mean "still working it"
    ACTIVE_STATUSES = (Status.IN_FLIGHT,)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["campaign", "lead"], name="uniq_campaign_lead"
            )
        ]

    def __str__(self):
        return f"{self.campaign_id}:{self.lead_id} [{self.status}]"

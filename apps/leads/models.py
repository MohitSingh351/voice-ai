from django.db import models

from apps.organizations.models import Organization


class Lead(models.Model):
    """A person to call. `variables` holds arbitrary CSV columns that are passed
    to Vapi as `assistantOverrides.variableValues` for prompt personalization.
    """

    class Source(models.TextChoices):
        CSV = "csv", "CSV upload"
        MANUAL = "manual", "Manual entry"

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="leads"
    )
    name = models.CharField(max_length=200)
    phone_e164 = models.CharField(max_length=20, db_index=True)
    raw_phone = models.CharField(max_length=50, blank=True)
    variables = models.JSONField(default=dict, blank=True)
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.CSV)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "phone_e164"], name="uniq_org_phone"
            )
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} <{self.phone_e164}>"

    def call_variables(self) -> dict:
        """Merge name + custom columns into the variable map sent to Vapi."""
        return {"name": self.name, **(self.variables or {})}

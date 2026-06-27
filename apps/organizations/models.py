from django.db import models


class Organization(models.Model):
    """Single-tenant container for the MVP.

    Holds the Vapi resource IDs created by `manage.py provision_vapi` so they
    live in the database (source of truth) rather than loose in settings.
    The FK on every other model keeps a clean path to real multi-tenancy.
    """

    name = models.CharField(max_length=200, default="Default Organization")

    # Vapi resources (populated by provisioning).
    vapi_credential_id = models.CharField(max_length=100, blank=True)
    vapi_phone_number_id = models.CharField(max_length=100, blank=True)
    vapi_assistant_id = models.CharField(max_length=100, blank=True)
    default_caller_id = models.CharField(max_length=20, blank=True)

    # Org-wide voice applied to every outbound call via assistantOverrides.
    # Blank = use the assistant's own voice (set in the Vapi dashboard).
    default_voice_provider = models.CharField(max_length=40, blank=True, default="vapi")
    default_voice_id = models.CharField(max_length=80, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

    @property
    def is_provisioned(self) -> bool:
        return bool(self.vapi_phone_number_id and self.vapi_assistant_id)

    @classmethod
    def get_default(cls) -> Organization:
        """Return the single MVP organization, creating it on first use."""
        org, _ = cls.objects.get_or_create(pk=1, defaults={"name": "Default Organization"})
        return org

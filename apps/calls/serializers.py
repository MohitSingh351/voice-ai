from rest_framework import serializers

from apps.calls.models import Call


class CallSerializer(serializers.ModelSerializer):
    lead_name = serializers.CharField(source="campaign_lead.lead.name", read_only=True)
    phone = serializers.CharField(source="campaign_lead.lead.phone_e164", read_only=True)

    class Meta:
        model = Call
        fields = [
            "id",
            "vapi_call_id",
            "lead_name",
            "phone",
            "status",
            "started_at",
            "ended_at",
            "ended_reason",
            "summary",
            "transcript",
            "recording_url",
            "structured_outcome",
            "cost",
        ]

from rest_framework import serializers

from apps.leads.models import Lead
from apps.leads.services.csv_import import normalize_phone


class LeadSerializer(serializers.ModelSerializer):
    class Meta:
        model = Lead
        fields = ["id", "name", "phone_e164", "variables", "source", "created_at"]
        read_only_fields = ["id", "source", "created_at"]


class ManualLeadSerializer(serializers.Serializer):
    """Single ad-hoc lead entry (name + phone), normalized to E.164."""

    name = serializers.CharField(max_length=200)
    phone = serializers.CharField(max_length=50)
    variables = serializers.DictField(required=False, default=dict)

    def validate_phone(self, value):
        try:
            return normalize_phone(value, default_region="US")
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc

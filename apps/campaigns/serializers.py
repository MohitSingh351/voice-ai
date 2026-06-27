from rest_framework import serializers

from apps.campaigns.models import Campaign


class CampaignSerializer(serializers.ModelSerializer):
    counts = serializers.SerializerMethodField()

    class Meta:
        model = Campaign
        fields = [
            "id",
            "name",
            "status",
            "assistant_id",
            "from_phone_number_id",
            "max_concurrent_calls",
            "calls_per_minute",
            "max_attempts",
            "retry_delay_minutes",
            "counts",
            "created_at",
            "started_at",
            "completed_at",
        ]
        read_only_fields = ["id", "status", "counts", "created_at", "started_at", "completed_at"]

    def get_counts(self, obj):
        return obj.counts()


class CampaignCreateSerializer(serializers.ModelSerializer):
    lead_ids = serializers.ListField(
        child=serializers.IntegerField(), write_only=True, required=False, default=list
    )

    class Meta:
        model = Campaign
        fields = [
            "name",
            "assistant_id",
            "from_phone_number_id",
            "max_concurrent_calls",
            "calls_per_minute",
            "max_attempts",
            "retry_delay_minutes",
            "lead_ids",
        ]

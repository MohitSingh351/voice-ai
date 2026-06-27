from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.routers import DefaultRouter

from apps.campaigns.models import Campaign
from apps.campaigns.serializers import CampaignCreateSerializer, CampaignSerializer
from apps.campaigns.services.lifecycle import (
    add_leads,
    pause_campaign,
    start_campaign,
    stop_campaign,
)
from apps.organizations.models import Organization


class CampaignViewSet(viewsets.ModelViewSet):
    def get_queryset(self):
        return Campaign.objects.filter(organization=Organization.get_default())

    def get_serializer_class(self):
        if self.action == "create":
            return CampaignCreateSerializer
        return CampaignSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        lead_ids = serializer.validated_data.pop("lead_ids", [])
        campaign = Campaign.objects.create(
            organization=Organization.get_default(), **serializer.validated_data
        )
        add_leads(campaign, lead_ids)
        return Response(CampaignSerializer(campaign).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def add_leads(self, request, pk=None):
        campaign = self.get_object()
        added = add_leads(campaign, request.data.get("lead_ids", []))
        return Response({"added": added})

    @action(detail=True, methods=["post"])
    def start(self, request, pk=None):
        campaign = self.get_object()
        start_campaign(campaign)
        return Response(CampaignSerializer(campaign).data)

    @action(detail=True, methods=["post"])
    def pause(self, request, pk=None):
        campaign = self.get_object()
        pause_campaign(campaign)
        return Response(CampaignSerializer(campaign).data)

    @action(detail=True, methods=["post"])
    def stop(self, request, pk=None):
        campaign = self.get_object()
        stop_campaign(campaign)
        return Response(CampaignSerializer(campaign).data)


router = DefaultRouter()
router.register("campaigns", CampaignViewSet, basename="campaign")
urlpatterns = router.urls

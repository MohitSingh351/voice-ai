from rest_framework import mixins, viewsets
from rest_framework.routers import DefaultRouter

from apps.calls.models import Call
from apps.calls.serializers import CallSerializer
from apps.organizations.models import Organization


class CallViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    serializer_class = CallSerializer

    def get_queryset(self):
        qs = Call.objects.filter(
            campaign_lead__campaign__organization=Organization.get_default()
        ).select_related("campaign_lead__lead")
        campaign_id = self.request.query_params.get("campaign")
        if campaign_id:
            qs = qs.filter(campaign_lead__campaign_id=campaign_id)
        return qs


router = DefaultRouter()
router.register("calls", CallViewSet, basename="call")
urlpatterns = router.urls

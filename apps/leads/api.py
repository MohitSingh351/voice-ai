from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.routers import DefaultRouter

from apps.leads.models import Lead
from apps.leads.serializers import LeadSerializer, ManualLeadSerializer
from apps.leads.services.csv_import import import_leads_csv
from apps.organizations.models import Organization


class LeadViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = LeadSerializer

    def get_queryset(self):
        return Lead.objects.filter(organization=Organization.get_default())

    @action(detail=False, methods=["post"], parser_classes=[MultiPartParser, FormParser])
    def upload(self, request):
        """Upload a CSV file (multipart field `file`)."""
        file = request.FILES.get("file")
        if not file:
            return Response(
                {"detail": "No file provided (field 'file')."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        result = import_leads_csv(file, Organization.get_default())
        return Response(
            {
                "created": result.created_count,
                "skipped_duplicates": result.skipped_duplicates,
                "errors": [
                    {"line": e.line, "reason": e.reason} for e in result.errors
                ],
            },
            status=status.HTTP_201_CREATED if result.created_count else status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"])
    def manual(self, request):
        """Create a single lead from name + phone."""
        serializer = ManualLeadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        lead, created = Lead.objects.get_or_create(
            organization=Organization.get_default(),
            phone_e164=data["phone"],
            defaults={
                "name": data["name"],
                "raw_phone": request.data.get("phone", ""),
                "variables": data.get("variables", {}),
                "source": Lead.Source.MANUAL,
            },
        )
        return Response(
            LeadSerializer(lead).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


router = DefaultRouter()
router.register("leads", LeadViewSet, basename="lead")
urlpatterns = router.urls

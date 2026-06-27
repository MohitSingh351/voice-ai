"""Server-rendered dashboard. Single-org MVP; all views require login."""
from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Max
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.calls.models import Call
from apps.campaigns.models import Campaign
from apps.campaigns.services.lifecycle import (
    add_leads,
    pause_campaign,
    start_campaign,
    stop_campaign,
)
from apps.leads.models import Lead
from apps.leads.serializers import normalize_phone
from apps.leads.services.csv_import import import_leads_csv
from apps.organizations.models import Organization


def _org():
    return Organization.get_default()


# Vapi's built-in voices (provider "vapi"); no extra API key needed. Confirm the
# exact list in your Vapi dashboard - edit here to add/remove options.
VAPI_VOICES = [
    "Elliot",
    "Rohan",
    "Hana",
    "Neha",
    "Cole",
    "Harry",
    "Paige",
    "Spencer",
    "Lily",
    "Savannah",
]


@login_required
def settings_view(request):
    org = _org()
    if request.method == "POST":
        voice_id = (request.POST.get("default_voice_id") or "").strip()
        # "" means: fall back to the assistant's own voice (Vapi dashboard).
        org.default_voice_id = voice_id
        org.default_voice_provider = "vapi" if voice_id else ""
        org.save(update_fields=["default_voice_id", "default_voice_provider", "updated_at"])
        if voice_id:
            messages.success(request, f"All calls will now use the “{voice_id}” voice.")
        else:
            messages.success(request, "Voice override cleared - using the assistant's own voice.")
        return redirect("settings")

    return render(
        request,
        "dashboard/settings.html",
        {"org": org, "voices": VAPI_VOICES},
    )


@login_required
def campaign_list(request):
    org = _org()
    if request.method == "POST":
        lead_count = org.leads.count()
        if not lead_count:
            messages.error(request, "Add some leads before creating a campaign.")
            return redirect("leads")
        name = (request.POST.get("name") or "Untitled campaign").strip()
        campaign = Campaign.objects.create(
            organization=org,
            name=name,
            max_concurrent_calls=int(request.POST.get("max_concurrent_calls") or 5),
            calls_per_minute=int(request.POST.get("calls_per_minute") or 10),
            max_attempts=int(request.POST.get("max_attempts") or 2),
        )
        added = add_leads(campaign, list(org.leads.values_list("id", flat=True)))
        messages.success(
            request, f"Created “{name}” with {added} lead{'' if added == 1 else 's'}."
        )
        return redirect(reverse("campaign_detail", args=[campaign.id]))

    campaigns = Campaign.objects.filter(organization=org)
    return render(
        request,
        "dashboard/campaign_list.html",
        {
            "campaigns": [(c, c.counts()) for c in campaigns],
            "org": org,
            "lead_count": org.leads.count(),
        },
    )


@login_required
def leads(request):
    org = _org()
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "csv":
            file = request.FILES.get("file")
            if not file:
                messages.error(request, "Choose a CSV file first.")
            else:
                result = import_leads_csv(file, org)
                messages.success(
                    request,
                    f"Imported {result.created_count} lead(s) · "
                    f"{result.skipped_duplicates} duplicate(s) skipped · "
                    f"{result.error_count} row(s) with errors.",
                )
                for err in result.errors[:10]:
                    messages.error(request, f"Line {err.line}: {err.reason}")
        elif action == "manual":
            name = (request.POST.get("name") or "").strip()
            phone = (request.POST.get("phone") or "").strip()
            try:
                e164 = normalize_phone(phone, "US")
            except ValueError as exc:
                messages.error(request, f"Invalid phone: {exc}")
            else:
                if not name:
                    messages.error(request, "Name is required.")
                else:
                    _, created = Lead.objects.get_or_create(
                        organization=org,
                        phone_e164=e164,
                        defaults={"name": name, "raw_phone": phone, "source": Lead.Source.MANUAL},
                    )
                    if created:
                        messages.success(request, f"Added {name} ({e164}).")
                    else:
                        messages.error(request, f"{e164} is already in your leads.")
        return redirect("leads")

    return render(
        request,
        "dashboard/leads.html",
        {"leads": org.leads.all(), "lead_count": org.leads.count(), "org": org},
    )


@login_required
@require_POST
def lead_delete(request, pk):
    lead = get_object_or_404(Lead, pk=pk, organization=_org())
    name = lead.name
    lead.delete()
    messages.success(request, f"Deleted {name}.")
    return redirect("leads")


@login_required
def campaign_detail(request, pk):
    campaign = get_object_or_404(Campaign, pk=pk, organization=_org())
    return render(
        request,
        "dashboard/campaign_detail.html",
        {"campaign": campaign, "counts": campaign.counts()},
    )


@login_required
def campaign_leads_partial(request, pk):
    """HTMX-polled fragment: per-lead status + latest call."""
    campaign = get_object_or_404(Campaign, pk=pk, organization=_org())
    rows = (
        campaign.campaign_leads.select_related("lead")
        .annotate(latest_call=Max("calls__id"))
        .order_by("id")
    )
    calls = {c.id: c for c in Call.objects.filter(campaign_lead__campaign=campaign)}
    return render(
        request,
        "dashboard/_leads_table.html",
        {"campaign": campaign, "rows": rows, "calls": calls, "counts": campaign.counts()},
    )


@login_required
@require_POST
def campaign_action(request, pk, action):
    campaign = get_object_or_404(Campaign, pk=pk, organization=_org())
    if action == "start":
        if not _org().is_provisioned:
            messages.error(request, "Run `manage.py provision_vapi` before starting calls.")
        else:
            start_campaign(campaign)
            messages.success(request, "Campaign started.")
    elif action == "pause":
        pause_campaign(campaign)
        messages.success(request, "Campaign paused.")
    elif action == "stop":
        stop_campaign(campaign)
        messages.success(request, "Campaign stopped.")
    return redirect(reverse("campaign_detail", args=[campaign.id]))


@login_required
def call_detail(request, pk):
    call = get_object_or_404(
        Call.objects.select_related("campaign_lead__lead", "campaign_lead__campaign"),
        pk=pk,
        campaign_lead__campaign__organization=_org(),
    )
    return render(request, "dashboard/call_detail.html", {"call": call})

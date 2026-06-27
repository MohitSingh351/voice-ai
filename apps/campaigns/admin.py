from django.contrib import admin

from .models import Campaign, CampaignLead


class CampaignLeadInline(admin.TabularInline):
    model = CampaignLead
    extra = 0
    raw_id_fields = ("lead",)


@admin.register(Campaign)
class CampaignAdmin(admin.ModelAdmin):
    list_display = ("name", "status", "max_concurrent_calls", "calls_per_minute", "created_at")
    list_filter = ("status",)
    inlines = [CampaignLeadInline]


@admin.register(CampaignLead)
class CampaignLeadAdmin(admin.ModelAdmin):
    list_display = ("campaign", "lead", "status", "attempts", "next_attempt_at")
    list_filter = ("status",)

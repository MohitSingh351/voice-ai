from django.contrib import admin

from .models import Call


@admin.register(Call)
class CallAdmin(admin.ModelAdmin):
    list_display = ("vapi_call_id", "status", "ended_reason", "cost", "created_at")
    list_filter = ("status",)
    search_fields = ("vapi_call_id",)
    readonly_fields = ("raw_end_report",)

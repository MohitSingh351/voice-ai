from django.contrib import admin

from .models import Lead


@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    list_display = ("name", "phone_e164", "source", "created_at")
    list_filter = ("source",)
    search_fields = ("name", "phone_e164")

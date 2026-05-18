from __future__ import annotations

from django.contrib import admin

from .models import AuditEvent


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "actor", "action", "table_name", "record_id", "source")
    list_filter = ("source", "action", "table_name", "created_at")
    search_fields = ("actor", "action", "table_name", "summary")
    readonly_fields = (
        "created_at",
        "organization_id",
        "actor",
        "action",
        "table_name",
        "record_id",
        "source",
        "summary",
        "before",
        "after",
    )
    ordering = ("-created_at", "-id")

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from django.conf import settings
from django.apps import apps
from django.core.paginator import Paginator
from django.db import OperationalError, ProgrammingError
from django.utils import timezone


def _audit_model():
    return apps.get_model("audit", "AuditEvent")


def record_django_audit_event(
    *,
    action: str,
    table_name: str,
    record_id: int | None = None,
    organization_id: int | None = None,
    actor: str = "",
    source: str = "django",
    summary: str = "",
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> int:
    AuditEvent = _audit_model()
    event = AuditEvent.objects.create(
        organization_id=organization_id,
        actor=actor,
        action=action,
        table_name=table_name,
        record_id=record_id,
        source=source,
        summary=summary,
        before=before,
        after=after,
    )
    return int(event.pk or 0)


def mirror_legacy_audit_event(
    *,
    organization_id: int | None,
    action: str,
    table_name: str,
    record_id: int | None,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    actor: str = "",
) -> None:
    """Best-effort mirror of legacy write audit into the Django audit table."""
    if os.environ.get("POWER_CHURCH_DJANGO_AUDIT_MIRROR", "1").strip().lower() in {"0", "false", "no", "nao", "off"}:
        return
    legacy_path = Path(str(getattr(settings, "POWER_CHURCH_LEGACY_DB_PATH", "")))
    if legacy_path.name.startswith("power_church_write_probe"):
        return
    try:
        record_django_audit_event(
            organization_id=organization_id,
            actor=actor,
            action=action,
            table_name=table_name,
            record_id=record_id,
            source="legacy_write_bridge",
            summary=f"Espelho Django da auditoria legada: {action}",
            before=before,
            after=after,
        )
    except Exception:
        return


def list_django_audit_events(
    *,
    action: str = "",
    table_name: str = "",
    page: int = 1,
    page_size: int = 120,
) -> dict[str, Any]:
    AuditEvent = _audit_model()
    page = max(1, int(page or 1))
    page_size = min(max(50, int(page_size or 120)), 1000)
    queryset = AuditEvent.objects.all()
    if action:
        queryset = queryset.filter(action=action)
    if table_name:
        queryset = queryset.filter(table_name=table_name)
    paginator = Paginator(queryset, page_size)
    page_obj = paginator.get_page(page)
    actions = list(
        AuditEvent.objects.order_by("action")
        .values_list("action", flat=True)
        .distinct()[:80]
    )
    tables = list(
        AuditEvent.objects.order_by("table_name")
        .values_list("table_name", flat=True)
        .distinct()[:80]
    )
    return {
        "action": action,
        "table": table_name,
        "items": [
            {
                "id": event.id,
                "criado_em": timezone.localtime(event.created_at).strftime("%d/%m/%Y %H:%M"),
                "actor": event.actor or "",
                "acao": event.action,
                "tabela": event.table_name,
                "registro_id": event.record_id or "",
                "origem": event.source,
                "resumo": event.summary,
            }
            for event in page_obj.object_list
        ],
        "actions": actions,
        "tables": tables,
        "total": paginator.count,
        "shown": len(page_obj.object_list),
        "page": page_obj.number,
        "page_size": page_size,
        "total_pages": paginator.num_pages or 1,
        "has_previous": page_obj.has_previous(),
        "has_next": page_obj.has_next(),
        "previous_page": page_obj.previous_page_number() if page_obj.has_previous() else 1,
        "next_page": page_obj.next_page_number() if page_obj.has_next() else paginator.num_pages or 1,
    }

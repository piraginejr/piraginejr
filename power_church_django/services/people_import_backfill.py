from __future__ import annotations

from typing import Any

from django.db import transaction

from power_church_django.apps.people.models import NativePeopleImportLine, NativePeopleImportLot, NativePeopleImportPending
from power_church_django.services.legacy import LegacyDatabaseError, get_people_import_lot_detail, people_import_dashboard
from power_church_django.services.people_import_native import get_people_import_lot_detail_postgres


def _safe_dashboard(limit: int) -> dict[str, Any]:
    try:
        return people_import_dashboard(limit=limit)
    except LegacyDatabaseError:
        return {"lots": [], "total_people": 0, "open_pendencies": 0, "total_lots": 0, "shown": 0}


def sync_people_import_lot_postgres(lot_id: int, line_limit: int = 1000, pending_limit: int = 0) -> dict[str, Any] | None:
    detail = get_people_import_lot_detail(int(lot_id or 0), line_limit=line_limit, pending_limit=pending_limit)
    if detail is None:
        return None
    lot = detail.get("lot") or {}
    cards = detail.get("cards") or {}
    with transaction.atomic():
        native_lot, _ = NativePeopleImportLot.objects.update_or_create(
            legacy_id=int(lot.get("id") or 0),
            defaults={
                "import_type": str(lot.get("type") or ""),
                "file_name": str(lot.get("arquivo_nome") or ""),
                "status": str(lot.get("status") or ""),
                "total_lines": int(lot.get("total_linhas") or 0),
                "imported_lines": int(lot.get("linhas_importadas") or 0),
                "ignored_lines": int(lot.get("linhas_ignoradas") or 0),
                "error_lines": int(lot.get("linhas_com_erro") or 0),
                "open_pendencies": int(cards.get("open_pendencies") or 0),
                "active_people": int(cards.get("active_people") or 0),
                "without_name": int(cards.get("without_name") or 0),
                "review_mappings": int(cards.get("review_mappings") or 0),
                "created_at_display": str(lot.get("criado_em") or ""),
                "confirmed_at_display": str(lot.get("confirmado_em") or ""),
                "status_rows_json": list(detail.get("status_rows") or []),
                "mapping_rows_json": list(detail.get("mapping_rows") or []),
            },
        )
        native_lot.pendings.all().delete()
        native_lot.lines.all().delete()
        NativePeopleImportPending.objects.bulk_create(
            [
                NativePeopleImportPending(
                    legacy_id=int(row.get("id") or 0),
                    lot=native_lot,
                    line_number=int(row.get("linha") or 0) if str(row.get("linha") or "").isdigit() else 0,
                    severity=str(row.get("severidade") or ""),
                    issue_type=str(row.get("tipo") or ""),
                    description=str(row.get("descricao") or ""),
                    suggested_action=str(row.get("acao_sugerida") or ""),
                    resolved=bool(row.get("resolvido")),
                    person_name=str(row.get("pessoa_nome") or ""),
                )
                for row in (detail.get("pending_rows") or [])
                if int(row.get("id") or 0)
            ]
        )
        NativePeopleImportLine.objects.bulk_create(
            [
                NativePeopleImportLine(
                    legacy_id=int(row.get("id") or 0),
                    lot=native_lot,
                    line_number=int(row.get("linha") or 0) if str(row.get("linha") or "").isdigit() else 0,
                    status=str(row.get("status") or ""),
                    original_name=str(row.get("original_name") or ""),
                    normalized_action=str(row.get("normalized_action") or ""),
                    person_legacy_id=int(row.get("person_id") or 0) or None,
                    person_name=str(row.get("person_name") or ""),
                    person_cpf=str(row.get("person_cpf") or ""),
                    person_status=str(row.get("person_status") or ""),
                    person_active=bool(row.get("person_active")),
                )
                for row in (detail.get("line_rows") or [])
                if int(row.get("id") or 0)
            ]
        )
    return get_people_import_lot_detail_postgres(int(lot_id or 0), line_limit=line_limit)


def backfill_people_import_lots_postgres(limit: int = 12, line_limit: int = 1000, pending_limit: int = 0) -> int:
    dashboard = _safe_dashboard(limit)
    synced = 0
    for lot in dashboard.get("lots") or []:
        if sync_people_import_lot_postgres(int(lot.get("id") or 0), line_limit=line_limit, pending_limit=pending_limit):
            synced += 1
    return synced

from __future__ import annotations

from dataclasses import asdict
from datetime import date
from decimal import Decimal
from pathlib import Path
import sqlite3

from django.db.models import Count, Q

from power_church_core.bank_lots import StatementEntryPlan, statement_entry_plan
from power_church_core.bank_parsers import parse_statement_pdf_by_layout, statement_should_skip_entry
from power_church_core.normalization import normalize_query

from power_church_django.services.legacy import (
    get_bank_movement_detail,
    _money,
    br_date,
    br_datetime,
    document_digits,
    document_query_matches,
    format_document,
    human_pending_review_sql,
    legacy_db_path,
    valid_cpf,
)

from .models import StatementImportPilotLot, StatementImportPilotMovement


def plan_statement_import(layout_code: str, pdf_path: Path) -> dict[str, object]:
    parsed = parse_statement_pdf_by_layout(layout_code, pdf_path)
    stored_layout = normalize_query(parsed.get("layout_code") or layout_code).upper() or layout_code
    plans: list[StatementEntryPlan] = [
        statement_entry_plan(stored_layout, entry)
        for entry in parsed["entries"]
        if not statement_should_skip_entry(stored_layout, entry)
    ]
    return {
        "bank_name": parsed["bank_name"],
        "statement_kind": parsed["statement_kind"],
        "layout_code": stored_layout,
        "file_hash": parsed["file_hash"],
        "period_start": parsed["period_start"],
        "period_end": parsed["period_end"],
        "entries": [asdict(plan) for plan in plans],
    }


def _parse_iso_date(value: object) -> date | None:
    text = normalize_query(value)
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _scalar(conn: sqlite3.Connection, sql: str, params: tuple[object, ...] = ()) -> object:
    row = conn.execute(sql, params).fetchone()
    return row[0] if row else None


def _persist_statement_pilot_from_sqlite(
    sqlite_db_path: Path,
    lot_id: int,
    *,
    source_backend: str,
    pdf_provider: str = "",
    comparison_ok: bool = False,
    comparison_note: str = "",
    report_path: str = "",
) -> StatementImportPilotLot:
    conn = sqlite3.connect(sqlite_db_path)
    conn.row_factory = sqlite3.Row
    try:
        lot = conn.execute(
            """
            SELECT id, banco, layout_codigo, nome_arquivo, hash_arquivo, periodo_inicio, periodo_fim,
                   total_movimentos, total_valor, status, observacoes, criado_em
              FROM extrato_lotes
             WHERE id = ?
            """,
            (lot_id,),
        ).fetchone()
        if lot is None:
            raise ValueError(f"Lote de extrato nao encontrado no clone: {lot_id}")
        reference_key = ":".join(
            [
                str(source_backend),
                normalize_query(lot["layout_codigo"]).upper(),
                normalize_query(lot["hash_arquivo"]) or normalize_query(lot["nome_arquivo"]) or str(lot_id),
            ]
        )
        review_counts_rows = conn.execute(
            """
            SELECT COALESCE(review_status, '') AS review_status, COUNT(*) AS total
              FROM extrato_movimentos
             WHERE lote_id = ? AND ativo = 1
             GROUP BY COALESCE(review_status, '')
             ORDER BY total DESC, review_status
            """,
            (lot_id,),
        ).fetchall()
        review_counts = {str(row["review_status"] or "sem_status"): int(row["total"] or 0) for row in review_counts_rows}
        imported_count = int(
            _scalar(
                conn,
                "SELECT COUNT(*) FROM extrato_movimentos WHERE lote_id = ? AND ativo = 1 AND imported_contribution_id IS NOT NULL",
                (lot_id,),
            )
            or 0
        )
        ignored_count = int(
            _scalar(
                conn,
                "SELECT COUNT(*) FROM extrato_movimentos WHERE lote_id = ? AND ativo = 1 AND review_status = 'ignorado'",
                (lot_id,),
            )
            or 0
        )
        pending_human_count = int(
            _scalar(
                conn,
                f"""
                SELECT COUNT(*)
                  FROM extrato_movimentos m
                 WHERE m.lote_id = ?
                   AND m.ativo = 1
                   AND {human_pending_review_sql('m')}
                """,
                (lot_id,),
            )
            or 0
        )
        pilot_lot, _ = StatementImportPilotLot.objects.update_or_create(
            reference_key=reference_key,
            defaults={
                "source_backend": source_backend,
                "source_db_path": str(sqlite_db_path),
                "source_lot_id": int(lot["id"] or 0),
                "bank_name": lot["banco"] or "",
                "layout_code": lot["layout_codigo"] or "",
                "file_name": lot["nome_arquivo"] or "",
                "file_hash": lot["hash_arquivo"] or "",
                "period_start": _parse_iso_date(lot["periodo_inicio"]),
                "period_end": _parse_iso_date(lot["periodo_fim"]),
                "movement_count": int(lot["total_movimentos"] or 0),
                "total_value": Decimal(str(lot["total_valor"] or 0)),
                "lot_status": lot["status"] or "",
                "pdf_provider": pdf_provider or "",
                "comparison_ok": bool(comparison_ok),
                "comparison_note": comparison_note or "",
                "report_path": report_path or "",
                "metadata": {
                    "review_counts": review_counts,
                    "imported_count": imported_count,
                    "ignored_count": ignored_count,
                    "pending_human_count": pending_human_count,
                    "observacoes": lot["observacoes"] or "",
                    "legacy_created_at": lot["criado_em"] or "",
                },
            },
        )
        StatementImportPilotMovement.objects.filter(lot=pilot_lot).delete()
        movement_rows = conn.execute(
            """
            SELECT m.id, m.pagina, m.ordem_no_lote, m.data_movimento, m.competencia, m.competencia_ordem, m.valor,
                   m.codigo_centavos, m.movement_kind, m.receiving_code, m.bank_document, m.prefixo_historico, m.tipo_sugerido,
                   m.nome_origem, m.nome_normalizado, m.origin_label, m.confidence, m.match_score,
                   m.suggested_person_id, m.resolved_person_id, m.suggested_contribuinte_id, m.resolved_contribuinte_id,
                   sp.nome AS suggested_person_name, sp.cpf AS suggested_person_cpf,
                   rp.nome AS resolved_person_name, rp.cpf AS resolved_person_cpf,
                   sc.nome AS suggested_contributor_name, rc.nome AS resolved_contributor_name,
                   m.review_status, m.review_notes, m.imported_contribution_id, m.duplicate_movement_id,
                   m.duplicate_contribution_id, m.duplicate_reason, m.fingerprint, m.signature_global, m.raw_text
              FROM extrato_movimentos m
              LEFT JOIN pessoas sp ON sp.id = m.suggested_person_id
              LEFT JOIN pessoas rp ON rp.id = m.resolved_person_id
              LEFT JOIN contribuintes sc ON sc.id = m.suggested_contribuinte_id
              LEFT JOIN contribuintes rc ON rc.id = m.resolved_contribuinte_id
             WHERE m.lote_id = ? AND m.ativo = 1
             ORDER BY m.ordem_no_lote ASC, m.id ASC
            """,
            (lot_id,),
        ).fetchall()
        StatementImportPilotMovement.objects.bulk_create(
            [
                StatementImportPilotMovement(
                    lot=pilot_lot,
                    source_movement_id=int(row["id"] or 0) or None,
                    page_number=int(row["pagina"] or 1),
                    order_in_lot=int(row["ordem_no_lote"] or 0),
                    movement_date=_parse_iso_date(row["data_movimento"]),
                    competence=row["competencia"] or "",
                    competence_order=int(row["competencia_ordem"] or 0),
                    amount=Decimal(str(row["valor"] or 0)),
                    cent_code=row["codigo_centavos"] or "",
                    movement_kind=row["movement_kind"] or "",
                    receiving_code=row["receiving_code"] or "",
                    bank_document=row["bank_document"] or "",
                    source_name=row["nome_origem"] or "",
                    source_name_normalized=row["nome_normalizado"] or "",
                    origin_label=row["origin_label"] or "",
                    confidence=row["confidence"] or "",
                    match_score=Decimal(str(row["match_score"] or 0)),
                    suggested_person_legacy_id=int(row["suggested_person_id"] or 0) or None,
                    resolved_person_legacy_id=int(row["resolved_person_id"] or 0) or None,
                    suggested_contributor_legacy_id=int(row["suggested_contribuinte_id"] or 0) or None,
                    resolved_contributor_legacy_id=int(row["resolved_contribuinte_id"] or 0) or None,
                    review_status=row["review_status"] or "",
                    review_notes=row["review_notes"] or "",
                    imported_contribution_legacy_id=int(row["imported_contribution_id"] or 0) or None,
                    duplicate_movement_legacy_id=int(row["duplicate_movement_id"] or 0) or None,
                    duplicate_contribution_legacy_id=int(row["duplicate_contribution_id"] or 0) or None,
                    duplicate_reason=row["duplicate_reason"] or "",
                    fingerprint=row["fingerprint"] or "",
                    signature_global=row["signature_global"] or "",
                    prefix=row["prefixo_historico"] or "",
                    raw_text=row["raw_text"] or "",
                    metadata={
                        "suggested_person_name": row["suggested_person_name"] or "",
                        "suggested_person_cpf": row["suggested_person_cpf"] or "",
                        "resolved_person_name": row["resolved_person_name"] or "",
                        "resolved_person_cpf": row["resolved_person_cpf"] or "",
                        "suggested_contributor_name": row["suggested_contributor_name"] or "",
                        "resolved_contributor_name": row["resolved_contributor_name"] or "",
                        "tipo_sugerido": row["tipo_sugerido"] or "",
                    },
                )
                for row in movement_rows
            ],
            ignore_conflicts=True,
        )
    finally:
        conn.close()
    return pilot_lot


def persist_statement_pilot_from_clone(
    clone_db_path: Path,
    lot_id: int,
    *,
    source_backend: str = StatementImportPilotLot.SourceBackend.LEGACY_CLONE,
    pdf_provider: str = "",
    comparison_ok: bool = False,
    comparison_note: str = "",
    report_path: str = "",
) -> StatementImportPilotLot:
    return _persist_statement_pilot_from_sqlite(
        clone_db_path,
        lot_id,
        source_backend=source_backend,
        pdf_provider=pdf_provider,
        comparison_ok=comparison_ok,
        comparison_note=comparison_note,
        report_path=report_path,
    )


def persist_statement_pilot_from_legacy(
    lot_id: int,
    *,
    source_backend: str = StatementImportPilotLot.SourceBackend.DJANGO_WEB,
    comparison_ok: bool = True,
    comparison_note: str = "Snapshot do lote operacional sincronizado para leitura em Postgres.",
    report_path: str = "",
) -> StatementImportPilotLot:
    return _persist_statement_pilot_from_sqlite(
        legacy_db_path(),
        lot_id,
        source_backend=source_backend,
        pdf_provider="",
        comparison_ok=comparison_ok,
        comparison_note=comparison_note,
        report_path=report_path,
    )


def sync_statement_lot_snapshot_from_legacy(lot_id: int) -> StatementImportPilotLot:
    return persist_statement_pilot_from_legacy(lot_id)


def _format_snapshot_lot_row(pilot_lot: StatementImportPilotLot) -> dict[str, object]:
    metadata = pilot_lot.metadata or {}
    period = ""
    if pilot_lot.period_start or pilot_lot.period_end:
        start = br_date(pilot_lot.period_start.isoformat()) if pilot_lot.period_start else ""
        end = br_date(pilot_lot.period_end.isoformat()) if pilot_lot.period_end else ""
        period = f"{start} a {end}".strip()
    legacy_created_at = str(metadata.get("legacy_created_at") or "")
    return {
        "id": int(pilot_lot.source_lot_id or 0),
        "tipo": "Extrato",
        "banco": pilot_lot.bank_name or "",
        "layout": pilot_lot.layout_code or "",
        "nome_arquivo": pilot_lot.file_name or "",
        "periodo": period or "Sem periodo",
        "movimentos": int(pilot_lot.movement_count or 0),
        "total_fmt": _money(pilot_lot.total_value),
        "status": pilot_lot.lot_status or "",
        "pendentes": int(metadata.get("pending_human_count") or 0),
        "ignorados": int(metadata.get("ignored_count") or 0),
        "criado_em": br_datetime(legacy_created_at),
        "criado_em_raw": legacy_created_at or pilot_lot.updated_at.isoformat(),
        "snapshot_backend": pilot_lot.source_backend,
    }


def overlay_statement_lot_snapshots(lots_data: dict[str, object]) -> dict[str, object]:
    items = list(lots_data.get("items") or [])
    statement_ids = [int(item["id"]) for item in items if item.get("tipo") == "Extrato"]
    if not statement_ids:
        return lots_data
    snapshots = {
        int(item.source_lot_id or 0): item
        for item in StatementImportPilotLot.objects.filter(
            source_backend=StatementImportPilotLot.SourceBackend.DJANGO_WEB,
            source_lot_id__in=statement_ids,
        )
    }
    merged: list[dict[str, object]] = []
    for item in items:
        source_lot_id = int(item.get("id") or 0)
        snapshot = snapshots.get(source_lot_id)
        if item.get("tipo") == "Extrato" and snapshot is not None:
            merged.append(_format_snapshot_lot_row(snapshot))
        else:
            merged.append(item)
    return {**lots_data, "items": merged}


def get_statement_lot_detail_from_snapshot(lot_id: int, status: str = "", limit: int = 500) -> dict[str, object] | None:
    pilot_lot = (
        StatementImportPilotLot.objects.filter(
            source_backend=StatementImportPilotLot.SourceBackend.DJANGO_WEB,
            source_lot_id=lot_id,
        )
        .order_by("-updated_at", "-id")
        .first()
    )
    if pilot_lot is None:
        return None
    human_pending_filter = (
        Q(review_status__in=["pendente", "revisar_pessoa", "revisar_destinacao", "classificacao_pendente"])
        | (Q(review_status="revisar_duplicidade") & Q(imported_contribution_legacy_id__isnull=True))
    )
    movement_qs = pilot_lot.movements.all().order_by("movement_date", "order_in_lot", "id")
    if status == "pendencias":
        movement_qs = movement_qs.filter(human_pending_filter)
    elif status:
        movement_qs = movement_qs.filter(review_status=status)
    rows = list(movement_qs[:limit])
    review_counts = {
        (row["review_status"] or ""): int(row["total"] or 0)
        for row in pilot_lot.movements.values("review_status").annotate(total=Count("id")).order_by("-total", "review_status")
    }
    pending_count = pilot_lot.movements.filter(human_pending_filter).count()
    status_options = [{"value": "pendencias", "count": pending_count}]
    status_options.extend({"value": key, "count": value} for key, value in review_counts.items())
    formatted_movements: list[dict[str, object]] = []
    for movement in rows:
        meta = movement.metadata or {}
        resolved_person = str(meta.get("resolved_person_name") or "")
        suggested_person = str(meta.get("suggested_person_name") or "")
        resolved_contributor = str(meta.get("resolved_contributor_name") or "")
        suggested_contributor = str(meta.get("suggested_contributor_name") or "")
        candidate_person_id = movement.resolved_person_legacy_id or movement.suggested_person_legacy_id
        candidate_person_cpf = str(meta.get("resolved_person_cpf") or meta.get("suggested_person_cpf") or "")
        bank_document = movement.bank_document or ""
        bank_document_fmt = format_document(bank_document) if bank_document else ""
        candidate_document_fmt = format_document(candidate_person_cpf) if candidate_person_cpf else ""
        bank_document_digits = document_digits(bank_document)
        candidate_document_digits = document_digits(candidate_person_cpf)
        bank_document_invalid = len(bank_document_digits) == 11 and not valid_cpf(bank_document_digits)
        candidate_document_invalid = len(candidate_document_digits) == 11 and not valid_cpf(candidate_document_digits)
        document_match = ""
        document_match_label = ""
        if bank_document and candidate_person_cpf:
            matches = document_query_matches(bank_document, candidate_person_cpf)
            document_match = "ok" if matches and not (bank_document_invalid or candidate_document_invalid) else "warn"
            if matches and (bank_document_invalid or candidate_document_invalid):
                document_match_label = "confere, CPF invalido"
            else:
                document_match_label = "confere" if matches else "conferir"
        legacy_movement_id = int(movement.source_movement_id or movement.id)
        detail_url = f"/imports/statement/movement/{legacy_movement_id}/"
        formatted_movements.append(
            {
                "id": legacy_movement_id,
                "detail_url": detail_url,
                "ordem": movement.order_in_lot,
                "data": br_date(movement.movement_date.isoformat() if movement.movement_date else ""),
                "competencia": movement.competence or "",
                "valor_fmt": _money(movement.amount),
                "nome_origem": movement.source_name or "Sem remetente",
                "documento": bank_document,
                "documento_fmt": bank_document_fmt,
                "documento_tipo": movement.movement_kind or "",
                "candidate_person_id": candidate_person_id,
                "candidate_person_cpf": candidate_person_cpf,
                "candidate_document_fmt": candidate_document_fmt,
                "bank_document_invalid": bank_document_invalid,
                "candidate_document_invalid": candidate_document_invalid,
                "document_match": document_match,
                "document_match_label": document_match_label,
                "confidence": movement.confidence or "",
                "match_score": movement.match_score or 0,
                "review_status": movement.review_status or "",
                "tipo_sugerido": str(meta.get("tipo_sugerido") or ""),
                "codigo_centavos": movement.cent_code or "",
                "resolved_person": resolved_person,
                "suggested_person": suggested_person,
                "resolved_contributor": resolved_contributor,
                "suggested_contributor": suggested_contributor,
                "resolved": resolved_person or resolved_contributor,
                "suggested": suggested_person or suggested_contributor,
                "imported_contribution_id": movement.imported_contribution_legacy_id,
            }
        )
    if status:
        from urllib.parse import quote

        return_to = quote(f"/imports/statement/{lot_id}/?status={status}", safe="")
        for movement in formatted_movements:
            movement["detail_url"] = f"{movement['detail_url']}?return_to={return_to}"
    return {
        "kind": "statement",
        "kind_label": "Extrato",
        "lot": _format_snapshot_lot_row(pilot_lot),
        "status": status,
        "status_options": status_options,
        "movements": formatted_movements,
        "shown": len(formatted_movements),
        "limit": limit,
        "snapshot_backend": pilot_lot.source_backend,
    }


def get_statement_movement_detail_from_snapshot(movement_id: int, lookup: str = "") -> dict[str, object] | None:
    pilot_movement = (
        StatementImportPilotMovement.objects.select_related("lot")
        .filter(
            lot__source_backend=StatementImportPilotLot.SourceBackend.DJANGO_WEB,
            source_movement_id=movement_id,
        )
        .order_by("-lot__updated_at", "-lot_id", "-id")
        .first()
    )
    if pilot_movement is None:
        return None
    detail = get_bank_movement_detail("statement", movement_id, lookup=lookup)
    if detail is None:
        return None
    meta = pilot_movement.metadata or {}
    detail["movement"].update(
        {
            "id": int(pilot_movement.source_movement_id or movement_id),
            "lote_id": int(pilot_movement.lot.source_lot_id or 0),
            "banco": pilot_movement.lot.bank_name or "",
            "nome_arquivo": pilot_movement.lot.file_name or "",
            "ordem": pilot_movement.order_in_lot,
            "pagina": pilot_movement.page_number or "",
            "data": br_date(pilot_movement.movement_date.isoformat() if pilot_movement.movement_date else ""),
            "competencia": pilot_movement.competence or "",
            "valor_fmt": _money(pilot_movement.amount),
            "nome_origem": pilot_movement.source_name or "Sem remetente",
            "documento": pilot_movement.bank_document or "",
            "documento_tipo": pilot_movement.movement_kind or "",
            "confidence": pilot_movement.confidence or "",
            "match_score": pilot_movement.match_score or 0,
            "review_status": pilot_movement.review_status or "",
            "tipo_sugerido": str(meta.get("tipo_sugerido") or ""),
            "codigo_centavos": pilot_movement.cent_code or "",
            "suggested_person_id": pilot_movement.suggested_person_legacy_id or 0,
            "resolved_person_id": pilot_movement.resolved_person_legacy_id or 0,
            "suggested": str(meta.get("suggested_person_name") or meta.get("suggested_contributor_name") or ""),
            "resolved": str(meta.get("resolved_person_name") or meta.get("resolved_contributor_name") or ""),
            "contribution_id": pilot_movement.imported_contribution_legacy_id or "",
            "raw_text": pilot_movement.raw_text or "",
        }
    )
    detail["snapshot_backend"] = pilot_movement.lot.source_backend
    return detail

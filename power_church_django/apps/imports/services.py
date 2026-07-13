from __future__ import annotations

from collections import defaultdict
import os
from contextlib import contextmanager
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from pathlib import Path
import sqlite3

from django.db import models, transaction
from django.db.models import Count, Q

from power_church_core.bank_lots import StatementEntryPlan, statement_entry_plan
from power_church_core.banking import statement_contributor_name_for_identity, statement_layout_contributor_source
from power_church_core.bank_parsers import parse_statement_pdf_by_layout, statement_should_skip_entry
from power_church_core.contributors import contributor_kind_for_identity
from power_church_core.formatting import br_date, br_datetime
from power_church_core.normalization import (
    document_digits,
    document_query_matches,
    format_cpf,
    format_document,
    moneyless_int,
    normalize_match_name,
    normalize_query,
    santander_document_type,
    valid_cpf,
)
from power_church_django.services.runtime_formatting import _money, format_status
from power_church_django.apps.contributions.models import ContributionTypeSnapshot, NativeAuxContributor, NativeContribution, NativeEnvelope
from power_church_django.apps.people.models import (
    PersonAddressSnapshot,
    PersonContactSnapshot,
    PersonContributionSnapshot,
    PersonContributorSnapshot,
    PersonRelationshipSnapshot,
    PersonSnapshot,
)
from power_church_django.services.contributions_native import (
    _catalogs_for_org,
    _native_contributor_id_for_person,
    _next_native_contribution_public_id,
    _resolve_native_aux_contributor,
    _selected_option_name,
    _sync_person_contribution_snapshot,
)
from power_church_django.services.legacy import broad_family_candidates_summary, organized_family_nuclei_summary
from power_church_django.services.receipt_delivery import (
    schedule_automatic_receipts_for_events,
    summarize_automatic_receipt_outcomes,
)

from .models import CentRuleSnapshot, StatementImportPilotLot, StatementImportPilotMovement


class LegacyBankWriteError(RuntimeError):
    """Compatibilidade local do fluxo de importacao nativo."""


PDF_PROVIDER_MODES = [
    {
        "value": "swift_pdfkit",
        "label": "Homologado atual (Swift/PDFKit)",
        "description": "Mantem o leitor atual do Mac enquanto validamos a portabilidade.",
    },
    {
        "value": "compare_pymupdf",
        "label": "Comparar Swift x PyMuPDF antes de gravar",
        "description": "Chave segura: se houver divergencia, o lote nao e gravado.",
    },
    {
        "value": "pymupdf",
        "label": "PyMuPDF direto",
        "description": "Leitor portavel para Linux/servidor, apos homologacao do banco.",
    },
]


@contextmanager
def _temporary_pdf_provider(provider: str) -> object:
    previous = os.environ.get("POWER_CHURCH_PDF_PROVIDER")
    os.environ["POWER_CHURCH_PDF_PROVIDER"] = provider
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("POWER_CHURCH_PDF_PROVIDER", None)
        else:
            os.environ["POWER_CHURCH_PDF_PROVIDER"] = previous


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


def _merge_notes(*parts: object) -> str:
    normalized: list[str] = []
    seen: set[str] = set()
    for part in parts:
        text = normalize_query(part)
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return "\n".join(normalized)


def _statement_human_pending_filter() -> Q:
    return Q(review_status__in=["pendente", "revisar_pessoa", "revisar_destinacao", "classificacao_pendente"]) | (
        Q(review_status="revisar_duplicidade") & Q(imported_contribution_legacy_id__isnull=True)
    )


def _scalar(conn: sqlite3.Connection, sql: str, params: tuple[object, ...] = ()) -> object:
    row = conn.execute(sql, params).fetchone()
    return row[0] if row else None


def _default_contribution_type_organization_id() -> int:
    snapshot = (
        ContributionTypeSnapshot.objects.filter(is_active=True)
        .only("organization_id", "legacy_id")
        .order_by("organization_id", "legacy_id")
        .first()
    )
    return int(snapshot.organization_id or 0) if snapshot is not None else 0


def create_statement_lot_postgres_native(
    *,
    filename: str,
    payload: bytes,
    layout_code: str,
    pdf_provider: str = "pymupdf",
    comparison_ok: bool = True,
    comparison_note: str = "Lote criado diretamente no Postgres nativo.",
    report_path: str = "",
) -> StatementImportPilotLot:
    if payload:
        from tempfile import NamedTemporaryFile

        suffix = Path(filename).suffix or ".pdf"
        with NamedTemporaryFile(prefix="power_church_native_", suffix=suffix, delete=False) as handle:
            handle.write(payload)
            tmp_path = Path(handle.name)
        try:
            with _temporary_pdf_provider(pdf_provider):
                parsed = plan_statement_import(layout_code, tmp_path)
        finally:
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass
    else:
        with _temporary_pdf_provider(pdf_provider):
            parsed = plan_statement_import(layout_code, Path(filename))
    entries = list(parsed.get("entries") or [])
    file_hash = str(parsed.get("file_hash") or "")
    stored_layout = normalize_query(parsed.get("layout_code") or layout_code).upper() or layout_code
    organization_id = _default_contribution_type_organization_id()
    review_counts = {"pendente": len(entries)} if entries else {}
    pilot_lot, _ = StatementImportPilotLot.objects.update_or_create(
        reference_key=":".join(
            [
                StatementImportPilotLot.SourceBackend.POSTGRES_NATIVE,
                stored_layout,
                file_hash or normalize_query(filename) or "sem_hash",
            ]
        ),
        defaults={
            "source_backend": StatementImportPilotLot.SourceBackend.POSTGRES_NATIVE,
            "source_db_path": "",
            "source_lot_id": None,
            "bank_name": str(parsed.get("bank_name") or ""),
            "layout_code": stored_layout,
            "file_name": filename,
            "file_hash": file_hash,
            "period_start": _parse_iso_date(parsed.get("period_start")),
            "period_end": _parse_iso_date(parsed.get("period_end")),
            "movement_count": len(entries),
            "total_value": Decimal(str(sum(Decimal(str(item.get("amount") or 0)) for item in entries) if entries else 0)),
            "lot_status": "pendente",
            "pdf_provider": pdf_provider,
            "comparison_ok": bool(comparison_ok),
            "comparison_note": comparison_note or "",
            "report_path": report_path or "",
            "metadata": {
                "review_counts": review_counts,
                "imported_count": 0,
                "ignored_count": 0,
                "pending_human_count": len(entries),
                "observacoes": "",
                "native_origin": "create_statement_lot_postgres_native",
            },
        },
    )
    StatementImportPilotMovement.objects.filter(lot=pilot_lot).delete()
    StatementImportPilotMovement.objects.bulk_create(
        [
            StatementImportPilotMovement(
                lot=pilot_lot,
                source_movement_id=None,
                page_number=int(item.get("page_number") or 1),
                order_in_lot=int(item.get("order_in_file") or index),
                movement_date=_parse_iso_date(item.get("received_on")),
                competence=normalize_query(item.get("competence")),
                competence_order=int(item.get("competence_order") or 0),
                amount=Decimal(str(item.get("amount") or 0)),
                cent_code=normalize_query(item.get("cent_code")),
                movement_kind=normalize_query(item.get("movement_kind")),
                receiving_code=normalize_query(item.get("receiving_code")),
                bank_document=normalize_query(item.get("bank_document")),
                document_type=normalize_query(item.get("document_type")),
                prefix=normalize_query(item.get("prefix")),
                source_name=normalize_query(item.get("source_name")),
                source_name_normalized=normalize_match_name(item.get("source_name")),
                origin_label=normalize_query(item.get("origin_label")),
                confidence="",
                match_score=Decimal("0"),
                review_status="pendente",
                review_notes="",
                duplicate_reason="",
                fingerprint="",
                signature_global="",
                raw_text=normalize_query(item.get("raw_text")),
                metadata={
                    "tipo_sugerido": normalize_query(item.get("suggested_type_name")),
                    "organizacao_id": organization_id,
                    "regra_id": 0,
                    "resolved_tipo_contribuicao_id": 0,
                    "contribution_type_id": 0,
                    "rule_type_id": 0,
                    "parsed_entry": item,
                },
            )
            for index, item in enumerate(entries, start=1)
        ],
        batch_size=500,
    )
    return pilot_lot


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
    from power_church_django.services.legacy import table_exists
    from power_church_django.services.runtime_formatting import human_pending_review_sql

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
            SELECT m.id, m.organizacao_id, m.regra_id, m.resolved_tipo_contribuicao_id,
                   m.pagina, m.ordem_no_lote, m.data_movimento, m.competencia, m.competencia_ordem, m.valor,
                   m.codigo_centavos, m.movement_kind, m.receiving_code, m.bank_document, m.prefixo_historico, m.tipo_sugerido,
                   m.nome_origem, m.nome_normalizado, m.origin_label, m.confidence, m.match_score,
                   m.suggested_person_id, m.resolved_person_id, m.suggested_contribuinte_id, m.resolved_contribuinte_id,
                   sp.nome AS suggested_person_name, sp.cpf AS suggested_person_cpf,
                   rp.nome AS resolved_person_name, rp.cpf AS resolved_person_cpf,
                   sc.nome AS suggested_contributor_name, rc.nome AS resolved_contributor_name,
                   m.review_status, m.review_notes, m.imported_contribution_id, m.duplicate_movement_id,
                   m.duplicate_contribution_id, m.duplicate_reason, m.fingerprint, m.signature_global, m.raw_text,
                   co.tipo_contribuicao_id AS contribution_type_id
              FROM extrato_movimentos m
              LEFT JOIN pessoas sp ON sp.id = m.suggested_person_id
              LEFT JOIN pessoas rp ON rp.id = m.resolved_person_id
              LEFT JOIN contribuintes sc ON sc.id = m.suggested_contribuinte_id
              LEFT JOIN contribuintes rc ON rc.id = m.resolved_contribuinte_id
              LEFT JOIN contribuicoes co ON co.id = m.imported_contribution_id
             WHERE m.lote_id = ? AND m.ativo = 1
             ORDER BY m.ordem_no_lote ASC, m.id ASC
            """,
            (lot_id,),
        ).fetchall()
        rule_type_by_id: dict[int, int] = {}
        if table_exists(conn, "pix_centavo_regras"):
            rule_type_by_id = {
                int(row["id"] or 0): int(row["tipo_contribuicao_id"] or 0)
                for row in conn.execute("SELECT id, tipo_contribuicao_id FROM pix_centavo_regras").fetchall()
            }
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
                        "organizacao_id": int(row["organizacao_id"] or 0),
                        "regra_id": int(row["regra_id"] or 0),
                        "resolved_tipo_contribuicao_id": int(row["resolved_tipo_contribuicao_id"] or 0),
                        "contribution_type_id": int(row["contribution_type_id"] or 0),
                        "rule_type_id": int(rule_type_by_id.get(int(row["regra_id"] or 0), 0) or 0),
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
    from power_church_django.services.legacy import legacy_db_path

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
    public_lot_id = (
        int(pilot_lot.source_lot_id or 0)
        if pilot_lot.source_backend != StatementImportPilotLot.SourceBackend.POSTGRES_NATIVE
        else int(pilot_lot.id or 0)
    )
    return {
        "id": public_lot_id,
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
    native_items = [
        _format_snapshot_lot_row(item)
        for item in StatementImportPilotLot.objects.filter(
            source_backend=StatementImportPilotLot.SourceBackend.POSTGRES_NATIVE
        ).order_by("-created_at", "-id")
    ]
    combined = native_items + merged
    return {
        **lots_data,
        "items": combined,
        "shown": len(combined),
        "total": max(int(lots_data.get("total") or 0), len(combined)),
    }


def get_statement_lot_detail_from_snapshot(
    lot_id: int,
    status: str = "",
    limit: int = 500,
    backend: str = "auto",
) -> dict[str, object] | None:
    backend = normalize_query(backend).lower() or "auto"
    queryset = StatementImportPilotLot.objects.all()
    if backend == StatementImportPilotLot.SourceBackend.POSTGRES_NATIVE:
        pilot_lot = queryset.filter(
            source_backend=StatementImportPilotLot.SourceBackend.POSTGRES_NATIVE,
            id=lot_id,
        ).order_by("-updated_at", "-id").first()
    elif backend == StatementImportPilotLot.SourceBackend.DJANGO_WEB:
        pilot_lot = queryset.filter(
            source_backend=StatementImportPilotLot.SourceBackend.DJANGO_WEB,
            source_lot_id=lot_id,
        ).order_by("-updated_at", "-id").first()
    else:
        pilot_lot = queryset.filter(
            Q(source_backend=StatementImportPilotLot.SourceBackend.DJANGO_WEB, source_lot_id=lot_id)
            | Q(source_backend=StatementImportPilotLot.SourceBackend.POSTGRES_NATIVE, id=lot_id)
        ).order_by("-updated_at", "-id").first()
    if pilot_lot is None:
        return None
    human_pending_filter = _statement_human_pending_filter()
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
        public_movement_id = int(movement.source_movement_id or movement.id)
        detail_url = f"/imports/statement/movement/{public_movement_id}/"
        if pilot_lot.source_backend == StatementImportPilotLot.SourceBackend.POSTGRES_NATIVE:
            detail_url = f"{detail_url}?backend={StatementImportPilotLot.SourceBackend.POSTGRES_NATIVE}"
        formatted_movements.append(
            {
                "id": public_movement_id,
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

        backend_query = (
            f"&backend={StatementImportPilotLot.SourceBackend.POSTGRES_NATIVE}"
            if pilot_lot.source_backend == StatementImportPilotLot.SourceBackend.POSTGRES_NATIVE
            else ""
        )
        return_to = quote(f"/imports/statement/{lot_id}/?status={status}{backend_query}", safe="")
        for movement in formatted_movements:
            separator = "&" if "?" in str(movement["detail_url"]) else "?"
            movement["detail_url"] = f"{movement['detail_url']}{separator}return_to={return_to}"
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


def get_statement_movement_detail_from_snapshot(
    movement_id: int,
    lookup: str = "",
    backend: str = "auto",
) -> dict[str, object] | None:
    backend = normalize_query(backend).lower() or "auto"
    queryset = StatementImportPilotMovement.objects.select_related("lot")
    if backend == StatementImportPilotLot.SourceBackend.POSTGRES_NATIVE:
        pilot_movement = queryset.filter(
            lot__source_backend=StatementImportPilotLot.SourceBackend.POSTGRES_NATIVE,
            id=movement_id,
        ).order_by("-lot__updated_at", "-lot_id", "-id").first()
    elif backend == StatementImportPilotLot.SourceBackend.DJANGO_WEB:
        pilot_movement = queryset.filter(
            lot__source_backend=StatementImportPilotLot.SourceBackend.DJANGO_WEB,
            source_movement_id=movement_id,
        ).order_by("-lot__updated_at", "-lot_id", "-id").first()
    else:
        pilot_movement = queryset.filter(
            Q(lot__source_backend=StatementImportPilotLot.SourceBackend.DJANGO_WEB, source_movement_id=movement_id)
            | Q(lot__source_backend=StatementImportPilotLot.SourceBackend.POSTGRES_NATIVE, id=movement_id)
        ).order_by("-lot__updated_at", "-lot_id", "-id").first()
    if pilot_movement is None:
        return None
    meta = pilot_movement.metadata or {}
    selected_person_id = (
        moneyless_int(pilot_movement.resolved_person_legacy_id)
        or (
            0
            if bool(meta.get("association_reviewed"))
            else moneyless_int(pilot_movement.suggested_person_legacy_id)
        )
    )
    selected_type_id = _statement_selected_type_id(pilot_movement) or _statement_default_type_id(pilot_movement)
    return {
        "kind": "statement",
        "kind_label": "Extrato",
        "lot_url": (
            f"/imports/statement/{int(pilot_movement.lot.id or 0)}/?backend={StatementImportPilotLot.SourceBackend.POSTGRES_NATIVE}"
            if pilot_movement.lot.source_backend == StatementImportPilotLot.SourceBackend.POSTGRES_NATIVE
            else f"/imports/statement/{int(pilot_movement.lot.source_lot_id or 0)}/"
        ),
        "movement": {
            "id": int(pilot_movement.source_movement_id or movement_id),
            "lote_id": _statement_resolved_lot_id(pilot_movement.lot),
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
            "selected_person_id": selected_person_id,
            "selected_type_id": selected_type_id,
            "suggested_person_id": moneyless_int(pilot_movement.suggested_person_legacy_id),
            "resolved_person_id": moneyless_int(pilot_movement.resolved_person_legacy_id),
            "association_reviewed": bool(meta.get("association_reviewed")),
            "suggested": str(meta.get("suggested_person_name") or meta.get("suggested_contributor_name") or ""),
            "resolved": str(meta.get("resolved_person_name") or meta.get("resolved_contributor_name") or ""),
            "contribution_id": pilot_movement.imported_contribution_legacy_id or "",
            "contribution_status": _statement_contribution_status(pilot_movement.imported_contribution_legacy_id),
            "raw_text": pilot_movement.raw_text or "",
            "review_notes": pilot_movement.review_notes or "",
        },
        "lookup": normalize_query(lookup),
        "person_options": _statement_person_options(pilot_movement, selected_person_id, lookup),
        "type_options": _statement_type_options(pilot_movement, selected_type_id),
        "can_same_owner": True,
        "snapshot_backend": pilot_movement.lot.source_backend,
    }


def _statement_selected_type_id(pilot_movement: StatementImportPilotMovement) -> int:
    meta = pilot_movement.metadata or {}
    return (
        moneyless_int(meta.get("resolved_tipo_contribuicao_id"))
        or moneyless_int(meta.get("contribution_type_id"))
        or moneyless_int(meta.get("rule_type_id"))
    )


def _statement_dizimo_type_id(organization_id: int) -> int:
    if not int(organization_id or 0):
        return 0
    row = (
        ContributionTypeSnapshot.objects.filter(
            organization_id=int(organization_id or 0),
            is_active=True,
        )
        .filter(Q(code__iexact="dizimo") | Q(name__iexact="dizimo"))
        .only("legacy_id")
        .order_by("legacy_id")
        .first()
    )
    return int(row.legacy_id or 0) if row is not None else 0


def _statement_default_type_id(pilot_movement: StatementImportPilotMovement) -> int:
    current = _statement_selected_type_id(pilot_movement)
    if current:
        return current
    cent_code = normalize_query(pilot_movement.cent_code)
    organization_id = moneyless_int((pilot_movement.metadata or {}).get("organizacao_id"))
    if cent_code and organization_id:
        rule = (
            CentRuleSnapshot.objects.filter(
                organization_id=organization_id,
                cent_code=cent_code.zfill(2),
                is_active=True,
            )
            .only("contribution_type_legacy_id")
            .order_by("legacy_id")
            .first()
        )
        if rule and int(rule.contribution_type_legacy_id or 0):
            return int(rule.contribution_type_legacy_id or 0)
        dizimo_id = _statement_dizimo_type_id(organization_id)
        if dizimo_id:
            return dizimo_id
    meta = pilot_movement.metadata or {}
    suggested_name = normalize_query(meta.get("tipo_sugerido"))
    if suggested_name:
        type_row = (
            ContributionTypeSnapshot.objects.filter(is_active=True)
            .filter(name__iexact=suggested_name)
            .only("legacy_id")
            .order_by("legacy_id")
            .first()
        )
        if type_row is not None:
            return int(type_row.legacy_id or 0)
    return _statement_dizimo_type_id(organization_id)


def _statement_contribution_status(contribution_legacy_id: int | None) -> str:
    if not contribution_legacy_id:
        return ""
    contribution = (
        PersonContributionSnapshot.objects.filter(legacy_id=contribution_legacy_id)
        .only("operational_status")
        .first()
    )
    return contribution.operational_status if contribution else ""


def _statement_person_option(person: PersonSnapshot, *, source: str, selected_person_id: int) -> dict[str, object]:
    return {
        "id": int(person.legacy_id or 0),
        "nome": person.name or "",
        "codigo": person.internal_code or "",
        "cpf": format_cpf(person.cpf),
        "status": format_status(person.status),
        "source": source,
        "checked": int(person.legacy_id or 0) == selected_person_id,
    }


def _statement_person_options(
    pilot_movement: StatementImportPilotMovement,
    selected_person_id: int,
    lookup: str,
) -> list[dict[str, object]]:
    options: list[dict[str, object]] = []
    seen: set[int] = set()

    def add_person(person_id: int, source: str) -> None:
        if not person_id or person_id in seen:
            return
        person = (
            PersonSnapshot.objects.filter(legacy_id=person_id)
            .only("legacy_id", "internal_code", "name", "cpf", "status")
            .first()
        )
        if person is not None:
            seen.add(person_id)
            options.append(_statement_person_option(person, source=source, selected_person_id=selected_person_id))

    add_person(moneyless_int(pilot_movement.resolved_person_legacy_id), "ja resolvido")
    add_person(moneyless_int(pilot_movement.suggested_person_legacy_id), "sugerido pelo motor")

    clean_lookup = normalize_query(lookup)
    if clean_lookup:
        normalized_lookup = normalize_match_name(clean_lookup)
        digits = "".join(ch for ch in clean_lookup if ch.isdigit())
        person_query = (
            Q(normalized_name__icontains=normalized_lookup)
            | Q(internal_code__icontains=clean_lookup)
            | Q(cpf__icontains=digits or clean_lookup)
            | Q(normalized_email__icontains=clean_lookup)
            | Q(primary_phone__icontains=digits or clean_lookup)
            | Q(primary_whatsapp__icontains=digits or clean_lookup)
        )
        contact_person_ids = list(
            PersonContactSnapshot.objects.filter(
                Q(normalized_value__icontains=normalized_lookup)
                | Q(value__icontains=digits or clean_lookup)
            )
            .values_list("person_id", flat=True)[:80]
        )
        if contact_person_ids:
            person_query = person_query | Q(id__in=contact_person_ids)
        people = (
            PersonSnapshot.objects.filter(person_query)
            .only("legacy_id", "internal_code", "name", "cpf", "status")
            .order_by("normalized_name", "legacy_id")[:80]
        )
        for person in people:
            person_id = int(person.legacy_id or 0)
            if person_id not in seen:
                seen.add(person_id)
                options.append(_statement_person_option(person, source="busca ampla", selected_person_id=selected_person_id))
    return options


def _statement_type_options(
    pilot_movement: StatementImportPilotMovement,
    selected_type_id: int,
) -> list[dict[str, object]]:
    meta = pilot_movement.metadata or {}
    organization_id = moneyless_int(meta.get("organizacao_id"))
    if not organization_id:
        return []
    rows = list(
        ContributionTypeSnapshot.objects.filter(
            organization_id=organization_id,
            is_active=True,
        )
        .only("legacy_id", "code", "name")
    )
    rows.sort(key=lambda row: (0 if normalize_query(row.code) == "dizimo" else 1, normalize_query(row.name), int(row.legacy_id or 0)))
    return [
        {
            "id": int(row.legacy_id or 0),
            "codigo": row.code or "",
            "nome": row.name or "",
            "selected": int(row.legacy_id or 0) == selected_type_id,
        }
        for row in rows
    ]


def _statement_resolved_lot_id(pilot_lot: StatementImportPilotLot) -> int:
    if pilot_lot.source_backend == StatementImportPilotLot.SourceBackend.POSTGRES_NATIVE:
        return int(pilot_lot.id or 0)
    return int(pilot_lot.source_lot_id or 0)


def _native_statement_candidate_people(
    pilot_movement: StatementImportPilotMovement,
) -> tuple[list[PersonSnapshot], str]:
    document = document_digits(pilot_movement.bank_document or "")
    if len(document) == 11:
        people = list(
            PersonSnapshot.objects.filter(cpf=document, is_active=True)
            .only("legacy_id", "name", "internal_code", "cpf", "status")
            .order_by("legacy_id")[:5]
        )
        if people:
            return people, "forte_doc"
    normalized_name = normalize_query(pilot_movement.source_name_normalized or pilot_movement.source_name)
    if normalized_name:
        people = list(
            PersonSnapshot.objects.filter(normalized_name=normalized_name, is_active=True)
            .only("legacy_id", "name", "internal_code", "cpf", "status")
            .order_by("legacy_id")[:5]
        )
        if people:
            return people, "forte_nome"
        people = list(
            PersonSnapshot.objects.filter(normalized_name__startswith=normalized_name[:32], is_active=True)
            .only("legacy_id", "name", "internal_code", "cpf", "status")
            .order_by("legacy_id")[:5]
        )
        if people:
            return people, "nome_parcial"
    return [], ""


def _sync_statement_person_metadata(
    pilot_movement: StatementImportPilotMovement,
    *,
    suggested_person: PersonSnapshot | None = None,
    resolved_person: PersonSnapshot | None = None,
) -> dict[str, object]:
    meta = dict(pilot_movement.metadata or {})
    meta["suggested_person_name"] = suggested_person.name if suggested_person else ""
    meta["suggested_person_cpf"] = suggested_person.cpf if suggested_person else ""
    meta["resolved_person_name"] = resolved_person.name if resolved_person else ""
    meta["resolved_person_cpf"] = resolved_person.cpf if resolved_person else ""
    return meta


def _find_duplicate_contribution_for_native_statement(
    pilot_movement: StatementImportPilotMovement,
    person_legacy_id: int,
) -> PersonContributionSnapshot | None:
    queryset = PersonContributionSnapshot.objects.filter(
        person__legacy_id=person_legacy_id,
        is_active=True,
        amount=pilot_movement.amount,
    ).only("legacy_id", "received_at", "competence")
    if pilot_movement.movement_date:
        exact_day = queryset.filter(received_at=pilot_movement.movement_date).order_by("-legacy_id").first()
        if exact_day is not None:
            return exact_day
    return None


def _statement_native_review_status(
    pilot_movement: StatementImportPilotMovement,
    *,
    selected_person_id: int,
    selected_type_id: int,
    confidence: str,
) -> tuple[str, str, int | None]:
    association_reviewed = bool((pilot_movement.metadata or {}).get("association_reviewed"))
    if not selected_type_id:
        return "revisar_destinacao", confidence, None
    if association_reviewed:
        if selected_person_id:
            return "aprovado", confidence or "aprovado_manual", None
        return "aprovado", confidence or "sem_vinculo_manual", None
    if not selected_person_id:
        return "revisar_pessoa", confidence or "sem_vinculo", None
    duplicate = _find_duplicate_contribution_for_native_statement(pilot_movement, selected_person_id)
    if duplicate is not None:
        return "revisar_duplicidade", confidence or "duplicidade", int(duplicate.legacy_id or 0)
    return "pronto", confidence or "manual", None


def _apply_native_statement_resolution(
    pilot_movement: StatementImportPilotMovement,
    *,
    preserve_manual_selection: bool,
) -> StatementImportPilotMovement:
    meta = dict(pilot_movement.metadata or {})
    if pilot_movement.review_status == "ignorado":
        meta.setdefault("association_reviewed", True)
        pilot_movement.metadata = meta
        return pilot_movement

    selected_type_id = _statement_selected_type_id(pilot_movement) or _statement_default_type_id(pilot_movement)
    if selected_type_id:
        meta["resolved_tipo_contribuicao_id"] = int(selected_type_id)

    selected_person_id = moneyless_int(pilot_movement.resolved_person_legacy_id)
    confidence = pilot_movement.confidence or ""
    suggested_people: list[PersonSnapshot] = []
    association_reviewed = bool(meta.get("association_reviewed"))
    should_recalculate_suggestion = (not selected_person_id and not association_reviewed) or not preserve_manual_selection
    if should_recalculate_suggestion:
        suggested_people, suggested_confidence = _native_statement_candidate_people(pilot_movement)
        confidence = confidence or suggested_confidence
        first_person = suggested_people[0] if suggested_people else None
        pilot_movement.suggested_person_legacy_id = int(first_person.legacy_id or 0) if first_person else None
        if not selected_person_id and not association_reviewed and len(suggested_people) == 1:
            selected_person_id = int(first_person.legacy_id or 0)
            pilot_movement.resolved_person_legacy_id = selected_person_id
        elif not selected_person_id and not association_reviewed:
            pilot_movement.resolved_person_legacy_id = None
    elif not selected_person_id:
        pilot_movement.resolved_person_legacy_id = None
        suggested_person = None
        if moneyless_int(pilot_movement.suggested_person_legacy_id):
            suggested_person = (
                PersonSnapshot.objects.filter(legacy_id=int(pilot_movement.suggested_person_legacy_id or 0))
                .only("legacy_id", "name", "cpf")
                .first()
            )
            if suggested_person is not None:
                suggested_people = [suggested_person]

    suggested_person = suggested_people[0] if suggested_people else None
    resolved_person = None
    if selected_person_id:
        resolved_person = (
            PersonSnapshot.objects.filter(legacy_id=selected_person_id)
            .only("legacy_id", "name", "cpf")
            .first()
        )

    review_status, confidence, duplicate_contribution_id = _statement_native_review_status(
        pilot_movement,
        selected_person_id=selected_person_id,
        selected_type_id=selected_type_id,
        confidence=confidence,
    )
    pilot_movement.confidence = confidence
    pilot_movement.review_status = review_status
    pilot_movement.duplicate_contribution_legacy_id = duplicate_contribution_id
    pilot_movement.imported_contribution_legacy_id = None
    pilot_movement.metadata = _sync_statement_person_metadata(
        pilot_movement,
        suggested_person=suggested_person,
        resolved_person=resolved_person,
    )
    pilot_movement.metadata.update(meta)
    return pilot_movement


def _statement_contribution_person_id_for_movement(pilot_movement: StatementImportPilotMovement) -> int | None:
    resolved_person_id = moneyless_int(pilot_movement.resolved_person_legacy_id)
    if resolved_person_id:
        return resolved_person_id
    if bool((pilot_movement.metadata or {}).get("association_reviewed")):
        return None
    if normalize_query(pilot_movement.review_status) in {"pronto", "aprovado", "importado", "revisar_destinacao"}:
        suggested_person_id = moneyless_int(pilot_movement.suggested_person_legacy_id)
        return suggested_person_id or None
    return None


def _statement_contribution_status_for_movement(
    pilot_movement: StatementImportPilotMovement,
    person_id: int | None,
) -> str:
    review_status = normalize_query(pilot_movement.review_status)
    if review_status == "ignorado":
        return "ignorado"
    if review_status == "revisar_duplicidade":
        return "duplicidade_suspeita"
    if not int(person_id or 0):
        return "sem_associacao"
    if review_status == "revisar_destinacao":
        return "classificacao_pendente"
    return "regular"


def _statement_receipt_method_for_movement(
    pilot_movement: StatementImportPilotMovement,
    catalogs: dict[str, object],
) -> tuple[int | None, str]:
    receiving_code = normalize_query(pilot_movement.receiving_code).upper()
    options = list(catalogs.get("receiving_options") or [])
    token_groups: list[list[str]] = []
    if receiving_code == "PIX":
        token_groups = [["PIX"]]
    elif receiving_code in {"TRANSFERENCIA", "TED", "DOC"}:
        token_groups = [["TRANSFER"], ["TED"], ["DOC"]]
    elif receiving_code == "DEPOSITO":
        token_groups = [["DEPOS"]]
    elif receiving_code:
        token_groups = [[receiving_code]]
    for tokens in token_groups:
        for option in options:
            option_name = normalize_query(option.get("nome")).upper()
            if option_name and all(token in option_name for token in tokens):
                return int(option.get("id") or 0) or None, str(option.get("nome") or "")
    fallback_name = normalize_query(pilot_movement.receiving_code) or normalize_query(pilot_movement.movement_kind)
    return None, fallback_name


def _statement_campaign_for_movement(pilot_movement: StatementImportPilotMovement) -> tuple[int | None, str]:
    rule_id = moneyless_int((pilot_movement.metadata or {}).get("regra_id"))
    if not rule_id:
        return None, ""
    rule = (
        CentRuleSnapshot.objects.filter(legacy_id=rule_id)
        .only("campaign_legacy_id", "campaign_name")
        .first()
    )
    if rule is None:
        return None, ""
    return int(rule.campaign_legacy_id or 0) or None, rule.campaign_name or ""


def _statement_notes_for_movement(pilot_movement: StatementImportPilotMovement) -> str:
    parts = [
        f"Importado do lote de extrato #{_statement_resolved_lot_id(pilot_movement.lot)}",
        f"Banco: {pilot_movement.lot.bank_name or 'Extrato bancario'}",
        f"Tipo: {normalize_query(pilot_movement.movement_kind) or 'Sem tipo'}",
    ]
    if normalize_query(pilot_movement.source_name):
        parts.append(f"Origem: {pilot_movement.source_name}")
    elif normalize_query(pilot_movement.origin_label):
        parts.append(f"Origem: {pilot_movement.origin_label}")
    if normalize_query(pilot_movement.bank_document):
        parts.append(f"Docto: {pilot_movement.bank_document}")
    if normalize_query(pilot_movement.cent_code):
        parts.append(f"Centavos: {pilot_movement.cent_code}")
    if normalize_query(pilot_movement.review_notes):
        parts.append(pilot_movement.review_notes)
    return " | ".join(part for part in parts if normalize_query(part))


def _statement_sync_native_contribution_for_movement(
    pilot_movement: StatementImportPilotMovement,
    *,
    actor: str = "",
) -> int:
    meta = dict(pilot_movement.metadata or {})
    organization_id = moneyless_int(meta.get("organizacao_id")) or _default_contribution_type_organization_id()
    selected_type_id = _statement_selected_type_id(pilot_movement) or _statement_default_type_id(pilot_movement)
    catalogs = _catalogs_for_org(int(organization_id or 0), selected_type_id=int(selected_type_id or 0))
    type_name = _selected_option_name(catalogs["type_options"], int(selected_type_id or 0)) or str(meta.get("tipo_sugerido") or "")
    person_id = _statement_contribution_person_id_for_movement(pilot_movement)
    status = _statement_contribution_status_for_movement(pilot_movement, person_id)
    receipt_method_id, receipt_method_name = _statement_receipt_method_for_movement(pilot_movement, catalogs)
    campaign_id, campaign_name = _statement_campaign_for_movement(pilot_movement)
    source_label = statement_layout_contributor_source(pilot_movement.lot.layout_code)
    contribution_id = int(pilot_movement.imported_contribution_legacy_id or 0)
    contribution = (
        NativeContribution.objects.filter(legacy_id=contribution_id).first()
        if contribution_id
        else None
    )
    is_new = contribution is None
    if contribution is None:
        contribution_id = contribution_id or _next_native_contribution_public_id()
        contribution = NativeContribution(
            legacy_id=int(contribution_id or 0),
            organization_id=int(organization_id or 0),
        )

    contributor_id: int | None = None
    native_aux_contributor_id: int | None = None
    contributor_source = "postgres_native_statement"
    contributor_name = normalize_query(pilot_movement.source_name)
    contributor_document = normalize_query(pilot_movement.bank_document)
    contributor_type = contributor_kind_for_identity(
        contributor_name,
        document_type=normalize_query(pilot_movement.document_type) or santander_document_type(contributor_document),
        document_value=contributor_document,
    )
    if int(person_id or 0):
        person = (
            PersonSnapshot.objects.filter(legacy_id=int(person_id or 0))
            .only("legacy_id", "name", "cpf")
            .first()
        )
        if person is not None:
            contributor_id = _native_contributor_id_for_person(person)
            contributor_source = "person_snapshot"
            contributor_name = person.name or contributor_name
            contributor_document = person.cpf or contributor_document
            contributor_type = "pf"
    else:
        aux_name = statement_contributor_name_for_identity(
            pilot_movement.lot.layout_code,
            pilot_movement.source_name,
            pilot_movement.bank_document,
            pilot_movement.document_type,
        ) or contributor_name or contributor_document or "Contribuinte sem vinculo"
        aux = _resolve_native_aux_contributor(
            organization_id=int(organization_id or 0),
            legacy_contributor_id=moneyless_int(
                pilot_movement.resolved_contributor_legacy_id or pilot_movement.suggested_contributor_legacy_id
            ),
            name=aux_name,
            document=contributor_document,
            source=source_label,
        )
        linked_person_id = int(aux.person_legacy_id or 0) or None
        linked_person = None
        if linked_person_id:
            linked_person = (
                PersonSnapshot.objects.filter(legacy_id=linked_person_id)
                .only("legacy_id", "name", "cpf")
                .first()
            )
        if linked_person is not None:
            person_id = int(linked_person.legacy_id or 0)
            contributor_id = _native_contributor_id_for_person(linked_person)
            native_aux_contributor_id = None
            contributor_source = "person_snapshot"
            contributor_name = linked_person.name or aux.name or aux_name
            contributor_document = linked_person.cpf or aux.primary_document or contributor_document
            contributor_type = "pf"
        else:
            contributor_id = int(aux.legacy_reference_id or 0) or None
            native_aux_contributor_id = int(aux.pk or 0) or None
            contributor_source = "legacy_aux_contributor" if contributor_id else "native_aux_contributor"
            contributor_name = aux.name or aux_name
            contributor_document = aux.primary_document or contributor_document
            contributor_type = aux.contributor_type or contributor_type

    contribution.person_legacy_id = int(person_id or 0) or None
    contribution.contributor_legacy_id = int(contributor_id or 0) or None
    contribution.native_aux_contributor_id = int(native_aux_contributor_id or 0) or None
    contribution.contributor_source = contributor_source
    contribution.contributor_name = contributor_name or ""
    contribution.contributor_document = contributor_document or ""
    contribution.contributor_type = contributor_type or ""
    contribution.received_at = pilot_movement.movement_date
    contribution.received_at_raw = pilot_movement.movement_date.isoformat() if pilot_movement.movement_date else ""
    contribution.competence = pilot_movement.competence or ""
    contribution.competence_order = int(pilot_movement.competence_order or 0)
    contribution.amount = pilot_movement.amount
    contribution.contribution_type_legacy_id = int(selected_type_id or 0)
    contribution.contribution_type_name = type_name or "Sem tipo"
    contribution.campaign_legacy_id = int(campaign_id or 0) or None
    contribution.campaign_name = campaign_name or ""
    contribution.receipt_method_legacy_id = int(receipt_method_id or 0) or None
    contribution.receipt_method_name = receipt_method_name or ""
    contribution.operational_status = status
    contribution.notes = _statement_notes_for_movement(pilot_movement)
    contribution.statement_movement_legacy_id = int(pilot_movement.id or 0)
    contribution.source = "postgres_native_statement"
    contribution.is_active = True
    if is_new:
        contribution.created_by = actor or "django"
    contribution.updated_by = actor or "django"
    contribution.save()
    _sync_person_contribution_snapshot(contribution)
    pilot_movement.imported_contribution_legacy_id = int(contribution.legacy_id or 0)
    pilot_movement.save(update_fields=["imported_contribution_legacy_id", "updated_at"])
    return int(contribution.legacy_id or 0)


def _statement_deactivate_imported_contribution_for_movement(
    pilot_movement: StatementImportPilotMovement,
    *,
    actor: str = "",
    note: str = "",
) -> None:
    contribution_id = int(pilot_movement.imported_contribution_legacy_id or 0)
    if not contribution_id:
        return
    contribution = NativeContribution.objects.filter(legacy_id=contribution_id).first()
    if contribution is None:
        return
    contribution.is_active = False
    contribution.operational_status = "ignorado"
    contribution.notes = _merge_notes(contribution.notes, note)
    contribution.updated_by = actor or "django"
    contribution.save()
    _sync_person_contribution_snapshot(contribution)


def _ensure_statement_financial_entries_postgres_native(
    pilot_lot: StatementImportPilotLot,
    *,
    actor: str = "",
) -> dict[str, int]:
    created = 0
    synced = 0
    for movement in pilot_lot.movements.exclude(review_status="ignorado").order_by("order_in_lot", "id"):
        already_linked = bool(int(movement.imported_contribution_legacy_id or 0))
        contribution_id = _statement_sync_native_contribution_for_movement(movement, actor=actor)
        if contribution_id:
            synced += 1
            if not already_linked:
                created += 1
    return {"created": created, "synced": synced}


def _statement_receipt_eligible_native_contribution_ids(
    *,
    lot: StatementImportPilotLot | None = None,
    contribution_ids: list[int] | None = None,
) -> list[int]:
    clean_ids = [int(value or 0) for value in (contribution_ids or []) if int(value or 0)]
    if lot is not None:
        clean_ids.extend(
            int(value or 0)
            for value in lot.movements.exclude(imported_contribution_legacy_id__isnull=True).values_list(
                "imported_contribution_legacy_id", flat=True
            )
            if int(value or 0)
        )
    unique_ids = sorted({value for value in clean_ids if value})
    if not unique_ids:
        return []
    return list(
        NativeContribution.objects.filter(
            legacy_id__in=unique_ids,
            is_active=True,
            operational_status="regular",
        )
        .exclude(person_legacy_id__isnull=True)
        .exclude(person_legacy_id=0)
        .order_by("legacy_id")
        .values_list("legacy_id", flat=True)
    )


def _statement_auto_receipt_result(
    contribution_ids: list[int],
    *,
    actor: str = "",
) -> dict[str, int]:
    eligible_ids = [int(value or 0) for value in contribution_ids if int(value or 0)]
    if not eligible_ids:
        return {
            "auto_receipt_candidates": 0,
            "auto_receipt_created": 0,
            "auto_receipt_sent": 0,
            "auto_receipt_queued": 0,
            "auto_receipt_failed": 0,
            "auto_receipt_without_email": 0,
        }
    outcomes = schedule_automatic_receipts_for_events(
        eligible_ids,
        actor=actor,
        send_now=False,
    )
    summary = summarize_automatic_receipt_outcomes(outcomes, send_now=False)
    return {
        "auto_receipt_candidates": len(eligible_ids),
        "auto_receipt_created": int(summary["created"]),
        "auto_receipt_sent": int(summary["sent"]),
        "auto_receipt_queued": int(summary["queued"]),
        "auto_receipt_failed": int(summary["failed"]),
        "auto_receipt_without_email": int(summary["without_email"]),
    }


def _recompute_native_statement_lot_status(pilot_lot: StatementImportPilotLot) -> str:
    current_status = normalize_query(pilot_lot.lot_status)
    if current_status == "encerrado":
        return "encerrado"
    total = pilot_lot.movements.count()
    pending_human_count = pilot_lot.movements.filter(_statement_human_pending_filter()).count()
    ignored_count = pilot_lot.movements.filter(review_status="ignorado").count()
    linked_ids = list(
        pilot_lot.movements.exclude(imported_contribution_legacy_id__isnull=True).values_list(
            "imported_contribution_legacy_id", flat=True
        )
    )
    imported_count = (
        NativeContribution.objects.filter(legacy_id__in=linked_ids, is_active=True).count()
        if linked_ids
        else 0
    )
    if total and imported_count + ignored_count >= total and pending_human_count == 0:
        return "concluido"
    if imported_count or ignored_count or pending_human_count:
        return "parcial"
    return "pendente"


def _refresh_native_statement_lot_metadata(pilot_lot: StatementImportPilotLot) -> StatementImportPilotLot:
    review_counts = {
        (row["review_status"] or "sem_status"): int(row["total"] or 0)
        for row in pilot_lot.movements.values("review_status").annotate(total=Count("id")).order_by("-total", "review_status")
    }
    pending_human_count = pilot_lot.movements.filter(_statement_human_pending_filter()).count()
    linked_ids = list(
        pilot_lot.movements.exclude(imported_contribution_legacy_id__isnull=True).values_list(
            "imported_contribution_legacy_id", flat=True
        )
    )
    metadata = dict(pilot_lot.metadata or {})
    metadata["review_counts"] = review_counts
    metadata["imported_count"] = (
        NativeContribution.objects.filter(legacy_id__in=linked_ids, is_active=True).count()
        if linked_ids
        else 0
    )
    metadata["ignored_count"] = pilot_lot.movements.filter(review_status="ignorado").count()
    metadata["pending_human_count"] = int(pending_human_count)
    pilot_lot.metadata = metadata
    pilot_lot.movement_count = pilot_lot.movements.count()
    pilot_lot.total_value = sum((movement.amount for movement in pilot_lot.movements.all()), Decimal("0"))
    pilot_lot.lot_status = _recompute_native_statement_lot_status(pilot_lot)
    pilot_lot.save(update_fields=["metadata", "movement_count", "total_value", "lot_status", "updated_at"])
    return pilot_lot


def _native_form_lists(data: object) -> dict[str, list[str]]:
    if hasattr(data, "lists"):
        return {str(key): [str(item) for item in values] for key, values in data.lists()}
    normalized: dict[str, list[str]] = {}
    for key, value in dict(data or {}).items():
        if isinstance(value, (list, tuple)):
            normalized[str(key)] = [str(item) for item in value]
        else:
            normalized[str(key)] = [str(value)]
    return normalized


def prepare_statement_lot_postgres_native(lot_id: int, actor: str = "") -> dict[str, int | str]:
    pilot_lot = StatementImportPilotLot.objects.filter(
        id=lot_id,
        source_backend=StatementImportPilotLot.SourceBackend.POSTGRES_NATIVE,
    ).first()
    if pilot_lot is None:
        raise ValueError("Lote nativo de extrato nao encontrado.")
    previous_status = pilot_lot.lot_status or ""
    reviewed = 0
    with transaction.atomic():
        for movement in pilot_lot.movements.all().order_by("order_in_lot", "id"):
            _apply_native_statement_resolution(movement, preserve_manual_selection=True)
            movement.save()
            reviewed += 1
        financial = _ensure_statement_financial_entries_postgres_native(pilot_lot, actor=actor)
        _refresh_native_statement_lot_metadata(pilot_lot)
    receipt_result = _statement_auto_receipt_result(
        _statement_receipt_eligible_native_contribution_ids(lot=pilot_lot),
        actor=actor,
    )
    return {
        "importados": int(financial.get("created", 0) or 0),
        "movidos_contribuintes": 0,
        "status_antes": previous_status,
        **receipt_result,
        "reviewed": reviewed,
        "actor": actor or "django",
    }


def reprocess_statement_lot_postgres_native(lot_id: int) -> int:
    pilot_lot = StatementImportPilotLot.objects.filter(
        id=lot_id,
        source_backend=StatementImportPilotLot.SourceBackend.POSTGRES_NATIVE,
    ).first()
    if pilot_lot is None:
        raise ValueError("Lote nativo de extrato nao encontrado.")
    updated = 0
    with transaction.atomic():
        for movement in pilot_lot.movements.all().order_by("order_in_lot", "id"):
            _apply_native_statement_resolution(movement, preserve_manual_selection=True)
            movement.save()
            updated += 1
        _ensure_statement_financial_entries_postgres_native(pilot_lot)
        _refresh_native_statement_lot_metadata(pilot_lot)
    return updated


def update_statement_movement_postgres_native(
    movement_id: int,
    form: object,
    *,
    actor: str = "",
) -> int:
    pilot_movement = StatementImportPilotMovement.objects.select_related("lot").filter(
        id=movement_id,
        lot__source_backend=StatementImportPilotLot.SourceBackend.POSTGRES_NATIVE,
    ).first()
    if pilot_movement is None:
        raise ValueError("Movimento nativo de extrato nao encontrado.")
    data = _native_form_lists(form)
    action = str((data.get("action") or ["approve"])[0] or "").strip().lower()
    review_notes = str((data.get("review_notes") or [""])[0] or "").strip()
    resolved_person_id = moneyless_int((data.get("resolved_person_id") or ["0"])[0])
    resolved_type_id = moneyless_int((data.get("resolved_tipo_contribuicao_id") or ["0"])[0])
    meta = dict(pilot_movement.metadata or {})
    meta["association_reviewed"] = True
    if actor:
        meta["last_actor"] = actor
    if review_notes:
        pilot_movement.review_notes = review_notes
    previous_eligible_ids = _statement_receipt_eligible_native_contribution_ids(
        contribution_ids=[int(pilot_movement.imported_contribution_legacy_id or 0)],
    )
    with transaction.atomic():
        if action == "ignore":
            pilot_movement.review_status = "ignorado"
            pilot_movement.resolved_person_legacy_id = None
            _statement_deactivate_imported_contribution_for_movement(
                pilot_movement,
                actor=actor,
                note=review_notes or "Movimento de extrato marcado como ignorado na auditoria.",
            )
        elif action == "same_owner":
            pilot_movement.review_status = "ignorado"
            pilot_movement.resolved_person_legacy_id = None
            pilot_movement.review_notes = review_notes or "Mesma titularidade / origem interna."
            _statement_deactivate_imported_contribution_for_movement(
                pilot_movement,
                actor=actor,
                note=pilot_movement.review_notes,
            )
        else:
            pilot_movement.resolved_person_legacy_id = resolved_person_id or None
            if resolved_type_id:
                meta["resolved_tipo_contribuicao_id"] = resolved_type_id
            pilot_movement.metadata = meta
            _apply_native_statement_resolution(pilot_movement, preserve_manual_selection=True)
            meta = dict(pilot_movement.metadata or {})
        pilot_movement.metadata = meta
        pilot_movement.save()
        pilot_lot = pilot_movement.lot
        imported_contribution_id = 0
        if action not in {"ignore", "same_owner"}:
            imported_contribution_id = _statement_sync_native_contribution_for_movement(pilot_movement, actor=actor)
        _refresh_native_statement_lot_metadata(pilot_lot)
    new_eligible_ids = _statement_receipt_eligible_native_contribution_ids(
        contribution_ids=[int(imported_contribution_id or 0)],
    )
    created_now = [item for item in new_eligible_ids if item not in previous_eligible_ids]
    if created_now:
        _statement_auto_receipt_result(created_now, actor=actor)
    return int(imported_contribution_id or 0)


def close_statement_lot_postgres_native(lot_id: int, actor: str = "") -> dict[str, int | str]:
    pilot_lot = StatementImportPilotLot.objects.filter(
        id=lot_id,
        source_backend=StatementImportPilotLot.SourceBackend.POSTGRES_NATIVE,
    ).first()
    if pilot_lot is None:
        raise ValueError("Lote nativo de extrato nao encontrado.")
    pilot_lot = _refresh_native_statement_lot_metadata(pilot_lot)
    pending_human_count = int((pilot_lot.metadata or {}).get("pending_human_count") or 0)
    if pending_human_count:
        raise ValueError(
            f"O lote ainda tem {pending_human_count} pendencia(s) humana(s). Conclua a auditoria antes de encerrar."
        )
    with transaction.atomic():
        financial = _ensure_statement_financial_entries_postgres_native(pilot_lot, actor=actor)
        pilot_lot.lot_status = "encerrado"
        metadata = dict(pilot_lot.metadata or {})
        metadata["closed_by"] = actor or "django"
        pilot_lot.metadata = metadata
        pilot_lot.save(update_fields=["lot_status", "metadata", "updated_at"])
    receipt_result = _statement_auto_receipt_result(
        _statement_receipt_eligible_native_contribution_ids(lot=pilot_lot),
        actor=actor,
    )
    return {
        "importados": int(financial.get("created", 0) or 0),
        "movidos_contribuintes": 0,
        **receipt_result,
    }


def list_import_lots_postgres(limit: int = 80) -> dict[str, Any]:
    queryset = StatementImportPilotLot.objects.order_by("-created_at", "-id")
    rows = list(queryset[: max(1, int(limit or 80))])
    items: list[dict[str, Any]] = []
    for row in rows:
        metadata = row.metadata or {}
        pending = int(metadata.get("pending_human_count") or 0)
        ignored = int(
            (metadata.get("review_counts") or {}).get("ignorado")
            or metadata.get("ignored_count")
            or 0
        )
        items.append(
            {
                "id": int(row.id or 0),
                "tipo": "Extrato",
                "banco": row.bank_name or "",
                "layout": row.layout_code or "",
                "nome_arquivo": row.file_name or "",
                "periodo": f"{br_date(row.period_start.isoformat() if row.period_start else '')} a {br_date(row.period_end.isoformat() if row.period_end else '')}".strip(" a "),
                "movimentos": int(row.movement_count or 0),
                "total_fmt": _money(row.total_value),
                "pendentes": pending,
                "ignorados": ignored,
                "status": row.lot_status or "",
                "criado_em": br_datetime(row.created_at.isoformat() if row.created_at else ""),
                "criado_em_raw": row.created_at.isoformat() if row.created_at else "",
                "snapshot_backend": row.source_backend or "",
            }
        )
    total = StatementImportPilotLot.objects.count()
    return {"items": items, "total": total, "shown": len(items), "limit": limit}


def _next_cent_rule_legacy_id() -> int:
    value = CentRuleSnapshot.objects.aggregate(value=models.Max("legacy_id")).get("value") or 0
    return int(value or 0) + 1


def _next_contribution_type_legacy_id(organization_id: int) -> int:
    value = (
        ContributionTypeSnapshot.objects.filter(organization_id=organization_id)
        .aggregate(value=models.Max("legacy_id"))
        .get("value")
        or 0
    )
    return int(value or 0) + 1


def cent_rules_data_postgres(edit_rule_id: int = 0) -> dict[str, Any]:
    organization_id = (
        ContributionTypeSnapshot.objects.filter(is_active=True)
        .order_by("organization_id", "legacy_id")
        .values_list("organization_id", flat=True)
        .first()
        or 1
    )
    rules = list(
        CentRuleSnapshot.objects.filter(organization_id=organization_id).order_by("cent_code", "legacy_id")
    )
    current = next((rule for rule in rules if int(rule.legacy_id or 0) == int(edit_rule_id or 0)), None)
    types = list(
        ContributionTypeSnapshot.objects.filter(
            organization_id=organization_id,
            is_active=True,
        ).order_by("name", "legacy_id")
    )
    return {
        "rules": [
            {
                "id": int(rule.legacy_id or 0),
                "codigo": str(rule.cent_code or "").zfill(2),
                "nome": rule.destination_name or "",
                "tipo_id": int(rule.contribution_type_legacy_id or 0) or None,
                "tipo_nome": rule.contribution_type_name or "Sem tipo vinculado",
                "tipo_codigo": "",
                "campanha_nome": rule.campaign_name or "",
                "conta_codigo": rule.account_code or "",
                "conta_nome": rule.account_name or "",
                "ativo": bool(rule.is_active),
            }
            for rule in rules
        ],
        "types": [
            {
                "id": int(row.legacy_id or 0),
                "codigo": row.code or "",
                "nome": row.name or "",
                "selected": bool(current and int(row.legacy_id or 0) == int(current.contribution_type_legacy_id or 0)),
            }
            for row in types
        ],
        "current": (
            {
                "id": int(current.legacy_id or 0),
                "codigo": str(current.cent_code or "").zfill(2),
                "nome": current.destination_name or "",
                "tipo_id": int(current.contribution_type_legacy_id or 0) or None,
                "tipo_nome": current.contribution_type_name or "",
                "campanha_nome": current.campaign_name or "",
                "conta_codigo": current.account_code or "",
                "conta_nome": current.account_name or "",
                "ativo": bool(current.is_active),
            }
            if current
            else None
        ),
        "edit_rule_id": int(edit_rule_id or 0),
        "active_count": sum(1 for rule in rules if rule.is_active),
    }


def save_cent_rule_from_form_postgres(form: Any) -> int:
    getter = getattr(form, "get", None)
    rule_id = moneyless_int(getter("rule_id") if getter else 0)
    organization_id = (
        ContributionTypeSnapshot.objects.filter(is_active=True)
        .order_by("organization_id", "legacy_id")
        .values_list("organization_id", flat=True)
        .first()
        or 1
    )
    cent_code = normalize_query(getter("codigo_centavos") if getter else "").zfill(2)
    if len(cent_code) != 2 or not cent_code.isdigit():
        raise ValueError("Informe um codigo de centavos com dois digitos.")
    destination_name = normalize_query(getter("nome_destinacao") if getter else "")
    if len(destination_name) < 3:
        raise ValueError("Informe o nome da destinacao com pelo menos 3 caracteres.")
    contribution_type_id = moneyless_int(getter("tipo_contribuicao_id") if getter else 0)
    active = str(getter("ativo") if getter else "1").strip() != "0"
    if contribution_type_id:
        type_row = ContributionTypeSnapshot.objects.filter(
            organization_id=organization_id,
            legacy_id=contribution_type_id,
            is_active=True,
        ).first()
        if type_row is None:
            raise ValueError("Tipo de contribuicao invalido para a regra.")
    else:
        type_row = ContributionTypeSnapshot.objects.create(
            legacy_id=_next_contribution_type_legacy_id(int(organization_id or 0)),
            organization_id=int(organization_id or 0),
            code=f"CENT.{cent_code}",
            name=destination_name,
            is_active=True,
        )
    defaults = {
        "organization_id": int(organization_id or 0),
        "cent_code": cent_code,
        "destination_name": destination_name,
        "contribution_type_legacy_id": int(type_row.legacy_id or 0),
        "contribution_type_name": type_row.name or destination_name,
        "campaign_name": destination_name,
        "account_code": f"CENT.{cent_code}",
        "account_name": destination_name,
        "is_active": active,
    }
    with transaction.atomic():
        if rule_id:
            rule = CentRuleSnapshot.objects.filter(legacy_id=rule_id).first()
            if rule is None:
                raise ValueError("Regra de centavos nao encontrada.")
            for key, value in defaults.items():
                setattr(rule, key, value)
            rule.save()
        else:
            rule = CentRuleSnapshot.objects.create(
                legacy_id=_next_cent_rule_legacy_id(),
                **defaults,
            )
    return int(rule.legacy_id or 0)


def dashboard_summary_postgres() -> dict[str, Any]:
    active_people = PersonSnapshot.objects.filter(is_active=True)
    people_total = active_people.count()
    active_members = active_people.filter(status="membro_ativo").count()
    active_members_niteroi = PersonAddressSnapshot.objects.filter(
        person__is_active=True,
        person__status="membro_ativo",
        is_primary=True,
    ).filter(Q(city__iexact="Niteroi") | Q(city__iexact="Niterói")).values("person_id").distinct().count()
    contributors_total = (
        PersonContributorSnapshot.objects.filter(is_active=True).count()
        + NativeAuxContributor.objects.filter(is_active=True).exclude(legacy_reference_id__isnull=False).count()
    )
    contributions_qs = NativeContribution.objects.filter(is_active=True)
    contributions_count = contributions_qs.count()
    contributions_total = float(
        contributions_qs.aggregate(value=models.Sum("amount")).get("value") or 0
    )
    envelope_qs = NativeEnvelope.objects.filter(is_active=True)
    envelope_count = envelope_qs.count()
    envelope_total = float(
        envelope_qs.aggregate(value=models.Sum("total_informed")).get("value") or 0
    )
    unlinked_contributions = contributions_qs.filter(person_legacy_id__isnull=True).count()
    statement_lots = StatementImportPilotLot.objects.count()
    pix_lots = 0
    pending_bank_reviews = StatementImportPilotMovement.objects.filter(_statement_human_pending_filter()).count()

    relationships = PersonRelationshipSnapshot.objects.filter(
        is_active=True,
        relationship_type="nucleo_familiar",
        person__is_active=True,
        related_person__is_active=True,
    ).values_list("person__legacy_id", "related_person__legacy_id")
    graph: dict[int, set[int]] = defaultdict(set)
    for left_id, right_id in relationships:
        left = int(left_id or 0)
        right = int(right_id or 0)
        if not left or not right:
            continue
        graph[left].add(right)
        graph[right].add(left)
    seen: set[int] = set()
    family_groups = 0
    grouped_people: set[int] = set()
    for node in sorted(graph):
        if node in seen:
            continue
        stack = [node]
        component: set[int] = set()
        seen.add(node)
        while stack:
            current = stack.pop()
            component.add(current)
            for next_id in graph[current]:
                if next_id not in seen:
                    seen.add(next_id)
                    stack.append(next_id)
        if len(component) >= 2:
            family_groups += 1
            grouped_people.update(component)
    single_groups = max(0, people_total - len(grouped_people))
    household_total = family_groups + single_groups

    household_summary = organized_family_nuclei_summary()
    household_broad = broad_family_candidates_summary()

    months_qs = (
        contributions_qs.values("competence")
        .annotate(
            count=models.Count("id"),
            total=models.Sum("amount"),
            ordem=models.Max("competence_order"),
        )
        .order_by("-ordem", "-competence")[:6]
    )
    months = [
        {
            "competencia": row["competence"] or "Sem competencia",
            "count": int(row["count"] or 0),
            "total": float(row["total"] or 0),
            "total_fmt": _money(row["total"] or 0),
        }
        for row in months_qs
    ]
    statuses = [
        {"status": format_status(row["status"] or ""), "count": int(row["total"] or 0)}
        for row in (
            active_people.values("status")
            .annotate(total=models.Count("id"))
            .order_by("-total", "status")
        )
    ]
    return {
        "people_total": people_total,
        "active_members": active_members,
        "active_members_niteroi": active_members_niteroi,
        "contributors_total": contributors_total,
        "contributions_count": contributions_count,
        "contributions_total": contributions_total,
        "contributions_total_fmt": _money(contributions_total),
        "envelope_count": envelope_count,
        "envelope_total": envelope_total,
        "envelope_total_fmt": _money(envelope_total),
        "unlinked_contributions": unlinked_contributions,
        "statement_lots": statement_lots,
        "pix_lots": pix_lots,
        "total_lots": statement_lots,
        "pending_bank_reviews": pending_bank_reviews,
        "household_total": int(household_summary.get("total") or household_total),
        "household_family_groups": int(household_summary.get("family_groups") or family_groups),
        "household_single_groups": int(household_summary.get("single_groups") or single_groups),
        "household_review_groups": int(household_summary.get("review_groups") or 0),
        "household_broad_groups": int(household_broad.get("total") or 0),
        "household_broad_pending": int(household_broad.get("pending_groups") or 0),
        "months": months,
        "people_statuses": statuses,
    }

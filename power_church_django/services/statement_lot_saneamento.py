from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from django.db import transaction
from django.utils import timezone

from power_church_core.normalization import normalize_match_name, normalize_query
from power_church_core.bank_lots import statement_entry_fingerprint
from power_church_django.apps.contributions.models import (
    NativeContribution,
    ReceiptDispatch,
    ReceiptItemSnapshot,
)
from power_church_django.apps.imports.models import StatementImportPilotLot, StatementImportPilotMovement
from power_church_django.apps.imports.services import (
    _apply_native_statement_resolution,
    _native_statement_people_matching_cache,
    _refresh_native_statement_lot_metadata,
    _statement_receipt_eligible_native_contribution_ids,
    _statement_sync_native_contribution_for_movement,
    _temporary_pdf_provider,
    _parse_iso_date,
    plan_statement_import,
)
from power_church_django.services.receipt_delivery import (
    cancel_receipts_for_contribution_ids,
    get_receipt_detail_cached,
    issue_receipts_for_event_contributions,
    queue_receipt_dispatches,
)
from power_church_django.services.contributions_native import _sync_person_contribution_snapshot


DEFAULT_CORRECTION_SUBJECT = "Correção de recibo de contribuição - {receipt_number}"
DEFAULT_CORRECTION_BODY = """Prezado(a) {person_name},

Identificamos uma falha no processamento automático de um extrato bancário referente às contribuições de junho/2026. Por esse motivo, o recibo enviado anteriormente pode ter apresentado valor ou composição divergente do lançamento realizado.

Pedimos sinceras desculpas pelo transtorno.

A contribuição foi conferida novamente com base no extrato original, e estamos encaminhando em anexo o recibo corrigido, no valor total de {total_fmt}, referente ao período {period_label}.

Por gentileza, desconsidere o recibo anterior relacionado a esse lançamento. Esta correção não altera a sua contribuição; ela apenas ajusta o registro e o recibo emitido pelo sistema.

Seguimos à disposição para qualquer conferência adicional.

Atenciosamente,
Tesouraria
Primeira Igreja Batista de Niterói
"""


@dataclass(slots=True)
class StatementSaneamentoDifference:
    movement_id: int
    order_in_lot: int
    current_amount: Decimal
    expected_amount: Decimal
    current_source_name: str
    expected_source_name: str
    current_document: str
    expected_document: str
    current_date: str
    expected_date: str
    imported_contribution_id: int
    contribution_person_id: int
    contribution_person_name: str
    active_receipts: list[dict[str, Any]] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


@dataclass(slots=True)
class StatementSaneamentoAnalysis:
    lot_id: int
    file_name: str
    layout_code: str
    pdf_path: str
    parsed_count: int
    movement_count: int
    parsed_total: Decimal
    current_total: Decimal
    differences: list[StatementSaneamentoDifference]
    missing_orders: list[int]
    extra_orders: list[int]

    @property
    def affected_contribution_ids(self) -> list[int]:
        return sorted(
            {
                int(diff.imported_contribution_id or 0)
                for diff in self.differences
                if int(diff.imported_contribution_id or 0)
            }
        )


def analyze_statement_lot_against_pdf(
    *,
    lot_id: int,
    pdf_path: Path,
    layout_code: str = "",
    pdf_provider: str = "pymupdf",
) -> StatementSaneamentoAnalysis:
    lot = StatementImportPilotLot.objects.get(id=int(lot_id or 0))
    selected_layout = normalize_query(layout_code).upper() or lot.layout_code
    with _temporary_pdf_provider(pdf_provider):
        parsed = plan_statement_import(selected_layout, pdf_path)
    expected_by_order = {
        int(item.get("order_in_file") or 0): item
        for item in list(parsed.get("entries") or [])
        if int(item.get("order_in_file") or 0)
    }
    movements = list(lot.movements.order_by("order_in_lot", "id"))
    movement_by_order = {int(movement.order_in_lot or 0): movement for movement in movements}
    differences: list[StatementSaneamentoDifference] = []
    for order, movement in movement_by_order.items():
        expected = expected_by_order.get(order)
        if expected is None:
            continue
        diff = _movement_difference(movement, expected)
        if diff is not None:
            differences.append(diff)
    return StatementSaneamentoAnalysis(
        lot_id=int(lot.id or 0),
        file_name=lot.file_name or "",
        layout_code=selected_layout,
        pdf_path=str(pdf_path),
        parsed_count=len(expected_by_order),
        movement_count=len(movement_by_order),
        parsed_total=sum((Decimal(str(item.get("amount") or 0)) for item in expected_by_order.values()), Decimal("0")),
        current_total=sum((movement.amount for movement in movements), Decimal("0")),
        differences=differences,
        missing_orders=sorted(order for order in expected_by_order if order not in movement_by_order),
        extra_orders=sorted(order for order in movement_by_order if order not in expected_by_order),
    )


def rebuild_missing_statement_lot_from_native_contributions(
    *,
    lot_id: int,
    pdf_path: Path,
    layout_code: str,
    pdf_provider: str = "pymupdf",
) -> StatementImportPilotLot:
    clean_lot_id = int(lot_id or 0)
    if not clean_lot_id:
        raise ValueError("Informe o numero do lote a reconstruir.")
    existing = StatementImportPilotLot.objects.filter(id=clean_lot_id).first()
    if existing is not None:
        return existing
    contributions = list(
        NativeContribution.objects.filter(notes__icontains=f"lote de extrato #{clean_lot_id}", is_active=True)
        .order_by("legacy_id")
    )
    if not contributions:
        raise ValueError(f"Nenhuma contribuicao ativa encontrada com nota do lote de extrato #{clean_lot_id}.")
    with _temporary_pdf_provider(pdf_provider):
        parsed = plan_statement_import(layout_code, pdf_path)
    entries = list(parsed.get("entries") or [])
    if len(entries) != len(contributions):
        raise ValueError(
            f"O PDF atual tem {len(entries)} linha(s), mas as contribuicoes do lote #{clean_lot_id} somam {len(contributions)}."
        )
    first = contributions[0]
    lot = StatementImportPilotLot(
        id=clean_lot_id,
        reference_key=f"reconstruido_notas:{clean_lot_id}:{parsed.get('file_hash') or normalize_query(pdf_path.name)}",
        source_backend=StatementImportPilotLot.SourceBackend.POSTGRES_NATIVE,
        source_db_path="",
        source_lot_id=clean_lot_id,
        bank_name=str(parsed.get("bank_name") or "Sicoob"),
        layout_code=normalize_query(layout_code).upper() or str(parsed.get("layout_code") or ""),
        file_name=pdf_path.name,
        file_hash=str(parsed.get("file_hash") or ""),
        period_start=_parse_iso_date(parsed.get("period_start")),
        period_end=_parse_iso_date(parsed.get("period_end")),
        movement_count=len(contributions),
        total_value=sum((item.amount for item in contributions), Decimal("0")),
        lot_status="parcial",
        pdf_provider=pdf_provider,
        comparison_ok=True,
        comparison_note="Lote reconstruido a partir das contribuicoes nativas para saneamento operacional.",
        metadata={
            "native_origin": "rebuild_missing_statement_lot_from_native_contributions",
            "reconstructed_from_notes_lot_id": clean_lot_id,
            "reconstructed_contribution_first_id": int(first.legacy_id or 0),
            "pending_human_count": 0,
            "imported_count": len(contributions),
        },
    )
    with transaction.atomic():
        lot.save(force_insert=True)
        StatementImportPilotMovement.objects.bulk_create(
            [
                _movement_from_native_contribution(
                    lot=lot,
                    contribution=contribution,
                    order_in_lot=index,
                )
                for index, contribution in enumerate(contributions, start=1)
            ]
        )
        _refresh_native_statement_lot_metadata(lot)
    return lot


def apply_statement_lot_saneamento(
    *,
    lot_id: int,
    pdf_path: Path,
    layout_code: str = "",
    pdf_provider: str = "pymupdf",
    actor: str = "saneamento_extrato",
    send_now: bool = False,
    subject: str = DEFAULT_CORRECTION_SUBJECT,
    body: str = DEFAULT_CORRECTION_BODY,
) -> dict[str, Any]:
    analysis = analyze_statement_lot_against_pdf(
        lot_id=lot_id,
        pdf_path=pdf_path,
        layout_code=layout_code,
        pdf_provider=pdf_provider,
    )
    affected_ids = analysis.affected_contribution_ids
    reason = (
        "Recibo cancelado para reemissao por saneamento de parser bancario. "
        f"Lote de extrato #{analysis.lot_id} relido a partir do PDF original."
    )
    updated_contribution_ids: list[int] = []
    cancelled_receipt_ids: list[int] = []
    if not analysis.differences:
        return {
            "analysis": analysis,
            "cancelled_receipt_ids": [],
            "updated_contribution_ids": [],
            "new_receipt_ids": [],
            "queued_dispatch_ids": [],
            "sent_now": 0,
            "without_email": 0,
        }
    lot = StatementImportPilotLot.objects.get(id=int(lot_id or 0))
    with _temporary_pdf_provider(pdf_provider):
        parsed = plan_statement_import(analysis.layout_code, pdf_path)
    expected_by_order = {
        int(item.get("order_in_file") or 0): item
        for item in list(parsed.get("entries") or [])
        if int(item.get("order_in_file") or 0)
    }
    reissue_contribution_ids = _contribution_ids_to_reissue_after_receipt_cancellation(affected_ids)
    with transaction.atomic():
        cancelled_receipt_ids = cancel_receipts_for_contribution_ids(
            affected_ids,
            actor=actor,
            reason=reason,
        )
        matching_cache = _native_statement_people_matching_cache()
        for diff in analysis.differences:
            movement = StatementImportPilotMovement.objects.select_related("lot").get(id=diff.movement_id)
            expected = expected_by_order.get(int(movement.order_in_lot or 0))
            if expected is None:
                continue
            target_contribution_id = _saneamento_target_contribution_id(
                movement,
                fallback=diff.imported_contribution_id,
            )
            _apply_expected_entry_to_movement(movement, expected, actor=actor)
            _apply_native_statement_resolution(
                movement,
                preserve_manual_selection=False,
                matching_cache=matching_cache,
            )
            if target_contribution_id:
                movement.imported_contribution_legacy_id = target_contribution_id
            movement.save()
            contribution_id = _statement_sync_native_contribution_for_movement(movement, actor=actor)
            if contribution_id:
                updated_contribution_ids.append(int(contribution_id))
        _refresh_native_statement_lot_metadata(lot)
    eligible_ids = _statement_receipt_eligible_native_contribution_ids(
        contribution_ids=sorted(set(reissue_contribution_ids + updated_contribution_ids))
    )
    new_receipt_ids = issue_receipts_for_event_contributions(
        eligible_ids,
        emission_date=date.today().isoformat(),
        notes="Recibo corrigido por saneamento de importacao de extrato bancario.",
        actor=actor,
        replace_existing=True,
    )
    queued_dispatch_ids: list[int] = []
    without_email = 0
    for receipt_id in new_receipt_ids:
        detail = get_receipt_detail_cached(receipt_id)
        receipt = detail.get("receipt") if detail else {}
        person_email = normalize_query((detail.get("person") or {}).get("email") if detail else "")
        if not person_email:
            without_email += 1
            continue
        dispatches = queue_receipt_dispatches(
            [receipt_id],
            email_to=person_email,
            subject=subject,
            body=body,
            actor=actor,
            trigger=ReceiptDispatch.Trigger.RETROACTIVE,
            auto_created=True,
            send_now=send_now,
            metadata_extra={
                "campaign_key": f"saneamento_extrato:{analysis.lot_id}:{timezone.localtime().strftime('%Y%m%d')}",
                "campaign_mode": "saneamento_parser_bancario",
                "source_lot_id": analysis.lot_id,
                "cancelled_receipt_ids": cancelled_receipt_ids,
                "corrected_contribution_ids": updated_contribution_ids,
                "reissued_contribution_ids": eligible_ids,
            },
        )
        queued_dispatch_ids.extend(int(dispatch.pk or 0) for dispatch in dispatches)
    return {
        "analysis": analysis,
        "cancelled_receipt_ids": cancelled_receipt_ids,
        "updated_contribution_ids": sorted(set(updated_contribution_ids)),
        "reissued_contribution_ids": eligible_ids,
        "new_receipt_ids": new_receipt_ids,
        "queued_dispatch_ids": queued_dispatch_ids,
        "sent_now": len(queued_dispatch_ids) if send_now else 0,
        "without_email": without_email,
    }


def recover_partial_statement_lot_saneamento(
    *,
    lot_id: int,
    actor: str = "saneamento_extrato",
    send_now: bool = False,
    subject: str = DEFAULT_CORRECTION_SUBJECT,
    body: str = DEFAULT_CORRECTION_BODY,
) -> dict[str, Any]:
    lot = StatementImportPilotLot.objects.get(id=int(lot_id or 0))
    movements = list(lot.movements.order_by("order_in_lot", "id"))
    pairs: list[tuple[StatementImportPilotMovement, int, int]] = []
    for movement in movements:
        target_id = _saneamento_target_contribution_id(movement)
        current_id = int(movement.imported_contribution_legacy_id or 0)
        if target_id and current_id and current_id != target_id:
            pairs.append((movement, current_id, target_id))
    duplicate_ids = sorted({current_id for _, current_id, _ in pairs})
    target_ids = sorted({target_id for _, _, target_id in pairs})
    reason = (
        "Recibo/contribuicao cancelado por recuperacao de rodada parcial de saneamento. "
        f"Lote de extrato #{lot.id} sera reemitido com as contribuicoes originais corrigidas."
    )
    impacted_old_receipt_pks = list(
        ReceiptItemSnapshot.objects.filter(
            contribution_legacy_id__in=target_ids,
            receipt__is_cancelled=True,
            receipt__notes__icontains="saneamento de parser bancario",
        )
        .values_list("receipt_id", flat=True)
        .distinct()
    )
    reissue_ids = sorted(
        {
            int(value or 0)
            for value in ReceiptItemSnapshot.objects.filter(receipt_id__in=impacted_old_receipt_pks)
            .exclude(contribution_legacy_id__isnull=True)
            .values_list("contribution_legacy_id", flat=True)
            if int(value or 0)
        }
        | set(target_ids)
    )
    with transaction.atomic():
        partial_receipt_ids = cancel_receipts_for_contribution_ids(
            duplicate_ids,
            actor=actor,
            reason=reason,
        )
        for duplicate in NativeContribution.objects.filter(legacy_id__in=duplicate_ids, is_active=True):
            duplicate.is_active = False
            duplicate.operational_status = "ignorado"
            duplicate.notes = _append_note(duplicate.notes, reason)
            duplicate.updated_by = actor or "django"
            duplicate.save(update_fields=["is_active", "operational_status", "notes", "updated_by", "updated_at"])
            _sync_person_contribution_snapshot(duplicate)
        updated_ids: list[int] = []
        for movement, _, target_id in pairs:
            movement.imported_contribution_legacy_id = int(target_id or 0)
            movement.save(update_fields=["imported_contribution_legacy_id", "updated_at"])
            contribution_id = _statement_sync_native_contribution_for_movement(movement, actor=actor)
            if contribution_id:
                updated_ids.append(int(contribution_id))
        _refresh_native_statement_lot_metadata(lot)
    eligible_ids = _statement_receipt_eligible_native_contribution_ids(
        contribution_ids=sorted(set(reissue_ids + updated_ids))
    )
    new_receipt_ids = issue_receipts_for_event_contributions(
        eligible_ids,
        emission_date=date.today().isoformat(),
        notes="Recibo corrigido por saneamento de importacao de extrato bancario.",
        actor=actor,
        replace_existing=True,
    )
    queued_dispatch_ids: list[int] = []
    without_email = 0
    for receipt_id in new_receipt_ids:
        detail = get_receipt_detail_cached(receipt_id)
        person_email = normalize_query((detail.get("person") or {}).get("email") if detail else "")
        if not person_email:
            without_email += 1
            continue
        dispatches = queue_receipt_dispatches(
            [receipt_id],
            email_to=person_email,
            subject=subject,
            body=body,
            actor=actor,
            trigger=ReceiptDispatch.Trigger.RETROACTIVE,
            auto_created=True,
            send_now=send_now,
            metadata_extra={
                "campaign_key": f"saneamento_extrato:{lot.id}:{timezone.localtime().strftime('%Y%m%d')}:recuperacao",
                "campaign_mode": "saneamento_parser_bancario",
                "campaign_recovery": True,
                "source_lot_id": int(lot.id or 0),
                "partial_receipt_ids_cancelled": partial_receipt_ids,
                "duplicate_contribution_ids_deactivated": duplicate_ids,
                "corrected_contribution_ids": updated_ids,
                "reissued_contribution_ids": eligible_ids,
            },
        )
        queued_dispatch_ids.extend(int(dispatch.pk or 0) for dispatch in dispatches)
    return {
        "lot_id": int(lot.id or 0),
        "partial_receipt_ids_cancelled": partial_receipt_ids,
        "duplicate_contribution_ids_deactivated": duplicate_ids,
        "updated_contribution_ids": sorted(set(updated_ids)),
        "reissued_contribution_ids": eligible_ids,
        "new_receipt_ids": new_receipt_ids,
        "queued_dispatch_ids": queued_dispatch_ids,
        "sent_now": len(queued_dispatch_ids) if send_now else 0,
        "without_email": without_email,
    }


def _contribution_ids_to_reissue_after_receipt_cancellation(contribution_ids: list[int]) -> list[int]:
    if not contribution_ids:
        return []
    active_receipt_ids = list(
        ReceiptItemSnapshot.objects.filter(
            contribution_legacy_id__in=contribution_ids,
            receipt__is_cancelled=False,
        )
        .values_list("receipt_id", flat=True)
        .distinct()
    )
    if not active_receipt_ids:
        return sorted(set(contribution_ids))
    receipt_item_ids = list(
        ReceiptItemSnapshot.objects.filter(receipt_id__in=active_receipt_ids)
        .exclude(contribution_legacy_id__isnull=True)
        .values_list("contribution_legacy_id", flat=True)
    )
    return sorted({int(value) for value in [*contribution_ids, *receipt_item_ids] if int(value or 0)})


def _saneamento_target_contribution_id(
    movement: StatementImportPilotMovement,
    *,
    fallback: int = 0,
) -> int:
    metadata = dict(movement.metadata or {})
    return int(metadata.get("reconstructed_from_contribution_id") or fallback or 0)


def _append_note(current: str, note: str) -> str:
    return "\n".join(part for part in [normalize_query(current), normalize_query(note)] if part)


def statement_saneamento_receipt_impact(analysis: StatementSaneamentoAnalysis) -> dict[str, Any]:
    affected_ids = analysis.affected_contribution_ids
    if not affected_ids:
        return {
            "affected_contribution_ids": [],
            "active_receipt_count": 0,
            "reissue_contribution_ids": [],
            "with_email_count": 0,
            "without_email_count": 0,
            "dispatch_status_counts": {},
            "rows": [],
        }
    active_items = list(
        ReceiptItemSnapshot.objects.select_related("receipt").filter(
            contribution_legacy_id__in=affected_ids,
            receipt__is_cancelled=False,
        )
    )
    active_receipt_pks = sorted({int(item.receipt_id or 0) for item in active_items if int(item.receipt_id or 0)})
    all_receipt_items = list(
        ReceiptItemSnapshot.objects.select_related("receipt").filter(receipt_id__in=active_receipt_pks)
    )
    reissue_contribution_ids = sorted(
        {
            int(item.contribution_legacy_id or 0)
            for item in all_receipt_items
            if int(item.contribution_legacy_id or 0)
        }
        | set(affected_ids)
    )
    receipt_legacy_ids = sorted(
        {int(item.receipt.legacy_id or 0) for item in all_receipt_items if int(item.receipt.legacy_id or 0)}
    )
    dispatches = list(
        ReceiptDispatch.objects.filter(legacy_receipt_id__in=receipt_legacy_ids)
        .exclude(status=ReceiptDispatch.Status.CANCELLED)
        .order_by("legacy_receipt_id", "-created_at", "-id")
    )
    dispatch_status_by_receipt: dict[int, list[str]] = {}
    for dispatch in dispatches:
        dispatch_status_by_receipt.setdefault(int(dispatch.legacy_receipt_id or 0), []).append(dispatch.status)
    rows: list[dict[str, Any]] = []
    with_email = 0
    without_email = 0
    for receipt_pk in active_receipt_pks:
        receipt_items = [item for item in all_receipt_items if int(item.receipt_id or 0) == receipt_pk]
        if not receipt_items:
            continue
        receipt = receipt_items[0].receipt
        email = normalize_query(receipt.person_email)
        if email:
            with_email += 1
        else:
            without_email += 1
        rows.append(
            {
                "receipt_id": int(receipt.legacy_id or 0),
                "receipt_number": receipt.receipt_number or "",
                "person_id": int(receipt.person_legacy_id or 0),
                "person_name": receipt.person_name or "",
                "email": email,
                "total": str(receipt.total_value or "0"),
                "affected_contribution_ids": sorted(
                    int(item.contribution_legacy_id or 0)
                    for item in receipt_items
                    if int(item.contribution_legacy_id or 0) in affected_ids
                ),
                "reissue_contribution_ids": sorted(
                    int(item.contribution_legacy_id or 0)
                    for item in receipt_items
                    if int(item.contribution_legacy_id or 0)
                ),
                "dispatch_statuses": dispatch_status_by_receipt.get(int(receipt.legacy_id or 0), []),
            }
        )
    return {
        "affected_contribution_ids": affected_ids,
        "active_receipt_count": len(active_receipt_pks),
        "reissue_contribution_ids": reissue_contribution_ids,
        "with_email_count": with_email,
        "without_email_count": without_email,
        "dispatch_status_counts": dict(Counter(dispatch.status for dispatch in dispatches)),
        "rows": rows,
    }


def write_statement_saneamento_report(
    result: StatementSaneamentoAnalysis | dict[str, Any],
    *,
    report_dir: Path,
    applied: bool,
) -> Path:
    if isinstance(result, StatementSaneamentoAnalysis):
        analysis = result
        payload: dict[str, Any] = {}
    else:
        analysis = result["analysis"]
        payload = result
    impact = statement_saneamento_receipt_impact(analysis)
    report_dir.mkdir(parents=True, exist_ok=True)
    target = report_dir / f"statement_lot_saneamento_{analysis.lot_id}_{timezone.localtime().strftime('%Y%m%d_%H%M%S')}.md"
    lines = [
        "# Saneamento de lote de extrato",
        "",
        f"- Lote: `{analysis.lot_id}`",
        f"- Arquivo: `{analysis.file_name}`",
        f"- PDF auditado: `{analysis.pdf_path}`",
        f"- Layout: `{analysis.layout_code}`",
        f"- Modo: `{'aplicado' if applied else 'relatorio'}`",
        f"- Linhas no PDF corrigido: `{analysis.parsed_count}`",
        f"- Movimentos no lote: `{analysis.movement_count}`",
        f"- Total PDF corrigido: `{analysis.parsed_total}`",
        f"- Total atual do lote: `{analysis.current_total}`",
        f"- Divergencias encontradas: `{len(analysis.differences)}`",
        f"- Contribuicoes afetadas: `{len(impact['affected_contribution_ids'])}`",
        f"- Recibos ativos impactados: `{impact['active_receipt_count']}`",
        f"- Contribuicoes a manter cobertas na reemissao: `{len(impact['reissue_contribution_ids'])}`",
        f"- Recibos com e-mail: `{impact['with_email_count']}`",
        f"- Recibos sem e-mail: `{impact['without_email_count']}`",
        f"- Status de envios existentes: `{impact['dispatch_status_counts']}`",
        f"- Ordens faltantes no lote: `{analysis.missing_orders[:30]}`",
        f"- Ordens extras no lote: `{analysis.extra_orders[:30]}`",
        "",
    ]
    if payload:
        lines.extend(
            [
                "## Aplicacao",
                "",
                f"- Recibos cancelados: `{payload.get('cancelled_receipt_ids', [])}`",
                f"- Contribuicoes atualizadas: `{payload.get('updated_contribution_ids', [])}`",
                f"- Contribuicoes reemitidas/cobertas: `{payload.get('reissued_contribution_ids', [])}`",
                f"- Novos recibos: `{payload.get('new_receipt_ids', [])}`",
                f"- Envios enfileirados: `{payload.get('queued_dispatch_ids', [])}`",
                f"- Sem e-mail: `{payload.get('without_email', 0)}`",
                "",
            ]
        )
    if impact["rows"]:
        lines.extend(
            [
                "## Impacto em recibos ativos",
                "",
                "| Recibo | Pessoa | Total atual | E-mail | Envios existentes | Itens afetados | Itens que devem continuar cobertos |",
                "| --- | --- | ---: | --- | --- | --- | --- |",
            ]
        )
        for row in impact["rows"]:
            lines.append(
                "| "
                + " | ".join(
                    [
                        f"{row['receipt_number'] or row['receipt_id']}",
                        str(row["person_name"]).replace("|", "/"),
                        str(row["total"]),
                        str(row["email"] or "sem e-mail"),
                        ", ".join(row["dispatch_statuses"]) or "sem envio registrado",
                        ", ".join(map(str, row["affected_contribution_ids"])),
                        ", ".join(map(str, row["reissue_contribution_ids"])),
                    ]
                )
                + " |"
            )
        lines.append("")
    lines.extend(
        [
            "## Divergencias",
            "",
            "| Ordem | Movimento | Contribuicao | Motivos | Atual | Corrigido | Recibos ativos antes |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for diff in analysis.differences:
        current = f"{diff.current_date} · {diff.current_source_name} · {diff.current_document} · {diff.current_amount}"
        expected = f"{diff.expected_date} · {diff.expected_source_name} · {diff.expected_document} · {diff.expected_amount}"
        receipts = ", ".join(
            f"{item.get('number') or item.get('id')} ({item.get('total')})"
            for item in diff.active_receipts
        )
        lines.append(
            "| "
            + " | ".join(
                [
                    str(diff.order_in_lot),
                    str(diff.movement_id),
                    str(diff.imported_contribution_id or ""),
                    ", ".join(diff.reasons).replace("|", "/"),
                    current.replace("|", "/"),
                    expected.replace("|", "/"),
                    receipts.replace("|", "/"),
                ]
            )
            + " |"
        )
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def _movement_difference(
    movement: StatementImportPilotMovement,
    expected: dict[str, Any],
) -> StatementSaneamentoDifference | None:
    reasons: list[str] = []
    expected_amount = Decimal(str(expected.get("amount") or 0)).quantize(Decimal("0.01"))
    current_amount = Decimal(str(movement.amount or 0)).quantize(Decimal("0.01"))
    expected_source_name = normalize_query(expected.get("source_name"))
    expected_document = normalize_query(expected.get("bank_document"))
    expected_date = normalize_query(expected.get("received_on"))
    current_source_name = normalize_query(movement.source_name)
    current_document = normalize_query(movement.bank_document)
    current_date = movement.movement_date.isoformat() if movement.movement_date else ""
    if current_amount != expected_amount:
        reasons.append("valor")
    if normalize_match_name(current_source_name) != normalize_match_name(expected_source_name):
        reasons.append("nome")
    if normalize_query(current_document) != normalize_query(expected_document):
        reasons.append("documento")
    if current_date != expected_date:
        reasons.append("data")
    if not reasons:
        return None
    contribution = NativeContribution.objects.filter(legacy_id=int(movement.imported_contribution_legacy_id or 0)).first()
    active_receipts = [
        {
            "id": int(item.receipt.legacy_id or 0),
            "number": item.receipt.receipt_number or "",
            "total": str(item.receipt.total_value or ""),
            "person_name": item.receipt.person_name or "",
        }
        for item in ReceiptItemSnapshot.objects.select_related("receipt")
        .filter(contribution_legacy_id=int(movement.imported_contribution_legacy_id or 0), receipt__is_cancelled=False)
        .order_by("receipt__legacy_id")
    ]
    return StatementSaneamentoDifference(
        movement_id=int(movement.id or 0),
        order_in_lot=int(movement.order_in_lot or 0),
        current_amount=current_amount,
        expected_amount=expected_amount,
        current_source_name=current_source_name,
        expected_source_name=expected_source_name,
        current_document=current_document,
        expected_document=expected_document,
        current_date=current_date,
        expected_date=expected_date,
        imported_contribution_id=int(movement.imported_contribution_legacy_id or 0),
        contribution_person_id=int(contribution.person_legacy_id or 0) if contribution is not None else 0,
        contribution_person_name=contribution.contributor_name if contribution is not None else "",
        active_receipts=active_receipts,
        reasons=reasons,
    )


def _movement_from_native_contribution(
    *,
    lot: StatementImportPilotLot,
    contribution: NativeContribution,
    order_in_lot: int,
) -> StatementImportPilotMovement:
    note_fields = _native_contribution_note_fields(contribution.notes)
    received_on = contribution.received_at.isoformat() if contribution.received_at else ""
    movement_kind = note_fields.get("tipo") or "pix"
    source_name = note_fields.get("origem") or contribution.contributor_name
    document = note_fields.get("docto") or contribution.contributor_document
    raw_text = contribution.notes or ""
    fingerprint = statement_entry_fingerprint(
        received_on,
        contribution.amount,
        source_name,
        document,
        1,
        order_in_lot,
        movement_kind,
    )
    return StatementImportPilotMovement(
        lot=lot,
        source_movement_id=None,
        page_number=1,
        order_in_lot=int(order_in_lot or 0),
        movement_date=contribution.received_at,
        competence=contribution.competence or "",
        competence_order=int(contribution.competence_order or 0),
        amount=contribution.amount,
        cent_code=note_fields.get("centavos") or "",
        movement_kind=movement_kind,
        receiving_code="PIX" if "pix" in normalize_query(movement_kind).lower() else movement_kind,
        bank_document=document,
        document_type="cpf" if "*" in document or len("".join(ch for ch in document if ch.isdigit())) == 11 else "",
        prefix=movement_kind,
        source_name=source_name,
        source_name_normalized=normalize_match_name(source_name),
        origin_label=source_name,
        confidence="reconstruido",
        match_score=Decimal("0"),
        suggested_person_legacy_id=contribution.person_legacy_id,
        resolved_person_legacy_id=contribution.person_legacy_id,
        suggested_contributor_legacy_id=contribution.contributor_legacy_id,
        resolved_contributor_legacy_id=contribution.contributor_legacy_id,
        review_status="importado",
        review_notes="Movimento reconstruido a partir da contribuicao nativa para saneamento do lote.",
        imported_contribution_legacy_id=int(contribution.legacy_id or 0),
        duplicate_movement_legacy_id=None,
        duplicate_contribution_legacy_id=None,
        duplicate_reason="",
        fingerprint=fingerprint,
        signature_global=fingerprint,
        raw_text=raw_text,
        metadata={
            "organizacao_id": int(contribution.organization_id or 0),
            "resolved_tipo_contribuicao_id": int(contribution.contribution_type_legacy_id or 0),
            "tipo_sugerido": contribution.contribution_type_name or "",
            "regra_id": "",
            "association_reviewed": True,
            "reconstructed_from_contribution_id": int(contribution.legacy_id or 0),
        },
    )


def _native_contribution_note_fields(notes: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for part in str(notes or "").split("|"):
        if ":" not in part:
            continue
        key, value = part.split(":", 1)
        normalized_key = normalize_match_name(key).replace(" ", "_")
        fields[normalized_key] = normalize_query(value)
    return fields


def _apply_expected_entry_to_movement(
    movement: StatementImportPilotMovement,
    expected: dict[str, Any],
    *,
    actor: str,
) -> None:
    metadata = dict(movement.metadata or {})
    history = list(metadata.get("parser_saneamento_history") or [])
    history.append(
        {
            "at": timezone.localtime().isoformat(timespec="seconds"),
            "actor": actor,
            "before": {
                "movement_date": movement.movement_date.isoformat() if movement.movement_date else "",
                "amount": str(movement.amount or ""),
                "source_name": movement.source_name or "",
                "bank_document": movement.bank_document or "",
                "raw_text": movement.raw_text or "",
            },
            "after": {
                "movement_date": normalize_query(expected.get("received_on")),
                "amount": str(expected.get("amount") or ""),
                "source_name": normalize_query(expected.get("source_name")),
                "bank_document": normalize_query(expected.get("bank_document")),
                "raw_text": normalize_query(expected.get("raw_text")),
            },
        }
    )
    metadata["parser_saneamento_history"] = history[-10:]
    metadata["association_reviewed"] = False
    metadata["saneamento_parser"] = True
    metadata["last_actor"] = actor
    movement.page_number = int(expected.get("page_number") or 1)
    movement.movement_date = _parse_iso_date(expected.get("received_on"))
    movement.competence = normalize_query(expected.get("competencia"))
    movement.competence_order = int(expected.get("competencia_ordem") or 0)
    movement.amount = Decimal(str(expected.get("amount") or 0))
    movement.cent_code = normalize_query(expected.get("cent_code"))
    movement.movement_kind = normalize_query(expected.get("movement_kind"))
    movement.receiving_code = normalize_query(expected.get("receiving_code"))
    movement.bank_document = normalize_query(expected.get("bank_document"))
    movement.document_type = normalize_query(expected.get("document_type"))
    movement.prefix = normalize_query(expected.get("prefix"))
    movement.source_name = normalize_query(expected.get("source_name"))
    movement.source_name_normalized = normalize_match_name(expected.get("source_name"))
    movement.origin_label = normalize_query(expected.get("origin_label"))
    movement.raw_text = normalize_query(expected.get("raw_text"))
    movement.signature_global = normalize_query(expected.get("signature_global"))
    movement.fingerprint = normalize_query(expected.get("fingerprint"))
    movement.suggested_person_legacy_id = None
    movement.resolved_person_legacy_id = None
    movement.suggested_contributor_legacy_id = None
    movement.resolved_contributor_legacy_id = None
    movement.duplicate_contribution_legacy_id = None
    movement.duplicate_movement_legacy_id = None
    movement.duplicate_reason = ""
    movement.confidence = ""
    movement.metadata = metadata

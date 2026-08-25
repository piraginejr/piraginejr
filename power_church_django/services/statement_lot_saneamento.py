from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from django.db import transaction
from django.utils import timezone

from power_church_core.normalization import normalize_match_name, normalize_query
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


DEFAULT_CORRECTION_SUBJECT = "Correção de recibo de contribuição - {receipt_number}"
DEFAULT_CORRECTION_BODY = """Prezado(a) {person_name},

Identificamos uma divergência no processamento automático de um extrato bancário e, por isso, o recibo enviado anteriormente pode ter apresentado valor incorreto.

Pedimos desculpas pelo transtorno. A contribuição foi conferida novamente no extrato original e segue em anexo o recibo corrigido, referente a {period_label}, no valor total de {total_fmt}.

Por gentileza, desconsidere o recibo anterior relacionado a esse lançamento. Esta correção não altera a sua contribuição; ela apenas ajusta o registro e o recibo emitido pelo sistema.

Seguimos à disposição para qualquer conferência adicional.

Atenciosamente,
Tesouraria / Primeira Igreja Batista de Niterói
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
            _apply_expected_entry_to_movement(movement, expected, actor=actor)
            _apply_native_statement_resolution(
                movement,
                preserve_manual_selection=False,
                matching_cache=matching_cache,
            )
            movement.save()
            contribution_id = _statement_sync_native_contribution_for_movement(movement, actor=actor)
            if contribution_id:
                updated_contribution_ids.append(int(contribution_id))
        _refresh_native_statement_lot_metadata(lot)
    eligible_ids = _statement_receipt_eligible_native_contribution_ids(contribution_ids=updated_contribution_ids)
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
            },
        )
        queued_dispatch_ids.extend(int(dispatch.pk or 0) for dispatch in dispatches)
    return {
        "analysis": analysis,
        "cancelled_receipt_ids": cancelled_receipt_ids,
        "updated_contribution_ids": sorted(set(updated_contribution_ids)),
        "new_receipt_ids": new_receipt_ids,
        "queued_dispatch_ids": queued_dispatch_ids,
        "sent_now": len(queued_dispatch_ids) if send_now else 0,
        "without_email": without_email,
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
                f"- Novos recibos: `{payload.get('new_receipt_ids', [])}`",
                f"- Envios enfileirados: `{payload.get('queued_dispatch_ids', [])}`",
                f"- Sem e-mail: `{payload.get('without_email', 0)}`",
                "",
            ]
        )
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

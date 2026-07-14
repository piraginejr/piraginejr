from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
import time
from typing import Any

from django.conf import settings
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from power_church_core.formatting import br_date
from power_church_core.normalization import normalize_query
from power_church_django.apps.contributions.models import (
    ReceiptDispatch,
    ReceiptEmailTemplate,
    ReceiptItemSnapshot,
    ReceiptSnapshot,
)
from power_church_django.apps.people.models import PersonContributionSnapshot, PersonSnapshot
from power_church_core.normalization import format_cpf
from power_church_django.services.django_audit import record_django_audit_event
from power_church_django.services.runtime_errors import LegacyWriteError
from power_church_django.services.runtime_formatting import format_status, status_sigla
from power_church_django.services.runtime_support import preferred_delivery_email
from power_church_django.services.mail_dispatch import (
    MailAttachment,
    MailDispatchError,
    configured_provider,
    send_email_message,
)
from power_church_django.services.pdf_reports import receipt_pdf, receipt_pdf_filename


DEFAULT_TEMPLATE_KEY = "receipt_default"
DEFAULT_TEMPLATE_NAME = "Recibo mensal de contribuicoes"
DEFAULT_SUBJECT_TEMPLATE = "[TESTE] Recibo de contribuicoes - {person_name} - {period_label}"
DEFAULT_BODY_TEMPLATE = """Prezado(a) {person_name},

Segue em anexo o recibo de contribuicoes referente a {period_label}, no valor total de {total_fmt}.

Esta operacao ainda esta em fase de teste e implantacao gradual, lote a lote.

Se houver qualquer divergencia, por favor entre em contato com a tesouraria para conferencia.

Estamos implantando este processo por etapas e, quando a validacao estiver concluida, cada nova contribuicao passara a gerar um novo recibo automaticamente.

Atenciosamente,
Tesouraria / Recebimento
"""


@dataclass
class ReceiptRenderContext:
    detail: dict[str, Any]
    subject: str
    body: str
    to_email: str


def receipt_email_enabled() -> bool:
    return bool(getattr(settings, "POWER_CHURCH_RECEIPT_AUTO_EMAIL_ENABLED", True))


def receipt_auto_send_enabled() -> bool:
    return bool(getattr(settings, "POWER_CHURCH_RECEIPT_AUTO_SEND_ENABLED", True))


def _parse_iso_date(value: object) -> date | None:
    raw = normalize_query(value)
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


def _money(value: object) -> str:
    return f"R$ {float(value or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def sync_receipt_snapshots(
    *,
    receipt_ids: list[int] | None = None,
    person_ids: list[int] | None = None,
) -> list[ReceiptSnapshot]:
    clean_receipt_ids = [int(value or 0) for value in (receipt_ids or []) if int(value or 0)]
    clean_person_ids = [int(value or 0) for value in (person_ids or []) if int(value or 0)]
    if not clean_receipt_ids and not clean_person_ids:
        return []
    queryset = ReceiptSnapshot.objects.all()
    if clean_receipt_ids:
        queryset = queryset.filter(legacy_id__in=clean_receipt_ids)
    if clean_person_ids:
        queryset = queryset.filter(person_legacy_id__in=clean_person_ids)
    return list(queryset.order_by("-emission_date", "-legacy_id"))


def get_receipt_detail_snapshot(receipt_id: int) -> dict[str, Any] | None:
    snapshot = (
        ReceiptSnapshot.objects.filter(legacy_id=int(receipt_id or 0))
        .prefetch_related("items")
        .first()
    )
    if snapshot is None:
        return None
    return {
        "receipt": {
            "id": int(snapshot.legacy_id or 0),
            "numero": snapshot.receipt_number or "",
            "status": snapshot.status or "",
            "organization_id": int(snapshot.organization_id or 0),
            "organizacao": snapshot.organization_name or "",
            "person_id": int(snapshot.person_legacy_id or 0),
            "person_name": snapshot.person_name or "",
            "person_code": snapshot.person_code or "",
            "person_cpf": snapshot.person_cpf or "",
            "person_email": snapshot.person_email or "",
            "person_phone": snapshot.person_phone or "",
            "data": br_date(snapshot.emission_date_raw or (snapshot.emission_date.isoformat() if snapshot.emission_date else "")),
            "data_raw": snapshot.emission_date_raw or "",
            "periodo_inicio": br_date(snapshot.period_start_raw or (snapshot.period_start.isoformat() if snapshot.period_start else "")),
            "periodo_inicio_raw": snapshot.period_start_raw or "",
            "periodo_fim": br_date(snapshot.period_end_raw or (snapshot.period_end.isoformat() if snapshot.period_end else "")),
            "periodo_fim_raw": snapshot.period_end_raw or "",
            "valor_fmt": _money(snapshot.total_value),
            "valor_total": round(float(snapshot.total_value or 0), 2),
            "observacoes": snapshot.notes or "",
        },
        "person": {
            "id": int(snapshot.person_legacy_id or 0),
            "nome": snapshot.person_name or "",
            "codigo": snapshot.person_code or "",
            "cpf": snapshot.person_cpf or "",
            "email": snapshot.person_email or "",
            "telefone": snapshot.person_phone or "",
        },
        "items": [
            {
                "contribution_id": int(item.contribution_legacy_id or 0),
                "data": br_date(item.received_at_raw or (item.received_at.isoformat() if item.received_at else "")),
                "competencia": item.competence or "",
                "tipo": item.contribution_type_name or "",
                "forma": item.receipt_method_name or "",
                "observacoes": item.notes or "",
                "valor_fmt": _money(item.amount),
            }
            for item in snapshot.items.all().order_by("received_at", "legacy_id")
        ],
    }


def get_receipt_detail_cached(receipt_id: int) -> dict[str, Any] | None:
    return get_receipt_detail_snapshot(receipt_id)


def get_or_create_receipt_email_template() -> ReceiptEmailTemplate:
    template = ReceiptEmailTemplate.objects.filter(key=DEFAULT_TEMPLATE_KEY).order_by("id").first()
    if template:
        changed = False
        if not normalize_query(template.subject_template):
            template.subject_template = DEFAULT_SUBJECT_TEMPLATE
            changed = True
        if not normalize_query(template.body_template):
            template.body_template = DEFAULT_BODY_TEMPLATE
            changed = True
        if not normalize_query(template.name):
            template.name = DEFAULT_TEMPLATE_NAME
            changed = True
        if template.reply_to_email is None:
            template.reply_to_email = ""
            changed = True
        if template.default_from_email is None:
            template.default_from_email = ""
            changed = True
        if changed:
            template.save(
                update_fields=[
                    "name",
                    "subject_template",
                    "body_template",
                    "default_from_email",
                    "reply_to_email",
                    "updated_at",
                ]
            )
        return template
    return ReceiptEmailTemplate.objects.create(
        key=DEFAULT_TEMPLATE_KEY,
        name=DEFAULT_TEMPLATE_NAME,
        subject_template=DEFAULT_SUBJECT_TEMPLATE,
        body_template=DEFAULT_BODY_TEMPLATE,
        default_from_email="",
        reply_to_email="",
        active=True,
    )


def update_receipt_email_template(
    *,
    subject_template: str,
    body_template: str,
    default_from_email: str = "",
    reply_to_email: str = "",
    actor: str = "",
) -> ReceiptEmailTemplate:
    template = get_or_create_receipt_email_template()
    before = {
        "subject_template": template.subject_template,
        "body_template": template.body_template,
        "default_from_email": template.default_from_email,
        "reply_to_email": template.reply_to_email,
    }
    template.subject_template = normalize_query(subject_template) or DEFAULT_SUBJECT_TEMPLATE
    template.body_template = str(body_template or "").strip() or DEFAULT_BODY_TEMPLATE
    template.default_from_email = normalize_query(default_from_email)
    template.reply_to_email = normalize_query(reply_to_email)
    template.save(
        update_fields=[
            "subject_template",
            "body_template",
            "default_from_email",
            "reply_to_email",
            "updated_at",
        ]
    )
    try:
        record_django_audit_event(
            actor=actor,
            action="atualizar_modelo_email_recibo_django",
            table_name="receipt_email_template",
            record_id=int(template.pk or 0),
            source="receipt_email",
            summary="Modelo padrao de e-mail de recibo atualizado",
            before=before,
            after={
                "subject_template": template.subject_template,
                "body_template": template.body_template,
                "default_from_email": template.default_from_email,
                "reply_to_email": template.reply_to_email,
            },
        )
    except Exception:
        pass
    return template


def receipt_period_options(person_id: int) -> list[dict[str, Any]]:
    person_id = int(person_id or 0)
    if not person_id:
        return []
    contributions = list(
        PersonContributionSnapshot.objects.filter(person__legacy_id=person_id, is_active=True).order_by(
            "-competence_order", "-received_at", "-legacy_id"
        )
    )
    if not contributions:
        return []
    grouped: dict[str, dict[str, Any]] = {}
    for item in contributions:
        key = item.competence or ""
        bucket = grouped.setdefault(
            key,
            {
                "competencia": item.competence or "Sem competencia",
                "competencia_key": key,
                "periodo_inicio_raw": item.received_at_raw or "",
                "periodo_fim_raw": item.received_at_raw or "",
                "quantidade": 0,
                "total": 0.0,
                "competencia_ordem": int(item.competence_order or 0),
            },
        )
        bucket["quantidade"] += 1
        bucket["total"] = round(float(bucket["total"]) + float(item.amount or 0), 2)
        if item.received_at_raw and (not bucket["periodo_inicio_raw"] or item.received_at_raw < bucket["periodo_inicio_raw"]):
            bucket["periodo_inicio_raw"] = item.received_at_raw
        if item.received_at_raw and (not bucket["periodo_fim_raw"] or item.received_at_raw > bucket["periodo_fim_raw"]):
            bucket["periodo_fim_raw"] = item.received_at_raw
    receipt_by_competence: dict[str, dict[str, Any]] = {}
    receipt_items = (
        ReceiptItemSnapshot.objects.select_related("receipt")
        .filter(receipt__person_legacy_id=person_id, receipt__is_cancelled=False)
        .order_by("-receipt__emission_date", "-receipt__legacy_id", "legacy_id")
    )
    for item in receipt_items:
        competence = item.competence or ""
        if competence in receipt_by_competence:
            continue
        receipt_by_competence[competence] = {
            "id": int(item.receipt.legacy_id or 0),
            "numero": item.receipt.receipt_number or "",
            "data": br_date(item.receipt.emission_date_raw or (item.receipt.emission_date.isoformat() if item.receipt.emission_date else "")),
            "valor_fmt": _money(item.receipt.total_value),
        }
    rows = sorted(
        grouped.values(),
        key=lambda item: (int(item["competencia_ordem"]), str(item["competencia_key"] or "")),
        reverse=True,
    )
    return [
        {
            "competencia": row["competencia"],
            "competencia_key": row["competencia_key"],
            "periodo_inicio": br_date(row["periodo_inicio_raw"]),
            "periodo_fim": br_date(row["periodo_fim_raw"]),
            "quantidade": int(row["quantidade"]),
            "total": round(float(row["total"]), 2),
            "total_fmt": _money(row["total"]),
            "active_receipt": receipt_by_competence.get(str(row["competencia_key"] or "")),
        }
        for row in rows
    ]


def receipt_dispatch_history(person_id: int, limit: int = 20) -> list[dict[str, Any]]:
    history = ReceiptDispatch.objects.filter(legacy_person_id=int(person_id or 0)).order_by("-created_at", "-id")[:limit]
    return [
        {
            "id": int(item.pk or 0),
            "period_label": item.period_label,
            "receipt_number": item.legacy_receipt_number,
            "status": item.get_status_display(),
            "email_to": item.email_to or item.person_email,
            "sent_at": timezone.localtime(item.sent_at).strftime("%d/%m/%Y %H:%M") if item.sent_at else "",
            "updated_at": timezone.localtime(item.updated_at).strftime("%d/%m/%Y %H:%M"),
            "trigger": item.get_trigger_display(),
            "last_error": item.last_error,
        }
        for item in history
    ]


def receipt_person_snapshot(person_id: int) -> dict[str, Any] | None:
    person_id = int(person_id or 0)
    if not person_id:
        return None
    snapshot = PersonSnapshot.objects.filter(legacy_id=person_id, is_active=True).first()
    if snapshot is None:
        return None
    return {
        "id": int(snapshot.legacy_id or 0),
        "nome": snapshot.name or "",
        "codigo": snapshot.internal_code or "",
        "cpf": format_cpf(snapshot.cpf),
        "status": format_status(snapshot.status),
        "sigla": status_sigla(snapshot.status, True),
        "email": preferred_delivery_email(snapshot.primary_email, snapshot.name),
        "telefone": snapshot.primary_phone or "",
    }


def enrich_receipt_form(form_data: dict[str, Any] | None, *, selected_competences: list[str] | None = None) -> dict[str, Any] | None:
    if not form_data:
        return None
    selected = {normalize_query(value) for value in (selected_competences or []) if normalize_query(value)}
    person = form_data.get("person") or {}
    template = get_or_create_receipt_email_template()
    period_options = receipt_period_options(int(person.get("id") or 0))
    for option in period_options:
        option["selected"] = normalize_query(option["competencia_key"]) in selected
    form_data["period_options"] = period_options
    form_data["email_to_default"] = person.get("email") or ""
    form_data["email_template_subject"] = template.subject_template
    form_data["email_template_body"] = template.body_template
    form_data["email_default_from"] = template.default_from_email or getattr(settings, "DEFAULT_FROM_EMAIL", "")
    form_data["email_reply_to"] = template.reply_to_email or getattr(settings, "POWER_CHURCH_RECEIPT_REPLY_TO", "")
    form_data["dispatch_history"] = receipt_dispatch_history(int(person.get("id") or 0))
    return form_data


def _render_template_value(template_value: str, detail: dict[str, Any]) -> str:
    receipt = detail.get("receipt") or {}
    values = {
        "person_name": receipt.get("person_name") or "",
        "receipt_number": receipt.get("numero") or "",
        "period_label": f"{receipt.get('periodo_inicio') or ''} a {receipt.get('periodo_fim') or ''}".strip(),
        "period_start": receipt.get("periodo_inicio") or "",
        "period_end": receipt.get("periodo_fim") or "",
        "total_fmt": receipt.get("valor_fmt") or "",
        "organization_name": receipt.get("organizacao") or "Power Church",
    }
    try:
        return str(template_value or "").format(**values).strip()
    except Exception:
        return str(template_value or "").strip()


def _dispatch_defaults(detail: dict[str, Any], *, email_to: str = "", subject: str = "", body: str = "") -> ReceiptRenderContext:
    template = get_or_create_receipt_email_template()
    rendered_subject = _render_template_value(subject or template.subject_template, detail)
    rendered_body = _render_template_value(body or template.body_template, detail)
    rendered_email = (
        preferred_delivery_email(email_to, (detail.get("receipt") or {}).get("person_name"))
        or preferred_delivery_email((detail.get("person") or {}).get("email"), (detail.get("receipt") or {}).get("person_name"))
        or preferred_delivery_email((detail.get("receipt") or {}).get("person_email"), (detail.get("receipt") or {}).get("person_name"))
    )
    return ReceiptRenderContext(detail=detail, subject=rendered_subject, body=rendered_body, to_email=rendered_email)


def queue_receipt_dispatches(
    receipt_ids: list[int],
    *,
    email_to: str,
    subject: str = "",
    body: str = "",
    actor: str = "",
    trigger: str = ReceiptDispatch.Trigger.MANUAL,
    auto_created: bool = False,
    send_now: bool = False,
    metadata_extra: dict[str, Any] | None = None,
) -> list[ReceiptDispatch]:
    dispatches: list[ReceiptDispatch] = []
    for receipt_id in [int(value or 0) for value in receipt_ids if int(value or 0)]:
        detail = get_receipt_detail_cached(receipt_id)
        if not detail:
            continue
        rendered = _dispatch_defaults(detail, email_to=email_to, subject=subject, body=body)
        receipt = detail.get("receipt") or {}
        metadata = {
            "items": [item.get("contribution_id") for item in detail.get("items") or []],
            "total_itens": len(detail.get("items") or []),
        }
        if metadata_extra:
            metadata.update(metadata_extra)
        dispatch = ReceiptDispatch.objects.create(
            organization_id=int(receipt.get("organization_id") or 0) or None,
            legacy_person_id=int(receipt.get("person_id") or 0),
            legacy_receipt_id=int(receipt.get("id") or 0),
            legacy_receipt_number=receipt.get("numero") or "",
            person_name=receipt.get("person_name") or "",
            person_email=rendered.to_email,
            competence=normalize_query((detail.get("items") or [{}])[0].get("competencia") if detail.get("items") else ""),
            period_label=f"{receipt.get('periodo_inicio') or ''} a {receipt.get('periodo_fim') or ''}".strip(),
            period_start=_parse_iso_date(_raw_date(receipt.get("periodo_inicio"))),
            period_end=_parse_iso_date(_raw_date(receipt.get("periodo_fim"))),
            mode=ReceiptDispatch.Mode.COMPETENCE,
            trigger=trigger,
            status=ReceiptDispatch.Status.PENDING,
            auto_created=auto_created,
            email_to=rendered.to_email,
            email_subject=rendered.subject,
            email_body=rendered.body,
            metadata=metadata,
        )
        dispatches.append(dispatch)
        try:
            record_django_audit_event(
                actor=actor,
                action="enfileirar_envio_recibo_django",
                table_name="receipt_dispatch",
                record_id=int(dispatch.pk or 0),
                organization_id=dispatch.organization_id,
                source="receipt_email",
                summary=f"Recibo {dispatch.legacy_receipt_number or dispatch.period_label} enfileirado para envio",
                after={
                    "receipt_id": dispatch.legacy_receipt_id,
                    "email_to": dispatch.email_to,
                    "trigger": dispatch.trigger,
                    "status": dispatch.status,
                },
            )
        except Exception:
            pass
        if send_now:
            send_receipt_dispatch(dispatch, actor=actor)
    return dispatches


def ensure_receipt_dispatches_for_receipt_ids(
    receipt_ids: list[int],
    *,
    actor: str = "",
    trigger: str = ReceiptDispatch.Trigger.AUTOMATIC,
    auto_created: bool = True,
    send_now: bool = False,
    metadata_extra: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    outcomes: list[dict[str, Any]] = []
    clean_ids = [int(value or 0) for value in receipt_ids if int(value or 0)]
    for receipt_id in clean_ids:
        detail = get_receipt_detail_cached(receipt_id)
        if not detail:
            outcomes.append({"status": "erro", "receipt_id": receipt_id, "error": "recibo_nao_encontrado"})
            continue
        receipt = detail.get("receipt") or {}
        person = detail.get("person") or {}
        existing = (
            ReceiptDispatch.objects.filter(legacy_receipt_id=receipt_id)
            .exclude(status=ReceiptDispatch.Status.CANCELLED)
            .order_by("-created_at", "-id")
            .first()
        )
        if existing is not None:
            outcomes.append(
                {
                    "status": "existing_dispatch",
                    "receipt_id": receipt_id,
                    "dispatch_id": int(existing.pk or 0),
                    "person_id": int(receipt.get("person_id") or 0),
                    "person_name": receipt.get("person_name") or "",
                }
            )
            continue
        email_to = preferred_delivery_email(person.get("email"), receipt.get("person_name")) or preferred_delivery_email(
            receipt.get("person_email"),
            receipt.get("person_name"),
        )
        if not email_to:
            outcomes.append(
                {
                    "status": "sem_email",
                    "receipt_id": receipt_id,
                    "person_id": int(receipt.get("person_id") or 0),
                    "person_name": receipt.get("person_name") or "",
                }
            )
            continue
        dispatches = queue_receipt_dispatches(
            [receipt_id],
            email_to=email_to,
            subject="",
            body="",
            actor=actor,
            trigger=trigger,
            auto_created=auto_created,
            send_now=send_now,
            metadata_extra=metadata_extra,
        )
        dispatch = dispatches[0] if dispatches else None
        outcomes.append(
            {
                "status": "queued_existing_receipt",
                "receipt_id": receipt_id,
                "dispatch_id": int(dispatch.pk or 0) if dispatch is not None else 0,
                "person_id": int(receipt.get("person_id") or 0),
                "person_name": receipt.get("person_name") or "",
            }
        )
    return outcomes


def _campaign_full_receipt_sets(
    *,
    cutoff_date: str = "",
) -> tuple[dict[int, dict[str, Any]], dict[int, list[dict[str, Any]]], dict[int, list[dict[str, Any]]]]:
    cutoff = normalize_query(cutoff_date)
    contribution_qs = PersonContributionSnapshot.objects.filter(
        person__is_active=True,
        is_active=True,
    ).exclude(person__primary_email="")
    if cutoff:
        contribution_qs = contribution_qs.filter(received_at_raw__lte=cutoff)
    receipt_item_qs = ReceiptItemSnapshot.objects.select_related("receipt").filter(receipt__is_cancelled=False)
    if cutoff:
        receipt_item_qs = receipt_item_qs.filter(received_at_raw__lte=cutoff)
    people: dict[int, dict[str, Any]] = {}
    receipt_sets: dict[int, list[dict[str, Any]]] = {}
    contributions_by_person: dict[int, list[dict[str, Any]]] = {}
    for row in contribution_qs.order_by("person__name", "received_at", "legacy_id"):
        person_id = int(row.person.legacy_id or 0)
        if not person_id:
            continue
        contributions_by_person.setdefault(person_id, []).append(
            {
                "id": int(row.legacy_id or 0),
                "value": round(float(row.amount or 0), 2),
                "date": row.received_at_raw or "",
            }
        )
        people.setdefault(
            person_id,
            {
                "person_id": person_id,
                "person_name": row.person.name or "",
                "email": preferred_delivery_email(row.person.primary_email, row.person.name),
            },
        )
    for row in receipt_item_qs.order_by("receipt__person_legacy_id", "receipt__legacy_id", "legacy_id"):
        person_id = int(row.receipt.person_legacy_id or 0)
        receipt_id = int(row.receipt.legacy_id or 0)
        if not person_id or not receipt_id:
            continue
        bucket = receipt_sets.setdefault(person_id, [])
        current = next((item for item in bucket if int(item["receipt_id"]) == receipt_id), None)
        if current is None:
            current = {
                "receipt_id": receipt_id,
                "receipt_number": row.receipt.receipt_number or "",
                "emission_date": row.receipt.emission_date_raw or "",
                "contribution_ids": [],
            }
            bucket.append(current)
        current["contribution_ids"].append(int(row.contribution_legacy_id or 0))
    return people, contributions_by_person, receipt_sets


def consolidated_receipt_campaign_candidates(
    *,
    cutoff_date: str = "",
    limit: int = 0,
) -> list[dict[str, Any]]:
    people, contributions_by_person, receipt_sets = _campaign_full_receipt_sets(cutoff_date=cutoff_date)
    candidates: list[dict[str, Any]] = []
    for person_id, person in people.items():
        contributions = contributions_by_person.get(person_id, [])
        if not contributions:
            continue
        full_ids = sorted(int(item["id"]) for item in contributions)
        full_id_set = set(full_ids)
        total_value = round(sum(float(item["value"] or 0) for item in contributions), 2)
        existing_receipts = receipt_sets.get(person_id, [])
        matching_receipt = next(
            (
                item
                for item in existing_receipts
                if set(int(value or 0) for value in item.get("contribution_ids", [])) == full_id_set
            ),
            None,
        )
        latest_dispatch = None
        if matching_receipt:
            latest_dispatch = (
                ReceiptDispatch.objects.filter(legacy_receipt_id=int(matching_receipt["receipt_id"] or 0))
                .order_by("-created_at", "-id")
                .first()
            )
        if latest_dispatch and latest_dispatch.status == ReceiptDispatch.Status.SENT:
            action = "already_sent"
        elif latest_dispatch and latest_dispatch.status == ReceiptDispatch.Status.PENDING:
            action = "already_queued"
        elif latest_dispatch and latest_dispatch.status == ReceiptDispatch.Status.FAILED:
            action = "retry_existing"
        elif matching_receipt:
            action = "queue_existing"
        else:
            action = "generate_and_queue"
        candidates.append(
            {
                "person_id": person_id,
                "person_name": person["person_name"],
                "email": person["email"],
                "contribution_count": len(full_ids),
                "contribution_ids": full_ids,
                "total_value": total_value,
                "period_start": min(str(item["date"] or "") for item in contributions),
                "period_end": max(str(item["date"] or "") for item in contributions),
                "matching_receipt_id": int(matching_receipt["receipt_id"] or 0) if matching_receipt else 0,
                "matching_receipt_number": matching_receipt["receipt_number"] if matching_receipt else "",
                "latest_dispatch_id": int(latest_dispatch.pk or 0) if latest_dispatch else 0,
                "latest_dispatch_status": str(latest_dispatch.status or "") if latest_dispatch else "",
                "action": action,
            }
        )
    candidates.sort(key=lambda item: (str(item["person_name"]).casefold(), int(item["person_id"])))
    if int(limit or 0) > 0:
        return candidates[: int(limit)]
    return candidates


def consolidated_receipt_campaign_summary(*, cutoff_date: str = "") -> dict[str, Any]:
    candidates = consolidated_receipt_campaign_candidates(cutoff_date=cutoff_date)
    summary = {
        "cutoff_date": normalize_query(cutoff_date) or "",
        "total_people": len(candidates),
        "generate_and_queue": 0,
        "queue_existing": 0,
        "retry_existing": 0,
        "already_queued": 0,
        "already_sent": 0,
    }
    for item in candidates:
        action = str(item["action"] or "")
        if action in summary:
            summary[action] += 1
    summary["ready_to_queue"] = summary["generate_and_queue"] + summary["queue_existing"] + summary["retry_existing"]
    return {"summary": summary, "items": candidates}


def prepare_consolidated_receipt_campaign(
    *,
    cutoff_date: str = "",
    emission_date: str = "",
    actor: str = "",
    limit: int = 0,
) -> dict[str, Any]:
    cutoff = normalize_query(cutoff_date)
    emission = normalize_query(emission_date) or date.today().isoformat()
    campaign_key = f"retroativo_consolidado:{cutoff or emission}"
    snapshot = consolidated_receipt_campaign_summary(cutoff_date=cutoff)
    prepared: list[dict[str, Any]] = []
    created = 0
    reused = 0
    retried = 0
    skipped = 0
    for item in snapshot["items"]:
        if int(limit or 0) > 0 and len(prepared) >= int(limit):
            break
        action = str(item["action"] or "")
        if action not in {"generate_and_queue", "queue_existing", "retry_existing"}:
            skipped += 1
            continue
        receipt_id = int(item["matching_receipt_id"] or 0)
        if action == "generate_and_queue":
            receipt_id = issue_receipt_for_contribution_ids(
                person_id=int(item["person_id"] or 0),
                contribution_ids=[int(value or 0) for value in item["contribution_ids"]],
                emission_date=emission,
                notes=f"Recibo consolidado retroativo preparado em campanha ate {cutoff or emission}.",
                actor=actor,
                replace_existing=True,
                audit_action="gerar_recibo_consolidado_retroativo_django",
            )
            created += 1
        existing_dispatches = list(
            ReceiptDispatch.objects.filter(
                legacy_receipt_id=receipt_id,
                metadata__campaign_key=campaign_key,
            )
            .exclude(status=ReceiptDispatch.Status.CANCELLED)
            .order_by("-created_at", "-id")
        )
        if action == "queue_existing":
            reused += 1
        elif action == "retry_existing":
            retried += 1
        if existing_dispatches:
            dispatches = existing_dispatches[:1]
        else:
            dispatches = queue_receipt_dispatches(
                [receipt_id],
                email_to=str(item["email"] or ""),
                subject="",
                body="",
                actor=actor,
                trigger=ReceiptDispatch.Trigger.RETROACTIVE,
                auto_created=True,
                send_now=False,
                metadata_extra={
                    "campaign_key": campaign_key,
                    "campaign_mode": "retroativo_consolidado",
                    "campaign_cutoff_date": cutoff or emission,
                },
            )
        prepared.append(
            {
                **item,
                "receipt_id": receipt_id,
                "dispatch_ids": [int(dispatch.pk or 0) for dispatch in dispatches],
            }
        )
    return {
        "campaign_key": campaign_key,
        "cutoff_date": cutoff or emission,
        "emission_date": emission,
        "prepared": len(prepared),
        "created": created,
        "reused": reused,
        "retried": retried,
        "skipped": skipped,
        "items": prepared,
        "summary": snapshot["summary"],
    }


def dedupe_campaign_receipt_dispatches(*, campaign_key: str, actor: str = "") -> dict[str, int]:
    clean_key = normalize_query(campaign_key)
    if not clean_key:
        return {"receipts": 0, "cancelled": 0}
    rows = list(
        ReceiptDispatch.objects.filter(metadata__campaign_key=clean_key)
        .exclude(status=ReceiptDispatch.Status.CANCELLED)
        .order_by("legacy_receipt_id", "-created_at", "-id")
    )
    by_receipt: dict[int, list[ReceiptDispatch]] = {}
    for item in rows:
        by_receipt.setdefault(int(item.legacy_receipt_id or 0), []).append(item)
    kept_receipts = 0
    cancelled = 0
    priority = {
        ReceiptDispatch.Status.SENT: 3,
        ReceiptDispatch.Status.PENDING: 2,
        ReceiptDispatch.Status.FAILED: 1,
    }
    for receipt_id, items in by_receipt.items():
        if not receipt_id or not items:
            continue
        keep = sorted(
            items,
            key=lambda item: (
                priority.get(str(item.status or ""), 0),
                item.created_at or timezone.now(),
                int(item.pk or 0),
            ),
            reverse=True,
        )[0]
        kept_receipts += 1
        for item in items:
            if int(item.pk or 0) == int(keep.pk or 0):
                continue
            item.status = ReceiptDispatch.Status.CANCELLED
            item.last_error = "Fila duplicada cancelada automaticamente para evitar envio repetido na campanha consolidada."
            item.save(update_fields=["status", "last_error", "updated_at"])
            cancelled += 1
            try:
                record_django_audit_event(
                    actor=actor,
                    action="cancelar_envio_recibo_duplicado_django",
                    table_name="receipt_dispatch",
                    record_id=int(item.pk or 0),
                    organization_id=item.organization_id,
                    source="receipt_email",
                    summary=f"Fila duplicada do recibo {item.legacy_receipt_number or item.legacy_receipt_id} cancelada automaticamente",
                    after={"status": item.status, "erro": item.last_error},
                )
            except Exception:
                pass
    return {"receipts": kept_receipts, "cancelled": cancelled}


def _raw_date(value: object) -> str:
    raw = normalize_query(value)
    if len(raw) == 10 and raw[2] == "/" and raw[5] == "/":
        day, month, year = raw.split("/")
        return f"{year}-{month}-{day}"
    return raw


def _next_receipt_legacy_id() -> int:
    value = ReceiptSnapshot.objects.aggregate(value=models.Max("legacy_id")).get("value") or 0
    return int(value or 0) + 1


def _next_receipt_item_legacy_id() -> int:
    value = ReceiptItemSnapshot.objects.aggregate(value=models.Max("legacy_id")).get("value") or 0
    return int(value or 0) + 1


def _next_receipt_number_postgres(organization_id: int, emission_date: str) -> str:
    digits = "".join(ch for ch in str(emission_date or "") if ch.isdigit())
    prefix = f"REC-{digits[:6] or date.today().strftime('%Y%m')}"
    row = (
        ReceiptSnapshot.objects.filter(organization_id=int(organization_id or 0), receipt_number__startswith=f"{prefix}-")
        .order_by("-receipt_number")
        .values_list("receipt_number", flat=True)
        .first()
    )
    next_seq = 1
    if row:
        try:
            next_seq = int(str(row).split("-")[-1]) + 1
        except ValueError:
            next_seq = 1
    return f"{prefix}-{next_seq:04d}"


def _receipt_snapshot_rows_for_person(
    *,
    person_id: int,
    contribution_ids: list[int],
    allow_existing_receipts: bool = False,
) -> list[PersonContributionSnapshot]:
    clean_ids = sorted({int(value or 0) for value in contribution_ids if int(value or 0)})
    if not clean_ids:
        raise LegacyWriteError("Selecione pelo menos uma contribuicao para o recibo.")
    rows = list(
        PersonContributionSnapshot.objects.select_related("person")
        .filter(
            legacy_id__in=clean_ids,
            person__legacy_id=int(person_id or 0),
            person__is_active=True,
            is_active=True,
        )
        .order_by("received_at", "legacy_id")
    )
    if len(rows) != len(clean_ids):
        raise LegacyWriteError("Uma ou mais contribuicoes selecionadas nao pertencem a pessoa do recibo.")
    if not allow_existing_receipts:
        covered = set(
            int(value or 0)
            for value in ReceiptItemSnapshot.objects.filter(
                contribution_legacy_id__in=clean_ids,
                receipt__is_cancelled=False,
            ).values_list("contribution_legacy_id", flat=True)
            if int(value or 0)
        )
        if covered:
            raise LegacyWriteError("Ja existe recibo ativo para uma ou mais contribuicoes selecionadas.")
    return rows


def _cancel_active_receipt_snapshots_for_contribution_ids(
    contribution_ids: list[int],
    *,
    actor: str = "",
    reason: str = "",
) -> list[int]:
    clean_ids = sorted({int(value or 0) for value in contribution_ids if int(value or 0)})
    if not clean_ids:
        return []
    snapshots = list(
        ReceiptSnapshot.objects.filter(items__contribution_legacy_id__in=clean_ids, is_cancelled=False).distinct()
    )
    cancelled_ids: list[int] = []
    for snapshot in snapshots:
        snapshot.is_cancelled = True
        snapshot.status = "cancelado"
        if reason:
            notes = normalize_query(snapshot.notes)
            snapshot.notes = "\n".join(part for part in [notes, reason] if part)
        snapshot.save(update_fields=["is_cancelled", "status", "notes", "synced_at"])
        cancelled_ids.append(int(snapshot.legacy_id or 0))
        try:
            record_django_audit_event(
                actor=actor,
                action="cancelar_recibo_postgres",
                table_name="contributions_receiptsnapshot",
                record_id=int(snapshot.pk or 0),
                organization_id=int(snapshot.organization_id or 0),
                source="receipt_postgres",
                summary=f"Recibo {snapshot.receipt_number or snapshot.legacy_id} cancelado para reemissao",
                after={"status": snapshot.status, "is_cancelled": snapshot.is_cancelled, "reason": reason},
            )
        except Exception:
            pass
    if cancelled_ids:
        mark_receipt_dispatches_cancelled(cancelled_ids, actor=actor, reason=reason)
    return cancelled_ids


def cancel_receipts_for_contribution_ids(
    contribution_ids: list[int],
    *,
    actor: str = "",
    reason: str = "",
) -> list[int]:
    return _cancel_active_receipt_snapshots_for_contribution_ids(contribution_ids, actor=actor, reason=reason)


def issue_receipt_for_contribution_ids(
    *,
    person_id: int,
    contribution_ids: list[int],
    emission_date: str,
    notes: str,
    actor: str = "",
    replace_existing: bool = False,
    audit_action: str = "gerar_recibo_django",
) -> int:
    person = receipt_person_snapshot(int(person_id or 0))
    if not person:
        raise LegacyWriteError("Escolha uma pessoa valida para gerar o recibo.")
    emission_date = normalize_query(emission_date) or date.today().isoformat()
    emission = _parse_iso_date(emission_date)
    if emission is None:
        raise LegacyWriteError("Informe uma data de emissao valida para o recibo.")
    notes = normalize_query(notes)
    rows = _receipt_snapshot_rows_for_person(
        person_id=int(person_id or 0),
        contribution_ids=contribution_ids,
        allow_existing_receipts=replace_existing,
    )
    if replace_existing:
        _cancel_active_receipt_snapshots_for_contribution_ids(
            [int(row.legacy_id or 0) for row in rows],
            actor=actor,
            reason="Recibo anterior cancelado para reemissao consolidada.",
        )
    organization_id = int((rows[0].organization_id if rows else 0) or 0)
    period_start = min(row.received_at_raw or "" for row in rows)
    period_end = max(row.received_at_raw or "" for row in rows)
    total = round(sum(float(row.amount or 0) for row in rows), 2)
    with transaction.atomic():
        receipt = ReceiptSnapshot.objects.create(
            legacy_id=_next_receipt_legacy_id(),
            organization_id=organization_id,
            person_legacy_id=int(person["id"] or 0),
            receipt_number=_next_receipt_number_postgres(organization_id, emission_date),
            status="emitido",
            organization_name="Power Church",
            person_name=str(person.get("nome") or ""),
            person_code=str(person.get("codigo") or ""),
            person_cpf=str(person.get("cpf") or ""),
            person_email=str(person.get("email") or ""),
            person_phone=str(person.get("telefone") or ""),
            emission_date=emission,
            emission_date_raw=emission_date,
            period_start=_parse_iso_date(period_start),
            period_start_raw=period_start,
            period_end=_parse_iso_date(period_end),
            period_end_raw=period_end,
            total_value=Decimal(str(total)),
            notes=notes,
            is_cancelled=False,
        )
        next_item_id = _next_receipt_item_legacy_id()
        ReceiptItemSnapshot.objects.bulk_create(
            [
                ReceiptItemSnapshot(
                    legacy_id=next_item_id + index,
                    receipt=receipt,
                    contribution_legacy_id=int(row.legacy_id or 0),
                    contributor_legacy_id=int(row.contributor_legacy_id or 0) or None,
                    received_at=row.received_at,
                    received_at_raw=row.received_at_raw or "",
                    competence=row.competence or "",
                    contribution_type_name=row.contribution_type_name or "",
                    receipt_method_name=row.receipt_method_name or "",
                    notes=getattr(row, "notes", "") or "",
                    amount=Decimal(str(row.amount or 0)),
                )
                for index, row in enumerate(rows)
            ]
        )
    try:
        record_django_audit_event(
            actor=actor,
            action=audit_action,
            table_name="contributions_receiptsnapshot",
            record_id=int(receipt.pk or 0),
            organization_id=int(receipt.organization_id or 0),
            source="receipt_postgres",
            summary=f"Recibo {receipt.receipt_number or receipt.legacy_id} gerado no Postgres.",
            after={
                "receipt_id": int(receipt.legacy_id or 0),
                "person_id": int(receipt.person_legacy_id or 0),
                "items": [int(row.legacy_id or 0) for row in rows],
                "total": total,
                "period_start": period_start,
                "period_end": period_end,
            },
        )
    except Exception:
        pass
    return int(receipt.legacy_id or 0)


def issue_period_receipts(
    *,
    person_id: int,
    competences: list[str],
    emission_date: str,
    notes: str,
    actor: str = "",
    replace_existing: bool = False,
) -> list[int]:
    person_id = int(person_id or 0)
    clean_competences = [normalize_query(value) for value in competences if normalize_query(value)]
    if not person_id or not clean_competences:
        raise LegacyWriteError("Selecione pelo menos uma competencia para gerar recibos.")
    rows = list(
        PersonContributionSnapshot.objects.filter(
            person__legacy_id=person_id,
            competence__in=clean_competences,
            person__is_active=True,
            is_active=True,
        ).order_by("-competence_order", "received_at", "legacy_id")
    )
    grouped: dict[str, list[int]] = {}
    for row in rows:
        grouped.setdefault(normalize_query(row.competence), []).append(int(row.legacy_id or 0))
    receipt_ids: list[int] = []
    for competence in clean_competences:
        ids = grouped.get(competence) or []
        if not ids:
            continue
        receipt_ids.append(
            issue_receipt_for_contribution_ids(
                person_id=person_id,
                contribution_ids=ids,
                emission_date=emission_date,
                notes=notes,
                actor=actor,
                replace_existing=replace_existing,
                audit_action="gerar_recibo_por_competencia_django",
            )
        )
    if not receipt_ids:
        raise LegacyWriteError("Nao ha contribuicoes ativas para as competencias selecionadas.")
    return receipt_ids


def issue_receipts_for_event_contributions(
    contribution_ids: list[int],
    *,
    emission_date: str,
    notes: str,
    actor: str = "",
    replace_existing: bool = False,
) -> list[int]:
    clean_ids = sorted({int(value or 0) for value in contribution_ids if int(value or 0)})
    if not clean_ids:
        return []
    rows = list(
        PersonContributionSnapshot.objects.filter(
            legacy_id__in=clean_ids,
            is_active=True,
            person__is_active=True,
        ).order_by("person__legacy_id", "received_at", "legacy_id")
    )
    grouped: dict[int, list[int]] = {}
    for row in rows:
        person_id = int(row.person.legacy_id or 0)
        if person_id:
            grouped.setdefault(person_id, []).append(int(row.legacy_id or 0))
    receipt_ids: list[int] = []
    for person_id, person_contribution_ids in grouped.items():
        receipt_ids.append(
            issue_receipt_for_contribution_ids(
                person_id=person_id,
                contribution_ids=person_contribution_ids,
                emission_date=emission_date,
                notes=notes,
                actor=actor,
                replace_existing=replace_existing,
                audit_action="gerar_recibo_por_evento_django",
            )
        )
    return receipt_ids


def create_receipt(payload: Any, actor: str = "", replace_existing: bool = False) -> int:
    person_id = int(getattr(payload, "get", lambda *_args, **_kwargs: 0)("pessoa_id") or 0)
    getter = getattr(payload, "getlist", None)
    raw_ids = getter("contribuicao_id") if getter else getattr(payload, "get", lambda *_args, **_kwargs: [])("contribuicao_id", [])
    if isinstance(raw_ids, str):
        raw_ids = [raw_ids]
    contribution_ids = sorted({int(value or 0) for value in raw_ids if int(value or 0)})
    emission_date = normalize_query(getattr(payload, "get", lambda *_args, **_kwargs: "")("data_emissao", date.today().isoformat())) or date.today().isoformat()
    notes = normalize_query(getattr(payload, "get", lambda *_args, **_kwargs: "")("observacoes"))
    return issue_receipt_for_contribution_ids(
        person_id=person_id,
        contribution_ids=contribution_ids,
        emission_date=emission_date,
        notes=notes,
        actor=actor,
        replace_existing=replace_existing,
    )


def receipt_new_context_postgres(person_id: int, date_start: str = "", date_end: str = "") -> dict[str, Any] | None:
    person = receipt_person_snapshot(int(person_id or 0))
    if not person:
        return None
    queryset = PersonContributionSnapshot.objects.filter(person__legacy_id=int(person_id or 0), is_active=True).order_by("received_at", "legacy_id")
    date_start = normalize_query(date_start)
    date_end = normalize_query(date_end)
    if date_start:
        queryset = queryset.filter(received_at_raw__gte=date_start)
    if date_end:
        queryset = queryset.filter(received_at_raw__lte=date_end)
    active_receipt_by_contribution: dict[int, dict[str, Any]] = {}
    for row in (
        ReceiptItemSnapshot.objects.select_related("receipt")
        .filter(receipt__person_legacy_id=int(person_id or 0), receipt__is_cancelled=False)
        .order_by("-receipt__emission_date", "-receipt__legacy_id", "legacy_id")
    ):
        contribution_id = int(row.contribution_legacy_id or 0)
        if contribution_id and contribution_id not in active_receipt_by_contribution:
            active_receipt_by_contribution[contribution_id] = {
                "id": int(row.receipt.legacy_id or 0),
                "numero": row.receipt.receipt_number or "",
                "data": br_date(row.receipt.emission_date_raw or (row.receipt.emission_date.isoformat() if row.receipt.emission_date else "")),
            }
    items = list(queryset)
    total = sum(float(item.amount or 0) for item in items)
    return {
        "person": person,
        "items": [
            {
                "id": int(row.legacy_id or 0),
                "data": br_date(row.received_at_raw or (row.received_at.isoformat() if row.received_at else "")),
                "competencia": row.competence or "",
                "tipo": row.contribution_type_name or "",
                "forma": row.receipt_method_name or "",
                "valor_fmt": _money(row.amount),
                "active_receipt": active_receipt_by_contribution.get(int(row.legacy_id or 0)),
            }
            for row in items
        ],
        "total_fmt": _money(total),
        "filters": {"date_start": date_start, "date_end": date_end},
    }


def list_receipts_postgres(q: str = "", person_id: int = 0, date_start: str = "", date_end: str = "", limit: int | None = None) -> dict[str, Any]:
    q = normalize_query(q)
    date_start = normalize_query(date_start)
    date_end = normalize_query(date_end)
    queryset = ReceiptSnapshot.objects.filter(is_cancelled=False).order_by("-emission_date", "-legacy_id")
    if int(person_id or 0):
        queryset = queryset.filter(person_legacy_id=int(person_id or 0))
    if date_start:
        queryset = queryset.filter(emission_date_raw__gte=date_start)
    if date_end:
        queryset = queryset.filter(emission_date_raw__lte=date_end)
    if q:
        digits = "".join(ch for ch in q if ch.isdigit())
        queryset = queryset.filter(
            Q(person_name__icontains=q)
            | Q(person_cpf__icontains=digits or q)
            | Q(person_code__icontains=q)
            | Q(receipt_number__icontains=q)
        )
    if limit is not None and int(limit or 0) > 0:
        queryset = queryset[: int(limit or 0)]
    rows = list(queryset)
    total_queryset = ReceiptSnapshot.objects.filter(is_cancelled=False)
    if int(person_id or 0):
        total_queryset = total_queryset.filter(person_legacy_id=int(person_id or 0))
    if date_start:
        total_queryset = total_queryset.filter(emission_date_raw__gte=date_start)
    if date_end:
        total_queryset = total_queryset.filter(emission_date_raw__lte=date_end)
    if q:
        digits = "".join(ch for ch in q if ch.isdigit())
        total_queryset = total_queryset.filter(
            Q(person_name__icontains=q)
            | Q(person_cpf__icontains=digits or q)
            | Q(person_code__icontains=q)
            | Q(receipt_number__icontains=q)
        )
    summary = total_queryset.aggregate(
        quantidade=models.Count("id"),
        total=models.Sum("total_value"),
        pessoas=models.Count("person_legacy_id", distinct=True),
        ultima_data=models.Max("emission_date_raw"),
    )
    return {
        "items": [
            {
                "id": int(row.legacy_id or 0),
                "numero": row.receipt_number or "",
                "data": br_date(row.emission_date_raw or (row.emission_date.isoformat() if row.emission_date else "")),
                "periodo": f"{br_date(row.period_start_raw or '')} a {br_date(row.period_end_raw or '')}",
                "valor_fmt": _money(row.total_value),
                "status": row.status or "",
                "person_id": int(row.person_legacy_id or 0),
                "person_name": row.person_name or "",
                "person_code": row.person_code or "",
                "person_cpf": row.person_cpf or "",
                "detail_url": f"/receipts/{int(row.legacy_id or 0)}/",
            }
            for row in rows
        ],
        "summary": {
            "quantidade": int(summary.get("quantidade") or 0),
            "total_fmt": _money(summary.get("total") or 0),
            "pessoas": int(summary.get("pessoas") or 0),
            "ultima_data": br_date(summary.get("ultima_data") or ""),
        },
        "filters": {"q": q, "person_id": person_id, "date_start": date_start, "date_end": date_end},
    }


def refresh_receipt_dispatch_destination(dispatch: ReceiptDispatch | int, *, actor: str = "") -> ReceiptDispatch:
    item = dispatch if isinstance(dispatch, ReceiptDispatch) else ReceiptDispatch.objects.get(pk=int(dispatch))
    detail = get_receipt_detail_cached(int(item.legacy_receipt_id or 0))
    if not detail:
        return item
    refreshed_email = (
        preferred_delivery_email((detail.get("person") or {}).get("email"), (detail.get("receipt") or {}).get("person_name"))
        or preferred_delivery_email((detail.get("receipt") or {}).get("person_email"), (detail.get("receipt") or {}).get("person_name"))
        or preferred_delivery_email(item.email_to or item.person_email, item.person_name)
    )
    changed = False
    if refreshed_email and refreshed_email != (item.email_to or ""):
        item.email_to = refreshed_email
        changed = True
    if refreshed_email and refreshed_email != (item.person_email or ""):
        item.person_email = refreshed_email
        changed = True
    if changed:
        item.save(update_fields=["email_to", "person_email", "updated_at"])
        try:
            record_django_audit_event(
                actor=actor,
                action="atualizar_destino_fila_recibo_django",
                table_name="receipt_dispatch",
                record_id=int(item.pk or 0),
                organization_id=item.organization_id,
                source="receipt_email",
                summary=f"Destino do recibo {item.legacy_receipt_number or item.period_label} sincronizado com a ficha",
                after={"email_to": item.email_to, "person_email": item.person_email},
            )
        except Exception:
            pass
    return item


def send_receipt_dispatch(dispatch: ReceiptDispatch | int, *, actor: str = "") -> ReceiptDispatch:
    item = dispatch if isinstance(dispatch, ReceiptDispatch) else ReceiptDispatch.objects.get(pk=int(dispatch))
    item = refresh_receipt_dispatch_destination(item, actor=actor)
    detail = get_receipt_detail_cached(int(item.legacy_receipt_id or 0))
    if not detail:
        item.status = ReceiptDispatch.Status.FAILED
        item.last_error = "Recibo legado nao encontrado para envio."
        item.send_attempts += 1
        item.last_attempt_at = timezone.now()
        item.save(update_fields=["status", "last_error", "send_attempts", "last_attempt_at", "updated_at"])
        return item
    rendered = _dispatch_defaults(detail, email_to=item.email_to or item.person_email, subject=item.email_subject, body=item.email_body)
    pdf_payload = receipt_pdf(detail)
    pdf_name = receipt_pdf_filename(detail)
    template = get_or_create_receipt_email_template()
    from_email = (
        normalize_query(template.default_from_email)
        or getattr(settings, "DEFAULT_FROM_EMAIL", "")
        or getattr(settings, "EMAIL_HOST_USER", "")
        or "recebimento@localhost"
    )
    try:
        reply_to = [
            value
            for value in [template.reply_to_email, getattr(settings, "POWER_CHURCH_RECEIPT_REPLY_TO", "")]
            if normalize_query(value)
        ]
        result = send_email_message(
            subject=rendered.subject,
            body=rendered.body,
            from_email=from_email,
            to_emails=[rendered.to_email],
            reply_to=reply_to,
            attachments=[MailAttachment(filename=pdf_name, content=pdf_payload, content_type="application/pdf")],
        )
        if not result.accepted:
            raise MailDispatchError("Provedor de e-mail nao confirmou entrega.")
        item.email_to = rendered.to_email
        item.email_subject = rendered.subject
        item.email_body = rendered.body
        item.pdf_filename = pdf_name
        item.status = ReceiptDispatch.Status.SENT
        item.send_attempts += 1
        item.last_attempt_at = timezone.now()
        item.sent_at = timezone.now()
        item.last_error = ""
        item.save(
            update_fields=[
                "email_to",
                "email_subject",
                "email_body",
                "pdf_filename",
                "status",
                "send_attempts",
                "last_attempt_at",
                "sent_at",
                "last_error",
                "updated_at",
            ]
        )
        try:
            record_django_audit_event(
                actor=actor,
                action="enviar_recibo_email_django",
                table_name="receipt_dispatch",
                record_id=int(item.pk or 0),
                organization_id=item.organization_id,
                source="receipt_email",
                summary=f"Recibo {item.legacy_receipt_number or item.period_label} enviado por e-mail",
                after={
                    "email_to": item.email_to,
                    "receipt_id": item.legacy_receipt_id,
                    "pdf_filename": item.pdf_filename,
                    "provider": result.provider,
                    "status": item.status,
                },
            )
        except Exception:
            pass
        return item
    except Exception as exc:
        item.email_to = rendered.to_email
        item.email_subject = rendered.subject
        item.email_body = rendered.body
        item.status = ReceiptDispatch.Status.FAILED
        item.send_attempts += 1
        item.last_attempt_at = timezone.now()
        item.last_error = str(exc)
        item.save(
            update_fields=[
                "email_to",
                "email_subject",
                "email_body",
                "status",
                "send_attempts",
                "last_attempt_at",
                "last_error",
                "updated_at",
            ]
        )
        try:
            record_django_audit_event(
                actor=actor,
                action="falhar_envio_recibo_email_django",
                table_name="receipt_dispatch",
                record_id=int(item.pk or 0),
                organization_id=item.organization_id,
                source="receipt_email",
                summary=f"Falha ao enviar recibo {item.legacy_receipt_number or item.period_label}",
                after={
                    "email_to": item.email_to,
                    "receipt_id": item.legacy_receipt_id,
                    "status": item.status,
                    "erro": item.last_error,
                },
            )
        except Exception:
            pass
        return item


def process_pending_receipt_dispatches(*, limit: int = 20, actor: str = "") -> list[ReceiptDispatch]:
    items = list(
        ReceiptDispatch.objects.filter(status__in=[ReceiptDispatch.Status.PENDING, ReceiptDispatch.Status.FAILED])
        .exclude(Q(email_to="") & Q(person_email=""))
        .order_by("created_at", "id")[: max(1, int(limit or 20))]
    )
    return [send_receipt_dispatch(item, actor=actor) for item in items]


def drain_receipt_dispatch_queue(
    *,
    limit: int = 10,
    actor: str = "",
    campaign_key: str = "",
    pending_only: bool = False,
    sleep_seconds: float = 3.0,
    pause_every: int = 40,
    pause_seconds: float = 60.0,
    drain: bool = False,
) -> dict[str, Any]:
    statuses = [ReceiptDispatch.Status.PENDING]
    if not pending_only:
        statuses.append(ReceiptDispatch.Status.FAILED)
    clean_campaign_key = normalize_query(campaign_key)
    if clean_campaign_key:
        dedupe_campaign_receipt_dispatches(campaign_key=clean_campaign_key, actor=actor)

    sent = 0
    failed = 0
    processed: list[dict[str, object]] = []
    rounds = 0
    batch_limit = max(1, int(limit or 10))

    while True:
        queryset = ReceiptDispatch.objects.filter(status__in=statuses).exclude(Q(email_to="") & Q(person_email=""))
        if clean_campaign_key:
            queryset = queryset.filter(metadata__campaign_key=clean_campaign_key)
        items = list(queryset.order_by("created_at", "id")[:batch_limit])
        if not items:
            break
        rounds += 1
        if clean_campaign_key:
            batch = process_campaign_receipt_dispatches(
                campaign_key=clean_campaign_key,
                limit=batch_limit,
                actor=actor,
                pending_only=bool(pending_only),
                sleep_seconds=float(sleep_seconds or 0),
                pause_every=int(pause_every or 0),
                pause_seconds=float(pause_seconds or 0),
            )
        else:
            batch = []
            for index, item in enumerate(items, start=1):
                result = send_receipt_dispatch(item, actor=actor)
                batch.append(result)
                if index >= len(items):
                    continue
                if int(pause_every or 0) > 0 and index % int(pause_every or 0) == 0 and float(pause_seconds or 0) > 0:
                    time.sleep(float(pause_seconds or 0))
                    continue
                if float(sleep_seconds or 0) > 0:
                    time.sleep(float(sleep_seconds or 0))
        for result in batch:
            processed.append(
                {
                    "dispatch_id": int(result.pk or 0),
                    "receipt_id": int(result.legacy_receipt_id or 0),
                    "receipt_number": result.legacy_receipt_number,
                    "email_to": result.email_to or result.person_email,
                    "status": result.status,
                    "error": result.last_error,
                }
            )
            if result.status == ReceiptDispatch.Status.SENT:
                sent += 1
            else:
                failed += 1
        if not drain:
            break

    return {
        "campaign_key": clean_campaign_key,
        "rounds": rounds,
        "selected": len(processed),
        "sent": sent,
        "failed": failed,
        "sleep_seconds": float(sleep_seconds or 0),
        "pause_every": int(pause_every or 0),
        "pause_seconds": float(pause_seconds or 0),
        "processed": processed,
    }


def process_campaign_receipt_dispatches(
    *,
    campaign_key: str,
    limit: int = 20,
    actor: str = "",
    pending_only: bool = False,
    sleep_seconds: float = 0.0,
    pause_every: int = 0,
    pause_seconds: float = 0.0,
) -> list[ReceiptDispatch]:
    statuses = [ReceiptDispatch.Status.PENDING]
    if not pending_only:
        statuses.append(ReceiptDispatch.Status.FAILED)
    items = list(
        ReceiptDispatch.objects.filter(status__in=statuses, metadata__campaign_key=normalize_query(campaign_key))
        .exclude(Q(email_to="") & Q(person_email=""))
        .order_by("created_at", "id")[: max(1, int(limit or 20))]
    )
    processed: list[ReceiptDispatch] = []
    for index, item in enumerate(items, start=1):
        processed.append(send_receipt_dispatch(item, actor=actor))
        if index >= len(items):
            continue
        if int(pause_every or 0) > 0 and index % int(pause_every) == 0 and float(pause_seconds or 0) > 0:
            time.sleep(float(pause_seconds or 0))
            continue
        if float(sleep_seconds or 0) > 0:
            time.sleep(float(sleep_seconds or 0))
    return processed


def email_runtime_snapshot() -> dict[str, Any]:
    return {
        "provider": configured_provider(),
        "default_from_email": getattr(settings, "DEFAULT_FROM_EMAIL", ""),
        "reply_to_default": getattr(settings, "POWER_CHURCH_RECEIPT_REPLY_TO", ""),
    }


def issue_and_optionally_send_receipts(
    *,
    person_id: int,
    competences: list[str],
    emission_date: str,
    notes: str,
    email_to: str,
    subject: str,
    body: str,
    actor: str = "",
    trigger: str = ReceiptDispatch.Trigger.MANUAL,
    auto_created: bool = False,
    send_now: bool = False,
) -> dict[str, Any]:
    clean_competences = [normalize_query(value) for value in competences if normalize_query(value)]
    receipt_ids = issue_period_receipts(
        person_id=int(person_id or 0),
        competences=clean_competences,
        emission_date=emission_date,
        notes=notes,
        actor=actor,
        replace_existing=True,
    )
    if receipt_ids:
        sync_receipt_snapshots(receipt_ids=receipt_ids, person_ids=[int(person_id or 0)])
    dispatches: list[ReceiptDispatch] = []
    if normalize_query(email_to):
        dispatches = queue_receipt_dispatches(
            receipt_ids,
            email_to=email_to,
            subject=subject,
            body=body,
            actor=actor,
            trigger=trigger,
            auto_created=auto_created,
            send_now=send_now,
    )
    return {"receipt_ids": receipt_ids, "dispatches": dispatches}


def issue_event_receipts_and_optionally_send(
    *,
    contribution_ids: list[int],
    email_overrides: dict[int, str] | None = None,
    subject: str,
    body: str,
    actor: str = "",
    trigger: str = ReceiptDispatch.Trigger.AUTOMATIC,
    auto_created: bool = False,
    send_now: bool = False,
) -> dict[str, Any]:
    receipt_ids = issue_receipts_for_event_contributions(
        contribution_ids,
        emission_date=date.today().isoformat(),
        notes="Recibo gerado automaticamente a partir do evento de lancamento.",
        actor=actor,
        replace_existing=True,
    )
    if receipt_ids:
        synced = sync_receipt_snapshots(receipt_ids=receipt_ids)
        synced_person_ids = [int(item.person_legacy_id or 0) for item in synced if int(item.person_legacy_id or 0)]
        if synced_person_ids:
            sync_receipt_snapshots(person_ids=synced_person_ids)
    dispatches: list[ReceiptDispatch] = []
    if receipt_ids:
        ensured = ensure_receipt_dispatches_for_receipt_ids(
            receipt_ids,
            actor=actor,
            trigger=trigger,
            auto_created=auto_created,
            send_now=send_now,
        )
        dispatch_ids = [int(item.get("dispatch_id") or 0) for item in ensured if int(item.get("dispatch_id") or 0)]
        if dispatch_ids:
            dispatches = list(ReceiptDispatch.objects.filter(pk__in=dispatch_ids).order_by("pk"))
    return {"receipt_ids": receipt_ids, "dispatches": dispatches}


def schedule_automatic_receipts_for_events(
    contribution_ids: list[int],
    *,
    actor: str = "",
    send_now: bool | None = None,
    trigger: str = ReceiptDispatch.Trigger.AUTOMATIC,
) -> list[dict[str, Any]]:
    if not receipt_email_enabled():
        return []
    effective_send_now = receipt_auto_send_enabled() if send_now is None else bool(send_now)
    outcomes: list[dict[str, Any]] = []
    clean_ids = [int(value or 0) for value in contribution_ids if int(value or 0)]
    if not clean_ids:
        return outcomes
    rows = list(
        PersonContributionSnapshot.objects.select_related("person")
        .filter(legacy_id__in=clean_ids, is_active=True, person__isnull=False)
        .order_by("person__name", "received_at", "legacy_id")
    )
    email_overrides: dict[int, str] = {}
    by_person: dict[int, dict[str, Any]] = {}
    for row in rows:
        person_id = int(row.person.legacy_id or 0)
        if not person_id:
            continue
        email_overrides[person_id] = preferred_delivery_email(row.person.primary_email, row.person.name)
        payload = by_person.setdefault(
            person_id,
            {
                "person_id": person_id,
                "person_name": row.person.name or "",
                "email": preferred_delivery_email(row.person.primary_email, row.person.name),
                "contribution_ids": [],
                "dates": [],
                "competences": [],
            },
        )
        payload["contribution_ids"].append(int(row.legacy_id or 0))
        payload["dates"].append(row.received_at_raw or "")
        payload["competences"].append(normalize_query(row.competence))
    if not by_person:
        return outcomes
    for payload in by_person.values():
        if not payload["email"]:
            outcomes.append(
                {
                    "person_id": payload["person_id"],
                    "person_name": payload["person_name"],
                    "status": "sem_email",
                    "contribution_ids": payload["contribution_ids"],
                }
            )
    deliverable_ids = [value for payload in by_person.values() if payload["email"] for value in payload["contribution_ids"]]
    if not deliverable_ids:
        return outcomes
    try:
        result = issue_event_receipts_and_optionally_send(
            contribution_ids=deliverable_ids,
            email_overrides=email_overrides,
            subject="",
            body="",
            actor=actor,
            trigger=trigger,
            auto_created=True,
            send_now=effective_send_now,
        )
        dispatch_by_receipt = {int(item.legacy_receipt_id or 0): int(item.pk or 0) for item in result["dispatches"]}
        for receipt_id in result["receipt_ids"]:
            detail = get_receipt_detail_cached(receipt_id)
            receipt = detail.get("receipt") if detail else {}
            outcomes.append(
                {
                    "person_id": int(receipt.get("person_id") or 0),
                    "person_name": receipt.get("person_name") or "",
                    "status": "ok",
                    "receipt_id": receipt_id,
                    "dispatch_id": dispatch_by_receipt.get(receipt_id, 0),
                    "period_label": f"{receipt.get('periodo_inicio') or ''} a {receipt.get('periodo_fim') or ''}".strip(),
                }
            )
    except Exception as exc:
        outcomes.append({"status": "erro", "error": str(exc), "contribution_ids": clean_ids})
    return outcomes


def summarize_automatic_receipt_outcomes(
    outcomes: list[dict[str, Any]],
    *,
    send_now: bool,
) -> dict[str, int]:
    created = 0
    sent = 0
    queued = 0
    failed = 0
    without_email = 0
    for item in outcomes:
        status = normalize_query(item.get("status"))
        if status == "sem_email":
            without_email += 1
            continue
        if status == "erro":
            failed += 1
            continue
        if status == "ok":
            created += 1
            if send_now:
                sent += 1
            else:
                queued += 1
            continue
        if status == "queued_existing_receipt":
            queued += 1
            continue
        if status == "existing_dispatch":
            if send_now:
                sent += 1
            else:
                queued += 1
    return {
        "created": created,
        "sent": sent,
        "queued": queued,
        "failed": failed,
        "without_email": without_email,
    }


def mark_receipt_dispatches_cancelled(receipt_ids: list[int], *, actor: str = "", reason: str = "") -> int:
    clean_ids = [int(value or 0) for value in receipt_ids if int(value or 0)]
    if not clean_ids:
        return 0
    items = list(
        ReceiptDispatch.objects.filter(legacy_receipt_id__in=clean_ids).exclude(status=ReceiptDispatch.Status.CANCELLED)
    )
    count = 0
    for item in items:
        item.status = ReceiptDispatch.Status.CANCELLED
        if reason:
            item.last_error = reason
        item.save(update_fields=["status", "last_error", "updated_at"])
        count += 1
        try:
            record_django_audit_event(
                actor=actor,
                action="cancelar_envio_recibo_django",
                table_name="receipt_dispatch",
                record_id=int(item.pk or 0),
                organization_id=item.organization_id,
                source="receipt_email",
                summary=f"Fila do recibo {item.legacy_receipt_number or item.period_label} cancelada",
                after={"status": item.status, "erro": item.last_error},
            )
        except Exception:
            pass
    return count


def backfill_receipt_dispatches(*, actor: str = "") -> dict[str, int]:
    scanned = 0
    created = 0
    covered_contribution_ids = set(
        int(value or 0)
        for value in ReceiptItemSnapshot.objects.filter(receipt__is_cancelled=False).values_list("contribution_legacy_id", flat=True)
        if int(value or 0)
    )
    rows = list(
        PersonContributionSnapshot.objects.filter(
            is_active=True,
            person__is_active=True,
        )
        .exclude(person__primary_email="")
        .exclude(legacy_id__in=covered_contribution_ids)
        .order_by("person__legacy_id", "received_at_raw", "legacy_id")
    )
    grouped: dict[tuple[int, str], list[int]] = {}
    for row in rows:
        scanned += 1
        person_id = int(row.person.legacy_id or 0)
        receipt_date = normalize_query(row.received_at_raw)
        if not person_id or not receipt_date:
            continue
        grouped.setdefault((person_id, receipt_date), []).append(int(row.legacy_id or 0))
    for (person_id, receipt_date), contribution_ids in grouped.items():
        existing_items = ReceiptDispatch.objects.filter(
            legacy_person_id=person_id,
            trigger=ReceiptDispatch.Trigger.RETROACTIVE,
        ).exclude(status=ReceiptDispatch.Status.CANCELLED)
        already_covered = False
        for item in existing_items:
            if sorted(int(value or 0) for value in item.metadata.get("items", [])) == sorted(contribution_ids):
                already_covered = True
                break
        if already_covered:
            continue
        person = receipt_person_snapshot(person_id)
        if not person or not normalize_query(person.get("email")):
            continue
        try:
            issue_event_receipts_and_optionally_send(
                contribution_ids=contribution_ids,
                email_overrides={person_id: str(person.get("email") or "")},
                subject="",
                body="",
                actor=actor,
                trigger=ReceiptDispatch.Trigger.RETROACTIVE,
                auto_created=True,
                send_now=False,
            )
            created += 1
        except Exception:
            continue
    return {"scanned": scanned, "created": created}


def backfill_native_event_receipts(*, actor: str = "") -> dict[str, int]:
    from power_church_django.apps.contributions.models import NativeContribution, NativeEnvelope, NativeEnvelopeItem
    from power_church_django.apps.imports.models import StatementImportPilotMovement

    active_receipt_items = list(
        ReceiptItemSnapshot.objects.select_related("receipt")
        .filter(receipt__is_cancelled=False)
        .order_by("receipt__legacy_id", "legacy_id")
    )
    covered_contribution_ids = {
        int(item.contribution_legacy_id or 0)
        for item in active_receipt_items
        if int(item.contribution_legacy_id or 0)
    }
    receipt_ids_by_contribution: dict[int, int] = {}
    for item in active_receipt_items:
        contribution_id = int(item.contribution_legacy_id or 0)
        receipt_id = int(item.receipt.legacy_id or 0)
        if contribution_id and receipt_id and contribution_id not in receipt_ids_by_contribution:
            receipt_ids_by_contribution[contribution_id] = receipt_id
    active_dispatch_receipt_ids = {
        int(value or 0)
        for value in ReceiptDispatch.objects.exclude(status=ReceiptDispatch.Status.CANCELLED).values_list("legacy_receipt_id", flat=True)
        if int(value or 0)
    }

    envelope_groups_scanned = 0
    statement_groups_scanned = 0
    envelope_outcomes: list[dict[str, Any]] = []
    statement_outcomes: list[dict[str, Any]] = []
    existing_receipt_outcomes: list[dict[str, Any]] = []

    envelope_ids = list(
        NativeEnvelope.objects.filter(is_active=True, status="lancado").order_by("competence_order", "received_at", "legacy_id").values_list("legacy_id", flat=True)
    )
    for envelope_id in [int(value or 0) for value in envelope_ids if int(value or 0)]:
        contribution_ids = [
            int(value or 0)
            for value in NativeEnvelopeItem.objects.filter(
                envelope__legacy_id=envelope_id,
                envelope__is_active=True,
                is_active=True,
            ).values_list("contribution_legacy_id", flat=True)
            if int(value or 0)
        ]
        eligible_ids = list(
            NativeContribution.objects.filter(
                legacy_id__in=contribution_ids,
                is_active=True,
            )
            .exclude(person_legacy_id__isnull=True)
            .exclude(person_legacy_id=0)
            .order_by("legacy_id")
            .values_list("legacy_id", flat=True)
        )
        envelope_groups_scanned += 1
        pending_ids = [int(value or 0) for value in eligible_ids if int(value or 0) and int(value or 0) not in covered_contribution_ids]
        if pending_ids:
            outcomes = schedule_automatic_receipts_for_events(
                pending_ids,
                actor=actor,
                send_now=False,
                trigger=ReceiptDispatch.Trigger.RETROACTIVE,
            )
            envelope_outcomes.extend(outcomes)
            covered_contribution_ids.update(pending_ids)
        pending_receipt_ids = sorted(
            {
                int(receipt_ids_by_contribution.get(int(value or 0)) or 0)
                for value in eligible_ids
                if int(receipt_ids_by_contribution.get(int(value or 0)) or 0)
                and int(receipt_ids_by_contribution.get(int(value or 0)) or 0) not in active_dispatch_receipt_ids
            }
        )
        if pending_receipt_ids:
            outcomes = ensure_receipt_dispatches_for_receipt_ids(
                pending_receipt_ids,
                actor=actor,
                trigger=ReceiptDispatch.Trigger.RETROACTIVE,
                auto_created=True,
                send_now=False,
                metadata_extra={"campaign_mode": "retroativo_eventos_nativos"},
            )
            existing_receipt_outcomes.extend(outcomes)
            active_dispatch_receipt_ids.update(pending_receipt_ids)

    movement_lot_map = {
        int(movement_id or 0): int(lot_id or 0)
        for movement_id, lot_id in StatementImportPilotMovement.objects.values_list("id", "lot_id")
    }
    statement_groups: dict[int, list[int]] = {}
    for contribution in NativeContribution.objects.filter(
        is_active=True,
        statement_movement_legacy_id__isnull=False,
    ).exclude(person_legacy_id__isnull=True).exclude(person_legacy_id=0).order_by("statement_movement_legacy_id", "legacy_id"):
        contribution_id = int(contribution.legacy_id or 0)
        if not contribution_id or contribution_id in covered_contribution_ids:
            continue
        lot_id = int(movement_lot_map.get(int(contribution.statement_movement_legacy_id or 0)) or 0)
        if not lot_id:
            continue
        statement_groups.setdefault(lot_id, []).append(contribution_id)

    for lot_id in sorted(statement_groups):
        statement_groups_scanned += 1
        pending_ids = [int(value or 0) for value in statement_groups.get(lot_id, []) if int(value or 0) and int(value or 0) not in covered_contribution_ids]
        if pending_ids:
            outcomes = schedule_automatic_receipts_for_events(
                pending_ids,
                actor=actor,
                send_now=False,
                trigger=ReceiptDispatch.Trigger.RETROACTIVE,
            )
            statement_outcomes.extend(outcomes)
            covered_contribution_ids.update(pending_ids)
        pending_receipt_ids = sorted(
            {
                int(receipt_ids_by_contribution.get(int(value or 0)) or 0)
                for value in statement_groups.get(lot_id, [])
                if int(receipt_ids_by_contribution.get(int(value or 0)) or 0)
                and int(receipt_ids_by_contribution.get(int(value or 0)) or 0) not in active_dispatch_receipt_ids
            }
        )
        if pending_receipt_ids:
            outcomes = ensure_receipt_dispatches_for_receipt_ids(
                pending_receipt_ids,
                actor=actor,
                trigger=ReceiptDispatch.Trigger.RETROACTIVE,
                auto_created=True,
                send_now=False,
                metadata_extra={"campaign_mode": "retroativo_eventos_nativos"},
            )
            existing_receipt_outcomes.extend(outcomes)
            active_dispatch_receipt_ids.update(pending_receipt_ids)

    envelope_summary = summarize_automatic_receipt_outcomes(envelope_outcomes, send_now=False)
    statement_summary = summarize_automatic_receipt_outcomes(statement_outcomes, send_now=False)
    existing_receipt_summary = summarize_automatic_receipt_outcomes(existing_receipt_outcomes, send_now=False)
    return {
        "envelope_groups_scanned": envelope_groups_scanned,
        "statement_groups_scanned": statement_groups_scanned,
        "queued": int(envelope_summary["queued"] + statement_summary["queued"] + existing_receipt_summary["queued"]),
        "created": int(envelope_summary["created"] + statement_summary["created"]),
        "failed": int(envelope_summary["failed"] + statement_summary["failed"] + existing_receipt_summary["failed"]),
        "without_email": int(envelope_summary["without_email"] + statement_summary["without_email"] + existing_receipt_summary["without_email"]),
        "existing_receipts_queued": int(existing_receipt_summary["queued"]),
    }

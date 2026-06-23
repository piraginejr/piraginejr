from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from django.conf import settings
from django.apps import apps
from django.core.paginator import Paginator
from django.db import OperationalError, ProgrammingError
from django.utils import timezone

from power_church_django.services.smart_audit import classify_email_audit, summarize_smart_audit

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


def list_system_email_events(
    *,
    kind: str = "",
    status: str = "",
    q: str = "",
    person_id: int = 0,
    page: int = 1,
    page_size: int = 120,
) -> dict[str, Any]:
    from power_church_django.apps.contributions.models import ReceiptDispatch

    AuditEvent = _audit_model()
    page = max(1, int(page or 1))
    page_size = min(max(50, int(page_size or 120)), 500)
    kind = (kind or "").strip().lower()
    status = (status or "").strip().lower()
    q = (q or "").strip().lower()
    person_id = max(0, int(person_id or 0))

    rows: list[dict[str, Any]] = []

    if kind in {"", "recibo"}:
        receipt_qs = ReceiptDispatch.objects.all().order_by("-created_at", "-id")
        if person_id:
            receipt_qs = receipt_qs.filter(legacy_person_id=person_id)
        if status == "enviado":
            receipt_qs = receipt_qs.filter(status=ReceiptDispatch.Status.SENT)
        elif status == "pendente":
            receipt_qs = receipt_qs.filter(status=ReceiptDispatch.Status.PENDING)
        elif status == "falhou":
            receipt_qs = receipt_qs.filter(status=ReceiptDispatch.Status.FAILED)
        elif status == "cancelado":
            receipt_qs = receipt_qs.filter(status=ReceiptDispatch.Status.CANCELLED)
        for item in receipt_qs:
            body = item.email_body or ""
            subject = item.email_subject or ""
            if q and q not in " ".join(
                [
                    str(item.person_name or "").lower(),
                    str(item.email_to or item.person_email or "").lower(),
                    subject.lower(),
                    body.lower(),
                    str(item.legacy_receipt_number or "").lower(),
                    str(item.trigger or "").lower(),
                ]
            ):
                continue
            rows.append(
                {
                    "kind_key": "recibo",
                    "row_id": int(item.pk or 0),
                    "person_id": int(item.legacy_person_id or 0),
                    "sort_date": item.sent_at or item.last_attempt_at or item.created_at,
                    "data": timezone.localtime(item.sent_at or item.last_attempt_at or item.created_at).strftime("%d/%m/%Y %H:%M"),
                    "tipo": "Recibo",
                    "origem": item.get_trigger_display(),
                    "status": item.get_status_display(),
                    "status_key": item.status,
                    "pessoa": item.person_name or f"Pessoa #{item.legacy_person_id}",
                    "destino": item.email_to or item.person_email or "",
                    "assunto": subject,
                    "corpo": body,
                    "anexo": item.pdf_filename or "",
                    "referencia": item.legacy_receipt_number or item.period_label or "",
                    "operador": "",
                    "resumo": f"Recibo {item.legacy_receipt_number or item.period_label}",
                    "can_resend": bool(item.legacy_receipt_id),
                }
            )
            rows[-1]["smart_audit"] = classify_email_audit(rows[-1])

    if kind in {"", "extrato"}:
        statement_actions = ["enviar_extrato_email_django"]
        statement_qs = AuditEvent.objects.filter(action__in=statement_actions).order_by("-created_at", "-id")
        for event in statement_qs:
            payload = event.after or {}
            status_label = "Enviado"
            if status and status not in {"enviado", "ok"}:
                continue
            event_person_id = int(payload.get("person_id") or event.record_id or 0)
            if person_id and event_person_id != person_id:
                continue
            body = str(payload.get("body") or "")
            subject = str(payload.get("subject") or "")
            destination = str(payload.get("email_to") or "")
            person_name = str(payload.get("person_name") or event.summary or "")
            if q and q not in " ".join(
                [
                    person_name.lower(),
                    destination.lower(),
                    subject.lower(),
                    body.lower(),
                    str(payload.get("filename") or "").lower(),
                ]
            ):
                continue
            rows.append(
                {
                    "kind_key": "extrato",
                    "row_id": int(event.id or 0),
                    "person_id": event_person_id,
                    "sort_date": event.created_at,
                    "data": timezone.localtime(event.created_at).strftime("%d/%m/%Y %H:%M"),
                    "tipo": "Extrato",
                    "origem": "Manual",
                    "status": status_label,
                    "status_key": "enviado",
                    "pessoa": person_name,
                    "destino": destination,
                    "assunto": subject,
                    "corpo": body,
                    "anexo": str(payload.get("filename") or ""),
                    "referencia": str(payload.get("query") or ""),
                    "operador": event.actor or "",
                    "resumo": event.summary or "",
                    "can_resend": bool(event_person_id and destination),
                }
            )
            rows[-1]["smart_audit"] = classify_email_audit(rows[-1])

    rows.sort(key=lambda item: item["sort_date"], reverse=True)
    paginator = Paginator(rows, page_size)
    page_obj = paginator.get_page(page)
    kinds = ["recibo", "extrato"]
    statuses = ["enviado", "pendente", "falhou", "cancelado"]
    return {
        "kind": kind,
        "status": status,
        "q": q,
        "person_id": person_id,
        "smart_summary": summarize_smart_audit(rows),
        "items": list(page_obj.object_list),
        "kinds": kinds,
        "statuses": statuses,
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


def resend_system_email_event(*, kind: str, row_id: int, actor: str = "") -> dict[str, Any]:
    from power_church_core.normalization import normalize_query
    from power_church_django.apps.contributions.models import ReceiptDispatch
    from power_church_django.services.contributions_native import person_statement_data_postgres
    from power_church_django.services.mail_dispatch import MailAttachment, send_email_message
    from power_church_django.services.pdf_reports import person_statement_pdf, person_statement_pdf_filename
    from power_church_django.services.receipt_delivery import send_receipt_dispatch

    kind = (kind or "").strip().lower()
    row_id = int(row_id or 0)
    if kind not in {"recibo", "extrato"} or row_id <= 0:
        raise LookupError("Registro de e-mail invalido para reenvio.")

    if kind == "recibo":
        dispatch = ReceiptDispatch.objects.filter(pk=row_id).first()
        if dispatch is None:
            raise LookupError("Envio de recibo nao encontrado para reenvio.")
        updated = send_receipt_dispatch(dispatch, actor=actor)
        return {
            "kind": "recibo",
            "person_id": int(updated.legacy_person_id or 0),
            "person_name": updated.person_name or "",
            "destination": updated.email_to or updated.person_email or "",
            "reference": updated.legacy_receipt_number or updated.period_label or "",
        }

    AuditEvent = _audit_model()
    event = AuditEvent.objects.filter(pk=row_id, action="enviar_extrato_email_django").first()
    if event is None:
        raise LookupError("Envio de extrato nao encontrado para reenvio.")
    payload = event.after or {}
    person_id = int(payload.get("person_id") or event.record_id or 0)
    if not person_id:
        raise LookupError("Extrato sem pessoa associada para reenvio.")
    query_payload = parse_qs(str(payload.get("query") or ""), keep_blank_values=True)
    type_ids = [int(value) for value in query_payload.get("tipo_id", []) if str(value).isdigit()]
    statement = person_statement_data_postgres(
        person_id,
        year=str((query_payload.get("year") or [""])[0] or ""),
        competencia=str((query_payload.get("competencia") or [""])[0] or ""),
        date_start=str((query_payload.get("date_start") or [""])[0] or ""),
        date_end=str((query_payload.get("date_end") or [""])[0] or ""),
        type_ids=type_ids,
    )
    if not statement:
        raise LookupError("Nao foi possivel reconstruir o extrato para reenvio.")
    destination = normalize_query(payload.get("email_to"))
    if not destination:
        raise LookupError("O envio original do extrato nao registrou destinatario para reenvio.")
    from_email = normalize_query(payload.get("from_email")) or getattr(settings, "DEFAULT_FROM_EMAIL", "") or "recebimento@localhost"
    reply_to_raw = payload.get("reply_to") or []
    if isinstance(reply_to_raw, str):
        reply_to = [normalize_query(reply_to_raw)] if normalize_query(reply_to_raw) else []
    else:
        reply_to = [normalize_query(value) for value in reply_to_raw if normalize_query(value)]
    subject = str(payload.get("subject") or "")
    body = str(payload.get("body") or "")
    filename = str(payload.get("filename") or person_statement_pdf_filename(statement))
    pdf_payload = person_statement_pdf(statement)
    result = send_email_message(
        subject=subject,
        body=body,
        from_email=from_email,
        to_emails=[destination],
        reply_to=reply_to,
        attachments=[MailAttachment(filename=filename, content=pdf_payload, content_type="application/pdf")],
    )
    record_django_audit_event(
        actor=actor,
        action="reenviar_extrato_email_django",
        table_name="pessoas",
        record_id=person_id,
        organization_id=event.organization_id,
        source="statement_email",
        summary=f"Extrato reenviado por e-mail para {destination}",
        after={
            "person_id": person_id,
            "person_name": (statement.get("person") or {}).get("nome") or "",
            "email_to": destination,
            "from_email": from_email,
            "reply_to": reply_to,
            "subject": subject,
            "body": body,
            "provider": result.provider,
            "filename": filename,
            "query": str(payload.get("query") or ""),
            "reenvio_de_evento_id": int(event.id or 0),
        },
    )
    return {
        "kind": "extrato",
        "person_id": person_id,
        "person_name": (statement.get("person") or {}).get("nome") or "",
        "destination": destination,
        "reference": filename,
    }

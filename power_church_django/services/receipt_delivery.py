from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from power_church_core.formatting import br_date
from power_church_core.normalization import normalize_query
from power_church_django.apps.contributions.models import ReceiptDispatch, ReceiptEmailTemplate
from power_church_django.services.django_audit import record_django_audit_event
from power_church_django.services.legacy import connect_legacy, format_cpf, format_status, get_receipt_detail, status_sigla
from power_church_django.services.legacy_write import issue_period_receipts, issue_receipts_for_event_contributions
from power_church_django.services.mail_dispatch import (
    MailAttachment,
    MailDispatchError,
    configured_provider,
    send_email_message,
)
from power_church_django.services.pdf_reports import receipt_pdf, receipt_pdf_filename


DEFAULT_TEMPLATE_KEY = "receipt_default"
DEFAULT_TEMPLATE_NAME = "Recibo mensal de contribuicoes"
DEFAULT_SUBJECT_TEMPLATE = "Recibo de contribuicoes - {person_name} - {period_label}"
DEFAULT_BODY_TEMPLATE = """Prezado(a) {person_name},

Segue em anexo o recibo de contribuicoes referente a {period_label}, no valor total de {total_fmt}.

Em caso de duvida, responda este e-mail para que a equipe possa conferir.

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
    with connect_legacy() as conn:
        rows = conn.execute(
            """
            SELECT COALESCE(c.competencia, '') AS competencia,
                   MIN(c.data_recebimento) AS periodo_inicio,
                   MAX(c.data_recebimento) AS periodo_fim,
                   COUNT(*) AS quantidade,
                   COALESCE(SUM(c.valor), 0) AS total,
                   MAX(COALESCE(c.competencia_ordem, 0)) AS competencia_ordem
              FROM contribuicoes c
             WHERE c.ativo = 1
               AND c.pessoa_id = ?
             GROUP BY COALESCE(c.competencia, '')
             ORDER BY competencia_ordem DESC, competencia DESC
            """,
            (person_id,),
        ).fetchall()
        receipt_rows = conn.execute(
            """
            SELECT c.competencia, r.id, r.numero, r.data_emissao, r.valor_total
              FROM recibos r
              JOIN recibo_itens ri ON ri.recibo_id = r.id
              JOIN contribuicoes c ON c.id = ri.contribuicao_id
             WHERE r.pessoa_id = ?
               AND r.status <> 'cancelado'
               AND r.cancelado_em IS NULL
               AND c.ativo = 1
             ORDER BY r.id DESC
            """,
            (person_id,),
        ).fetchall()
    receipt_by_competence: dict[str, dict[str, Any]] = {}
    for row in receipt_rows:
        competence = str(row["competencia"] or "")
        if competence not in receipt_by_competence:
            receipt_by_competence[competence] = {
                "id": int(row["id"] or 0),
                "numero": row["numero"] or "",
                "data": br_date(row["data_emissao"]),
                "valor_fmt": f"R$ {float(row['valor_total'] or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
            }
    return [
        {
            "competencia": row["competencia"] or "Sem competencia",
            "competencia_key": row["competencia"] or "",
            "periodo_inicio": br_date(row["periodo_inicio"]),
            "periodo_fim": br_date(row["periodo_fim"]),
            "quantidade": int(row["quantidade"] or 0),
            "total": round(float(row["total"] or 0), 2),
            "total_fmt": f"R$ {float(row['total'] or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
            "active_receipt": receipt_by_competence.get(str(row["competencia"] or "")),
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
    with connect_legacy() as conn:
        row = conn.execute(
            """
            SELECT id, codigo_interno, nome, cpf, status, email_principal, telefone_principal
              FROM pessoas
             WHERE id = ? AND ativo = 1
             LIMIT 1
            """,
            (person_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        "id": int(row["id"] or 0),
        "nome": row["nome"] or "",
        "codigo": row["codigo_interno"] or "",
        "cpf": format_cpf(row["cpf"]),
        "status": format_status(row["status"]),
        "sigla": status_sigla(row["status"], True),
        "email": row["email_principal"] or "",
        "telefone": row["telefone_principal"] or "",
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
    rendered_email = normalize_query(email_to) or normalize_query((detail.get("person") or {}).get("email")) or normalize_query((detail.get("receipt") or {}).get("person_email"))
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
) -> list[ReceiptDispatch]:
    dispatches: list[ReceiptDispatch] = []
    for receipt_id in [int(value or 0) for value in receipt_ids if int(value or 0)]:
        detail = get_receipt_detail(receipt_id)
        if not detail:
            continue
        rendered = _dispatch_defaults(detail, email_to=email_to, subject=subject, body=body)
        receipt = detail.get("receipt") or {}
        metadata = {
            "items": [item.get("contribution_id") for item in detail.get("items") or []],
            "total_itens": len(detail.get("items") or []),
        }
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


def _raw_date(value: object) -> str:
    raw = normalize_query(value)
    if len(raw) == 10 and raw[2] == "/" and raw[5] == "/":
        day, month, year = raw.split("/")
        return f"{year}-{month}-{day}"
    return raw


def send_receipt_dispatch(dispatch: ReceiptDispatch | int, *, actor: str = "") -> ReceiptDispatch:
    item = dispatch if isinstance(dispatch, ReceiptDispatch) else ReceiptDispatch.objects.get(pk=int(dispatch))
    detail = get_receipt_detail(int(item.legacy_receipt_id or 0))
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
    dispatches: list[ReceiptDispatch] = []
    if receipt_ids:
        for receipt_id in receipt_ids:
            detail = get_receipt_detail(receipt_id)
            if not detail:
                continue
            person_id = int((detail.get("receipt") or {}).get("person_id") or 0)
            email_to = ""
            if email_overrides:
                email_to = normalize_query(email_overrides.get(person_id))
            if not email_to:
                email_to = normalize_query((detail.get("person") or {}).get("email")) or normalize_query(
                    (detail.get("receipt") or {}).get("person_email")
                )
            if not email_to:
                continue
            dispatches.extend(
                queue_receipt_dispatches(
                    [receipt_id],
                    email_to=email_to,
                    subject=subject,
                    body=body,
                    actor=actor,
                    trigger=trigger,
                    auto_created=auto_created,
                    send_now=send_now,
                )
            )
    return {"receipt_ids": receipt_ids, "dispatches": dispatches}


def schedule_automatic_receipts_for_events(
    contribution_ids: list[int],
    *,
    actor: str = "",
    send_now: bool | None = None,
) -> list[dict[str, Any]]:
    if not receipt_email_enabled():
        return []
    effective_send_now = receipt_auto_send_enabled() if send_now is None else bool(send_now)
    outcomes: list[dict[str, Any]] = []
    clean_ids = [int(value or 0) for value in contribution_ids if int(value or 0)]
    if not clean_ids:
        return outcomes
    with connect_legacy() as conn:
        rows = conn.execute(
            f"""
            SELECT c.id, c.pessoa_id, p.nome, p.email_principal, c.data_recebimento, COALESCE(c.competencia, '') AS competencia
              FROM contribuicoes c
              JOIN pessoas p ON p.id = c.pessoa_id
             WHERE c.ativo = 1
               AND c.id IN ({','.join('?' for _ in clean_ids)})
               AND c.pessoa_id IS NOT NULL
             ORDER BY p.nome, c.data_recebimento, c.id
            """,
            tuple(clean_ids),
        ).fetchall()
    email_overrides: dict[int, str] = {}
    by_person: dict[int, dict[str, Any]] = {}
    for row in rows:
        person_id = int(row["pessoa_id"] or 0)
        if not person_id:
            continue
        email_overrides[person_id] = normalize_query(row["email_principal"])
        payload = by_person.setdefault(
            person_id,
            {
                "person_id": person_id,
                "person_name": row["nome"] or "",
                "email": normalize_query(row["email_principal"]),
                "contribution_ids": [],
                "dates": [],
                "competences": [],
            },
        )
        payload["contribution_ids"].append(int(row["id"] or 0))
        payload["dates"].append(row["data_recebimento"] or "")
        payload["competences"].append(normalize_query(row["competencia"]))
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
            trigger=ReceiptDispatch.Trigger.AUTOMATIC,
            auto_created=True,
            send_now=effective_send_now,
        )
        dispatch_by_receipt = {int(item.legacy_receipt_id or 0): int(item.pk or 0) for item in result["dispatches"]}
        for receipt_id in result["receipt_ids"]:
            detail = get_receipt_detail(receipt_id)
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
    with connect_legacy() as conn:
        rows = conn.execute(
            """
            SELECT c.id, c.pessoa_id, c.data_recebimento, COALESCE(p.email_principal, '') AS email_principal
              FROM contribuicoes c
              JOIN pessoas p ON p.id = c.pessoa_id
             WHERE c.ativo = 1
               AND p.ativo = 1
               AND COALESCE(p.email_principal, '') <> ''
               AND NOT EXISTS (
                    SELECT 1
                      FROM recibo_itens ri
                      JOIN recibos r ON r.id = ri.recibo_id
                     WHERE ri.contribuicao_id = c.id
                       AND r.status <> 'cancelado'
                       AND r.cancelado_em IS NULL
               )
             ORDER BY c.pessoa_id, c.data_recebimento, c.id
            """
        ).fetchall()
    grouped: dict[tuple[int, str], list[int]] = {}
    for row in rows:
        scanned += 1
        person_id = int(row["pessoa_id"] or 0)
        receipt_date = normalize_query(row["data_recebimento"])
        if not person_id or not receipt_date:
            continue
        grouped.setdefault((person_id, receipt_date), []).append(int(row["id"] or 0))
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

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import time
from typing import Any

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from power_church_core.formatting import br_date
from power_church_core.normalization import normalize_query
from power_church_django.apps.contributions.models import ReceiptDispatch, ReceiptEmailTemplate
from power_church_django.services.django_audit import record_django_audit_event
from power_church_django.services.legacy import connect_legacy, format_cpf, format_status, get_receipt_detail, status_sigla
from power_church_django.services.legacy_write import (
    issue_period_receipts,
    issue_receipt_for_contribution_ids,
    issue_receipts_for_event_contributions,
    preferred_delivery_email,
)
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
        "email": preferred_delivery_email(row["email_principal"], row["nome"]),
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
        detail = get_receipt_detail(receipt_id)
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


def _campaign_full_receipt_sets(
    *,
    cutoff_date: str = "",
) -> tuple[dict[int, dict[str, Any]], dict[int, list[dict[str, Any]]], dict[int, list[dict[str, Any]]]]:
    cutoff = normalize_query(cutoff_date)
    contribution_clause = ""
    contribution_params: list[Any] = []
    receipt_clause = ""
    receipt_params: list[Any] = []
    if cutoff:
        contribution_clause = " AND c.data_recebimento <= ?"
        contribution_params.append(cutoff)
        receipt_clause = " AND c.data_recebimento <= ?"
        receipt_params.append(cutoff)
    with connect_legacy() as conn:
        contribution_rows = conn.execute(
            f"""
            SELECT p.id AS person_id,
                   p.nome,
                   p.email_principal,
                   c.id AS contribution_id,
                   c.valor,
                   c.data_recebimento
              FROM pessoas p
              JOIN contribuicoes c ON c.pessoa_id = p.id
             WHERE p.ativo = 1
               AND c.ativo = 1
               AND COALESCE(p.email_principal, '') <> ''
               {contribution_clause}
             ORDER BY p.nome, c.data_recebimento, c.id
            """,
            tuple(contribution_params),
        ).fetchall()
        receipt_rows = conn.execute(
            f"""
            SELECT r.id AS receipt_id,
                   r.pessoa_id AS person_id,
                   r.numero,
                   r.data_emissao,
                   ri.contribuicao_id
              FROM recibos r
              JOIN recibo_itens ri ON ri.recibo_id = r.id
              JOIN contribuicoes c ON c.id = ri.contribuicao_id
             WHERE r.status <> 'cancelado'
               AND r.cancelado_em IS NULL
               {receipt_clause}
             ORDER BY r.pessoa_id, r.id, ri.id
            """,
            tuple(receipt_params),
        ).fetchall()
    people: dict[int, dict[str, Any]] = {}
    receipt_sets: dict[int, list[dict[str, Any]]] = {}
    contributions_by_person: dict[int, list[dict[str, Any]]] = {}
    for row in contribution_rows:
        person_id = int(row["person_id"] or 0)
        if not person_id:
            continue
        contributions_by_person.setdefault(person_id, []).append(
            {
                "id": int(row["contribution_id"] or 0),
                "value": round(float(row["valor"] or 0), 2),
                "date": row["data_recebimento"] or "",
            }
        )
        people.setdefault(
            person_id,
            {
                "person_id": person_id,
                "person_name": row["nome"] or "",
                "email": preferred_delivery_email(row["email_principal"], row["nome"]),
            },
        )
    for row in receipt_rows:
        person_id = int(row["person_id"] or 0)
        receipt_id = int(row["receipt_id"] or 0)
        if not person_id or not receipt_id:
            continue
        bucket = receipt_sets.setdefault(person_id, [])
        current = next((item for item in bucket if int(item["receipt_id"]) == receipt_id), None)
        if current is None:
            current = {
                "receipt_id": receipt_id,
                "receipt_number": row["numero"] or "",
                "emission_date": row["data_emissao"] or "",
                "contribution_ids": [],
            }
            bucket.append(current)
        current["contribution_ids"].append(int(row["contribuicao_id"] or 0))
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


def refresh_receipt_dispatch_destination(dispatch: ReceiptDispatch | int, *, actor: str = "") -> ReceiptDispatch:
    item = dispatch if isinstance(dispatch, ReceiptDispatch) else ReceiptDispatch.objects.get(pk=int(dispatch))
    detail = get_receipt_detail(int(item.legacy_receipt_id or 0))
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
                email_to = preferred_delivery_email(email_overrides.get(person_id), (detail.get("receipt") or {}).get("person_name"))
            if not email_to:
                email_to = preferred_delivery_email((detail.get("person") or {}).get("email"), (detail.get("receipt") or {}).get("person_name")) or preferred_delivery_email(
                    (detail.get("receipt") or {}).get("person_email"),
                    (detail.get("receipt") or {}).get("person_name"),
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
        email_overrides[person_id] = preferred_delivery_email(row["email_principal"], row["nome"])
        payload = by_person.setdefault(
            person_id,
            {
                "person_id": person_id,
                "person_name": row["nome"] or "",
                "email": preferred_delivery_email(row["email_principal"], row["nome"]),
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

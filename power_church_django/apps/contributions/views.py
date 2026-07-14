from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.http import FileResponse, Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from pathlib import Path
from urllib.parse import urlencode

from power_church_django.apps.contributions.models import ReceiptDispatch
from power_church_core.normalization import normalize_query
from power_church_django.services.access_control import module_permission_required, user_has_module_permission
from power_church_django.services.audit_native import search_receipt_people_postgres
from power_church_django.services.django_audit import record_django_audit_event
from power_church_django.services.contributions_native import (
    combine_contribution_dashboards,
    create_contribution_postgres,
    create_manual_contribution_batch_postgres,
    get_contribution_detail_postgres,
    list_contributions_postgres,
    manual_contribution_context_postgres,
    new_contribution_context_postgres,
    person_statement_data_postgres,
    split_contribution_context_postgres,
    split_contribution_postgres,
    update_contribution_postgres,
)
from power_church_django.services.envelopes_native import (
    apply_envelope_profile_update_postgres,
    backfill_envelope_profile_updates_postgres,
    combine_envelope_dashboards,
    create_envelope_contribution_batch_postgres,
    create_envelope_image_lot_postgres,
    envelope_contribution_context_postgres,
    envelope_lot_form_context_postgres,
    get_envelope_detail_postgres,
    get_envelope_lot_detail_postgres,
    get_next_pending_envelope_id_postgres,
    ignore_envelope_profile_update_postgres,
    ignore_pending_envelope_postgres,
    launched_envelope_edit_context_postgres,
    launch_pending_envelope_postgres,
    list_envelopes_postgres,
    pending_envelope_contribution_context_postgres,
    update_launched_envelope_postgres,
)
from power_church_django.services.contributors_native import (
    create_frequentador_from_contributor_postgres,
    get_contributor_detail_postgres,
    link_contributor_to_person_by_id_postgres,
    list_contributors_postgres,
    lookup_envelope_people_postgres,
    repoint_contributor_to_person_by_id_postgres,
    unlink_contributor_from_person_by_id_postgres,
    update_person_email_from_manual_delivery_postgres,
)
from power_church_django.services.runtime_support import envelope_upload_root
from power_church_django.services.runtime_errors import LegacyDatabaseError, LegacyWriteError
from power_church_django.services.pdf_reports import receipt_pdf, receipt_pdf_filename
from power_church_django.services.pdf_reports import person_statement_pdf, person_statement_pdf_filename
from power_church_django.services.receipt_delivery import (
    backfill_native_event_receipts,
    drain_receipt_dispatch_queue,
    email_runtime_snapshot,
    enrich_receipt_form,
    create_receipt,
    get_receipt_detail_cached,
    list_receipts_postgres,
    refresh_receipt_dispatch_destination,
    receipt_new_context_postgres,
    queue_receipt_dispatches,
    receipt_dispatch_history,
    send_receipt_dispatch,
    issue_and_optionally_send_receipts,
    update_receipt_email_template,
)
from power_church_django.services.mail_dispatch import MailAttachment, send_email_message


def _actor(request: HttpRequest) -> str:
    user = getattr(request, "user", None)
    if getattr(user, "is_authenticated", False):
        return str(user.username)
    return "django"


def _resolve_runtime_envelope_path(raw_path: object) -> Path:
    path_text = str(raw_path or "").strip()
    if not path_text:
        raise Http404("Imagem nao encontrada.")
    configured_root = Path(envelope_upload_root()).resolve()
    candidate = Path(path_text)
    if candidate.is_absolute():
        try:
            candidate.relative_to(configured_root)
            return candidate
        except ValueError:
            pass
    parts = candidate.parts
    if "envelope_uploads" in parts:
        suffix_parts = parts[parts.index("envelope_uploads") + 1 :]
        remapped = configured_root.joinpath(*suffix_parts).resolve()
        try:
            remapped.relative_to(configured_root)
        except ValueError as exc:
            raise Http404("Imagem fora da pasta de envelopes.") from exc
        return remapped
    remapped = (configured_root / candidate.name).resolve()
    try:
        remapped.relative_to(configured_root)
    except ValueError as exc:
        raise Http404("Imagem fora da pasta de envelopes.") from exc
    return remapped


def _envelope_hub() -> dict[str, object]:
    envelopes = list_envelopes_postgres()
    lots = list(envelopes.get("lots") or [])
    items = list(envelopes.get("items") or [])
    next_pending_lot = next((lot for lot in lots if lot.get("next_pending_url")), None)
    next_pending_item = next((item for item in items if item.get("launch_url")), None)
    latest_launched = next((item for item in items if item.get("edit_url")), None)
    recent_lot = lots[0] if lots else None
    pending_total = sum(int(lot.get("pendentes") or 0) + int(lot.get("em_digitacao") or 0) for lot in lots)
    return {
        "total": int(envelopes.get("total") or 0),
        "total_value_fmt": str(envelopes.get("total_value_fmt") or ""),
        "lots_count": len(lots),
        "pending_total": pending_total,
        "list_url": "/contributions/envelopes/",
        "new_lot_url": "/contributions/envelopes/lots/new/",
        "new_envelope_url": "/contributions/envelopes/new/",
        "next_pending_url": (
            str(next_pending_lot.get("next_pending_url") or "")
            if next_pending_lot
            else (str(next_pending_item.get("launch_url") or "") if next_pending_item else "")
        ),
        "next_pending_label": (
            f"{next_pending_lot['nome']} ({next_pending_lot['competencia']})"
            if next_pending_lot
            else (str(next_pending_item.get("nome") or "") if next_pending_item else "")
        ),
        "recent_lot_url": str(recent_lot.get("detail_url") or "") if recent_lot else "",
        "recent_lot_label": (
            f"{recent_lot['nome']} ({recent_lot['competencia']})"
            if recent_lot
            else ""
        ),
        "latest_edit_url": str(latest_launched.get("edit_url") or "") if latest_launched else "",
        "latest_edit_label": str(latest_launched.get("nome") or "") if latest_launched else "",
        "latest_detail_url": str(latest_launched.get("detail_url") or "") if latest_launched else "",
    }


def _notify_envelope_reconciliation(request: HttpRequest, result: dict[str, object]) -> None:
    reconciliation = result.get("reconciliation")
    if not isinstance(reconciliation, dict):
        return
    if reconciliation.get("matched"):
        source_label = str(reconciliation.get("source_label") or "fonte bancaria existente")
        matched_id = int(reconciliation.get("matched_contribution_id") or 0)
        messages.info(
            request,
            f"Conciliacao automatica aplicada: o envelope reaproveitou a contribuicao bancaria #{matched_id} via {source_label}; o valor nao foi duplicado.",
        )
        return
    if reconciliation.get("reason") == "multiple_candidates_forced_new":
        candidate_count = int(reconciliation.get("candidate_count") or 0)
        messages.info(
            request,
            f"Atencao do operador: havia {candidate_count} candidato(s) bancario(s) para conciliacao e a opcao foi lancar como novo. Revise depois na auditoria para evitar duplicidade.",
        )


def _receipt_hub_query(
    *,
    q: str = "",
    person_id: int = 0,
    date_start: str = "",
    date_end: str = "",
    selected_person_id: int = 0,
    person_lookup: str = "",
    form_date_start: str = "",
    form_date_end: str = "",
    generated_receipt_ids: list[int] | None = None,
) -> str:
    params: list[tuple[str, str]] = []
    if str(q or "").strip():
        params.append(("q", str(q).strip()))
    if int(person_id or 0):
        params.append(("person_id", str(int(person_id))))
    if str(date_start or "").strip():
        params.append(("date_start", str(date_start).strip()))
    if str(date_end or "").strip():
        params.append(("date_end", str(date_end).strip()))
    if int(selected_person_id or 0):
        params.append(("selected_person_id", str(int(selected_person_id))))
    if str(person_lookup or "").strip():
        params.append(("person_lookup", str(person_lookup).strip()))
    if str(form_date_start or "").strip():
        params.append(("form_date_start", str(form_date_start).strip()))
    if str(form_date_end or "").strip():
        params.append(("form_date_end", str(form_date_end).strip()))
    for item in generated_receipt_ids or []:
        if int(item or 0):
            params.append(("generated_receipt_id", str(int(item))))
    return urlencode(params)


def _receipt_hub_redirect(query: str = "") -> HttpResponse:
    return redirect(f"/receipts/{'?' + query if query else ''}")


def _receipt_message_fields(payload: object) -> dict[str, str]:
    getter = getattr(payload, "get", None)
    if getter is None:
        return {
            "email_to": "",
            "subject": "",
            "body": "",
            "default_from_email": "",
            "reply_to_email": "",
            "update_person_email": "",
            "email_update_reason": "",
        }
    return {
        "email_to": str(getter("email_to", "") or "").strip(),
        "subject": str(getter("email_subject", "") or "").strip(),
        "body": str(getter("email_body", "") or "").strip(),
        "default_from_email": str(getter("email_default_from", "") or "").strip(),
        "reply_to_email": str(getter("email_reply_to", "") or "").strip(),
        "update_person_email": str(getter("update_person_email", "") or "").strip(),
        "email_update_reason": str(getter("email_update_reason", "") or "").strip(),
    }


def _maybe_update_person_email_for_manual_receipt(*, person_id: int, fields: dict[str, str], actor: str) -> bool:
    if str(fields.get("update_person_email") or "") not in {"1", "on", "true", "sim"}:
        return False
    return update_person_email_from_manual_delivery_postgres(
        person_id,
        email_value=fields.get("email_to", ""),
        reason=fields.get("email_update_reason", ""),
        actor=actor,
        source="envio_manual_recibo",
    )


def _maybe_save_receipt_template(payload: object, *, actor: str) -> None:
    getter = getattr(payload, "get", None)
    if getter is None:
        return
    if str(getter("save_as_default", "") or "") not in {"1", "on", "true", "sim"}:
        return
    fields = _receipt_message_fields(payload)
    update_receipt_email_template(
        subject_template=fields["subject"],
        body_template=fields["body"],
        default_from_email=fields["default_from_email"],
        reply_to_email=fields["reply_to_email"],
        actor=actor,
    )


def _receipt_return_query(payload: object, fallback_selected_person_id: int = 0) -> str:
    getter = getattr(payload, "get", None)
    if getter is None:
        return _receipt_hub_query(selected_person_id=fallback_selected_person_id)
    return _receipt_hub_query(
        q=getter("return_q", ""),
        person_id=int(getter("return_person_id", "") or 0),
        date_start=getter("return_date_start", ""),
        date_end=getter("return_date_end", ""),
        selected_person_id=int(getter("return_selected_person_id", "") or fallback_selected_person_id or 0),
        person_lookup=getter("return_person_lookup", ""),
        form_date_start=getter("return_form_date_start", ""),
        form_date_end=getter("return_form_date_end", ""),
    )


def _generated_receipt_cards(receipt_ids: list[int]) -> list[dict[str, object]]:
    cards: list[dict[str, object]] = []
    for receipt_id in receipt_ids:
        detail = get_receipt_detail_cached(int(receipt_id or 0))
        if not detail:
            continue
        receipt = detail.get("receipt") or {}
        cards.append(
            {
                "id": int(receipt.get("id") or 0),
                "numero": receipt.get("numero") or "",
                "data": receipt.get("data") or "",
                "periodo": f"{receipt.get('periodo_inicio') or '-'} a {receipt.get('periodo_fim') or '-'}",
                "valor_fmt": receipt.get("valor_fmt") or "",
                "detail_url": f"/receipts/{int(receipt.get('id') or 0)}/",
                "pdf_url": f"/receipts/{int(receipt.get('id') or 0)}/pdf/",
            }
        )
    return cards


def _receipt_queue_campaigns(limit: int = 12) -> list[str]:
    values = (
        ReceiptDispatch.objects.exclude(metadata__campaign_key="")
        .order_by("-created_at", "-id")
        .values_list("metadata__campaign_key", flat=True)[: max(50, int(limit or 12) * 20)]
    )
    seen: set[str] = set()
    campaigns: list[str] = []
    for value in values:
        key = normalize_query(value)
        if not key or key in seen:
            continue
        seen.add(key)
        campaigns.append(key)
        if len(campaigns) >= int(limit or 12):
            break
    return campaigns


def _receipt_queue_snapshot(*, campaign_key: str = "", status: str = "", limit: int = 120) -> dict[str, object]:
    selected_campaign = normalize_query(campaign_key)
    selected_status = normalize_query(status)
    base_qs = ReceiptDispatch.objects.all()
    if selected_campaign:
        base_qs = base_qs.filter(metadata__campaign_key=selected_campaign)
    counts = {
        "pendente": int(base_qs.filter(status=ReceiptDispatch.Status.PENDING).count()),
        "enviado": int(base_qs.filter(status=ReceiptDispatch.Status.SENT).count()),
        "falhou": int(base_qs.filter(status=ReceiptDispatch.Status.FAILED).count()),
        "cancelado": int(base_qs.filter(status=ReceiptDispatch.Status.CANCELLED).count()),
    }
    total = sum(counts.values())
    actionable_total = counts["pendente"] + counts["enviado"] + counts["falhou"]
    progress = round((counts["enviado"] / actionable_total) * 100, 1) if actionable_total else 0.0
    list_qs = base_qs
    if selected_status:
        list_qs = list_qs.filter(status=selected_status)
    latest_attempt = base_qs.exclude(last_attempt_at__isnull=True).order_by("-last_attempt_at", "-id").first()
    latest_sent = base_qs.exclude(sent_at__isnull=True).order_by("-sent_at", "-id").first()
    items: list[dict[str, object]] = []
    for item in list_qs.order_by("-created_at", "-id")[: max(1, int(limit or 120))]:
        metadata = item.metadata or {}
        campaign = normalize_query(metadata.get("campaign_key"))
        items.append(
            {
                "id": int(item.pk or 0),
                "campaign_key": campaign,
                "person_name": item.person_name or f"Pessoa #{int(item.legacy_person_id or 0)}",
                "person_id": int(item.legacy_person_id or 0),
                "receipt_number": item.legacy_receipt_number or "",
                "receipt_id": int(item.legacy_receipt_id or 0),
                "period_label": item.period_label or "",
                "email_to": item.email_to or item.person_email or "",
                "status": item.get_status_display(),
                "status_code": str(item.status or ""),
                "trigger": item.get_trigger_display(),
                "attempts": int(item.send_attempts or 0),
                "created_at": timezone.localtime(item.created_at).strftime("%d/%m/%Y %H:%M") if item.created_at else "",
                "updated_at": timezone.localtime(item.updated_at).strftime("%d/%m/%Y %H:%M") if item.updated_at else "",
                "last_attempt_at": timezone.localtime(item.last_attempt_at).strftime("%d/%m/%Y %H:%M") if item.last_attempt_at else "",
                "sent_at": timezone.localtime(item.sent_at).strftime("%d/%m/%Y %H:%M") if item.sent_at else "",
                "last_error": item.last_error or "",
                "person_url": f"/people/{int(item.legacy_person_id or 0)}/" if int(item.legacy_person_id or 0) else "",
                "receipt_url": f"/receipts/{int(item.legacy_receipt_id or 0)}/" if int(item.legacy_receipt_id or 0) else "",
            }
        )
    return {
        "campaign_key": selected_campaign,
        "status": selected_status,
        "counts": counts,
        "total": total,
        "progress_percent": progress,
        "latest_attempt": timezone.localtime(latest_attempt.last_attempt_at).strftime("%d/%m/%Y %H:%M") if latest_attempt and latest_attempt.last_attempt_at else "",
        "latest_sent": timezone.localtime(latest_sent.sent_at).strftime("%d/%m/%Y %H:%M") if latest_sent and latest_sent.sent_at else "",
        "items": items,
        "campaigns": _receipt_queue_campaigns(),
    }


def _receipt_queue_filtered_queryset(*, campaign_key: str = "", status: str = ""):
    selected_campaign = normalize_query(campaign_key)
    selected_status = normalize_query(status)
    queryset = ReceiptDispatch.objects.all()
    if selected_campaign:
        queryset = queryset.filter(metadata__campaign_key=selected_campaign)
    if selected_status:
        queryset = queryset.filter(status=selected_status)
    return queryset


def _statement_query(
    *,
    year: str = "",
    competencia: str = "",
    date_start: str = "",
    date_end: str = "",
    type_ids: list[int] | None = None,
) -> str:
    params: list[tuple[str, str]] = []
    if str(year or "").strip():
        params.append(("year", str(year).strip()))
    if str(competencia or "").strip():
        params.append(("competencia", str(competencia).strip()))
    if str(date_start or "").strip():
        params.append(("date_start", str(date_start).strip()))
    if str(date_end or "").strip():
        params.append(("date_end", str(date_end).strip()))
    for item in type_ids or []:
        if int(item or 0):
            params.append(("tipo_id", str(int(item))))
    return urlencode(params)


def _statement_email_defaults(statement: dict[str, object]) -> dict[str, str]:
    person = statement.get("person") or {}
    filters = statement.get("filters") or {}
    person_name = str(person.get("nome") or "")
    period_label = ""
    if filters.get("competencia"):
        period_label = str(filters.get("competencia") or "")
    elif filters.get("date_start") or filters.get("date_end"):
        period_label = f"{filters.get('date_start') or '-'} a {filters.get('date_end') or '-'}"
    elif filters.get("year"):
        period_label = f"Ano {filters.get('year')}"
    else:
        period_label = "historico completo"
    subject = f"Extrato de contribuicoes - {person_name} - {period_label}".strip(" -")
    body = (
        f"Prezado(a) {person_name},\n\n"
        f"Segue em anexo o extrato de contribuicoes referente a {period_label}.\n\n"
        "Este documento apresenta o historico detalhado dos lancamentos registrados no periodo, "
        "incluindo origem, tipo, forma e observacoes quando houver.\n\n"
        "Se identificar qualquer divergencia, por favor entre em contato com a tesouraria para conferencia.\n\n"
        "Atenciosamente,\n"
        "Tesouraria / Recebimento"
    )
    return {
        "email_to": str(person.get("email") or ""),
        "subject": subject,
        "body": body,
        "default_from_email": getattr(settings, "DEFAULT_FROM_EMAIL", ""),
        "reply_to_email": getattr(settings, "POWER_CHURCH_RECEIPT_REPLY_TO", ""),
    }


def _statement_message_fields(payload: object) -> dict[str, str]:
    getter = getattr(payload, "get", None)
    if getter is None:
        return {
            "email_to": "",
            "subject": "",
            "body": "",
            "default_from_email": "",
            "reply_to_email": "",
            "update_person_email": "",
            "email_update_reason": "",
        }
    return {
        "email_to": str(getter("email_to", "") or "").strip(),
        "subject": str(getter("email_subject", "") or "").strip(),
        "body": str(getter("email_body", "") or "").strip(),
        "default_from_email": str(getter("email_default_from", "") or "").strip(),
        "reply_to_email": str(getter("email_reply_to", "") or "").strip(),
        "update_person_email": str(getter("update_person_email", "") or "").strip(),
        "email_update_reason": str(getter("email_update_reason", "") or "").strip(),
    }


def _receipt_queue_return_response(request: HttpRequest, *, campaign: str = "", status: str = "", auto_refresh: bool = False) -> HttpResponse:
    return_to = normalize_query(request.POST.get("return_to", ""))
    if return_to == "audit":
        params: list[tuple[str, str]] = [("modo", "emails")]
        email_kind = str(request.POST.get("return_email_kind", "") or "").strip()
        email_status = str(request.POST.get("return_email_status", "") or "").strip()
        q = str(request.POST.get("return_q", "") or "").strip()
        selected_person_id = int(request.POST.get("return_selected_person_id") or 0)
        person_lookup = str(request.POST.get("return_person_lookup", "") or "").strip()
        page = int(request.POST.get("return_page") or 1)
        page_size = int(request.POST.get("return_page_size") or 120)
        if email_kind:
            params.append(("email_kind", email_kind))
        if email_status:
            params.append(("email_status", email_status))
        if q:
            params.append(("q", q))
        if selected_person_id:
            params.append(("selected_person_id", str(selected_person_id)))
        if person_lookup:
            params.append(("person_lookup", person_lookup))
        if page > 1:
            params.append(("page", str(page)))
        if page_size != 120:
            params.append(("page_size", str(page_size)))
        query = urlencode(params)
        return redirect(f"/audit/?{query}" if query else "/audit/?modo=emails")
    if return_to == "receipts":
        return redirect("/receipts/")
    query = urlencode(
        {
            "campaign": campaign,
            "status": status,
            "auto": "1" if auto_refresh else "",
        }
    )
    return redirect(f"/receipts/queue/?{query}" if query else "/receipts/queue/")


@module_permission_required("view_contributions")
def index(request: HttpRequest) -> HttpResponse:
    context = {
        "title": "Contribuicoes",
        "q": request.GET.get("q", ""),
        "competencia": request.GET.get("competencia", ""),
        "status": request.GET.get("status", ""),
    }
    context["contributions"] = list_contributions_postgres(
        q=context["q"],
        competencia=context["competencia"],
        status=context["status"],
    )
    context["envelope_hub"] = _envelope_hub()
    return render(request, "power_church_django/contributions/list.html", context)


@module_permission_required("manage_contributions")
def new(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        person_id = request.POST.get("pessoa_id", "")
        try:
            contribution_id = create_contribution_postgres(request.POST, actor=_actor(request))
            messages.success(request, f"Contribuicao #{contribution_id} registrada com auditoria.")
            return redirect(f"/contributions/{contribution_id}/")
        except LegacyWriteError as exc:
            messages.error(request, str(exc))
            return redirect(f"/contributions/new/?person_id={person_id}")

    person_id = int(request.GET.get("person_id") or 0)
    context = {"title": "Nova contribuicao", "person_id": person_id, "form_data": new_contribution_context_postgres(person_id)}
    return render(request, "power_church_django/contributions/form.html", context)


@module_permission_required("manage_contributions")
def manual_batch(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        try:
            contribution_ids = create_manual_contribution_batch_postgres(request.POST, actor=_actor(request))
            messages.success(request, f"{len(contribution_ids)} contribuicao(oes) registrada(s) com auditoria.")
            return redirect("/contributions/")
        except LegacyWriteError as exc:
            messages.error(request, str(exc))
            return redirect("/contributions/manual/")

    context = {"title": "Lancamento manual assistido", "form_data": manual_contribution_context_postgres()}
    return render(request, "power_church_django/contributions/manual_batch.html", context)


@module_permission_required("view_contributions")
def envelopes(request: HttpRequest) -> HttpResponse:
    context = {
        "title": "Envelopes de contribuicao",
        "q": request.GET.get("q", ""),
        "competencia": request.GET.get("competencia", ""),
    }
    context["envelopes"] = list_envelopes_postgres(
        q=context["q"],
        competencia=context["competencia"],
    )
    context["envelope_hub"] = _envelope_hub()
    return render(request, "power_church_django/contributions/envelopes.html", context)


@module_permission_required("manage_contributions")
def envelope_new(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        try:
            result = create_envelope_contribution_batch_postgres(
                request.POST,
                request.FILES.get("imagem_envelope"),
                actor=_actor(request),
            )
            messages.success(
                request,
                f"Envelope #{result['envelope_id']} registrado com {len(result['contribution_ids'])} linha(s) e imagem arquivada.",
            )
            _notify_envelope_reconciliation(request, result)
            return redirect(f"/contributions/envelopes/{result['envelope_id']}/")
        except (LegacyWriteError, ValueError) as exc:
            messages.error(request, str(exc))
            return redirect("/contributions/envelopes/new/")

    context = {"title": "Novo envelope"}
    try:
        context["form_data"] = envelope_contribution_context_postgres()
    except LegacyDatabaseError as exc:
        context["error"] = str(exc)
    return render(request, "power_church_django/contributions/envelope_form.html", context)


@module_permission_required("manage_contributions")
def envelope_lot_new(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        try:
            result = create_envelope_image_lot_postgres(
                request.POST,
                request.FILES.getlist("imagens_envelope"),
                request.FILES.get("arquivo_zip_lote"),
                actor=_actor(request),
            )
            duplicates = len(result.get("duplicates") or [])
            suffix = f" ({duplicates} duplicado(s) marcados para revisao)" if duplicates else ""
            messages.success(
                request,
                f"Lote de envelopes criado com {len(result.get('envelope_ids') or [])} arquivo(s){suffix}.",
            )
            return redirect(f"/contributions/envelopes/lots/{result['lot_id']}/")
        except (LegacyWriteError, ValueError) as exc:
            messages.error(request, str(exc))
            return redirect("/contributions/envelopes/lots/new/")

    context = {"title": "Criar lote de envelopes"}
    try:
        context["form_data"] = envelope_lot_form_context_postgres()
    except LegacyDatabaseError as exc:
        context["error"] = str(exc)
    return render(request, "power_church_django/contributions/envelope_lot_form.html", context)


@module_permission_required("view_contributions")
def envelope_lot_detail(request: HttpRequest, lot_id: int) -> HttpResponse:
    context = {"title": "Lote de envelopes"}
    context["lot"] = get_envelope_lot_detail_postgres(lot_id)
    if not context.get("lot") and not context.get("error"):
        raise Http404("Lote de envelopes nao encontrado.")
    return render(request, "power_church_django/contributions/envelope_lot_detail.html", context)


@module_permission_required("manage_contributions")
def envelope_lot_next(request: HttpRequest, lot_id: int) -> HttpResponse:
    envelope_id = get_next_pending_envelope_id_postgres(lot_id, actor=_actor(request))
    if not envelope_id:
        messages.info(request, "Nao ha envelopes disponiveis neste lote no momento.")
        return redirect(f"/contributions/envelopes/lots/{lot_id}/")
    return redirect(f"/contributions/envelopes/{envelope_id}/launch/")


@module_permission_required("manage_contributions")
def envelope_launch(request: HttpRequest, envelope_id: int) -> HttpResponse:
    if request.method == "POST":
        lot_id = int(request.POST.get("lote_id") or 0)
        try:
            result = launch_pending_envelope_postgres(envelope_id, request.POST, actor=_actor(request))
            next_id = get_next_pending_envelope_id_postgres(int(result["lot_id"]), actor=_actor(request))
            lot_id = int(result["lot_id"])
            messages.success(
                request,
                f"Envelope #{envelope_id} lancado com {len(result['contribution_ids'])} contribuicao(oes).",
            )
            _notify_envelope_reconciliation(request, result)
            if next_id:
                return redirect(f"/contributions/envelopes/{next_id}/launch/")
            return redirect(f"/contributions/envelopes/lots/{lot_id}/")
        except (LegacyWriteError, ValueError) as exc:
            messages.error(request, str(exc))
            return redirect(f"/contributions/envelopes/{envelope_id}/launch/")

    context = {"title": "Digitar envelope"}
    try:
        context["form_data"] = pending_envelope_contribution_context_postgres(envelope_id, actor=_actor(request))
    except LegacyWriteError as exc:
        messages.info(request, str(exc))
        return redirect("/contributions/envelopes/")
    if not context.get("form_data") and not context.get("error"):
        raise Http404("Envelope pendente nao encontrado.")
    return render(request, "power_church_django/contributions/envelope_form.html", context)


@module_permission_required("manage_contributions")
def envelope_edit(request: HttpRequest, envelope_id: int) -> HttpResponse:
    if request.method == "POST":
        try:
            result = update_launched_envelope_postgres(envelope_id, request.POST, actor=_actor(request))
            messages.success(
                request,
                f"Envelope #{envelope_id} corrigido com {len(result['contribution_ids'])} linha(s) ativa(s); versao anterior preservada na auditoria.",
            )
            _notify_envelope_reconciliation(request, result)
            return redirect(f"/contributions/envelopes/{envelope_id}/")
        except (LegacyWriteError, ValueError) as exc:
            messages.error(request, str(exc))
            return redirect(f"/contributions/envelopes/{envelope_id}/edit/")

    context = {"title": "Editar envelope"}
    context["form_data"] = launched_envelope_edit_context_postgres(envelope_id)
    if not context.get("form_data") and not context.get("error"):
        raise Http404("Envelope lancado nao encontrado.")
    return render(request, "power_church_django/contributions/envelope_form.html", context)


@module_permission_required("manage_contributions")
def envelope_ignore(request: HttpRequest, envelope_id: int) -> HttpResponse:
    if request.method != "POST":
        return redirect(f"/contributions/envelopes/{envelope_id}/launch/")
    lot_id = int(request.POST.get("lote_id") or 0)
    try:
        ignore_pending_envelope_postgres(envelope_id, request.POST.get("justificativa_ignorar", ""), actor=_actor(request))
        messages.success(request, f"Envelope #{envelope_id} ignorado com justificativa.")
    except LegacyWriteError as exc:
        messages.error(request, str(exc))
        return redirect(f"/contributions/envelopes/{envelope_id}/launch/")
    if lot_id:
        return redirect(f"/contributions/envelopes/lots/{lot_id}/")
    return redirect("/contributions/envelopes/")


@module_permission_required("manage_contributions")
def envelope_lookup(request: HttpRequest) -> JsonResponse:
    phone = request.GET.get("phone", "")
    address = request.GET.get("address", "")
    try:
        payload = lookup_envelope_people_postgres(phone=phone, address=address)
    except LegacyDatabaseError as exc:
        return JsonResponse({"ok": False, "error": str(exc), "phone_matches": [], "address_matches": []}, status=500)
    return JsonResponse({"ok": True, **payload})


@module_permission_required("manage_contributions")
def envelope_profile_update_apply(request: HttpRequest, update_id: int) -> HttpResponse:
    if request.method != "POST":
        return redirect("/contributions/envelopes/")
    envelope_id = int(request.POST.get("envelope_id") or 0)
    try:
        result = apply_envelope_profile_update_postgres(update_id, actor=_actor(request))
        envelope_id = int(result["envelope_id"])
        messages.success(request, f"Telefone aplicado na ficha da pessoa vinculada ao envelope #{envelope_id}.")
    except LegacyWriteError as exc:
        messages.error(request, str(exc))
    if envelope_id:
        return redirect(f"/contributions/envelopes/{envelope_id}/")
    return redirect("/contributions/envelopes/")


@module_permission_required("manage_contributions")
def envelope_profile_update_ignore(request: HttpRequest, update_id: int) -> HttpResponse:
    if request.method != "POST":
        return redirect("/contributions/envelopes/")
    envelope_id = int(request.POST.get("envelope_id") or 0)
    try:
        result = ignore_envelope_profile_update_postgres(update_id, actor=_actor(request))
        envelope_id = int(result["envelope_id"])
        messages.success(request, f"Pendencia cadastral do envelope #{envelope_id} marcada como revisada.")
    except LegacyWriteError as exc:
        messages.error(request, str(exc))
    if envelope_id:
        return redirect(f"/contributions/envelopes/{envelope_id}/")
    return redirect("/contributions/envelopes/")


@module_permission_required("manage_contributions")
def envelope_profile_update_backfill(request: HttpRequest) -> HttpResponse:
    if request.method != "POST":
        return redirect("/contributions/envelopes/")
    try:
        result = backfill_envelope_profile_updates_postgres(actor=_actor(request))
        messages.success(
            request,
            f"Reprocessamento concluido: {result['scanned']} envelope(s) verificado(s) e {result['created']} pendencia(s) criada(s).",
        )
    except LegacyWriteError as exc:
        messages.error(request, str(exc))
    return redirect("/contributions/envelopes/")


@module_permission_required("view_contributions")
def envelope_detail(request: HttpRequest, envelope_id: int) -> HttpResponse:
    context = {"title": "Envelope"}
    context["detail"] = get_envelope_detail_postgres(envelope_id)
    if not context.get("detail") and not context.get("error"):
        raise Http404("Envelope nao encontrado.")
    return render(request, "power_church_django/contributions/envelope_detail.html", context)


@module_permission_required("view_contributions")
def envelope_image(request: HttpRequest, envelope_id: int) -> HttpResponse:
    detail = get_envelope_detail_postgres(envelope_id)
    if not detail or not detail.get("has_image"):
        raise Http404("Imagem nao encontrada.")
    root = Path(envelope_upload_root()).resolve()
    path = _resolve_runtime_envelope_path(detail.get("image_path"))
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise Http404("Imagem fora da pasta de envelopes.") from exc
    if not path.exists():
        raise Http404("Arquivo do envelope nao encontrado.")
    content_type = str(detail.get("image_content_type") or "") or None
    return FileResponse(path.open("rb"), content_type=content_type)


@module_permission_required("view_contributions", "manage_contributions")
def detail(request: HttpRequest, contribution_id: int) -> HttpResponse:
    if request.method == "POST":
        try:
            update_contribution_postgres(contribution_id, request.POST, actor=_actor(request))
            messages.success(request, f"Contribuicao #{contribution_id} ajustada com auditoria.")
        except LegacyWriteError as exc:
            messages.error(request, str(exc))
        return redirect(f"/contributions/{contribution_id}/")

    context = {"title": "Contribuicao", "detail": get_contribution_detail_postgres(contribution_id)}
    return render(request, "power_church_django/contributions/detail.html", context)


@module_permission_required("manage_contributions")
def split(request: HttpRequest, contribution_id: int) -> HttpResponse:
    if request.method == "POST":
        try:
            contribution_ids = split_contribution_postgres(contribution_id, request.POST, actor=_actor(request))
            messages.success(request, f"Rateio salvo com {len(contribution_ids)} linha(s) e soma conferida.")
            return redirect(f"/contributions/{contribution_id}/")
        except LegacyWriteError as exc:
            messages.error(request, str(exc))
            return redirect(f"/contributions/{contribution_id}/split/")

    context = {"title": "Ratear contribuicao"}
    context["form_data"] = split_contribution_context_postgres(contribution_id)
    return render(request, "power_church_django/contributions/split.html", context)


@module_permission_required("view_contributions", "manage_contributions")
def person_statement(request: HttpRequest, person_id: int) -> HttpResponse:
    context = {
        "title": "Extrato de contribuicoes",
        "year": request.GET.get("year", ""),
        "competencia": request.GET.get("competencia", ""),
        "date_start": request.GET.get("date_start", ""),
        "date_end": request.GET.get("date_end", ""),
        "type_ids": [int(value) for value in request.GET.getlist("tipo_id") if str(value).isdigit()],
    }
    try:
        statement = person_statement_data_postgres(
            person_id,
            year=context["year"],
            competencia=context["competencia"],
            date_start=context["date_start"],
            date_end=context["date_end"],
            type_ids=context["type_ids"],
        )
        context["statement"] = statement
        if statement:
            context["statement_email"] = _statement_email_defaults(statement)
        if request.method == "POST":
            if not statement:
                raise LegacyWriteError("Extrato nao encontrado para envio.")
            fields = _statement_message_fields(request.POST)
            if str(fields.get("update_person_email") or "") in {"1", "on", "true", "sim"}:
                updated = update_person_email_from_manual_delivery_postgres(
                    person_id,
                    email_value=fields.get("email_to", ""),
                    reason=fields.get("email_update_reason", ""),
                    actor=_actor(request),
                    source="envio_manual_extrato",
                )
                if updated:
                    messages.info(request, "E-mail da ficha atualizado durante o envio manual do extrato.")
                    statement = person_statement_data_postgres(
                        person_id,
                        year=context["year"],
                        competencia=context["competencia"],
                        date_start=context["date_start"],
                        date_end=context["date_end"],
                        type_ids=context["type_ids"],
                    )
                    context["statement"] = statement
            payload = person_statement_pdf(context["statement"])
            filename = person_statement_pdf_filename(context["statement"])
            from_email = normalize_query(fields["default_from_email"]) or getattr(settings, "DEFAULT_FROM_EMAIL", "") or "recebimento@localhost"
            reply_to = [value for value in [fields["reply_to_email"]] if normalize_query(value)]
            result = send_email_message(
                subject=fields["subject"] or context["statement_email"]["subject"],
                body=fields["body"] or context["statement_email"]["body"],
                from_email=from_email,
                to_emails=[fields["email_to"] or context["statement_email"]["email_to"]],
                reply_to=reply_to,
                attachments=[MailAttachment(filename=filename, content=payload, content_type="application/pdf")],
            )
            record_django_audit_event(
                actor=_actor(request),
                action="enviar_extrato_email_django",
                table_name="pessoas",
                record_id=int(person_id or 0),
                source="statement_email",
                summary=f"Extrato enviado por e-mail para {fields['email_to'] or context['statement_email']['email_to']}",
                after={
                    "person_id": int(person_id or 0),
                    "person_name": (context["statement"].get("person") or {}).get("nome") or "",
                    "email_to": fields["email_to"] or context["statement_email"]["email_to"],
                    "from_email": from_email,
                    "reply_to": reply_to,
                    "subject": fields["subject"] or context["statement_email"]["subject"],
                    "body": fields["body"] or context["statement_email"]["body"],
                    "provider": result.provider,
                    "filename": filename,
                    "query": _statement_query(
                        year=context["year"],
                        competencia=context["competencia"],
                        date_start=context["date_start"],
                        date_end=context["date_end"],
                        type_ids=context["type_ids"],
                    ),
                },
            )
            messages.success(request, "Extrato processado para envio manual.")
            return redirect(
                f"/contributions/statements/{person_id}/"
                + (f"?{_statement_query(year=context['year'], competencia=context['competencia'], date_start=context['date_start'], date_end=context['date_end'], type_ids=context['type_ids'])}" if _statement_query(year=context['year'], competencia=context['competencia'], date_start=context['date_start'], date_end=context['date_end'], type_ids=context['type_ids']) else "")
            )
    except LegacyDatabaseError as exc:
        context["error"] = str(exc)
    except LegacyWriteError as exc:
        messages.error(request, str(exc))
    except Exception as exc:
        messages.error(request, f"Nao foi possivel enviar o extrato: {exc}")
    return render(request, "power_church_django/contributions/statement.html", context)


@module_permission_required("view_contributions")
def person_statement_pdf_view(request: HttpRequest, person_id: int) -> HttpResponse:
    statement = person_statement_data_postgres(
        person_id,
        year=request.GET.get("year", ""),
        competencia=request.GET.get("competencia", ""),
        date_start=request.GET.get("date_start", ""),
        date_end=request.GET.get("date_end", ""),
        type_ids=[int(value) for value in request.GET.getlist("tipo_id") if str(value).isdigit()],
    )
    if not statement:
        raise Http404("Extrato nao encontrado.")
    payload = person_statement_pdf(statement)
    response = HttpResponse(payload, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{person_statement_pdf_filename(statement)}"'
    return response


@module_permission_required("view_contributions", "manage_contributions")
def receipts(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        person_id = int(request.POST.get("pessoa_id") or 0)
        action = str(request.POST.get("action") or "generate_consolidated")
        try:
            actor = _actor(request)
            fields = _receipt_message_fields(request.POST)
            if action == "save_email_template":
                update_receipt_email_template(
                    subject_template=fields["subject"],
                    body_template=fields["body"],
                    default_from_email=fields["default_from_email"],
                    reply_to_email=fields["reply_to_email"],
                    actor=actor,
                )
                messages.success(request, "Modelo padrao de e-mail do recibo atualizado.")
                return _receipt_hub_redirect(_receipt_return_query(request.POST, fallback_selected_person_id=person_id))
            _maybe_save_receipt_template(request.POST, actor=actor)
            if action in {"generate_competence", "generate_and_send_competence"}:
                competences = [value for value in request.POST.getlist("competencia_key") if str(value).strip()]
                if not competences:
                    raise LegacyWriteError("Selecione pelo menos uma competencia para gerar os recibos.")
                email_updated = False
                if action == "generate_and_send_competence":
                    email_updated = _maybe_update_person_email_for_manual_receipt(
                        person_id=person_id,
                        fields=fields,
                        actor=actor,
                    )
                result = issue_and_optionally_send_receipts(
                    person_id=person_id,
                    competences=competences,
                    emission_date=str(request.POST.get("data_emissao") or ""),
                    notes=str(request.POST.get("observacoes") or ""),
                    email_to=fields["email_to"] if action == "generate_and_send_competence" else "",
                    subject=fields["subject"],
                    body=fields["body"],
                    actor=actor,
                    send_now=action == "generate_and_send_competence",
                )
                if email_updated:
                    messages.info(request, "E-mail da ficha atualizado durante o envio manual dos recibos.")
                messages.success(request, f"{len(result['receipt_ids'])} recibo(s) gerado(s) por competencia.")
                base_query = _receipt_return_query(request.POST, fallback_selected_person_id=person_id)
                query = _receipt_hub_query(
                    q=request.POST.get("return_q", ""),
                    person_id=int(request.POST.get("return_person_id", "") or 0),
                    date_start=request.POST.get("return_date_start", ""),
                    date_end=request.POST.get("return_date_end", ""),
                    selected_person_id=int(request.POST.get("return_selected_person_id", "") or person_id or 0),
                    person_lookup=request.POST.get("return_person_lookup", ""),
                    form_date_start=request.POST.get("return_form_date_start", ""),
                    form_date_end=request.POST.get("return_form_date_end", ""),
                    generated_receipt_ids=[int(item) for item in result["receipt_ids"]],
                )
                return _receipt_hub_redirect(query or base_query)
            receipt_id = create_receipt(request.POST, actor=actor, replace_existing=True)
            if action == "generate_and_send_consolidated":
                email_updated = _maybe_update_person_email_for_manual_receipt(
                    person_id=person_id,
                    fields=fields,
                    actor=actor,
                )
                queue_receipt_dispatches(
                    [receipt_id],
                    email_to=fields["email_to"],
                    subject=fields["subject"],
                    body=fields["body"],
                    actor=actor,
                    send_now=True,
                )
                if email_updated:
                    messages.info(request, "E-mail da ficha atualizado durante o envio manual do recibo.")
                messages.success(request, f"Recibo #{receipt_id} gerado e processado para envio.")
            else:
                messages.success(request, f"Recibo #{receipt_id} gerado com auditoria.")
            return redirect(f"/receipts/{receipt_id}/")
        except LegacyWriteError as exc:
            messages.error(request, str(exc))
            return _receipt_hub_redirect(_receipt_return_query(request.POST, fallback_selected_person_id=person_id))
    context = {
        "title": "Recibos",
        "q": request.GET.get("q", ""),
        "person_id": int(request.GET.get("person_id") or 0),
        "date_start": request.GET.get("date_start", ""),
        "date_end": request.GET.get("date_end", ""),
        "selected_person_id": int(request.GET.get("selected_person_id") or 0),
        "person_lookup": request.GET.get("person_lookup", ""),
        "form_date_start": request.GET.get("form_date_start", ""),
        "form_date_end": request.GET.get("form_date_end", ""),
        "generated_receipt_ids": [int(value) for value in request.GET.getlist("generated_receipt_id") if str(value).isdigit()],
        "can_manage_receipts": user_has_module_permission(request.user, "manage_contributions"),
    }
    try:
        context["receipts"] = list_receipts_postgres(
            q=context["q"],
            person_id=context["person_id"],
            date_start=context["date_start"],
            date_end=context["date_end"],
        )
        context["receipt_people"] = search_receipt_people_postgres(context["person_lookup"]) if context["person_lookup"] else []
        if context["selected_person_id"]:
            context["receipt_form"] = enrich_receipt_form(
                receipt_new_context_postgres(
                    context["selected_person_id"],
                    date_start=context["form_date_start"],
                    date_end=context["form_date_end"],
                ),
                selected_competences=request.GET.getlist("competencia_key"),
            )
            if context["receipt_form"] is None:
                context["selected_person_id"] = 0
                messages.error(request, "Pessoa selecionada para gerar recibo nao foi encontrada.")
        context["generated_receipts"] = _generated_receipt_cards(context["generated_receipt_ids"])
        latest_campaigns = _receipt_queue_campaigns(limit=1)
        context["queue_overview"] = _receipt_queue_snapshot(
            campaign_key=latest_campaigns[0] if latest_campaigns else "",
            limit=10,
        )
        context["email_runtime"] = email_runtime_snapshot()
    except LegacyDatabaseError as exc:
        context["error"] = str(exc)
    return render(request, "power_church_django/receipts/list.html", context)


@module_permission_required("view_contributions", "manage_contributions")
def receipt_queue_monitor(request: HttpRequest) -> HttpResponse:
    selected_campaign = normalize_query(request.GET.get("campaign", request.POST.get("campaign", "")))
    selected_status = normalize_query(request.GET.get("status", request.POST.get("status", "")))
    auto_refresh = str(request.GET.get("auto", "") or "") in {"1", "on", "true", "sim"}
    if request.method == "POST":
        actor = _actor(request)
        action = normalize_query(request.POST.get("action", ""))
        try:
            if action == "reprocess_dispatch":
                dispatch_id = int(request.POST.get("dispatch_id") or 0)
                if not dispatch_id:
                    raise LegacyWriteError("Selecione uma falha valida para reprocessar.")
                item = ReceiptDispatch.objects.filter(pk=dispatch_id).first()
                if item is None:
                    raise LegacyWriteError("Registro de fila nao encontrado para reprocessamento.")
                if str(item.status or "") not in {ReceiptDispatch.Status.FAILED, ReceiptDispatch.Status.PENDING}:
                    raise LegacyWriteError("Somente falhas ou pendencias podem ser reprocessadas manualmente.")
                updated = send_receipt_dispatch(item, actor=actor)
                if str(updated.status or "") == ReceiptDispatch.Status.SENT:
                    messages.success(request, f"Recibo {updated.legacy_receipt_number or updated.legacy_receipt_id} reprocessado com sucesso.")
                else:
                    messages.error(request, updated.last_error or "O reprocessamento nao concluiu o envio.")
            elif action == "refresh_dispatch_destination":
                dispatch_id = int(request.POST.get("dispatch_id") or 0)
                if not dispatch_id:
                    raise LegacyWriteError("Selecione um item valido para atualizar o destinatario.")
                item = ReceiptDispatch.objects.filter(pk=dispatch_id).first()
                if item is None:
                    raise LegacyWriteError("Registro de fila nao encontrado para sincronizar destinatario.")
                updated = refresh_receipt_dispatch_destination(item, actor=actor)
                messages.success(request, f"Destino sincronizado para {updated.email_to or updated.person_email or 'sem e-mail'}.")
            elif action == "reprocess_filtered":
                queryset = _receipt_queue_filtered_queryset(campaign_key=selected_campaign, status=selected_status).filter(
                    status__in=[ReceiptDispatch.Status.FAILED, ReceiptDispatch.Status.PENDING]
                )
                ids = list(queryset.order_by("created_at", "id").values_list("id", flat=True)[:100])
                if not ids:
                    raise LegacyWriteError("Nao ha falhas ou pendencias neste filtro para reprocessar.")
                sent = 0
                failed = 0
                for dispatch_id in ids:
                    updated = send_receipt_dispatch(int(dispatch_id), actor=actor)
                    if str(updated.status or "") == ReceiptDispatch.Status.SENT:
                        sent += 1
                    else:
                        failed += 1
                messages.success(request, f"Reprocessamento do filtro concluido: {sent} enviado(s), {failed} ainda com falha.")
            elif action == "backfill_automatic_pending":
                summary = backfill_native_event_receipts(actor=actor)
                messages.success(
                    request,
                    "Recibos automaticos pendentes reenfileirados: "
                    f"{int(summary.get('created', 0) or 0)} criado(s), "
                    f"{int(summary.get('queued', 0) or 0)} em fila, "
                    f"{int(summary.get('without_email', 0) or 0)} sem e-mail, "
                    f"{int(summary.get('failed', 0) or 0)} falha(s).",
                )
            elif action == "drain_pending_queue":
                result = drain_receipt_dispatch_queue(
                    actor=actor,
                    campaign_key=selected_campaign,
                    limit=int(request.POST.get("batch_limit") or 40),
                    pending_only=False,
                    sleep_seconds=float(request.POST.get("sleep_seconds") or 3),
                    pause_every=int(request.POST.get("pause_every") or 40),
                    pause_seconds=float(request.POST.get("pause_seconds") or 60),
                    drain=False,
                )
                messages.success(
                    request,
                    "Processamento da fila concluido: "
                    f"{int(result.get('sent', 0) or 0)} enviado(s), "
                    f"{int(result.get('failed', 0) or 0)} com falha, "
                    f"{int(result.get('selected', 0) or 0)} item(ns) tratado(s).",
                )
            else:
                raise LegacyWriteError("Acao de monitoramento invalida.")
        except LegacyWriteError as exc:
            messages.error(request, str(exc))
        return _receipt_queue_return_response(
            request,
            campaign=selected_campaign,
            status=selected_status,
            auto_refresh=str(request.POST.get("auto", "") or "") in {"1", "on", "true", "sim"},
        )
    campaigns = _receipt_queue_campaigns()
    if not selected_campaign and campaigns:
        selected_campaign = campaigns[0]
    snapshot = _receipt_queue_snapshot(
        campaign_key=selected_campaign,
        status=selected_status,
        limit=160,
    )
    snapshot["campaigns"] = campaigns
    context = {
        "title": "Monitor de fila de recibos",
        "monitor": snapshot,
        "auto_refresh": auto_refresh,
        "email_runtime": email_runtime_snapshot(),
        "can_manage_receipts": user_has_module_permission(request.user, "manage_contributions"),
    }
    return render(request, "power_church_django/receipts/queue_monitor.html", context)


@module_permission_required("manage_contributions")
def receipt_new(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        person_id = request.POST.get("pessoa_id", "")
        try:
            receipt_id = create_receipt(request.POST, actor=_actor(request), replace_existing=True)
            messages.success(request, f"Recibo #{receipt_id} gerado com auditoria.")
            return redirect(f"/receipts/{receipt_id}/")
        except LegacyWriteError as exc:
            messages.error(request, str(exc))
            query = _receipt_return_query(request.POST, fallback_selected_person_id=int(person_id or 0))
            if not query:
                query = _receipt_hub_query(selected_person_id=int(person_id or 0))
            return _receipt_hub_redirect(query)
    query = _receipt_hub_query(
        selected_person_id=int(request.GET.get("person_id") or 0),
        person_lookup=request.GET.get("person_lookup", ""),
        form_date_start=request.GET.get("form_date_start", "") or request.GET.get("date_start", ""),
        form_date_end=request.GET.get("form_date_end", "") or request.GET.get("date_end", ""),
    )
    return _receipt_hub_redirect(query)


@module_permission_required("view_contributions", "manage_contributions")
def receipt_detail(request: HttpRequest, receipt_id: int) -> HttpResponse:
    if request.method == "POST":
        try:
            fields = _receipt_message_fields(request.POST)
            _maybe_save_receipt_template(request.POST, actor=_actor(request))
            person_id = int((get_receipt_detail_cached(receipt_id) or {}).get("receipt", {}).get("person_id") or 0)
            email_updated = _maybe_update_person_email_for_manual_receipt(
                person_id=person_id,
                fields=fields,
                actor=_actor(request),
            )
            queue_receipt_dispatches(
                [receipt_id],
                email_to=fields["email_to"],
                subject=fields["subject"],
                body=fields["body"],
                actor=_actor(request),
                send_now=True,
            )
            if email_updated:
                messages.info(request, "E-mail da ficha atualizado durante o reenvio manual do recibo.")
            messages.success(request, "Recibo processado para envio manual.")
        except LegacyWriteError as exc:
            messages.error(request, str(exc))
        return redirect(f"/receipts/{receipt_id}/")
    context = {"title": "Recibo"}
    try:
        context["detail"] = get_receipt_detail_cached(receipt_id)
        if context["detail"]:
            person_id = int((context["detail"].get("receipt") or {}).get("person_id") or 0)
            context["dispatch_history"] = [
                item
                for item in receipt_dispatch_history(person_id, limit=20)
                if str(item.get("receipt_number") or "") == str((context["detail"].get("receipt") or {}).get("numero") or "")
            ]
            context["receipt_form"] = enrich_receipt_form(
                {
                    "person": context["detail"].get("person") or {},
                    "items": context["detail"].get("items") or [],
                    "total_fmt": (context["detail"].get("receipt") or {}).get("valor_fmt") or "",
                    "filters": {},
                }
            )
    except LegacyDatabaseError as exc:
        context["error"] = str(exc)
    return render(request, "power_church_django/receipts/detail.html", context)


@module_permission_required("view_contributions")
def receipt_pdf_view(request: HttpRequest, receipt_id: int) -> HttpResponse:
    detail = get_receipt_detail_cached(receipt_id)
    if detail is None:
        raise Http404("Recibo nao encontrado.")
    payload = receipt_pdf(detail)
    response = HttpResponse(payload, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{receipt_pdf_filename(detail)}"'
    return response


@module_permission_required("view_contributors")
def contributors(request: HttpRequest) -> HttpResponse:
    selected_tags = [value for value in request.GET.getlist("tag") if value.strip()]
    context = {
        "title": "Contribuintes auxiliares",
        "q": request.GET.get("q", ""),
        "status": request.GET.get("status", ""),
        "tipo": request.GET.get("tipo", ""),
        "mode": request.GET.get("mode", "todos") or "todos",
        "section": request.GET.get("section", ""),
        "selected_tags": selected_tags,
    }
    try:
        context["contributors"] = list_contributors_postgres(
            q=context["q"],
            status=context["status"],
            tipo=context["tipo"],
            mode=context["mode"],
            tags=selected_tags,
            section=context["section"],
        )
    except LegacyDatabaseError as exc:
        context["error"] = str(exc)
    return render(request, "power_church_django/contributors/list.html", context)


@module_permission_required("view_contributors", "manage_contributors")
def contributor_detail(request: HttpRequest, contributor_id: int) -> HttpResponse:
    if request.method == "POST":
        action = request.POST.get("action", "")
        try:
            if action == "link_person":
                person_id = int(request.POST.get("person_id") or 0)
                link_contributor_to_person_by_id_postgres(contributor_id, person_id, actor=_actor(request))
                messages.success(request, "Contribuinte vinculado a pessoa com auditoria.")
            elif action == "repoint_person":
                person_id = int(request.POST.get("person_id") or 0)
                repoint_contributor_to_person_by_id_postgres(contributor_id, person_id, actor=_actor(request))
                messages.success(request, "Identidade financeira reapontada com auditoria.")
            elif action == "unlink_person":
                unlink_contributor_from_person_by_id_postgres(contributor_id, actor=_actor(request))
                messages.success(request, "Identidade financeira desvinculada e voltou para revisao controlada.")
            elif action == "create_frequentador":
                family_person_id = int(request.POST.get("family_person_id") or 0)
                person_id = create_frequentador_from_contributor_postgres(contributor_id, family_person_id=family_person_id, actor=_actor(request))
                messages.success(request, f"Frequentador #{person_id} criado e vinculado com auditoria.")
                return redirect(f"/people/{person_id}/")
            else:
                messages.error(request, "Acao de contribuinte nao reconhecida.")
        except (LegacyWriteError, ValueError) as exc:
            messages.error(request, str(exc))
        return redirect(f"/contributors/{contributor_id}/")

    context = {"title": "Ficha do contribuinte auxiliar"}
    try:
        context["detail"] = get_contributor_detail_postgres(contributor_id)
    except LegacyDatabaseError as exc:
        context["error"] = str(exc)
    return render(request, "power_church_django/contributors/detail.html", context)

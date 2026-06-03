from __future__ import annotations

from urllib.parse import urlencode

from django.contrib import messages
from django.db import OperationalError, ProgrammingError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render

from power_church_django.services.django_audit import list_django_audit_events, list_system_email_events, resend_system_email_event
from power_church_django.services.legacy import LegacyDatabaseError, operational_audit, search_receipt_people, technical_audit


def _int_param(request: HttpRequest, name: str, default: int) -> int:
    try:
        return int(request.GET.get(name, default))
    except (TypeError, ValueError):
        return default


def _int_payload_param(request: HttpRequest, name: str, default: int) -> int:
    try:
        return int(request.POST.get(name, default))
    except (TypeError, ValueError):
        return default


def _query(**kwargs: object) -> str:
    clean = {key: value for key, value in kwargs.items() if value not in (None, "")}
    return urlencode(clean)


def _actor(request: HttpRequest) -> str:
    user = getattr(request, "user", None)
    if getattr(user, "is_authenticated", False):
        return str(user.username)
    return "django"


def _search_person_by_id(person_id: int) -> dict[str, object] | None:
    if person_id <= 0:
        return None
    for item in search_receipt_people(str(person_id), limit=10):
        try:
            if int(item.get("id") or 0) == person_id:
                return item
        except (TypeError, ValueError):
            continue
    return None


def index(request: HttpRequest) -> HttpResponse:
    mode = request.GET.get("modo", "cadastro")
    context = {
        "title": "Auditoria",
        "mode": mode,
        "tipo": request.GET.get("tipo", ""),
        "severidade": request.GET.get("severidade", ""),
        "acao": request.GET.get("acao", ""),
        "tabela": request.GET.get("tabela", ""),
        "email_kind": request.GET.get("email_kind", ""),
        "email_status": request.GET.get("email_status", ""),
        "q": request.GET.get("q", ""),
        "merge_lookup": request.GET.get("merge_lookup", ""),
        "merge_primary_lookup": request.GET.get("merge_primary_lookup", ""),
        "merge_duplicate_lookup": request.GET.get("merge_duplicate_lookup", ""),
        "merge_primary_id": _int_param(request, "merge_primary_id", 0),
        "merge_duplicate_id": _int_param(request, "merge_duplicate_id", 0),
        "selected_person_id": _int_param(request, "selected_person_id", 0),
        "person_lookup": request.GET.get("person_lookup", ""),
    }
    try:
        if mode == "emails":
            email_audit = list_system_email_events(
                kind=context["email_kind"],
                status=context["email_status"],
                q=context["q"],
                person_id=context["selected_person_id"],
                page=_int_param(request, "page", 1),
                page_size=_int_param(request, "page_size", 120),
            )
            if context["person_lookup"]:
                context["email_people"] = search_receipt_people(context["person_lookup"])
            email_audit["previous_query"] = _query(
                modo="emails",
                email_kind=context["email_kind"],
                email_status=context["email_status"],
                q=context["q"],
                selected_person_id=context["selected_person_id"],
                person_lookup=context["person_lookup"],
                page=email_audit["previous_page"],
                page_size=email_audit["page_size"],
            )
            email_audit["next_query"] = _query(
                modo="emails",
                email_kind=context["email_kind"],
                email_status=context["email_status"],
                q=context["q"],
                selected_person_id=context["selected_person_id"],
                person_lookup=context["person_lookup"],
                page=email_audit["next_page"],
                page_size=email_audit["page_size"],
            )
            context["email_audit"] = email_audit
        elif mode == "django":
            django_audit = list_django_audit_events(
                action=context["acao"],
                table_name=context["tabela"],
                page=_int_param(request, "page", 1),
                page_size=_int_param(request, "page_size", 120),
            )
            django_audit["previous_query"] = _query(
                modo="django",
                acao=context["acao"],
                tabela=context["tabela"],
                page=django_audit["previous_page"],
                page_size=django_audit["page_size"],
            )
            django_audit["next_query"] = _query(
                modo="django",
                acao=context["acao"],
                tabela=context["tabela"],
                page=django_audit["next_page"],
                page_size=django_audit["page_size"],
            )
            context["django_audit"] = django_audit
        elif mode == "tecnica":
            technical = technical_audit(
                action=context["acao"],
                table=context["tabela"],
                page=_int_param(request, "page", 1),
                page_size=_int_param(request, "page_size", 120),
            )
            technical["previous_query"] = _query(
                modo="tecnica",
                acao=context["acao"],
                tabela=context["tabela"],
                page=technical["previous_page"],
                page_size=technical["page_size"],
            )
            technical["next_query"] = _query(
                modo="tecnica",
                acao=context["acao"],
                tabela=context["tabela"],
                page=technical["next_page"],
                page_size=technical["page_size"],
            )
            context["technical"] = technical
        else:
            if context["merge_lookup"]:
                context["merge_people"] = search_receipt_people(context["merge_lookup"], limit=20)
            if context["merge_primary_lookup"]:
                context["merge_primary_people"] = search_receipt_people(context["merge_primary_lookup"], limit=20)
            if context["merge_duplicate_lookup"]:
                context["merge_duplicate_people"] = search_receipt_people(context["merge_duplicate_lookup"], limit=20)
            context["merge_primary_person"] = _search_person_by_id(context["merge_primary_id"])
            context["merge_duplicate_person"] = _search_person_by_id(context["merge_duplicate_id"])
            audit = operational_audit(
                tipo=context["tipo"],
                severidade=context["severidade"],
                page=_int_param(request, "page", 1),
                page_size=_int_param(request, "page_size", 200),
            )
            audit["previous_query"] = _query(
                tipo=context["tipo"],
                severidade=context["severidade"],
                page=audit["previous_page"],
                page_size=audit["page_size"],
            )
            audit["next_query"] = _query(
                tipo=context["tipo"],
                severidade=context["severidade"],
                page=audit["next_page"],
                page_size=audit["page_size"],
            )
            context["audit"] = audit
    except (LegacyDatabaseError, LookupError, OperationalError, ProgrammingError) as exc:
        context["error"] = str(exc)
    return render(request, "power_church_django/audit/index.html", context)


def email_resend(request: HttpRequest) -> HttpResponse:
    if request.method != "POST":
        return redirect("/audit/?modo=emails")
    query = _query(
        modo="emails",
        email_kind=request.POST.get("return_email_kind", ""),
        email_status=request.POST.get("return_email_status", ""),
        q=request.POST.get("return_q", ""),
        selected_person_id=_int_payload_param(request, "return_selected_person_id", 0),
        person_lookup=request.POST.get("return_person_lookup", ""),
        page=_int_payload_param(request, "return_page", 1),
        page_size=_int_payload_param(request, "return_page_size", 120),
    )
    try:
        result = resend_system_email_event(
            kind=request.POST.get("delivery_kind", ""),
            row_id=_int_payload_param(request, "delivery_id", 0),
            actor=_actor(request),
        )
        messages.success(
            request,
            f"{str(result.get('kind') or '').title()} reenviado para {result.get('destination') or '-'} com sucesso.",
        )
    except (LegacyDatabaseError, LookupError, OperationalError, ProgrammingError) as exc:
        messages.error(request, str(exc))
    except Exception as exc:
        messages.error(request, f"Nao foi possivel reenviar o e-mail: {exc}")
    return redirect(f"/audit/?{query}" if query else "/audit/?modo=emails")

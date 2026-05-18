from __future__ import annotations

from urllib.parse import urlencode

from django.db import OperationalError, ProgrammingError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from power_church_django.services.django_audit import list_django_audit_events
from power_church_django.services.legacy import LegacyDatabaseError, operational_audit, technical_audit


def _int_param(request: HttpRequest, name: str, default: int) -> int:
    try:
        return int(request.GET.get(name, default))
    except (TypeError, ValueError):
        return default


def _query(**kwargs: object) -> str:
    clean = {key: value for key, value in kwargs.items() if value not in (None, "")}
    return urlencode(clean)


def index(request: HttpRequest) -> HttpResponse:
    mode = request.GET.get("modo", "cadastro")
    context = {
        "title": "Auditoria",
        "mode": mode,
        "tipo": request.GET.get("tipo", ""),
        "severidade": request.GET.get("severidade", ""),
        "acao": request.GET.get("acao", ""),
        "tabela": request.GET.get("tabela", ""),
    }
    try:
        if mode == "django":
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

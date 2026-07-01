from __future__ import annotations

from urllib.parse import urlencode

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from power_church_django.services.access_control import module_permission_required
from power_church_django.services.pdf_reports import (
    contribution_destination_pdf,
    contribution_destination_pdf_filename,
    contribution_period_pdf,
    contribution_period_pdf_filename,
)
from power_church_django.services.reports_native import (
    contribution_destination_report_postgres,
    contribution_report_postgres,
)


@module_permission_required("view_reports")
def index(request: HttpRequest) -> HttpResponse:
    context = {
        "title": "Relatorios",
        "competencia": request.GET.get("competencia", ""),
        "q": request.GET.get("q", ""),
        "date_start": request.GET.get("date_start", ""),
        "date_end": request.GET.get("date_end", ""),
    }
    params = {
        "competencia": context["competencia"],
        "q": context["q"],
        "date_start": context["date_start"],
        "date_end": context["date_end"],
    }
    context["report_query_string"] = urlencode({key: value for key, value in params.items() if value})
    context["report"] = contribution_report_postgres(
        competencia=context["competencia"],
        q=context["q"],
        date_start=context["date_start"],
        date_end=context["date_end"],
    )
    return render(request, "power_church_django/reports/index.html", context, content_type="text/html; charset=utf-8")


@module_permission_required("view_reports")
def contribution_period_pdf_view(request: HttpRequest) -> HttpResponse:
    report = contribution_report_postgres(
        competencia=request.GET.get("competencia", ""),
        q=request.GET.get("q", ""),
        date_start=request.GET.get("date_start", ""),
        date_end=request.GET.get("date_end", ""),
    )
    payload = contribution_period_pdf(report)
    filename = contribution_period_pdf_filename(report)
    disposition = "inline" if request.GET.get("inline") == "1" else "attachment"
    response = HttpResponse(payload, content_type="application/pdf")
    response["Content-Disposition"] = f'{disposition}; filename="{filename}"'
    return response


@module_permission_required("view_reports")
def destinations(request: HttpRequest) -> HttpResponse:
    context = {
        "title": "Relatorios por destino",
        "competencia": request.GET.get("competencia", ""),
        "q": request.GET.get("q", ""),
        "date_start": request.GET.get("date_start", ""),
        "date_end": request.GET.get("date_end", ""),
        "destination": request.GET.get("destination", ""),
    }
    params = {
        "competencia": context["competencia"],
        "q": context["q"],
        "date_start": context["date_start"],
        "date_end": context["date_end"],
        "destination": context["destination"],
    }
    context["report_query_string"] = urlencode({key: value for key, value in params.items() if value})
    context["report"] = contribution_destination_report_postgres(
        competencia=context["competencia"],
        q=context["q"],
        date_start=context["date_start"],
        date_end=context["date_end"],
        destination=context["destination"],
    )
    return render(request, "power_church_django/reports/destinations.html", context, content_type="text/html; charset=utf-8")


@module_permission_required("view_reports")
def contribution_destinations_pdf_view(request: HttpRequest) -> HttpResponse:
    report = contribution_destination_report_postgres(
        competencia=request.GET.get("competencia", ""),
        q=request.GET.get("q", ""),
        date_start=request.GET.get("date_start", ""),
        date_end=request.GET.get("date_end", ""),
        destination=request.GET.get("destination", ""),
    )
    payload = contribution_destination_pdf(report)
    filename = contribution_destination_pdf_filename(report)
    disposition = "inline" if request.GET.get("inline") == "1" else "attachment"
    response = HttpResponse(payload, content_type="application/pdf")
    response["Content-Disposition"] = f'{disposition}; filename="{filename}"'
    return response

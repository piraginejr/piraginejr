from __future__ import annotations

from collections import defaultdict
from typing import Any

from django.db.models import Q

from power_church_core.formatting import br_date
from power_church_core.normalization import contribution_report_identity, normalize_display_payload, normalize_query
from power_church_django.apps.contributions.models import NativeContribution
from power_church_django.apps.people.models import PersonSnapshot
from power_church_django.services.runtime_formatting import _money, format_status, status_sigla


def _person_status_index() -> dict[int, dict[str, str]]:
    return {
        int(row.legacy_id or 0): {
            "name": normalize_query(row.name),
            "status": normalize_query(row.status),
            "code": normalize_query(row.internal_code),
            "cpf": normalize_query(row.cpf),
        }
        for row in PersonSnapshot.objects.filter(is_active=True).only("legacy_id", "name", "status", "internal_code", "cpf")
    }


def _competencias() -> list[str]:
    rows = (
        NativeContribution.objects.filter(is_active=True)
        .exclude(competence="")
        .values("competence", "competence_order")
        .distinct()
        .order_by("-competence_order", "-competence")
    )
    return [str(row.get("competence") or "") for row in rows if str(row.get("competence") or "").strip()]


def contribution_report_postgres(
    competencia: str = "",
    q: str = "",
    date_start: str = "",
    date_end: str = "",
    limit_rows: int = 5000,
) -> dict[str, Any]:
    competencia = normalize_query(competencia)
    q = normalize_query(q)
    date_start = normalize_query(date_start)
    date_end = normalize_query(date_end)
    queryset = NativeContribution.objects.filter(is_active=True).order_by(
        "contributor_name", "received_at_raw", "legacy_id"
    )
    if competencia:
        queryset = queryset.filter(competence=competencia)
    if date_start:
        queryset = queryset.filter(received_at_raw__gte=date_start)
    if date_end:
        queryset = queryset.filter(received_at_raw__lte=date_end)
    if q:
        digits = "".join(ch for ch in q if ch.isdigit())
        queryset = queryset.filter(
            Q(contributor_name__icontains=q)
            | Q(contributor_document__icontains=digits or q)
            | Q(contributor_name__icontains=q)
        )
    rows = list(queryset[:limit_rows])
    person_index = _person_status_index()
    grouped: dict[tuple[str, str, str, int], dict[str, Any]] = {}
    for row in rows:
        person = person_index.get(int(row.person_legacy_id or 0), {})
        has_person = bool(int(row.person_legacy_id or 0))
        sigla = status_sigla(person.get("status"), has_person)
        identity = contribution_report_identity(
            person.get("name"),
            row.contributor_name,
            row.contributor_document,
        )
        display_name = identity["name"] or "Documento nao identificado"
        group_id = int(row.person_legacy_id or row.contributor_legacy_id or row.native_aux_contributor_id or 0)
        key = (identity["group_kind"], display_name, sigla, group_id)
        item = grouped.setdefault(
            key,
            {
                "nome": display_name,
                "sort_key": identity["sort_key"],
                "group_kind": identity["group_kind"],
                "group_label": identity["group_label"],
                "documento": identity["document"],
                "nome_original": identity["raw_name"],
                "sigla": sigla,
                "remessas": [],
                "total": 0.0,
                "total_fmt": _money(0),
            },
        )
        value = float(row.amount or 0)
        item["total"] += value
        item["total_fmt"] = _money(item["total"])
        item["remessas"].append(
            {
                "data": br_date(row.received_at_raw),
                "competencia": row.competence or "",
                "valor_fmt": _money(value),
            }
        )
    items = list(grouped.values())
    items.sort(key=lambda item: (0 if item["group_kind"] == "nome" else 1, str(item["sort_key"]), str(item["documento"])))
    total_value = sum(item["total"] for item in items)
    sigla_counts = defaultdict(int)
    for item in items:
        sigla_counts[item["sigla"]] += 1
    named_items = [item for item in items if item["group_kind"] == "nome"]
    document_items = [item for item in items if item["group_kind"] == "documento"]
    return normalize_display_payload({
        "items": items,
        "named_items": named_items,
        "document_items": document_items,
        "competencia": competencia,
        "q": q,
        "date_start": date_start,
        "date_end": date_end,
        "competencias": _competencias(),
        "summary": {
            "total_fmt": _money(total_value),
            "contribuintes": len(items),
            "remessas": len(rows),
            "sa": sigla_counts["SA"],
            "si": sigla_counts["SI"],
            "nf": sigla_counts["NF"],
            "nv": sigla_counts["NV"],
            "nm": sigla_counts["NM"],
            "nr": sigla_counts["NR"],
            "somente_documento": len(document_items),
        },
        "truncated": len(rows) >= limit_rows,
    })


def _destination_from_native_row(row: NativeContribution) -> dict[str, Any]:
    if int(row.campaign_legacy_id or 0):
        label = normalize_query(row.campaign_name) or "Campanha sem nome"
        return {
            "key": f"campanha:{int(row.campaign_legacy_id or 0)}",
            "kind": "campanha",
            "kind_label": "Campanha",
            "label": label,
            "detail": f"Campanha: {label}",
            "tipo": normalize_query(row.contribution_type_name),
            "campanha": label,
        }
    label = normalize_query(row.contribution_type_name) or "Sem destinacao"
    return {
        "key": f"tipo:{int(row.contribution_type_legacy_id or 0)}",
        "kind": "tipo",
        "kind_label": "Tipo",
        "label": label,
        "detail": f"Tipo: {label}",
        "tipo": label,
        "campanha": "",
    }


def contribution_destination_report_postgres(
    competencia: str = "",
    q: str = "",
    date_start: str = "",
    date_end: str = "",
    destination: str = "",
    limit_rows: int = 10000,
) -> dict[str, Any]:
    competencia = normalize_query(competencia)
    q = normalize_query(q)
    date_start = normalize_query(date_start)
    date_end = normalize_query(date_end)
    destination = normalize_query(destination)
    queryset = NativeContribution.objects.filter(is_active=True).order_by(
        "campaign_name", "contribution_type_name", "contributor_name", "received_at_raw", "legacy_id"
    )
    if competencia:
        queryset = queryset.filter(competence=competencia)
    if date_start:
        queryset = queryset.filter(received_at_raw__gte=date_start)
    if date_end:
        queryset = queryset.filter(received_at_raw__lte=date_end)
    if destination.startswith("campanha:"):
        destination_id = int(destination.split(":", 1)[1] or 0)
        queryset = queryset.filter(campaign_legacy_id=destination_id)
    elif destination.startswith("tipo:"):
        destination_id = int(destination.split(":", 1)[1] or 0)
        queryset = queryset.filter(contribution_type_legacy_id=destination_id, campaign_legacy_id__isnull=True)
    if q:
        digits = "".join(ch for ch in q if ch.isdigit())
        queryset = queryset.filter(
            Q(contributor_name__icontains=q)
            | Q(contributor_document__icontains=digits or q)
            | Q(contribution_type_name__icontains=q)
            | Q(campaign_name__icontains=q)
        )
    option_rows = list(queryset)
    rows = option_rows[:limit_rows]
    person_index = _person_status_index()
    grouped_options: dict[str, dict[str, Any]] = {}
    for row in option_rows:
        destination_data = _destination_from_native_row(row)
        bucket = grouped_options.setdefault(
            destination_data["key"],
            {**destination_data, "remessas": 0, "contribuintes": set(), "total": 0.0, "total_fmt": _money(0)},
        )
        bucket["remessas"] += 1
        bucket["total"] += float(row.amount or 0)
        bucket["total_fmt"] = _money(bucket["total"])
        bucket["contribuintes"].add(
                (
                    int(row.person_legacy_id or 0),
                    int(row.contributor_legacy_id or 0),
                    int(row.native_aux_contributor_id or 0),
                    normalize_query(row.contributor_name),
                    normalize_query(row.contributor_document),
                )
            )
    destination_options = []
    for item in grouped_options.values():
        item["contribuintes"] = len(item["contribuintes"])
        item["selected"] = item["key"] == destination
        destination_options.append(item)
    destination_options.sort(key=lambda item: (0 if item["key"] == "tipo:1" else 1, str(item["label"]).casefold()))
    selected_destination_label = next((item["detail"] for item in destination_options if item.get("selected")), "")

    destination_groups: dict[str, dict[str, Any]] = {}
    overall_contributors: set[tuple[object, ...]] = set()
    sigla_counts = defaultdict(int)
    for row in rows:
        destination_data = _destination_from_native_row(row)
        group = destination_groups.setdefault(
            destination_data["key"],
            {
                **destination_data,
                "items_by_key": {},
                "items": [],
                "named_items": [],
                "document_items": [],
                "remessas": 0,
                "contribuintes": 0,
                "total": 0.0,
                "total_fmt": _money(0),
            },
        )
        person = person_index.get(int(row.person_legacy_id or 0), {})
        has_person = bool(int(row.person_legacy_id or 0))
        sigla = status_sigla(person.get("status"), has_person)
        identity = contribution_report_identity(person.get("name"), row.contributor_name, row.contributor_document)
        display_name = identity["name"] or "Documento nao identificado"
        contributor_key = (
            identity["group_kind"],
            display_name,
            sigla,
            int(row.person_legacy_id or 0),
            int(row.contributor_legacy_id or 0),
            identity["document"],
        )
        overall_contributors.add(contributor_key)
        item = group["items_by_key"].setdefault(
            contributor_key,
            {
                "nome": display_name,
                "sort_key": identity["sort_key"],
                "group_kind": identity["group_kind"],
                "documento": identity["document"],
                "nome_original": identity["raw_name"],
                "sigla": sigla,
                "remessas": [],
                "total": 0.0,
                "total_fmt": _money(0),
            },
        )
        value = float(row.amount or 0)
        item["total"] += value
        item["total_fmt"] = _money(item["total"])
        item["remessas"].append(
            {
                "id": int(row.legacy_id or 0),
                "detail_url": f"/contributions/{int(row.legacy_id or 0)}/",
                "data": br_date(row.received_at_raw),
                "competencia": row.competence or "",
                "forma": normalize_query(row.receipt_method_name),
                "valor_fmt": _money(value),
            }
        )
        group["remessas"] += 1
        group["total"] += value
        group["total_fmt"] = _money(group["total"])
        sigla_counts[sigla] += 1
    destinations = []
    for group in destination_groups.values():
        items = list(group["items_by_key"].values())
        items.sort(key=lambda item: (0 if item["group_kind"] == "nome" else 1, str(item["sort_key"]), str(item["documento"])))
        group["items"] = items
        group["named_items"] = [item for item in items if item["group_kind"] == "nome"]
        group["document_items"] = [item for item in items if item["group_kind"] == "documento"]
        group["contribuintes"] = len(items)
        destinations.append(group)
    destinations.sort(key=lambda item: (0 if item["key"] == "tipo:1" else 1, str(item["label"]).casefold()))
    total_value = sum(float(item["total"] or 0) for item in destinations)
    return normalize_display_payload({
        "competencia": competencia,
        "q": q,
        "date_start": date_start,
        "date_end": date_end,
        "selected_destination": destination,
        "selected_destination_label": selected_destination_label,
        "competencias": _competencias(),
        "destination_options": destination_options,
        "destinations": destinations,
        "summary": {
            "total_fmt": _money(total_value),
            "destinos": len(destinations),
            "contribuintes": len(overall_contributors),
            "remessas": len(rows),
            "sa": sigla_counts["SA"],
            "si": sigla_counts["SI"],
            "nf": sigla_counts["NF"],
            "nv": sigla_counts["NV"],
            "nm": sigla_counts["NM"],
            "nr": sigla_counts["NR"],
        },
        "truncated": len(rows) >= limit_rows,
    })

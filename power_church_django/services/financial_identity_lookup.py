from __future__ import annotations

from collections import defaultdict

from power_church_core.matching import derived_pix_name_aliases
from power_church_core.normalization import normalize_match_name, normalize_query
from power_church_django.apps.contributions.models import NativeAuxContributor
from power_church_django.apps.people.models import (
    FinancialIdentityLookup,
    PersonContributionSnapshot,
    PersonContributorSnapshot,
    PersonIdentifierSnapshot,
    PersonSnapshot,
)


ALIAS_LOOKUP_KINDS = {
    "nome_ficha",
    "nome_derivado",
    "nome_contribuinte",
    "nome_financeiro",
    "nome_auxiliar",
}


def _lookup_rows_for_people(person_ids: list[int] | None = None) -> list[FinancialIdentityLookup]:
    person_filter = {}
    if person_ids is not None:
        clean_ids = [int(value or 0) for value in person_ids if int(value or 0)]
        if not clean_ids:
            return []
        person_filter["legacy_id__in"] = clean_ids

    people_rows = list(
        PersonSnapshot.objects.filter(is_active=True, **person_filter)
        .values("id", "legacy_id", "organization_id", "name", "cpf")
        .order_by("legacy_id")
    )
    if not people_rows:
        return []

    people_db_ids = [int(row["id"] or 0) for row in people_rows]
    people_legacy_ids = [int(row["legacy_id"] or 0) for row in people_rows]
    legacy_to_db_id = {int(row["legacy_id"] or 0): int(row["id"] or 0) for row in people_rows}
    legacy_to_org_id = {int(row["legacy_id"] or 0): int(row["organization_id"] or 0) for row in people_rows}
    rows_by_key: dict[tuple[int, str, str, str], FinancialIdentityLookup] = {}

    def add_row(person_legacy_id: int, lookup_kind: str, value: object, *, source: str = "", priority: int = 0, notes: str = "") -> None:
        person_legacy_id = int(person_legacy_id or 0)
        raw_value = normalize_query(value)
        if not person_legacy_id or not raw_value:
            return
        normalized_value = normalize_match_name(raw_value) if lookup_kind in ALIAS_LOOKUP_KINDS else normalize_query(raw_value)
        if not normalized_value:
            return
        key = (person_legacy_id, lookup_kind, normalized_value, source)
        existing = rows_by_key.get(key)
        if existing is not None:
            if priority > existing.priority:
                existing.priority = priority
                existing.value = raw_value
                existing.notes = notes
            return
        rows_by_key[key] = FinancialIdentityLookup(
            organization_id=legacy_to_org_id[person_legacy_id],
            person_id=legacy_to_db_id[person_legacy_id],
            lookup_kind=lookup_kind,
            value=raw_value,
            normalized_value=normalized_value,
            source=source,
            priority=priority,
            notes=notes,
            is_active=True,
        )

    for row in people_rows:
        person_legacy_id = int(row["legacy_id"] or 0)
        name = row.get("name") or ""
        add_row(person_legacy_id, "nome_ficha", name, source="person_snapshot", priority=100, notes=name)
        add_row(person_legacy_id, "cpf", row.get("cpf"), source="person_snapshot", priority=200, notes="cpf_ficha")
        for alias in derived_pix_name_aliases(name):
            add_row(person_legacy_id, "nome_derivado", alias, source="person_snapshot", priority=80, notes=name)

    for row in PersonIdentifierSnapshot.objects.filter(person__legacy_id__in=people_legacy_ids, is_active=True).values(
        "person__legacy_id",
        "identifier_type",
        "value",
        "notes",
    ):
        add_row(
            int(row["person__legacy_id"] or 0),
            normalize_query(row.get("identifier_type")) or "documento",
            row.get("value"),
            source="person_identifier",
            priority=190,
            notes=normalize_query(row.get("notes")),
        )

    for row in PersonContributorSnapshot.objects.filter(person__legacy_id__in=people_legacy_ids, is_active=True).values(
        "person__legacy_id",
        "name",
        "primary_document",
        "document_type",
    ):
        person_legacy_id = int(row["person__legacy_id"] or 0)
        contributor_name = row.get("name") or ""
        add_row(person_legacy_id, "nome_contribuinte", contributor_name, source="person_contributor", priority=90, notes=contributor_name)
        add_row(
            person_legacy_id,
            normalize_query(row.get("document_type")) or "documento",
            row.get("primary_document"),
            source="person_contributor",
            priority=195,
            notes=contributor_name,
        )

    for row in (
        PersonContributionSnapshot.objects.filter(person__legacy_id__in=people_legacy_ids, is_active=True)
        .exclude(source_name="")
        .values("person__legacy_id", "source_name")
    ):
        add_row(
            int(row["person__legacy_id"] or 0),
            "nome_financeiro",
            row.get("source_name"),
            source="person_contribution",
            priority=70,
            notes=normalize_query(row.get("source_name")),
        )

    for row in NativeAuxContributor.objects.filter(person_legacy_id__in=people_legacy_ids, is_active=True).values(
        "person_legacy_id",
        "name",
        "primary_document",
        "document_type",
    ):
        person_legacy_id = int(row["person_legacy_id"] or 0)
        aux_name = row.get("name") or ""
        add_row(person_legacy_id, "nome_auxiliar", aux_name, source="native_aux", priority=85, notes=aux_name)
        add_row(
            person_legacy_id,
            normalize_query(row.get("document_type")) or "documento",
            row.get("primary_document"),
            source="native_aux",
            priority=185,
            notes=aux_name,
        )

    return list(rows_by_key.values())


def rebuild_financial_identity_lookup(*, person_ids: list[int] | None = None) -> int:
    if person_ids is None:
        FinancialIdentityLookup.objects.all().delete()
    else:
        clean_ids = [int(value or 0) for value in person_ids if int(value or 0)]
        if not clean_ids:
            return 0
        FinancialIdentityLookup.objects.filter(person__legacy_id__in=clean_ids).delete()
    rows = _lookup_rows_for_people(person_ids)
    if rows:
        FinancialIdentityLookup.objects.bulk_create(rows, batch_size=1000)
    return len(rows)


def sync_financial_identity_lookup_for_people(person_ids: list[int] | None = None) -> int:
    return rebuild_financial_identity_lookup(person_ids=person_ids)


def financial_identity_people_cache() -> dict[str, object]:
    if not FinancialIdentityLookup.objects.filter(is_active=True).exists():
        return {"people_cache": []}
    people_rows = list(
        PersonSnapshot.objects.filter(is_active=True)
        .values("legacy_id", "name", "normalized_name", "status", "cpf")
        .order_by("legacy_id")
    )
    aliases_by_person: dict[int, dict[tuple[str, str], dict[str, str]]] = defaultdict(dict)
    identifiers_by_person: dict[int, list[dict[str, str]]] = defaultdict(list)

    lookup_rows = FinancialIdentityLookup.objects.filter(is_active=True).values(
        "person__legacy_id",
        "lookup_kind",
        "value",
        "normalized_value",
        "source",
    )

    for row in lookup_rows:
        person_id = int(row["person__legacy_id"] or 0)
        kind = normalize_query(row.get("lookup_kind"))
        if kind in ALIAS_LOOKUP_KINDS:
            aliases_by_person[person_id][(row["normalized_value"], kind)] = {
                "name": row["value"] or "",
                "name_norm": row["normalized_value"] or "",
                "alias_kind": kind,
                "source_name": normalize_query(row.get("source")) or (row["value"] or ""),
            }
            continue
        identifiers_by_person[person_id].append(
            {
                "kind": kind or "documento",
                "value": row["value"] or "",
                "source_name": normalize_query(row.get("source")) or (row["value"] or ""),
            }
        )

    people_cache = [
        {
            "id": int(row["legacy_id"] or 0),
            "nome": row["name"] or "",
            "name_norm": row["normalized_name"] or "",
            "status": row["status"] or "",
            "financial_aliases": list(aliases_by_person.get(int(row["legacy_id"] or 0), {}).values()),
            "identifiers": identifiers_by_person.get(int(row["legacy_id"] or 0), []),
        }
        for row in people_rows
    ]
    return {"people_cache": people_cache}

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.parse import quote

from django.conf import settings
from django.core.exceptions import AppRegistryNotReady
from django.db.utils import OperationalError as DjangoOperationalError, ProgrammingError as DjangoProgrammingError

from power_church_core.formatting import br_date, br_datetime, br_money, competencia_from_date
from power_church_core.family import (
    address_complement_specificity,
    complement_has_specific_unit,
    family_address_key,
    family_base_address_key,
    family_group_label,
    normalize_address_complement,
)
from power_church_core.normalization import (
    contribution_report_identity,
    document_digits,
    document_query_matches,
    format_cpf,
    format_document,
    moneyless_int,
    normalize_match_name,
    normalize_query,
    valid_cpf,
)
from power_church_django.services.photos import member_photo_url
from power_church_django.services.smart_audit import (
    classify_contributor_link_block,
    classify_family_audit_group,
    classify_import_pendency,
    summarize_smart_audit,
)


PENDING_REVIEW_STATUSES = {
    "pendente",
    "revisar_pessoa",
    "revisar_destinacao",
    "revisar_duplicidade",
    "classificacao_pendente",
}

HUMAN_PENDING_REVIEW_STATUSES = (
    "pendente",
    "revisar_pessoa",
    "revisar_destinacao",
    "classificacao_pendente",
)


def human_pending_review_sql(alias: str = "m") -> str:
    direct_statuses = ",".join(f"'{value}'" for value in HUMAN_PENDING_REVIEW_STATUSES)
    return (
        f"({alias}.review_status IN ({direct_statuses}) "
        f"OR ({alias}.review_status = 'revisar_duplicidade' AND COALESCE({alias}.imported_contribution_id, 0) = 0))"
    )


STATUS_LABELS = {
    "membro_ativo": "Membro ativo",
    "membro_inativo": "Membro inativo",
    "frequentador": "Frequentador",
    "visitante": "Visitante",
    "arquivo_morto": "Arquivo morto",
}


STATUS_SIGLAS = {
    "membro_ativo": "SA",
    "membro_inativo": "SI",
    "frequentador": "NF",
    "visitante": "NV",
    "arquivo_morto": "NM",
}

CONTRIBUTION_STATUS_LABELS = {
    "regular": "Regular",
    "sem_associacao": "Sem associacao",
    "em_saneamento": "Em saneamento",
    "revisar_destinacao": "Revisar destinacao",
}

FAMILY_RELATIONSHIP_LABELS = {
    "nucleo_familiar": "Familia domiciliar",
    "familia_estendida": "Familia estendida",
    "conjuge": "Conjuge",
    "filho": "Filho(a) / dependente",
    "pai_mae": "Pai / mae",
    "irmao": "Irmao(a)",
    "neto": "Neto(a)",
    "genro_nora": "Genro / nora",
    "outro_familiar": "Outro familiar",
}

FAMILY_RELATIONSHIP_OPTIONS = [
    {"value": key, "label": label}
    for key, label in FAMILY_RELATIONSHIP_LABELS.items()
]

MANUAL_FAMILY_SUPPRESSION_MARKER = "IGNORADO MANUALMENTE"


class LegacyDatabaseError(RuntimeError):
    """Raised when the read-only legacy database cannot be reached."""


def legacy_db_path() -> Path:
    return Path(settings.POWER_CHURCH_LEGACY_DB_PATH)


def connect_legacy() -> sqlite3.Connection:
    path = legacy_db_path()
    if not path.exists():
        raise LegacyDatabaseError(f"Banco legado nao encontrado: {path}")
    uri = f"file:{quote(str(path), safe='/')}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.create_function("NORMALIZE_MATCH", 1, normalize_match_name)
    conn.execute("PRAGMA query_only = ON")
    return conn


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return bool(row)


def scalar(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> Any:
    row = conn.execute(sql, params).fetchone()
    return row[0] if row else None


def format_status(status: object) -> str:
    text = str(status or "").strip()
    return STATUS_LABELS.get(text, text.replace("_", " ").title() if text else "Sem status")


def status_sigla(status: object, has_person: bool = True) -> str:
    if not has_person:
        return "NR"
    return STATUS_SIGLAS.get(str(status or "").strip(), "NR")


def _money(value: object) -> str:
    return br_money(value)


def _person_search_clause(alias: str = "") -> str:
    prefix = f"{alias}." if alias else ""
    return f"""
    (
        CAST({prefix}id AS TEXT) = ?
        OR
        NORMALIZE_MATCH(COALESCE({prefix}nome, '')) LIKE ?
        OR NORMALIZE_MATCH(COALESCE({prefix}nome_social, '')) LIKE ?
        OR COALESCE({prefix}codigo_interno, '') LIKE ?
        OR COALESCE({prefix}cpf, '') LIKE ?
        OR NORMALIZE_MATCH(COALESCE({prefix}email_principal, '')) LIKE ?
        OR COALESCE({prefix}telefone_principal, '') LIKE ?
    )
    """


def _person_search_params(query: str) -> list[str]:
    clean = normalize_query(query)
    digits = "".join(ch for ch in clean if ch.isdigit())
    normalized_like = f"%{normalize_match_name(clean)}%"
    raw_like = f"%{clean}%"
    digit_like = f"%{digits or clean}%"
    exact_id = digits or clean
    return [exact_id, normalized_like, normalized_like, raw_like, digit_like, normalized_like, digit_like]


def _people_snapshot_models() -> dict[str, Any] | None:
    try:
        from django.apps import apps

        models = {
            "person": apps.get_model("people", "PersonSnapshot"),
            "contact": apps.get_model("people", "PersonContactSnapshot"),
            "address": apps.get_model("people", "PersonAddressSnapshot"),
            "relationship": apps.get_model("people", "PersonRelationshipSnapshot"),
            "profile": apps.get_model("people", "PersonProfileSnapshot"),
            "history": apps.get_model("people", "PersonHistorySnapshot"),
            "contributor": apps.get_model("people", "PersonContributorSnapshot"),
            "identifier": apps.get_model("people", "PersonIdentifierSnapshot"),
            "contribution": apps.get_model("people", "PersonContributionSnapshot"),
        }
        models["person"].objects.only("id").first()
        return models
    except (LookupError, AppRegistryNotReady, DjangoOperationalError, DjangoProgrammingError):
        return None


def _people_snapshot_available() -> bool:
    models = _people_snapshot_models()
    if not models:
        return False
    try:
        return models["person"].objects.exists()
    except (DjangoOperationalError, DjangoProgrammingError):
        return False


def _person_snapshot_search_q(query: str):
    from django.db.models import Q

    clean = normalize_query(query)
    digits = "".join(ch for ch in clean if ch.isdigit())
    normalized = normalize_match_name(clean)
    query_filter = (
        Q(legacy_id=moneyless_int(digits or clean))
        | Q(normalized_name__icontains=normalized)
        | Q(name__icontains=clean)
        | Q(social_name__icontains=clean)
        | Q(internal_code__icontains=clean)
        | Q(cpf__icontains=digits or clean)
        | Q(normalized_email__icontains=clean.lower())
        | Q(primary_email__icontains=clean)
        | Q(primary_phone__icontains=digits or clean)
        | Q(primary_whatsapp__icontains=digits or clean)
    )
    return query_filter


def _status_options_from_snapshots(person_model) -> list[dict[str, Any]]:
    from django.db.models import Count

    rows = (
        person_model.objects.filter(is_active=True)
        .values("status")
        .annotate(total=Count("id"))
        .order_by("-total", "status")
    )
    return [
        {
            "value": row["status"] or "",
            "label": format_status(row["status"]),
            "count": int(row["total"] or 0),
        }
        for row in rows
    ]


def _list_people_from_snapshots(q: str = "", status: str = "", city: str = "", limit: int | None = None) -> dict[str, Any]:
    from django.db.models import Q

    models = _people_snapshot_models()
    if not models:
        raise LegacyDatabaseError("Espelho cadastral Postgres indisponivel.")
    person_model = models["person"]
    address_model = models["address"]

    queryset = person_model.objects.filter(is_active=True)
    if status:
        queryset = queryset.filter(status=status)
    if city:
        normalized_city = normalize_match_name(city)
        city_ids = list(
            {
                row["person_id"]
                for row in address_model.objects.values("person_id", "city")
                if normalize_match_name(row["city"] or "") == normalized_city
            }
        )
        queryset = queryset.filter(id__in=city_ids)
    if q:
        queryset = queryset.filter(_person_snapshot_search_q(q))
    total = queryset.count()
    queryset = queryset.order_by("normalized_name", "legacy_id")
    limit_value = moneyless_int(limit) if limit is not None else 0
    if limit_value > 0:
        queryset = queryset[:limit_value]
    rows = list(queryset)
    items = [
        {
            "id": row.legacy_id,
            "codigo": row.internal_code or "",
            "nome": row.name or "",
            "cpf": row.cpf or "",
            "status": format_status(row.status),
            "status_raw": row.status or "",
            "sigla": status_sigla(row.status, True),
            "ativo": "Sim" if row.is_active else "Nao",
            "email": row.primary_email or "",
            "telefone": row.primary_phone or "",
        }
        for row in rows
    ]
    return {
        "items": items,
        "total": total,
        "shown": len(items),
        "q": q,
        "status": status,
        "city": city,
        "status_options": _status_options_from_snapshots(person_model),
        "limit": limit_value or total,
    }


def _search_people_for_relationship_from_snapshots(person_id: int, q: str = "", limit: int = 20) -> list[dict[str, Any]]:
    models = _people_snapshot_models()
    if not models:
        raise LegacyDatabaseError("Espelho cadastral Postgres indisponivel.")
    person_model = models["person"]
    relationship_model = models["relationship"]

    current = person_model.objects.filter(legacy_id=person_id, is_active=True).first()
    if current is None:
        return []
    related_ids = set(
        relationship_model.objects.filter(is_active=True, person=current).values_list("related_person__legacy_id", flat=True)
    )
    related_ids.update(
        relationship_model.objects.filter(is_active=True, related_person=current).values_list("person__legacy_id", flat=True)
    )
    queryset = (
        person_model.objects.filter(organization_id=current.organization_id, is_active=True)
        .exclude(legacy_id=current.legacy_id)
        .exclude(legacy_id__in=related_ids)
        .filter(_person_snapshot_search_q(q))
        .order_by("normalized_name", "legacy_id")[:limit]
    )
    return [
        {
            "id": moneyless_int(row.legacy_id),
            "nome": row.name or "",
            "codigo": row.internal_code or "",
            "cpf": format_cpf(row.cpf),
            "status": format_status(row.status),
            "sigla": status_sigla(row.status, True),
            "label": " · ".join(
                part
                for part in [
                    row.name or "",
                    status_sigla(row.status, True),
                    f"CPF {format_cpf(row.cpf)}" if row.cpf else "CPF -",
                    f"Cod. {row.internal_code}" if row.internal_code else "",
                ]
                if part
            ),
        }
        for row in queryset
    ]


def _family_relationships_from_snapshots(person_snapshot, relationship_model) -> tuple[list[dict[str, Any]], set[int]]:
    rows = list(
        relationship_model.objects.filter(is_active=True)
        .filter(person=person_snapshot)
        .select_related("related_person")
    ) + list(
        relationship_model.objects.filter(is_active=True)
        .filter(related_person=person_snapshot)
        .select_related("person")
    )
    relationships: list[dict[str, Any]] = []
    related_ids: set[int] = set()
    for row in rows:
        related = row.related_person if row.person_id == person_snapshot.id else row.person
        related_ids.add(int(related.legacy_id or 0))
        relationships.append(
            {
                "id": moneyless_int(row.legacy_id),
                "related_id": moneyless_int(related.legacy_id),
                "related_nome": related.name or "",
                "codigo": related.internal_code or "",
                "cpf": format_cpf(related.cpf),
                "status": format_status(related.status),
                "sigla": status_sigla(related.status, True),
                "tipo": row.relationship_type or "",
                "tipo_label": FAMILY_RELATIONSHIP_LABELS.get(row.relationship_type or "", row.relationship_type or ""),
                "observacoes": row.notes or "",
                "automatico_endereco": "AUTOMATICAMENTE POR ENDERECO" in normalize_match_name(row.notes or ""),
                "criado_em": br_datetime(row.created_at_legacy),
            }
        )
    relationships.sort(key=lambda item: (normalize_match_name(item["related_nome"]), item["id"]))
    return relationships, related_ids


def _suppressed_family_ids_from_snapshots(person_snapshot, relationship_model) -> set[int]:
    suppressed_rows = list(
        relationship_model.objects.filter(is_active=False, relationship_type="nucleo_familiar")
        .filter(person=person_snapshot)
        .select_related("related_person")
    ) + list(
        relationship_model.objects.filter(is_active=False, relationship_type="nucleo_familiar")
        .filter(related_person=person_snapshot)
        .select_related("person")
    )
    suppressed: set[int] = set()
    for row in suppressed_rows:
        if MANUAL_FAMILY_SUPPRESSION_MARKER not in normalize_match_name(row.notes or ""):
            continue
        related = row.related_person if row.person_id == person_snapshot.id else row.person
        suppressed.add(int(related.legacy_id or 0))
    return suppressed


def _family_suggestions_from_snapshots(person_snapshot, address_model, relationship_model) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    person_addresses = list(address_model.objects.filter(person=person_snapshot).order_by("-is_primary", "legacy_id"))
    address_keys = {
        _address_match_key(
            {
                "cep": address.cep,
                "logradouro": address.street,
                "numero": address.number,
                "complemento": address.complement,
                "bairro": address.neighborhood,
                "cidade": address.city,
                "uf": address.state,
            }
        ): _format_address_line(
            {
                "cep": address.cep,
                "logradouro": address.street,
                "numero": address.number,
                "complemento": address.complement,
                "bairro": address.neighborhood,
                "cidade": address.city,
                "uf": address.state,
            }
        )
        for address in person_addresses
        if _address_match_key(
            {
                "cep": address.cep,
                "logradouro": address.street,
                "numero": address.number,
                "complemento": address.complement,
                "bairro": address.neighborhood,
                "cidade": address.city,
                "uf": address.state,
            }
        )
    }
    relationships, related_ids = _family_relationships_from_snapshots(person_snapshot, relationship_model)
    suppressed_related_ids = _suppressed_family_ids_from_snapshots(person_snapshot, relationship_model)
    people_options: list[dict[str, Any]] = []
    if not address_keys:
        return relationships, [], people_options
    suggestions: list[dict[str, Any]] = []
    seen_people: set[int] = set()
    candidate_addresses = (
        address_model.objects.filter(person__organization_id=person_snapshot.organization_id, person__is_active=True)
        .exclude(person=person_snapshot)
        .select_related("person")
        .order_by("person__normalized_name", "-is_primary", "legacy_id")
    )
    for address in candidate_addresses:
        related_legacy_id = int(address.person.legacy_id or 0)
        if related_legacy_id in related_ids or related_legacy_id in suppressed_related_ids or related_legacy_id in seen_people:
            continue
        row = {
            "cep": address.cep,
            "logradouro": address.street,
            "numero": address.number,
            "complemento": address.complement,
            "bairro": address.neighborhood,
            "cidade": address.city,
            "uf": address.state,
        }
        key = _address_match_key(row)
        if key not in address_keys:
            continue
        seen_people.add(related_legacy_id)
        suggestions.append(
            {
                "related_id": related_legacy_id,
                "related_nome": address.person.name or "",
                "codigo": address.person.internal_code or "",
                "cpf": format_cpf(address.person.cpf),
                "status": format_status(address.person.status),
                "sigla": status_sigla(address.person.status, True),
                "address": _format_address_line(row),
                "reason": "Endereco completo igual ao da ficha.",
            }
        )
    return relationships, suggestions[:40], people_options


def _family_rows_from_snapshots() -> list[dict[str, Any]]:
    models = _people_snapshot_models()
    if not models:
        raise LegacyDatabaseError("Espelho cadastral Postgres indisponivel.")
    person_model = models["person"]
    address_model = models["address"]

    address_index: dict[int, list[Any]] = defaultdict(list)
    for address in address_model.objects.order_by("person_id", "-is_primary", "legacy_id"):
        address_index[moneyless_int(address.person_id)].append(address)

    rows: list[dict[str, Any]] = []
    for person in person_model.objects.filter(is_active=True).order_by("legacy_id"):
        person_id = moneyless_int(person.legacy_id)
        addresses = address_index.get(moneyless_int(person.id)) or [None]
        for address in addresses:
            rows.append(
                {
                    "id": person_id,
                    "codigo_interno": person.internal_code or "",
                    "nome": person.name or "",
                    "cpf": person.cpf or "",
                    "status": person.status or "",
                    "data_nascimento": person.birth_date_raw or "",
                    "cep": address.cep if address else "",
                    "logradouro": address.street if address else "",
                    "numero": address.number if address else "",
                    "complemento": address.complement if address else "",
                    "bairro": address.neighborhood if address else "",
                    "cidade": address.city if address else "",
                    "uf": address.state if address else "",
                }
            )
    return rows


def _family_relationship_sets_from_snapshots() -> tuple[set[tuple[int, int]], set[tuple[int, int]]]:
    models = _people_snapshot_models()
    if not models:
        raise LegacyDatabaseError("Espelho cadastral Postgres indisponivel.")
    relationship_model = models["relationship"]
    relationship_pairs = {
        _relationship_pair(row.person.legacy_id, row.related_person.legacy_id)
        for row in relationship_model.objects.filter(is_active=True).select_related("person", "related_person")
    }
    suppressed_pairs = {
        _relationship_pair(row.person.legacy_id, row.related_person.legacy_id)
        for row in relationship_model.objects.filter(is_active=False, relationship_type="nucleo_familiar").select_related("person", "related_person")
        if MANUAL_FAMILY_SUPPRESSION_MARKER in normalize_match_name(row.notes or "")
    }
    return relationship_pairs, suppressed_pairs


def _nucleus_relationship_rows_from_snapshots() -> list[dict[str, Any]]:
    models = _people_snapshot_models()
    if not models:
        raise LegacyDatabaseError("Espelho cadastral Postgres indisponivel.")
    relationship_model = models["relationship"]
    return [
        {
            "pessoa_id": moneyless_int(row.person.legacy_id),
            "pessoa_relacionada_id": moneyless_int(row.related_person.legacy_id),
        }
        for row in relationship_model.objects.filter(is_active=True, relationship_type="nucleo_familiar").select_related("person", "related_person")
    ]


def _family_financial_indexes_from_snapshots() -> tuple[dict[int, dict[str, Any]], dict[int, dict[str, Any]]]:
    models = _people_snapshot_models()
    if not models:
        raise LegacyDatabaseError("Espelho cadastral Postgres indisponivel.")
    contribution_model = models["contribution"]
    contributor_model = models["contributor"]
    contribution_index: dict[int, dict[str, Any]] = {}
    for row in contribution_model.objects.filter(is_active=True).only(
        "person_id",
        "amount",
        "received_at_raw",
    ):
        person_id = moneyless_int(getattr(row, "person_id", 0))
        if not person_id:
            continue
        bucket = contribution_index.setdefault(
            person_id,
            {"count": 0, "total_value": 0.0, "last_date": ""},
        )
        bucket["count"] += 1
        bucket["total_value"] += float(row.amount or 0)
        received_at_raw = str(row.received_at_raw or "")
        if received_at_raw and received_at_raw > str(bucket.get("last_date") or ""):
            bucket["last_date"] = received_at_raw
    contributor_index: dict[int, dict[str, Any]] = {}
    for row in contributor_model.objects.filter(is_active=True).only(
        "person_id",
        "name",
    ):
        person_id = moneyless_int(getattr(row, "person_id", 0))
        if not person_id:
            continue
        bucket = contributor_index.setdefault(
            person_id,
            {"count": 0, "names": []},
        )
        bucket["count"] += 1
        name = normalize_query(getattr(row, "name", ""))
        if name:
            bucket["names"].append(name)
    contributor_index = {
        person_id: {
            "count": values["count"],
            "names": " / ".join(values["names"]),
        }
        for person_id, values in contributor_index.items()
    }
    return contribution_index, contributor_index


ENVELOPE_STATUS_LABELS = {
    "aguardando_digitacao": "Aguardando digitacao",
    "lancado": "Lancado",
    "ignorado": "Ignorado",
    "duplicado": "Duplicado",
}


def _row_get(row: sqlite3.Row | None, key: str, default: Any = "") -> Any:
    if row is None:
        return default
    return row[key] if key in row.keys() else default


def column_exists(conn: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    return any(str(row["name"]) == column_name for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall())


def _month_value_from_order(order: object) -> str:
    value = moneyless_int(order)
    if value <= 0:
        return date.today().strftime("%Y-%m")
    year = value // 100
    month = value % 100
    if year <= 0 or month < 1 or month > 12:
        return date.today().strftime("%Y-%m")
    return f"{year:04d}-{month:02d}"


def _default_type_id(type_options: list[dict[str, Any]]) -> int:
    for option in type_options:
        if str(option.get("codigo") or "").upper() == "DIZIMO":
            return moneyless_int(option.get("id"))
    return moneyless_int(type_options[0].get("id")) if type_options else 0


def _envelope_status_label(status: object) -> str:
    text = str(status or "").strip()
    return ENVELOPE_STATUS_LABELS.get(text, text.replace("_", " ").title() if text else "Sem status")


def dashboard_summary() -> dict[str, Any]:
    with connect_legacy() as conn:
        people_total = int(scalar(conn, "SELECT COUNT(*) FROM pessoas WHERE ativo = 1") or 0)
        active_members = int(
            scalar(conn, "SELECT COUNT(*) FROM pessoas WHERE ativo = 1 AND status = 'membro_ativo'") or 0
        )
        active_members_niteroi = int(
            scalar(
                conn,
                """
                SELECT COUNT(*)
                  FROM pessoas p
                 WHERE p.ativo = 1
                   AND p.status = 'membro_ativo'
                   AND EXISTS (
                        SELECT 1
                          FROM pessoa_enderecos pe
                         WHERE pe.pessoa_id = p.id
                           AND NORMALIZE_MATCH(COALESCE(pe.cidade, '')) = 'NITEROI'
                   )
                """,
            )
            or 0
        )
        contributors_total = int(scalar(conn, "SELECT COUNT(*) FROM contribuintes WHERE ativo = 1") or 0)
        contributions_count = int(scalar(conn, "SELECT COUNT(*) FROM contribuicoes WHERE ativo = 1") or 0)
        contributions_total = float(scalar(conn, "SELECT COALESCE(SUM(valor), 0) FROM contribuicoes WHERE ativo = 1") or 0)
        envelope_count = 0
        envelope_total = 0.0
        if table_exists(conn, "envelopes"):
            envelope_count = int(scalar(conn, "SELECT COUNT(*) FROM envelopes WHERE ativo = 1") or 0)
            envelope_total = float(
                scalar(conn, "SELECT COALESCE(SUM(total_informado), 0) FROM envelopes WHERE ativo = 1 AND status = 'lancado'")
                or 0
            )
        unlinked_contributions = int(
            scalar(
                conn,
                """
                SELECT COUNT(*)
                  FROM contribuicoes
                 WHERE ativo = 1
                   AND pessoa_id IS NULL
                   AND COALESCE(status_operacional, '') <> 'ignorado'
                """,
            )
            or 0
        )
        statement_lots = int(scalar(conn, "SELECT COUNT(*) FROM extrato_lotes") or 0)
        pix_lots = int(scalar(conn, "SELECT COUNT(*) FROM pix_lotes") or 0)
        pending_pix = int(
            scalar(
                conn,
                f"""
                SELECT COUNT(*)
                  FROM pix_movimentos
                 WHERE ativo = 1
                   AND {human_pending_review_sql('pix_movimentos')}
                """,
            )
            or 0
        )
        pending_statements = int(
            scalar(
                conn,
                f"""
                SELECT COUNT(*)
                  FROM extrato_movimentos
                 WHERE ativo = 1
                   AND {human_pending_review_sql('extrato_movimentos')}
                """,
            )
            or 0
        )
        months = [
            {
                "competencia": row["competencia"] or "Sem competencia",
                "count": int(row["total_lancamentos"] or 0),
                "total": float(row["total_valor"] or 0),
                "total_fmt": _money(row["total_valor"]),
            }
            for row in conn.execute(
                """
                SELECT COALESCE(competencia, '') AS competencia,
                       COUNT(*) AS total_lancamentos,
                       COALESCE(SUM(valor), 0) AS total_valor,
                       MAX(COALESCE(competencia_ordem, 0)) AS ordem
                  FROM contribuicoes
                 WHERE ativo = 1
                 GROUP BY COALESCE(competencia, '')
                 ORDER BY ordem DESC, competencia DESC
                 LIMIT 6
                """
            ).fetchall()
        ]
        statuses = [
            {"status": format_status(row["status"]), "count": int(row["total"] or 0)}
            for row in conn.execute(
                """
                SELECT COALESCE(status, '') AS status, COUNT(*) AS total
                  FROM pessoas
                 WHERE ativo = 1
                 GROUP BY COALESCE(status, '')
                 ORDER BY total DESC
                """
            ).fetchall()
        ]
    household_summary = organized_family_nuclei_summary()
    household_broad = broad_family_candidates_summary()

    return {
        "people_total": people_total,
        "active_members": active_members,
        "active_members_niteroi": active_members_niteroi,
        "contributors_total": contributors_total,
        "contributions_count": contributions_count,
        "contributions_total": contributions_total,
        "contributions_total_fmt": _money(contributions_total),
        "envelope_count": envelope_count,
        "envelope_total": envelope_total,
        "envelope_total_fmt": _money(envelope_total),
        "unlinked_contributions": unlinked_contributions,
        "statement_lots": statement_lots,
        "pix_lots": pix_lots,
        "total_lots": statement_lots + pix_lots,
        "pending_bank_reviews": pending_pix + pending_statements,
        "household_total": household_summary["total"],
        "household_family_groups": household_summary["family_groups"],
        "household_single_groups": household_summary["single_groups"],
        "household_review_groups": household_summary["review_groups"],
        "household_broad_groups": household_broad["total"],
        "household_broad_pending": household_broad["pending_groups"],
        "months": months,
        "people_statuses": statuses,
    }


def list_people(q: str = "", status: str = "", city: str = "", limit: int | None = None) -> dict[str, Any]:
    q = (q or "").strip()
    status = (status or "").strip()
    city = normalize_query(city)
    if _people_snapshot_available():
        return _list_people_from_snapshots(q=q, status=status, city=city, limit=limit)
    raise LegacyDatabaseError("Espelho cadastral Postgres indisponivel para listar pessoas.")


def search_people_for_relationship(person_id: int, q: str = "", limit: int = 20) -> list[dict[str, Any]]:
    person_id = moneyless_int(person_id)
    query = normalize_query(q)
    digits = "".join(ch for ch in query if ch.isdigit())
    if not person_id or (len(query) < 2 and len(digits) < 2):
        return []
    if _people_snapshot_available():
        return _search_people_for_relationship_from_snapshots(person_id, q=query, limit=limit)
    raise LegacyDatabaseError("Espelho cadastral Postgres indisponivel para busca de relacionamento.")


def get_person_detail(person_id: int) -> dict[str, Any] | None:
    snapshot_models = _people_snapshot_models() if _people_snapshot_available() else None
    if snapshot_models:
        person_snapshot = snapshot_models["person"].objects.filter(legacy_id=person_id, is_active=True).first()
        if person_snapshot is not None:
            contacts = list(snapshot_models["contact"].objects.filter(person=person_snapshot).order_by("-is_primary", "contact_type", "legacy_id"))
            addresses = list(snapshot_models["address"].objects.filter(person=person_snapshot).order_by("-is_primary", "legacy_id"))
            profiles = list(snapshot_models["profile"].objects.filter(person=person_snapshot, is_active=True).order_by("profile", "legacy_id"))
            history = list(snapshot_models["history"].objects.filter(person=person_snapshot).order_by("-created_at_legacy", "-legacy_id")[:12])
            contributors = list(snapshot_models["contributor"].objects.filter(person=person_snapshot, is_active=True).order_by("name", "legacy_id"))
            identifiers = list(snapshot_models["identifier"].objects.filter(person=person_snapshot, is_active=True).order_by("-is_primary", "identifier_type", "legacy_id")[:30])
            contributions = list(
                snapshot_models["contribution"].objects.filter(person=person_snapshot, is_active=True).order_by(
                    "-competence_order", "-received_at", "-legacy_id"
                )[:40]
            )
            family_relationships, family_suggestions, family_people_options = _family_suggestions_from_snapshots(
                person_snapshot,
                snapshot_models["address"],
                snapshot_models["relationship"],
            )
            contribution_summary_map: dict[str, dict[str, Any]] = {}
            total_value = 0.0
            for contribution in snapshot_models["contribution"].objects.filter(person=person_snapshot, is_active=True):
                competence_key = contribution.competence or ""
                item = contribution_summary_map.setdefault(
                    competence_key,
                    {
                        "competencia": competence_key or "Sem competencia",
                        "remessas": 0,
                        "total_valor": 0.0,
                        "ordem": int(contribution.competence_order or 0),
                    },
                )
                item["remessas"] += 1
                item["total_valor"] += float(contribution.amount or 0)
                item["ordem"] = max(int(item["ordem"]), int(contribution.competence_order or 0))
                total_value += float(contribution.amount or 0)
            return {
                "person": {
                    "id": person_snapshot.legacy_id,
                    "codigo": person_snapshot.internal_code or "",
                    "nome": person_snapshot.name or "",
                    "nome_social": person_snapshot.social_name or "",
                    "cpf": person_snapshot.cpf or "",
                    "rg": person_snapshot.rg or "",
                    "data_nascimento": br_date(person_snapshot.birth_date_raw),
                    "sexo": person_snapshot.sex or "",
                    "estado_civil": person_snapshot.marital_status or "",
                    "email": person_snapshot.primary_email or "",
                    "telefone": person_snapshot.primary_phone or "",
                    "whatsapp": person_snapshot.primary_whatsapp or "",
                    "status": format_status(person_snapshot.status),
                    "status_raw": person_snapshot.status or "",
                    "sigla": status_sigla(person_snapshot.status, True),
                    "ativo": "Sim" if person_snapshot.is_active else "Nao",
                    "observacoes": person_snapshot.notes or "",
                    "criado_em": br_datetime(person_snapshot.created_at_legacy),
                    "atualizado_em": br_datetime(person_snapshot.updated_at_legacy),
                    "photo_url": member_photo_url(person_snapshot.legacy_id, person_snapshot.cpf, person_snapshot.name),
                },
                "profiles": [
                    {
                        "perfil": row.profile or "",
                        "data_inicio": row.start_date_raw or "",
                        "data_fim": row.end_date_raw or "",
                        "observacoes": row.notes or "",
                    }
                    for row in profiles
                ],
                "contacts": [
                    {
                        "tipo": row.contact_type,
                        "valor": row.value,
                        "principal": row.is_primary,
                        "observacoes": row.notes,
                    }
                    for row in contacts
                ],
                "addresses": [
                    {
                        "tipo": row.address_type,
                        "cep": row.cep,
                        "logradouro": row.street,
                        "numero": row.number,
                        "complemento": row.complement,
                        "bairro": row.neighborhood,
                        "cidade": row.city,
                        "uf": row.state,
                        "principal": row.is_primary,
                    }
                    for row in addresses
                ],
                "family_relationships": family_relationships,
                "family_suggestions": family_suggestions,
                "family_people_options": family_people_options,
                "family_relationship_type_options": FAMILY_RELATIONSHIP_OPTIONS,
                "history": [
                    {
                        "tipo_evento": row.event_type or "",
                        "data_evento": br_date(row.event_date_raw),
                        "titulo": row.title or "",
                        "descricao": row.description or "",
                        "origem": row.origin or "",
                        "destino": row.destination or "",
                        "criado_em": br_datetime(row.created_at_legacy),
                    }
                    for row in history
                ],
                "contributors": [
                    {
                        "id": moneyless_int(row.legacy_id),
                        "nome": row.name or "",
                        "tipo": row.contributor_type or "",
                        "documento_principal": row.primary_document or "",
                        "documento_tipo": row.document_type or "",
                        "origem": row.origin or "",
                        "qualidade": row.quality or "",
                        "status": row.status or "",
                    }
                    for row in contributors
                ],
                "identifiers": [
                    {
                        "tipo": row.identifier_type or "",
                        "valor": row.value or "",
                        "principal": row.is_primary,
                        "observacoes": row.notes or "",
                    }
                    for row in identifiers
                ],
                "contributions": [_format_snapshot_contribution_row(row) for row in contributions],
                "contribution_summary": [
                    {
                        "competencia": row["competencia"] or "Sem competencia",
                        "remessas": int(row["remessas"] or 0),
                        "total_fmt": _money(row["total_valor"]),
                    }
                    for row in sorted(
                        contribution_summary_map.values(),
                        key=lambda item: (-int(item["ordem"]), str(item["competencia"])),
                    )[:12]
                ],
                "total_contributions_fmt": _money(total_value),
                "audit": [],
            }
    raise LegacyDatabaseError("Espelho cadastral Postgres indisponivel para abrir a ficha da pessoa.")


def list_secure_people_trash(limit: int = 200) -> dict[str, Any]:
    with connect_legacy() as conn:
        if not table_exists(conn, "pessoas_lixeira_segura"):
            return {"items": [], "total": 0, "shown": 0}
        total = int(scalar(conn, "SELECT COUNT(*) FROM pessoas_lixeira_segura") or 0)
        rows = conn.execute(
            """
            SELECT id, pessoa_id, nome, cpf, motivo, operador, restaurado, criado_em, restaurado_em, snapshot_json
              FROM pessoas_lixeira_segura
             ORDER BY COALESCE(criado_em, '') DESC, id DESC
             LIMIT ?
            """,
            (limit,),
        ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        snapshot: dict[str, Any] = {}
        try:
            snapshot = json.loads(row["snapshot_json"] or "{}")
        except json.JSONDecodeError:
            snapshot = {}
        person_snapshot = snapshot.get("pessoa") if isinstance(snapshot, dict) else {}
        if not isinstance(person_snapshot, dict):
            person_snapshot = {}
        person_id = moneyless_int(row["pessoa_id"])
        direct_contributions = int(
            scalar(conn, "SELECT COUNT(*) FROM contribuicoes WHERE pessoa_id = ?", (person_id,)) or 0
        )
        contributor_contributions = int(
            scalar(
                conn,
                """
                SELECT COUNT(*)
                  FROM contribuicoes co
                  JOIN contribuintes c ON c.id = co.contribuinte_id
                 WHERE c.pessoa_id = ?
                """,
                (person_id,),
            )
            or 0
        )
        receipts = int(scalar(conn, "SELECT COUNT(*) FROM recibos WHERE pessoa_id = ?", (person_id,)) or 0)
        financial_entries = int(
            scalar(
                conn,
                "SELECT COUNT(*) FROM lancamentos_financeiros WHERE entidade_pessoa_id = ?",
                (person_id,),
            )
            or 0
        )
        blockers = {
            "contribuicoes": direct_contributions + contributor_contributions,
            "recibos": receipts,
            "lancamentos_financeiros": financial_entries,
        }
        blocker_text = ", ".join(f"{value} {label}" for label, value in blockers.items() if value)
        items.append(
            {
                "id": row["id"],
                "person_id": person_id,
                "nome": row["nome"] or person_snapshot.get("nome") or "",
                "cpf": format_cpf(row["cpf"] or person_snapshot.get("cpf") or ""),
                "motivo": row["motivo"] or "",
                "operador": row["operador"] or "",
                "restaurado": "Sim" if row["restaurado"] else "Nao",
                "can_purge": not row["restaurado"] and not any(blockers.values()),
                "purge_blockers": blocker_text,
                "criado_em": br_datetime(row["criado_em"]),
                "restaurado_em": br_datetime(row["restaurado_em"]),
                "status_original": format_status(person_snapshot.get("status")),
                "codigo_original": person_snapshot.get("codigo_interno") or "",
            }
        )
    return {"items": items, "total": total, "shown": len(items)}


def _address_match_key(row: sqlite3.Row) -> tuple[str, ...]:
    return family_address_key(row)


def _format_address_line(row: sqlite3.Row) -> str:
    first = normalize_query(f"{row['logradouro'] or ''} {row['numero'] or ''}")
    second_parts = [row["complemento"] or "", row["bairro"] or ""]
    second = " - ".join(normalize_query(part) for part in second_parts if normalize_query(part))
    city = normalize_query(f"{row['cidade'] or ''}/{row['uf'] or ''}").strip("/")
    cep = normalize_query(row["cep"])
    return " | ".join(part for part in [first, second, city, cep] if part)


def _person_family_data(
    conn: sqlite3.Connection,
    person_id: int,
    organization_id: int,
    address_rows: list[sqlite3.Row],
) -> dict[str, Any]:
    if not table_exists(conn, "pessoa_relacionamentos"):
        return {"relationships": [], "suggestions": [], "people_options": []}

    relationship_rows = conn.execute(
        """
        SELECT r.id, r.pessoa_id, r.pessoa_relacionada_id, r.tipo_relacionamento,
               r.observacoes, r.criado_em,
               p.id AS related_id, p.codigo_interno, p.nome, p.cpf, p.status
          FROM pessoa_relacionamentos r
          JOIN pessoas p
            ON p.id = CASE
                     WHEN r.pessoa_id = ? THEN r.pessoa_relacionada_id
                     ELSE r.pessoa_id
                 END
         WHERE r.organizacao_id = ?
           AND r.ativo = 1
           AND (r.pessoa_id = ? OR r.pessoa_relacionada_id = ?)
         ORDER BY p.nome COLLATE NOCASE, r.id
        """,
        (person_id, organization_id, person_id, person_id),
    ).fetchall()
    relationships = [
        {
            "id": moneyless_int(row["id"]),
            "related_id": moneyless_int(row["related_id"]),
            "related_nome": row["nome"] or "",
            "codigo": row["codigo_interno"] or "",
            "cpf": format_cpf(row["cpf"]),
            "status": format_status(row["status"]),
            "sigla": status_sigla(row["status"], True),
            "tipo": row["tipo_relacionamento"] or "",
            "tipo_label": FAMILY_RELATIONSHIP_LABELS.get(row["tipo_relacionamento"] or "", row["tipo_relacionamento"] or ""),
            "observacoes": row["observacoes"] or "",
            "automatico_endereco": "AUTOMATICAMENTE POR ENDERECO" in normalize_match_name(row["observacoes"] or ""),
            "criado_em": br_datetime(row["criado_em"]),
        }
        for row in relationship_rows
    ]
    related_ids = {item["related_id"] for item in relationships}
    suppressed_rows = conn.execute(
        """
        SELECT pessoa_id, pessoa_relacionada_id, tipo_relacionamento, observacoes
          FROM pessoa_relacionamentos
         WHERE organizacao_id = ?
           AND ativo = 0
           AND (pessoa_id = ? OR pessoa_relacionada_id = ?)
        """,
        (organization_id, person_id, person_id),
    ).fetchall()
    suppressed_related_ids = {
        (
            moneyless_int(row["pessoa_relacionada_id"])
            if moneyless_int(row["pessoa_id"]) == person_id
            else moneyless_int(row["pessoa_id"])
        )
        for row in suppressed_rows
        if row["tipo_relacionamento"] == "nucleo_familiar"
        and MANUAL_FAMILY_SUPPRESSION_MARKER in normalize_match_name(row["observacoes"] or "")
    }

    # A escolha manual usa busca incremental; evitar renderizar milhares de nomes na ficha.
    people_options: list[dict[str, Any]] = []

    address_keys = {
        _address_match_key(row): _format_address_line(row)
        for row in address_rows
        if _address_match_key(row)
    }
    if not address_keys:
        return {"relationships": relationships, "suggestions": [], "people_options": people_options}

    candidate_rows = conn.execute(
        """
        SELECT p.id, p.codigo_interno, p.nome, p.cpf, p.status,
               e.cep, e.logradouro, e.numero, e.complemento, e.bairro, e.cidade, e.uf
          FROM pessoas p
          JOIN pessoa_enderecos e ON e.pessoa_id = p.id
         WHERE p.organizacao_id = ?
           AND p.ativo = 1
           AND p.id <> ?
         ORDER BY p.nome COLLATE NOCASE, e.principal DESC, e.id
        """,
        (organization_id, person_id),
    ).fetchall()
    suggestions = []
    seen_people: set[int] = set()
    for row in candidate_rows:
        related_id = moneyless_int(row["id"])
        if related_id in related_ids or related_id in suppressed_related_ids or related_id in seen_people:
            continue
        key = _address_match_key(row)
        if key not in address_keys:
            continue
        seen_people.add(related_id)
        suggestions.append(
            {
                "related_id": related_id,
                "related_nome": row["nome"] or "",
                "codigo": row["codigo_interno"] or "",
                "cpf": format_cpf(row["cpf"]),
                "status": format_status(row["status"]),
                "sigla": status_sigla(row["status"], True),
                "address": _format_address_line(row),
                "reason": "Endereco completo igual ao da ficha.",
            }
        )
    return {"relationships": relationships, "suggestions": suggestions[:40], "people_options": people_options}


def _family_status_summary(people: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {"SA": 0, "SI": 0, "NF": 0, "NV": 0, "NM": 0, "NR": 0}
    for person in people:
        sigla = person.get("sigla") or "NR"
        counts[sigla] = counts.get(sigla, 0) + 1
    others = sum(value for key, value in counts.items() if key != "SA")
    return {
        "sa": counts.get("SA", 0),
        "outros": others,
        "counts": counts,
        "total": len(people),
    }


def _family_person_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": moneyless_int(row["id"]),
        "nome": row["nome"] or "",
        "codigo": row["codigo_interno"] or "",
        "cpf": format_cpf(row["cpf"]),
        "status": format_status(row["status"]),
        "status_raw": row["status"] or "",
        "sigla": status_sigla(row["status"], True),
        "data_nascimento_raw": row["data_nascimento"] if "data_nascimento" in row.keys() else "",
        "data_nascimento": br_date(row["data_nascimento"] if "data_nascimento" in row.keys() else ""),
        "photo_url": member_photo_url(row["id"], row["cpf"], row["nome"]),
        "complemento": row["complemento"] or "",
        "endereco": _format_address_line(row),
        "cep": row["cep"] or "",
        "logradouro": row["logradouro"] or "",
        "numero": row["numero"] or "",
        "bairro": row["bairro"] or "",
        "cidade": row["cidade"] or "",
        "uf": row["uf"] or "",
        "exact_key": family_address_key(row),
        "base_key": family_base_address_key(row),
    }


def _family_summary_person_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": moneyless_int(row["id"]),
        "nome": row["nome"] or "",
        "complemento": row["complemento"] or "",
        "endereco": _format_address_line(row),
        "exact_key": family_address_key(row),
        "base_key": family_base_address_key(row),
    }


def _household_short_name(value: object) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        return ""
    return text.split()[0].title()


def _household_profile_payload(people: list[dict[str, Any]]) -> dict[str, Any]:
    try:
        from power_church_django.services.family_profiles import household_profile_context
    except Exception:
        household_profile_context = None
    person_ids = [moneyless_int(person.get("id")) for person in people if moneyless_int(person.get("id"))]
    fallback_name = _organized_family_surname_label(people) or "Familia domiciliar"
    if not household_profile_context:
        return {
            "signature": ",".join(str(value) for value in sorted(set(person_ids))),
            "head_person_id": 0,
            "head_person_name": "",
            "display_name_override": "",
            "display_name_auto": fallback_name,
            "display_name_effective": fallback_name,
            "display_name_sort": normalize_match_name(fallback_name),
        }
    return household_profile_context(person_ids, people)


def _group_rows_have_specific_distinct_units(rows: list[sqlite3.Row]) -> bool:
    unit_by_person: dict[int, str] = {}
    for row in rows:
        person_id = moneyless_int(row["id"])
        if not person_id or person_id in unit_by_person:
            continue
        unit_by_person[person_id] = normalize_address_complement(row["complemento"])
    if len(unit_by_person) < 2:
        return False
    complements = [value for value in unit_by_person.values() if value]
    if len(complements) != len(unit_by_person):
        return False
    if len(set(complements)) != len(unit_by_person):
        return False
    return all(complement_has_specific_unit(value) for value in complements)


def _ambiguous_unit_rows(rows: list[sqlite3.Row]) -> list[sqlite3.Row]:
    seen_people: set[int] = set()
    ambiguous: list[sqlite3.Row] = []
    for row in rows:
        person_id = moneyless_int(row["id"])
        if not person_id or person_id in seen_people:
            continue
        seen_people.add(person_id)
        if address_complement_specificity(row["complemento"]) != "exact":
            ambiguous.append(row)
    return ambiguous


def _family_group_payload(
    key: tuple[str, ...],
    rows: list[sqlite3.Row],
    *,
    confidence: str,
    reason: str,
    include_complement: bool = True,
) -> dict[str, Any]:
    unique: dict[int, dict[str, Any]] = {}
    for row in rows:
        unique.setdefault(moneyless_int(row["id"]), _family_person_from_row(row))
    people = sorted(unique.values(), key=lambda item: normalize_match_name(item["nome"]))
    sample = rows[0]
    label = family_group_label(sample, sample["complemento"] if include_complement else "")
    status_summary = _family_status_summary(people)
    return {
        "label": label,
        "confidence": confidence,
        "reason": reason,
        "people": people,
        "person_ids": ",".join(str(item["id"]) for item in people),
        "status_summary": status_summary,
        "cep": sample["cep"] or "",
        "address": _format_address_line(sample),
    }


def _relationship_pair(left_id: object, right_id: object) -> tuple[int, int]:
    left = moneyless_int(left_id)
    right = moneyless_int(right_id)
    return (left, right) if left <= right else (right, left)


def _family_group_missing_relationships(
    payload: dict[str, Any],
    relationship_pairs: set[tuple[int, int]],
    suppressed_pairs: set[tuple[int, int]],
) -> int:
    ids = [moneyless_int(person["id"]) for person in payload["people"] if moneyless_int(person["id"])]
    missing = 0
    for index, left_id in enumerate(ids):
        for right_id in ids[index + 1 :]:
            pair = _relationship_pair(left_id, right_id)
            if pair not in relationship_pairs and pair not in suppressed_pairs:
                missing += 1
    return missing


def _digits_only(value: object) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _family_query_matches(query_key: str, digits: str, text_values: list[object], digit_values: list[object]) -> bool:
    if query_key:
        haystack = normalize_match_name(" ".join(str(value or "") for value in text_values))
        if query_key in haystack:
            return True
    if digits:
        digit_blob = " ".join(_digits_only(value) for value in digit_values if value)
        if digits in digit_blob:
            return True
    return not query_key and not digits


def _group_matches_person_status(payload: dict[str, Any], person_status: str) -> bool:
    status_filter = normalize_query(person_status)
    if not status_filter or status_filter == "all":
        return True
    return any(normalize_query(person.get("status_raw")) == status_filter for person in payload.get("people") or [])


def _family_group_matches_query(payload: dict[str, Any], query_key: str, digits: str) -> bool:
    people = payload.get("people") or []
    text_values: list[object] = [payload.get("label"), payload.get("address"), payload.get("reason"), payload.get("confidence")]
    digit_values: list[object] = [payload.get("cep")]
    for person in people:
        text_values.extend(
            [
                person.get("nome"),
                person.get("codigo"),
                person.get("cpf"),
                person.get("status"),
                person.get("endereco"),
                person.get("complemento"),
                person.get("bairro"),
                person.get("cidade"),
                person.get("uf"),
            ]
        )
        digit_values.extend([person.get("codigo"), person.get("cpf"), person.get("cep"), person.get("numero")])
    return _family_query_matches(query_key, digits, text_values, digit_values)


def _pick_primary_person_rows(rows: list[sqlite3.Row]) -> dict[int, sqlite3.Row]:
    picked: dict[int, sqlite3.Row] = {}
    for row in rows:
        person_id = moneyless_int(row["id"])
        if person_id and person_id not in picked:
            picked[person_id] = row
    return picked


def _family_person_financial_payload(
    person_id: int,
    contribution_index: dict[int, dict[str, Any]],
    contributor_index: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    contribution = contribution_index.get(person_id, {})
    contributor = contributor_index.get(person_id, {})
    contributions = moneyless_int(contribution.get("count"))
    total_value = float(contribution.get("total_value") or 0)
    last_date_raw = contribution.get("last_date") or ""
    linked_contributors = moneyless_int(contributor.get("count"))
    if contributions > 0:
        if last_date_raw:
            summary = f"{contributions} contribuicao(oes), {_money(total_value)}, ultima em {br_date(last_date_raw)}"
        else:
            summary = f"{contributions} contribuicao(oes), {_money(total_value)}"
    elif linked_contributors > 0:
        summary = f"{linked_contributors} contribuinte(s) vinculado(s) sem lancamentos ativos"
    else:
        summary = ""
    return {
        "contributions": contributions,
        "total_value": total_value,
        "total_fmt": _money(total_value),
        "last_date_raw": last_date_raw,
        "last_date": br_date(last_date_raw),
        "linked_contributors": linked_contributors,
        "linked_names": contributor.get("names", ""),
        "has_activity": contributions > 0 or linked_contributors > 0,
        "summary": summary,
    }


def _family_financial_summary(people: list[dict[str, Any]]) -> dict[str, Any]:
    financial_people = [person for person in people if person.get("financial", {}).get("has_activity")]
    contribution_people = [person for person in people if person.get("financial", {}).get("contributions", 0) > 0]
    linked_people = [person for person in people if person.get("financial", {}).get("linked_contributors", 0) > 0]
    total_value = sum(float(person.get("financial", {}).get("total_value") or 0) for person in people)
    last_date_raw = max(
        (str(person.get("financial", {}).get("last_date_raw") or "") for person in contribution_people),
        default="",
    )
    notes: list[str] = []
    for person in financial_people:
        summary = person.get("financial", {}).get("summary") or ""
        if summary:
            notes.append(f"{person['nome']}: {summary}")
    return {
        "people": financial_people,
        "active_people": len(financial_people),
        "contributing_people": len(contribution_people),
        "linked_people": len(linked_people),
        "linked_contributors": sum(moneyless_int(person.get("financial", {}).get("linked_contributors")) for person in linked_people),
        "total_value": total_value,
        "total_fmt": _money(total_value),
        "last_date_raw": last_date_raw,
        "last_date": br_date(last_date_raw),
        "note": " | ".join(notes[:6]),
    }


def _family_alignment_payload(people: list[dict[str, Any]]) -> dict[str, Any]:
    address_lines = [str(person.get("endereco") or "") for person in people if person.get("endereco")]
    unique_addresses = sorted(dict.fromkeys(address_lines))
    exact_keys = {str(person.get("exact_key") or "") for person in people if person.get("exact_key")}
    base_keys = {str(person.get("base_key") or "") for person in people if person.get("base_key")}
    missing_address = any(not person.get("exact_key") for person in people)
    representative = next((person for person in people if person.get("exact_key")), people[0] if people else {})
    if len(exact_keys) == 1 and not missing_address:
        status = "alinhado"
        badge = "Endereco alinhado"
        reason = "Todos os membros seguem o mesmo endereco completo."
        label = family_group_label(representative, representative.get("complemento", ""))
    elif len(base_keys) == 1 and base_keys:
        status = "complemento"
        badge = "Auditar complemento"
        reason = "Mesmo domicilio base, mas ha complemento divergente, incompleto ou ausente."
        label = family_group_label(representative, "")
    elif missing_address and len(exact_keys) <= 1:
        status = "sem_endereco"
        badge = "Auditar endereco"
        reason = "Ha membro sem endereco cadastrado ou sem endereco principal consistente."
        label = family_group_label(representative, representative.get("complemento", "")) if representative else "Nucleo familiar"
    else:
        status = "divergente"
        badge = "Auditar domicilio"
        reason = "O vinculo domiciliar esta ativo, mas os enderecos atuais do nucleo divergem."
        surname = _organized_family_surname_label(people) or "Nucleo familiar"
        label = f"Nucleo {surname}"
    if unique_addresses:
        address_preview = unique_addresses[0]
        if len(unique_addresses) > 1:
            address_preview = f"{address_preview} (+{len(unique_addresses) - 1} variacao(oes))"
    else:
        address_preview = "Sem endereco principal cadastrado."
    return {
        "status": status,
        "badge": badge,
        "needs_review": status != "alinhado",
        "reason": reason,
        "label": label,
        "address_preview": address_preview,
        "addresses": unique_addresses,
    }


def _organized_family_surname_label(people: list[dict[str, Any]]) -> str:
    broad_counts: dict[str, int] = defaultdict(int)
    nuclear_counts: dict[str, int] = defaultdict(int)
    for person in people:
        keys = contributor_family_keys(person.get("nome"))
        if keys.get("broad"):
            broad_counts[keys["broad"]] += 1
        if keys.get("nuclear"):
            nuclear_counts[keys["nuclear"]] += 1
    if nuclear_counts:
        key = sorted(nuclear_counts.items(), key=lambda item: (-item[1], item[0]))[0][0]
        return key.title()
    if broad_counts:
        key = sorted(broad_counts.items(), key=lambda item: (-item[1], item[0]))[0][0]
        return key.title()
    return ""


def organized_family_nuclei_summary() -> dict[str, int]:
    if _people_snapshot_available():
        try:
            person_rows = _family_rows_from_snapshots()
            relationship_rows = _nucleus_relationship_rows_from_snapshots()
        except LegacyDatabaseError:
            person_rows = []
            relationship_rows = []
    else:
        raise LegacyDatabaseError("Espelho cadastral Postgres indisponivel para familias domiciliares.")
    primary_rows = _pick_primary_person_rows(person_rows)
    graph: dict[int, set[int]] = defaultdict(set)
    for row in relationship_rows:
        left_id = moneyless_int(row["pessoa_id"])
        right_id = moneyless_int(row["pessoa_relacionada_id"])
        if not left_id or not right_id:
            continue
        if left_id not in primary_rows or right_id not in primary_rows:
            continue
        graph[left_id].add(right_id)
        graph[right_id].add(left_id)

    def _group_needs_review(component_ids: list[int]) -> bool:
        people = [
            _family_summary_person_from_row(primary_rows[person_id])
            for person_id in sorted(set(component_ids))
            if person_id in primary_rows
        ]
        if not people:
            return False
        return bool(_family_alignment_payload(people).get("needs_review"))

    seen: set[int] = set()
    assigned_ids: set[int] = set()
    family_groups = 0
    single_groups = 0
    review_groups = 0

    for node in sorted(graph):
        if node in seen:
            continue
        stack = [node]
        component_ids: list[int] = []
        seen.add(node)
        while stack:
            current = stack.pop()
            component_ids.append(current)
            for next_id in graph[current]:
                if next_id not in seen:
                    seen.add(next_id)
                    stack.append(next_id)
        if not component_ids:
            continue
        assigned_ids.update(component_ids)
        if len(component_ids) <= 1:
            single_groups += 1
        else:
            family_groups += 1
        if _group_needs_review(component_ids):
            review_groups += 1

    for person_id in sorted(primary_rows):
        if person_id in assigned_ids:
            continue
        single_groups += 1
        if _group_needs_review([person_id]):
            review_groups += 1

    return {
        "total": family_groups + single_groups,
        "family_groups": family_groups,
        "single_groups": single_groups,
        "review_groups": review_groups,
    }


def broad_family_candidates_summary() -> dict[str, int]:
    if _people_snapshot_available():
        try:
            rows = _family_rows_from_snapshots()
            relationship_pairs, suppressed_pairs = _family_relationship_sets_from_snapshots()
        except LegacyDatabaseError:
            rows = []
            relationship_pairs = set()
            suppressed_pairs = set()
    else:
        raise LegacyDatabaseError("Espelho cadastral Postgres indisponivel para o criterio amplo de familias.")

    base_groups: dict[tuple[str, ...], dict[int, dict[str, Any]]] = defaultdict(dict)
    for row in rows:
        base_key = family_base_address_key(row)
        person_id = moneyless_int(row["id"])
        if not base_key or not person_id:
            continue
        base_groups[base_key].setdefault(person_id, row)

    total_groups = 0
    pending_groups = 0
    consolidated_groups = 0
    for group_rows in base_groups.values():
        person_ids = sorted(group_rows)
        if len(person_ids) < 2:
            continue
        total_groups += 1
        missing_relationships = 0
        for index, left_id in enumerate(person_ids):
            for right_id in person_ids[index + 1 :]:
                pair = _relationship_pair(left_id, right_id)
                if pair not in relationship_pairs and pair not in suppressed_pairs:
                    missing_relationships += 1
        if missing_relationships > 0:
            pending_groups += 1
        else:
            consolidated_groups += 1

    return {
        "total": total_groups,
        "pending_groups": pending_groups,
        "consolidated_groups": consolidated_groups,
    }


def _organized_family_payload(
    person_ids: list[int],
    person_rows: dict[int, sqlite3.Row],
    contribution_index: dict[int, dict[str, Any]],
    contributor_index: dict[int, dict[str, Any]],
) -> dict[str, Any] | None:
    unique_people: list[dict[str, Any]] = []
    for person_id in sorted(set(moneyless_int(value) for value in person_ids if moneyless_int(value))):
        row = person_rows.get(person_id)
        if row is None:
            continue
        person = _family_person_from_row(row)
        person["nome_curto"] = _household_short_name(person["nome"])
        person["financial"] = _family_person_financial_payload(person_id, contribution_index, contributor_index)
        unique_people.append(person)
    unique_people.sort(key=lambda item: normalize_match_name(item["nome"]))
    if not unique_people:
        return None
    alignment = _family_alignment_payload(unique_people)
    financial = _family_financial_summary(unique_people)
    surname_label = _organized_family_surname_label(unique_people)
    profile = _household_profile_payload(unique_people)
    member_names = ", ".join(person["nome"] for person in unique_people)
    member_short_names = ", ".join(person["nome_curto"] for person in unique_people if person.get("nome_curto"))
    household_kind = "unipessoal" if len(unique_people) == 1 else "familiar"
    other_member_short_names = ", ".join(
        person["nome_curto"]
        for person in unique_people
        if person.get("nome_curto") and moneyless_int(person.get("id")) != moneyless_int(profile.get("head_person_id"))
    )
    return {
        "label": profile["display_name_effective"],
        "automatic_label": profile["display_name_auto"],
        "display_name_override": profile["display_name_override"],
        "system_label": alignment["label"],
        "profile_signature": profile["signature"],
        "head_person_id": profile["head_person_id"],
        "head_person_name": profile["head_person_name"],
        "sort_label": profile["display_name_sort"],
        "surname_label": surname_label,
        "people": unique_people,
        "member_count": len(unique_people),
        "member_names": member_names,
        "member_short_names": member_short_names,
        "other_member_short_names": other_member_short_names,
        "member_tag": member_short_names if len(unique_people) > 1 else "Unipessoal",
        "household_kind": household_kind,
        "household_kind_label": "Unipessoal" if household_kind == "unipessoal" else "Familia domiciliar",
        "status_summary": _family_status_summary(unique_people),
        "alignment_status": alignment["status"],
        "alignment_badge": alignment["badge"],
        "needs_review": alignment["needs_review"],
        "review_reason": alignment["reason"],
        "address": alignment["address_preview"],
        "addresses": alignment["addresses"],
        "person_ids": ",".join(str(person["id"]) for person in unique_people),
        "financial_summary": financial,
        "has_financial_member": financial["active_people"] > 0,
    }


def _organized_family_matches_query(payload: dict[str, Any], query_key: str, digits: str) -> bool:
    people = payload.get("people") or []
    text_values: list[object] = [
        payload.get("label"),
        payload.get("automatic_label"),
        payload.get("system_label"),
        payload.get("surname_label"),
        payload.get("review_reason"),
        payload.get("address"),
        payload.get("member_names"),
        payload.get("financial_summary", {}).get("note"),
    ]
    digit_values: list[object] = []
    for person in people:
        text_values.extend(
            [
                person.get("nome"),
                person.get("codigo"),
                person.get("cpf"),
                person.get("endereco"),
                person.get("complemento"),
                person.get("bairro"),
                person.get("cidade"),
                person.get("uf"),
                person.get("financial", {}).get("summary"),
            ]
        )
        digit_values.extend([person.get("codigo"), person.get("cpf"), person.get("cep"), person.get("numero")])
    return _family_query_matches(query_key, digits, text_values, digit_values)


def family_nuclei_dashboard(
    cep: str = "",
    mode: str = "all",
    q: str = "",
    limit: int | None = None,
    category: str = "all",
    person_status: str = "all",
) -> dict[str, Any]:
    cep_filter = "".join(ch for ch in str(cep or "") if ch.isdigit())
    mode = normalize_query(mode) or "all"
    category = normalize_query(category).lower() or "all"
    query = normalize_query(q)
    query_key = normalize_match_name(query)
    digits = _digits_only(query)
    if _people_snapshot_available():
        try:
            rows = _family_rows_from_snapshots()
            relationship_pairs, suppressed_pairs = _family_relationship_sets_from_snapshots()
        except LegacyDatabaseError:
            rows = []
            relationship_pairs = set()
            suppressed_pairs = set()
    else:
        raise LegacyDatabaseError("Espelho cadastral Postgres indisponivel para a auditoria de familias.")
    if cep_filter:
        rows = [
            row for row in rows
            if cep_filter in "".join(ch for ch in str(row["cep"] or "") if ch.isdigit())
        ]

    exact_groups: dict[tuple[str, ...], list[sqlite3.Row]] = defaultdict(list)
    base_groups: dict[tuple[str, ...], list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        exact_key = family_address_key(row)
        base_key = family_base_address_key(row)
        if exact_key:
            exact_groups[exact_key].append(row)
        if base_key:
            base_groups[base_key].append(row)

    exact_payloads = [
        _family_group_payload(
            key,
            group_rows,
            confidence="Alta confianca",
            reason="Endereco e unidade/complemento equivalentes.",
        )
        for key, group_rows in exact_groups.items()
        if len({moneyless_int(row["id"]) for row in group_rows}) > 1
    ]
    for payload in exact_payloads:
        payload["missing_relationships"] = _family_group_missing_relationships(payload, relationship_pairs, suppressed_pairs)
    exact_payloads = [payload for payload in exact_payloads if payload["missing_relationships"] > 0]
    hypothesis_payloads = []
    for key, group_rows in base_groups.items():
        people_ids = {moneyless_int(row["id"]) for row in group_rows}
        exact_keys = {family_address_key(row) for row in group_rows if family_address_key(row)}
        if len(people_ids) < 2 or len(exact_keys) <= 1:
            continue
        if _group_rows_have_specific_distinct_units(group_rows):
            continue
        candidate_rows = _ambiguous_unit_rows(group_rows)
        candidate_people_ids = {moneyless_int(row["id"]) for row in candidate_rows}
        if len(candidate_people_ids) < 2:
            continue
        payload = _family_group_payload(
            key,
            candidate_rows,
            confidence="Auditoria",
            reason="Mesmo CEP, logradouro e numero, com complemento ausente ou parcial que ainda precisa de auditoria humana.",
            include_complement=False,
        )
        payload["smart_audit"] = classify_family_audit_group([person.get("complemento") for person in payload.get("people") or []])
        payload["missing_relationships"] = _family_group_missing_relationships(payload, relationship_pairs, suppressed_pairs)
        if payload["missing_relationships"] > 0:
            hypothesis_payloads.append(payload)

    total_exact_payloads = list(exact_payloads)
    total_hypothesis_payloads = list(hypothesis_payloads)
    if query_key or digits:
        exact_payloads = [payload for payload in exact_payloads if _family_group_matches_query(payload, query_key, digits)]
        hypothesis_payloads = [payload for payload in hypothesis_payloads if _family_group_matches_query(payload, query_key, digits)]
    if person_status and person_status != "all":
        exact_payloads = [payload for payload in exact_payloads if _group_matches_person_status(payload, person_status)]
        hypothesis_payloads = [payload for payload in hypothesis_payloads if _group_matches_person_status(payload, person_status)]
    if category != "all":
        hypothesis_payloads = [
            payload
            for payload in hypothesis_payloads
            if str((payload.get("smart_audit") or {}).get("category_key") or "").lower() == category
        ]

    exact_payloads.sort(key=lambda item: (-item["status_summary"]["total"], item["label"]))
    hypothesis_payloads.sort(key=lambda item: (-item["status_summary"]["total"], item["label"]))
    selected_groups = []
    if mode == "all":
        selected_groups = exact_payloads + hypothesis_payloads
    elif mode in {"automaticos", "alta"}:
        selected_groups.extend(exact_payloads)
    elif mode in {"hipoteses", "auditoria"}:
        selected_groups.extend(hypothesis_payloads)
    limit_value = moneyless_int(limit) if limit is not None else 0
    if limit_value > 0:
        selected_groups = selected_groups[:limit_value]
    exact_people_ids = {person["id"] for group in total_exact_payloads for person in group["people"]}
    hypothesis_people_ids = {person["id"] for group in total_hypothesis_payloads for person in group["people"]}
    all_people = [person for group in selected_groups for person in group["people"]]
    return {
        "cep": cep,
        "mode": mode,
        "category": category,
        "q": query,
        "groups": selected_groups,
        "automatic_groups": exact_payloads,
        "hypothesis_groups": hypothesis_payloads,
        "smart_summary": summarize_smart_audit(total_hypothesis_payloads),
        "summary": {
            "automatic_groups": len(total_exact_payloads),
            "automatic_people": len(exact_people_ids),
            "hypothesis_groups": len(total_hypothesis_payloads),
            "hypothesis_people": len(hypothesis_people_ids),
            "shown_groups": len(selected_groups),
            "shown_status": _family_status_summary(all_people),
            "filtered_automatic_groups": len(exact_payloads),
            "filtered_hypothesis_groups": len(hypothesis_payloads),
        },
    }


def broad_family_candidates(q: str = "", cep: str = "", review: str = "all", person_status: str = "all") -> dict[str, Any]:
    query = normalize_query(q)
    query_key = normalize_match_name(query)
    digits = _digits_only(query)
    cep_filter = _digits_only(cep)
    review = normalize_query(review) or "all"
    if _people_snapshot_available():
        try:
            rows = _family_rows_from_snapshots()
            relationship_pairs, suppressed_pairs = _family_relationship_sets_from_snapshots()
        except LegacyDatabaseError:
            rows = []
            relationship_pairs = set()
            suppressed_pairs = set()
    else:
        raise LegacyDatabaseError("Espelho cadastral Postgres indisponivel para o criterio amplo de familias.")
    if cep_filter:
        rows = [
            row for row in rows
            if cep_filter in "".join(ch for ch in str(row["cep"] or "") if ch.isdigit())
        ]

    base_groups: dict[tuple[str, ...], list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        base_key = family_base_address_key(row)
        if base_key:
            base_groups[base_key].append(row)

    payloads: list[dict[str, Any]] = []
    for key, group_rows in base_groups.items():
        if len({moneyless_int(row["id"]) for row in group_rows}) < 2:
            continue
        payload = _family_group_payload(
            key,
            group_rows,
            confidence="Criterio amplo",
            reason="Mesmo CEP, logradouro e numero no criterio amplo. Esta lista serve para refinamento manual e consolidacao assistida.",
            include_complement=True,
        )
        payload["smart_audit"] = classify_family_audit_group([person.get("complemento") for person in payload.get("people") or []])
        payload["missing_relationships"] = _family_group_missing_relationships(payload, relationship_pairs, suppressed_pairs)
        payload["is_consolidated"] = payload["missing_relationships"] <= 0
        payload["can_consolidate"] = payload["missing_relationships"] > 0
        payloads.append(payload)

    total_groups = len(payloads)
    total_people = len({person["id"] for group in payloads for person in group["people"]})
    total_pending = sum(1 for group in payloads if group["can_consolidate"])
    total_consolidated = sum(1 for group in payloads if group["is_consolidated"])
    if query_key or digits:
        payloads = [payload for payload in payloads if _family_group_matches_query(payload, query_key, digits)]
    if person_status and person_status != "all":
        payloads = [payload for payload in payloads if _group_matches_person_status(payload, person_status)]
    if review == "audit":
        payloads = [payload for payload in payloads if payload["can_consolidate"]]
    elif review == "alinhados":
        payloads = [payload for payload in payloads if payload["is_consolidated"]]
    payloads.sort(
        key=lambda item: (
            0 if item["can_consolidate"] else 1,
            -item["status_summary"]["total"],
            item["label"],
        )
    )
    shown_people = [person for group in payloads for person in group["people"]]
    return {
        "q": query,
        "cep": cep,
        "review": review,
        "items": payloads,
        "total": total_groups,
        "shown": len(payloads),
        "total_people": total_people,
        "pending_groups": total_pending,
        "consolidated_groups": total_consolidated,
        "smart_summary": summarize_smart_audit(payloads),
        "shown_status": _family_status_summary(shown_people),
    }


def organized_family_nuclei(
    q: str = "",
    cep: str = "",
    review: str = "all",
    household_kind: str = "all",
    person_status: str = "all",
) -> dict[str, Any]:
    query = normalize_query(q)
    query_key = normalize_match_name(query)
    digits = _digits_only(query)
    cep_filter = _digits_only(cep)
    review = normalize_query(review) or "all"
    household_kind = normalize_query(household_kind) or "all"
    if _people_snapshot_available():
        try:
            person_rows = _family_rows_from_snapshots()
            relationship_rows = _nucleus_relationship_rows_from_snapshots()
            contribution_index, contributor_index = _family_financial_indexes_from_snapshots()
        except LegacyDatabaseError:
            person_rows = []
            relationship_rows = []
            contribution_index = {}
            contributor_index = {}
    else:
        raise LegacyDatabaseError("Espelho cadastral Postgres indisponivel para familias domiciliares.")
    primary_rows = _pick_primary_person_rows(person_rows)
    graph: dict[int, set[int]] = defaultdict(set)
    for row in relationship_rows:
        left_id = moneyless_int(row["pessoa_id"])
        right_id = moneyless_int(row["pessoa_relacionada_id"])
        if not left_id or not right_id:
            continue
        if left_id not in primary_rows or right_id not in primary_rows:
            continue
        graph[left_id].add(right_id)
        graph[right_id].add(left_id)
    seen: set[int] = set()
    nuclei: list[dict[str, Any]] = []
    for node in sorted(graph):
        if node in seen:
            continue
        stack = [node]
        component_ids: list[int] = []
        seen.add(node)
        while stack:
            current = stack.pop()
            component_ids.append(current)
            for next_id in graph[current]:
                if next_id not in seen:
                    seen.add(next_id)
                    stack.append(next_id)
        payload = _organized_family_payload(component_ids, primary_rows, contribution_index, contributor_index)
        if payload is None:
            continue
        nuclei.append(payload)
    assigned_ids = {person["id"] for group in nuclei for person in group["people"]}
    for person_id in sorted(primary_rows):
        if person_id in assigned_ids:
            continue
        payload = _organized_family_payload([person_id], primary_rows, contribution_index, contributor_index)
        if payload is None:
            continue
        nuclei.append(payload)
    total_groups = len(nuclei)
    total_people = len({person["id"] for group in nuclei for person in group["people"]})
    total_review = sum(1 for group in nuclei if group["needs_review"])
    family_groups = sum(1 for group in nuclei if group.get("household_kind") == "familiar")
    single_groups = sum(1 for group in nuclei if group.get("household_kind") == "unipessoal")
    if cep_filter:
        nuclei = [
            group
            for group in nuclei
            if any(cep_filter in _digits_only(person.get("cep")) for person in group["people"])
        ]
    if query_key or digits:
        nuclei = [group for group in nuclei if _organized_family_matches_query(group, query_key, digits)]
    if person_status and person_status != "all":
        nuclei = [group for group in nuclei if _group_matches_person_status(group, person_status)]
    if household_kind == "familiar":
        nuclei = [group for group in nuclei if group.get("household_kind") == "familiar"]
    elif household_kind == "unipessoal":
        nuclei = [group for group in nuclei if group.get("household_kind") == "unipessoal"]
    if review == "audit":
        nuclei = [group for group in nuclei if group["needs_review"]]
    elif review == "alinhados":
        nuclei = [group for group in nuclei if not group["needs_review"]]
    nuclei.sort(
        key=lambda item: (
            item.get("sort_label") or normalize_match_name(item["label"]),
            normalize_match_name(item.get("address")),
        )
    )
    return {
        "items": nuclei,
        "total": total_groups,
        "shown": len(nuclei),
        "review_groups": total_review,
        "total_people": total_people,
        "q": query,
        "cep": cep,
        "review": review,
        "household_kind": household_kind,
        "family_groups": family_groups,
        "single_groups": single_groups,
    }


def extended_family_clusters(
    nuclei: list[dict[str, Any]],
    q: str = "",
    review: str = "all",
    person_status: str = "all",
) -> dict[str, Any]:
    query = normalize_query(q)
    query_key = normalize_match_name(query)
    digits = _digits_only(query)
    review = normalize_query(review) or "all"
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for nucleus in nuclei:
        key = normalize_match_name(nucleus.get("surname_label") or "")
        if not key:
            continue
        grouped[key].append(nucleus)
    clusters: list[dict[str, Any]] = []
    for key, members in grouped.items():
        label = (members[0].get("surname_label") or key.title()).title()
        filtered_members = list(members)
        if person_status and person_status != "all":
            filtered_members = [member for member in filtered_members if _group_matches_person_status(member, person_status)]
        if review == "audit":
            filtered_members = [member for member in filtered_members if member["needs_review"]]
        elif review == "alinhados":
            filtered_members = [member for member in filtered_members if not member["needs_review"]]
        if not filtered_members:
            continue
        text_values: list[object] = [label]
        digit_values: list[object] = []
        for nucleus in filtered_members:
            text_values.extend([nucleus.get("label"), nucleus.get("automatic_label"), nucleus.get("system_label"), nucleus.get("member_names"), nucleus.get("address"), nucleus.get("review_reason")])
            for person in nucleus.get("people") or []:
                text_values.extend([person.get("nome"), person.get("codigo"), person.get("cpf"), person.get("endereco"), person.get("complemento"), person.get("bairro"), person.get("cidade"), person.get("uf")])
                digit_values.extend([person.get("codigo"), person.get("cpf"), person.get("cep"), person.get("numero")])
        if query_key or digits:
            if not _family_query_matches(query_key, digits, text_values, digit_values):
                continue
        if not query and len(filtered_members) < 2:
            continue
        filtered_members.sort(
            key=lambda item: (
                0 if item["needs_review"] else 1,
                normalize_match_name(item["label"]),
            )
        )
        clusters.append(
            {
                "label": label,
                "key": key,
                "nuclei": filtered_members,
                "nuclei_count": len(filtered_members),
                "people_count": len({person["id"] for nucleus in filtered_members for person in nucleus["people"]}),
                "review_count": sum(1 for nucleus in filtered_members if nucleus["needs_review"]),
                "household_names": " | ".join(nucleus["label"] for nucleus in filtered_members[:6]),
            }
        )
    clusters.sort(key=lambda item: (-item["nuclei_count"], normalize_match_name(item["label"])))
    return {
        "items": clusters,
        "total": len(clusters),
        "shown": len(clusters),
        "review": review,
        "q": query,
    }


def family_registry_dashboard(
    q: str = "",
    cep: str = "",
    section: str = "organized",
    mode: str = "all",
    review: str = "all",
    category: str = "all",
    household_kind: str = "all",
    person_status: str = "all",
) -> dict[str, Any]:
    section = normalize_query(section) or "organized"
    if section not in {"organized", "audit", "extended", "broad"}:
        section = "organized"
    audit = family_nuclei_dashboard(cep=cep, mode=mode, q=q, category=category, person_status=person_status)
    organized = organized_family_nuclei(q=q, cep=cep, review=review, household_kind=household_kind, person_status=person_status)
    broad = broad_family_candidates(q=q, cep=cep, review=review, person_status=person_status)
    extended = extended_family_clusters(
        organized_family_nuclei(q="", cep=cep, review="all", household_kind="all", person_status=person_status)["items"],
        q=q,
        review=review,
        person_status=person_status,
    )
    return {
        "section": section,
        "q": normalize_query(q),
        "cep": cep,
        "mode": mode,
        "review": review,
        "category": category,
        "household_kind": household_kind,
        "person_status": person_status,
        "audit": audit,
        "organized": organized,
        "broad": broad,
        "extended": extended,
        "summary": {
            "audit_groups": audit["summary"]["automatic_groups"] + audit["summary"]["hypothesis_groups"],
            "audit_people": audit["summary"]["automatic_people"] + audit["summary"]["hypothesis_people"],
            "organized_groups": organized["total"],
            "organized_people": organized["total_people"],
            "organized_review_groups": organized["review_groups"],
            "organized_family_groups": organized["family_groups"],
            "organized_single_groups": organized["single_groups"],
            "broad_groups": broad["total"],
            "broad_pending_groups": broad["pending_groups"],
            "extended_groups": extended["total"],
            "extended_nuclei": sum(cluster["nuclei_count"] for cluster in extended["items"]),
        },
    }


def _format_history(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "tipo_evento": row["tipo_evento"] or "",
        "data_evento": br_date(row["data_evento"]),
        "titulo": row["titulo"] or row["tipo_evento"] or "",
        "descricao": row["descricao"] or "",
        "origem": row["origem"] or "",
        "destino": row["destino"] or "",
        "criado_em": br_datetime(row["criado_em"]),
    }


def _format_contributor_brief(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "nome": row["nome"] or "",
        "tipo": (row["tipo"] or "").upper(),
        "documento": row["documento_principal"] or "",
        "documento_tipo": row["documento_tipo"] or "",
        "origem": row["origem"] or "",
        "qualidade": row["qualidade"] or "",
        "status": row["status"] or "",
    }


def _format_contribution_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "data": br_date(row["data_recebimento"]),
        "competencia": row["competencia"] or "",
        "valor_fmt": _money(row["valor"]),
        "status": row["status_operacional"] or "regular",
        "tipo": row["tipo_nome"] or "Sem tipo",
        "forma": row["forma_nome"] or "Sem forma",
        "origem": row["origem_nome"] or "",
    }


def _format_snapshot_contribution_row(row: Any) -> dict[str, Any]:
    return {
        "id": row.legacy_id,
        "data": br_date(row.received_at_raw),
        "competencia": row.competence or "",
        "valor_fmt": _money(row.amount),
        "status": row.operational_status or "regular",
        "tipo": row.contribution_type_name or "Sem tipo",
        "forma": row.receipt_method_name or "Sem forma",
        "origem": row.source_name or "",
    }


def _format_audit_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "acao": row["acao"] or "",
        "tabela": row["tabela"] or "",
        "registro_id": row["registro_id"] or "",
        "criado_em": br_datetime(row["criado_em"]),
    }


def people_import_type_label(value: object) -> str:
    labels = {
        "pessoas_membros": "Importacao inicial de membros",
        "pessoas_complementar_incremental": "Importacao incremental de pessoas",
    }
    return labels.get(str(value or ""), str(value or "Importacao de pessoas"))


def people_import_dashboard(limit: int = 12) -> dict[str, Any]:
    with connect_legacy() as conn:
        total_people = int(scalar(conn, "SELECT COUNT(*) FROM pessoas WHERE ativo = 1") or 0)
        open_pendencies = int(
            scalar(
                conn,
                """
                SELECT COUNT(*)
                  FROM import_pendencias ip
                  JOIN import_lotes il ON il.id = ip.lote_id
                 WHERE ip.resolvido = 0
                   AND il.tipo_importacao IN ('pessoas_membros', 'pessoas_complementar_incremental')
                """,
            )
            or 0
        )
        total_lots = int(
            scalar(
                conn,
                """
                SELECT COUNT(*)
                  FROM import_lotes
                 WHERE tipo_importacao IN ('pessoas_membros', 'pessoas_complementar_incremental')
                """,
            )
            or 0
        )
        rows = conn.execute(
            """
            SELECT
                il.*,
                (
                    SELECT COUNT(*)
                      FROM import_pendencias ip
                     WHERE ip.lote_id = il.id AND ip.resolvido = 0
                ) AS pendencias_abertas,
                (
                    SELECT COUNT(*)
                      FROM import_linhas linha
                      JOIN pessoas p ON p.id = linha.registro_id
                     WHERE linha.lote_id = il.id
                       AND linha.registro_tipo = 'pessoa'
                       AND p.ativo = 1
                ) AS pessoas_ativas,
                (
                    SELECT COUNT(*)
                      FROM import_linhas linha
                      JOIN pessoas p ON p.id = linha.registro_id
                     WHERE linha.lote_id = il.id
                       AND linha.registro_tipo = 'pessoa'
                       AND p.ativo = 1
                       AND p.nome = 'Nome nao informado'
                ) AS pessoas_sem_nome
              FROM import_lotes il
             WHERE il.tipo_importacao IN ('pessoas_membros', 'pessoas_complementar_incremental')
             ORDER BY il.criado_em DESC, il.id DESC
             LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return {
        "total_people": total_people,
        "open_pendencies": open_pendencies,
        "total_lots": total_lots,
        "shown": len(rows),
        "lots": [_format_people_import_lot(row) for row in rows],
    }


def get_people_import_lot_detail(lot_id: int, line_limit: int = 250, pending_limit: int = 100) -> dict[str, Any] | None:
    with connect_legacy() as conn:
        lot = conn.execute(
            """
            SELECT
                il.*,
                (
                    SELECT COUNT(*)
                      FROM import_pendencias ip
                     WHERE ip.lote_id = il.id AND ip.resolvido = 0
                ) AS pendencias_abertas
              FROM import_lotes il
             WHERE il.id = ?
               AND il.tipo_importacao IN ('pessoas_membros', 'pessoas_complementar_incremental')
            """,
            (lot_id,),
        ).fetchone()
        if lot is None:
            return None
        status_rows = conn.execute(
            """
            SELECT COALESCE(p.status, 'sem ficha') AS status,
                   COUNT(*) AS quantidade
              FROM import_linhas il
              LEFT JOIN pessoas p ON p.id = il.registro_id
             WHERE il.lote_id = ? AND il.registro_tipo = 'pessoa'
             GROUP BY COALESCE(p.status, 'sem ficha')
             ORDER BY quantidade DESC, status
            """,
            (lot_id,),
        ).fetchall()
        mapping_rows = []
        if table_exists(conn, "import_mapeamentos"):
            mapping_rows = conn.execute(
                """
                SELECT coluna_origem, campo_destino, acao
                  FROM import_mapeamentos
                 WHERE lote_id = ?
                 ORDER BY CASE WHEN acao = 'revisar_depois' THEN 0 ELSE 1 END, coluna_origem
                 LIMIT 160
                """,
                (lot_id,),
            ).fetchall()
        pending_sql = """
            SELECT ip.*, il.numero_linha, p.nome AS pessoa_nome
              FROM import_pendencias ip
              LEFT JOIN import_linhas il ON il.id = ip.linha_id
              LEFT JOIN pessoas p ON p.id = il.registro_id
             WHERE ip.lote_id = ?
             ORDER BY ip.resolvido ASC, ip.severidade DESC, COALESCE(il.numero_linha, 0) ASC, ip.id ASC
        """
        if int(pending_limit or 0) > 0:
            pending_rows = conn.execute(
                pending_sql + "\n LIMIT ?",
                (lot_id, int(pending_limit or 0)),
            ).fetchall()
        else:
            pending_rows = conn.execute(pending_sql, (lot_id,)).fetchall()
        line_rows = conn.execute(
            """
            SELECT
                il.id,
                il.numero_linha,
                il.status,
                il.dados_originais_json,
                il.dados_normalizados_json,
                p.id AS pessoa_id,
                p.nome AS pessoa_nome,
                p.cpf,
                p.status AS pessoa_status,
                p.ativo AS pessoa_ativa
              FROM import_linhas il
              LEFT JOIN pessoas p ON p.id = il.registro_id
             WHERE il.lote_id = ?
             ORDER BY il.numero_linha
             LIMIT ?
            """,
            (lot_id, line_limit),
        ).fetchall()
        active_people = int(
            scalar(
                conn,
                """
                SELECT COUNT(*)
                  FROM import_linhas il
                  JOIN pessoas p ON p.id = il.registro_id
                 WHERE il.lote_id = ? AND p.ativo = 1
                """,
                (lot_id,),
            )
            or 0
        )
        without_name = int(
            scalar(
                conn,
                """
                SELECT COUNT(*)
                  FROM import_linhas il
                  JOIN pessoas p ON p.id = il.registro_id
                 WHERE il.lote_id = ? AND p.ativo = 1 AND p.nome = 'Nome nao informado'
                """,
                (lot_id,),
            )
            or 0
        )
    review_mappings = sum(1 for row in mapping_rows if str(row["acao"]) == "revisar_depois")
    return {
        "lot": _format_people_import_lot(lot),
        "cards": {
            "total_lines": int(lot["total_linhas"] or 0),
            "active_people": active_people,
            "open_pendencies": int(lot["pendencias_abertas"] or 0),
            "without_name": without_name,
            "review_mappings": review_mappings,
        },
        "status_rows": [
            {"status": format_status(row["status"]), "status_raw": row["status"], "count": int(row["quantidade"] or 0)}
            for row in status_rows
        ],
        "mapping_rows": [
            {
                "coluna_origem": row["coluna_origem"] or "",
                "campo_destino": row["campo_destino"] or "",
                "acao": row["acao"] or "",
                "acao_label": "Revisar" if row["acao"] == "revisar_depois" else "Mapeado",
            }
            for row in mapping_rows
        ],
        "pending_rows": [_format_people_import_pending(row) for row in pending_rows],
        "line_rows": [_format_people_import_line(row) for row in line_rows],
        "line_limit": line_limit,
    }


def _format_people_import_lot(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "type": row["tipo_importacao"] or "",
        "type_label": people_import_type_label(row["tipo_importacao"]),
        "arquivo_nome": row["arquivo_nome"] or "",
        "status": row["status"] or "",
        "total_linhas": int(row["total_linhas"] or 0),
        "linhas_importadas": int(row["linhas_importadas"] or 0),
        "linhas_ignoradas": int(row["linhas_ignoradas"] or 0),
        "linhas_com_erro": int(row["linhas_com_erro"] or 0),
        "pendencias_abertas": int(row["pendencias_abertas"] or 0) if "pendencias_abertas" in row.keys() else 0,
        "pessoas_ativas": int(row["pessoas_ativas"] or 0) if "pessoas_ativas" in row.keys() else 0,
        "pessoas_sem_nome": int(row["pessoas_sem_nome"] or 0) if "pessoas_sem_nome" in row.keys() else 0,
        "criado_em": br_datetime(row["criado_em"]),
        "confirmado_em": br_datetime(row["confirmado_em"]),
        "detail_url": f"/people/imports/{row['id']}/",
    }


def _format_people_import_pending(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "linha": row["numero_linha"] or "",
        "severidade": row["severidade"] or "",
        "tipo": row["tipo"] or "",
        "descricao": row["descricao"] or "",
        "acao_sugerida": row["acao_sugerida"] or "",
        "resolvido": bool(row["resolvido"]),
        "status": "Resolvida" if row["resolvido"] else "Aberta",
        "pessoa_nome": row["pessoa_nome"] or "",
    }


def _json_dict(value: object) -> dict[str, Any]:
    try:
        loaded = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _format_people_import_line(row: sqlite3.Row) -> dict[str, Any]:
    original = _json_dict(row["dados_originais_json"])
    normalized = _json_dict(row["dados_normalizados_json"])
    original_name = normalize_query(
        original.get("Nome completo")
        or original.get("Nome Completo")
        or original.get("nome")
        or original.get("Nome")
        or ""
    )
    return {
        "id": row["id"],
        "linha": row["numero_linha"] or "",
        "status": row["status"] or "",
        "original_name": original_name or "-",
        "normalized_action": normalized.get("acao", "") or "-",
        "person_id": row["pessoa_id"] or "",
        "person_name": row["pessoa_nome"] or "Sem ficha ativa",
        "person_cpf": format_cpf(row["cpf"]),
        "person_status": format_status(row["pessoa_status"]),
        "person_active": bool(row["pessoa_ativa"]),
    }


def list_competencias() -> list[str]:
    with connect_legacy() as conn:
        return [
            row["competencia"]
            for row in conn.execute(
                """
                SELECT DISTINCT competencia, MAX(COALESCE(competencia_ordem, 0)) AS ordem
                  FROM contribuicoes
                 WHERE ativo = 1 AND COALESCE(competencia, '') <> ''
                 GROUP BY competencia
                 ORDER BY ordem DESC, competencia DESC
                """
            ).fetchall()
            if row["competencia"]
        ]


def list_contributions(q: str = "", competencia: str = "", status: str = "", limit: int | None = None) -> dict[str, Any]:
    q = (q or "").strip()
    competencia = (competencia or "").strip()
    status = (status or "").strip()
    clauses = ["co.ativo = 1"]
    params: list[Any] = []
    if competencia:
        clauses.append("COALESCE(co.competencia, '') = ?")
        params.append(competencia)
    if status:
        clauses.append("COALESCE(co.status_operacional, '') = ?")
        params.append(status)
    if q:
        like = f"%{normalize_match_name(q)}%"
        digits = "".join(ch for ch in q if ch.isdigit())
        clauses.append(
            """
            (
                NORMALIZE_MATCH(COALESCE(p.nome, '')) LIKE ?
                OR NORMALIZE_MATCH(COALESCE(c.nome, '')) LIKE ?
                OR COALESCE(c.documento_principal, '') LIKE ?
                OR COALESCE(p.cpf, '') LIKE ?
                OR NORMALIZE_MATCH(COALESCE(co.observacoes, '')) LIKE ?
            )
            """
        )
        params.extend([like, like, f"%{digits or q}%", f"%{digits or q}%", like])
    where = " AND ".join(clauses)
    with connect_legacy() as conn:
        total = int(
            scalar(
                conn,
                f"""
                SELECT COUNT(*)
                  FROM contribuicoes co
                  LEFT JOIN pessoas p ON p.id = co.pessoa_id
                  LEFT JOIN contribuintes c ON c.id = co.contribuinte_id
                 WHERE {where}
                """,
                tuple(params),
            )
            or 0
        )
        total_value = float(
            scalar(
                conn,
                f"""
                SELECT COALESCE(SUM(co.valor), 0)
                  FROM contribuicoes co
                  LEFT JOIN pessoas p ON p.id = co.pessoa_id
                  LEFT JOIN contribuintes c ON c.id = co.contribuinte_id
                 WHERE {where}
                """,
                tuple(params),
            )
            or 0
        )
        limit_value = moneyless_int(limit) if limit is not None else 0
        limit_clause = "LIMIT ?" if limit_value > 0 else ""
        row_params: tuple[Any, ...] = (*params, limit_value) if limit_value > 0 else tuple(params)
        rows = conn.execute(
            f"""
            SELECT co.id, co.data_recebimento, co.competencia, co.valor,
                   COALESCE(co.competencia_ordem, 0) AS competencia_ordem,
                   COALESCE(co.status_operacional, '') AS status_operacional,
                   COALESCE(p.nome, c.nome, 'Contribuinte nao vinculado') AS contribuinte_nome,
                   p.nome AS pessoa_nome,
                   c.nome AS contribuinte_nome_raw,
                   c.documento_principal AS contribuinte_documento,
                   p.id AS pessoa_id,
                   p.status AS pessoa_status,
                   c.id AS contribuinte_id,
                   COALESCE(t.nome, '') AS tipo_nome,
                   COALESCE(f.nome, '') AS forma_nome
              FROM contribuicoes co
              LEFT JOIN pessoas p ON p.id = co.pessoa_id
              LEFT JOIN contribuintes c ON c.id = co.contribuinte_id
              LEFT JOIN tipos_contribuicao t ON t.id = co.tipo_contribuicao_id
              LEFT JOIN formas_recebimento f ON f.id = co.forma_recebimento_id
             WHERE {where}
             ORDER BY contribuinte_nome COLLATE NOCASE ASC,
                      COALESCE(co.data_recebimento, '') ASC,
                      COALESCE(co.competencia_ordem, 0) ASC,
                      co.id ASC
             {limit_clause}
            """,
            row_params,
        ).fetchall()
        status_options = [
            {"value": row["status_operacional"], "count": int(row["total"] or 0)}
            for row in conn.execute(
                """
                SELECT COALESCE(status_operacional, '') AS status_operacional, COUNT(*) AS total
                  FROM contribuicoes
                 WHERE ativo = 1
                 GROUP BY COALESCE(status_operacional, '')
                 ORDER BY total DESC
                """
            ).fetchall()
        ]
    items = []
    for row in rows:
        identity = contribution_report_identity(row["pessoa_nome"], row["contribuinte_nome_raw"], row["contribuinte_documento"])
        items.append(
            {
                "id": row["id"],
                "detail_url": f"/contributions/{row['id']}/",
                "data": br_date(row["data_recebimento"]),
                "data_raw": row["data_recebimento"] or "",
                "competencia": row["competencia"] or "",
                "competencia_ordem": moneyless_int(row["competencia_ordem"]),
                "nome": identity["name"] or "Contribuinte nao vinculado",
                "nome_original": row["contribuinte_nome"] or "",
                "sort_key": identity["sort_key"],
                "group_kind": identity["group_kind"],
                "documento": identity["document"],
                "sigla": status_sigla(row["pessoa_status"], bool(row["pessoa_id"])),
                "tipo": row["tipo_nome"] or "Sem tipo",
                "forma": row["forma_nome"] or "Sem forma",
                "status": row["status_operacional"] or "regular",
                "valor": float(row["valor"] or 0),
                "valor_fmt": _money(row["valor"]),
            }
        )
    items.sort(
        key=lambda item: (
            0 if item["group_kind"] == "nome" else 1,
            str(item["sort_key"]),
            str(item["data_raw"]),
            moneyless_int(item["competencia_ordem"]),
            moneyless_int(item["id"]),
        )
    )
    if limit_value > 0:
        items = items[:limit_value]
    return {
        "items": items,
        "total": total,
        "shown": len(items),
        "total_value": total_value,
        "total_value_fmt": _money(total_value),
        "q": q,
        "competencia": competencia,
        "status": status,
        "competencias": list_competencias(),
        "status_options": status_options,
        "limit": limit_value or total,
    }


def _contribution_catalog_options(
    conn: sqlite3.Connection,
    organization_id: int,
    selected_type_id: int = 0,
    selected_form_id: int = 0,
    selected_campaign_id: int = 0,
) -> dict[str, Any]:
    type_rows = conn.execute(
        """
        SELECT id, codigo, nome
          FROM tipos_contribuicao
         WHERE organizacao_id = ? AND ativo = 1
         ORDER BY CASE WHEN codigo = 'DIZIMO' THEN 0 ELSE 1 END, nome COLLATE NOCASE
        """,
        (organization_id,),
    ).fetchall()
    form_rows = conn.execute(
        """
        SELECT id, codigo, nome
          FROM formas_recebimento
         WHERE organizacao_id = ? AND ativo = 1
         ORDER BY nome COLLATE NOCASE
        """,
        (organization_id,),
    ).fetchall()
    campaign_rows = conn.execute(
        """
        SELECT id, nome, status
          FROM campanhas
         WHERE organizacao_id = ? AND COALESCE(status, 'ativa') = 'ativa'
         ORDER BY nome COLLATE NOCASE
        """,
        (organization_id,),
    ).fetchall()

    def traceability_value_for_receiving(row: sqlite3.Row) -> str:
        code = normalize_match_name(row["codigo"] or row["nome"])
        if "DINHEIRO" in code:
            return "dinheiro"
        if "PIX" in code:
            return "pix"
        if "TRANSFERENCIA" in code or "TED" in code or "DOC" in code:
            return "transferencia"
        if "CARTAO" in code:
            return "cartao_credito"
        if "CHEQUE" in code:
            return "cheque"
        if "DEPOSITO" in code:
            return "deposito"
        return ""

    return {
        "type_options": [
            {
                "id": moneyless_int(row["id"]),
                "codigo": row["codigo"] or "",
                "nome": row["nome"] or "",
                "selected": moneyless_int(row["id"]) == moneyless_int(selected_type_id),
            }
            for row in type_rows
        ],
        "receiving_options": [
            {
                "id": moneyless_int(row["id"]),
                "codigo": row["codigo"] or "",
                "nome": row["nome"] or "",
                "traceability_value": traceability_value_for_receiving(row),
                "selected": moneyless_int(row["id"]) == moneyless_int(selected_form_id),
            }
            for row in form_rows
        ],
        "campaign_options": [
            {
                "id": moneyless_int(row["id"]),
                "nome": row["nome"] or "",
                "status": row["status"] or "",
                "selected": moneyless_int(row["id"]) == moneyless_int(selected_campaign_id),
            }
            for row in campaign_rows
        ],
        "status_options": [
            {"value": value, "label": label}
            for value, label in CONTRIBUTION_STATUS_LABELS.items()
        ],
    }


def _manual_people_options(conn: sqlite3.Connection, organization_id: int, limit: int = 5000) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, codigo_interno, nome, cpf, status, telefone_principal, whatsapp_principal
          FROM pessoas
         WHERE organizacao_id = ? AND ativo = 1
         ORDER BY nome COLLATE NOCASE ASC, id ASC
         LIMIT ?
        """,
        (organization_id, limit),
    ).fetchall()
    return [
        {
            "id": moneyless_int(row["id"]),
            "nome": row["nome"] or "",
            "codigo": row["codigo_interno"] or "",
            "cpf": format_cpf(row["cpf"]),
            "status": format_status(row["status"]),
            "sigla": status_sigla(row["status"], True),
            "telefone": row["telefone_principal"] or "",
            "whatsapp": row["whatsapp_principal"] or "",
        }
        for row in rows
    ]


def _manual_contributor_options(conn: sqlite3.Connection, organization_id: int, limit: int = 5000) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, nome, documento_principal, tipo, pessoa_id
          FROM contribuintes
         WHERE organizacao_id = ? AND ativo = 1
         ORDER BY nome COLLATE NOCASE ASC, id ASC
         LIMIT ?
        """,
        (organization_id, limit),
    ).fetchall()
    return [
        {
            "id": moneyless_int(row["id"]),
            "nome": row["nome"] or "",
            "documento": row["documento_principal"] or "",
            "tipo": (row["tipo"] or "").upper(),
            "pessoa_id": moneyless_int(row["pessoa_id"]),
        }
        for row in rows
    ]


def _digits_only(value: object) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _address_lookup_key(value: object) -> str:
    normalized = normalize_match_name(value)
    return "".join(ch for ch in normalized if ch.isalnum())


def _address_lookup_tokens(value: object) -> set[str]:
    return {
        token
        for token in normalize_match_name(value).split()
        if len(token) >= 3 and token not in {"RUA", "AV", "AVENIDA", "APT", "APTO", "BLOCO", "CASA"}
    }


def lookup_envelope_people(phone: str = "", address: str = "", limit: int = 8) -> dict[str, Any]:
    limit = max(1, min(moneyless_int(limit) or 8, 12))
    phone_digits = _digits_only(phone)
    address_text = normalize_query(address)
    address_key = _address_lookup_key(address_text)
    address_tokens = _address_lookup_tokens(address_text)
    address_cep = _digits_only(address_text)

    with connect_legacy() as conn:
        phone_matches: list[dict[str, Any]] = []
        address_matches: list[dict[str, Any]] = []

        if len(phone_digits) >= 8:
            like = f"%{phone_digits[-8:]}%"
            rows = conn.execute(
                """
                SELECT p.id, p.codigo_interno, p.nome, p.cpf, p.status,
                       p.telefone_principal, p.whatsapp_principal,
                       GROUP_CONCAT(pc.valor, '||') AS contatos_texto
                  FROM pessoas p
                  LEFT JOIN pessoa_contatos pc
                    ON pc.pessoa_id = p.id
                   AND pc.tipo IN ('telefone', 'celular', 'whatsapp')
                   AND COALESCE(pc.valor, '') <> ''
                 WHERE p.ativo = 1
                   AND (
                        COALESCE(p.telefone_principal, '') LIKE ?
                        OR COALESCE(p.whatsapp_principal, '') LIKE ?
                        OR COALESCE(pc.valor, '') LIKE ?
                   )
                 GROUP BY p.id, p.codigo_interno, p.nome, p.cpf, p.status, p.telefone_principal, p.whatsapp_principal
                 ORDER BY p.nome COLLATE NOCASE ASC, p.id ASC
                 LIMIT ?
                """,
                (like, like, like, limit * 6),
            ).fetchall()
            ranked: list[tuple[int, dict[str, Any]]] = []
            for row in rows:
                contact_values = {
                    normalize_query(row["telefone_principal"]),
                    normalize_query(row["whatsapp_principal"]),
                }
                contact_values.update(
                    normalize_query(item)
                    for item in str(row["contatos_texto"] or "").split("||")
                    if normalize_query(item)
                )
                best_score = 0
                matched_value = ""
                for contact in contact_values:
                    candidate_digits = _digits_only(contact)
                    if not candidate_digits:
                        continue
                    if candidate_digits == phone_digits:
                        best_score = max(best_score, 30)
                        matched_value = contact
                    elif len(candidate_digits) >= 8 and phone_digits[-8:] == candidate_digits[-8:]:
                        if best_score < 20:
                            best_score = 20
                            matched_value = contact
                if not best_score:
                    continue
                person = {
                    "id": moneyless_int(row["id"]),
                    "nome": row["nome"] or "",
                    "codigo": row["codigo_interno"] or "",
                    "cpf": format_cpf(row["cpf"]),
                    "sigla": status_sigla(row["status"], True),
                }
                ranked.append(
                    (
                        best_score,
                        {
                            "id": person["id"],
                            "label": _person_option_label(person),
                            "nome": person["nome"],
                            "codigo": person["codigo"],
                            "cpf": person["cpf"],
                            "sigla": person["sigla"],
                            "matched_value": matched_value,
                            "source": "Telefone exato" if best_score >= 30 else "Final do telefone",
                        },
                    )
                )
            phone_matches = [item for _score, item in sorted(ranked, key=lambda pair: (-pair[0], pair[1]["nome"], pair[1]["id"]))[:limit]]
            contributor_rows = conn.execute(
                """
                SELECT c.id, c.nome, c.documento_principal, c.tipo,
                       GROUP_CONCAT(ci.valor, '||') AS identificadores
                  FROM contribuintes c
                  JOIN contribuintes_identificadores ci
                    ON ci.contribuinte_id = c.id
                   AND ci.ativo = 1
                   AND ci.tipo IN ('telefone', 'celular', 'whatsapp')
                 WHERE c.ativo = 1
                   AND COALESCE(c.pessoa_id, 0) = 0
                 GROUP BY c.id, c.nome, c.documento_principal, c.tipo
                 ORDER BY c.nome COLLATE NOCASE ASC, c.id ASC
                """
            ).fetchall()
            ranked_contributors: list[tuple[int, dict[str, Any]]] = []
            for row in contributor_rows:
                best_score = 0
                matched_value = ""
                for contact in str(row["identificadores"] or "").split("||"):
                    candidate_digits = _digits_only(contact)
                    if not candidate_digits:
                        continue
                    if candidate_digits == phone_digits:
                        best_score = max(best_score, 30)
                        matched_value = contact
                    elif len(candidate_digits) >= 8 and phone_digits[-8:] == candidate_digits[-8:]:
                        if best_score < 20:
                            best_score = 20
                            matched_value = contact
                if not best_score:
                    continue
                contributor = {
                    "id": moneyless_int(row["id"]),
                    "nome": row["nome"] or "",
                    "documento": row["documento_principal"] or "",
                    "tipo": (row["tipo"] or "").upper(),
                }
                ranked_contributors.append(
                    (
                        best_score,
                        {
                            "id": contributor["id"],
                            "label": _contributor_option_label(contributor),
                            "nome": contributor["nome"],
                            "codigo": "",
                            "cpf": format_document(contributor["documento"]),
                            "sigla": "NF",
                            "matched_value": matched_value,
                            "source": "Contribuinte sem ficha por telefone",
                        },
                    )
                )
            phone_matches.extend(
                item
                for _score, item in sorted(ranked_contributors, key=lambda pair: (-pair[0], pair[1]["nome"], pair[1]["id"]))[:limit]
            )
            phone_matches = sorted(phone_matches, key=lambda item: (item["source"], item["nome"], item["id"]))[:limit]

        if address_key or address_tokens:
            rows = conn.execute(
                """
                SELECT p.id, p.codigo_interno, p.nome, p.cpf, p.status,
                       e.cep, e.logradouro, e.numero, e.complemento, e.bairro, e.cidade, e.uf
                  FROM pessoas p
                  JOIN pessoa_enderecos e ON e.pessoa_id = p.id
                 WHERE p.ativo = 1
                 ORDER BY p.nome COLLATE NOCASE ASC, p.id ASC
                """
            ).fetchall()
            ranked_addresses: list[tuple[int, dict[str, Any]]] = []
            for row in rows:
                current_address = _format_address_line(row)
                current_key = _address_lookup_key(current_address)
                current_tokens = _address_lookup_tokens(current_address)
                current_cep = _digits_only(row["cep"])
                score = 0
                if address_key and current_key and (address_key in current_key or current_key in address_key):
                    score = max(score, 30)
                common_tokens = len(address_tokens & current_tokens)
                if common_tokens >= 4:
                    score = max(score, 26)
                elif common_tokens >= 3:
                    score = max(score, 20)
                elif common_tokens >= 2:
                    score = max(score, 12)
                if address_cep and current_cep and (address_cep.endswith(current_cep) or current_cep.endswith(address_cep)):
                    score = max(score, 18)
                if not score:
                    continue
                person = {
                    "id": moneyless_int(row["id"]),
                    "nome": row["nome"] or "",
                    "codigo": row["codigo_interno"] or "",
                    "cpf": format_cpf(row["cpf"]),
                    "sigla": status_sigla(row["status"], True),
                }
                ranked_addresses.append(
                    (
                        score,
                        {
                            "id": person["id"],
                            "label": _person_option_label(person),
                            "nome": person["nome"],
                            "codigo": person["codigo"],
                            "cpf": person["cpf"],
                            "sigla": person["sigla"],
                            "matched_value": current_address,
                            "source": "Endereco muito proximo" if score >= 26 else "Endereco semelhante",
                        },
                    )
                )
            address_matches = [
                item
                for _score, item in sorted(ranked_addresses, key=lambda pair: (-pair[0], pair[1]["nome"], pair[1]["id"]))[:limit]
            ]
            contributor_rows = conn.execute(
                """
                SELECT c.id, c.nome, c.documento_principal, c.tipo, ci.valor
                  FROM contribuintes c
                  JOIN contribuintes_identificadores ci
                    ON ci.contribuinte_id = c.id
                   AND ci.ativo = 1
                   AND ci.tipo = 'endereco'
                 WHERE c.ativo = 1
                   AND COALESCE(c.pessoa_id, 0) = 0
                 ORDER BY c.nome COLLATE NOCASE ASC, c.id ASC
                """
            ).fetchall()
            ranked_contributor_addresses: list[tuple[int, dict[str, Any]]] = []
            for row in contributor_rows:
                current_address = normalize_query(row["valor"])
                current_key = _address_lookup_key(current_address)
                current_tokens = _address_lookup_tokens(current_address)
                score = 0
                if address_key and current_key and (address_key in current_key or current_key in address_key):
                    score = max(score, 30)
                common_tokens = len(address_tokens & current_tokens)
                if common_tokens >= 4:
                    score = max(score, 26)
                elif common_tokens >= 3:
                    score = max(score, 20)
                elif common_tokens >= 2:
                    score = max(score, 12)
                if not score:
                    continue
                contributor = {
                    "id": moneyless_int(row["id"]),
                    "nome": row["nome"] or "",
                    "documento": row["documento_principal"] or "",
                    "tipo": (row["tipo"] or "").upper(),
                }
                ranked_contributor_addresses.append(
                    (
                        score,
                        {
                            "id": contributor["id"],
                            "label": _contributor_option_label(contributor),
                            "nome": contributor["nome"],
                            "codigo": "",
                            "cpf": format_document(contributor["documento"]),
                            "sigla": "NF",
                            "matched_value": current_address,
                            "source": "Contribuinte sem ficha por endereco",
                        },
                    )
                )
            address_matches.extend(
                item
                for _score, item in sorted(ranked_contributor_addresses, key=lambda pair: (-pair[0], pair[1]["nome"], pair[1]["id"]))[:limit]
            )
            address_matches = sorted(address_matches, key=lambda item: (item["source"], item["nome"], item["id"]))[:limit]

    return {"phone_matches": phone_matches, "address_matches": address_matches}


def _person_option_label(person: dict[str, Any]) -> str:
    cpf = f" · CPF {person['cpf']}" if person.get("cpf") else ""
    code = f" · Ficha {person['codigo']}" if person.get("codigo") else ""
    search_hint = ""
    if any(ord(ch) > 127 for ch in str(person.get("nome") or "")):
        search_hint = f" · busca {normalize_match_name(person.get('nome'))}"
    return f"Pessoa #{person['id']} · {person['nome']} · {person['sigla']}{code}{cpf}{search_hint}"


def _contributor_option_label(contributor: dict[str, Any]) -> str:
    document = f" · {format_document(contributor.get('documento'))}" if contributor.get("documento") else ""
    kind = f" · {contributor['tipo']}" if contributor.get("tipo") else ""
    search_hint = ""
    if any(ord(ch) > 127 for ch in str(contributor.get("nome") or "")):
        search_hint = f" · busca {normalize_match_name(contributor.get('nome'))}"
    return f"Contribuinte #{contributor['id']} · {contributor['nome']}{kind}{document}{search_hint}"


def _participant_options(people: list[dict[str, Any]], contributors: list[dict[str, Any]]) -> list[dict[str, str]]:
    options: list[dict[str, str]] = []
    options.extend(
        {
            "value": _person_option_label(person),
            "label": f"Pessoa do rol · {person['nome']}",
            "kind": "pessoa",
        }
        for person in people
    )
    options.extend(
        {
            "value": _contributor_option_label(contributor),
            "label": f"Contribuinte auxiliar · {contributor['nome']}",
            "kind": "contribuinte",
        }
        for contributor in contributors
        if contributor.get("nome")
    )
    return options


def _empty_envelope_line_defaults(count: int = 10) -> list[dict[str, Any]]:
    return [
        {
            "index": index,
            "participant_ref": "",
            "document": "",
            "type_id": 0,
            "campaign_id": 0,
            "value": "",
            "observations": "",
        }
        for index in range(1, count + 1)
    ]


def _clean_optional_text(value: object) -> str:
    text = normalize_query(value)
    return "" if text.lower() in {"none", "null"} else text


def manual_contribution_context() -> dict[str, Any]:
    with connect_legacy() as conn:
        organization_id = int(scalar(conn, "SELECT id FROM organizacoes ORDER BY id LIMIT 1") or 1)
        catalogs = _contribution_catalog_options(conn, organization_id)
        people_options = _manual_people_options(conn, organization_id)
        contributor_options = _manual_contributor_options(conn, organization_id)
        return {
            "organization_id": organization_id,
            "today": date.today().isoformat(),
            "line_range": range(1, 9),
            "people_options": people_options,
            "contributor_options": contributor_options,
            "participant_options": _participant_options(people_options, contributor_options),
            "type_options": catalogs["type_options"],
            "campaign_options": catalogs["campaign_options"],
            "receiving_options": catalogs["receiving_options"],
            "status_options": catalogs["status_options"],
        }


def envelope_contribution_context(person_id: int = 0) -> dict[str, Any]:
    context = manual_contribution_context()
    context["default_competencia_mes"] = date.today().strftime("%Y-%m")
    context["line_range"] = range(1, 11)
    context["default_type_id"] = _default_type_id(context["type_options"])
    context["default_campaign_id"] = 0
    context["default_form_id"] = 0
    context["default_status"] = "regular"
    context["default_origin"] = "Envelope digitalizado"
    context["default_justification"] = "Envelope conferido manualmente; imagem anexada para auditoria."
    context["default_lot_name"] = ""
    context["default_total"] = ""
    context["selected_primary_ref"] = ""
    context["selected_person_ref"] = ""
    context["selected_contributor_ref"] = ""
    context["default_nome_informado"] = ""
    context["default_telefone_informado"] = ""
    context["default_endereco_informado"] = ""
    context["default_observacoes"] = ""
    context["line_defaults"] = _empty_envelope_line_defaults(10)
    context["traceability_form_options"] = [
        {"value": "", "label": "Nao informada"},
        {"value": "dinheiro", "label": "Dinheiro"},
        {"value": "cheque", "label": "Cheque"},
        {"value": "cartao_credito", "label": "Cartao de credito"},
        {"value": "cartao_debito", "label": "Cartao de debito"},
        {"value": "pix", "label": "PIX"},
        {"value": "transferencia", "label": "Transferencia"},
        {"value": "deposito", "label": "Deposito"},
    ]
    context["traceability_status_options"] = [
        {"value": "pendente", "label": "Pendente"},
        {"value": "conciliado", "label": "Conciliado"},
        {"value": "divergente", "label": "Divergente"},
        {"value": "ignorado", "label": "Ignorado"},
    ]
    context["traceability"] = {
        "forma_identificada": "",
        "banco_operadora": "",
        "numero_cheque": "",
        "numero_operacao": "",
        "nsu_tid": "",
        "ultimos_digitos_cartao": "",
        "data_operacao": "",
        "valor_operacao": "",
        "status_conciliacao": "pendente",
        "observacoes": "",
    }
    if person_id:
        for person in context["people_options"]:
            if moneyless_int(person["id"]) == moneyless_int(person_id):
                context["selected_primary_ref"] = _person_option_label(person)
                context["selected_person_ref"] = _person_option_label(person)
                break
    return context


def _envelope_participant_ref_from_item(item: sqlite3.Row) -> str:
    person_id = moneyless_int(item["pessoa_id"])
    if person_id:
        person = {
            "id": person_id,
            "nome": item["pessoa_nome"] or "",
            "codigo": item["pessoa_codigo"] or "",
            "cpf": format_cpf(item["pessoa_cpf"]),
            "sigla": status_sigla(item["pessoa_status"], True),
        }
        return _person_option_label(person)
    contributor_id = moneyless_int(item["contribuinte_id"])
    if contributor_id:
        contributor = {
            "id": contributor_id,
            "nome": item["contribuinte_nome"] or "",
            "documento": item["contribuinte_documento"] or "",
            "tipo": (item["contribuinte_tipo"] or "").upper(),
        }
        return _contributor_option_label(contributor)
    return ""


def launched_envelope_edit_context(envelope_id: int) -> dict[str, Any] | None:
    envelope_id = moneyless_int(envelope_id)
    with connect_legacy() as conn:
        if not _envelope_tables_ready(conn):
            return None
        row = conn.execute(
            """
            SELECT e.*, l.nome AS lote_nome, l.caminho_pasta,
                   p.nome AS pessoa_nome, p.codigo_interno AS pessoa_codigo, p.cpf AS pessoa_cpf, p.status AS pessoa_status,
                   ct.nome AS contribuinte_nome, ct.documento_principal AS contribuinte_documento, ct.tipo AS contribuinte_tipo
              FROM envelopes e
              JOIN envelope_lotes l ON l.id = e.lote_id
              LEFT JOIN pessoas p ON p.id = e.pessoa_id
              LEFT JOIN contribuintes ct ON ct.id = e.contribuinte_id
             WHERE e.id = ? AND e.ativo = 1 AND e.status = 'lancado'
             LIMIT 1
            """,
            (envelope_id,),
        ).fetchone()
        if row is None:
            return None
        item_rows = conn.execute(
            """
            SELECT ei.*, c.status_operacional,
                   p.nome AS pessoa_nome, p.codigo_interno AS pessoa_codigo, p.cpf AS pessoa_cpf, p.status AS pessoa_status,
                   ct.nome AS contribuinte_nome, ct.documento_principal AS contribuinte_documento, ct.tipo AS contribuinte_tipo
              FROM envelope_itens ei
              LEFT JOIN contribuicoes c ON c.id = ei.contribuicao_id
              LEFT JOIN pessoas p ON p.id = ei.pessoa_id
              LEFT JOIN contribuintes ct ON ct.id = ei.contribuinte_id
             WHERE ei.envelope_id = ? AND ei.ativo = 1
             ORDER BY ei.id
            """,
            (envelope_id,),
        ).fetchall()

    context = envelope_contribution_context()
    suffix = Path(str(row["caminho_imagem"] or "")).suffix.lower()
    line_defaults = _empty_envelope_line_defaults(10)
    for position, item in enumerate(item_rows[:10]):
        line_defaults[position] = {
            "index": position + 1,
            "participant_ref": _envelope_participant_ref_from_item(item),
            "document": format_document(item["contribuinte_documento"]) if item["contribuinte_documento"] else "",
            "type_id": moneyless_int(item["tipo_contribuicao_id"]),
            "campaign_id": moneyless_int(item["campanha_id"]),
            "value": _money(item["valor"]).replace("R$ ", ""),
            "observations": item["observacoes"] or "",
        }
    main_person_ref = ""
    if moneyless_int(row["pessoa_id"]):
        main_person_ref = _person_option_label(
            {
                "id": moneyless_int(row["pessoa_id"]),
                "nome": row["pessoa_nome"] or "",
                "codigo": row["pessoa_codigo"] or "",
                "cpf": format_cpf(row["pessoa_cpf"]),
                "sigla": status_sigla(row["pessoa_status"], True),
            }
        )
    main_contributor_ref = ""
    if moneyless_int(row["contribuinte_id"]):
        main_contributor_ref = _contributor_option_label(
            {
                "id": moneyless_int(row["contribuinte_id"]),
                "nome": row["contribuinte_nome"] or "",
                "documento": row["contribuinte_documento"] or "",
                "tipo": (row["contribuinte_tipo"] or "").upper(),
            }
        )
    selected_primary_ref = main_person_ref or main_contributor_ref or (row["nome_informado"] or "")
    context.update(
        {
            "pending_envelope": {
                "id": envelope_id,
                "lote_id": moneyless_int(row["lote_id"]),
                "status": row["status"] or "",
                "status_label": "Envelope lancado",
                "arquivo": row["nome_arquivo_original"] or "",
                "image_url": f"/contributions/envelopes/{envelope_id}/image/" if row["caminho_imagem"] else "",
                "is_image": suffix in {".jpg", ".jpeg", ".png", ".webp"},
                "lot_url": f"/contributions/envelopes/lots/{moneyless_int(row['lote_id'])}/",
                "ignore_url": "",
            },
            "is_editing": True,
            "form_action": f"/contributions/envelopes/{envelope_id}/edit/",
            "today": row["data_recebimento"] or date.today().isoformat(),
            "default_competencia_mes": _month_value_from_order(row["competencia_ordem"]),
            "default_lot_name": row["lote_nome"] or "",
            "default_origin": row["origem_operacional"] or "Envelope digitalizado",
            "default_type_id": line_defaults[0]["type_id"] or context.get("default_type_id") or 0,
            "default_campaign_id": line_defaults[0]["campaign_id"] or 0,
            "default_form_id": moneyless_int(row["forma_recebimento_id"]),
            "default_status": item_rows[0]["status_operacional"] if item_rows and item_rows[0]["status_operacional"] else "regular",
            "default_justification": row["justificativa"] or "Correcao manual de envelope lancado; imagem preservada para auditoria.",
            "default_total": "" if round(float(row["total_informado"] or 0), 2) <= 0 else _money(row["total_informado"]).replace("R$ ", ""),
            "selected_primary_ref": selected_primary_ref,
            "selected_person_ref": main_person_ref,
            "selected_contributor_ref": main_contributor_ref,
            "default_nome_informado": row["nome_informado"] or "",
            "default_telefone_informado": row["telefone_informado"] or "",
            "default_endereco_informado": row["endereco_informado"] or "",
            "default_observacoes": row["observacoes"] or "",
            "line_defaults": line_defaults,
            "traceability": {
                "forma_identificada": _clean_optional_text(_row_get(row, "rastreio_forma_identificada")),
                "banco_operadora": _clean_optional_text(_row_get(row, "rastreio_banco_operadora")),
                "numero_cheque": _clean_optional_text(_row_get(row, "rastreio_numero_cheque")),
                "numero_operacao": _clean_optional_text(_row_get(row, "rastreio_numero_operacao")),
                "nsu_tid": _clean_optional_text(_row_get(row, "rastreio_nsu_tid")),
                "ultimos_digitos_cartao": _clean_optional_text(_row_get(row, "rastreio_ultimos_digitos_cartao")),
                "data_operacao": _clean_optional_text(_row_get(row, "rastreio_data_operacao")),
                "valor_operacao": (
                    "" if _row_get(row, "rastreio_valor_operacao", None) in (None, "")
                    else _money(_row_get(row, "rastreio_valor_operacao")).replace("R$ ", "")
                ),
                "status_conciliacao": _row_get(row, "rastreio_status_conciliacao", "pendente") or "pendente",
                "observacoes": _clean_optional_text(_row_get(row, "rastreio_observacoes")),
            },
        }
    )
    return context


def envelope_lot_form_context() -> dict[str, Any]:
    context = manual_contribution_context()
    context["default_competencia_mes"] = date.today().strftime("%Y-%m")
    context["default_data_recebimento"] = date.today().isoformat()
    context["default_origin"] = "Envelope digitalizado"
    context["default_type_id"] = _default_type_id(context["type_options"])
    context["default_campaign_id"] = 0
    context["default_form_id"] = 0
    return context


def _envelope_tables_ready(conn: sqlite3.Connection) -> bool:
    return table_exists(conn, "envelope_lotes") and table_exists(conn, "envelopes") and table_exists(conn, "envelope_itens")


def list_envelopes(q: str = "", competencia: str = "", limit: int | None = None) -> dict[str, Any]:
    with connect_legacy() as conn:
        if not _envelope_tables_ready(conn):
            return {
                "items": [],
                "total": 0,
                "shown": 0,
                "total_value_fmt": _money(0),
                "competencias": [],
                "lots": [],
            }
        clauses = ["e.ativo = 1"]
        params: list[Any] = []
        q_norm = normalize_query(q)
        if q_norm:
            like = f"%{normalize_match_name(q_norm)}%"
            clauses.append(
                """
                (
                    NORMALIZE_MATCH(COALESCE(e.nome_informado, '')) LIKE ?
                    OR NORMALIZE_MATCH(COALESCE(p.nome, '')) LIKE ?
                    OR NORMALIZE_MATCH(COALESCE(ct.nome, '')) LIKE ?
                    OR NORMALIZE_MATCH(COALESCE(e.nome_arquivo_original, '')) LIKE ?
                    OR NORMALIZE_MATCH(COALESCE(e.imagem_hash, '')) LIKE ?
                )
                """
            )
            params.extend([like, like, like, like, like])
        if competencia:
            clauses.append("e.competencia = ?")
            params.append(competencia)
        where = " AND ".join(clauses)
        limit_value = moneyless_int(limit) if limit is not None else 0
        limit_clause = "LIMIT ?" if limit_value > 0 else ""
        row_params: tuple[Any, ...] = (*params, limit_value) if limit_value > 0 else tuple(params)
        total_row = conn.execute(
            f"""
            SELECT COUNT(*) AS qtd,
                   COALESCE(SUM(CASE WHEN e.status = 'lancado' THEN e.total_informado ELSE 0 END), 0) AS total
              FROM envelopes e
              LEFT JOIN pessoas p ON p.id = e.pessoa_id
              LEFT JOIN contribuintes ct ON ct.id = e.contribuinte_id
             WHERE {where}
            """,
            tuple(params),
        ).fetchone()
        rows = conn.execute(
            f"""
            SELECT e.*, l.nome AS lote_nome, l.caminho_pasta, p.nome AS pessoa_nome, p.status AS pessoa_status,
                   ct.nome AS contribuinte_nome, fr.nome AS forma_nome
              FROM envelopes e
              JOIN envelope_lotes l ON l.id = e.lote_id
              LEFT JOIN pessoas p ON p.id = e.pessoa_id
              LEFT JOIN contribuintes ct ON ct.id = e.contribuinte_id
              LEFT JOIN formas_recebimento fr ON fr.id = e.forma_recebimento_id
             WHERE {where}
             ORDER BY e.competencia_ordem DESC, l.id DESC, e.data_recebimento DESC, e.id DESC
             {limit_clause}
            """,
            row_params,
        ).fetchall()
        competencias = [
            row["competencia"]
            for row in conn.execute(
                """
                SELECT competencia, MAX(competencia_ordem) AS ordem
                  FROM envelopes
                 WHERE ativo = 1
                 GROUP BY competencia
                 ORDER BY ordem DESC, competencia DESC
                """
            ).fetchall()
        ]
        lots = [
            {
                "id": moneyless_int(row["id"]),
                "nome": row["nome"] or "",
                "competencia": row["competencia"] or "",
                "status": row["status"] or "",
                "status_label": _envelope_status_label(row["status"]),
                "total_envelopes": moneyless_int(row["total_envelopes_calc"]),
                "pendentes": moneyless_int(row["pendentes"]),
                "lancados": moneyless_int(row["lancados"]),
                "ignorados": moneyless_int(row["ignorados"]),
                "duplicados": moneyless_int(row["duplicados"]),
                "total_valor_fmt": _money(row["total_lancado"]),
                "caminho_pasta": row["caminho_pasta"] or "",
                "detail_url": f"/contributions/envelopes/lots/{moneyless_int(row['id'])}/",
                "next_pending_url": (
                    f"/contributions/envelopes/lots/{moneyless_int(row['id'])}/next/"
                    if moneyless_int(row["next_pending_id"])
                    else ""
                ),
            }
            for row in conn.execute(
                """
                SELECT l.*,
                       COUNT(e.id) AS total_envelopes_calc,
                       COALESCE(SUM(CASE WHEN e.status = 'aguardando_digitacao' THEN 1 ELSE 0 END), 0) AS pendentes,
                       COALESCE(SUM(CASE WHEN e.status = 'lancado' THEN 1 ELSE 0 END), 0) AS lancados,
                       COALESCE(SUM(CASE WHEN e.status = 'ignorado' THEN 1 ELSE 0 END), 0) AS ignorados,
                       COALESCE(SUM(CASE WHEN e.status = 'duplicado' THEN 1 ELSE 0 END), 0) AS duplicados,
                       COALESCE(SUM(CASE WHEN e.status = 'lancado' THEN e.total_informado ELSE 0 END), 0) AS total_lancado,
                       MIN(CASE WHEN e.status = 'aguardando_digitacao' THEN e.id ELSE NULL END) AS next_pending_id
                  FROM envelope_lotes l
                  LEFT JOIN envelopes e ON e.lote_id = l.id AND e.ativo = 1
                 GROUP BY l.id
                 ORDER BY l.competencia_ordem DESC, l.id DESC
                 LIMIT 30
                """
            ).fetchall()
        ]
    items = []
    for row in rows:
        person_name = row["pessoa_nome"] or ""
        contributor_name = row["contribuinte_nome"] or ""
        informed_name = row["nome_informado"] or ""
        display_name = person_name or contributor_name or informed_name or "Envelope sem nome informado"
        has_image = bool(row["caminho_imagem"])
        items.append(
            {
                "id": moneyless_int(row["id"]),
                "lote_id": moneyless_int(row["lote_id"]),
                "lote_nome": row["lote_nome"] or "",
                "competencia": row["competencia"] or "",
                "data": br_date(row["data_recebimento"]),
                "nome": display_name,
                "nome_informado": informed_name,
                "sigla": status_sigla(row["pessoa_status"], bool(person_name)) if person_name else "NR",
                "forma": row["forma_nome"] or "Nao informada",
                "status": row["status"] or "",
                "status_label": _envelope_status_label(row["status"]),
                "total_fmt": _money(row["total_informado"]),
                "imagem": "Sim" if has_image else "Nao",
                "detail_url": f"/contributions/envelopes/{moneyless_int(row['id'])}/",
                "image_url": f"/contributions/envelopes/{moneyless_int(row['id'])}/image/" if has_image else "",
                "launch_url": (
                    f"/contributions/envelopes/{moneyless_int(row['id'])}/launch/"
                    if str(row["status"] or "") == "aguardando_digitacao"
                    else ""
                ),
                "edit_url": (
                    f"/contributions/envelopes/{moneyless_int(row['id'])}/edit/"
                    if str(row["status"] or "") == "lancado"
                    else ""
                ),
            }
        )
    return {
        "items": items,
        "total": int(total_row["qtd"] or 0) if total_row else 0,
        "shown": len(items),
        "total_value_fmt": _money(total_row["total"] if total_row else 0),
        "competencias": competencias,
        "lots": lots,
        "limit": limit_value or (int(total_row["qtd"] or 0) if total_row else 0),
    }


def get_next_pending_envelope_id(lot_id: int) -> int:
    with connect_legacy() as conn:
        if not _envelope_tables_ready(conn):
            return 0
        order_expr = "COALESCE(ordem_lote, id)" if column_exists(conn, "envelopes", "ordem_lote") else "id"
        row = conn.execute(
            f"""
            SELECT id
              FROM envelopes
             WHERE lote_id = ? AND ativo = 1 AND status = 'aguardando_digitacao'
             ORDER BY {order_expr} ASC, id ASC
             LIMIT 1
            """,
            (moneyless_int(lot_id),),
        ).fetchone()
    return moneyless_int(row["id"]) if row else 0


def get_envelope_lot_detail(lot_id: int) -> dict[str, Any] | None:
    lot_id = moneyless_int(lot_id)
    with connect_legacy() as conn:
        if not _envelope_tables_ready(conn):
            return None
        lot = conn.execute(
            "SELECT * FROM envelope_lotes WHERE id = ?",
            (lot_id,),
        ).fetchone()
        if lot is None:
            return None
        order_expr = "COALESCE(e.ordem_lote, e.id)" if column_exists(conn, "envelopes", "ordem_lote") else "e.id"
        rows = conn.execute(
            f"""
            SELECT e.*, p.nome AS pessoa_nome, p.status AS pessoa_status,
                   ct.nome AS contribuinte_nome, fr.nome AS forma_nome
              FROM envelopes e
              LEFT JOIN pessoas p ON p.id = e.pessoa_id
              LEFT JOIN contribuintes ct ON ct.id = e.contribuinte_id
              LEFT JOIN formas_recebimento fr ON fr.id = e.forma_recebimento_id
             WHERE e.lote_id = ? AND e.ativo = 1
             ORDER BY {order_expr} ASC, e.id ASC
            """,
            (lot_id,),
        ).fetchall()
    items: list[dict[str, Any]] = []
    counts = {"total": 0, "pendentes": 0, "lancados": 0, "ignorados": 0, "duplicados": 0}
    total_launched = 0.0
    next_pending_id = 0
    for row in rows:
        status = str(row["status"] or "")
        counts["total"] += 1
        if status == "aguardando_digitacao":
            counts["pendentes"] += 1
            if not next_pending_id:
                next_pending_id = moneyless_int(row["id"])
        elif status == "lancado":
            counts["lancados"] += 1
            total_launched += float(row["total_informado"] or 0)
        elif status == "ignorado":
            counts["ignorados"] += 1
        elif status == "duplicado":
            counts["duplicados"] += 1
        display_name = row["pessoa_nome"] or row["contribuinte_nome"] or row["nome_informado"] or row["nome_arquivo_original"] or "Envelope sem nome"
        has_image = bool(row["caminho_imagem"])
        items.append(
            {
                "id": moneyless_int(row["id"]),
                "ordem": moneyless_int(_row_get(row, "ordem_lote")) or len(items) + 1,
                "arquivo": row["nome_arquivo_original"] or "",
                "data": br_date(row["data_recebimento"]),
                "data_raw": row["data_recebimento"] or "",
                "nome": display_name,
                "forma": row["forma_nome"] or "Nao informada",
                "status": status,
                "status_label": _envelope_status_label(status),
                "total_fmt": _money(row["total_informado"]),
                "observacoes": row["observacoes"] or "",
                "detail_url": f"/contributions/envelopes/{moneyless_int(row['id'])}/",
                "launch_url": f"/contributions/envelopes/{moneyless_int(row['id'])}/launch/" if status == "aguardando_digitacao" else "",
                "image_url": f"/contributions/envelopes/{moneyless_int(row['id'])}/image/" if has_image else "",
            }
        )
    competencia_ordem = moneyless_int(lot["competencia_ordem"])
    return {
        "id": lot_id,
        "nome": lot["nome"] or "",
        "competencia": lot["competencia"] or "",
        "competencia_mes": _month_value_from_order(competencia_ordem),
        "status": lot["status"] or "",
        "status_label": _envelope_status_label(lot["status"]),
        "data_padrao": br_date(_row_get(lot, "data_padrao_recebimento")),
        "data_padrao_raw": _row_get(lot, "data_padrao_recebimento") or date.today().isoformat(),
        "origem_operacional": _row_get(lot, "origem_operacional_padrao") or "Envelope digitalizado",
        "caminho_pasta": lot["caminho_pasta"] or "",
        "observacoes": lot["observacoes"] or "",
        "counts": counts,
        "total_lancado": total_launched,
        "total_lancado_fmt": _money(total_launched),
        "next_pending_id": next_pending_id,
        "next_pending_url": f"/contributions/envelopes/lots/{lot_id}/next/" if next_pending_id else "",
        "items": items,
    }


def pending_envelope_contribution_context(envelope_id: int) -> dict[str, Any] | None:
    envelope_id = moneyless_int(envelope_id)
    with connect_legacy() as conn:
        if not _envelope_tables_ready(conn):
            return None
        row = conn.execute(
            """
            SELECT e.*, l.nome AS lote_nome, l.caminho_pasta,
                   l.data_padrao_recebimento, l.origem_operacional_padrao,
                   l.tipo_contribuicao_id_padrao, l.campanha_id_padrao, l.forma_recebimento_id_padrao
              FROM envelopes e
              JOIN envelope_lotes l ON l.id = e.lote_id
             WHERE e.id = ? AND e.ativo = 1
             LIMIT 1
            """,
            (envelope_id,),
        ).fetchone()
        if row is None:
            return None
    context = envelope_contribution_context()
    suffix = Path(str(row["caminho_imagem"] or "")).suffix.lower()
    context.update(
        {
            "pending_envelope": {
                "id": envelope_id,
                "lote_id": moneyless_int(row["lote_id"]),
                "status": row["status"] or "",
                "status_label": _envelope_status_label(row["status"]),
                "arquivo": row["nome_arquivo_original"] or "",
                "image_url": f"/contributions/envelopes/{envelope_id}/image/" if row["caminho_imagem"] else "",
                "is_image": suffix in {".jpg", ".jpeg", ".png", ".webp"},
                "lot_url": f"/contributions/envelopes/lots/{moneyless_int(row['lote_id'])}/",
                "ignore_url": f"/contributions/envelopes/{envelope_id}/ignore/",
            },
            "form_action": f"/contributions/envelopes/{envelope_id}/launch/",
            "today": row["data_recebimento"] or _row_get(row, "data_padrao_recebimento") or date.today().isoformat(),
            "default_competencia_mes": _month_value_from_order(row["competencia_ordem"]),
            "default_lot_name": row["lote_nome"] or "",
            "default_origin": row["origem_operacional"] or _row_get(row, "origem_operacional_padrao") or "Envelope digitalizado",
            "default_type_id": moneyless_int(_row_get(row, "tipo_contribuicao_id_padrao")) or context.get("default_type_id") or 0,
            "default_campaign_id": moneyless_int(_row_get(row, "campanha_id_padrao")),
            "default_form_id": moneyless_int(row["forma_recebimento_id"]) or moneyless_int(_row_get(row, "forma_recebimento_id_padrao")),
            "selected_primary_ref": row["nome_informado"] or "",
            "default_justification": "Envelope conferido manualmente; imagem anexada para auditoria.",
            "default_total": "" if round(float(row["total_informado"] or 0), 2) <= 0 else _money(row["total_informado"]).replace("R$ ", ""),
            "default_nome_informado": row["nome_informado"] or "",
            "default_telefone_informado": row["telefone_informado"] or "",
            "default_endereco_informado": row["endereco_informado"] or "",
            "default_observacoes": row["observacoes"] or "",
            "traceability": {
                "forma_identificada": _clean_optional_text(_row_get(row, "rastreio_forma_identificada")),
                "banco_operadora": _clean_optional_text(_row_get(row, "rastreio_banco_operadora")),
                "numero_cheque": _clean_optional_text(_row_get(row, "rastreio_numero_cheque")),
                "numero_operacao": _clean_optional_text(_row_get(row, "rastreio_numero_operacao")),
                "nsu_tid": _clean_optional_text(_row_get(row, "rastreio_nsu_tid")),
                "ultimos_digitos_cartao": _clean_optional_text(_row_get(row, "rastreio_ultimos_digitos_cartao")),
                "data_operacao": _clean_optional_text(_row_get(row, "rastreio_data_operacao")),
                "valor_operacao": (
                    "" if _row_get(row, "rastreio_valor_operacao", None) in (None, "")
                    else _money(_row_get(row, "rastreio_valor_operacao")).replace("R$ ", "")
                ),
                "status_conciliacao": _row_get(row, "rastreio_status_conciliacao", "pendente") or "pendente",
                "observacoes": _clean_optional_text(_row_get(row, "rastreio_observacoes")),
            },
        }
    )
    return context


def get_envelope_detail(envelope_id: int) -> dict[str, Any] | None:
    with connect_legacy() as conn:
        if not _envelope_tables_ready(conn):
            return None
        row = conn.execute(
            """
            SELECT e.*, l.nome AS lote_nome, l.caminho_pasta, p.nome AS pessoa_nome, p.cpf AS pessoa_cpf,
                   p.status AS pessoa_status, ct.nome AS contribuinte_nome, ct.documento_principal,
                   fr.nome AS forma_nome
              FROM envelopes e
              JOIN envelope_lotes l ON l.id = e.lote_id
              LEFT JOIN pessoas p ON p.id = e.pessoa_id
              LEFT JOIN contribuintes ct ON ct.id = e.contribuinte_id
              LEFT JOIN formas_recebimento fr ON fr.id = e.forma_recebimento_id
             WHERE e.id = ? AND e.ativo = 1
            """,
            (envelope_id,),
        ).fetchone()
        if row is None:
            return None
        item_rows = conn.execute(
            """
            SELECT ei.*, c.data_recebimento, p.nome AS pessoa_nome, p.status AS pessoa_status,
                   ct.nome AS contribuinte_nome, tc.nome AS tipo_nome, ca.nome AS campanha_nome
              FROM envelope_itens ei
              LEFT JOIN contribuicoes c ON c.id = ei.contribuicao_id
              LEFT JOIN pessoas p ON p.id = ei.pessoa_id
              LEFT JOIN contribuintes ct ON ct.id = ei.contribuinte_id
              LEFT JOIN tipos_contribuicao tc ON tc.id = ei.tipo_contribuicao_id
              LEFT JOIN campanhas ca ON ca.id = ei.campanha_id
             WHERE ei.envelope_id = ? AND ei.ativo = 1
             ORDER BY ei.id
            """,
            (envelope_id,),
        ).fetchall()
        update_rows = []
        if table_exists(conn, "envelope_atualizacoes_cadastrais"):
            update_rows = conn.execute(
                """
                SELECT u.*, p.nome AS pessoa_nome, p.codigo_interno, p.cpf
                  FROM envelope_atualizacoes_cadastrais u
                  JOIN pessoas p ON p.id = u.pessoa_id
                 WHERE u.envelope_id = ?
                 ORDER BY CASE u.status WHEN 'pendente' THEN 0 ELSE 1 END, u.campo, u.id
                """,
                (envelope_id,),
            ).fetchall()
    image_path = Path(str(row["caminho_imagem"] or ""))
    image_suffix = image_path.suffix.lower()
    is_image = image_suffix in {".jpg", ".jpeg", ".png", ".webp"}
    items = [
        {
            "id": moneyless_int(item["id"]),
            "contribution_id": moneyless_int(item["contribuicao_id"]),
            "contribution_url": f"/contributions/{moneyless_int(item['contribuicao_id'])}/" if item["contribuicao_id"] else "",
            "nome": item["pessoa_nome"] or item["contribuinte_nome"] or "Sem pessoa vinculada",
            "sigla": status_sigla(item["pessoa_status"], bool(item["pessoa_nome"])),
            "tipo": item["tipo_nome"] or "",
            "campanha": item["campanha_nome"] or "",
            "valor_fmt": _money(item["valor"]),
            "observacoes": item["observacoes"] or "",
        }
        for item in item_rows
    ]
    return {
        "id": moneyless_int(row["id"]),
        "lote_id": moneyless_int(row["lote_id"]),
        "lote_nome": row["lote_nome"] or "",
        "lote_url": f"/contributions/envelopes/lots/{moneyless_int(row['lote_id'])}/",
        "edit_url": f"/contributions/envelopes/{moneyless_int(row['id'])}/edit/" if str(row["status"] or "") == "lancado" else "",
        "competencia": row["competencia"] or "",
        "data": br_date(row["data_recebimento"]),
        "total_fmt": _money(row["total_informado"]),
        "total_linhas_fmt": _money(row["total_linhas"]),
        "nome_informado": row["nome_informado"] or "",
        "telefone_informado": row["telefone_informado"] or "",
        "endereco_informado": row["endereco_informado"] or "",
        "pessoa_nome": row["pessoa_nome"] or "",
        "pessoa_url": f"/people/{moneyless_int(row['pessoa_id'])}/" if row["pessoa_id"] else "",
        "pessoa_cpf": format_cpf(row["pessoa_cpf"]),
        "contribuinte_nome": row["contribuinte_nome"] or "",
        "contribuinte_url": f"/contributors/{moneyless_int(row['contribuinte_id'])}/" if row["contribuinte_id"] else "",
        "documento_principal": format_document(row["documento_principal"]),
        "forma": row["forma_nome"] or "Nao informada",
        "origem_operacional": row["origem_operacional"] or "",
            "traceability": {
                "forma_identificada": _clean_optional_text(_row_get(row, "rastreio_forma_identificada")),
                "banco_operadora": _clean_optional_text(_row_get(row, "rastreio_banco_operadora")),
                "numero_cheque": _clean_optional_text(_row_get(row, "rastreio_numero_cheque")),
                "numero_operacao": _clean_optional_text(_row_get(row, "rastreio_numero_operacao")),
                "nsu_tid": _clean_optional_text(_row_get(row, "rastreio_nsu_tid")),
                "ultimos_digitos_cartao": _clean_optional_text(_row_get(row, "rastreio_ultimos_digitos_cartao")),
                "data_operacao": br_date(_row_get(row, "rastreio_data_operacao")) if _row_get(row, "rastreio_data_operacao") else "",
                "valor_operacao_fmt": _money(_row_get(row, "rastreio_valor_operacao")) if _row_get(row, "rastreio_valor_operacao", None) not in (None, "") else "",
                "status_conciliacao": _row_get(row, "rastreio_status_conciliacao", "pendente") or "pendente",
                "observacoes": _clean_optional_text(_row_get(row, "rastreio_observacoes")),
            },
        "status": row["status"] or "",
        "observacoes": row["observacoes"] or "",
        "justificativa": row["justificativa"] or "",
        "nome_arquivo_original": row["nome_arquivo_original"] or "",
        "imagem_hash": row["imagem_hash"] or "",
        "caminho_pasta": row["caminho_pasta"] or "",
        "has_image": bool(row["caminho_imagem"]),
        "image_path": str(row["caminho_imagem"] or ""),
        "image_content_type": str(row["imagem_content_type"] or ""),
        "image_url": f"/contributions/envelopes/{moneyless_int(row['id'])}/image/" if row["caminho_imagem"] else "",
        "is_image": is_image,
        "items": items,
        "profile_updates": [
            {
                "id": moneyless_int(update["id"]),
                "pessoa_nome": update["pessoa_nome"] or "",
                "pessoa_url": f"/people/{moneyless_int(update['pessoa_id'])}/",
                "pessoa_edit_url": f"/people/{moneyless_int(update['pessoa_id'])}/edit/",
                "codigo": update["codigo_interno"] or "",
                "cpf": format_cpf(update["cpf"]),
                "campo": update["campo"] or "",
                "valor_cadastro": update["valor_cadastro"] or "",
                "valor_envelope": update["valor_envelope"] or "",
                "status": update["status"] or "",
                "can_apply": (update["campo"] or "") == "telefone" and (update["status"] or "") == "pendente",
                "can_ignore": (update["status"] or "") == "pendente",
                "apply_url": f"/contributions/envelopes/profile-updates/{moneyless_int(update['id'])}/apply/",
                "ignore_url": f"/contributions/envelopes/profile-updates/{moneyless_int(update['id'])}/ignore/",
            }
            for update in update_rows
        ],
    }


def new_contribution_context(person_id: int) -> dict[str, Any] | None:
    with connect_legacy() as conn:
        person = conn.execute(
            """
            SELECT id, organizacao_id, codigo_interno, nome, cpf, status
              FROM pessoas
             WHERE id = ? AND ativo = 1
            """,
            (person_id,),
        ).fetchone()
        if person is None:
            return None
        total_value = float(
            scalar(
                conn,
                "SELECT COALESCE(SUM(valor), 0) FROM contribuicoes WHERE ativo = 1 AND pessoa_id = ?",
                (person_id,),
            )
            or 0
        )
        catalogs = _contribution_catalog_options(conn, moneyless_int(person["organizacao_id"]))
    return {
        "person": {
            "id": moneyless_int(person["id"]),
            "codigo": person["codigo_interno"] or "",
            "nome": person["nome"] or "",
            "cpf": format_cpf(person["cpf"]),
            "status": format_status(person["status"]),
            "sigla": status_sigla(person["status"], True),
            "total_fmt": _money(total_value),
        },
        "type_options": catalogs["type_options"],
        "campaign_options": catalogs["campaign_options"],
        "receiving_options": catalogs["receiving_options"],
        "status_options": catalogs["status_options"],
    }


def get_contribution_detail(contribution_id: int) -> dict[str, Any] | None:
    with connect_legacy() as conn:
        row = conn.execute(
            """
            SELECT co.*,
                   p.nome AS pessoa_nome,
                   p.codigo_interno AS pessoa_codigo,
                   p.cpf AS pessoa_cpf,
                   p.status AS pessoa_status,
                   c.nome AS contribuinte_nome,
                   c.tipo AS contribuinte_tipo,
                   c.documento_principal AS contribuinte_documento,
                   t.nome AS tipo_nome,
                   ca.nome AS campanha_nome,
                   f.nome AS forma_nome,
                   pm.id AS pix_movimento_id,
                   pm.lote_id AS pix_lote_id,
                   em.id AS extrato_movimento_id,
                   em.lote_id AS extrato_lote_id,
                   el.banco AS extrato_banco
              FROM contribuicoes co
              LEFT JOIN pessoas p ON p.id = co.pessoa_id
              LEFT JOIN contribuintes c ON c.id = co.contribuinte_id
              LEFT JOIN tipos_contribuicao t ON t.id = co.tipo_contribuicao_id
              LEFT JOIN campanhas ca ON ca.id = co.campanha_id
              LEFT JOIN formas_recebimento f ON f.id = co.forma_recebimento_id
              LEFT JOIN pix_movimentos pm ON pm.id = co.pix_movimento_id
              LEFT JOIN extrato_movimentos em ON em.id = co.extrato_movimento_id
              LEFT JOIN extrato_lotes el ON el.id = em.lote_id
             WHERE co.id = ?
            """,
            (contribution_id,),
        ).fetchone()
        if row is None:
            return None
        catalogs = _contribution_catalog_options(
            conn,
            moneyless_int(row["organizacao_id"]),
            selected_type_id=moneyless_int(row["tipo_contribuicao_id"]),
            selected_form_id=moneyless_int(row["forma_recebimento_id"]),
            selected_campaign_id=moneyless_int(row["campanha_id"]),
        )
        audit_rows = conn.execute(
            """
            SELECT acao, dados_antes_json, dados_depois_json, criado_em
              FROM auditoria
             WHERE tabela = 'contribuicoes' AND registro_id = ?
             ORDER BY criado_em DESC, id DESC
             LIMIT 12
            """,
            (contribution_id,),
        ).fetchall()
    status_raw = row["status_operacional"] or "regular"
    status_options = list(catalogs["status_options"])
    if status_raw not in {item["value"] for item in status_options}:
        status_options.append({"value": status_raw, "label": status_raw.replace("_", " ").title()})
    for option in status_options:
        option["selected"] = option["value"] == status_raw
    return {
        "contribution": {
            "id": moneyless_int(row["id"]),
            "data": br_date(row["data_recebimento"]),
            "data_raw": row["data_recebimento"] or "",
            "competencia": row["competencia"] or "",
            "valor": float(row["valor"] or 0),
            "valor_fmt": _money(row["valor"]),
            "valor_input": _money(row["valor"]).replace("R$ ", ""),
            "status": status_raw,
            "status_label": CONTRIBUTION_STATUS_LABELS.get(status_raw, status_raw.replace("_", " ").title()),
            "observacoes": row["observacoes"] or "",
            "ativo": bool(row["ativo"]),
            "criado_em": br_datetime(row["criado_em"]),
            "atualizado_em": br_datetime(row["atualizado_em"]),
            "person_id": moneyless_int(row["pessoa_id"]),
            "person_name": row["pessoa_nome"] or "",
            "person_code": row["pessoa_codigo"] or "",
            "person_cpf": format_cpf(row["pessoa_cpf"]),
            "person_status": format_status(row["pessoa_status"]),
            "person_sigla": status_sigla(row["pessoa_status"], bool(row["pessoa_id"])),
            "contributor_id": moneyless_int(row["contribuinte_id"]),
            "contributor_name": row["contribuinte_nome"] or "",
            "contributor_type": row["contribuinte_tipo"] or "",
            "contributor_document": row["contribuinte_documento"] or "",
            "type_name": row["tipo_nome"] or "",
            "campaign_name": row["campanha_nome"] or "",
            "form_name": row["forma_nome"] or "",
            "pix_movement_id": moneyless_int(row["pix_movimento_id"]),
            "pix_lot_id": moneyless_int(row["pix_lote_id"]),
            "statement_movement_id": moneyless_int(row["extrato_movimento_id"]),
            "statement_lot_id": moneyless_int(row["extrato_lote_id"]),
            "statement_bank": row["extrato_banco"] or "",
        },
        "type_options": catalogs["type_options"],
        "campaign_options": catalogs["campaign_options"],
        "receiving_options": catalogs["receiving_options"],
        "status_options": status_options,
        "audit_rows": [
            {
                "acao": audit["acao"] or "",
                "criado_em": br_datetime(audit["criado_em"]),
                "antes": audit["dados_antes_json"] or "",
                "depois": audit["dados_depois_json"] or "",
            }
            for audit in audit_rows
        ],
    }


def split_contribution_context(contribution_id: int) -> dict[str, Any] | None:
    detail = get_contribution_detail(contribution_id)
    if detail is None:
        return None
    with connect_legacy() as conn:
        organization_id = int(
            scalar(conn, "SELECT organizacao_id FROM contribuicoes WHERE id = ?", (contribution_id,)) or 1
        )
        catalogs = _contribution_catalog_options(
            conn,
            organization_id,
            selected_form_id=moneyless_int(
                scalar(conn, "SELECT forma_recebimento_id FROM contribuicoes WHERE id = ?", (contribution_id,)) or 0
            ),
        )
        return {
            "detail": detail,
            "line_range": range(1, 9),
            "people_options": _manual_people_options(conn, organization_id),
            "contributor_options": _manual_contributor_options(conn, organization_id),
            "type_options": catalogs["type_options"],
            "campaign_options": catalogs["campaign_options"],
            "receiving_options": catalogs["receiving_options"],
            "status_options": catalogs["status_options"],
            "original_total": detail["contribution"]["valor"],
            "original_total_fmt": detail["contribution"]["valor_fmt"],
        }


def person_statement_data(
    person_id: int,
    year: str = "",
    competencia: str = "",
    date_start: str = "",
    date_end: str = "",
    type_ids: list[int] | None = None,
) -> dict[str, Any] | None:
    year = normalize_query(year)
    competencia = normalize_query(competencia)
    date_start = normalize_query(date_start)
    date_end = normalize_query(date_end)
    selected_type_ids = [moneyless_int(item) for item in (type_ids or []) if moneyless_int(item)]
    with connect_legacy() as conn:
        person = conn.execute(
            "SELECT id, organizacao_id, codigo_interno, nome, cpf, status, email_principal FROM pessoas WHERE id = ?",
            (person_id,),
        ).fetchone()
        if person is None:
            return None
        clauses = ["co.ativo = 1", "co.pessoa_id = ?"]
        params: list[Any] = [person_id]
        if year:
            clauses.append("substr(co.data_recebimento, 1, 4) = ?")
            params.append(year)
        if competencia:
            clauses.append("COALESCE(co.competencia, '') = ?")
            params.append(competencia)
        if date_start:
            clauses.append("co.data_recebimento >= ?")
            params.append(date_start)
        if date_end:
            clauses.append("co.data_recebimento <= ?")
            params.append(date_end)
        if selected_type_ids:
            placeholders = ",".join("?" for _ in selected_type_ids)
            clauses.append(f"co.tipo_contribuicao_id IN ({placeholders})")
            params.extend(selected_type_ids)
        rows = conn.execute(
            f"""
            SELECT co.id, co.data_recebimento, co.competencia, co.valor, co.observacoes,
                   t.nome AS tipo_nome, f.nome AS forma_nome
              FROM contribuicoes co
              JOIN tipos_contribuicao t ON t.id = co.tipo_contribuicao_id
              LEFT JOIN formas_recebimento f ON f.id = co.forma_recebimento_id
             WHERE {' AND '.join(clauses)}
             ORDER BY COALESCE(co.competencia_ordem, 0), co.data_recebimento, co.id
            """,
            tuple(params),
        ).fetchall()
        years = [
            row["ano"]
            for row in conn.execute(
                """
                SELECT DISTINCT substr(data_recebimento, 1, 4) AS ano
                  FROM contribuicoes
                 WHERE ativo = 1 AND pessoa_id = ? AND COALESCE(data_recebimento, '') <> ''
                 ORDER BY ano DESC
                """,
                (person_id,),
            ).fetchall()
            if row["ano"]
        ]
        competencias = [
            row["competencia"]
            for row in conn.execute(
                """
                SELECT DISTINCT competencia, MAX(COALESCE(competencia_ordem, 0)) AS ordem
                  FROM contribuicoes
                 WHERE ativo = 1 AND pessoa_id = ? AND COALESCE(competencia, '') <> ''
                 GROUP BY competencia
                 ORDER BY ordem DESC
                """,
                (person_id,),
            ).fetchall()
            if row["competencia"]
        ]
        catalogs = _contribution_catalog_options(conn, moneyless_int(person["organizacao_id"]))
    entries: list[dict[str, Any]] = []
    total = 0.0
    current_competence = ""
    subtotal = 0.0
    competence_count = 0
    for row in rows:
        row_competence = row["competencia"] or ""
        value = float(row["valor"] or 0)
        if current_competence and row_competence != current_competence:
            entries.append({"kind": "subtotal", "competencia": current_competence, "subtotal": subtotal, "subtotal_fmt": _money(subtotal)})
            subtotal = 0.0
        if row_competence != current_competence:
            current_competence = row_competence
            competence_count += 1
        total += value
        subtotal += value
        entries.append(
            {
                "kind": "item",
                "id": moneyless_int(row["id"]),
                "data": br_date(row["data_recebimento"]),
                "competencia": row_competence,
                "tipo": row["tipo_nome"] or "",
                "forma": row["forma_nome"] or "",
                "observacoes": row["observacoes"] or "",
                "valor_fmt": _money(value),
                "detail_url": f"/contributions/{row['id']}/",
            }
        )
    if current_competence:
        entries.append({"kind": "subtotal", "competencia": current_competence, "subtotal": subtotal, "subtotal_fmt": _money(subtotal)})
    for option in catalogs["type_options"]:
        option["selected"] = option["id"] in selected_type_ids
    return {
        "person": {
            "id": moneyless_int(person["id"]),
            "nome": person["nome"] or "",
            "codigo": person["codigo_interno"] or "",
            "cpf": format_cpf(person["cpf"]),
            "status": format_status(person["status"]),
            "sigla": status_sigla(person["status"], True),
            "email": person["email_principal"] or "",
        },
        "entries": entries,
        "summary": {
            "lancamentos": len(rows),
            "competencias": competence_count,
            "total": total,
            "total_fmt": _money(total),
        },
        "years": years,
        "competencias": competencias,
        "type_options": catalogs["type_options"],
        "filters": {
            "year": year,
            "competencia": competencia,
            "date_start": date_start,
            "date_end": date_end,
            "type_ids": selected_type_ids,
        },
    }


def search_receipt_people(q: str = "", limit: int = 20) -> list[dict[str, Any]]:
    query = normalize_query(q)
    digits = "".join(ch for ch in query if ch.isdigit())
    if len(query) < 2 and len(digits) < 2:
        return []
    limit = max(1, min(moneyless_int(limit) or 20, 80))
    models = _people_snapshot_models()
    if models:
        rows = (
            models["person"].objects.filter(is_active=True)
            .filter(_person_snapshot_search_q(query))
            .order_by("normalized_name", "legacy_id")[:limit]
        )
        return [
            {
                "id": moneyless_int(row.legacy_id),
                "nome": row.name or "",
                "codigo": row.internal_code or "",
                "cpf": format_cpf(row.cpf),
                "status": format_status(row.status),
                "sigla": status_sigla(row.status, True),
                "email": row.primary_email or "",
                "telefone": row.primary_phone or "",
            }
            for row in rows
        ]
    raise LegacyDatabaseError("Espelho cadastral Postgres indisponivel para busca de recibos.")


def receipt_new_context(person_id: int, date_start: str = "", date_end: str = "") -> dict[str, Any] | None:
    date_start = normalize_query(date_start)
    date_end = normalize_query(date_end)
    with connect_legacy() as conn:
        person = conn.execute(
            "SELECT id, codigo_interno, nome, cpf, status, email_principal, telefone_principal FROM pessoas WHERE id = ?",
            (person_id,),
        ).fetchone()
        if person is None:
            return None
        clauses = ["co.ativo = 1", "co.pessoa_id = ?"]
        params: list[Any] = [person_id]
        if date_start:
            clauses.append("co.data_recebimento >= ?")
            params.append(date_start)
        if date_end:
            clauses.append("co.data_recebimento <= ?")
            params.append(date_end)
        rows = conn.execute(
            f"""
            SELECT co.id, co.data_recebimento, co.competencia, co.valor,
                   t.nome AS tipo_nome, f.nome AS forma_nome
             FROM contribuicoes co
              JOIN tipos_contribuicao t ON t.id = co.tipo_contribuicao_id
              LEFT JOIN formas_recebimento f ON f.id = co.forma_recebimento_id
             WHERE {' AND '.join(clauses)}
             ORDER BY co.data_recebimento, co.id
            """,
            tuple(params),
        ).fetchall()
        receipt_rows = conn.execute(
            """
            SELECT ri.contribuicao_id, r.id, r.numero, r.data_emissao
              FROM recibo_itens ri
              JOIN recibos r ON r.id = ri.recibo_id
             WHERE r.pessoa_id = ?
               AND r.status <> 'cancelado'
               AND r.cancelado_em IS NULL
            """,
            (person_id,),
        ).fetchall()
    active_receipt_by_contribution: dict[int, dict[str, Any]] = {}
    for row in receipt_rows:
        contribution_id = moneyless_int(row["contribuicao_id"])
        if contribution_id and contribution_id not in active_receipt_by_contribution:
            active_receipt_by_contribution[contribution_id] = {
                "id": moneyless_int(row["id"]),
                "numero": row["numero"] or "",
                "data": br_date(row["data_emissao"]),
            }
    total = sum(float(row["valor"] or 0) for row in rows)
    return {
        "person": {
            "id": moneyless_int(person["id"]),
            "nome": person["nome"] or "",
            "codigo": person["codigo_interno"] or "",
            "cpf": format_cpf(person["cpf"]),
            "status": format_status(person["status"]),
            "sigla": status_sigla(person["status"], True),
            "email": person["email_principal"] or "",
            "telefone": person["telefone_principal"] or "",
        },
        "items": [
            {
                "id": moneyless_int(row["id"]),
                "data": br_date(row["data_recebimento"]),
                "competencia": row["competencia"] or "",
                "tipo": row["tipo_nome"] or "",
                "forma": row["forma_nome"] or "",
                "valor_fmt": _money(row["valor"]),
                "active_receipt": active_receipt_by_contribution.get(moneyless_int(row["id"])),
            }
            for row in rows
        ],
        "total_fmt": _money(total),
        "filters": {"date_start": date_start, "date_end": date_end},
    }


def list_receipts(q: str = "", person_id: int = 0, date_start: str = "", date_end: str = "", limit: int | None = None) -> dict[str, Any]:
    q = normalize_query(q)
    date_start = normalize_query(date_start)
    date_end = normalize_query(date_end)
    clauses = ["r.status <> 'cancelado'"]
    params: list[Any] = []
    if person_id:
        clauses.append("r.pessoa_id = ?")
        params.append(person_id)
    if q:
        like = f"%{normalize_match_name(q)}%"
        digits = "".join(ch for ch in q if ch.isdigit())
        clauses.append("(NORMALIZE_MATCH(COALESCE(p.nome, '')) LIKE ? OR COALESCE(p.cpf, '') LIKE ? OR COALESCE(p.codigo_interno, '') LIKE ? OR r.numero LIKE ?)")
        params.extend([like, f"%{digits or q}%", f"%{q}%", f"%{q}%"])
    if date_start:
        clauses.append("r.data_emissao >= ?")
        params.append(date_start)
    if date_end:
        clauses.append("r.data_emissao <= ?")
        params.append(date_end)
    where = " AND ".join(clauses)
    with connect_legacy() as conn:
        limit_value = moneyless_int(limit) if limit is not None else 0
        limit_clause = "LIMIT ?" if limit_value > 0 else ""
        row_params: tuple[Any, ...] = (*params, limit_value) if limit_value > 0 else tuple(params)
        rows = conn.execute(
            f"""
            SELECT r.*, p.nome AS pessoa_nome, p.codigo_interno, p.cpf
              FROM recibos r
              JOIN pessoas p ON p.id = r.pessoa_id
             WHERE {where}
             ORDER BY r.data_emissao DESC, r.id DESC
             {limit_clause}
            """,
            row_params,
        ).fetchall()
        summary = conn.execute(
            f"""
            SELECT COUNT(*) AS quantidade, COALESCE(SUM(r.valor_total), 0) AS total,
                   COUNT(DISTINCT r.pessoa_id) AS pessoas, MAX(r.data_emissao) AS ultima_data
              FROM recibos r
              JOIN pessoas p ON p.id = r.pessoa_id
             WHERE {where}
            """,
            tuple(params),
        ).fetchone()
    return {
        "items": [
            {
                "id": moneyless_int(row["id"]),
                "numero": row["numero"] or "",
                "data": br_date(row["data_emissao"]),
                "periodo": f"{br_date(row['periodo_inicio'])} a {br_date(row['periodo_fim'])}",
                "valor_fmt": _money(row["valor_total"]),
                "status": row["status"] or "",
                "person_id": moneyless_int(row["pessoa_id"]),
                "person_name": row["pessoa_nome"] or "",
                "person_code": row["codigo_interno"] or "",
                "person_cpf": format_cpf(row["cpf"]),
                "detail_url": f"/receipts/{row['id']}/",
            }
            for row in rows
        ],
        "summary": {
            "quantidade": int(summary["quantidade"] or 0) if summary else 0,
            "total_fmt": _money(summary["total"] if summary else 0),
            "pessoas": int(summary["pessoas"] or 0) if summary else 0,
            "ultima_data": br_date(summary["ultima_data"]) if summary else "",
        },
        "filters": {"q": q, "person_id": person_id, "date_start": date_start, "date_end": date_end},
    }


def get_receipt_detail(receipt_id: int) -> dict[str, Any] | None:
    with connect_legacy() as conn:
        receipt = conn.execute(
            """
            SELECT r.*, o.nome AS organizacao_nome, o.nome_fantasia AS organizacao_fantasia,
                   p.nome AS pessoa_nome, p.codigo_interno, p.cpf, p.email_principal, p.telefone_principal
              FROM recibos r
              JOIN pessoas p ON p.id = r.pessoa_id
              JOIN organizacoes o ON o.id = r.organizacao_id
             WHERE r.id = ?
            """,
            (receipt_id,),
        ).fetchone()
        if receipt is None:
            return None
        items = conn.execute(
            """
            SELECT ri.*, co.data_recebimento, co.competencia, co.observacoes,
                   t.nome AS tipo_nome, f.nome AS forma_nome
              FROM recibo_itens ri
              JOIN contribuicoes co ON co.id = ri.contribuicao_id
              JOIN tipos_contribuicao t ON t.id = co.tipo_contribuicao_id
              LEFT JOIN formas_recebimento f ON f.id = co.forma_recebimento_id
             WHERE ri.recibo_id = ?
             ORDER BY co.data_recebimento, co.id
            """,
            (receipt_id,),
        ).fetchall()
    return {
        "receipt": {
            "id": moneyless_int(receipt["id"]),
            "numero": receipt["numero"] or "",
            "status": receipt["status"] or "",
            "organization_id": moneyless_int(receipt["organizacao_id"]),
            "organizacao": receipt["organizacao_fantasia"] or receipt["organizacao_nome"] or "",
            "person_id": moneyless_int(receipt["pessoa_id"]),
            "person_name": receipt["pessoa_nome"] or "",
            "person_code": receipt["codigo_interno"] or "",
            "person_cpf": format_cpf(receipt["cpf"]),
            "person_email": receipt["email_principal"] or "",
            "person_phone": receipt["telefone_principal"] or "",
            "data": br_date(receipt["data_emissao"]),
            "data_raw": receipt["data_emissao"] or "",
            "periodo_inicio": br_date(receipt["periodo_inicio"]),
            "periodo_inicio_raw": receipt["periodo_inicio"] or "",
            "periodo_fim": br_date(receipt["periodo_fim"]),
            "periodo_fim_raw": receipt["periodo_fim"] or "",
            "valor_fmt": _money(receipt["valor_total"]),
            "valor_total": round(float(receipt["valor_total"] or 0), 2),
            "observacoes": receipt["observacoes"] or "",
        },
        "person": {
            "id": moneyless_int(receipt["pessoa_id"]),
            "nome": receipt["pessoa_nome"] or "",
            "codigo": receipt["codigo_interno"] or "",
            "cpf": format_cpf(receipt["cpf"]),
            "email": receipt["email_principal"] or "",
            "telefone": receipt["telefone_principal"] or "",
        },
        "items": [
            {
                "contribution_id": moneyless_int(row["contribuicao_id"]),
                "data": br_date(row["data_recebimento"]),
                "competencia": row["competencia"] or "",
                "tipo": row["tipo_nome"] or "",
                "forma": row["forma_nome"] or "",
                "observacoes": row["observacoes"] or "",
                "valor_fmt": _money(row["valor"]),
            }
            for row in items
        ],
    }


def contributor_recurrence_flags(row: dict[str, Any]) -> dict[str, Any]:
    qty = moneyless_int(row.get("contribuicoes_qtd") or row.get("quantidade"))
    weeks = moneyless_int(row.get("semanas_qtd"))
    competencias = moneyless_int(row.get("competencias_qtd") or row.get("competencias"))
    months = moneyless_int(row.get("meses_recebimento_qtd"))
    person_id = moneyless_int(row.get("pessoa_id"))
    weekly = qty >= 2 and weeks >= 2
    multi_competencia = qty >= 2 and max(competencias, months) >= 2
    candidate = person_id == 0 and (weekly or multi_competencia)
    priority = 2 if candidate and weekly and multi_competencia else 1 if candidate else 0
    return {
        "weekly": weekly,
        "multi_competencia": multi_competencia,
        "candidate": candidate,
        "priority": priority,
        "weeks": weeks,
        "competencias": max(competencias, months),
    }


def contributor_family_keys(value: object) -> dict[str, str]:
    particles = {"DE", "DA", "DO", "DAS", "DOS", "E"}
    tokens = [token for token in normalize_match_name(value).split() if token]
    if len(tokens) < 2:
        return {"broad": "", "nuclear": ""}
    surname_tokens = [token for token in tokens[1:] if token not in particles and len(token) > 1]
    if not surname_tokens:
        surname_tokens = [token for token in tokens[1:] if len(token) > 1]
    if not surname_tokens:
        return {"broad": "", "nuclear": ""}
    broad = surname_tokens[-1]
    nuclear = " ".join(surname_tokens[-2:]) if len(surname_tokens) >= 2 else broad
    return {"broad": broad, "nuclear": nuclear}


def _format_dashboard_contributor(row: dict[str, Any]) -> dict[str, Any]:
    recurrence = contributor_recurrence_flags(row)
    identity = contribution_report_identity("", row.get("nome"), row.get("documento_principal"))
    display_name = identity["name"] or row.get("nome") or ""
    family_keys = contributor_family_keys(display_name)
    total = float(row.get("total_contribuido") or 0)
    return {
        "id": moneyless_int(row.get("id")),
        "nome": display_name,
        "nome_original": row.get("nome") or "",
        "sort_key": identity["sort_key"] or normalize_match_name(display_name),
        "group_kind": identity["group_kind"],
        "documento": row.get("documento_principal") or "",
        "documento_principal": row.get("documento_principal") or "",
        "documento_tipo": row.get("documento_tipo") or "",
        "tipo": row.get("tipo") or "",
        "tipo_label": "PF" if str(row.get("tipo") or "") == "pf" else "PJ",
        "status": row.get("status") or "",
        "origem": row.get("origem") or "",
        "qualidade": row.get("qualidade") or "",
        "pessoa_id": moneyless_int(row.get("pessoa_id")),
        "pessoa_nome": row.get("pessoa_nome") or "",
        "pessoa_sigla": status_sigla(row.get("pessoa_status"), bool(moneyless_int(row.get("pessoa_id")))),
        "pessoa_status": row.get("pessoa_status") or "",
        "contribuicoes_qtd": moneyless_int(row.get("contribuicoes_qtd")),
        "total_contribuido": total,
        "total_contribuido_fmt": _money(total),
        "total_fmt": _money(total),
        "primeira_contribuicao": br_date(row.get("primeira_contribuicao")),
        "ultima_contribuicao": row.get("ultima_contribuicao") or "",
        "ultima_contribuicao_fmt": br_date(row.get("ultima_contribuicao")),
        "competencias_qtd": moneyless_int(row.get("competencias_qtd")),
        "semanas_qtd": moneyless_int(row.get("semanas_qtd")),
        "contribuicoes_sem_pessoa": moneyless_int(row.get("contribuicoes_sem_pessoa")),
        "pix_pendentes": moneyless_int(row.get("pix_pendentes")),
        "pix_pendentes_pessoa": moneyless_int(row.get("pix_pendentes_pessoa")),
        "pix_pendentes_destinacao": moneyless_int(row.get("pix_pendentes_destinacao")),
        "pix_pendentes_duplicidade": moneyless_int(row.get("pix_pendentes_duplicidade")),
        "pendencias_total": moneyless_int(row.get("pendencias_total")),
        "recorrencia_semanal": 1 if recurrence["weekly"] else 0,
        "recorrencia_multicompetencia": 1 if recurrence["multi_competencia"] else 0,
        "sugestao_integracao": 1 if recurrence["candidate"] else 0,
        "prioridade_integracao": moneyless_int(recurrence["priority"]),
        "recorrencia_semanas": moneyless_int(recurrence["weeks"]),
        "recorrencia_competencias": moneyless_int(recurrence["competencias"]),
        "familia_sugerida": 1 if family_keys.get("nuclear") or family_keys.get("broad") else 0,
        "familia_nuclear": family_keys.get("nuclear", "").title(),
        "familia_ampliada": family_keys.get("broad", "").title(),
        "identificadores_texto": row.get("identificadores_texto") or "",
    }


def build_contributor_family_groups(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates = [row for row in rows if moneyless_int(row.get("sugestao_integracao"))]
    if len(candidates) < 2:
        return []
    groups: list[dict[str, Any]] = []
    used_ids: set[int] = set()
    nuclear_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in candidates:
        key = contributor_family_keys(row.get("nome")).get("nuclear")
        if key:
            nuclear_map[key].append(row)
    for key, members in sorted(nuclear_map.items(), key=lambda item: (-len(item[1]), item[0])):
        unique = []
        seen: set[int] = set()
        for member in members:
            contributor_id = moneyless_int(member.get("id"))
            if contributor_id and contributor_id not in seen:
                seen.add(contributor_id)
                unique.append(member)
        if len(unique) < 2:
            continue
        groups.append({"scope": "nuclear", "label": key.title(), "members": unique})
        used_ids.update(moneyless_int(member.get("id")) for member in unique)
    broad_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in candidates:
        contributor_id = moneyless_int(row.get("id"))
        if contributor_id in used_ids:
            continue
        key = contributor_family_keys(row.get("nome")).get("broad")
        if key:
            broad_map[key].append(row)
    for key, members in sorted(broad_map.items(), key=lambda item: (-len(item[1]), item[0])):
        unique = []
        seen: set[int] = set()
        for member in members:
            contributor_id = moneyless_int(member.get("id"))
            if contributor_id and contributor_id not in seen:
                seen.add(contributor_id)
                unique.append(member)
        if len(unique) >= 2:
            groups.append({"scope": "ampliada", "label": key.title(), "members": unique})
    groups.sort(key=lambda item: (-len(item["members"]), 0 if item["scope"] == "nuclear" else 1, str(item["label"])))
    return groups


def build_contributor_family_links(
    contributors: list[dict[str, Any]],
    people_rows: list[dict[str, Any]],
    limit_people: int = 6,
) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for contributor in contributors:
        if not moneyless_int(contributor.get("sugestao_integracao")):
            continue
        keys = contributor_family_keys(contributor.get("nome"))
        if not keys.get("broad") and not keys.get("nuclear"):
            continue
        matches: list[dict[str, Any]] = []
        seen_ids: set[int] = set()
        for person in people_rows:
            person_id = moneyless_int(person.get("id"))
            if not person_id or person_id in seen_ids:
                continue
            person_keys = contributor_family_keys(person.get("nome"))
            relation = ""
            if keys.get("nuclear") and keys.get("nuclear") == person_keys.get("nuclear"):
                relation = "nuclear"
            elif keys.get("broad") and keys.get("broad") == person_keys.get("broad"):
                relation = "ampliada"
            if not relation:
                continue
            seen_ids.add(person_id)
            matches.append(
                {
                    "id": person_id,
                    "nome": person.get("nome") or "",
                    "status": person.get("status") or "",
                    "status_label": format_status(person.get("status")),
                    "sigla": status_sigla(person.get("status"), True),
                    "codigo_interno": person.get("codigo_interno") or "",
                    "cpf": format_cpf(person.get("cpf")),
                    "relation": relation,
                }
            )
        if not matches:
            continue
        matches.sort(key=lambda item: (0 if item["relation"] == "nuclear" else 1, str(item["nome"])))
        block = {"contributor": contributor, "matches": matches[:limit_people], "matches_count": len(matches)}
        block["smart_audit"] = classify_contributor_link_block(block)
        blocks.append(block)
    blocks.sort(
        key=lambda item: (
            -moneyless_int(item["contributor"].get("prioridade_integracao")),
            -moneyless_int(item["contributor"].get("recorrencia_competencias")),
            -moneyless_int(item["contributor"].get("recorrencia_semanas")),
            -float(item["contributor"].get("total_contribuido") or 0),
            str(item["contributor"].get("nome")),
        )
    )
    return blocks


def list_contributors(
    q: str = "",
    status: str = "",
    tipo: str = "",
    mode: str = "todos",
    tags: list[str] | tuple[str, ...] | set[str] | None = None,
    section: str = "",
    limit: int | None = None,
) -> dict[str, Any]:
    q = (q or "").strip()
    status = (status or "").strip()
    tipo = (tipo or "").strip()
    mode = (mode or "todos").strip() or "todos"
    section = (section or "").strip()
    tag_set = {normalize_query(item).lower() for item in (tags or []) if normalize_query(item)}
    with connect_legacy() as conn:
        rows = conn.execute(
            """
            WITH contrib_stats AS (
                SELECT co.contribuinte_id,
                       COUNT(*) AS contribuicoes_qtd,
                       COALESCE(SUM(co.valor), 0) AS total_contribuido,
                       MIN(co.data_recebimento) AS primeira_contribuicao,
                       MAX(co.data_recebimento) AS ultima_contribuicao,
                       COUNT(DISTINCT CASE WHEN COALESCE(co.competencia, '') <> '' THEN co.competencia END) AS competencias_qtd,
                       COUNT(DISTINCT CASE WHEN COALESCE(co.data_recebimento, '') <> '' THEN strftime('%Y-%W', co.data_recebimento) END) AS semanas_qtd,
                       COUNT(DISTINCT CASE WHEN COALESCE(co.data_recebimento, '') <> '' THEN substr(co.data_recebimento, 1, 7) END) AS meses_recebimento_qtd,
                       SUM(CASE WHEN co.pessoa_id IS NULL THEN 1 ELSE 0 END) AS contribuicoes_sem_pessoa
                  FROM contribuicoes co
                 WHERE co.ativo = 1 AND co.contribuinte_id IS NOT NULL
                 GROUP BY co.contribuinte_id
            ),
            identifier_stats AS (
                SELECT ci.contribuinte_id,
                       GROUP_CONCAT(DISTINCT ci.valor) AS identificadores_texto
                  FROM contribuintes_identificadores ci
                 WHERE ci.ativo = 1 AND ci.contribuinte_id IS NOT NULL
                 GROUP BY ci.contribuinte_id
            ),
            pix_stats AS (
                SELECT contributor_id AS contribuinte_id,
                       SUM(
                           CASE
                               WHEN review_status IN ('revisar_pessoa', 'revisar_destinacao')
                                    OR (review_status = 'revisar_duplicidade' AND COALESCE(imported_contribution_id, 0) = 0)
                               THEN 1
                               ELSE 0
                           END
                       ) AS pix_pendentes,
                       SUM(CASE WHEN review_status = 'revisar_pessoa' THEN 1 ELSE 0 END) AS pix_pendentes_pessoa,
                       SUM(CASE WHEN review_status = 'revisar_destinacao' THEN 1 ELSE 0 END) AS pix_pendentes_destinacao,
                       SUM(
                           CASE
                               WHEN review_status = 'revisar_duplicidade' AND COALESCE(imported_contribution_id, 0) = 0
                               THEN 1
                               ELSE 0
                           END
                       ) AS pix_pendentes_duplicidade
                  FROM (
                        SELECT COALESCE(resolved_contribuinte_id, suggested_contribuinte_id) AS contributor_id,
                               review_status,
                               imported_contribution_id
                          FROM pix_movimentos
                         WHERE ativo = 1
                           AND COALESCE(resolved_contribuinte_id, suggested_contribuinte_id) IS NOT NULL
                  )
                 GROUP BY contributor_id
            )
            SELECT c.id, c.nome, c.documento_principal, c.documento_tipo, c.tipo, c.status,
                   c.origem, c.qualidade, p.nome AS pessoa_nome, p.status AS pessoa_status,
                   c.pessoa_id,
                   COALESCE(cs.contribuicoes_qtd, 0) AS contribuicoes_qtd,
                   COALESCE(cs.total_contribuido, 0) AS total_contribuido,
                   cs.primeira_contribuicao,
                   cs.ultima_contribuicao,
                   COALESCE(cs.competencias_qtd, 0) AS competencias_qtd,
                   COALESCE(cs.semanas_qtd, 0) AS semanas_qtd,
                   COALESCE(cs.meses_recebimento_qtd, 0) AS meses_recebimento_qtd,
                   COALESCE(cs.contribuicoes_sem_pessoa, 0) AS contribuicoes_sem_pessoa,
                   COALESCE(ps.pix_pendentes, 0) AS pix_pendentes,
                   COALESCE(ps.pix_pendentes_pessoa, 0) AS pix_pendentes_pessoa,
                   COALESCE(ps.pix_pendentes_destinacao, 0) AS pix_pendentes_destinacao,
                   COALESCE(ps.pix_pendentes_duplicidade, 0) AS pix_pendentes_duplicidade,
                   ids.identificadores_texto
              FROM contribuintes c
              LEFT JOIN pessoas p ON p.id = c.pessoa_id
              LEFT JOIN contrib_stats cs ON cs.contribuinte_id = c.id
              LEFT JOIN pix_stats ps ON ps.contribuinte_id = c.id
              LEFT JOIN identifier_stats ids ON ids.contribuinte_id = c.id
             WHERE c.ativo = 1
             ORDER BY c.nome COLLATE NOCASE ASC, c.id ASC
            """,
        ).fetchall()
        status_options = [
            {"value": row["status"], "count": int(row["total"] or 0)}
            for row in conn.execute(
                """
                SELECT COALESCE(status, '') AS status, COUNT(*) AS total
                  FROM contribuintes
                 WHERE ativo = 1
                 GROUP BY COALESCE(status, '')
                 ORDER BY total DESC, status ASC
                """
            ).fetchall()
        ]
        type_options = [
            {"value": row["tipo"], "count": int(row["total"] or 0)}
            for row in conn.execute(
                """
                SELECT COALESCE(tipo, '') AS tipo, COUNT(*) AS total
                  FROM contribuintes
                 WHERE ativo = 1
                 GROUP BY COALESCE(tipo, '')
                ORDER BY total DESC, tipo ASC
                """
            ).fetchall()
        ]
        people_rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT id, nome, status, codigo_interno, cpf
                  FROM pessoas
                 WHERE ativo = 1
                 ORDER BY nome COLLATE NOCASE
                """
            ).fetchall()
        ]

    all_items = []
    for row in rows:
        raw = dict(row)
        raw["pendencias_total"] = moneyless_int(raw.get("pix_pendentes")) + moneyless_int(raw.get("contribuicoes_sem_pessoa"))
        all_items.append(_format_dashboard_contributor(raw))

    positive_names = {
        normalize_match_name(item["nome"])
        for item in all_items
        if normalize_match_name(item["nome"])
        and (
            moneyless_int(item.get("contribuicoes_qtd")) > 0
            or float(item.get("total_contribuido") or 0) > 0
            or moneyless_int(item.get("pendencias_total")) > 0
        )
    }

    def is_shadow_identity(item: dict[str, Any]) -> bool:
        name_key = normalize_match_name(item["nome"])
        if not name_key or name_key not in positive_names:
            return False
        return (
            moneyless_int(item.get("contribuicoes_qtd")) == 0
            and float(item.get("total_contribuido") or 0) == 0
            and moneyless_int(item.get("pendencias_total")) == 0
        )

    operational_items = [item for item in all_items if not is_shadow_identity(item)]

    def matches_query(item: dict[str, Any]) -> bool:
        if not q:
            return True
        query_text = normalize_query(q)
        query_lower = query_text.lower()
        if query_text.isdigit() and moneyless_int(query_text) == moneyless_int(item.get("id")):
            return True
        text_candidates = [
            item.get("nome"),
            item.get("documento_principal"),
            item.get("pessoa_nome"),
            item.get("identificadores_texto"),
        ]
        if any(query_lower in normalize_query(value).lower() for value in text_candidates if value):
            return True
        if document_query_matches(query_text, item.get("documento_principal")):
            return True
        identifiers = [part.strip() for part in str(item.get("identificadores_texto") or "").split(",") if part.strip()]
        return any(document_query_matches(query_text, identifier) for identifier in identifiers)

    filtered = []
    for item in operational_items:
        if status and item["status"] != status:
            continue
        if tipo and item["tipo"] != tipo:
            continue
        if mode == "pendentes" and moneyless_int(item["pendencias_total"]) <= 0:
            continue
        if mode == "nao_lancados" and moneyless_int(item["pix_pendentes"]) <= 0:
            continue
        if mode == "sem_pessoa" and moneyless_int(item["contribuicoes_sem_pessoa"]) <= 0:
            continue
        if mode == "recorrentes" and not moneyless_int(item["sugestao_integracao"]):
            continue
        if "pf" in tag_set and item["tipo"] != "pf":
            continue
        if "pj" in tag_set and item["tipo"] != "pj":
            continue
        if "vinculado" in tag_set and moneyless_int(item["pessoa_id"]) <= 0:
            continue
        if "sem_vinculo" in tag_set and moneyless_int(item["pessoa_id"]) > 0:
            continue
        if "recorrente" in tag_set and moneyless_int(item["contribuicoes_qtd"]) < 2:
            continue
        if "semanal" in tag_set and moneyless_int(item["recorrencia_semanal"]) <= 0:
            continue
        if "multicompetencia" in tag_set and moneyless_int(item["recorrencia_multicompetencia"]) <= 0:
            continue
        if "integracao" in tag_set and moneyless_int(item["sugestao_integracao"]) <= 0:
            continue
        if "pendencias" in tag_set and moneyless_int(item["pendencias_total"]) <= 0:
            continue
        if "pix_saneamento" in tag_set and moneyless_int(item["pix_pendentes"]) <= 0:
            continue
        if "sem_pessoa" in tag_set and moneyless_int(item["contribuicoes_sem_pessoa"]) <= 0:
            continue
        if "familia_sugerida" in tag_set and moneyless_int(item["familia_sugerida"]) <= 0:
            continue
        if not matches_query(item):
            continue
        filtered.append(item)

    filtered.sort(
        key=lambda item: (
            0 if item.get("group_kind") == "nome" else 1,
            str(item.get("sort_key") or normalize_match_name(item["nome"])),
            moneyless_int(item["id"]),
            str(item["nome"]).casefold(),
        )
    )

    limit_value = moneyless_int(limit) if limit is not None else 0
    limited_items = filtered[:limit_value] if limit_value > 0 else list(filtered)
    family_groups = build_contributor_family_groups(filtered)
    family_links = build_contributor_family_links(filtered, people_rows)
    summary_source = operational_items
    tag_options = [
        {"value": "integracao", "label": "Sugerir integracao", "selected": "integracao" in tag_set},
        {"value": "familia_sugerida", "label": "Familia sugerida", "selected": "familia_sugerida" in tag_set},
        {"value": "recorrente", "label": "Recorrente", "selected": "recorrente" in tag_set},
        {"value": "semanal", "label": "Recorrencia semanal", "selected": "semanal" in tag_set},
        {"value": "multicompetencia", "label": "Multicompetencia", "selected": "multicompetencia" in tag_set},
        {"value": "pendencias", "label": "Com pendencias", "selected": "pendencias" in tag_set},
        {"value": "pix_saneamento", "label": "PIX em saneamento", "selected": "pix_saneamento" in tag_set},
        {"value": "sem_pessoa", "label": "Contribuicoes sem pessoa", "selected": "sem_pessoa" in tag_set},
        {"value": "sem_vinculo", "label": "Sem vinculo", "selected": "sem_vinculo" in tag_set},
        {"value": "vinculado", "label": "Vinculado", "selected": "vinculado" in tag_set},
        {"value": "pf", "label": "Somente PF", "selected": "pf" in tag_set},
        {"value": "pj", "label": "Somente PJ", "selected": "pj" in tag_set},
    ]
    return {
        "items": limited_items,
        "all_filtered_items": filtered,
        "total": len(filtered),
        "shown": len(limited_items),
        "q": q,
        "status": status,
        "tipo": tipo,
        "mode": mode,
        "section": section,
        "tags": sorted(tag_set),
        "tag_options": tag_options,
        "status_options": status_options,
        "type_options": type_options,
        "family_groups": family_groups,
        "family_links": family_links,
        "family_links_smart_summary": summarize_smart_audit(family_links),
        "summary": {
            "total": len(summary_source),
            "linked": sum(1 for item in summary_source if moneyless_int(item["pessoa_id"])),
            "pf": sum(1 for item in summary_source if item["tipo"] == "pf"),
            "pj": sum(1 for item in summary_source if item["tipo"] == "pj"),
            "pending_contributors": sum(1 for item in summary_source if moneyless_int(item["pendencias_total"]) > 0),
            "pending_unlaunched": sum(1 for item in summary_source if moneyless_int(item["pix_pendentes"]) > 0),
            "pending_without_person": sum(1 for item in summary_source if moneyless_int(item["contribuicoes_sem_pessoa"]) > 0),
            "recurring_unlinked": sum(1 for item in summary_source if moneyless_int(item["sugestao_integracao"]) > 0),
            "family_links": len(family_links),
            "family_groups": len(family_groups),
        },
        "limit": limit_value or len(filtered),
    }


def contributor_possible_people(contributor_id: int, limit: int = 12) -> list[dict[str, Any]]:
    with connect_legacy() as conn:
        contributor = conn.execute("SELECT * FROM contribuintes WHERE id = ?", (contributor_id,)).fetchone()
        if contributor is None:
            return []
        doc_value = str(contributor["documento_principal"] or "")
        doc_digits = "".join(ch for ch in doc_value if ch.isdigit())
        contributor_norm = normalize_match_name(contributor["nome"])
        people = conn.execute(
            """
            SELECT id, nome, status, codigo_interno, cpf
              FROM pessoas
             WHERE organizacao_id = ? AND ativo = 1
             ORDER BY nome COLLATE NOCASE
            """,
            (contributor["organizacao_id"],),
        ).fetchall()
    rows: list[dict[str, Any]] = []
    for person in people:
        person_norm = normalize_match_name(person["nome"])
        exact_name = bool(contributor_norm and contributor_norm == person_norm)
        doc_match = bool(doc_digits and clean_digits(person["cpf"]) == doc_digits)
        ratio = SequenceMatcher(None, contributor_norm, person_norm).ratio() if contributor_norm and person_norm else 0.0
        if not doc_match and not exact_name and ratio < 0.72:
            continue
        if doc_match and exact_name:
            score = 0.99
            reason = "Documento e nome coincidem com a ficha."
        elif doc_match:
            score = 0.96
            reason = "Documento coincide com a ficha."
        elif exact_name:
            score = 0.93
            reason = "Nome financeiro coincide integralmente."
        else:
            score = ratio
            reason = f"Nome com semelhanca relevante ({ratio:.2f})."
        rows.append(
            {
                "id": moneyless_int(person["id"]),
                "nome": person["nome"] or "",
                "status": person["status"] or "",
                "status_label": format_status(person["status"]),
                "sigla": status_sigla(person["status"], True),
                "codigo_interno": person["codigo_interno"] or "",
                "cpf": format_cpf(person["cpf"]),
                "score": round(score, 4),
                "reason": reason,
            }
        )
    rows.sort(key=lambda item: (-float(item["score"]), str(item["nome"])))
    return rows[:limit]


def clean_digits(value: object) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def get_contributor_detail(contributor_id: int) -> dict[str, Any] | None:
    with connect_legacy() as conn:
        contributor = conn.execute(
            """
            SELECT c.id, c.pessoa_id, c.tipo, c.nome, c.documento_principal, c.documento_tipo,
                   c.origem, c.qualidade, c.status, c.observacoes, c.criado_em, c.atualizado_em,
                   p.nome AS pessoa_nome, p.status AS pessoa_status, p.cpf AS pessoa_cpf
              FROM contribuintes c
              LEFT JOIN pessoas p ON p.id = c.pessoa_id
             WHERE c.id = ?
            """,
            (contributor_id,),
        ).fetchone()
        if contributor is None:
            return None
        identifiers = conn.execute(
            """
            SELECT tipo, valor, principal, observacoes
              FROM contribuintes_identificadores
             WHERE ativo = 1 AND contribuinte_id = ?
             ORDER BY principal DESC, tipo COLLATE NOCASE, valor COLLATE NOCASE
             LIMIT 30
            """,
            (contributor_id,),
        ).fetchall()
        contributions = conn.execute(
            """
            SELECT co.id, co.data_recebimento, co.competencia, co.valor,
                   COALESCE(co.status_operacional, '') AS status_operacional,
                   COALESCE(t.nome, '') AS tipo_nome,
                   COALESCE(f.nome, '') AS forma_nome,
                   COALESCE(p.nome, '') AS origem_nome
              FROM contribuicoes co
              LEFT JOIN tipos_contribuicao t ON t.id = co.tipo_contribuicao_id
              LEFT JOIN formas_recebimento f ON f.id = co.forma_recebimento_id
              LEFT JOIN pessoas p ON p.id = co.pessoa_id
             WHERE co.ativo = 1 AND co.contribuinte_id = ?
             ORDER BY COALESCE(co.competencia_ordem, 0) DESC,
                      COALESCE(co.data_recebimento, '') DESC,
                      co.id DESC
             LIMIT 60
            """,
            (contributor_id,),
        ).fetchall()
        summary = conn.execute(
            """
            SELECT COALESCE(competencia, '') AS competencia,
                   COUNT(*) AS remessas,
                   COALESCE(SUM(valor), 0) AS total_valor,
                   MAX(COALESCE(competencia_ordem, 0)) AS ordem
              FROM contribuicoes
             WHERE ativo = 1 AND contribuinte_id = ?
             GROUP BY COALESCE(competencia, '')
             ORDER BY ordem DESC, competencia DESC
             LIMIT 12
            """,
            (contributor_id,),
        ).fetchall()
        total_value = float(
            scalar(
                conn,
                "SELECT COALESCE(SUM(valor), 0) FROM contribuicoes WHERE ativo = 1 AND contribuinte_id = ?",
                (contributor_id,),
            )
            or 0
        )
    return {
        "contributor": {
            "id": contributor["id"],
            "person_id": contributor["pessoa_id"],
            "tipo": (contributor["tipo"] or "").upper(),
            "nome": contributor["nome"] or "",
            "documento": contributor["documento_principal"] or "",
            "documento_tipo": contributor["documento_tipo"] or "",
            "origem": contributor["origem"] or "",
            "qualidade": contributor["qualidade"] or "",
            "status": contributor["status"] or "",
            "observacoes": contributor["observacoes"] or "",
            "criado_em": br_datetime(contributor["criado_em"]),
            "atualizado_em": br_datetime(contributor["atualizado_em"]),
            "pessoa_nome": contributor["pessoa_nome"] or "",
            "pessoa_sigla": status_sigla(contributor["pessoa_status"], bool(contributor["pessoa_id"])),
            "pessoa_cpf": contributor["pessoa_cpf"] or "",
        },
        "identifiers": [dict(row) for row in identifiers],
        "possible_people": contributor_possible_people(contributor_id),
        "contributions": [_format_contribution_row(row) for row in contributions],
        "summary": [
            {
                "competencia": row["competencia"] or "Sem competencia",
                "remessas": int(row["remessas"] or 0),
                "total_fmt": _money(row["total_valor"]),
            }
            for row in summary
        ],
        "total_contributions_fmt": _money(total_value),
    }


def list_import_lots(limit: int = 80) -> dict[str, Any]:
    lots: list[dict[str, Any]] = []
    with connect_legacy() as conn:
        for row in conn.execute(
            f"""
            SELECT l.id, l.banco, l.layout_codigo, l.nome_arquivo, l.periodo_inicio, l.periodo_fim,
                   l.total_movimentos, l.total_valor, l.status, l.criado_em,
                   (
                       SELECT COUNT(*)
                         FROM extrato_movimentos m
                        WHERE m.lote_id = l.id
                          AND m.ativo = 1
                          AND {human_pending_review_sql('m')}
                   ) AS pendentes,
                   (
                       SELECT COUNT(*)
                         FROM extrato_movimentos m
                        WHERE m.lote_id = l.id
                          AND m.ativo = 1
                          AND m.review_status = 'ignorado'
                   ) AS ignorados
              FROM extrato_lotes l
            """,
        ).fetchall():
            lots.append(_format_lot_row(row, "Extrato"))
        for row in conn.execute(
            f"""
            SELECT l.id, l.banco, 'sicoob_pix' AS layout_codigo, l.nome_arquivo, l.periodo_inicio, l.periodo_fim,
                   l.total_movimentos, l.total_valor, l.status, l.criado_em,
                   (
                       SELECT COUNT(*)
                         FROM pix_movimentos m
                        WHERE m.lote_id = l.id
                          AND m.ativo = 1
                          AND {human_pending_review_sql('m')}
                   ) AS pendentes,
                   (
                       SELECT COUNT(*)
                         FROM pix_movimentos m
                        WHERE m.lote_id = l.id
                          AND m.ativo = 1
                          AND m.review_status = 'ignorado'
                   ) AS ignorados
              FROM pix_lotes l
            """,
        ).fetchall():
            lots.append(_format_lot_row(row, "PIX"))
    lots.sort(key=lambda item: (item["criado_em_raw"], item["id"]), reverse=True)
    return {"items": lots[:limit], "total": len(lots), "shown": min(len(lots), limit), "limit": limit}


def cent_rules_data(edit_rule_id: int = 0) -> dict[str, Any]:
    with connect_legacy() as conn:
        organization_id = int(scalar(conn, "SELECT id FROM organizacoes ORDER BY id LIMIT 1") or 1)
        rules = conn.execute(
            """
            SELECT
                r.id,
                r.codigo_centavos,
                r.nome_destinacao,
                r.tipo_contribuicao_id,
                r.campanha_id,
                r.plano_conta_id,
                r.ativo,
                tc.nome AS tipo_nome,
                tc.codigo AS tipo_codigo,
                ca.nome AS campanha_nome,
                pc.codigo AS plano_conta_codigo,
                pc.nome AS plano_conta_nome
              FROM pix_centavo_regras r
              LEFT JOIN tipos_contribuicao tc ON tc.id = r.tipo_contribuicao_id
              LEFT JOIN campanhas ca ON ca.id = r.campanha_id
              LEFT JOIN plano_contas pc ON pc.id = COALESCE(r.plano_conta_id, ca.plano_conta_id, tc.plano_conta_id)
             WHERE r.organizacao_id = ?
             ORDER BY r.codigo_centavos
            """,
            (organization_id,),
        ).fetchall()
        types = conn.execute(
            """
            SELECT id, codigo, nome
              FROM tipos_contribuicao
             WHERE organizacao_id = ? AND ativo = 1
             ORDER BY CASE WHEN codigo = 'DIZIMO' THEN 0 ELSE 1 END, nome COLLATE NOCASE
            """,
            (organization_id,),
        ).fetchall()

    formatted_rules = [
        {
            "id": moneyless_int(row["id"]),
            "codigo": str(row["codigo_centavos"] or "").zfill(2),
            "nome": row["nome_destinacao"] or "",
            "tipo_id": moneyless_int(row["tipo_contribuicao_id"]),
            "tipo_nome": row["tipo_nome"] or "Sem tipo vinculado",
            "tipo_codigo": row["tipo_codigo"] or "",
            "campanha_nome": row["campanha_nome"] or "",
            "conta_codigo": row["plano_conta_codigo"] or "",
            "conta_nome": row["plano_conta_nome"] or "",
            "ativo": bool(row["ativo"]),
        }
        for row in rules
    ]
    current = next((rule for rule in formatted_rules if rule["id"] == moneyless_int(edit_rule_id)), None)
    return {
        "rules": formatted_rules,
        "types": [
            {
                "id": moneyless_int(row["id"]),
                "codigo": row["codigo"] or "",
                "nome": row["nome"] or "",
                "selected": bool(current and moneyless_int(row["id"]) == current["tipo_id"]),
            }
            for row in types
        ],
        "current": current,
        "edit_rule_id": moneyless_int(edit_rule_id),
        "active_count": sum(1 for rule in formatted_rules if rule["ativo"]),
    }


def _format_lot_row(row: sqlite3.Row, tipo: str) -> dict[str, Any]:
    periodo = ""
    if row["periodo_inicio"] or row["periodo_fim"]:
        periodo = f"{br_date(row['periodo_inicio'])} a {br_date(row['periodo_fim'])}".strip()
    return {
        "id": row["id"],
        "tipo": tipo,
        "banco": row["banco"] or "",
        "layout": row["layout_codigo"] or "",
        "nome_arquivo": row["nome_arquivo"] or "",
        "periodo": periodo or "Sem periodo",
        "movimentos": int(row["total_movimentos"] or 0),
        "total_fmt": _money(row["total_valor"]),
        "status": row["status"] or "",
        "pendentes": int(row["pendentes"] or 0),
        "ignorados": int(row["ignorados"] or 0),
        "criado_em": br_datetime(row["criado_em"]),
        "criado_em_raw": row["criado_em"] or "",
    }


def get_import_lot_detail(kind: str, lot_id: int, status: str = "", limit: int = 500) -> dict[str, Any] | None:
    kind = "pix" if kind == "pix" else "statement"
    status = (status or "").strip()
    with connect_legacy() as conn:
        if kind == "pix":
            lot = conn.execute(
                """
                SELECT id, banco, 'sicoob_pix' AS layout_codigo, nome_arquivo, periodo_inicio,
                       periodo_fim, total_movimentos, total_valor, status, criado_em
                  FROM pix_lotes
                 WHERE id = ?
                """,
                (lot_id,),
            ).fetchone()
            if lot is None:
                return None
            clauses = ["m.lote_id = ?", "m.ativo = 1"]
            params: list[Any] = [lot_id]
            if status == "pendencias":
                clauses.append(human_pending_review_sql("m"))
            elif status:
                clauses.append("m.review_status = ?")
                params.append(status)
            where = " AND ".join(clauses)
            movements = conn.execute(
                f"""
                SELECT m.id, m.ordem_no_lote, m.data_recebimento AS data_movimento,
                       m.competencia, m.valor, m.nome_origem, m.documento_mascarado,
                       m.documento_tipo, m.confidence, m.match_score, m.review_status,
                       m.tipo_sugerido, m.codigo_centavos,
                       m.suggested_person_id, m.resolved_person_id,
                       sp.nome AS suggested_person_name,
                       sp.cpf AS suggested_person_cpf,
                       rp.nome AS resolved_person_name,
                       rp.cpf AS resolved_person_cpf,
                       sc.nome AS suggested_contributor_name,
                       rc.nome AS resolved_contributor_name,
                       m.imported_contribution_id
                  FROM pix_movimentos m
                  LEFT JOIN pessoas sp ON sp.id = m.suggested_person_id
                  LEFT JOIN pessoas rp ON rp.id = m.resolved_person_id
                  LEFT JOIN contribuintes sc ON sc.id = m.suggested_contribuinte_id
                  LEFT JOIN contribuintes rc ON rc.id = m.resolved_contribuinte_id
                 WHERE {where}
                 ORDER BY COALESCE(m.data_recebimento, '') ASC, m.ordem_no_lote ASC, m.id ASC
                 LIMIT ?
                """,
                (*params, limit),
            ).fetchall()
            status_rows = conn.execute(
                """
                SELECT review_status, COUNT(*) AS total
                  FROM pix_movimentos
                 WHERE lote_id = ? AND ativo = 1
                 GROUP BY review_status
                 ORDER BY total DESC
                """,
                (lot_id,),
            ).fetchall()
            pending_count = int(
                scalar(
                    conn,
                    f"""
                    SELECT COUNT(*)
                      FROM pix_movimentos m
                     WHERE m.lote_id = ?
                       AND m.ativo = 1
                       AND {human_pending_review_sql('m')}
                    """,
                    (lot_id,),
                )
                or 0
            )
        else:
            lot = conn.execute(
                """
                SELECT id, banco, layout_codigo, nome_arquivo, periodo_inicio,
                       periodo_fim, total_movimentos, total_valor, status, criado_em
                  FROM extrato_lotes
                 WHERE id = ?
                """,
                (lot_id,),
            ).fetchone()
            if lot is None:
                return None
            clauses = ["m.lote_id = ?", "m.ativo = 1"]
            params = [lot_id]
            if status == "pendencias":
                clauses.append(human_pending_review_sql("m"))
            elif status:
                clauses.append("m.review_status = ?")
                params.append(status)
            where = " AND ".join(clauses)
            movements = conn.execute(
                f"""
                SELECT m.id, m.ordem_no_lote, m.data_movimento,
                       m.competencia, m.valor, m.nome_origem, m.bank_document AS documento_mascarado,
                       m.movement_kind AS documento_tipo, m.confidence, m.match_score, m.review_status,
                       m.tipo_sugerido, m.codigo_centavos,
                       m.suggested_person_id, m.resolved_person_id,
                       sp.nome AS suggested_person_name,
                       sp.cpf AS suggested_person_cpf,
                       rp.nome AS resolved_person_name,
                       rp.cpf AS resolved_person_cpf,
                       sc.nome AS suggested_contributor_name,
                       rc.nome AS resolved_contributor_name,
                       m.imported_contribution_id
                  FROM extrato_movimentos m
                  LEFT JOIN pessoas sp ON sp.id = m.suggested_person_id
                  LEFT JOIN pessoas rp ON rp.id = m.resolved_person_id
                  LEFT JOIN contribuintes sc ON sc.id = m.suggested_contribuinte_id
                  LEFT JOIN contribuintes rc ON rc.id = m.resolved_contribuinte_id
                 WHERE {where}
                 ORDER BY COALESCE(m.data_movimento, '') ASC, m.ordem_no_lote ASC, m.id ASC
                 LIMIT ?
                """,
                (*params, limit),
            ).fetchall()
            status_rows = conn.execute(
                """
                SELECT review_status, COUNT(*) AS total
                  FROM extrato_movimentos
                 WHERE lote_id = ? AND ativo = 1
                 GROUP BY review_status
                 ORDER BY total DESC
                """,
                (lot_id,),
            ).fetchall()
            pending_count = int(
                scalar(
                    conn,
                    f"""
                    SELECT COUNT(*)
                      FROM extrato_movimentos m
                     WHERE m.lote_id = ?
                       AND m.ativo = 1
                       AND {human_pending_review_sql('m')}
                    """,
                    (lot_id,),
                )
                or 0
            )
    status_options = [{"value": "pendencias", "count": pending_count}]
    status_options.extend(
        {"value": row["review_status"] or "", "count": int(row["total"] or 0)}
        for row in status_rows
    )
    formatted_movements = [_format_bank_movement_brief(kind, row) for row in movements]
    if status:
        return_to = quote(f"/imports/{kind}/{lot_id}/?status={status}", safe="")
        for movement in formatted_movements:
            movement["detail_url"] = f"{movement['detail_url']}?return_to={return_to}"
    return {
        "kind": kind,
        "kind_label": "PIX" if kind == "pix" else "Extrato",
        "lot": _format_lot_row_with_raw(lot, "PIX" if kind == "pix" else "Extrato"),
        "status": status,
        "status_options": status_options,
        "movements": formatted_movements,
        "shown": len(movements),
        "limit": limit,
    }


def _format_lot_row_with_raw(row: sqlite3.Row, tipo: str) -> dict[str, Any]:
    base = _format_lot_row(
        {
            "id": row["id"],
            "banco": row["banco"],
            "layout_codigo": row["layout_codigo"],
            "nome_arquivo": row["nome_arquivo"],
            "periodo_inicio": row["periodo_inicio"],
            "periodo_fim": row["periodo_fim"],
            "total_movimentos": row["total_movimentos"],
            "total_valor": row["total_valor"],
            "status": row["status"],
            "pendentes": 0,
            "ignorados": 0,
            "criado_em": row["criado_em"],
        },
        tipo,
    )
    return base


def _format_bank_movement_brief(kind: str, row: sqlite3.Row) -> dict[str, Any]:
    resolved_person = row["resolved_person_name"] or ""
    suggested_person = row["suggested_person_name"] or ""
    resolved_contributor = row["resolved_contributor_name"] or ""
    suggested_contributor = row["suggested_contributor_name"] or ""
    candidate_person_id = moneyless_int(row["resolved_person_id"]) or moneyless_int(row["suggested_person_id"])
    candidate_person_cpf = row["resolved_person_cpf"] or row["suggested_person_cpf"] or ""
    bank_document = row["documento_mascarado"] or ""
    bank_document_fmt = format_document(bank_document) if bank_document else ""
    candidate_document_fmt = format_document(candidate_person_cpf) if candidate_person_cpf else ""
    bank_document_digits = document_digits(bank_document)
    candidate_document_digits = document_digits(candidate_person_cpf)
    bank_document_invalid = len(bank_document_digits) == 11 and not valid_cpf(bank_document_digits)
    candidate_document_invalid = len(candidate_document_digits) == 11 and not valid_cpf(candidate_document_digits)
    document_match = ""
    document_match_label = ""
    if bank_document and candidate_person_cpf:
        matches = document_query_matches(bank_document, candidate_person_cpf)
        document_match = "ok" if matches and not (bank_document_invalid or candidate_document_invalid) else "warn"
        if matches and (bank_document_invalid or candidate_document_invalid):
            document_match_label = "confere, CPF invalido"
        else:
            document_match_label = "confere" if matches else "conferir"
    return {
        "id": row["id"],
        "detail_url": f"/imports/{kind}/movement/{row['id']}/",
        "ordem": row["ordem_no_lote"],
        "data": br_date(row["data_movimento"]),
        "competencia": row["competencia"] or "",
        "valor_fmt": _money(row["valor"]),
        "nome_origem": row["nome_origem"] or "Sem remetente",
        "documento": bank_document,
        "documento_fmt": bank_document_fmt,
        "documento_tipo": row["documento_tipo"] or "",
        "candidate_person_id": candidate_person_id,
        "candidate_person_cpf": candidate_person_cpf,
        "candidate_document_fmt": candidate_document_fmt,
        "bank_document_invalid": bank_document_invalid,
        "candidate_document_invalid": candidate_document_invalid,
        "document_match": document_match,
        "document_match_label": document_match_label,
        "confidence": row["confidence"] or "",
        "match_score": row["match_score"] or 0,
        "review_status": row["review_status"] or "",
        "tipo_sugerido": row["tipo_sugerido"] or "",
        "codigo_centavos": row["codigo_centavos"] or "",
        "resolved_person": resolved_person,
        "suggested_person": suggested_person,
        "resolved_contributor": resolved_contributor,
        "suggested_contributor": suggested_contributor,
        "resolved": resolved_person or resolved_contributor,
        "suggested": suggested_person or suggested_contributor,
        "imported_contribution_id": row["imported_contribution_id"],
    }


def _bank_person_option(row: sqlite3.Row, *, source: str, selected_person_id: int) -> dict[str, Any]:
    return {
        "id": moneyless_int(row["id"]),
        "nome": row["nome"] or "",
        "codigo": row["codigo_interno"] or "",
        "cpf": format_cpf(row["cpf"]),
        "status": format_status(row["status"]),
        "source": source,
        "checked": moneyless_int(row["id"]) == selected_person_id,
    }


def _contribution_type_options(conn: sqlite3.Connection, organization_id: int, selected_type_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, codigo, nome
          FROM tipos_contribuicao
         WHERE organizacao_id = ? AND ativo = 1
         ORDER BY CASE WHEN codigo = 'dizimo' THEN 0 ELSE 1 END, nome COLLATE NOCASE
        """,
        (organization_id,),
    ).fetchall()
    return [
        {
            "id": moneyless_int(row["id"]),
            "codigo": row["codigo"] or "",
            "nome": row["nome"] or "",
            "selected": moneyless_int(row["id"]) == selected_type_id,
        }
        for row in rows
    ]


def _bank_person_options(
    conn: sqlite3.Connection,
    movement: sqlite3.Row,
    selected_person_id: int,
    lookup: str,
) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    seen: set[int] = set()

    def add_person(person_id: int, source: str) -> None:
        if not person_id or person_id in seen:
            return
        row = conn.execute(
            "SELECT id, codigo_interno, nome, cpf, status FROM pessoas WHERE id = ?",
            (person_id,),
        ).fetchone()
        if row is not None:
            seen.add(person_id)
            options.append(_bank_person_option(row, source=source, selected_person_id=selected_person_id))

    add_person(moneyless_int(movement["resolved_person_id"]), "ja resolvido")
    add_person(moneyless_int(movement["suggested_person_id"]), "sugerido pelo motor")

    lookup = normalize_query(lookup)
    if lookup:
        rows = conn.execute(
            """
            SELECT id, codigo_interno, nome, cpf, status
              FROM pessoas
             WHERE """
            + _person_search_clause()
            + """
             ORDER BY nome COLLATE NOCASE ASC, id ASC
             LIMIT 80
            """,
            tuple(_person_search_params(lookup)),
        ).fetchall()
        for row in rows:
            person_id = moneyless_int(row["id"])
            if person_id not in seen:
                seen.add(person_id)
                options.append(_bank_person_option(row, source="busca ampla", selected_person_id=selected_person_id))
    return options


def get_bank_movement_detail(kind: str, movement_id: int, lookup: str = "") -> dict[str, Any] | None:
    kind = "pix" if kind == "pix" else "statement"
    table = "pix_movimentos" if kind == "pix" else "extrato_movimentos"
    lot_table = "pix_lotes" if kind == "pix" else "extrato_lotes"
    date_col = "data_recebimento" if kind == "pix" else "data_movimento"
    doc_col = "documento_mascarado" if kind == "pix" else "bank_document"
    type_col = "documento_tipo" if kind == "pix" else "movement_kind"
    with connect_legacy() as conn:
        movement = conn.execute(
            f"""
            SELECT m.*, m.{date_col} AS data_base, m.{doc_col} AS doc_base,
                   m.{type_col} AS doc_type_base,
                   l.banco, l.nome_arquivo,
                   sp.nome AS suggested_person_name,
                   rp.nome AS resolved_person_name,
                   sc.nome AS suggested_contributor_name,
                   rc.nome AS resolved_contributor_name,
                   co.id AS contribution_id,
                   co.status_operacional AS contribution_status,
                   co.tipo_contribuicao_id AS contribution_type_id
              FROM {table} m
              JOIN {lot_table} l ON l.id = m.lote_id
              LEFT JOIN pessoas sp ON sp.id = m.suggested_person_id
              LEFT JOIN pessoas rp ON rp.id = m.resolved_person_id
              LEFT JOIN contribuintes sc ON sc.id = m.suggested_contribuinte_id
              LEFT JOIN contribuintes rc ON rc.id = m.resolved_contribuinte_id
              LEFT JOIN contribuicoes co ON co.id = m.imported_contribution_id
             WHERE m.id = ?
            """,
            (movement_id,),
        ).fetchone()
        if movement is None:
            return None
        rule_type_id = 0
        if moneyless_int(movement["regra_id"]) and table_exists(conn, "pix_centavo_regras"):
            rule = conn.execute(
                "SELECT tipo_contribuicao_id FROM pix_centavo_regras WHERE id = ?",
                (moneyless_int(movement["regra_id"]),),
            ).fetchone()
            rule_type_id = moneyless_int(rule["tipo_contribuicao_id"] if rule else 0)
        selected_person_id = (
            0
            if moneyless_int(movement["association_reviewed"])
            else moneyless_int(movement["resolved_person_id"]) or moneyless_int(movement["suggested_person_id"])
        )
        selected_type_id = (
            moneyless_int(movement["resolved_tipo_contribuicao_id"])
            or moneyless_int(movement["contribution_type_id"])
            or rule_type_id
        )
        type_options = _contribution_type_options(conn, moneyless_int(movement["organizacao_id"]), selected_type_id)
        person_options = _bank_person_options(conn, movement, selected_person_id, lookup)
    return {
        "kind": kind,
        "kind_label": "PIX" if kind == "pix" else "Extrato",
        "lot_url": f"/imports/{kind}/{movement['lote_id']}/",
        "movement": {
            "id": movement["id"],
            "lote_id": movement["lote_id"],
            "banco": movement["banco"] or "",
            "nome_arquivo": movement["nome_arquivo"] or "",
            "ordem": movement["ordem_no_lote"],
            "pagina": movement["pagina"] or "",
            "data": br_date(movement["data_base"]),
            "competencia": movement["competencia"] or "",
            "valor_fmt": _money(movement["valor"]),
            "nome_origem": movement["nome_origem"] or "Sem remetente",
            "documento": movement["doc_base"] or "",
            "documento_tipo": movement["doc_type_base"] or "",
            "confidence": movement["confidence"] or "",
            "match_score": movement["match_score"] or 0,
            "review_status": movement["review_status"] or "",
            "tipo_sugerido": movement["tipo_sugerido"] or "",
            "codigo_centavos": movement["codigo_centavos"] or "",
            "selected_person_id": selected_person_id,
            "selected_type_id": selected_type_id,
            "suggested_person_id": moneyless_int(movement["suggested_person_id"]),
            "resolved_person_id": moneyless_int(movement["resolved_person_id"]),
            "association_reviewed": bool(moneyless_int(movement["association_reviewed"])),
            "suggested": movement["suggested_person_name"] or movement["suggested_contributor_name"] or "",
            "resolved": movement["resolved_person_name"] or movement["resolved_contributor_name"] or "",
            "contribution_id": movement["contribution_id"] or "",
            "contribution_status": movement["contribution_status"] or "",
            "raw_text": movement["raw_text"] or "",
            "review_notes": movement["review_notes"] or "",
        },
        "lookup": normalize_query(lookup),
        "person_options": person_options,
        "type_options": type_options,
        "can_same_owner": kind == "statement",
    }


def contribution_report(
    competencia: str = "",
    q: str = "",
    date_start: str = "",
    date_end: str = "",
    limit_rows: int = 5000,
) -> dict[str, Any]:
    competencia = (competencia or "").strip()
    q = (q or "").strip()
    date_start = (date_start or "").strip()
    date_end = (date_end or "").strip()
    clauses = ["co.ativo = 1"]
    params: list[Any] = []
    if competencia:
        clauses.append("COALESCE(co.competencia, '') = ?")
        params.append(competencia)
    if date_start:
        clauses.append("co.data_recebimento >= ?")
        params.append(date_start)
    if date_end:
        clauses.append("co.data_recebimento <= ?")
        params.append(date_end)
    if q:
        like = f"%{q}%"
        digits = "".join(ch for ch in q if ch.isdigit())
        clauses.append(
            """
            (
                COALESCE(p.nome, '') LIKE ?
                OR COALESCE(c.nome, '') LIKE ?
                OR COALESCE(p.codigo_interno, '') LIKE ?
                OR COALESCE(p.cpf, '') LIKE ?
                OR COALESCE(c.documento_principal, '') LIKE ?
            )
            """
        )
        params.extend([like, like, like, f"%{digits or q}%", f"%{digits or q}%"])
    where = " AND ".join(clauses)
    with connect_legacy() as conn:
        rows = conn.execute(
            f"""
            SELECT co.id, co.data_recebimento, co.competencia, co.valor,
                   COALESCE(p.nome, c.nome, 'Contribuinte nao vinculado') AS nome,
                   p.id AS pessoa_id,
                   p.status AS pessoa_status,
                   p.nome AS pessoa_nome,
                   c.id AS contribuinte_id,
                   c.nome AS contribuinte_nome,
                   c.documento_principal AS contribuinte_documento
              FROM contribuicoes co
              LEFT JOIN pessoas p ON p.id = co.pessoa_id
              LEFT JOIN contribuintes c ON c.id = co.contribuinte_id
             WHERE {where}
             ORDER BY nome COLLATE NOCASE ASC,
                      COALESCE(co.data_recebimento, '') ASC,
                      co.id ASC
             LIMIT ?
            """,
            (*params, limit_rows),
        ).fetchall()
    grouped: dict[tuple[str, str, int | None], dict[str, Any]] = {}
    for row in rows:
        has_person = bool(row["pessoa_id"])
        sigla = status_sigla(row["pessoa_status"], has_person)
        identity = contribution_report_identity(row["pessoa_nome"], row["contribuinte_nome"], row["contribuinte_documento"])
        display_name = identity["name"] or "Documento nao identificado"
        group_id = int(row["pessoa_id"] or row["contribuinte_id"] or 0)
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
        value = float(row["valor"] or 0)
        item["total"] += value
        item["total_fmt"] = _money(item["total"])
        item["remessas"].append(
            {
                "data": br_date(row["data_recebimento"]),
                "competencia": row["competencia"] or "",
                "valor_fmt": _money(value),
            }
        )
    items = list(grouped.values())
    items.sort(
        key=lambda item: (
            0 if item["group_kind"] == "nome" else 1,
            str(item["sort_key"]),
            str(item["documento"]),
        )
    )
    total_value = sum(item["total"] for item in items)
    sigla_counts = defaultdict(int)
    for item in items:
        sigla_counts[item["sigla"]] += 1
    named_items = [item for item in items if item["group_kind"] == "nome"]
    document_items = [item for item in items if item["group_kind"] == "documento"]
    return {
        "items": items,
        "named_items": named_items,
        "document_items": document_items,
        "competencia": competencia,
        "q": q,
        "date_start": date_start,
        "date_end": date_end,
        "competencias": list_competencias(),
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
    }


def _destination_from_contribution_row(row: sqlite3.Row) -> dict[str, Any]:
    campaign_id = moneyless_int(row["campanha_id"])
    if campaign_id:
        label = normalize_query(row["campanha_nome"]) or "Campanha sem nome"
        type_label = normalize_query(row["tipo_nome"])
        return {
            "key": f"campanha:{campaign_id}",
            "kind": "campanha",
            "kind_label": "Campanha",
            "label": label,
            "detail": f"Campanha: {label}",
            "tipo": type_label,
            "campanha": label,
        }
    type_id = moneyless_int(row["tipo_contribuicao_id"])
    label = normalize_query(row["tipo_nome"]) or "Sem destinacao"
    return {
        "key": f"tipo:{type_id}",
        "kind": "tipo",
        "kind_label": "Tipo",
        "label": label,
        "detail": f"Tipo: {label}",
        "tipo": label,
        "campanha": "",
    }


def _destination_filter(destination: str) -> tuple[str, list[Any], str]:
    destination = normalize_query(destination)
    if not destination or ":" not in destination:
        return "", [], ""
    kind, raw_id = destination.split(":", 1)
    destination_id = moneyless_int(raw_id)
    if not destination_id:
        return "", [], ""
    if kind == "campanha":
        return "co.campanha_id = ?", [destination_id], f"campanha:{destination_id}"
    if kind == "tipo":
        return "co.tipo_contribuicao_id = ? AND co.campanha_id IS NULL", [destination_id], f"tipo:{destination_id}"
    return "", [], ""


def _contribution_destination_options(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    contributors_by_destination: dict[str, set[tuple[object, object]]] = defaultdict(set)
    for row in rows:
        destination = _destination_from_contribution_row(row)
        item = grouped.setdefault(
            destination["key"],
            {
                **destination,
                "remessas": 0,
                "contribuintes": 0,
                "total": 0.0,
                "total_fmt": _money(0),
            },
        )
        item["remessas"] += 1
        item["total"] += float(row["valor"] or 0)
        item["total_fmt"] = _money(item["total"])
        contributors_by_destination[destination["key"]].add((row["pessoa_id"], row["contribuinte_id"]))
    for key, contributor_keys in contributors_by_destination.items():
        grouped[key]["contribuintes"] = len(contributor_keys)
    return sorted(
        grouped.values(),
        key=lambda item: (
            0 if item["key"] == "tipo:1" else 1,
            str(item["label"]).casefold(),
        ),
    )


def contribution_destination_report(
    competencia: str = "",
    q: str = "",
    date_start: str = "",
    date_end: str = "",
    destination: str = "",
    limit_rows: int = 10000,
) -> dict[str, Any]:
    competencia = (competencia or "").strip()
    q = (q or "").strip()
    date_start = (date_start or "").strip()
    date_end = (date_end or "").strip()
    destination = (destination or "").strip()
    base_clauses = ["co.ativo = 1"]
    base_params: list[Any] = []
    if competencia:
        base_clauses.append("COALESCE(co.competencia, '') = ?")
        base_params.append(competencia)
    if date_start:
        base_clauses.append("co.data_recebimento >= ?")
        base_params.append(date_start)
    if date_end:
        base_clauses.append("co.data_recebimento <= ?")
        base_params.append(date_end)
    if q:
        like = f"%{q}%"
        digits = "".join(ch for ch in q if ch.isdigit())
        base_clauses.append(
            """
            (
                COALESCE(p.nome, '') LIKE ?
                OR COALESCE(c.nome, '') LIKE ?
                OR COALESCE(p.codigo_interno, '') LIKE ?
                OR COALESCE(p.cpf, '') LIKE ?
                OR COALESCE(c.documento_principal, '') LIKE ?
                OR COALESCE(t.nome, '') LIKE ?
                OR COALESCE(ca.nome, '') LIKE ?
            )
            """
        )
        base_params.extend([like, like, like, f"%{digits or q}%", f"%{digits or q}%", like, like])
    destination_clause, destination_params, selected_destination = _destination_filter(destination)
    final_clauses = list(base_clauses)
    final_params = list(base_params)
    if destination_clause:
        final_clauses.append(destination_clause)
        final_params.extend(destination_params)
    select_sql = """
        SELECT co.id, co.data_recebimento, co.competencia, co.valor,
               co.tipo_contribuicao_id, co.campanha_id,
               COALESCE(t.codigo, '') AS tipo_codigo,
               COALESCE(t.nome, '') AS tipo_nome,
               COALESCE(ca.nome, '') AS campanha_nome,
               COALESCE(f.nome, '') AS forma_nome,
               p.id AS pessoa_id,
               p.status AS pessoa_status,
               p.nome AS pessoa_nome,
               c.id AS contribuinte_id,
               c.nome AS contribuinte_nome,
               c.documento_principal AS contribuinte_documento
          FROM contribuicoes co
          LEFT JOIN pessoas p ON p.id = co.pessoa_id
          LEFT JOIN contribuintes c ON c.id = co.contribuinte_id
          LEFT JOIN tipos_contribuicao t ON t.id = co.tipo_contribuicao_id
          LEFT JOIN campanhas ca ON ca.id = co.campanha_id
          LEFT JOIN formas_recebimento f ON f.id = co.forma_recebimento_id
    """
    with connect_legacy() as conn:
        option_rows = conn.execute(
            f"""
            {select_sql}
             WHERE {" AND ".join(base_clauses)}
             ORDER BY COALESCE(ca.nome, t.nome, '') COLLATE NOCASE ASC,
                      COALESCE(p.nome, c.nome, '') COLLATE NOCASE ASC,
                      COALESCE(co.data_recebimento, '') ASC,
                      co.id ASC
            """,
            tuple(base_params),
        ).fetchall()
        rows = conn.execute(
            f"""
            {select_sql}
             WHERE {" AND ".join(final_clauses)}
             ORDER BY COALESCE(ca.nome, t.nome, '') COLLATE NOCASE ASC,
                      COALESCE(p.nome, c.nome, '') COLLATE NOCASE ASC,
                      COALESCE(co.data_recebimento, '') ASC,
                      co.id ASC
             LIMIT ?
            """,
            (*final_params, limit_rows),
        ).fetchall()
    destination_options = _contribution_destination_options(option_rows)
    selected_destination_label = ""
    for option in destination_options:
        option["selected"] = option["key"] == selected_destination
        if option["selected"]:
            selected_destination_label = option["detail"]
    destination_groups: dict[str, dict[str, Any]] = {}
    overall_contributors: set[tuple[object, ...]] = set()
    sigla_counts = defaultdict(int)
    for row in rows:
        destination_data = _destination_from_contribution_row(row)
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
        has_person = bool(row["pessoa_id"])
        sigla = status_sigla(row["pessoa_status"], has_person)
        identity = contribution_report_identity(row["pessoa_nome"], row["contribuinte_nome"], row["contribuinte_documento"])
        display_name = identity["name"] or "Documento nao identificado"
        contributor_key = (
            identity["group_kind"],
            display_name,
            sigla,
            int(row["pessoa_id"] or 0),
            int(row["contribuinte_id"] or 0),
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
        value = float(row["valor"] or 0)
        item["total"] += value
        item["total_fmt"] = _money(item["total"])
        item["remessas"].append(
            {
                "id": row["id"],
                "detail_url": f"/contributions/{row['id']}/",
                "data": br_date(row["data_recebimento"]),
                "competencia": row["competencia"] or "",
                "valor_fmt": _money(value),
                "forma": row["forma_nome"] or "Sem forma",
            }
        )
        group["remessas"] += 1
        group["total"] += value
        group["total_fmt"] = _money(group["total"])
    for group in destination_groups.values():
        group_items = list(group.pop("items_by_key").values())
        group_items.sort(
            key=lambda item: (
                0 if item["group_kind"] == "nome" else 1,
                str(item["sort_key"]),
                str(item["documento"]),
            )
        )
        group["items"] = group_items
        group["named_items"] = [item for item in group_items if item["group_kind"] == "nome"]
        group["document_items"] = [item for item in group_items if item["group_kind"] == "documento"]
        group["contribuintes"] = len(group_items)
    destinations = sorted(
        destination_groups.values(),
        key=lambda item: (
            0 if item["key"] == "tipo:1" else 1,
            str(item["label"]).casefold(),
        ),
    )
    for contributor_key in overall_contributors:
        sigla_counts[str(contributor_key[2])] += 1
    total_value = sum(float(group["total"] or 0) for group in destinations)
    return {
        "destinations": destinations,
        "destination_options": destination_options,
        "selected_destination": selected_destination,
        "selected_destination_label": selected_destination_label,
        "competencia": competencia,
        "q": q,
        "date_start": date_start,
        "date_end": date_end,
        "competencias": list_competencias(),
        "summary": {
            "total_fmt": _money(total_value),
            "total": total_value,
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
    }


def list_audit(limit: int = 120) -> dict[str, Any]:
    with connect_legacy() as conn:
        if not table_exists(conn, "auditoria"):
            return {"items": [], "total": 0, "shown": 0, "limit": limit}
        total = int(scalar(conn, "SELECT COUNT(*) FROM auditoria") or 0)
        rows = conn.execute(
            """
            SELECT id, usuario_id, acao, tabela, registro_id, criado_em
              FROM auditoria
             ORDER BY COALESCE(criado_em, '') DESC, id DESC
             LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return {
        "items": [
            {
                "id": row["id"],
                "usuario_id": row["usuario_id"] or "",
                "acao": row["acao"] or "",
                "tabela": row["tabela"] or "",
                "registro_id": row["registro_id"] or "",
                "criado_em": br_datetime(row["criado_em"]),
            }
            for row in rows
        ],
        "total": total,
        "shown": min(total, limit),
        "limit": limit,
    }


def _duplicate_member_number_items(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, organizacao_id, nome, codigo_interno, status
          FROM pessoas
         WHERE ativo = 1
           AND codigo_interno IS NOT NULL
           AND TRIM(codigo_interno) <> ''
         ORDER BY organizacao_id, codigo_interno, nome, id
        """
    ).fetchall()
    groups: dict[tuple[int, str], list[sqlite3.Row]] = {}
    for row in rows:
        groups.setdefault((int(row["organizacao_id"] or 0), str(row["codigo_interno"] or "")), []).append(row)
    items: list[dict[str, Any]] = []
    for (_organization_id, member_code), people in groups.items():
        if len(people) <= 1:
            continue
        for person in people:
            others = [
                f"{row['nome']} (ID-{int(row['id']):06d})"
                for row in people
                if int(row["id"] or 0) != int(person["id"] or 0)
            ]
            formatted_member_code = f"MEM-{int(member_code):05d}" if member_code.isdigit() else member_code
            items.append(
                {
                    "id": f"dup_numero_membro_{person['id']}",
                    "tipo": "numero_membro_duplicado",
                    "severidade": "aviso",
                    "descricao": f"Numero de membro {formatted_member_code} repetido em {len(people)} ficha(s): "
                    + ", ".join(others),
                    "acao_sugerida": "Defina um numero operacional exclusivo para cada membro e preserve o numero original apenas como referencia historica.",
                    "numero_linha": "cadastro atual",
                    "pessoa_id": int(person["id"] or 0),
                    "nome": person["nome"] or "",
                    "codigo_interno": person["codigo_interno"] or "",
                    "status": person["status"] or "",
                    "resolvivel": False,
                    "origem": "cadastro",
                }
            )
    for item in items:
        item["smart_audit"] = classify_import_pendency(item)
    return items


def _import_audit_items(conn: sqlite3.Connection, tipo: str = "", severidade: str = "") -> list[dict[str, Any]]:
    clauses = ["ip.resolvido = 0"]
    params: list[Any] = []
    if tipo:
        clauses.append("ip.tipo = ?")
        params.append(tipo)
    if severidade:
        clauses.append("ip.severidade = ?")
        params.append(severidade)
    rows = conn.execute(
        f"""
        SELECT ip.id, ip.tipo, ip.severidade, ip.descricao, ip.acao_sugerida,
               ip.resolvido, il.numero_linha, p.id AS pessoa_id, p.nome,
               p.codigo_interno, p.status
          FROM import_pendencias ip
          JOIN import_linhas il ON il.id = ip.linha_id
          LEFT JOIN pessoas p ON p.id = il.registro_id AND il.registro_tipo = 'pessoa'
         WHERE {' AND '.join(clauses)}
         ORDER BY CASE ip.severidade WHEN 'aviso' THEN 0 ELSE 1 END,
                  ip.tipo COLLATE NOCASE,
                  p.nome COLLATE NOCASE,
                  ip.id
        """,
        tuple(params),
    ).fetchall()
    items = [
        {
            "id": row["id"],
            "tipo": row["tipo"] or "",
            "severidade": row["severidade"] or "",
            "descricao": row["descricao"] or "",
            "acao_sugerida": row["acao_sugerida"] or "",
            "numero_linha": row["numero_linha"] or "",
            "pessoa_id": row["pessoa_id"] or 0,
            "nome": row["nome"] or "",
            "codigo_interno": row["codigo_interno"] or "",
            "status": row["status"] or "",
            "resolvivel": True,
            "origem": "importacao",
        }
        for row in rows
    ]
    for item in items:
        item["smart_audit"] = classify_import_pendency(item)
    return items


def _audit_items(conn: sqlite3.Connection, tipo: str = "", severidade: str = "") -> list[dict[str, Any]]:
    items = _import_audit_items(conn, tipo=tipo, severidade=severidade)
    for item in _duplicate_member_number_items(conn):
        if tipo and item["tipo"] != tipo:
            continue
        if severidade and item["severidade"] != severidade:
            continue
        items.append(item)
    severity_rank = {"aviso": 0, "info": 1}
    return sorted(
        items,
        key=lambda item: (
            severity_rank.get(str(item.get("severidade") or ""), 9),
            str(item.get("tipo") or ""),
            str(item.get("nome") or ""),
            str(item.get("id") or ""),
        ),
    )


def operational_audit(tipo: str = "", severidade: str = "", page: int = 1, page_size: int = 200) -> dict[str, Any]:
    tipo = (tipo or "").strip()
    severidade = (severidade or "").strip()
    page = max(1, int(page or 1))
    page_size = min(max(50, int(page_size or 200)), 1000)
    with connect_legacy() as conn:
        all_items = _audit_items(conn, tipo=tipo, severidade=severidade)
        all_summary_items = _audit_items(conn)
        technical_count = int(scalar(conn, "SELECT COUNT(*) FROM auditoria") or 0)
    grouped_summary: dict[tuple[str, str], int] = {}
    for item in all_summary_items:
        key = (str(item.get("tipo") or ""), str(item.get("severidade") or ""))
        grouped_summary[key] = grouped_summary.get(key, 0) + 1
    severity_rank = {"aviso": 0, "info": 1}
    summary_rows = [
        {"tipo": key[0], "severidade": key[1], "quantidade": value}
        for key, value in grouped_summary.items()
    ]
    summary_rows.sort(
        key=lambda row: (
            severity_rank.get(str(row["severidade"]), 9),
            -int(row["quantidade"] or 0),
            str(row["tipo"]),
        )
    )
    people_by_id: dict[int, dict[str, Any]] = {}
    for item in all_items:
        person_id = int(item.get("pessoa_id") or 0)
        if not person_id:
            continue
        bucket = people_by_id.setdefault(
            person_id,
            {
                "pessoa_id": person_id,
                "nome": item.get("nome") or "",
                "codigo_interno": item.get("codigo_interno") or "",
                "status": item.get("status") or "",
                "avisos": 0,
                "infos": 0,
                "total": 0,
                "tipos_set": set(),
            },
        )
        if item.get("severidade") == "aviso":
            bucket["avisos"] += 1
        else:
            bucket["infos"] += 1
        bucket["total"] += 1
        bucket["tipos_set"].add(str(item.get("tipo") or ""))
    people_rows = []
    for bucket in people_by_id.values():
        people_rows.append(
            {
                "pessoa_id": bucket["pessoa_id"],
                "nome": bucket["nome"],
                "codigo_interno": bucket["codigo_interno"],
                "status": format_status(bucket["status"]),
                "avisos": bucket["avisos"],
                "infos": bucket["infos"],
                "total": bucket["total"],
                "tipos": ", ".join(sorted(bucket["tipos_set"])),
            }
        )
    people_rows.sort(key=lambda row: (-int(row["avisos"]), -int(row["total"]), str(row["nome"]).casefold()))
    total_items = len(all_items)
    offset = (page - 1) * page_size
    paged_items = all_items[offset : offset + page_size]
    total_pages = max(1, (total_items + page_size - 1) // page_size)
    return {
        "tipo": tipo,
        "severidade": severidade,
        "summary": summary_rows,
        "smart_summary": summarize_smart_audit(all_items),
        "people": people_rows,
        "items": [_format_operational_audit_item(item) for item in paged_items],
        "total": total_items,
        "shown": len(paged_items),
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "has_previous": page > 1,
        "has_next": page < total_pages,
        "previous_page": max(1, page - 1),
        "next_page": min(total_pages, page + 1),
        "technical_count": technical_count,
    }


def _format_operational_audit_item(item: dict[str, Any]) -> dict[str, Any]:
    person_id = int(item.get("pessoa_id") or 0)
    member_code = str(item.get("codigo_interno") or "").strip()
    return {
        "id": item.get("id"),
        "tipo": item.get("tipo") or "",
        "severidade": item.get("severidade") or "",
        "descricao": item.get("descricao") or "",
        "acao_sugerida": item.get("acao_sugerida") or "",
        "smart_category": (item.get("smart_audit") or {}).get("category_label") or "",
        "smart_risk": (item.get("smart_audit") or {}).get("risk_label") or "",
        "smart_confidence": (item.get("smart_audit") or {}).get("confidence_label") or "",
        "smart_operator_hint": (item.get("smart_audit") or {}).get("operator_hint") or "",
        "numero_linha": item.get("numero_linha") or "",
        "pessoa_id": person_id,
        "nome": item.get("nome") or "",
        "codigo": f"MEM-{int(member_code):05d}" if member_code.isdigit() else member_code,
        "system_id": f"ID-{person_id:06d}" if person_id else "",
        "status": format_status(item.get("status")),
        "resolvivel": bool(item.get("resolvivel")),
        "origem": item.get("origem") or "",
    }


def technical_audit(action: str = "", table: str = "", page: int = 1, page_size: int = 120) -> dict[str, Any]:
    action = (action or "").strip()
    table = (table or "").strip()
    page = max(1, int(page or 1))
    page_size = min(max(50, int(page_size or 120)), 1000)
    clauses = ["1 = 1"]
    params: list[Any] = []
    if action:
        clauses.append("acao = ?")
        params.append(action)
    if table:
        clauses.append("tabela = ?")
        params.append(table)
    where = " AND ".join(clauses)
    offset = (page - 1) * page_size
    with connect_legacy() as conn:
        total = int(scalar(conn, f"SELECT COUNT(*) FROM auditoria WHERE {where}", tuple(params)) or 0)
        rows = conn.execute(
            f"""
            SELECT id, usuario_id, acao, tabela, registro_id, criado_em
              FROM auditoria
             WHERE {where}
             ORDER BY COALESCE(criado_em, '') DESC, id DESC
             LIMIT ? OFFSET ?
            """,
            (*params, page_size, offset),
        ).fetchall()
        actions = [
            row["acao"]
            for row in conn.execute(
                "SELECT acao, COUNT(*) AS total FROM auditoria GROUP BY acao ORDER BY total DESC, acao LIMIT 60"
            ).fetchall()
        ]
        tables = [
            row["tabela"]
            for row in conn.execute(
                "SELECT tabela, COUNT(*) AS total FROM auditoria GROUP BY tabela ORDER BY total DESC, tabela LIMIT 60"
            ).fetchall()
        ]
    total_pages = max(1, (total + page_size - 1) // page_size)
    return {
        "action": action,
        "table": table,
        "items": [
            {
                "id": row["id"],
                "usuario_id": row["usuario_id"] or "",
                "acao": row["acao"] or "",
                "tabela": row["tabela"] or "",
                "registro_id": row["registro_id"] or "",
                "criado_em": br_datetime(row["criado_em"]),
            }
            for row in rows
        ],
        "actions": actions,
        "tables": tables,
        "total": total,
        "shown": len(rows),
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "has_previous": page > 1,
        "has_next": page < total_pages,
        "previous_page": max(1, page - 1),
        "next_page": min(total_pages, page + 1),
    }

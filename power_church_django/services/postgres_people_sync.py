from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from django.db import transaction
from django.utils.dateparse import parse_date, parse_datetime
from django.utils import timezone

from power_church_core.normalization import normalize_match_name, normalize_query
from power_church_django.apps.people.models import (
    HouseholdProfile,
    PersonAddressSnapshot,
    PersonContactSnapshot,
    PersonContributionSnapshot,
    PersonContributorSnapshot,
    PersonHistorySnapshot,
    PersonIdentifierSnapshot,
    PersonProfileSnapshot,
    PersonRelationshipSnapshot,
    PersonSnapshot,
)
from power_church_django.services.django_audit import record_django_audit_event


@dataclass
class SyncStats:
    people_total: int
    people_active: int
    contacts_total: int
    addresses_total: int
    relationships_total: int
    relationships_active: int
    profiles_total: int
    history_total: int
    contributors_total: int
    identifiers_total: int
    contributions_total: int
    household_profiles_total: int


def _connect_legacy(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _parse_date(raw_value: object):
    text = normalize_query(raw_value)
    return parse_date(text) if text else None


def _parse_datetime(raw_value: object):
    text = normalize_query(raw_value)
    if not text:
        return None
    parsed = parse_datetime(text)
    if parsed is not None:
        return parsed if timezone.is_aware(parsed) else timezone.make_aware(parsed, timezone.get_current_timezone())
    try:
        fallback = datetime.fromisoformat(text)
        return fallback if timezone.is_aware(fallback) else timezone.make_aware(fallback, timezone.get_current_timezone())
    except ValueError:
        return None


def _normalized_email(value: object) -> str:
    return normalize_query(value).lower()


def _normalized_address(row: sqlite3.Row) -> str:
    parts = [
        row["logradouro"],
        row["numero"],
        row["complemento"],
        row["bairro"],
        row["cidade"],
        row["uf"],
        row["cep"],
    ]
    return normalize_match_name(" ".join(normalize_query(part) for part in parts if normalize_query(part)))


def sync_people_snapshots(legacy_db_path: Path, actor: str = "django:etapa2_sync") -> dict[str, Any]:
    with _connect_legacy(legacy_db_path) as conn:
        people_rows = conn.execute(
            """
            SELECT id, organizacao_id, unidade_preferencial_id, codigo_interno, nome, nome_social, cpf, rg,
                   data_nascimento, sexo, estado_civil, email_principal, telefone_principal, whatsapp_principal,
                   status, arquivo_morto, observacoes, import_lote_id, ativo, criado_em, atualizado_em
              FROM pessoas
             ORDER BY id
            """
        ).fetchall()
        contact_rows = conn.execute(
            """
            SELECT id, organizacao_id, pessoa_id, tipo, valor, principal, observacoes, criado_em
              FROM pessoa_contatos
             ORDER BY id
            """
        ).fetchall()
        address_rows = conn.execute(
            """
            SELECT id, organizacao_id, pessoa_id, tipo, cep, logradouro, numero, complemento, bairro, cidade, uf,
                   principal, criado_em, atualizado_em
              FROM pessoa_enderecos
             ORDER BY id
            """
        ).fetchall()
        relationship_rows = conn.execute(
            """
            SELECT id, organizacao_id, pessoa_id, pessoa_relacionada_id, tipo_relacionamento, observacoes, ativo, criado_em
              FROM pessoa_relacionamentos
             ORDER BY id
            """
        ).fetchall()
        profile_rows = conn.execute(
            """
            SELECT id, organizacao_id, pessoa_id, perfil, data_inicio, data_fim, observacoes, ativo
              FROM pessoa_perfis
             ORDER BY id
            """
        ).fetchall()
        history_rows = conn.execute(
            """
            SELECT id, organizacao_id, pessoa_id, tipo_evento, data_evento, titulo, descricao, origem, destino, criado_em
              FROM pessoa_historico
             ORDER BY id
            """
        ).fetchall()
        contributor_rows = conn.execute(
            """
            SELECT id, organizacao_id, pessoa_id, nome, tipo, documento_principal, documento_tipo, origem, qualidade, status, ativo
              FROM contribuintes
             ORDER BY id
            """
        ).fetchall()
        identifier_rows = conn.execute(
            """
            SELECT id, organizacao_id, pessoa_id, contribuinte_id, tipo, valor, principal, observacoes, ativo
              FROM contribuintes_identificadores
             ORDER BY id
            """
        ).fetchall()
        contribution_rows = conn.execute(
            """
            SELECT co.id, co.organizacao_id, co.pessoa_id, co.contribuinte_id, co.data_recebimento, co.competencia,
                   co.competencia_ordem, co.valor, co.status_operacional, co.ativo,
                   COALESCE(tc.nome, '') AS tipo_nome,
                   COALESCE(fr.nome, '') AS forma_nome,
                   COALESCE(c.nome, '') AS origem_nome
              FROM contribuicoes co
              LEFT JOIN tipos_contribuicao tc ON tc.id = co.tipo_contribuicao_id
              LEFT JOIN formas_recebimento fr ON fr.id = co.forma_recebimento_id
              LEFT JOIN contribuintes c ON c.id = co.contribuinte_id
             WHERE co.pessoa_id IS NOT NULL
             ORDER BY co.id
            """
        ).fetchall()

    with transaction.atomic():
        PersonIdentifierSnapshot.objects.all().delete()
        PersonContributionSnapshot.objects.all().delete()
        PersonContributorSnapshot.objects.all().delete()
        PersonHistorySnapshot.objects.all().delete()
        PersonProfileSnapshot.objects.all().delete()
        PersonRelationshipSnapshot.objects.all().delete()
        PersonAddressSnapshot.objects.all().delete()
        PersonContactSnapshot.objects.all().delete()
        PersonSnapshot.objects.all().delete()

        PersonSnapshot.objects.bulk_create(
            [
                PersonSnapshot(
                    legacy_id=int(row["id"]),
                    organization_id=int(row["organizacao_id"] or 0),
                    preferred_unit_id=int(row["unidade_preferencial_id"]) if row["unidade_preferencial_id"] is not None else None,
                    internal_code=normalize_query(row["codigo_interno"]),
                    name=normalize_query(row["nome"]),
                    normalized_name=normalize_match_name(row["nome"]),
                    social_name=normalize_query(row["nome_social"]),
                    cpf=normalize_query(row["cpf"]),
                    rg=normalize_query(row["rg"]),
                    birth_date=_parse_date(row["data_nascimento"]),
                    birth_date_raw=normalize_query(row["data_nascimento"]),
                    sex=normalize_query(row["sexo"]),
                    marital_status=normalize_query(row["estado_civil"]),
                    primary_email=normalize_query(row["email_principal"]),
                    normalized_email=_normalized_email(row["email_principal"]),
                    primary_phone=normalize_query(row["telefone_principal"]),
                    primary_whatsapp=normalize_query(row["whatsapp_principal"]),
                    status=normalize_query(row["status"]),
                    is_archived=bool(int(row["arquivo_morto"] or 0)),
                    is_active=bool(int(row["ativo"] or 0)),
                    notes=normalize_query(row["observacoes"]),
                    import_lot_id=int(row["import_lote_id"]) if row["import_lote_id"] is not None else None,
                    created_at_legacy=_parse_datetime(row["criado_em"]),
                    updated_at_legacy=_parse_datetime(row["atualizado_em"]),
                )
                for row in people_rows
            ],
            batch_size=500,
        )

        people_map = {
            item.legacy_id: item
            for item in PersonSnapshot.objects.only("id", "legacy_id")
        }

        PersonContactSnapshot.objects.bulk_create(
            [
                PersonContactSnapshot(
                    legacy_id=int(row["id"]),
                    organization_id=int(row["organizacao_id"] or 0),
                    person=people_map[int(row["pessoa_id"])],
                    contact_type=normalize_query(row["tipo"]),
                    value=normalize_query(row["valor"]),
                    normalized_value=normalize_match_name(row["valor"]),
                    is_primary=bool(int(row["principal"] or 0)),
                    notes=normalize_query(row["observacoes"]),
                    created_at_legacy=_parse_datetime(row["criado_em"]),
                )
                for row in contact_rows
                if int(row["pessoa_id"]) in people_map
            ],
            batch_size=1000,
        )

        PersonAddressSnapshot.objects.bulk_create(
            [
                PersonAddressSnapshot(
                    legacy_id=int(row["id"]),
                    organization_id=int(row["organizacao_id"] or 0),
                    person=people_map[int(row["pessoa_id"])],
                    address_type=normalize_query(row["tipo"]),
                    cep=normalize_query(row["cep"]),
                    street=normalize_query(row["logradouro"]),
                    number=normalize_query(row["numero"]),
                    complement=normalize_query(row["complemento"]),
                    neighborhood=normalize_query(row["bairro"]),
                    city=normalize_query(row["cidade"]),
                    state=normalize_query(row["uf"]),
                    is_primary=bool(int(row["principal"] or 0)),
                    normalized_address=_normalized_address(row),
                    created_at_legacy=_parse_datetime(row["criado_em"]),
                    updated_at_legacy=_parse_datetime(row["atualizado_em"]),
                )
                for row in address_rows
                if int(row["pessoa_id"]) in people_map
            ],
            batch_size=1000,
        )

        PersonRelationshipSnapshot.objects.bulk_create(
            [
                PersonRelationshipSnapshot(
                    legacy_id=int(row["id"]),
                    organization_id=int(row["organizacao_id"] or 0),
                    person=people_map[int(row["pessoa_id"])],
                    related_person=people_map[int(row["pessoa_relacionada_id"])],
                    relationship_type=normalize_query(row["tipo_relacionamento"]),
                    notes=normalize_query(row["observacoes"]),
                    is_active=bool(int(row["ativo"] or 0)),
                    created_at_legacy=_parse_datetime(row["criado_em"]),
                )
                for row in relationship_rows
                if int(row["pessoa_id"]) in people_map and int(row["pessoa_relacionada_id"]) in people_map
            ],
            batch_size=1000,
        )

        PersonProfileSnapshot.objects.bulk_create(
            [
                PersonProfileSnapshot(
                    legacy_id=int(row["id"]),
                    organization_id=int(row["organizacao_id"] or 0),
                    person=people_map[int(row["pessoa_id"])],
                    profile=normalize_query(row["perfil"]),
                    start_date_raw=normalize_query(row["data_inicio"]),
                    end_date_raw=normalize_query(row["data_fim"]),
                    notes=normalize_query(row["observacoes"]),
                    is_active=bool(int(row["ativo"] or 0)),
                )
                for row in profile_rows
                if int(row["pessoa_id"]) in people_map
            ],
            batch_size=1000,
        )

        PersonHistorySnapshot.objects.bulk_create(
            [
                PersonHistorySnapshot(
                    legacy_id=int(row["id"]),
                    organization_id=int(row["organizacao_id"] or 0),
                    person=people_map[int(row["pessoa_id"])],
                    event_type=normalize_query(row["tipo_evento"]),
                    event_date_raw=normalize_query(row["data_evento"]),
                    title=normalize_query(row["titulo"]),
                    description=normalize_query(row["descricao"]),
                    origin=normalize_query(row["origem"]),
                    destination=normalize_query(row["destino"]),
                    created_at_legacy=_parse_datetime(row["criado_em"]),
                )
                for row in history_rows
                if int(row["pessoa_id"]) in people_map
            ],
            batch_size=1000,
        )

        PersonContributorSnapshot.objects.bulk_create(
            [
                PersonContributorSnapshot(
                    legacy_id=int(row["id"]),
                    organization_id=int(row["organizacao_id"] or 0),
                    person=people_map[int(row["pessoa_id"])],
                    name=normalize_query(row["nome"]),
                    contributor_type=normalize_query(row["tipo"]),
                    primary_document=normalize_query(row["documento_principal"]),
                    document_type=normalize_query(row["documento_tipo"]),
                    origin=normalize_query(row["origem"]),
                    quality=normalize_query(row["qualidade"]),
                    status=normalize_query(row["status"]),
                    is_active=bool(int(row["ativo"] or 0)),
                )
                for row in contributor_rows
                if row["pessoa_id"] is not None and int(row["pessoa_id"]) in people_map
            ],
            batch_size=1000,
        )

        PersonIdentifierSnapshot.objects.bulk_create(
            [
                PersonIdentifierSnapshot(
                    legacy_id=int(row["id"]),
                    organization_id=int(row["organizacao_id"] or 0),
                    person=people_map[int(row["pessoa_id"])],
                    contributor_legacy_id=int(row["contribuinte_id"]) if row["contribuinte_id"] is not None else None,
                    identifier_type=normalize_query(row["tipo"]),
                    value=normalize_query(row["valor"]),
                    is_primary=bool(int(row["principal"] or 0)),
                    notes=normalize_query(row["observacoes"]),
                    is_active=bool(int(row["ativo"] or 0)),
                )
                for row in identifier_rows
                if row["pessoa_id"] is not None and int(row["pessoa_id"]) in people_map
            ],
            batch_size=1000,
        )

        PersonContributionSnapshot.objects.bulk_create(
            [
                PersonContributionSnapshot(
                    legacy_id=int(row["id"]),
                    organization_id=int(row["organizacao_id"] or 0),
                    person=people_map[int(row["pessoa_id"])],
                    contributor_legacy_id=int(row["contribuinte_id"]) if row["contribuinte_id"] is not None else None,
                    received_at=_parse_date(row["data_recebimento"]),
                    received_at_raw=normalize_query(row["data_recebimento"]),
                    competence=normalize_query(row["competencia"]),
                    competence_order=int(row["competencia_ordem"] or 0),
                    amount=row["valor"] or 0,
                    operational_status=normalize_query(row["status_operacional"]),
                    contribution_type_name=normalize_query(row["tipo_nome"]),
                    receipt_method_name=normalize_query(row["forma_nome"]),
                    source_name=normalize_query(row["origem_nome"]),
                    is_active=bool(int(row["ativo"] or 0)),
                )
                for row in contribution_rows
                if row["pessoa_id"] is not None and int(row["pessoa_id"]) in people_map
            ],
            batch_size=1000,
        )

    stats = compare_people_snapshots(legacy_db_path)
    record_django_audit_event(
        actor=actor,
        action="sincronizar_espelho_cadastro_postgres",
        table_name="people_personsnapshot",
        source="stage2_people_sync",
        summary="Espelho cadastral sincronizado do legado SQLite para o Postgres.",
        after=stats,
    )
    return stats


def compare_people_snapshots(legacy_db_path: Path) -> dict[str, Any]:
    with _connect_legacy(legacy_db_path) as conn:
        legacy_people_total = int(conn.execute("SELECT COUNT(*) FROM pessoas").fetchone()[0] or 0)
        legacy_people_active = int(conn.execute("SELECT COUNT(*) FROM pessoas WHERE ativo = 1").fetchone()[0] or 0)
        legacy_contacts_total = int(conn.execute("SELECT COUNT(*) FROM pessoa_contatos").fetchone()[0] or 0)
        legacy_addresses_total = int(conn.execute("SELECT COUNT(*) FROM pessoa_enderecos").fetchone()[0] or 0)
        legacy_relationships_total = int(conn.execute("SELECT COUNT(*) FROM pessoa_relacionamentos").fetchone()[0] or 0)
        legacy_relationships_active = int(conn.execute("SELECT COUNT(*) FROM pessoa_relacionamentos WHERE ativo = 1").fetchone()[0] or 0)
        legacy_profiles_total = int(conn.execute("SELECT COUNT(*) FROM pessoa_perfis WHERE ativo = 1").fetchone()[0] or 0)
        legacy_history_total = int(conn.execute("SELECT COUNT(*) FROM pessoa_historico").fetchone()[0] or 0)
        legacy_contributors_total = int(
            conn.execute("SELECT COUNT(*) FROM contribuintes WHERE ativo = 1 AND pessoa_id IS NOT NULL").fetchone()[0] or 0
        )
        legacy_identifiers_total = int(
            conn.execute(
                """
                SELECT COUNT(*)
                  FROM contribuintes_identificadores
                 WHERE ativo = 1
                   AND pessoa_id IS NOT NULL
                """
            ).fetchone()[0]
            or 0
        )
        legacy_contributions_total = int(
            conn.execute(
                """
                SELECT COUNT(*)
                  FROM contribuicoes
                 WHERE ativo = 1
                   AND pessoa_id IS NOT NULL
                """
            ).fetchone()[0]
            or 0
        )

    postgres_people_total = PersonSnapshot.objects.count()
    postgres_people_active = PersonSnapshot.objects.filter(is_active=True).count()
    postgres_contacts_total = PersonContactSnapshot.objects.count()
    postgres_addresses_total = PersonAddressSnapshot.objects.count()
    postgres_relationships_total = PersonRelationshipSnapshot.objects.count()
    postgres_relationships_active = PersonRelationshipSnapshot.objects.filter(is_active=True).count()
    postgres_profiles_total = PersonProfileSnapshot.objects.filter(is_active=True).count()
    postgres_history_total = PersonHistorySnapshot.objects.count()
    postgres_contributors_total = PersonContributorSnapshot.objects.filter(is_active=True).count()
    postgres_identifiers_total = PersonIdentifierSnapshot.objects.filter(is_active=True).count()
    postgres_contributions_total = PersonContributionSnapshot.objects.filter(is_active=True).count()
    household_profiles_total = HouseholdProfile.objects.count()

    return {
        "legacy_people_total": legacy_people_total,
        "postgres_people_total": postgres_people_total,
        "legacy_people_active": legacy_people_active,
        "postgres_people_active": postgres_people_active,
        "legacy_contacts_total": legacy_contacts_total,
        "postgres_contacts_total": postgres_contacts_total,
        "legacy_addresses_total": legacy_addresses_total,
        "postgres_addresses_total": postgres_addresses_total,
        "legacy_relationships_total": legacy_relationships_total,
        "postgres_relationships_total": postgres_relationships_total,
        "legacy_relationships_active": legacy_relationships_active,
        "postgres_relationships_active": postgres_relationships_active,
        "legacy_profiles_total": legacy_profiles_total,
        "postgres_profiles_total": postgres_profiles_total,
        "legacy_history_total": legacy_history_total,
        "postgres_history_total": postgres_history_total,
        "legacy_contributors_total": legacy_contributors_total,
        "postgres_contributors_total": postgres_contributors_total,
        "legacy_identifiers_total": legacy_identifiers_total,
        "postgres_identifiers_total": postgres_identifiers_total,
        "legacy_contributions_total": legacy_contributions_total,
        "postgres_contributions_total": postgres_contributions_total,
        "household_profiles_total": household_profiles_total,
        "counts_match": all(
            [
                legacy_people_total == postgres_people_total,
                legacy_people_active == postgres_people_active,
                legacy_contacts_total == postgres_contacts_total,
                legacy_addresses_total == postgres_addresses_total,
                legacy_relationships_total == postgres_relationships_total,
                legacy_relationships_active == postgres_relationships_active,
                legacy_profiles_total == postgres_profiles_total,
                legacy_history_total == postgres_history_total,
                legacy_contributors_total == postgres_contributors_total,
                legacy_identifiers_total == postgres_identifiers_total,
                legacy_contributions_total == postgres_contributions_total,
            ]
        ),
    }

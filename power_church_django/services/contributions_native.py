from __future__ import annotations

from decimal import Decimal
from datetime import datetime
from typing import Any

from django.db import models, transaction

from power_church_core.formatting import br_date, br_datetime, br_money, competencia_from_date, parse_money
from power_church_core.normalization import contribution_report_identity, format_cpf, moneyless_int, normalize_match_name, normalize_query
from power_church_django.apps.audit.models import AuditEvent
from power_church_django.apps.contributions.models import (
    ContributionTypeSnapshot,
    NativeAuxContributor,
    NativeContribution,
    NativeEnvelope,
    NativeEnvelopeItem,
    NativeEnvelopeLot,
)
from power_church_django.apps.people.models import (
    PersonContributionSnapshot,
    PersonContributorSnapshot,
    PersonIdentifierSnapshot,
    PersonSnapshot,
)
from power_church_django.services.django_audit import record_django_audit_event
from power_church_django.services.runtime_errors import LegacyWriteError
from power_church_django.services.runtime_formatting import CONTRIBUTION_STATUS_OPTIONS, format_status, status_sigla


def _next_native_contribution_public_id() -> int:
    native_max = (
        NativeContribution.objects.aggregate(value=models.Max("legacy_id")).get("value") or 0
    )
    snapshot_max = (
        PersonContributionSnapshot.objects.aggregate(value=models.Max("legacy_id")).get("value") or 0
    )
    return int(max(int(native_max or 0), int(snapshot_max or 0)) + 1)


def _active_person_snapshot(person_id: int) -> PersonSnapshot:
    person = PersonSnapshot.objects.filter(legacy_id=int(person_id or 0), is_active=True).first()
    if person is None:
        raise LegacyWriteError("Escolha uma pessoa valida para registrar a contribuicao.")
    return person


def _native_contributor_id_for_person(person: PersonSnapshot) -> int | None:
    row = (
        PersonContributorSnapshot.objects.filter(person=person, is_active=True)
        .order_by("legacy_id")
        .values_list("legacy_id", flat=True)
        .first()
    )
    return int(row or 0) or None


def _catalogs_for_org(
    organization_id: int,
    *,
    selected_type_id: int = 0,
    selected_form_id: int = 0,
    selected_campaign_id: int = 0,
) -> dict[str, Any]:
    organization_id = int(organization_id or 0)

    def _traceability_value(code: object, name: object) -> str:
        value = normalize_match_name(code or name)
        if "DINHEIRO" in value:
            return "dinheiro"
        if "PIX" in value:
            return "pix"
        if "TRANSFERENCIA" in value or "TED" in value or "DOC" in value:
            return "transferencia"
        if "CARTAO" in value:
            return "cartao_credito"
        if "CHEQUE" in value:
            return "cheque"
        if "DEPOSITO" in value:
            return "deposito"
        return ""

    type_rows = list(
        ContributionTypeSnapshot.objects.filter(
            organization_id=organization_id,
            is_active=True,
        ).only("legacy_id", "code", "name")
    )
    type_rows.sort(
        key=lambda row: (
            0 if normalize_query(row.code) == "dizimo" else 1,
            normalize_query(row.name),
            int(row.legacy_id or 0),
        )
    )

    receiving_map: dict[int, dict[str, Any]] = {}
    for row in (
        NativeContribution.objects.filter(
            organization_id=organization_id,
            is_active=True,
        )
        .exclude(receipt_method_legacy_id__isnull=True)
        .exclude(receipt_method_name="")
        .values("receipt_method_legacy_id", "receipt_method_name")
        .distinct()
    ):
        legacy_id = int(row["receipt_method_legacy_id"] or 0)
        if not legacy_id:
            continue
        name = str(row["receipt_method_name"] or "")
        receiving_map[legacy_id] = {
            "id": legacy_id,
            "codigo": "",
            "nome": name,
            "traceability_value": _traceability_value("", name),
            "selected": legacy_id == int(selected_form_id or 0),
        }
    for row in (
        NativeEnvelopeLot.objects.filter(organization_id=organization_id, is_active=True)
        .exclude(default_receipt_method_legacy_id__isnull=True)
        .values("default_receipt_method_legacy_id")
        .distinct()
    ):
        legacy_id = int(row["default_receipt_method_legacy_id"] or 0)
        if legacy_id and legacy_id not in receiving_map:
            receiving_map[legacy_id] = {
                "id": legacy_id,
                "codigo": "",
                "nome": f"Forma #{legacy_id}",
                "traceability_value": "",
                "selected": legacy_id == int(selected_form_id or 0),
            }

    campaign_map: dict[int, dict[str, Any]] = {}
    for row in (
        NativeContribution.objects.filter(
            organization_id=organization_id,
            is_active=True,
        )
        .exclude(campaign_legacy_id__isnull=True)
        .exclude(campaign_name="")
        .values("campaign_legacy_id", "campaign_name")
        .distinct()
    ):
        legacy_id = int(row["campaign_legacy_id"] or 0)
        if not legacy_id:
            continue
        campaign_map[legacy_id] = {
            "id": legacy_id,
            "nome": str(row["campaign_name"] or ""),
            "status": "ativa",
            "selected": legacy_id == int(selected_campaign_id or 0),
        }
    for row in (
        NativeEnvelopeItem.objects.filter(
            envelope__organization_id=organization_id,
            is_active=True,
            envelope__is_active=True,
        )
        .exclude(campaign_legacy_id__isnull=True)
        .exclude(campaign_name="")
        .values("campaign_legacy_id", "campaign_name")
        .distinct()
    ):
        legacy_id = int(row["campaign_legacy_id"] or 0)
        if legacy_id and legacy_id not in campaign_map:
            campaign_map[legacy_id] = {
                "id": legacy_id,
                "nome": str(row["campaign_name"] or ""),
                "status": "ativa",
                "selected": legacy_id == int(selected_campaign_id or 0),
            }

    return {
        "type_options": [
            {
                "id": int(row.legacy_id or 0),
                "codigo": row.code or "",
                "nome": row.name or "",
                "selected": int(row.legacy_id or 0) == int(selected_type_id or 0),
            }
            for row in type_rows
        ],
        "receiving_options": sorted(
            receiving_map.values(),
            key=lambda item: (normalize_query(item["nome"]), int(item["id"])),
        ),
        "campaign_options": sorted(
            campaign_map.values(),
            key=lambda item: (normalize_query(item["nome"]), int(item["id"])),
        ),
        "status_options": [
            {"value": value, "label": value.replace("_", " ").title()}
            for value in sorted(CONTRIBUTION_STATUS_OPTIONS)
        ],
    }


def _selected_option_name(options: list[dict[str, Any]], selected_id: int) -> str:
    selected = next((item for item in options if int(item.get("id") or 0) == int(selected_id or 0)), None)
    return str((selected or {}).get("nome") or "")


def _contribution_payload(data: Any) -> dict[str, object]:
    getter = getattr(data, "get", None)
    received_on = normalize_query(getter("data_recebimento") if getter else "")
    try:
        competence, competence_order = competencia_from_date(received_on)
    except ValueError as exc:
        raise LegacyWriteError(str(exc)) from exc
    status = normalize_query(getter("status_operacional", "regular") if getter else "regular") or "regular"
    if status not in CONTRIBUTION_STATUS_OPTIONS:
        raise LegacyWriteError("Status operacional invalido para ajuste manual.")
    try:
        amount = parse_money(getter("valor") if getter else "")
    except ValueError as exc:
        raise LegacyWriteError(str(exc)) from exc
    justification = normalize_query(getter("justificativa") if getter else "")
    if len(justification) < 8:
        raise LegacyWriteError("Informe uma justificativa com pelo menos 8 caracteres para o ajuste manual.")
    type_id = moneyless_int(getter("tipo_contribuicao_id") if getter else 0)
    if not type_id:
        raise LegacyWriteError("Escolha o tipo de contribuicao.")
    return {
        "data_recebimento": received_on,
        "data_recebimento_date": datetime.strptime(received_on, "%Y-%m-%d").date(),
        "competencia": competence,
        "competencia_ordem": competence_order,
        "valor": amount,
        "tipo_contribuicao_id": type_id,
        "campanha_id": moneyless_int(getter("campanha_id") if getter else 0) or None,
        "forma_recebimento_id": moneyless_int(getter("forma_recebimento_id") if getter else 0) or None,
        "status_operacional": status,
        "observacoes": normalize_query(getter("observacoes") if getter else ""),
        "justificativa": justification,
    }


def _sync_person_contribution_snapshot(record: NativeContribution) -> None:
    if not int(record.person_legacy_id or 0):
        PersonContributionSnapshot.objects.filter(legacy_id=int(record.legacy_id or 0)).delete()
        return
    person = _active_person_snapshot(int(record.person_legacy_id or 0))
    PersonContributionSnapshot.objects.update_or_create(
        legacy_id=int(record.legacy_id or 0),
        defaults={
            "organization_id": int(record.organization_id or 0),
            "person": person,
            "contributor_legacy_id": int(record.contributor_legacy_id or 0) or None,
            "received_at": record.received_at,
            "received_at_raw": record.received_at_raw or "",
            "competence": record.competence or "",
            "competence_order": int(record.competence_order or 0),
            "amount": Decimal(record.amount or 0),
            "operational_status": record.operational_status or "",
            "contribution_type_name": record.contribution_type_name or "",
            "receipt_method_name": record.receipt_method_name or "",
            "source_name": record.source or "postgres_native_manual",
            "is_active": bool(record.is_active),
        },
    )


def _next_person_contributor_legacy_id() -> int:
    contributor_max = PersonContributorSnapshot.objects.aggregate(value=models.Max("legacy_id")).get("value") or 0
    return int(contributor_max or 0) + 1


def _next_native_aux_reference_id() -> int:
    value = NativeAuxContributor.objects.aggregate(value=models.Max("legacy_reference_id")).get("value") or 0
    return int(value or 0) + 1


def _next_person_identifier_legacy_id() -> int:
    value = PersonIdentifierSnapshot.objects.aggregate(value=models.Max("legacy_id")).get("value") or 0
    return int(value or 0) + 1


def _document_kind(value: object) -> str | None:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(digits) == 11:
        return "cpf"
    if len(digits) == 14:
        return "cnpj"
    return None


def _contributor_type(name: object, document: object) -> str:
    digits = "".join(ch for ch in str(document or "") if ch.isdigit())
    if len(digits) == 14:
        return "pj"
    if normalize_query(name):
        return "pf"
    return "externo"


def _ensure_manual_contributor_postgres(
    *,
    organization_id: int,
    person: PersonSnapshot,
    name: str,
    document: str = "",
    source: str = "rateio_manual_postgres",
) -> int:
    name = normalize_query(name)
    document = normalize_query(document)
    digits = "".join(ch for ch in document if ch.isdigit())
    document_kind = _document_kind(document)
    if digits:
        existing = (
            PersonIdentifierSnapshot.objects.filter(
                organization_id=organization_id,
                identifier_type=document_kind or "documento",
                value=digits,
                is_active=True,
            )
            .order_by("legacy_id")
            .first()
        )
        if existing and existing.person_id == person.id:
            contributor = (
                PersonContributorSnapshot.objects.filter(
                    person=person,
                    legacy_id=int(existing.contributor_legacy_id or 0),
                    is_active=True,
                )
                .order_by("legacy_id")
                .first()
            )
            if contributor:
                return int(contributor.legacy_id or 0)
    normalized_name = normalize_match_name(name or document)
    existing = (
        PersonContributorSnapshot.objects.filter(
            organization_id=organization_id,
            person=person,
            is_active=True,
            name__iexact=name or document,
        )
        .order_by("legacy_id")
        .first()
    )
    if existing:
        return int(existing.legacy_id or 0)
    contributor = PersonContributorSnapshot.objects.create(
        legacy_id=_next_person_contributor_legacy_id(),
        organization_id=organization_id,
        person=person,
        name=name or document,
        contributor_type=_contributor_type(name, document),
        primary_document=digits or document or "",
        document_type=document_kind or "",
        origin=source,
        quality="doador",
        status="ativo",
        is_active=True,
    )
    if digits:
        PersonIdentifierSnapshot.objects.create(
            legacy_id=_next_person_identifier_legacy_id(),
            organization_id=organization_id,
            person=person,
            contributor_legacy_id=int(contributor.legacy_id or 0),
            identifier_type=document_kind or "documento",
            value=digits,
            is_primary=True,
            notes=f"Sincronizado a partir de {source}.",
            is_active=True,
        )
    return int(contributor.legacy_id or 0)


def _validated_native_contributor(organization_id: int, contributor_id: int) -> PersonContributorSnapshot | None:
    if not contributor_id:
        return None
    contributor = (
        PersonContributorSnapshot.objects.filter(
            organization_id=organization_id,
            legacy_id=int(contributor_id or 0),
            is_active=True,
        )
        .select_related("person")
        .first()
    )
    if contributor is None:
        raise LegacyWriteError("Contribuinte auxiliar invalido.")
    return contributor


def _legacy_contributor_row(organization_id: int, contributor_id: int) -> dict[str, Any] | None:
    contributor = (
        PersonContributorSnapshot.objects.filter(
            organization_id=organization_id,
            legacy_id=int(contributor_id or 0),
            is_active=True,
        )
        .select_related("person")
        .first()
    )
    if contributor is not None:
        return {
            "id": int(contributor.legacy_id or 0),
            "pessoa_id": int(contributor.person.legacy_id or 0),
            "nome": contributor.name or "",
            "documento_principal": contributor.primary_document or "",
            "documento_tipo": contributor.document_type or "",
            "tipo": contributor.contributor_type or "",
            "origem": contributor.origin or "",
            "qualidade": contributor.quality or "",
            "status": contributor.status or "",
            "observacoes": "",
        }
    aux = (
        NativeAuxContributor.objects.filter(
            organization_id=organization_id,
            legacy_reference_id=int(contributor_id or 0),
            is_active=True,
        )
        .first()
    )
    if aux is None:
        return None
    return {
        "id": int(aux.legacy_reference_id or 0),
        "pessoa_id": int(aux.person_legacy_id or 0) or None,
        "nome": aux.name or "",
        "documento_principal": aux.primary_document or "",
        "documento_tipo": aux.document_type or "",
        "tipo": aux.contributor_type or "",
        "origem": aux.origin or "",
        "qualidade": aux.quality or "",
        "status": aux.status or "",
        "observacoes": aux.notes or "",
    }


def _resolve_native_aux_contributor(
    *,
    organization_id: int,
    legacy_contributor_id: int = 0,
    person_legacy_id: int | None = None,
    name: str = "",
    document: str = "",
    source: str = "rateio_manual_postgres",
) -> NativeAuxContributor:
    name = normalize_query(name)
    document = normalize_query(document)
    digits = "".join(ch for ch in document if ch.isdigit())
    if legacy_contributor_id:
        existing = NativeAuxContributor.objects.filter(
            organization_id=organization_id,
            legacy_reference_id=int(legacy_contributor_id or 0),
            is_active=True,
        ).first()
        if existing:
            return existing
        legacy_row = _legacy_contributor_row(organization_id, legacy_contributor_id)
        if legacy_row is None:
            raise LegacyWriteError("Contribuinte auxiliar invalido.")
        return NativeAuxContributor.objects.create(
            organization_id=organization_id,
            legacy_reference_id=int(legacy_contributor_id or 0),
            person_legacy_id=moneyless_int(legacy_row.get("pessoa_id")) or person_legacy_id or None,
            name=normalize_query(legacy_row.get("nome")) or f"Contribuinte #{legacy_contributor_id}",
            normalized_name=normalize_match_name(legacy_row.get("nome") or f"Contribuinte #{legacy_contributor_id}"),
            primary_document=normalize_query(legacy_row.get("documento_principal")) or "",
            document_type=normalize_query(legacy_row.get("documento_tipo")) or "",
            contributor_type=normalize_query(legacy_row.get("tipo")) or "",
            origin=normalize_query(legacy_row.get("origem")) or source,
            quality=normalize_query(legacy_row.get("qualidade")) or "doador",
            status=normalize_query(legacy_row.get("status")) or "ativo",
            notes=normalize_query(legacy_row.get("observacoes")) or "",
            is_active=True,
        )
    existing = None
    if digits:
        existing = NativeAuxContributor.objects.filter(
            organization_id=organization_id,
            primary_document=digits,
            is_active=True,
        ).first()
    if existing is None and normalize_match_name(name or document):
        existing = NativeAuxContributor.objects.filter(
            organization_id=organization_id,
            normalized_name=normalize_match_name(name or document),
            is_active=True,
        ).first()
    if existing:
        return existing
    return NativeAuxContributor.objects.create(
        organization_id=organization_id,
        legacy_reference_id=_next_native_aux_reference_id(),
        person_legacy_id=person_legacy_id or None,
        name=name or document,
        normalized_name=normalize_match_name(name or document),
        primary_document=digits or document or "",
        document_type=_document_kind(document) or "",
        contributor_type=_contributor_type(name, document),
        origin=source,
        quality="doador",
        status="ativo",
        notes="Criado por rateio/contribuicao nativa.",
        is_active=True,
    )


def _native_people_options(organization_id: int, limit: int = 5000) -> list[dict[str, Any]]:
    rows = (
        PersonSnapshot.objects.filter(organization_id=organization_id, is_active=True)
        .order_by("name", "legacy_id")[:limit]
    )
    return [
        {
            "id": int(row.legacy_id or 0),
            "nome": row.name or "",
            "codigo": row.internal_code or "",
            "cpf": format_cpf(row.cpf or ""),
            "status": format_status(row.status or ""),
            "sigla": status_sigla(row.status or "", True),
            "telefone": row.primary_phone or "",
            "whatsapp": row.primary_whatsapp or "",
        }
        for row in rows
    ]


def _native_contributor_options(organization_id: int, limit: int = 5000) -> list[dict[str, Any]]:
    options = (
        PersonContributorSnapshot.objects.filter(
            organization_id=organization_id,
            is_active=True,
        )
        .select_related("person")
        .order_by("name", "legacy_id")[:limit]
    )
    return [
        {
            "id": int(row.legacy_id or 0),
            "nome": row.name or "",
            "documento": row.primary_document or "",
            "tipo": (row.contributor_type or "").upper(),
            "person_id": int(row.person.legacy_id or 0),
        }
        for row in options
    ]


def split_contribution_context_postgres(contribution_id: int) -> dict[str, Any] | None:
    detail = get_contribution_detail_postgres(contribution_id)
    if detail is None:
        return None
    contribution = NativeContribution.objects.filter(legacy_id=int(contribution_id or 0), is_active=True).first()
    if contribution is None:
        return None
    catalogs = _catalogs_for_org(
        int(contribution.organization_id or 0),
        selected_form_id=int(contribution.receipt_method_legacy_id or 0),
    )
    return {
        "detail": detail,
        "line_range": range(1, 9),
        "people_options": _native_people_options(int(contribution.organization_id or 0)),
        "contributor_options": _native_contributor_options(int(contribution.organization_id or 0)),
        "type_options": catalogs["type_options"],
        "campaign_options": catalogs["campaign_options"],
        "receiving_options": catalogs["receiving_options"],
        "status_options": catalogs["status_options"],
        "original_total": detail["contribution"]["valor"],
        "original_total_fmt": detail["contribution"]["valor_fmt"],
    }


def manual_contribution_context_postgres() -> dict[str, Any]:
    organization_id = (
        PersonSnapshot.objects.filter(is_active=True)
        .order_by("organization_id", "legacy_id")
        .values_list("organization_id", flat=True)
        .first()
        or 1
    )
    catalogs = _catalogs_for_org(int(organization_id or 0))
    return {
        "today": datetime.now().date().isoformat(),
        "line_range": range(1, 9),
        "people_options": _native_people_options(int(organization_id or 0)),
        "contributor_options": _native_contributor_options(int(organization_id or 0)),
        "type_options": catalogs["type_options"],
        "campaign_options": catalogs["campaign_options"],
        "receiving_options": catalogs["receiving_options"],
        "status_options": catalogs["status_options"],
    }


def new_contribution_context_postgres(person_id: int) -> dict[str, Any] | None:
    person = _active_person_snapshot(int(person_id or 0)) if int(person_id or 0) else None
    if person is None:
        return None
    total_value = float(
        PersonContributionSnapshot.objects.filter(person__legacy_id=int(person_id or 0), is_active=True).aggregate(
            value=models.Sum("amount")
        ).get("value")
        or 0
    )
    catalogs = _catalogs_for_org(int(person.organization_id or 0))
    return {
        "person": {
            "id": int(person.legacy_id or 0),
            "codigo": person.internal_code or "",
            "nome": person.name or "",
            "cpf": format_cpf(person.cpf),
            "status": format_status(person.status),
            "sigla": status_sigla(person.status, True),
        },
        "catalogs": catalogs,
        "today": datetime.now().date().isoformat(),
        "total_fmt": br_money(total_value),
    }


def _native_line_observations(base: str, line_notes: str, origin_note: str) -> str:
    parts = [normalize_query(base), normalize_query(line_notes), normalize_query(origin_note)]
    return "\n".join(part for part in parts if part)


def _contributor_cache_from_person(person: PersonSnapshot) -> dict[str, object]:
    return {
        "person_legacy_id": int(person.legacy_id or 0),
        "contributor_legacy_id": _native_contributor_id_for_person(person),
        "native_aux_contributor_id": None,
        "contributor_source": "person_snapshot",
        "contributor_name": person.name or "",
        "contributor_document": person.cpf or "",
        "contributor_type": "pf",
    }


def _contributor_cache_from_aux(aux: NativeAuxContributor) -> dict[str, object]:
    person_id = int(aux.person_legacy_id or 0) or None
    source = "native_aux_contributor"
    if int(aux.legacy_reference_id or 0):
        source = "legacy_aux_contributor"
    return {
        "person_legacy_id": person_id,
        "contributor_legacy_id": int(aux.legacy_reference_id or 0) or None,
        "native_aux_contributor_id": int(aux.pk or 0) or None,
        "contributor_source": source,
        "contributor_name": aux.name or "",
        "contributor_document": aux.primary_document or "",
        "contributor_type": aux.contributor_type or _contributor_type(aux.name, aux.primary_document),
    }


def _resolve_contributor_selection(
    *,
    organization_id: int,
    index: int,
    person_id: int = 0,
    contributor_id: int = 0,
    contributor_name: str = "",
    document: str = "",
    source: str = "rateio_manual_postgres",
) -> dict[str, object]:
    resolved_person = _active_person_snapshot(person_id) if person_id else None
    contributor_name = normalize_query(contributor_name)
    document = normalize_query(document)
    if resolved_person is not None and int(resolved_person.organization_id or 0) != int(organization_id or 0):
        raise LegacyWriteError(f"Pessoa invalida na linha {index}.")

    if contributor_id:
        legacy_row = _legacy_contributor_row(organization_id, contributor_id)
        if legacy_row is None:
            raise LegacyWriteError(f"Contribuinte auxiliar invalido na linha {index}.")
        contributor_person_id = moneyless_int(legacy_row.get("pessoa_id")) or 0
        if resolved_person is not None and contributor_person_id and contributor_person_id != int(resolved_person.legacy_id or 0):
            raise LegacyWriteError(f"Contribuinte da linha {index} nao pertence a pessoa escolhida.")
        if resolved_person is None and contributor_person_id:
            resolved_person = _active_person_snapshot(contributor_person_id)
        aux = _resolve_native_aux_contributor(
            organization_id=organization_id,
            legacy_contributor_id=contributor_id,
            person_legacy_id=int(resolved_person.legacy_id or 0) if resolved_person else None,
            source=source,
        )
        cache = _contributor_cache_from_aux(aux)
        if resolved_person is not None:
            cache["person_legacy_id"] = int(resolved_person.legacy_id or 0)
        return cache

    if contributor_name or document:
        if resolved_person is not None:
            aux = _resolve_native_aux_contributor(
                organization_id=organization_id,
                person_legacy_id=int(resolved_person.legacy_id or 0),
                name=contributor_name or resolved_person.name or "",
                document=document or resolved_person.cpf or "",
                source=source,
            )
            cache = _contributor_cache_from_aux(aux)
            cache["person_legacy_id"] = int(resolved_person.legacy_id or 0)
            return cache
        aux = _resolve_native_aux_contributor(
            organization_id=organization_id,
            name=contributor_name or document,
            document=document,
            source=source,
        )
        return _contributor_cache_from_aux(aux)

    if resolved_person is not None:
        return _contributor_cache_from_person(resolved_person)

    raise LegacyWriteError(
        f"Na linha {index}, escolha uma pessoa do rol, um contribuinte auxiliar ou informe nome/documento."
    )


def _native_split_line_payloads(data: Any, organization_id: int) -> list[dict[str, object]]:
    getter = getattr(data, "get", None)
    line_count = moneyless_int(getter("line_count") if getter else 0) or 8
    rows: list[dict[str, object]] = []
    catalogs = _catalogs_for_org(organization_id)
    valid_type_ids = {int(item.get("id") or 0) for item in catalogs["type_options"]}
    valid_campaign_ids = {int(item.get("id") or 0) for item in catalogs["campaign_options"]}
    for index in range(1, line_count + 1):
        value_text = str(getter(f"linha_valor_{index}", "") if getter else "").strip()
        person_id = moneyless_int(getter(f"linha_pessoa_id_{index}") if getter else 0)
        contributor_id = moneyless_int(getter(f"linha_contribuinte_id_{index}") if getter else 0)
        contributor_name = normalize_query(getter(f"linha_contribuinte_nome_{index}") if getter else "")
        document = normalize_query(getter(f"linha_documento_{index}") if getter else "")
        type_id = moneyless_int(getter(f"linha_tipo_contribuicao_id_{index}") if getter else 0)
        campaign_id = moneyless_int(getter(f"linha_campanha_id_{index}") if getter else 0)
        notes = normalize_query(getter(f"linha_observacoes_{index}") if getter else "")
        if not any([value_text, person_id, contributor_id, contributor_name, document, type_id, campaign_id, notes]):
            continue
        if not value_text:
            raise LegacyWriteError(f"Informe o valor da linha {index}.")
        try:
            value = parse_money(value_text)
        except ValueError as exc:
            raise LegacyWriteError(str(exc)) from exc
        if not type_id or type_id not in valid_type_ids:
            raise LegacyWriteError(f"Escolha a destinacao/tipo da linha {index}.")
        if campaign_id and campaign_id not in valid_campaign_ids:
            raise LegacyWriteError(f"Campanha invalida na linha {index}.")
        resolved = _resolve_contributor_selection(
            organization_id=organization_id,
            index=index,
            person_id=person_id,
            contributor_id=contributor_id,
            contributor_name=contributor_name,
            document=document,
        )
        rows.append(
            {
                "index": index,
                "pessoa_id": int(resolved.get("person_legacy_id") or 0) or None,
                "contribuinte_id": int(resolved.get("contributor_legacy_id") or 0) or None,
                "native_aux_contributor_id": int(resolved.get("native_aux_contributor_id") or 0) or None,
                "contributor_source": str(resolved.get("contributor_source") or ""),
                "contributor_name": str(resolved.get("contributor_name") or ""),
                "contributor_document": str(resolved.get("contributor_document") or ""),
                "contributor_type": str(resolved.get("contributor_type") or ""),
                "tipo_contribuicao_id": type_id,
                "campanha_id": campaign_id or None,
                "valor": float(value),
                "observacoes": notes,
            }
        )
    if not rows:
        raise LegacyWriteError("Informe pelo menos uma linha de rateio/contribuicao.")
    return rows


def split_contribution_postgres(contribution_id: int, payload: Any, actor: str = "") -> list[int]:
    contribution = NativeContribution.objects.filter(legacy_id=int(contribution_id or 0), is_active=True).first()
    if contribution is None:
        raise LegacyWriteError("Contribuicao original nao encontrada.")
    getter = getattr(payload, "get", None)
    justification = normalize_query(getter("justificativa") if getter else "")
    if len(justification) < 8:
        raise LegacyWriteError("Informe uma justificativa com pelo menos 8 caracteres para ratear a contribuicao.")
    lines = _native_split_line_payloads(payload, int(contribution.organization_id or 0))
    original_total = round(float(contribution.amount or 0), 2)
    split_total = round(sum(float(line["valor"]) for line in lines), 2)
    if abs(split_total - original_total) > 0.009:
        raise LegacyWriteError(f"A soma do rateio ({split_total:.2f}) deve fechar com o valor original ({original_total:.2f}).")
    received_on = normalize_query(getter("data_recebimento") if getter else "") or normalize_query(contribution.received_at_raw)
    try:
        competence, competence_order = competencia_from_date(received_on)
        received_on_date = datetime.strptime(received_on, "%Y-%m-%d").date()
    except ValueError as exc:
        raise LegacyWriteError(str(exc)) from exc
    form_id = moneyless_int(getter("forma_recebimento_id") if getter else 0) or int(contribution.receipt_method_legacy_id or 0) or None
    status = normalize_query(getter("status_operacional", contribution.operational_status or "regular") if getter else contribution.operational_status or "regular") or "regular"
    if status not in CONTRIBUTION_STATUS_OPTIONS:
        raise LegacyWriteError("Status operacional invalido para rateio.")
    catalogs = _catalogs_for_org(
        int(contribution.organization_id or 0),
        selected_form_id=int(form_id or 0),
    )
    valid_form_ids = {int(item.get("id") or 0) for item in catalogs["receiving_options"]}
    if form_id and form_id not in valid_form_ids:
        raise LegacyWriteError("Forma de recebimento invalida.")
    header_notes = normalize_query(getter("observacoes") if getter else "") or normalize_query(contribution.notes)
    origin_note = f"Rateio manual da contribuicao original #{contribution_id}; comprovante/envelope/e-mail conferido pelo operador."
    before = {
        "id_publico": int(contribution.legacy_id or 0),
        "valor": float(contribution.amount or 0),
        "competencia": contribution.competence or "",
        "status_operacional": contribution.operational_status or "",
    }
    created_ids: list[int] = [int(contribution.legacy_id or 0)]
    with transaction.atomic():
        first = lines[0]
        contribution.person_legacy_id = int(first["pessoa_id"] or 0) or None
        contribution.contributor_legacy_id = int(first["contribuinte_id"] or 0) or None
        contribution.native_aux_contributor_id = int(first["native_aux_contributor_id"] or 0) or None
        contribution.contributor_source = str(first["contributor_source"] or "")
        contribution.contributor_name = str(first["contributor_name"] or "")
        contribution.contributor_document = str(first["contributor_document"] or "")
        contribution.contributor_type = str(first["contributor_type"] or "")
        contribution.contribution_type_legacy_id = int(first["tipo_contribuicao_id"] or 0)
        contribution.contribution_type_name = _selected_option_name(catalogs["type_options"], int(first["tipo_contribuicao_id"] or 0))
        contribution.campaign_legacy_id = int(first["campanha_id"] or 0) or None
        contribution.campaign_name = _selected_option_name(catalogs["campaign_options"], int(first["campanha_id"] or 0))
        contribution.received_at = received_on_date
        contribution.received_at_raw = received_on
        contribution.competence = competence
        contribution.competence_order = competence_order
        contribution.amount = Decimal(str(first["valor"]))
        contribution.receipt_method_legacy_id = int(form_id or 0) or None
        contribution.receipt_method_name = _selected_option_name(catalogs["receiving_options"], int(form_id or 0))
        contribution.notes = _native_line_observations(header_notes, str(first["observacoes"] or ""), origin_note)
        contribution.operational_status = status
        contribution.updated_by = actor
        contribution.save()
        _sync_person_contribution_snapshot(contribution)
        for line in lines[1:]:
            child = NativeContribution.objects.create(
                legacy_id=_next_native_contribution_public_id(),
                organization_id=int(contribution.organization_id or 0),
                person_legacy_id=int(line["pessoa_id"] or 0) or None,
                contributor_legacy_id=int(line["contribuinte_id"] or 0) or None,
                native_aux_contributor_id=int(line["native_aux_contributor_id"] or 0) or None,
                contributor_source=str(line["contributor_source"] or ""),
                contributor_name=str(line["contributor_name"] or ""),
                contributor_document=str(line["contributor_document"] or ""),
                contributor_type=str(line["contributor_type"] or ""),
                received_at=received_on_date,
                received_at_raw=received_on,
                competence=competence,
                competence_order=competence_order,
                amount=Decimal(str(line["valor"])),
                contribution_type_legacy_id=int(line["tipo_contribuicao_id"] or 0),
                contribution_type_name=_selected_option_name(catalogs["type_options"], int(line["tipo_contribuicao_id"] or 0)),
                campaign_legacy_id=int(line["campanha_id"] or 0) or None,
                campaign_name=_selected_option_name(catalogs["campaign_options"], int(line["campanha_id"] or 0)),
                receipt_method_legacy_id=int(form_id or 0) or None,
                receipt_method_name=_selected_option_name(catalogs["receiving_options"], int(form_id or 0)),
                operational_status=status,
                notes=_native_line_observations(
                    header_notes,
                    str(line["observacoes"] or ""),
                    f"{origin_note}\nLinha complementar do rateio nativo.",
                ),
                split_parent_legacy_id=int(contribution.legacy_id or 0),
                source="postgres_native_split",
                is_active=True,
                created_by=actor,
                updated_by=actor,
            )
            _sync_person_contribution_snapshot(child)
            created_ids.append(int(child.legacy_id or 0))
    try:
        record_django_audit_event(
            actor=actor,
            action="ratear_contribuicao_postgres",
            table_name="contributions_nativecontribution",
            record_id=int(contribution.pk or 0),
            organization_id=int(contribution.organization_id or 0),
            source="postgres_native_contributions",
            summary=f"Rateio nativo da contribuicao #{int(contribution.legacy_id or 0)}.",
            before=before,
            after={
                "id_publico": int(contribution.legacy_id or 0),
                "linhas": len(created_ids),
                "ids_criados": created_ids,
                "justificativa_operador": justification,
                "status_operacional": status,
            },
        )
    except Exception:
        pass
    return created_ids


def create_contribution_postgres(payload: Any, actor: str = "") -> int:
    person_id = moneyless_int(getattr(payload, "get", lambda *_args, **_kwargs: 0)("pessoa_id"))
    person = _active_person_snapshot(person_id)
    values = _contribution_payload(payload)
    catalogs = _catalogs_for_org(
        int(person.organization_id or 0),
        selected_type_id=int(values["tipo_contribuicao_id"] or 0),
        selected_form_id=int(values["forma_recebimento_id"] or 0),
        selected_campaign_id=int(values["campanha_id"] or 0),
    )
    type_name = _selected_option_name(catalogs["type_options"], int(values["tipo_contribuicao_id"] or 0))
    if not type_name:
        raise LegacyWriteError("Tipo de contribuicao invalido.")
    contribution = NativeContribution.objects.create(
        legacy_id=_next_native_contribution_public_id(),
        organization_id=int(person.organization_id or 0),
        person_legacy_id=int(person.legacy_id or 0),
        contributor_legacy_id=_native_contributor_id_for_person(person),
        contributor_source="person_snapshot",
        contributor_name=person.name or "",
        contributor_document=person.cpf or "",
        contributor_type="pf",
        received_at=values["data_recebimento_date"],
        received_at_raw=str(values["data_recebimento"] or ""),
        competence=str(values["competencia"] or ""),
        competence_order=int(values["competencia_ordem"] or 0),
        amount=Decimal(str(values["valor"] or 0)),
        contribution_type_legacy_id=int(values["tipo_contribuicao_id"] or 0),
        contribution_type_name=type_name,
        campaign_legacy_id=int(values["campanha_id"] or 0) or None,
        campaign_name=_selected_option_name(catalogs["campaign_options"], int(values["campanha_id"] or 0)),
        receipt_method_legacy_id=int(values["forma_recebimento_id"] or 0) or None,
        receipt_method_name=_selected_option_name(catalogs["receiving_options"], int(values["forma_recebimento_id"] or 0)),
        operational_status=str(values["status_operacional"] or ""),
        notes=str(values["observacoes"] or ""),
        source="postgres_native_manual",
        is_active=True,
        created_by=actor,
        updated_by=actor,
    )
    _sync_person_contribution_snapshot(contribution)
    try:
        record_django_audit_event(
            actor=actor,
            action="lancar_contribuicao_postgres",
            table_name="contributions_nativecontribution",
            record_id=int(contribution.pk or 0),
            organization_id=int(contribution.organization_id or 0),
            source="postgres_native_contributions",
            summary=f"Contribuicao nativa #{int(contribution.legacy_id or 0)} criada no Postgres.",
            after={
                "id_publico": int(contribution.legacy_id or 0),
                "person_id": int(contribution.person_legacy_id or 0),
                "valor": float(contribution.amount or 0),
                "competencia": contribution.competence or "",
                "status_operacional": contribution.operational_status or "",
                "justificativa_operador": values["justificativa"],
            },
        )
    except Exception:
        pass
    return int(contribution.legacy_id or 0)


def create_manual_contribution_batch_postgres(payload: Any, actor: str = "") -> list[int]:
    getter = getattr(payload, "get", None)
    received_on = normalize_query(getter("data_recebimento") if getter else "")
    try:
        competence, competence_order = competencia_from_date(received_on)
        received_on_date = datetime.strptime(received_on, "%Y-%m-%d").date()
    except ValueError as exc:
        raise LegacyWriteError(str(exc)) from exc
    organization_id = (
        PersonSnapshot.objects.filter(is_active=True)
        .order_by("organization_id", "legacy_id")
        .values_list("organization_id", flat=True)
        .first()
        or 1
    )
    form_id = moneyless_int(getter("forma_recebimento_id") if getter else 0) or None
    status = normalize_query(getter("status_operacional", "regular") if getter else "regular") or "regular"
    if status not in CONTRIBUTION_STATUS_OPTIONS:
        raise LegacyWriteError("Status operacional invalido para lancamento manual.")
    justification = normalize_query(getter("justificativa") if getter else "")
    if len(justification) < 8:
        raise LegacyWriteError("Informe uma justificativa com pelo menos 8 caracteres.")
    header_notes = normalize_query(getter("observacoes") if getter else "")
    source_label = normalize_query(getter("origem_operacional") if getter else "")
    expected_total_text = normalize_query(getter("valor_total") if getter else "")
    lines = _native_split_line_payloads(payload, int(organization_id or 0))
    catalogs = _catalogs_for_org(
        int(organization_id or 0),
        selected_form_id=int(form_id or 0),
    )
    valid_form_ids = {int(item.get("id") or 0) for item in catalogs["receiving_options"]}
    if form_id and form_id not in valid_form_ids:
        raise LegacyWriteError("Forma de recebimento invalida.")
    total = round(sum(float(line["valor"]) for line in lines), 2)
    if expected_total_text:
        expected_total = round(float(parse_money(expected_total_text)), 2)
        if abs(total - expected_total) > 0.009:
            raise LegacyWriteError(f"A soma das linhas ({total:.2f}) nao fecha com o total informado ({expected_total:.2f}).")
    origin_note = f"Origem manual: {source_label or 'envelope/e-mail/comprovante informado pelo operador'}."
    created_ids: list[int] = []
    with transaction.atomic():
        for line in lines:
            contribution = NativeContribution.objects.create(
                legacy_id=_next_native_contribution_public_id(),
                organization_id=int(organization_id or 0),
                person_legacy_id=int(line["pessoa_id"] or 0) or None,
                contributor_legacy_id=int(line["contribuinte_id"] or 0) or None,
                native_aux_contributor_id=int(line["native_aux_contributor_id"] or 0) or None,
                contributor_source=str(line["contributor_source"] or ""),
                contributor_name=str(line["contributor_name"] or ""),
                contributor_document=str(line["contributor_document"] or ""),
                contributor_type=str(line["contributor_type"] or ""),
                received_at=received_on_date,
                received_at_raw=received_on,
                competence=competence,
                competence_order=competence_order,
                amount=Decimal(str(line["valor"])),
                contribution_type_legacy_id=int(line["tipo_contribuicao_id"] or 0),
                contribution_type_name=_selected_option_name(catalogs["type_options"], int(line["tipo_contribuicao_id"] or 0)),
                campaign_legacy_id=int(line["campanha_id"] or 0) or None,
                campaign_name=_selected_option_name(catalogs["campaign_options"], int(line["campanha_id"] or 0)),
                receipt_method_legacy_id=int(form_id or 0) or None,
                receipt_method_name=_selected_option_name(catalogs["receiving_options"], int(form_id or 0)),
                operational_status=status,
                notes=_native_line_observations(header_notes, str(line["observacoes"] or ""), origin_note),
                source="postgres_native_manual_batch",
                is_active=True,
                created_by=actor,
                updated_by=actor,
            )
            _sync_person_contribution_snapshot(contribution)
            created_ids.append(int(contribution.legacy_id or 0))
    return created_ids


def update_contribution_postgres(contribution_id: int, payload: Any, actor: str = "") -> None:
    contribution = NativeContribution.objects.filter(legacy_id=int(contribution_id or 0), is_active=True).first()
    if contribution is None:
        raise LegacyWriteError("Contribuicao nao encontrada.")
    values = _contribution_payload(payload)
    catalogs = _catalogs_for_org(
        int(contribution.organization_id or 0),
        selected_type_id=int(values["tipo_contribuicao_id"] or 0),
        selected_form_id=int(values["forma_recebimento_id"] or 0),
        selected_campaign_id=int(values["campanha_id"] or 0),
    )
    type_name = _selected_option_name(catalogs["type_options"], int(values["tipo_contribuicao_id"] or 0))
    if not type_name:
        raise LegacyWriteError("Tipo de contribuicao invalido.")
    before = {
        "id_publico": int(contribution.legacy_id or 0),
        "valor": float(contribution.amount or 0),
        "competencia": contribution.competence or "",
        "status_operacional": contribution.operational_status or "",
        "tipo": contribution.contribution_type_name or "",
    }
    contribution.received_at = values["data_recebimento_date"]
    contribution.received_at_raw = str(values["data_recebimento"] or "")
    contribution.competence = str(values["competencia"] or "")
    contribution.competence_order = int(values["competencia_ordem"] or 0)
    contribution.amount = Decimal(str(values["valor"] or 0))
    contribution.contribution_type_legacy_id = int(values["tipo_contribuicao_id"] or 0)
    contribution.contribution_type_name = type_name
    contribution.campaign_legacy_id = int(values["campanha_id"] or 0) or None
    contribution.campaign_name = _selected_option_name(catalogs["campaign_options"], int(values["campanha_id"] or 0))
    contribution.receipt_method_legacy_id = int(values["forma_recebimento_id"] or 0) or None
    contribution.receipt_method_name = _selected_option_name(catalogs["receiving_options"], int(values["forma_recebimento_id"] or 0))
    contribution.operational_status = str(values["status_operacional"] or "")
    contribution.notes = str(values["observacoes"] or "")
    contribution.updated_by = actor
    contribution.save()
    _sync_person_contribution_snapshot(contribution)
    try:
        record_django_audit_event(
            actor=actor,
            action="ajustar_contribuicao_postgres",
            table_name="contributions_nativecontribution",
            record_id=int(contribution.pk or 0),
            organization_id=int(contribution.organization_id or 0),
            source="postgres_native_contributions",
            summary=f"Contribuicao nativa #{int(contribution.legacy_id or 0)} ajustada no Postgres.",
            before=before,
            after={
                "id_publico": int(contribution.legacy_id or 0),
                "valor": float(contribution.amount or 0),
                "competencia": contribution.competence or "",
                "status_operacional": contribution.operational_status or "",
                "tipo": contribution.contribution_type_name or "",
                "justificativa_operador": values["justificativa"],
            },
        )
    except Exception:
        pass


def get_contribution_detail_postgres(contribution_id: int) -> dict[str, Any] | None:
    contribution = NativeContribution.objects.filter(legacy_id=int(contribution_id or 0), is_active=True).first()
    if contribution is None:
        return None
    person = PersonSnapshot.objects.filter(legacy_id=int(contribution.person_legacy_id or 0)).first()
    contributor = None
    aux = None
    if int(contribution.native_aux_contributor_id or 0):
        aux = NativeAuxContributor.objects.filter(pk=int(contribution.native_aux_contributor_id or 0)).first()
    if int(contribution.contributor_legacy_id or 0):
        contributor = _legacy_contributor_row(int(contribution.organization_id or 0), int(contribution.contributor_legacy_id or 0))
    catalogs = _catalogs_for_org(
        int(contribution.organization_id or 0),
        selected_type_id=int(contribution.contribution_type_legacy_id or 0),
        selected_form_id=int(contribution.receipt_method_legacy_id or 0),
        selected_campaign_id=int(contribution.campaign_legacy_id or 0),
    )
    status_options = list(catalogs["status_options"])
    for option in status_options:
        option["selected"] = option["value"] == (contribution.operational_status or "regular")
    audit_rows = [
        {
            "acao": event.action or "",
            "criado_em": br_datetime(event.created_at),
            "antes": str(event.before or ""),
            "depois": str(event.after or ""),
        }
        for event in AuditEvent.objects.filter(
            table_name="contributions_nativecontribution",
            record_id=int(contribution.pk or 0),
        ).order_by("-created_at", "-id")[:12]
    ]
    return {
        "is_native": True,
        "can_split": True,
        "contribution": {
            "id": int(contribution.legacy_id or 0),
            "data": br_date(contribution.received_at_raw or contribution.received_at),
            "data_raw": contribution.received_at_raw or "",
            "competencia": contribution.competence or "",
            "valor": float(contribution.amount or 0),
            "valor_fmt": br_money(contribution.amount or 0),
            "valor_input": br_money(contribution.amount or 0).replace("R$ ", ""),
            "status": contribution.operational_status or "regular",
            "status_label": (contribution.operational_status or "regular").replace("_", " ").title(),
            "observacoes": contribution.notes or "",
            "ativo": bool(contribution.is_active),
            "criado_em": br_datetime(contribution.created_at),
            "atualizado_em": br_datetime(contribution.updated_at),
            "person_id": int(contribution.person_legacy_id or 0),
            "person_name": person.name if person else "",
            "person_code": person.internal_code if person else "",
            "person_cpf": format_cpf(person.cpf if person else ""),
            "person_status": format_status(person.status if person else ""),
            "person_sigla": status_sigla(person.status if person else "", bool(person)),
            "contributor_id": int(contribution.contributor_legacy_id or 0),
            "contributor_name": contribution.contributor_name or (aux.name if aux else str((contributor or {}).get("nome") or "")),
            "contributor_type": contribution.contributor_type or (aux.contributor_type if aux else str((contributor or {}).get("tipo") or "")),
            "contributor_document": contribution.contributor_document or (aux.primary_document if aux else str((contributor or {}).get("documento_principal") or "")),
            "type_name": contribution.contribution_type_name or "",
            "campaign_name": contribution.campaign_name or "",
            "form_name": contribution.receipt_method_name or "",
            "pix_movement_id": int(contribution.pix_movement_legacy_id or 0),
            "pix_lot_id": 0,
            "statement_movement_id": int(contribution.statement_movement_legacy_id or 0),
            "statement_lot_id": 0,
            "statement_bank": "",
        },
        "type_options": catalogs["type_options"],
        "campaign_options": catalogs["campaign_options"],
        "receiving_options": catalogs["receiving_options"],
        "status_options": status_options,
        "audit_rows": audit_rows,
    }


def list_contributions_postgres(q: str = "", competencia: str = "", status: str = "") -> dict[str, Any]:
    q = normalize_query(q)
    competencia = normalize_query(competencia)
    status = normalize_query(status)
    queryset = NativeContribution.objects.filter(is_active=True).order_by("legacy_id")
    if competencia:
        queryset = queryset.filter(competence=competencia)
    if status:
        queryset = queryset.filter(operational_status=status)
    people = {
        int(person.legacy_id or 0): person
        for person in PersonSnapshot.objects.filter(
            legacy_id__in=list(queryset.values_list("person_legacy_id", flat=True).distinct())
        )
    }
    items: list[dict[str, Any]] = []
    total_value = 0.0
    for row in queryset:
        person = people.get(int(row.person_legacy_id or 0))
        identity_name = person.name if person else (row.contributor_name or "")
        identity_document = person.cpf if person else (row.contributor_document or "")
        if q:
            text = " ".join(
                [
                    normalize_query(identity_name),
                    normalize_query(row.notes),
                    normalize_query(identity_document),
                    normalize_query(row.campaign_name),
                    normalize_query(row.receipt_method_name),
                    normalize_query(row.contribution_type_name),
                ]
            ).lower()
            if q.lower() not in text:
                continue
        identity = contribution_report_identity(
            identity_name,
            "",
            identity_document,
        )
        total_value += float(row.amount or 0)
        items.append(
            {
                "id": int(row.legacy_id or 0),
                "detail_url": f"/contributions/{int(row.legacy_id or 0)}/",
                "data": br_date(row.received_at_raw or row.received_at),
                "data_raw": row.received_at_raw or "",
                "competencia": row.competence or "",
                "competencia_ordem": int(row.competence_order or 0),
                "nome": identity["name"] or "Contribuinte nao vinculado",
                "nome_original": person.name if person else "",
                "sort_key": identity["sort_key"],
                "group_kind": identity["group_kind"],
                "documento": identity["document"],
                "sigla": status_sigla(person.status if person else "", bool(person)),
                "tipo": row.contribution_type_name or "Sem tipo",
                "forma": row.receipt_method_name or "Sem forma",
                "status": row.operational_status or "regular",
                "valor": float(row.amount or 0),
                "valor_fmt": br_money(row.amount or 0),
            }
        )
    items.sort(
        key=lambda item: (
            0 if item["group_kind"] == "nome" else 1,
            str(item["sort_key"]),
            str(item["data_raw"]),
            int(item["competencia_ordem"] or 0),
            int(item["id"] or 0),
        )
    )
    status_counts: dict[str, int] = {}
    for item in items:
        key = str(item["status"] or "regular")
        status_counts[key] = status_counts.get(key, 0) + 1
    competencias = sorted({str(item["competencia"] or "") for item in items if item["competencia"]})
    return {
        "items": items,
        "total": len(items),
        "shown": len(items),
        "total_value": total_value,
        "total_value_fmt": br_money(total_value),
        "q": q,
        "competencia": competencia,
        "status": status,
        "competencias": competencias,
        "status_options": [{"value": key, "count": value} for key, value in sorted(status_counts.items())],
        "limit": len(items),
    }


def combine_contribution_dashboards(*dashboards: dict[str, Any]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    total = 0
    total_value = 0.0
    competencias: set[str] = set()
    status_counts: dict[str, int] = {}
    q = ""
    competencia = ""
    status = ""
    for dashboard in dashboards:
        if not dashboard:
            continue
        q = q or str(dashboard.get("q") or "")
        competencia = competencia or str(dashboard.get("competencia") or "")
        status = status or str(dashboard.get("status") or "")
        items.extend(list(dashboard.get("items") or []))
        total += int(dashboard.get("total") or 0)
        total_value += float(dashboard.get("total_value") or 0)
        for value in dashboard.get("competencias") or []:
            if value:
                competencias.add(str(value))
        for option in dashboard.get("status_options") or []:
            key = str(option.get("value") or "")
            status_counts[key] = status_counts.get(key, 0) + int(option.get("count") or 0)
    items.sort(
        key=lambda item: (
            0 if item["group_kind"] == "nome" else 1,
            str(item["sort_key"]),
            str(item["data_raw"]),
            int(item["competencia_ordem"] or 0),
            int(item["id"] or 0),
        )
    )
    return {
        "items": items,
        "total": total,
        "shown": len(items),
        "total_value": total_value,
        "total_value_fmt": br_money(total_value),
        "q": q,
        "competencia": competencia,
        "status": status,
        "competencias": sorted(competencias),
        "status_options": [{"value": key, "count": value} for key, value in sorted(status_counts.items())],
        "limit": len(items),
    }


def person_statement_data_postgres(
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
    person = PersonSnapshot.objects.filter(legacy_id=int(person_id or 0), is_active=True).first()
    if person is None:
        return None
    queryset = PersonContributionSnapshot.objects.filter(
        person__legacy_id=int(person_id or 0),
        person__is_active=True,
        is_active=True,
    ).order_by("competence_order", "received_at", "legacy_id")
    if year:
        queryset = queryset.filter(received_at_raw__startswith=f"{year}-")
    if competencia:
        queryset = queryset.filter(competence=competencia)
    if date_start:
        queryset = queryset.filter(received_at_raw__gte=date_start)
    if date_end:
        queryset = queryset.filter(received_at_raw__lte=date_end)
    if selected_type_ids:
        selected_type_names = set(
            ContributionTypeSnapshot.objects.filter(
                legacy_id__in=selected_type_ids,
                is_active=True,
            ).values_list("name", flat=True)
        )
        queryset = queryset.filter(contribution_type_name__in=selected_type_names)
    rows = list(queryset)
    years = sorted(
        {
            str(value)[:4]
            for value in PersonContributionSnapshot.objects.filter(
                person__legacy_id=int(person_id or 0),
                person__is_active=True,
                is_active=True,
            ).exclude(received_at_raw="").values_list("received_at_raw", flat=True)
            if str(value)[:4]
        },
        reverse=True,
    )
    competencias = list(
        PersonContributionSnapshot.objects.filter(
            person__legacy_id=int(person_id or 0),
            person__is_active=True,
            is_active=True,
        ).exclude(competence="").values_list("competence", flat=True).distinct().order_by("-competence_order", "competence")
    )
    type_options = [
        {
            "id": int(row.legacy_id or 0),
            "nome": row.name or "",
            "selected": int(row.legacy_id or 0) in selected_type_ids,
        }
        for row in ContributionTypeSnapshot.objects.filter(
            organization_id=int(person.organization_id or 0),
            is_active=True,
        ).order_by("name", "legacy_id")
    ]
    entries: list[dict[str, Any]] = []
    total = 0.0
    current_competence = ""
    subtotal = 0.0
    competence_count = 0
    for row in rows:
        row_competence = row.competence or ""
        value = float(row.amount or 0)
        if current_competence and row_competence != current_competence:
            entries.append({"kind": "subtotal", "competencia": current_competence, "subtotal": subtotal, "subtotal_fmt": br_money(subtotal)})
            subtotal = 0.0
        if row_competence != current_competence:
            current_competence = row_competence
            competence_count += 1
        total += value
        subtotal += value
        entries.append(
            {
                "kind": "item",
                "id": int(row.legacy_id or 0),
                "data": br_date(row.received_at_raw or (row.received_at.isoformat() if row.received_at else "")),
                "competencia": row_competence,
                "tipo": row.contribution_type_name or "",
                "forma": row.receipt_method_name or "",
                "observacoes": row.notes or "",
                "valor_fmt": br_money(value),
                "detail_url": f"/contributions/{int(row.legacy_id or 0)}/",
            }
        )
    if current_competence:
        entries.append({"kind": "subtotal", "competencia": current_competence, "subtotal": subtotal, "subtotal_fmt": br_money(subtotal)})
    return {
        "person": {
            "id": int(person.legacy_id or 0),
            "nome": person.name or "",
            "codigo": person.internal_code or "",
            "cpf": format_cpf(person.cpf),
            "status": format_status(person.status),
            "sigla": status_sigla(person.status, True),
            "email": person.primary_email or "",
        },
        "entries": entries,
        "summary": {
            "lancamentos": len(rows),
            "competencias": competence_count,
            "total": total,
            "total_fmt": br_money(total),
        },
        "years": years,
        "competencias": competencias,
        "type_options": type_options,
        "filters": {
            "year": year,
            "competencia": competencia,
            "date_start": date_start,
            "date_end": date_end,
            "type_ids": selected_type_ids,
        },
    }

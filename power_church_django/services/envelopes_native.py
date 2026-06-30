from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from django.db import models, transaction
from django.utils import timezone

from power_church_core.formatting import br_date, br_datetime, br_money, competencia_from_date, parse_money
from power_church_core.normalization import normalize_query
from power_church_django.apps.contributions.models import (
    NativeAuxContributor,
    NativeContribution,
    NativeEnvelope,
    NativeEnvelopeItem,
    NativeEnvelopeLot,
    NativeEnvelopeProfileUpdate,
)
from power_church_django.apps.people.models import PersonAddressSnapshot, PersonContactSnapshot, PersonSnapshot
from power_church_django.services.contributions_native import (
    _catalogs_for_org,
    _contributor_cache_from_aux,
    _contributor_cache_from_person,
    _native_contributor_options,
    _native_people_options,
    _next_native_contribution_public_id,
    _resolve_native_aux_contributor,
    _selected_option_name,
    _sync_person_contribution_snapshot,
)
from power_church_django.services.django_audit import record_django_audit_event
from power_church_django.services.runtime_errors import LegacyWriteError
from power_church_django.services.runtime_formatting import CONTRIBUTION_STATUS_OPTIONS, _clean_optional_text
from power_church_django.services.runtime_support import envelope_upload_root

ENVELOPE_PENDING_STATUS = "aguardando_digitacao"
ENVELOPE_IN_PROGRESS_STATUS = "em_digitacao"
ENVELOPE_IN_PROGRESS_TIMEOUT = timedelta(minutes=30)
ENVELOPE_OPEN_STATUSES = {ENVELOPE_PENDING_STATUS, ENVELOPE_IN_PROGRESS_STATUS}
ENVELOPE_STATUS_LABELS = {
    ENVELOPE_PENDING_STATUS: "Aguardando digitacao",
    ENVELOPE_IN_PROGRESS_STATUS: "Em digitacao",
    "lancado": "Lancado",
    "ignorado": "Ignorado",
    "duplicado": "Duplicado",
}


def _default_organization_id() -> int:
    value = PersonSnapshot.objects.filter(is_active=True).order_by("organization_id", "legacy_id").values_list("organization_id", flat=True).first()
    return int(value or 1)


def _next_native_envelope_legacy_id() -> int:
    value = NativeEnvelope.objects.aggregate(value=models.Max("legacy_id")).get("value") or 0
    return int(value or 0) + 1


def _next_native_envelope_item_legacy_id() -> int:
    value = NativeEnvelopeItem.objects.aggregate(value=models.Max("legacy_id")).get("value") or 0
    return int(value or 0) + 1


def _next_native_envelope_lot_legacy_id() -> int:
    value = NativeEnvelopeLot.objects.aggregate(value=models.Max("legacy_id")).get("value") or 0
    return int(value or 0) + 1


def _envelope_lock_owner_label(envelope: NativeEnvelope) -> str:
    return normalize_query(envelope.updated_by) or "outro operador"


def _envelope_status_label(status: str) -> str:
    return ENVELOPE_STATUS_LABELS.get(str(status or ""), str(status or ""))


def _is_stale_envelope_lock(envelope: NativeEnvelope, *, now: datetime | None = None) -> bool:
    if str(envelope.status or "") != ENVELOPE_IN_PROGRESS_STATUS:
        return False
    now = now or timezone.now()
    updated_at = envelope.updated_at or now
    return updated_at <= now - ENVELOPE_IN_PROGRESS_TIMEOUT


def _can_claim_envelope(envelope: NativeEnvelope, actor: str, *, now: datetime | None = None) -> bool:
    status = str(envelope.status or "")
    actor = normalize_query(actor) or "django"
    if status == ENVELOPE_PENDING_STATUS:
        return True
    if status != ENVELOPE_IN_PROGRESS_STATUS:
        return False
    if normalize_query(envelope.updated_by) == actor:
        return True
    return _is_stale_envelope_lock(envelope, now=now)


def _claim_envelope_for_digitization(envelope: NativeEnvelope, actor: str) -> None:
    actor = normalize_query(actor) or "django"
    envelope.status = ENVELOPE_IN_PROGRESS_STATUS
    envelope.updated_by = actor
    envelope.save(update_fields=["status", "updated_by", "updated_at"])
    if int(envelope.native_lot_legacy_id or 0):
        _refresh_envelope_lot_status_postgres(int(envelope.native_lot_legacy_id or 0), actor=actor)


def _refresh_envelope_lot_status_postgres(lot_id: int, actor: str = "") -> None:
    lot_id = int(lot_id or 0)
    if not lot_id:
        return
    has_open_work = NativeEnvelope.objects.filter(
        native_lot_legacy_id=lot_id,
        is_active=True,
        status__in=sorted(ENVELOPE_OPEN_STATUSES),
    ).exists()
    lot_status = "parcial" if has_open_work else "digitado"
    NativeEnvelopeLot.objects.filter(legacy_id=lot_id, is_active=True).update(
        status=lot_status,
        updated_by=normalize_query(actor) or "",
    )


def _parse_participant_reference(raw_value: object) -> tuple[int, int, str, str]:
    text = normalize_query(raw_value)
    if not text:
        return 0, 0, "", ""
    person_match = re.match(r"^Pessoa\s*#\s*(\d+)", text, flags=re.IGNORECASE)
    if person_match:
        return int(person_match.group(1)), 0, "", ""
    contributor_match = re.match(r"^Contribuinte\s*#\s*(\d+)", text, flags=re.IGNORECASE)
    if contributor_match:
        return 0, int(contributor_match.group(1)), "", ""
    document_match = re.search(r"(\d[\d.\-/ ]{8,}\d)", text)
    document = document_match.group(1) if document_match else ""
    name = re.split(r"\s+·\s+|\s+-\s+", text, maxsplit=1)[0].strip()
    return 0, 0, name or text, document


def _active_person(person_id: int) -> PersonSnapshot:
    person = PersonSnapshot.objects.filter(legacy_id=int(person_id or 0), is_active=True).first()
    if person is None:
        raise LegacyWriteError("Pessoa invalida para envelope.")
    return person


def _native_envelope_identity(
    *,
    organization_id: int,
    person_id: int = 0,
    contributor_id: int = 0,
    participant_ref: str = "",
    contributor_name: str = "",
    document: str = "",
    source: str = "envelope_manual_postgres",
) -> dict[str, object]:
    ref_person_id, ref_contributor_id, ref_name, ref_document = _parse_participant_reference(participant_ref)
    person_id = int(person_id or ref_person_id or 0)
    contributor_id = int(contributor_id or ref_contributor_id or 0)
    contributor_name = normalize_query(contributor_name) or normalize_query(ref_name)
    document = normalize_query(document) or normalize_query(ref_document)
    if person_id:
        person = _active_person(person_id)
        cache = _contributor_cache_from_person(person)
        cache["stored_name"] = person.name or contributor_name or ""
        return cache
    if contributor_id:
        aux = _resolve_native_aux_contributor(
            organization_id=organization_id,
            legacy_contributor_id=contributor_id,
            name=contributor_name,
            document=document,
            source=source,
        )
        cache = _contributor_cache_from_aux(aux)
        cache["stored_name"] = aux.name or contributor_name or ""
        return cache
    if contributor_name or document:
        aux = _resolve_native_aux_contributor(
            organization_id=organization_id,
            name=contributor_name or document,
            document=document,
            source=source,
        )
        cache = _contributor_cache_from_aux(aux)
        cache["stored_name"] = aux.name or contributor_name or ""
        return cache
    return {
        "person_legacy_id": None,
        "contributor_legacy_id": None,
        "native_aux_contributor_id": None,
        "contributor_source": "",
        "contributor_name": "",
        "contributor_document": "",
        "contributor_type": "",
        "stored_name": "",
    }


def _store_native_envelope_file(*, envelope_id: int, competence: str, upload: Any, lot_folder: str = "") -> dict[str, object]:
    if upload is None:
        return {"filename": "", "hash": "", "content_type": "", "size": 0, "path": ""}
    content = upload.read()
    file_hash = hashlib.sha256(content).hexdigest()
    original_name = getattr(upload, "name", "") or f"envelope_{envelope_id}"
    suffix = Path(original_name).suffix or ".bin"
    folder = Path(envelope_upload_root()) / "native" / (competence or "sem_competencia")
    if lot_folder:
        folder = folder / lot_folder
    folder.mkdir(parents=True, exist_ok=True)
    filename = f"envelope_{envelope_id:06d}_{file_hash[:10]}{suffix}"
    path = folder / filename
    path.write_bytes(content)
    return {
        "filename": original_name,
        "hash": file_hash,
        "content_type": getattr(upload, "content_type", "") or "",
        "size": len(content),
        "path": str(path),
    }


def _slug_folder(value: object) -> str:
    text = normalize_query(value)
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return slug or "lote"


def _digits_only(value: object) -> str:
    return re.sub(r"\D+", "", str(value or ""))


def _profile_compare_key(value: object) -> str:
    return normalize_query(value).casefold()


def _current_phone_values(person_id: int) -> tuple[str, set[str]]:
    person = PersonSnapshot.objects.filter(legacy_id=int(person_id or 0), is_active=True).only("primary_phone", "primary_whatsapp").first()
    current_values: set[str] = set()
    if person is not None:
        if person.primary_phone:
            current_values.add(_digits_only(person.primary_phone))
        if person.primary_whatsapp:
            current_values.add(_digits_only(person.primary_whatsapp))
    for row in PersonContactSnapshot.objects.filter(person__legacy_id=int(person_id or 0)).only("value", "contact_type"):
        if str(row.contact_type or "") in {"telefone", "celular", "whatsapp"}:
            digits = _digits_only(row.value)
            if digits:
                current_values.add(digits)
    primary = person.primary_phone if person is not None else ""
    return normalize_query(primary), current_values


def _current_address_value(person_id: int) -> str:
    row = (
        PersonAddressSnapshot.objects.filter(person__legacy_id=int(person_id or 0), is_primary=True)
        .order_by("legacy_id")
        .first()
    )
    if row is None:
        return ""
    parts = [
        normalize_query(row.street),
        normalize_query(row.number),
        normalize_query(row.complement),
        normalize_query(row.neighborhood),
        normalize_query(row.city),
        normalize_query(row.state),
        normalize_query(row.cep),
    ]
    return ", ".join(part for part in parts if part)


def _build_profile_update_defaults(
    *,
    envelope: NativeEnvelope,
    person_id: int,
    field_name: str,
    current_value: str,
    envelope_value: str,
    actor: str,
) -> dict[str, object]:
    return {
        "organization_id": int(envelope.organization_id or 0),
        "person_legacy_id": int(person_id or 0),
        "field_name": field_name,
        "current_value": normalize_query(current_value),
        "envelope_value": normalize_query(envelope_value),
        "status": NativeEnvelopeProfileUpdate.Status.PENDING,
        "notes": "Sugerido automaticamente porque o envelope contem dado diferente da ficha.",
        "updated_by": actor,
    }


def refresh_envelope_profile_updates_postgres(envelope: NativeEnvelope, actor: str = "") -> dict[str, int]:
    if not int(envelope.person_legacy_id or 0):
        deleted, _ = NativeEnvelopeProfileUpdate.objects.filter(envelope=envelope, status=NativeEnvelopeProfileUpdate.Status.PENDING).delete()
        return {"deleted": int(deleted or 0), "created": 0}
    person_id = int(envelope.person_legacy_id or 0)
    deleted, _ = NativeEnvelopeProfileUpdate.objects.filter(envelope=envelope, status=NativeEnvelopeProfileUpdate.Status.PENDING).delete()
    created = 0
    envelope_phone = normalize_query(envelope.informed_phone)
    if envelope_phone:
        _, current_phone_values = _current_phone_values(person_id)
        phone_digits = _digits_only(envelope_phone)
        if phone_digits and phone_digits not in current_phone_values:
            NativeEnvelopeProfileUpdate.objects.create(
                envelope=envelope,
                created_by=actor,
                **_build_profile_update_defaults(
                    envelope=envelope,
                    person_id=person_id,
                    field_name="telefone",
                    current_value=" / ".join(sorted(current_phone_values)) if current_phone_values else "",
                    envelope_value=envelope_phone,
                    actor=actor,
                ),
            )
            created += 1
    envelope_address = normalize_query(envelope.informed_address)
    if envelope_address:
        current_address = _current_address_value(person_id)
        envelope_key = _profile_compare_key(envelope_address)
        current_key = _profile_compare_key(current_address)
        if envelope_key and not (current_key and (envelope_key in current_key or current_key in envelope_key)):
            NativeEnvelopeProfileUpdate.objects.create(
                envelope=envelope,
                created_by=actor,
                **_build_profile_update_defaults(
                    envelope=envelope,
                    person_id=person_id,
                    field_name="endereco",
                    current_value=current_address,
                    envelope_value=envelope_address,
                    actor=actor,
                ),
            )
            created += 1
    return {"deleted": int(deleted or 0), "created": created}


def apply_envelope_profile_update_postgres(update_id: int, actor: str = "") -> dict[str, object]:
    update = (
        NativeEnvelopeProfileUpdate.objects.select_related("envelope")
        .filter(pk=int(update_id or 0))
        .first()
    )
    if update is None:
        raise LegacyWriteError("Pendencia cadastral do envelope nao encontrada.")
    if str(update.status or "") != NativeEnvelopeProfileUpdate.Status.PENDING:
        raise LegacyWriteError("Esta pendencia cadastral ja foi tratada.")
    if str(update.field_name or "") != "telefone":
        raise LegacyWriteError("Aplicacao direta so esta disponivel para telefone.")
    person = PersonSnapshot.objects.filter(legacy_id=int(update.person_legacy_id or 0), is_active=True).first()
    if person is None:
        raise LegacyWriteError("Pessoa vinculada a pendencia nao encontrada no Postgres.")
    phone_value = normalize_query(update.envelope_value)
    if not phone_value:
        raise LegacyWriteError("O telefone sugerido pelo envelope esta vazio.")
    with transaction.atomic():
        person.primary_phone = phone_value
        person.save(update_fields=["primary_phone", "synced_at"])
        contact = (
            PersonContactSnapshot.objects.filter(person=person, contact_type="telefone", is_primary=True)
            .order_by("legacy_id")
            .first()
        )
        normalized_value = normalize_query(phone_value)
        if contact is None:
            next_legacy_id = int(PersonContactSnapshot.objects.aggregate(value=models.Max("legacy_id")).get("value") or 0) + 1
            PersonContactSnapshot.objects.create(
                legacy_id=next_legacy_id,
                organization_id=int(person.organization_id or update.organization_id or 0),
                person=person,
                contact_type="telefone",
                value=phone_value,
                normalized_value=normalized_value,
                is_primary=True,
            )
        else:
            contact.value = phone_value
            contact.normalized_value = normalized_value
            contact.is_primary = True
            contact.save(update_fields=["value", "normalized_value", "is_primary", "synced_at"])
        update.current_value = phone_value
        update.status = NativeEnvelopeProfileUpdate.Status.APPLIED
        update.notes = "Aplicado automaticamente na ficha a partir do envelope, com auditoria preservada."
        update.updated_by = actor
        update.save(update_fields=["current_value", "status", "notes", "updated_by", "updated_at"])
    try:
        record_django_audit_event(
            actor=actor,
            action="aplicar_atualizacao_cadastral_por_envelope_postgres",
            table_name="contributions_nativeenvelopeprofileupdate",
            record_id=int(update.pk or 0),
            organization_id=int(update.organization_id or 0),
            source="postgres_native_envelope",
            summary=f"Telefone aplicado na ficha #{int(person.legacy_id or 0)} a partir do envelope #{int(update.envelope.legacy_id or 0)}",
            after={
                "person_id": int(person.legacy_id or 0),
                "field_name": update.field_name,
                "value": phone_value,
                "envelope_id": int(update.envelope.legacy_id or 0),
            },
        )
    except Exception:
        pass
    return {"update_id": int(update.pk or 0), "envelope_id": int(update.envelope.legacy_id or 0), "person_id": int(person.legacy_id or 0), "field": str(update.field_name or "")}


def ignore_envelope_profile_update_postgres(update_id: int, actor: str = "") -> dict[str, object]:
    update = (
        NativeEnvelopeProfileUpdate.objects.select_related("envelope")
        .filter(pk=int(update_id or 0))
        .first()
    )
    if update is None:
        raise LegacyWriteError("Pendencia cadastral do envelope nao encontrada.")
    if str(update.status or "") != NativeEnvelopeProfileUpdate.Status.PENDING:
        raise LegacyWriteError("Esta pendencia cadastral ja foi tratada.")
    update.status = NativeEnvelopeProfileUpdate.Status.IGNORED
    update.notes = "Ignorado manualmente pelo operador apos revisao do envelope e da ficha."
    update.updated_by = actor
    update.save(update_fields=["status", "notes", "updated_by", "updated_at"])
    return {"update_id": int(update.pk or 0), "envelope_id": int(update.envelope.legacy_id or 0), "person_id": int(update.person_legacy_id or 0)}


def backfill_envelope_profile_updates_postgres(actor: str = "") -> dict[str, int]:
    scanned = 0
    created = 0
    for envelope in NativeEnvelope.objects.filter(is_active=True, status="lancado").order_by("legacy_id"):
        scanned += 1
        created += int(refresh_envelope_profile_updates_postgres(envelope, actor=actor).get("created") or 0)
    return {"scanned": scanned, "created": created}


def _participant_options_native(
    people: list[dict[str, Any]],
    contributors: list[dict[str, Any]],
) -> list[dict[str, str]]:
    options: list[dict[str, str]] = []
    options.extend(
        {
            "value": f"Pessoa #{int(person.get('id') or 0)}",
            "label": f"Pessoa do rol · {person.get('nome') or ''}",
            "kind": "pessoa",
        }
        for person in people
        if int(person.get("id") or 0)
    )
    options.extend(
        {
            "value": f"Contribuinte #{int(contributor.get('id') or 0)}",
            "label": f"Contribuinte auxiliar · {contributor.get('nome') or ''}",
            "kind": "contribuinte",
        }
        for contributor in contributors
        if int(contributor.get("id") or 0) and contributor.get("nome")
    )
    return options


def envelope_contribution_context_postgres() -> dict[str, Any]:
    organization_id = _default_organization_id()
    catalogs = _catalogs_for_org(organization_id)
    people_options = _native_people_options(organization_id)
    contributor_options = _native_contributor_options(organization_id)
    return {
        "organization_id": organization_id,
        "today": datetime.now().date().isoformat(),
        "default_competencia_mes": datetime.now().date().strftime("%Y-%m"),
        "default_lot_name": "Envelope manual Postgres",
        "default_origin": "Envelope manual Postgres",
        "default_type_id": int((catalogs["type_options"][0]["id"] if catalogs["type_options"] else 0) or 0),
        "default_campaign_id": 0,
        "default_form_id": 0,
        "default_total": "",
        "default_nome_informado": "",
        "default_telefone_informado": "",
        "default_endereco_informado": "",
        "default_observacoes": "",
        "default_justification": "Envelope conferido manualmente no fluxo nativo Postgres.",
        "default_status": "regular",
        "people_options": people_options,
        "contributor_options": contributor_options,
        "participant_options": _participant_options_native(people_options, contributor_options),
        "type_options": catalogs["type_options"],
        "campaign_options": catalogs["campaign_options"],
        "receiving_options": catalogs["receiving_options"],
        "status_options": catalogs["status_options"],
        "traceability_status_options": [
            {"value": "pendente", "label": "Pendente"},
            {"value": "conciliado", "label": "Conciliado"},
            {"value": "revisar", "label": "Revisar"},
        ],
        "traceability": {
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
        },
        "line_defaults": [
            {"index": index, "participant_ref": "", "document": "", "type_id": 0, "campaign_id": 0, "value": "", "observations": ""}
            for index in range(1, 11)
        ],
    }


def envelope_lot_form_context_postgres() -> dict[str, Any]:
    context = envelope_contribution_context_postgres()
    return {
        "default_competencia_mes": context["default_competencia_mes"],
        "default_data_recebimento": context["today"],
        "default_origin": context["default_origin"],
        "default_type_id": context["default_type_id"],
        "default_campaign_id": context["default_campaign_id"],
        "default_form_id": context["default_form_id"],
        "type_options": context["type_options"],
        "campaign_options": context["campaign_options"],
        "receiving_options": context["receiving_options"],
    }


def _native_envelope_line_payloads(payload: Any, organization_id: int, expected_total: float, main_identity: dict[str, object]) -> list[dict[str, object]]:
    default_type_id = int(getattr(payload, "get", lambda *_args, **_kwargs: 0)("tipo_contribuicao_id_padrao") or 0)
    default_campaign_id = int(getattr(payload, "get", lambda *_args, **_kwargs: 0)("campanha_id_padrao") or 0)
    line_count = int(getattr(payload, "get", lambda *_args, **_kwargs: 10)("line_count") or 10)
    rows: list[dict[str, object]] = []
    for index in range(1, line_count + 1):
        get = getattr(payload, "get", lambda *_args, **_kwargs: "")
        value_text = str(get(f"linha_valor_{index}", "") or "").strip()
        participant_ref = str(get(f"linha_participante_ref_{index}", "") or "").strip()
        document = str(get(f"linha_documento_{index}", "") or "").strip()
        raw_type_id = int(get(f"linha_tipo_contribuicao_id_{index}", 0) or 0)
        raw_campaign_id = int(get(f"linha_campanha_id_{index}", 0) or 0)
        type_id = raw_type_id or default_type_id
        campaign_id = raw_campaign_id or default_campaign_id
        notes = normalize_query(get(f"linha_observacoes_{index}", ""))
        has_context = any([participant_ref, document, raw_type_id, raw_campaign_id, notes])
        if not value_text and not has_context:
            continue
        if not value_text:
            raise LegacyWriteError(f"Informe o valor da linha {index}.")
        value = parse_money(value_text)
        if not type_id:
            raise LegacyWriteError(f"Escolha o tipo principal do envelope ou a destinacao da linha {index}.")
        identity = (
            _native_envelope_identity(
                organization_id=organization_id,
                participant_ref=participant_ref,
                document=document,
                source="envelope_rateio_postgres",
            )
            if has_context and (participant_ref or document)
            else dict(main_identity)
        )
        rows.append(
            {
                **identity,
                "index": index,
                "type_id": type_id,
                "campaign_id": campaign_id or None,
                "value": float(value),
                "notes": notes or ("Lancamento principal do envelope." if index == 1 else ""),
            }
        )
    if rows:
        return rows
    if not default_type_id:
        raise LegacyWriteError("Escolha o tipo principal do envelope.")
    return [
        {
            **main_identity,
            "index": 1,
            "type_id": default_type_id,
            "campaign_id": default_campaign_id or None,
            "value": float(expected_total),
            "notes": "Lancamento principal do envelope.",
        }
    ]


def _materialize_native_envelope(
    envelope: NativeEnvelope,
    payload: Any,
    *,
    actor: str = "",
    source: str = "postgres_native_envelope",
) -> dict[str, object]:
    get = getattr(payload, "get", lambda *_args, **_kwargs: "")
    organization_id = int(envelope.organization_id or 0) or _default_organization_id()
    received_on = normalize_query(get("data_recebimento", ""))
    if not received_on:
        raise LegacyWriteError("Informe a data do envelope.")
    competence, competence_order = competencia_from_date(received_on)
    expected_total = round(float(parse_money(get("valor_total", ""))), 2)
    if expected_total <= 0:
        raise LegacyWriteError("Informe o total do envelope.")
    status = normalize_query(get("status_operacional", "regular")) or "regular"
    if status not in CONTRIBUTION_STATUS_OPTIONS:
        raise LegacyWriteError("Status operacional invalido para envelope.")
    justification = normalize_query(get("justificativa", ""))
    if len(justification) < 8:
        raise LegacyWriteError("Informe uma justificativa com pelo menos 8 caracteres.")
    catalogs = _catalogs_for_org(
        organization_id,
        selected_type_id=int(get("tipo_contribuicao_id_padrao", 0) or 0),
        selected_form_id=int(get("forma_recebimento_id", 0) or 0),
        selected_campaign_id=int(get("campanha_id_padrao", 0) or 0),
    )
    main_identity = _native_envelope_identity(
        organization_id=organization_id,
        participant_ref=get("participante_principal_ref", ""),
        contributor_name=get("nome_informado", ""),
        source="envelope_manual_postgres",
    )
    lines = _native_envelope_line_payloads(payload, organization_id, expected_total, main_identity)
    total = round(sum(float(line["value"]) for line in lines), 2)
    if abs(total - expected_total) > 0.009:
        raise LegacyWriteError(f"A soma das linhas ({total:.2f}) nao fecha com o total do envelope ({expected_total:.2f}).")

    created_contribution_ids: list[int] = []
    envelope.lot_name = normalize_query(get("nome_lote", "")) or envelope.lot_name or "Envelope manual Postgres"
    envelope.competence = competence
    envelope.competence_order = competence_order
    envelope.received_at = datetime.strptime(received_on, "%Y-%m-%d").date()
    envelope.received_at_raw = received_on
    envelope.total_informed = Decimal(str(expected_total))
    envelope.total_lines = Decimal(str(total))
    envelope.informed_name = normalize_query(get("nome_informado", "")) or str(main_identity.get("stored_name") or "")
    envelope.informed_phone = normalize_query(get("telefone_informado", ""))
    envelope.informed_address = normalize_query(get("endereco_informado", ""))
    envelope.person_legacy_id = int(main_identity.get("person_legacy_id") or 0) or None
    envelope.contributor_legacy_id = int(main_identity.get("contributor_legacy_id") or 0) or None
    envelope.native_aux_contributor_id = int(main_identity.get("native_aux_contributor_id") or 0) or None
    envelope.receipt_method_legacy_id = int(get("forma_recebimento_id", 0) or 0) or None
    envelope.receipt_method_name = _selected_option_name(catalogs["receiving_options"], int(get("forma_recebimento_id", 0) or 0))
    envelope.operational_status = status
    envelope.source = normalize_query(get("origem_operacional", "")) or envelope.source or "Envelope manual Postgres"
    envelope.status = "lancado"
    envelope.notes = normalize_query(get("observacoes", ""))
    envelope.justification = justification
    envelope.traceability_form = _clean_optional_text(get("rastreio_forma_identificada", ""))
    envelope.traceability_provider = _clean_optional_text(get("rastreio_banco_operadora", ""))
    envelope.traceability_check_number = _clean_optional_text(get("rastreio_numero_cheque", ""))
    envelope.traceability_operation_number = _clean_optional_text(get("rastreio_numero_operacao", ""))
    envelope.traceability_nsu_tid = _clean_optional_text(get("rastreio_nsu_tid", ""))
    envelope.traceability_card_suffix = _clean_optional_text(get("rastreio_ultimos_digitos_cartao", ""))
    envelope.traceability_operation_date = datetime.strptime(get("rastreio_data_operacao", ""), "%Y-%m-%d").date() if normalize_query(get("rastreio_data_operacao", "")) else None
    envelope.traceability_operation_date_raw = normalize_query(get("rastreio_data_operacao", ""))
    envelope.traceability_operation_amount = Decimal(str(parse_money(get("rastreio_valor_operacao", "")))) if normalize_query(get("rastreio_valor_operacao", "")) else None
    envelope.traceability_status = _clean_optional_text(get("rastreio_status_conciliacao", "")) or "pendente"
    envelope.traceability_notes = _clean_optional_text(get("rastreio_observacoes", ""))
    envelope.updated_by = actor
    update_fields = [
        "lot_name",
        "competence",
        "competence_order",
        "received_at",
        "received_at_raw",
        "total_informed",
        "total_lines",
        "informed_name",
        "informed_phone",
        "informed_address",
        "person_legacy_id",
        "contributor_legacy_id",
        "native_aux_contributor_id",
        "receipt_method_legacy_id",
        "receipt_method_name",
        "operational_status",
        "source",
        "status",
        "notes",
        "justification",
        "traceability_form",
        "traceability_provider",
        "traceability_check_number",
        "traceability_operation_number",
        "traceability_nsu_tid",
        "traceability_card_suffix",
        "traceability_operation_date",
        "traceability_operation_date_raw",
        "traceability_operation_amount",
        "traceability_status",
        "traceability_notes",
        "updated_by",
        "updated_at",
    ]
    if actor and not envelope.created_by:
        envelope.created_by = actor
        update_fields.append("created_by")
    with transaction.atomic():
        envelope.save(update_fields=update_fields)
        previous_items = list(envelope.items.filter(is_active=True))
        previous_contribution_ids = [int(item.contribution_legacy_id or 0) for item in previous_items if int(item.contribution_legacy_id or 0)]
        envelope.items.filter(is_active=True).update(is_active=False)
        if previous_contribution_ids:
            previous_contributions = list(
                NativeContribution.objects.filter(legacy_id__in=previous_contribution_ids, is_active=True)
            )
            NativeContribution.objects.filter(legacy_id__in=previous_contribution_ids, is_active=True).update(
                is_active=False,
                updated_by=actor,
            )
            for contribution in previous_contributions:
                contribution.is_active = False
                _sync_person_contribution_snapshot(contribution)
        next_item_id = _next_native_envelope_item_legacy_id()
        next_contribution_id = _next_native_contribution_public_id()
        for index, line in enumerate(lines):
            type_name = _selected_option_name(catalogs["type_options"], int(line["type_id"] or 0))
            campaign_name = _selected_option_name(catalogs["campaign_options"], int(line["campaign_id"] or 0))
            contribution = NativeContribution.objects.create(
                legacy_id=next_contribution_id + index,
                organization_id=organization_id,
                person_legacy_id=int(line.get("person_legacy_id") or 0) or None,
                contributor_legacy_id=int(line.get("contributor_legacy_id") or 0) or None,
                native_aux_contributor_id=int(line.get("native_aux_contributor_id") or 0) or None,
                contributor_source=str(line.get("contributor_source") or ""),
                contributor_name=str(line.get("contributor_name") or ""),
                contributor_document=str(line.get("contributor_document") or ""),
                contributor_type=str(line.get("contributor_type") or ""),
                received_at=envelope.received_at,
                received_at_raw=envelope.received_at_raw,
                competence=competence,
                competence_order=competence_order,
                amount=Decimal(str(line["value"])),
                contribution_type_legacy_id=int(line["type_id"] or 0),
                contribution_type_name=type_name,
                campaign_legacy_id=int(line["campaign_id"] or 0) or None,
                campaign_name=campaign_name,
                receipt_method_legacy_id=int(envelope.receipt_method_legacy_id or 0) or None,
                receipt_method_name=envelope.receipt_method_name or "",
                operational_status=status,
                notes=str(line.get("notes") or ""),
                source=source,
                is_active=True,
                created_by=actor,
                updated_by=actor,
            )
            _sync_person_contribution_snapshot(contribution)
            created_contribution_ids.append(int(contribution.legacy_id or 0))
            NativeEnvelopeItem.objects.create(
                legacy_id=next_item_id + index,
                envelope=envelope,
                person_legacy_id=int(line.get("person_legacy_id") or 0) or None,
                contributor_legacy_id=int(line.get("contributor_legacy_id") or 0) or None,
                native_aux_contributor_id=int(line.get("native_aux_contributor_id") or 0) or None,
                contributor_name=str(line.get("contributor_name") or ""),
                contributor_document=str(line.get("contributor_document") or ""),
                contribution_legacy_id=int(contribution.legacy_id or 0),
                contribution_type_legacy_id=int(line["type_id"] or 0),
                contribution_type_name=type_name,
                campaign_legacy_id=int(line["campaign_id"] or 0) or None,
                campaign_name=campaign_name,
                amount=Decimal(str(line["value"])),
                notes=str(line.get("notes") or ""),
                is_active=True,
            )
    if int(envelope.native_lot_legacy_id or 0):
        _refresh_envelope_lot_status_postgres(int(envelope.native_lot_legacy_id or 0), actor=actor)
    refresh_envelope_profile_updates_postgres(envelope, actor=actor)
    try:
        record_django_audit_event(
            actor=actor,
            action="registrar_envelope_postgres",
            table_name="contributions_nativeenvelope",
            record_id=int(envelope.pk or 0),
            organization_id=organization_id,
            source=source,
            summary=f"Envelope nativo #{envelope.legacy_id} registrado no Postgres.",
            after={"envelope_id": int(envelope.legacy_id or 0), "contributions": created_contribution_ids, "total": total},
        )
    except Exception:
        pass
    return {"envelope_id": int(envelope.legacy_id or 0), "lot_id": int(envelope.native_lot_legacy_id or 0), "contribution_ids": created_contribution_ids}


def create_envelope_contribution_batch_postgres(payload: Any, upload: Any, actor: str = "") -> dict[str, object]:
    envelope_legacy_id = _next_native_envelope_legacy_id()
    received_on = normalize_query(getattr(payload, "get", lambda *_args, **_kwargs: "")("data_recebimento", ""))
    competence = competencia_from_date(received_on)[0] if received_on else "sem_competencia"
    file_payload = _store_native_envelope_file(envelope_id=envelope_legacy_id, competence=competence, upload=upload)
    envelope = NativeEnvelope.objects.create(
        legacy_id=envelope_legacy_id,
        organization_id=_default_organization_id(),
        status=ENVELOPE_PENDING_STATUS,
        image_original_name=str(file_payload.get("filename") or ""),
        image_hash=str(file_payload.get("hash") or ""),
        image_content_type=str(file_payload.get("content_type") or ""),
        image_size=int(file_payload.get("size") or 0),
        image_path=str(file_payload.get("path") or ""),
        is_active=True,
        created_by=actor,
        updated_by=actor,
    )
    return _materialize_native_envelope(envelope, payload, actor=actor, source="postgres_native_envelope")


def create_envelope_image_lot_postgres(payload: Any, uploads: list[Any] | tuple[Any, ...] | None = None, actor: str = "") -> dict[str, object]:
    get = getattr(payload, "get", lambda *_args, **_kwargs: "")
    organization_id = _default_organization_id()
    competence_mes = normalize_query(get("competencia_mes", ""))
    if not competence_mes:
        raise LegacyWriteError("Informe o mes de competencia do lote.")
    try:
        competence_date = datetime.strptime(f"{competence_mes}-01", "%Y-%m-%d").date()
    except ValueError as exc:
        raise LegacyWriteError("Mes de competencia invalido para o lote.") from exc
    competence = competence_date.strftime("%b/%Y").lower().replace("may", "mai")
    competence_order = int(competence_date.strftime("%Y%m"))
    lot_name = normalize_query(get("nome_lote", ""))
    if not lot_name:
        raise LegacyWriteError("Informe o nome do lote.")
    uploads = list(uploads or [])
    if not uploads:
        raise LegacyWriteError("Selecione ao menos uma imagem/PDF para o lote.")
    lot_legacy_id = _next_native_envelope_lot_legacy_id()
    lot_folder = _slug_folder(lot_name)
    with transaction.atomic():
        lot = NativeEnvelopeLot.objects.create(
            legacy_id=lot_legacy_id,
            organization_id=organization_id,
            name=lot_name,
            competence=competence,
            competence_order=competence_order,
            default_received_at=datetime.strptime(normalize_query(get("data_padrao_recebimento", "")), "%Y-%m-%d").date() if normalize_query(get("data_padrao_recebimento", "")) else None,
            default_received_at_raw=normalize_query(get("data_padrao_recebimento", "")),
            default_source=normalize_query(get("origem_operacional", "")) or "Envelope digitalizado",
            default_contribution_type_legacy_id=int(get("tipo_contribuicao_id_padrao", 0) or 0) or None,
            default_campaign_legacy_id=int(get("campanha_id_padrao", 0) or 0) or None,
            default_receipt_method_legacy_id=int(get("forma_recebimento_id", 0) or 0) or None,
            folder_path=str(Path(envelope_upload_root()) / "native" / competence / lot_folder),
            notes=normalize_query(get("observacoes", "")),
            status="aberto",
            is_active=True,
            created_by=actor,
            updated_by=actor,
        )
        envelope_ids: list[int] = []
        duplicates: list[int] = []
        for order, upload in enumerate(sorted(uploads, key=lambda item: str(getattr(item, "name", "")).casefold()), start=1):
            envelope_legacy_id = _next_native_envelope_legacy_id() + (order - 1)
            file_payload = _store_native_envelope_file(
                envelope_id=envelope_legacy_id,
                competence=competence,
                upload=upload,
                lot_folder=lot_folder,
            )
            duplicate = NativeEnvelope.objects.filter(image_hash=str(file_payload.get("hash") or ""), is_active=True).exists()
            envelope = NativeEnvelope.objects.create(
                legacy_id=envelope_legacy_id,
                organization_id=organization_id,
                native_lot_legacy_id=int(lot.legacy_id or 0),
                lot_name=lot.name,
                competence=lot.competence,
                competence_order=lot.competence_order,
                received_at=lot.default_received_at,
                received_at_raw=lot.default_received_at_raw or "",
                total_informed=Decimal("0"),
                total_lines=Decimal("0"),
                receipt_method_legacy_id=int(lot.default_receipt_method_legacy_id or 0) or None,
                receipt_method_name="",
                operational_status="regular",
                source=lot.default_source or "Envelope digitalizado",
                status="duplicado" if duplicate else ENVELOPE_PENDING_STATUS,
                notes="",
                justification="",
                image_original_name=str(file_payload.get("filename") or ""),
                image_hash=str(file_payload.get("hash") or ""),
                image_content_type=str(file_payload.get("content_type") or ""),
                image_size=int(file_payload.get("size") or 0),
                image_path=str(file_payload.get("path") or ""),
                traceability_status="pendente",
                is_active=True,
                created_by=actor,
                updated_by=actor,
            )
            envelope_ids.append(int(envelope.legacy_id or 0))
            if duplicate:
                duplicates.append(int(envelope.legacy_id or 0))
    return {"lot_id": int(lot.legacy_id or 0), "envelope_ids": envelope_ids, "duplicates": duplicates}


def get_next_pending_envelope_id_postgres(lot_id: int, actor: str = "") -> int:
    lot_id = int(lot_id or 0)
    if not lot_id:
        return 0
    actor = normalize_query(actor) or "django"
    now = timezone.now()
    with transaction.atomic():
        current = (
            NativeEnvelope.objects.select_for_update()
            .filter(
                native_lot_legacy_id=lot_id,
                is_active=True,
                status=ENVELOPE_IN_PROGRESS_STATUS,
                updated_by=actor,
            )
            .order_by("legacy_id")
            .first()
        )
        if current and not _is_stale_envelope_lock(current, now=now):
            _claim_envelope_for_digitization(current, actor)
            return int(current.legacy_id or 0)
        for envelope in (
            NativeEnvelope.objects.select_for_update()
            .filter(
                native_lot_legacy_id=lot_id,
                is_active=True,
                status__in=sorted(ENVELOPE_OPEN_STATUSES),
            )
            .order_by("legacy_id")
        ):
            if not _can_claim_envelope(envelope, actor, now=now):
                continue
            _claim_envelope_for_digitization(envelope, actor)
            return int(envelope.legacy_id or 0)
    return 0


def get_envelope_lot_detail_postgres(lot_id: int) -> dict[str, Any] | None:
    lot = NativeEnvelopeLot.objects.filter(legacy_id=int(lot_id or 0), is_active=True).first()
    if lot is None:
        return None
    rows = list(
        NativeEnvelope.objects.filter(native_lot_legacy_id=int(lot.legacy_id or 0), is_active=True).order_by("legacy_id")
    )
    counts = {"total": 0, "pendentes": 0, "em_digitacao": 0, "lancados": 0, "ignorados": 0, "duplicados": 0}
    total_launched = 0.0
    next_pending_id = 0
    items: list[dict[str, Any]] = []
    for order, row in enumerate(rows, start=1):
        counts["total"] += 1
        if row.status == ENVELOPE_PENDING_STATUS:
            counts["pendentes"] += 1
            if not next_pending_id:
                next_pending_id = int(row.legacy_id or 0)
        elif row.status == ENVELOPE_IN_PROGRESS_STATUS:
            counts["em_digitacao"] += 1
            if not next_pending_id:
                next_pending_id = int(row.legacy_id or 0)
        elif row.status == "lancado":
            counts["lancados"] += 1
            total_launched += float(row.total_informed or 0)
        elif row.status == "ignorado":
            counts["ignorados"] += 1
        elif row.status == "duplicado":
            counts["duplicados"] += 1
        items.append(
            {
                "id": int(row.legacy_id or 0),
                "ordem": order,
                "arquivo": row.image_original_name or "",
                "data": br_date(row.received_at_raw or (row.received_at.isoformat() if row.received_at else "")),
                "data_raw": row.received_at_raw or "",
                "nome": row.informed_name or row.image_original_name or "Envelope sem nome",
                "forma": row.receipt_method_name or "Nao informada",
                "status": row.status or "",
                "status_label": _envelope_status_label(row.status or ""),
                "total_fmt": br_money(row.total_informed or 0),
                "observacoes": row.notes or "",
                "detail_url": f"/contributions/envelopes/{int(row.legacy_id or 0)}/",
                "launch_url": f"/contributions/envelopes/{int(row.legacy_id or 0)}/launch/" if row.status in ENVELOPE_OPEN_STATUSES else "",
                "image_url": f"/contributions/envelopes/{int(row.legacy_id or 0)}/image/" if row.image_path else "",
            }
        )
    return {
        "id": int(lot.legacy_id or 0),
        "nome": lot.name or "",
        "competencia": lot.competence or "",
        "competencia_mes": lot.default_received_at_raw[:7] if lot.default_received_at_raw else "",
        "status": lot.status or "",
        "status_label": lot.status or "",
        "data_padrao": br_date(lot.default_received_at_raw or (lot.default_received_at.isoformat() if lot.default_received_at else "")),
        "data_padrao_raw": lot.default_received_at_raw or "",
        "origem_operacional": lot.default_source or "",
        "caminho_pasta": lot.folder_path or "",
        "observacoes": lot.notes or "",
        "counts": counts,
        "total_lancado": total_launched,
        "total_lancado_fmt": br_money(total_launched),
        "next_pending_id": next_pending_id,
        "next_pending_url": f"/contributions/envelopes/lots/{int(lot.legacy_id or 0)}/next/" if next_pending_id else "",
        "items": items,
    }


def pending_envelope_contribution_context_postgres(envelope_id: int, actor: str = "") -> dict[str, Any] | None:
    actor = normalize_query(actor) or "django"
    now = timezone.now()
    with transaction.atomic():
        envelope = NativeEnvelope.objects.select_for_update().filter(legacy_id=int(envelope_id or 0), is_active=True).first()
        if envelope is None:
            return None
        status = str(envelope.status or "")
        if status not in ENVELOPE_OPEN_STATUSES:
            return None
        if not _can_claim_envelope(envelope, actor, now=now):
            owner = _envelope_lock_owner_label(envelope)
            raise LegacyWriteError(f"Envelope em digitacao por {owner}. Abra o proximo disponivel.")
        _claim_envelope_for_digitization(envelope, actor)
    context = envelope_contribution_context_postgres()
    suffix = Path(str(envelope.image_path or "")).suffix.lower()
    context.update(
        {
            "pending_envelope": {
                "id": int(envelope.legacy_id or 0),
                "lote_id": int(envelope.native_lot_legacy_id or 0),
                "status": envelope.status or "",
                "status_label": _envelope_status_label(envelope.status or ""),
                "arquivo": envelope.image_original_name or "",
                "image_url": f"/contributions/envelopes/{int(envelope.legacy_id or 0)}/image/" if envelope.image_path else "",
                "is_image": suffix in {".jpg", ".jpeg", ".png", ".webp"},
                "lot_url": f"/contributions/envelopes/lots/{int(envelope.native_lot_legacy_id or 0)}/" if int(envelope.native_lot_legacy_id or 0) else "/contributions/envelopes/",
                "ignore_url": f"/contributions/envelopes/{int(envelope.legacy_id or 0)}/ignore/",
            },
            "form_action": f"/contributions/envelopes/{int(envelope.legacy_id or 0)}/launch/",
            "today": envelope.received_at_raw or context["today"],
            "default_competencia_mes": envelope.received_at_raw[:7] if envelope.received_at_raw else context["default_competencia_mes"],
            "default_lot_name": envelope.lot_name or "",
            "default_origin": envelope.source or "",
            "default_type_id": 0,
            "default_campaign_id": 0,
            "default_form_id": int(envelope.receipt_method_legacy_id or 0) or 0,
            "selected_primary_ref": envelope.informed_name or "",
            "default_justification": "Envelope conferido manualmente; imagem anexada para auditoria.",
            "default_total": "" if round(float(envelope.total_informed or 0), 2) <= 0 else br_money(envelope.total_informed or 0).replace("R$ ", ""),
            "default_nome_informado": envelope.informed_name or "",
            "default_telefone_informado": envelope.informed_phone or "",
            "default_endereco_informado": envelope.informed_address or "",
            "default_observacoes": envelope.notes or "",
            "traceability": {
                "forma_identificada": envelope.traceability_form or "",
                "banco_operadora": envelope.traceability_provider or "",
                "numero_cheque": envelope.traceability_check_number or "",
                "numero_operacao": envelope.traceability_operation_number or "",
                "nsu_tid": envelope.traceability_nsu_tid or "",
                "ultimos_digitos_cartao": envelope.traceability_card_suffix or "",
                "data_operacao": envelope.traceability_operation_date_raw or "",
                "valor_operacao": br_money(envelope.traceability_operation_amount or 0).replace("R$ ", "") if envelope.traceability_operation_amount is not None else "",
                "status_conciliacao": envelope.traceability_status or "pendente",
                "observacoes": envelope.traceability_notes or "",
            },
        }
    )
    return context


def launch_pending_envelope_postgres(envelope_id: int, payload: Any, actor: str = "") -> dict[str, object]:
    actor = normalize_query(actor) or "django"
    with transaction.atomic():
        envelope = NativeEnvelope.objects.select_for_update().filter(legacy_id=int(envelope_id or 0), is_active=True).first()
        if envelope is None:
            raise LegacyWriteError("Envelope pendente nao encontrado.")
        status = str(envelope.status or "")
        if status == "lancado":
            raise LegacyWriteError("Este envelope ja foi lancado.")
        if status in {"ignorado", "duplicado"}:
            raise LegacyWriteError("Este envelope nao esta disponivel para lancamento.")
        if status not in ENVELOPE_OPEN_STATUSES:
            raise LegacyWriteError("Este envelope nao esta disponivel para lancamento.")
        if not _can_claim_envelope(envelope, actor):
            owner = _envelope_lock_owner_label(envelope)
            raise LegacyWriteError(f"Envelope em digitacao por {owner}. Abra o proximo disponivel.")
        result = _materialize_native_envelope(envelope, payload, actor=actor, source="postgres_pending_envelope")
    result["reconciliation"] = {"matched": False, "reason": "postgres_pending_manual"}
    return result


def ignore_pending_envelope_postgres(envelope_id: int, justification: str = "", actor: str = "") -> dict[str, object]:
    actor = normalize_query(actor) or "django"
    with transaction.atomic():
        envelope = NativeEnvelope.objects.select_for_update().filter(legacy_id=int(envelope_id or 0), is_active=True).first()
        if envelope is None:
            raise LegacyWriteError("Envelope pendente nao encontrado.")
        status = str(envelope.status or "")
        if status == "lancado":
            raise LegacyWriteError("Este envelope ja foi lancado.")
        if status == "duplicado":
            raise LegacyWriteError("Este envelope nao esta disponivel para lancamento.")
        if status not in ENVELOPE_OPEN_STATUSES:
            raise LegacyWriteError("Este envelope nao esta disponivel para lancamento.")
        if not _can_claim_envelope(envelope, actor):
            owner = _envelope_lock_owner_label(envelope)
            raise LegacyWriteError(f"Envelope em digitacao por {owner}. Abra o proximo disponivel.")
        envelope.status = "ignorado"
        if normalize_query(justification):
            envelope.justification = normalize_query(justification)
        envelope.updated_by = actor
        envelope.save(update_fields=["status", "justification", "updated_by", "updated_at"])
    if int(envelope.native_lot_legacy_id or 0):
        _refresh_envelope_lot_status_postgres(int(envelope.native_lot_legacy_id or 0), actor=actor)
    return {"envelope_id": int(envelope.legacy_id or 0), "lot_id": int(envelope.native_lot_legacy_id or 0)}


def launched_envelope_edit_context_postgres(envelope_id: int) -> dict[str, Any] | None:
    envelope = NativeEnvelope.objects.filter(legacy_id=int(envelope_id or 0), is_active=True).prefetch_related("items").first()
    if envelope is None or str(envelope.status or "") != "lancado":
        return None
    context = envelope_contribution_context_postgres()
    item_rows = list(envelope.items.filter(is_active=True).order_by("legacy_id"))
    context.update(
        {
            "is_editing": True,
            "form_action": f"/contributions/envelopes/{int(envelope.legacy_id or 0)}/edit/",
            "today": envelope.received_at_raw or context["today"],
            "default_competencia_mes": envelope.received_at_raw[:7] if envelope.received_at_raw else context["default_competencia_mes"],
            "default_lot_name": envelope.lot_name or "",
            "default_origin": envelope.source or "",
            "default_type_id": int(item_rows[0].contribution_type_legacy_id or 0) if item_rows else 0,
            "default_campaign_id": int(item_rows[0].campaign_legacy_id or 0) if item_rows else 0,
            "default_form_id": int(envelope.receipt_method_legacy_id or 0) or 0,
            "selected_primary_ref": envelope.informed_name or "",
            "default_justification": envelope.justification or "Correcao auditada do envelope no fluxo nativo.",
            "default_total": br_money(envelope.total_informed or 0).replace("R$ ", "") if round(float(envelope.total_informed or 0), 2) > 0 else "",
            "default_nome_informado": envelope.informed_name or "",
            "default_telefone_informado": envelope.informed_phone or "",
            "default_endereco_informado": envelope.informed_address or "",
            "default_observacoes": envelope.notes or "",
            "default_status": envelope.operational_status or "regular",
            "traceability": {
                "forma_identificada": envelope.traceability_form or "",
                "banco_operadora": envelope.traceability_provider or "",
                "numero_cheque": envelope.traceability_check_number or "",
                "numero_operacao": envelope.traceability_operation_number or "",
                "nsu_tid": envelope.traceability_nsu_tid or "",
                "ultimos_digitos_cartao": envelope.traceability_card_suffix or "",
                "data_operacao": envelope.traceability_operation_date_raw or "",
                "valor_operacao": br_money(envelope.traceability_operation_amount or 0).replace("R$ ", "") if envelope.traceability_operation_amount is not None else "",
                "status_conciliacao": envelope.traceability_status or "pendente",
                "observacoes": envelope.traceability_notes or "",
            },
            "line_defaults": [
                {
                    "index": index + 1,
                    "participant_ref": item.contributor_name or "",
                    "document": item.contributor_document or "",
                    "type_id": int(item.contribution_type_legacy_id or 0),
                    "campaign_id": int(item.campaign_legacy_id or 0),
                    "value": br_money(item.amount or 0).replace("R$ ", ""),
                    "observations": item.notes or "",
                }
                for index, item in enumerate(item_rows[:10])
            ]
            + [
                {"index": index, "participant_ref": "", "document": "", "type_id": 0, "campaign_id": 0, "value": "", "observations": ""}
                for index in range(len(item_rows[:10]) + 1, 11)
            ],
        }
    )
    return context


def update_launched_envelope_postgres(envelope_id: int, payload: Any, actor: str = "") -> dict[str, object]:
    envelope = NativeEnvelope.objects.filter(legacy_id=int(envelope_id or 0), is_active=True).first()
    if envelope is None:
        raise LegacyWriteError("Envelope nao encontrado.")
    if str(envelope.status or "") != "lancado":
        raise LegacyWriteError("Somente envelopes ja lancados podem ser corrigidos por esta tela.")
    result = _materialize_native_envelope(envelope, payload, actor=actor, source="postgres_edit_envelope")
    result["reconciliation"] = {"matched": False, "reason": "postgres_edit_manual"}
    return result


def list_envelopes_postgres(q: str = "", competencia: str = "") -> dict[str, Any]:
    q = normalize_query(q)
    competencia = normalize_query(competencia)
    queryset = NativeEnvelope.objects.filter(is_active=True).order_by("-competence_order", "-received_at", "-legacy_id")
    if competencia:
        queryset = queryset.filter(competence=competencia)
    rows = list(queryset)
    if q:
        needle = q.lower()
        rows = [
            row
            for row in rows
            if needle in " ".join(
                [
                    normalize_query(row.informed_name),
                    normalize_query(row.lot_name),
                    normalize_query(row.image_original_name),
                    normalize_query(row.image_hash),
                    normalize_query(row.informed_phone),
                    normalize_query(row.informed_address),
                ]
            ).lower()
        ]
    people = {
        int(person.legacy_id or 0): person
        for person in PersonSnapshot.objects.filter(
            legacy_id__in=[int(row.person_legacy_id or 0) for row in rows if int(row.person_legacy_id or 0)]
        )
    }
    competencias = sorted({str(row.competence or "") for row in NativeEnvelope.objects.filter(is_active=True) if row.competence}, reverse=True)
    total_value = sum(float(row.total_informed or 0) for row in rows)
    lots: list[dict[str, Any]] = []
    visible_lot_ids = {int(row.native_lot_legacy_id or 0) for row in rows if int(row.native_lot_legacy_id or 0)}
    for lot in NativeEnvelopeLot.objects.filter(is_active=True).order_by("-competence_order", "-legacy_id")[:30]:
        lot_id = int(lot.legacy_id or 0)
        if visible_lot_ids and lot_id not in visible_lot_ids and (q or competencia):
            continue
        lot_rows = [row for row in rows if int(row.native_lot_legacy_id or 0) == lot_id]
        next_pending_id = next((int(row.legacy_id or 0) for row in lot_rows if str(row.status or "") in ENVELOPE_OPEN_STATUSES), 0)
        lots.append(
            {
                "id": lot_id,
                "nome": lot.name or "",
                "competencia": lot.competence or "",
                "status": lot.status or "",
                "status_label": lot.status or "",
                "total_envelopes": len(lot_rows),
                "pendentes": sum(1 for row in lot_rows if str(row.status or "") == ENVELOPE_PENDING_STATUS),
                "em_digitacao": sum(1 for row in lot_rows if str(row.status or "") == ENVELOPE_IN_PROGRESS_STATUS),
                "lancados": sum(1 for row in lot_rows if str(row.status or "") == "lancado"),
                "ignorados": sum(1 for row in lot_rows if str(row.status or "") == "ignorado"),
                "duplicados": sum(1 for row in lot_rows if str(row.status or "") == "duplicado"),
                "total_valor_fmt": br_money(sum(float(row.total_informed or 0) for row in lot_rows if str(row.status or "") == "lancado")),
                "caminho_pasta": lot.folder_path or "",
                "detail_url": f"/contributions/envelopes/lots/{lot_id}/",
                "next_pending_url": f"/contributions/envelopes/lots/{lot_id}/next/" if next_pending_id else "",
            }
        )
    return {
        "lots": lots,
        "items": [
            {
                "id": int(row.legacy_id or 0),
                "lote_id": int(row.native_lot_legacy_id or 0),
                "data": br_date(row.received_at_raw or (row.received_at.isoformat() if row.received_at else "")),
                "competencia": row.competence or "",
                "lote_nome": row.lot_name or "Envelope manual Postgres",
                "nome": (people.get(int(row.person_legacy_id or 0)).name if int(row.person_legacy_id or 0) and people.get(int(row.person_legacy_id or 0)) else row.informed_name or "Sem identificacao"),
                "nome_informado": row.informed_name or "",
                "sigla": "PG" if int(row.person_legacy_id or 0) else "AUX",
                "forma": row.receipt_method_name or "Nao informada",
                "status_label": _envelope_status_label(row.status or "lancado"),
                "image_url": f"/contributions/envelopes/{int(row.legacy_id or 0)}/image/" if row.image_path else "",
                "detail_url": f"/contributions/envelopes/{int(row.legacy_id or 0)}/",
                "edit_url": f"/contributions/envelopes/{int(row.legacy_id or 0)}/edit/" if str(row.status or "") == "lancado" else "",
                "launch_url": f"/contributions/envelopes/{int(row.legacy_id or 0)}/launch/" if str(row.status or "") in ENVELOPE_OPEN_STATUSES else "",
                "total_fmt": br_money(row.total_informed or 0),
            }
            for row in rows
        ],
        "total": len(rows),
        "shown": len(rows),
        "total_value": total_value,
        "total_value_fmt": br_money(total_value),
        "competencias": competencias,
    }


def get_envelope_detail_postgres(envelope_id: int) -> dict[str, Any] | None:
    envelope = (
        NativeEnvelope.objects.filter(legacy_id=int(envelope_id or 0), is_active=True)
        .prefetch_related("items", "profile_updates")
        .first()
    )
    if envelope is None:
        return None
    person = PersonSnapshot.objects.filter(legacy_id=int(envelope.person_legacy_id or 0)).first() if int(envelope.person_legacy_id or 0) else None
    profile_updates = []
    for update in envelope.profile_updates.order_by("status", "field_name", "id"):
        update_person = (
            person
            if person is not None and int(person.legacy_id or 0) == int(update.person_legacy_id or 0)
            else PersonSnapshot.objects.filter(legacy_id=int(update.person_legacy_id or 0), is_active=True).first()
        )
        profile_updates.append(
            {
                "id": int(update.pk or 0),
                "pessoa_nome": update_person.name if update_person else f"Pessoa #{int(update.person_legacy_id or 0)}",
                "pessoa_url": f"/people/{int(update.person_legacy_id or 0)}/" if int(update.person_legacy_id or 0) else "",
                "pessoa_edit_url": f"/people/{int(update.person_legacy_id or 0)}/edit/" if int(update.person_legacy_id or 0) else "",
                "codigo": update_person.internal_code if update_person else "",
                "cpf": update_person.cpf if update_person else "",
                "campo": str(update.field_name or "").replace("_", " ").capitalize(),
                "valor_cadastro": update.current_value or "",
                "valor_envelope": update.envelope_value or "",
                "status": update.get_status_display() or update.status,
                "can_apply": str(update.status or "") == NativeEnvelopeProfileUpdate.Status.PENDING and str(update.field_name or "") == "telefone",
                "can_ignore": str(update.status or "") == NativeEnvelopeProfileUpdate.Status.PENDING,
                "apply_url": f"/contributions/envelopes/profile-updates/{int(update.pk or 0)}/apply/",
                "ignore_url": f"/contributions/envelopes/profile-updates/{int(update.pk or 0)}/ignore/",
            }
        )
    return {
        "id": int(envelope.legacy_id or 0),
        "lote_id": int(envelope.native_lot_legacy_id or 0),
        "lote_nome": envelope.lot_name or "Envelope manual Postgres",
        "lote_url": f"/contributions/envelopes/lots/{int(envelope.native_lot_legacy_id or 0)}/" if int(envelope.native_lot_legacy_id or 0) else "",
        "edit_url": f"/contributions/envelopes/{int(envelope.legacy_id or 0)}/edit/" if str(envelope.status or "") == "lancado" else "",
        "competencia": envelope.competence or "",
        "data": br_date(envelope.received_at_raw or (envelope.received_at.isoformat() if envelope.received_at else "")),
        "total_fmt": br_money(envelope.total_informed or 0),
        "total_linhas_fmt": br_money(envelope.total_lines or 0),
        "nome_informado": envelope.informed_name or "",
        "telefone_informado": envelope.informed_phone or "",
        "endereco_informado": envelope.informed_address or "",
        "pessoa_nome": person.name if person else "",
        "pessoa_url": f"/people/{int(person.legacy_id or 0)}/" if person else "",
        "pessoa_cpf": person.cpf if person else "",
        "contribuinte_nome": "",
        "contribuinte_url": "",
        "documento_principal": "",
        "forma": envelope.receipt_method_name or "Nao informada",
        "origem_operacional": envelope.source or "",
        "traceability": {
            "forma_identificada": envelope.traceability_form or "",
            "banco_operadora": envelope.traceability_provider or "",
            "numero_cheque": envelope.traceability_check_number or "",
            "numero_operacao": envelope.traceability_operation_number or "",
            "nsu_tid": envelope.traceability_nsu_tid or "",
            "ultimos_digitos_cartao": envelope.traceability_card_suffix or "",
            "data_operacao": br_date(envelope.traceability_operation_date_raw or (envelope.traceability_operation_date.isoformat() if envelope.traceability_operation_date else "")),
            "valor_operacao_fmt": br_money(envelope.traceability_operation_amount or 0) if envelope.traceability_operation_amount is not None else "",
            "status_conciliacao": envelope.traceability_status or "pendente",
            "observacoes": envelope.traceability_notes or "",
        },
        "status": envelope.status or "lancado",
        "observacoes": envelope.notes or "",
        "justificativa": envelope.justification or "",
        "nome_arquivo_original": envelope.image_original_name or "",
        "imagem_hash": envelope.image_hash or "",
        "caminho_pasta": str(Path(envelope.image_path).parent) if envelope.image_path else "",
        "has_image": bool(envelope.image_path),
        "image_path": envelope.image_path or "",
        "image_content_type": envelope.image_content_type or "",
        "image_url": f"/contributions/envelopes/{int(envelope.legacy_id or 0)}/image/" if envelope.image_path else "",
        "is_image": Path(envelope.image_path or "").suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"},
        "items": [
            {
                "id": int(item.legacy_id or 0),
                "contribution_id": int(item.contribution_legacy_id or 0),
                "contribution_url": f"/contributions/{int(item.contribution_legacy_id or 0)}/" if int(item.contribution_legacy_id or 0) else "",
                "nome": item.contributor_name or "Sem identificacao",
                "sigla": "PG" if int(item.person_legacy_id or 0) else "AUX",
                "tipo": item.contribution_type_name or "",
                "campanha": item.campaign_name or "",
                "valor_fmt": br_money(item.amount or 0),
                "observacoes": item.notes or "",
            }
            for item in envelope.items.filter(is_active=True).order_by("legacy_id")
        ],
        "profile_updates": profile_updates,
    }


def combine_envelope_dashboards(*dashboards: dict[str, Any]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    lots: list[dict[str, Any]] = []
    competencias: set[str] = set()
    total = 0
    total_value = 0.0
    for dashboard in dashboards:
        if not dashboard:
            continue
        items.extend(list(dashboard.get("items") or []))
        lots.extend(list(dashboard.get("lots") or []))
        total += int(dashboard.get("total") or 0)
        total_value += float(dashboard.get("total_value") or 0)
        for value in dashboard.get("competencias") or []:
            if value:
                competencias.add(str(value))
    items.sort(key=lambda item: (str(item.get("data") or ""), int(item.get("id") or 0)), reverse=True)
    return {
        "lots": lots,
        "items": items,
        "total": total,
        "shown": len(items),
        "total_value": total_value,
        "total_value_fmt": br_money(total_value),
        "competencias": sorted(competencias, reverse=True),
    }

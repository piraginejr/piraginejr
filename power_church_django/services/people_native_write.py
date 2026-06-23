from __future__ import annotations

import hashlib
from datetime import datetime
from dataclasses import dataclass

from django.db import models, transaction

from power_church_core.family import family_address_key
from power_church_core.normalization import moneyless_int, normalize_match_name, normalize_query
from power_church_django.apps.contributions.models import ReceiptDispatch, ReceiptSnapshot
from power_church_django.apps.imports.models import StatementImportPilotMovement
from power_church_django.apps.people.models import (
    PersonAddressSnapshot,
    PersonContactSnapshot,
    PersonProfileSnapshot,
    PersonHistorySnapshot,
    PersonIdentifierSnapshot,
    PersonContributorSnapshot,
    PersonSecurePurgeSnapshot,
    PersonSecureTrashSnapshot,
    PersonRelationshipSnapshot,
    PersonSnapshot,
    PersonContributionSnapshot,
    HouseholdProfile,
)
from power_church_django.services.django_audit import record_django_audit_event
from power_church_django.services.people_form_support import (
    ALLOWED_FAMILY_RELATIONSHIP_TYPES,
    ALLOWED_PERSON_STATUSES,
    clean_member_code,
    empty_person_form,
    manual_cpf_or_error,
    manual_email_or_error,
    person_form_payload,
    status_grants_member_code,
)
from power_church_django.services.photos import list_member_photo_variants
from power_church_django.services.runtime_errors import LegacyWriteError


@dataclass
class NativePersonForm:
    codigo_interno: str
    nome: str
    nome_social: str
    cpf: str
    rg: str
    data_nascimento: str
    sexo: str
    estado_civil: str
    email_principal: str
    telefone_principal: str
    whatsapp_principal: str
    status: str
    observacoes: str
    cep: str
    logradouro: str
    numero: str
    complemento: str
    bairro: str
    cidade: str
    uf: str
    allow_member_code_edit: str


def _default_organization_id() -> int:
    row = PersonSnapshot.objects.order_by("organization_id", "legacy_id").only("organization_id").first()
    return int(row.organization_id or 1) if row is not None else 1


def _next_legacy_id(model) -> int:
    last = model.objects.order_by("-legacy_id").only("legacy_id").first()
    return int((last.legacy_id if last else 0) or 0) + 1


def _normalized_person_payload(payload: dict[str, str]) -> NativePersonForm:
    data = person_form_payload(payload)
    status = normalize_query(data.get("status") or "frequentador")
    if status not in ALLOWED_PERSON_STATUSES:
        raise LegacyWriteError("Status invalido para a ficha.")
    name = normalize_query(data.get("nome"))
    if not name:
        raise LegacyWriteError("Nome e obrigatorio.")
    cpf_db = manual_cpf_or_error(data.get("cpf"), LegacyWriteError)
    email_db = manual_email_or_error(data.get("email_principal"), LegacyWriteError)
    return NativePersonForm(
        codigo_interno=clean_member_code(data.get("codigo_interno")),
        nome=name,
        nome_social=normalize_query(data.get("nome_social")),
        cpf=cpf_db or "",
        rg=normalize_query(data.get("rg")),
        data_nascimento=normalize_query(data.get("data_nascimento")),
        sexo=normalize_query(data.get("sexo")),
        estado_civil=normalize_query(data.get("estado_civil")),
        email_principal=email_db,
        telefone_principal=normalize_query(data.get("telefone_principal")),
        whatsapp_principal=normalize_query(data.get("whatsapp_principal")),
        status=status,
        observacoes=normalize_query(data.get("observacoes")),
        cep=normalize_query(data.get("cep")),
        logradouro=normalize_query(data.get("logradouro")),
        numero=normalize_query(data.get("numero")),
        complemento=normalize_query(data.get("complemento")),
        bairro=normalize_query(data.get("bairro")),
        cidade=normalize_query(data.get("cidade")),
        uf=normalize_query(data.get("uf")).upper(),
        allow_member_code_edit="1" if data.get("allow_member_code_edit") == "1" else "",
    )


def _assert_unique_snapshot_cpf(cpf: str, *, ignore_person_id: int = 0) -> None:
    if not cpf:
        return
    queryset = PersonSnapshot.objects.filter(cpf=cpf, is_active=True)
    if int(ignore_person_id or 0):
        queryset = queryset.exclude(legacy_id=int(ignore_person_id))
    if queryset.exists():
        raise LegacyWriteError("Ja existe outra ficha ativa com este CPF.")


def _assert_unique_snapshot_email(email: str, *, ignore_person_id: int = 0) -> None:
    if not email:
        return
    queryset = PersonSnapshot.objects.filter(normalized_email=email, is_active=True)
    if int(ignore_person_id or 0):
        queryset = queryset.exclude(legacy_id=int(ignore_person_id))
    if queryset.exists():
        raise LegacyWriteError("Ja existe outra ficha ativa com este e-mail principal.")


def _next_member_code() -> str:
    existing = []
    for row in PersonSnapshot.objects.exclude(internal_code="").only("internal_code"):
        digits = "".join(ch for ch in str(row.internal_code or "") if ch.isdigit())
        if digits:
            existing.append(int(digits))
    return str(max(existing or [100000]) + 1)


def _resolved_member_code(form: NativePersonForm, *, current_code: str = "") -> str:
    if current_code and not form.allow_member_code_edit:
        return current_code
    requested = clean_member_code(form.codigo_interno or current_code)
    if requested:
        queryset = PersonSnapshot.objects.filter(internal_code=requested, is_active=True)
        if current_code:
            queryset = queryset.exclude(internal_code=current_code)
        if queryset.exists():
            raise LegacyWriteError("Codigo interno ja utilizado por outra ficha ativa.")
        return requested
    if status_grants_member_code(form.status):
        return _next_member_code()
    return ""


def _upsert_primary_contact(person: PersonSnapshot, *, contact_type: str, value: str, organization_id: int) -> None:
    existing = (
        PersonContactSnapshot.objects.filter(person=person, contact_type=contact_type, is_primary=True)
        .order_by("legacy_id")
        .first()
    )
    normalized_value = normalize_query(value).lower() if contact_type == "email" else normalize_query(value)
    if not value:
        if existing is not None:
            existing.delete()
        return
    if existing is None:
        PersonContactSnapshot.objects.create(
            legacy_id=_next_legacy_id(PersonContactSnapshot),
            organization_id=organization_id,
            person=person,
            contact_type=contact_type,
            value=value,
            normalized_value=normalized_value,
            is_primary=True,
        )
        return
    changed = False
    if existing.value != value:
        existing.value = value
        changed = True
    if existing.normalized_value != normalized_value:
        existing.normalized_value = normalized_value
        changed = True
    if not existing.is_primary:
        existing.is_primary = True
        changed = True
    if changed:
        existing.save()


def _upsert_primary_address(person: PersonSnapshot, form: NativePersonForm, *, organization_id: int) -> None:
    existing = PersonAddressSnapshot.objects.filter(person=person, is_primary=True).order_by("legacy_id").first()
    has_any = any(
        [
            form.cep,
            form.logradouro,
            form.numero,
            form.complemento,
            form.bairro,
            form.cidade,
            form.uf,
        ]
    )
    if not has_any:
        if existing is not None:
            existing.delete()
        return
    normalized_address = " | ".join(
        [
            normalize_query(form.logradouro),
            normalize_query(form.numero),
            normalize_query(form.complemento),
            normalize_query(form.bairro),
            normalize_query(form.cidade),
            normalize_query(form.uf),
            normalize_query(form.cep),
        ]
    ).strip(" |")
    if existing is None:
        PersonAddressSnapshot.objects.create(
            legacy_id=_next_legacy_id(PersonAddressSnapshot),
            organization_id=organization_id,
            person=person,
            address_type="residencial",
            cep=form.cep,
            street=form.logradouro,
            number=form.numero,
            complement=form.complemento,
            neighborhood=form.bairro,
            city=form.cidade,
            state=form.uf,
            is_primary=True,
            normalized_address=normalized_address,
        )
        return
    changed = False
    for field, value in [
        ("cep", form.cep),
        ("street", form.logradouro),
        ("number", form.numero),
        ("complement", form.complemento),
        ("neighborhood", form.bairro),
        ("city", form.cidade),
        ("state", form.uf),
        ("normalized_address", normalized_address),
    ]:
        if getattr(existing, field) != value:
            setattr(existing, field, value)
            changed = True
    if not existing.is_primary:
        existing.is_primary = True
        changed = True
    if changed:
        existing.save()


def _sync_member_profile(person: PersonSnapshot, *, organization_id: int, status: str) -> None:
    active_profile = (
        PersonProfileSnapshot.objects.filter(person=person, profile="membro", is_active=True)
        .order_by("legacy_id")
        .first()
    )
    should_have = status_grants_member_code(status)
    if should_have and active_profile is None:
        PersonProfileSnapshot.objects.create(
            legacy_id=_next_legacy_id(PersonProfileSnapshot),
            organization_id=organization_id,
            person=person,
            profile="membro",
            is_active=True,
        )
    if not should_have and active_profile is not None:
        active_profile.is_active = False
        active_profile.save(update_fields=["is_active", "synced_at"])


def _person_snapshot_dict(person: PersonSnapshot) -> dict[str, object]:
    return {
        "id": int(person.legacy_id or 0),
        "codigo_interno": person.internal_code or "",
        "nome": person.name or "",
        "cpf": person.cpf or "",
        "status": person.status or "",
        "email_principal": person.primary_email or "",
        "telefone_principal": person.primary_phone or "",
        "whatsapp_principal": person.primary_whatsapp or "",
        "cidade": (
            PersonAddressSnapshot.objects.filter(person=person, is_primary=True).values_list("city", flat=True).first()
            or ""
        ),
    }


def create_person_postgres(payload: dict[str, str], actor: str = "") -> int:
    form = _normalized_person_payload(payload)
    _assert_unique_snapshot_cpf(form.cpf)
    _assert_unique_snapshot_email(form.email_principal)
    organization_id = _default_organization_id()
    with transaction.atomic():
        legacy_id = _next_legacy_id(PersonSnapshot)
        person = PersonSnapshot.objects.create(
            legacy_id=legacy_id,
            organization_id=organization_id,
            internal_code=_resolved_member_code(form),
            name=form.nome,
            normalized_name=normalize_query(form.nome),
            social_name=form.nome_social,
            cpf=form.cpf,
            rg=form.rg,
            birth_date_raw=form.data_nascimento,
            sex=form.sexo,
            marital_status=form.estado_civil,
            primary_email=form.email_principal,
            normalized_email=form.email_principal,
            primary_phone=form.telefone_principal,
            primary_whatsapp=form.whatsapp_principal,
            status=form.status,
            is_archived=form.status == "arquivo_morto",
            is_active=True,
            notes=form.observacoes,
        )
        _upsert_primary_contact(person, contact_type="email", value=form.email_principal, organization_id=organization_id)
        _upsert_primary_contact(person, contact_type="telefone", value=form.telefone_principal, organization_id=organization_id)
        _upsert_primary_contact(person, contact_type="whatsapp", value=form.whatsapp_principal, organization_id=organization_id)
        _upsert_primary_address(person, form, organization_id=organization_id)
        _sync_member_profile(person, organization_id=organization_id, status=form.status)
    try:
        record_django_audit_event(
            actor=actor,
            action="criar_cadastro_postgres",
            table_name="people_personsnapshot",
            record_id=int(person.pk or 0),
            organization_id=organization_id,
            source="postgres_native_people",
            summary=f"Ficha criada diretamente no Postgres para {person.name}",
            after=_person_snapshot_dict(person),
        )
    except Exception:
        pass
    return int(person.legacy_id or 0)


def update_person_postgres(person_id: int, payload: dict[str, str], actor: str = "") -> None:
    person = PersonSnapshot.objects.filter(legacy_id=int(person_id or 0), is_active=True).first()
    if person is None:
        raise LegacyWriteError("Pessoa nao encontrada.")
    form = _normalized_person_payload(payload)
    _assert_unique_snapshot_cpf(form.cpf, ignore_person_id=int(person_id))
    _assert_unique_snapshot_email(form.email_principal, ignore_person_id=int(person_id))
    organization_id = int(person.organization_id or _default_organization_id())
    before = _person_snapshot_dict(person)
    with transaction.atomic():
        person.internal_code = _resolved_member_code(form, current_code=person.internal_code or "")
        person.name = form.nome
        person.normalized_name = normalize_query(form.nome)
        person.social_name = form.nome_social
        person.cpf = form.cpf
        person.rg = form.rg
        person.birth_date_raw = form.data_nascimento
        person.sex = form.sexo
        person.marital_status = form.estado_civil
        person.primary_email = form.email_principal
        person.normalized_email = form.email_principal
        person.primary_phone = form.telefone_principal
        person.primary_whatsapp = form.whatsapp_principal
        person.status = form.status
        person.is_archived = form.status == "arquivo_morto"
        person.notes = form.observacoes
        person.save()
        _upsert_primary_contact(person, contact_type="email", value=form.email_principal, organization_id=organization_id)
        _upsert_primary_contact(person, contact_type="telefone", value=form.telefone_principal, organization_id=organization_id)
        _upsert_primary_contact(person, contact_type="whatsapp", value=form.whatsapp_principal, organization_id=organization_id)
        _upsert_primary_address(person, form, organization_id=organization_id)
        _sync_member_profile(person, organization_id=organization_id, status=form.status)
    try:
        record_django_audit_event(
            actor=actor,
            action="atualizar_cadastro_postgres",
            table_name="people_personsnapshot",
            record_id=int(person.pk or 0),
            organization_id=organization_id,
            source="postgres_native_people",
            summary=f"Ficha atualizada diretamente no Postgres para {person.name}",
            before=before,
            after=_person_snapshot_dict(person),
        )
    except Exception:
        pass


def get_person_form_initial_postgres(person_id: int) -> dict[str, str] | None:
    person = PersonSnapshot.objects.filter(legacy_id=int(person_id or 0), is_active=True).first()
    if person is None:
        return None
    address = PersonAddressSnapshot.objects.filter(person=person, is_primary=True).order_by("legacy_id").first()
    initial = empty_person_form()
    initial.update(
        {
            "codigo_interno": person.internal_code or "",
            "nome": person.name or "",
            "nome_social": person.social_name or "",
            "cpf": person.cpf or "",
            "rg": person.rg or "",
            "data_nascimento": person.birth_date_raw or "",
            "sexo": person.sex or "",
            "estado_civil": person.marital_status or "",
            "email_principal": person.primary_email or "",
            "telefone_principal": person.primary_phone or "",
            "whatsapp_principal": person.primary_whatsapp or "",
            "status": person.status or "frequentador",
            "observacoes": person.notes or "",
        }
    )
    if address is not None:
        initial.update(
            {
                "cep": address.cep or "",
                "logradouro": address.street or "",
                "numero": address.number or "",
                "complemento": address.complement or "",
                "bairro": address.neighborhood or "",
                "cidade": address.city or "",
                "uf": address.state or "",
            }
        )
    return initial


def validate_person_cpf_postgres(value: object, ignore_person_id: int = 0) -> dict[str, object]:
    try:
        cpf_value = manual_cpf_or_error(value, LegacyWriteError)
    except LegacyWriteError as exc:
        return {"ok": False, "message": str(exc), "normalized": ""}
    if not cpf_value:
        return {"ok": True, "message": "", "normalized": ""}
    try:
        _assert_unique_snapshot_cpf(cpf_value, ignore_person_id=ignore_person_id)
    except LegacyWriteError as exc:
        return {"ok": False, "message": str(exc), "normalized": cpf_value}
    return {"ok": True, "message": "CPF valido e ainda nao usado por outra ficha ativa no Postgres.", "normalized": cpf_value}


def validate_person_email_postgres(value: object, ignore_person_id: int = 0) -> dict[str, object]:
    try:
        email = manual_email_or_error(value, LegacyWriteError)
    except LegacyWriteError as exc:
        return {"ok": False, "message": str(exc), "normalized": ""}
    if not email:
        return {"ok": True, "message": "", "normalized": ""}
    try:
        _assert_unique_snapshot_email(email, ignore_person_id=ignore_person_id)
    except LegacyWriteError as exc:
        return {"ok": False, "message": str(exc), "normalized": email}
    return {"ok": True, "message": "E-mail valido e ainda nao usado por outra ficha ativa no Postgres.", "normalized": email}


def _append_note(existing: str, new_note: str) -> str:
    parts = [normalize_query(existing), normalize_query(new_note)]
    return "\n".join(part for part in parts if part)


def _merge_pick_text(primary_value: object, duplicate_value: object, *, prefer_duplicate: bool = False) -> str:
    primary_text = normalize_query(primary_value)
    duplicate_text = normalize_query(duplicate_value)
    if prefer_duplicate and duplicate_text:
        return duplicate_text
    return primary_text or duplicate_text


def _merge_pick_name(primary_name: object, duplicate_name: object, *, prefer_duplicate_name: bool = False) -> str:
    primary_text = normalize_query(primary_name)
    duplicate_text = normalize_query(duplicate_name)
    if prefer_duplicate_name and duplicate_text:
        return duplicate_text
    if not primary_text:
        return duplicate_text
    if not duplicate_text:
        return primary_text
    primary_words = len(primary_text.split())
    duplicate_words = len(duplicate_text.split())
    if duplicate_words > primary_words and primary_words <= 1:
        return duplicate_text
    if len(duplicate_text) > len(primary_text) + 4 and primary_words <= 1:
        return duplicate_text
    return primary_text


def _merge_notes(primary_notes: object, duplicate_notes: object, merge_note: str) -> str:
    chunks: list[str] = []
    for raw in (primary_notes, duplicate_notes):
        text = normalize_query(raw)
        if text and text not in chunks:
            chunks.append(text)
    if merge_note and merge_note not in chunks:
        chunks.append(merge_note)
    return "\n".join(chunks)


def _coerce_possible_birth_date(value: object) -> tuple[str, datetime.date | None]:
    raw = normalize_query(value)
    if not raw:
        return "", None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            parsed = datetime.strptime(raw, fmt).date()
            return raw, parsed
        except ValueError:
            continue
    return raw, None


def _merge_birth_date(primary_value: object, duplicate_value: object) -> tuple[str, bool]:
    primary_raw, primary_date = _coerce_possible_birth_date(primary_value)
    duplicate_raw, duplicate_date = _coerce_possible_birth_date(duplicate_value)
    if primary_date and duplicate_date and primary_date != duplicate_date:
        return "", True
    if primary_date and not duplicate_date:
        return primary_raw, False
    if duplicate_date and not primary_date:
        return duplicate_raw, False
    if primary_date and duplicate_date:
        return primary_raw or duplicate_raw, False
    return _merge_pick_text(primary_raw, duplicate_raw), False


def _address_score_from_snapshot(address: PersonAddressSnapshot | None) -> int:
    if address is None:
        return 0
    key = family_address_key(
        {
            "cep": address.cep or "",
            "logradouro": address.street or "",
            "numero": address.number or "",
            "complemento": address.complement or "",
            "bairro": address.neighborhood or "",
            "cidade": address.city or "",
            "uf": address.state or "",
        }
    )
    return sum(1 for value in key if value)


def _merge_duplicate_contacts_postgres(
    *,
    primary_person: PersonSnapshot,
    duplicate_person: PersonSnapshot,
) -> dict[str, int]:
    primary_keys = {
        (normalize_query(row.contact_type).lower(), normalize_query(row.value).lower())
        for row in PersonContactSnapshot.objects.filter(person=primary_person).order_by("legacy_id")
    }
    moved = 0
    removed = 0
    rows = list(
        PersonContactSnapshot.objects.filter(person=duplicate_person).order_by("-is_primary", "legacy_id")
    )
    for row in rows:
        key = (normalize_query(row.contact_type).lower(), normalize_query(row.value).lower())
        if key in primary_keys:
            row.delete()
            removed += 1
            continue
        row.person = primary_person
        row.save(update_fields=["person", "synced_at"])
        primary_keys.add(key)
        moved += 1
    for kind in ("email", "telefone", "whatsapp", "celular"):
        rows = list(
            PersonContactSnapshot.objects.filter(person=primary_person, contact_type=kind).order_by("-is_primary", "legacy_id")
        )
        for index, row in enumerate(rows):
            desired = index == 0
            if bool(row.is_primary) != desired:
                row.is_primary = desired
                row.save(update_fields=["is_primary", "synced_at"])
    return {"moved": moved, "removed": removed}


def _merge_duplicate_addresses_postgres(
    *,
    primary_person: PersonSnapshot,
    duplicate_person: PersonSnapshot,
) -> dict[str, int]:
    primary_keys = {
        (
            normalize_query(row.cep).lower(),
            normalize_query(row.street).lower(),
            normalize_query(row.number).lower(),
            normalize_query(row.complement).lower(),
            normalize_query(row.neighborhood).lower(),
            normalize_query(row.city).lower(),
            normalize_query(row.state).lower(),
        ): int(row.legacy_id or 0)
        for row in PersonAddressSnapshot.objects.filter(person=primary_person).order_by("-is_primary", "legacy_id")
    }
    moved = 0
    removed = 0
    rows = list(
        PersonAddressSnapshot.objects.filter(person=duplicate_person).order_by("-is_primary", "legacy_id")
    )
    for row in rows:
        key = (
            normalize_query(row.cep).lower(),
            normalize_query(row.street).lower(),
            normalize_query(row.number).lower(),
            normalize_query(row.complement).lower(),
            normalize_query(row.neighborhood).lower(),
            normalize_query(row.city).lower(),
            normalize_query(row.state).lower(),
        )
        if key in primary_keys or _address_score_from_snapshot(row) <= 1:
            row.delete()
            removed += 1
            continue
        row.person = primary_person
        row.save(update_fields=["person", "synced_at"])
        primary_keys[key] = int(row.legacy_id or 0)
        moved += 1
    rows = list(PersonAddressSnapshot.objects.filter(person=primary_person).order_by("-is_primary", "legacy_id"))
    if rows:
        rich_rows = [row for row in rows if _address_score_from_snapshot(row) >= 3]
        if rich_rows:
            for row in list(rows):
                if _address_score_from_snapshot(row) <= 1:
                    row.delete()
                    removed += 1
            rows = list(PersonAddressSnapshot.objects.filter(person=primary_person).order_by("-is_primary", "legacy_id"))
        best_row = max(
            rows,
            key=lambda row: (
                _address_score_from_snapshot(row),
                int(bool(row.is_primary)),
                -int(row.legacy_id or 0),
            ),
        )
        for row in rows:
            desired = int(row.legacy_id or 0) == int(best_row.legacy_id or 0)
            if bool(row.is_primary) != desired:
                row.is_primary = desired
                row.save(update_fields=["is_primary", "synced_at"])
    return {"moved": moved, "removed": removed}


def _merge_duplicate_profiles_postgres(
    *,
    primary_person: PersonSnapshot,
    duplicate_person: PersonSnapshot,
) -> dict[str, int]:
    existing = {
        (normalize_query(row.profile).lower(), int(bool(row.is_active)))
        for row in PersonProfileSnapshot.objects.filter(person=primary_person).order_by("legacy_id")
    }
    moved = 0
    removed = 0
    rows = list(PersonProfileSnapshot.objects.filter(person=duplicate_person).order_by("legacy_id"))
    for row in rows:
        key = (normalize_query(row.profile).lower(), int(bool(row.is_active)))
        if key in existing:
            row.delete()
            removed += 1
            continue
        row.person = primary_person
        row.save(update_fields=["person", "synced_at"])
        existing.add(key)
        moved += 1
    return {"moved": moved, "removed": removed}


def _normalize_merged_relationships_postgres(primary_person: PersonSnapshot) -> dict[str, int]:
    deleted_self = PersonRelationshipSnapshot.objects.filter(
        person=primary_person,
        related_person=primary_person,
    ).delete()[0]
    rows = (
        PersonRelationshipSnapshot.objects.filter(
            models.Q(person=primary_person) | models.Q(related_person=primary_person)
        )
        .values("person_id", "related_person_id", "relationship_type", "is_active")
        .annotate(keep_id=models.Min("id"), total=models.Count("id"))
        .filter(total__gt=1)
    )
    deleted_duplicates = 0
    for row in rows:
        deleted_duplicates += (
            PersonRelationshipSnapshot.objects.filter(
                person_id=row["person_id"],
                related_person_id=row["related_person_id"],
                relationship_type=row["relationship_type"],
                is_active=row["is_active"],
            )
            .exclude(id=row["keep_id"])
            .delete()[0]
        )
    return {"deleted_self": int(deleted_self or 0), "deleted_duplicates": int(deleted_duplicates or 0)}


def merge_people_postgres(
    primary_person_id: int,
    duplicate_person_id: int,
    *,
    reason: str,
    actor: str = "",
    prefer_duplicate_name: bool = False,
) -> dict[str, object]:
    primary_person_id = moneyless_int(primary_person_id)
    duplicate_person_id = moneyless_int(duplicate_person_id)
    reason_text = normalize_query(reason)
    if not primary_person_id or not duplicate_person_id:
        raise LegacyWriteError("Escolha duas fichas validas para a mesclagem.")
    if primary_person_id == duplicate_person_id:
        raise LegacyWriteError("A ficha principal e a duplicada nao podem ser a mesma.")
    if len(reason_text) < 8:
        raise LegacyWriteError("Informe uma justificativa com pelo menos 8 caracteres para mesclar as fichas.")

    primary = PersonSnapshot.objects.filter(legacy_id=primary_person_id, is_active=True).first()
    duplicate = PersonSnapshot.objects.filter(legacy_id=duplicate_person_id, is_active=True).first()
    if primary is None or duplicate is None:
        raise LegacyWriteError("Uma das fichas nao foi encontrada para a mesclagem.")
    if int(primary.organization_id or 0) != int(duplicate.organization_id or 0):
        raise LegacyWriteError("As duas fichas precisam pertencer a mesma organizacao.")

    primary_cpf = normalize_query(primary.cpf)
    duplicate_cpf = normalize_query(duplicate.cpf)
    if primary_cpf and duplicate_cpf and primary_cpf != duplicate_cpf:
        raise LegacyWriteError("As fichas possuem CPFs diferentes. Revise manualmente antes de qualquer merge.")
    merged_birth_date_raw, birth_conflict = _merge_birth_date(primary.birth_date_raw, duplicate.birth_date_raw)
    if birth_conflict:
        raise LegacyWriteError("As fichas possuem datas de nascimento diferentes. Revise manualmente antes de mesclar.")
    merged_birth_date = _coerce_possible_birth_date(merged_birth_date_raw)[1]
    primary_sex = normalize_query(primary.sex)
    duplicate_sex = normalize_query(duplicate.sex)
    if primary_sex and duplicate_sex and primary_sex != duplicate_sex:
        raise LegacyWriteError("As fichas possuem sexo diferente. Revise manualmente antes de mesclar.")

    before_primary = _person_full_snapshot(primary)
    before_duplicate = _person_full_snapshot(duplicate)
    merge_note = (
        f"Ficha duplicada #{duplicate_person_id} ({normalize_query(duplicate.name) or 'sem nome'}) "
        f"mesclada em {datetime.now().date().isoformat()} por {actor or 'django'}."
    )
    final_name = _merge_pick_name(primary.name, duplicate.name, prefer_duplicate_name=prefer_duplicate_name)
    final_payload = {
        "name": final_name,
        "normalized_name": normalize_query(final_name),
        "social_name": _merge_pick_text(primary.social_name, duplicate.social_name),
        "cpf": primary_cpf or duplicate_cpf,
        "rg": _merge_pick_text(primary.rg, duplicate.rg),
        "birth_date_raw": merged_birth_date_raw,
        "birth_date": merged_birth_date,
        "sex": _merge_pick_text(primary.sex, duplicate.sex),
        "marital_status": _merge_pick_text(primary.marital_status, duplicate.marital_status),
        "primary_email": _merge_pick_text(primary.primary_email, duplicate.primary_email),
        "primary_phone": _merge_pick_text(primary.primary_phone, duplicate.primary_phone),
        "primary_whatsapp": _merge_pick_text(primary.primary_whatsapp, duplicate.primary_whatsapp),
        "notes": _merge_notes(
            primary.notes,
            duplicate.notes,
            f"{merge_note} Codigo absorvido: {normalize_query(duplicate.internal_code) or 'sem codigo'}. Justificativa: {reason_text}",
        ),
    }
    primary_address = PersonAddressSnapshot.objects.filter(person=primary, is_primary=True).order_by("legacy_id").first()
    duplicate_address = PersonAddressSnapshot.objects.filter(person=duplicate, is_primary=True).order_by("legacy_id").first()
    chosen_address = duplicate_address if _address_score_from_snapshot(duplicate_address) > _address_score_from_snapshot(primary_address) else primary_address

    counts: dict[str, int] = {}
    with transaction.atomic():
        trash_row = PersonSecureTrashSnapshot.objects.create(
            legacy_id=_next_trash_legacy_id(),
            organization_id=int(duplicate.organization_id or 0),
            person_legacy_id=int(duplicate.legacy_id or 0),
            person_name=duplicate.name or "",
            person_cpf=duplicate.cpf or "",
            original_status=duplicate.status or "",
            original_code=duplicate.internal_code or "",
            reason=f"Mesclada na ficha #{primary_person_id}. {reason_text}",
            operator=actor,
            snapshot_data=before_duplicate,
            restored=False,
        )

        primary.name = str(final_payload["name"] or "")
        primary.normalized_name = str(final_payload["normalized_name"] or "")
        primary.social_name = str(final_payload["social_name"] or "")
        primary.cpf = str(final_payload["cpf"] or "")
        primary.rg = str(final_payload["rg"] or "")
        primary.birth_date_raw = str(final_payload["birth_date_raw"] or "")
        primary.birth_date = final_payload["birth_date"]
        primary.sex = str(final_payload["sex"] or "")
        primary.marital_status = str(final_payload["marital_status"] or "")
        primary.primary_email = str(final_payload["primary_email"] or "")
        primary.normalized_email = str(final_payload["primary_email"] or "")
        primary.primary_phone = str(final_payload["primary_phone"] or "")
        primary.primary_whatsapp = str(final_payload["primary_whatsapp"] or "")
        primary.notes = str(final_payload["notes"] or "")
        primary.save()

        if chosen_address is not None and _address_score_from_snapshot(chosen_address):
            PersonAddressSnapshot.objects.filter(person=primary, is_primary=True).update(is_primary=False)
            chosen_address.person = primary
            chosen_address.is_primary = True
            chosen_address.save(update_fields=["person", "is_primary", "synced_at"])
        _upsert_primary_contact(
            primary,
            contact_type="email",
            value=str(final_payload["primary_email"] or ""),
            organization_id=int(primary.organization_id or _default_organization_id()),
        )
        _upsert_primary_contact(
            primary,
            contact_type="telefone",
            value=str(final_payload["primary_phone"] or ""),
            organization_id=int(primary.organization_id or _default_organization_id()),
        )
        _upsert_primary_contact(
            primary,
            contact_type="whatsapp",
            value=str(final_payload["primary_whatsapp"] or ""),
            organization_id=int(primary.organization_id or _default_organization_id()),
        )

        counts["history"] = PersonHistorySnapshot.objects.filter(person=duplicate).update(person=primary)
        counts["contributors"] = PersonContributorSnapshot.objects.filter(person=duplicate).update(person=primary)
        counts["contributor_identifiers"] = PersonIdentifierSnapshot.objects.filter(person=duplicate).update(person=primary)
        counts["contributions"] = PersonContributionSnapshot.objects.filter(person=duplicate).update(person=primary)
        counts["receipts"] = ReceiptSnapshot.objects.filter(person_legacy_id=duplicate_person_id).update(person_legacy_id=primary_person_id)
        counts["receipt_dispatches"] = ReceiptDispatch.objects.filter(legacy_person_id=duplicate_person_id).update(legacy_person_id=primary_person_id)
        counts["statement_suggested"] = StatementImportPilotMovement.objects.filter(
            suggested_person_legacy_id=duplicate_person_id
        ).update(suggested_person_legacy_id=primary_person_id)
        counts["statement_resolved"] = StatementImportPilotMovement.objects.filter(
            resolved_person_legacy_id=duplicate_person_id
        ).update(resolved_person_legacy_id=primary_person_id)
        counts["relationships_origin"] = PersonRelationshipSnapshot.objects.filter(person=duplicate).update(person=primary)
        counts["relationships_related"] = PersonRelationshipSnapshot.objects.filter(related_person=duplicate).update(related_person=primary)
        counts["household_heads"] = HouseholdProfile.objects.filter(head_person_id=duplicate_person_id).update(
            head_person_id=primary_person_id
        )

        dedupe_contacts = _merge_duplicate_contacts_postgres(primary_person=primary, duplicate_person=duplicate)
        counts["contacts"] = dedupe_contacts["moved"]
        dedupe_addresses = _merge_duplicate_addresses_postgres(primary_person=primary, duplicate_person=duplicate)
        counts["addresses"] = dedupe_addresses["moved"]
        dedupe_profiles = _merge_duplicate_profiles_postgres(primary_person=primary, duplicate_person=duplicate)
        counts["profiles"] = dedupe_profiles["moved"]
        relationship_cleanup = _normalize_merged_relationships_postgres(primary)
        _sync_member_profile(primary, organization_id=int(primary.organization_id or _default_organization_id()), status=primary.status or duplicate.status or "membro_ativo")

        duplicate.is_active = False
        duplicate.is_archived = True
        duplicate.notes = _merge_notes(
            duplicate.notes,
            "",
            f"Ficha mesclada na pessoa #{primary_person_id} em {datetime.now().date().isoformat()}. Justificativa: {reason_text}",
        )
        duplicate.save(update_fields=["is_active", "is_archived", "notes", "synced_at"])

    after_primary = _person_full_snapshot(primary)
    after_primary["merged_duplicate_person_id"] = duplicate_person_id
    after_primary["merged_duplicate_trash_id"] = int(trash_row.legacy_id or 0)
    after_primary["merge_counts"] = {
        **counts,
        "contacts_deduped": dedupe_contacts,
        "addresses_deduped": dedupe_addresses,
        "profiles_deduped": dedupe_profiles,
        "relationships_cleaned": relationship_cleanup,
    }
    after_primary["merge_reason"] = reason_text
    after_duplicate = {
        "merged_into_person_id": primary_person_id,
        "trash_id": int(trash_row.legacy_id or 0),
        "ativo": False,
        "nome": duplicate.name or "",
        "codigo_interno": duplicate.internal_code or "",
        "merge_reason": reason_text,
    }
    try:
        record_django_audit_event(
            actor=actor,
            action="mesclar_fichas_postgres",
            table_name="people_personsnapshot",
            record_id=int(primary.pk or 0),
            organization_id=int(primary.organization_id or 0),
            source="postgres_native_people",
            summary="Mesclagem de fichas executada diretamente no Postgres.",
            before={"principal": before_primary, "duplicada": before_duplicate},
            after=after_primary,
        )
        record_django_audit_event(
            actor=actor,
            action="mesclar_ficha_origem_postgres",
            table_name="people_personsnapshot",
            record_id=int(duplicate.pk or 0),
            organization_id=int(primary.organization_id or 0),
            source="postgres_native_people",
            summary="Ficha de origem marcada como absorvida por mesclagem no Postgres.",
            before=before_duplicate,
            after=after_duplicate,
        )
    except Exception:
        pass
    return {
        "primary_person_id": primary_person_id,
        "duplicate_person_id": duplicate_person_id,
        "duplicate_trash_id": int(trash_row.legacy_id or 0),
        "primary_name": final_name,
        "counts": {
            **counts,
            "contacts_deduped": dedupe_contacts,
            "addresses_deduped": dedupe_addresses,
            "profiles_deduped": dedupe_profiles,
            "relationships_cleaned": relationship_cleanup,
        },
    }


def _active_person_snapshot(person_id: int) -> PersonSnapshot:
    person = PersonSnapshot.objects.filter(legacy_id=int(person_id or 0), is_active=True).first()
    if person is None:
        raise LegacyWriteError("Pessoa principal ou pessoa relacionada nao encontrada.")
    return person


def _resolved_relationship_type(payload: dict[str, str] | object) -> str:
    if isinstance(payload, dict):
        relationship_type = normalize_query(payload.get("tipo_relacionamento"))
    else:
        getter = getattr(payload, "get", None)
        relationship_type = normalize_query(getter("tipo_relacionamento", "nucleo_familiar") if getter else "")
    relationship_type = relationship_type or "nucleo_familiar"
    if relationship_type not in ALLOWED_FAMILY_RELATIONSHIP_TYPES:
        raise LegacyWriteError("Tipo de relacao familiar invalido.")
    return relationship_type


def _relationship_notes(payload: dict[str, str] | object) -> str:
    if isinstance(payload, dict):
        return normalize_query(payload.get("observacoes"))
    getter = getattr(payload, "get", None)
    return normalize_query(getter("observacoes") if getter else "")


def create_person_relationship_postgres(person_id: int, payload: object, actor: str = "") -> int:
    person_id = moneyless_int(person_id)
    related_person_id = moneyless_int(getattr(payload, "get", lambda *_args, **_kwargs: 0)("related_person_id"))
    relationship_type = _resolved_relationship_type(payload)
    notes = _relationship_notes(payload)
    if not person_id or not related_person_id:
        raise LegacyWriteError("Escolha a pessoa principal e a pessoa relacionada.")
    if person_id == related_person_id:
        raise LegacyWriteError("A pessoa relacionada nao pode ser a propria ficha.")
    person = _active_person_snapshot(person_id)
    related_person = _active_person_snapshot(related_person_id)
    if int(person.organization_id or 0) != int(related_person.organization_id or 0):
        raise LegacyWriteError("As duas pessoas precisam pertencer a mesma organizacao.")
    duplicate = (
        PersonRelationshipSnapshot.objects.filter(
            organization_id=int(person.organization_id or 0),
            is_active=True,
        )
        .filter(
            models.Q(person=person, related_person=related_person)
            | models.Q(person=related_person, related_person=person)
        )
        .order_by("legacy_id")
        .first()
    )
    if duplicate is not None:
        return int(duplicate.legacy_id or 0)
    relationship = PersonRelationshipSnapshot.objects.create(
        legacy_id=_next_legacy_id(PersonRelationshipSnapshot),
        organization_id=int(person.organization_id or 0),
        person=person,
        related_person=related_person,
        relationship_type=relationship_type,
        notes=notes,
        is_active=True,
    )
    try:
        record_django_audit_event(
            actor=actor,
            action="criar_vinculo_familiar_postgres",
            table_name="people_personrelationshipsnapshot",
            record_id=int(relationship.pk or 0),
            organization_id=int(person.organization_id or 0),
            source="postgres_native_people",
            summary="Relacao familiar criada diretamente no Postgres.",
            after={
                "id_legado": int(relationship.legacy_id or 0),
                "pessoa_id": int(person.legacy_id or 0),
                "pessoa_relacionada_id": int(related_person.legacy_id or 0),
                "tipo_relacionamento": relationship.relationship_type,
                "observacoes": relationship.notes or "",
                "ativo": True,
            },
        )
    except Exception:
        pass
    return int(relationship.legacy_id or 0)


def update_person_relationship_postgres(person_id: int, relationship_id: int, payload: object, actor: str = "") -> None:
    relationship = (
        PersonRelationshipSnapshot.objects.filter(
            legacy_id=int(relationship_id or 0),
            is_active=True,
        )
        .filter(models.Q(person__legacy_id=int(person_id or 0)) | models.Q(related_person__legacy_id=int(person_id or 0)))
        .first()
    )
    if relationship is None:
        raise LegacyWriteError("Relacao familiar nao encontrada para esta ficha.")
    before = {
        "tipo_relacionamento": relationship.relationship_type,
        "observacoes": relationship.notes or "",
        "ativo": bool(relationship.is_active),
    }
    relationship.relationship_type = _resolved_relationship_type(payload)
    relationship.notes = _relationship_notes(payload)
    relationship.save(update_fields=["relationship_type", "notes", "synced_at"])
    try:
        record_django_audit_event(
            actor=actor,
            action="atualizar_vinculo_familiar_postgres",
            table_name="people_personrelationshipsnapshot",
            record_id=int(relationship.pk or 0),
            organization_id=int(relationship.organization_id or 0),
            source="postgres_native_people",
            summary="Relacao familiar atualizada diretamente no Postgres.",
            before=before,
            after={
                "tipo_relacionamento": relationship.relationship_type,
                "observacoes": relationship.notes or "",
                "ativo": bool(relationship.is_active),
            },
        )
    except Exception:
        pass


def deactivate_person_relationship_postgres(person_id: int, relationship_id: int, actor: str = "") -> None:
    relationship = (
        PersonRelationshipSnapshot.objects.filter(
            legacy_id=int(relationship_id or 0),
            is_active=True,
        )
        .filter(models.Q(person__legacy_id=int(person_id or 0)) | models.Q(related_person__legacy_id=int(person_id or 0)))
        .first()
    )
    if relationship is None:
        raise LegacyWriteError("Relacao familiar nao encontrada para esta ficha.")
    before = {
        "tipo_relacionamento": relationship.relationship_type,
        "observacoes": relationship.notes or "",
        "ativo": bool(relationship.is_active),
    }
    relationship.is_active = False
    relationship.notes = _append_note(
        relationship.notes or "",
        "Ignorado manualmente pelo operador para nao recriar familia domiciliar por endereco.",
    )
    relationship.save(update_fields=["is_active", "notes", "synced_at"])
    try:
        record_django_audit_event(
            actor=actor,
            action="desativar_vinculo_familiar_postgres",
            table_name="people_personrelationshipsnapshot",
            record_id=int(relationship.pk or 0),
            organization_id=int(relationship.organization_id or 0),
            source="postgres_native_people",
            summary="Relacao familiar desativada diretamente no Postgres.",
            before=before,
            after={
                "tipo_relacionamento": relationship.relationship_type,
                "observacoes": relationship.notes or "",
                "ativo": False,
            },
        )
    except Exception:
        pass


def suppress_family_suggestion_postgres(person_id: int, related_person_id: int, actor: str = "") -> int:
    person_id = moneyless_int(person_id)
    related_person_id = moneyless_int(related_person_id)
    if not person_id or not related_person_id:
        raise LegacyWriteError("Escolha a pessoa principal e a pessoa relacionada.")
    if person_id == related_person_id:
        raise LegacyWriteError("A pessoa relacionada nao pode ser a propria ficha.")
    person = _active_person_snapshot(person_id)
    related_person = _active_person_snapshot(related_person_id)
    if int(person.organization_id or 0) != int(related_person.organization_id or 0):
        raise LegacyWriteError("As duas pessoas precisam pertencer a mesma organizacao.")
    note = "Ignorado manualmente pelo operador para nao recriar familia domiciliar por endereco."
    relationship = (
        PersonRelationshipSnapshot.objects.filter(organization_id=int(person.organization_id or 0))
        .filter(
            models.Q(person=person, related_person=related_person)
            | models.Q(person=related_person, related_person=person)
        )
        .order_by("-is_active", "legacy_id")
        .first()
    )
    before = None
    if relationship is None:
        relationship = PersonRelationshipSnapshot.objects.create(
            legacy_id=_next_legacy_id(PersonRelationshipSnapshot),
            organization_id=int(person.organization_id or 0),
            person=person,
            related_person=related_person,
            relationship_type="nucleo_familiar",
            notes=note,
            is_active=False,
        )
    else:
        before = {
            "tipo_relacionamento": relationship.relationship_type,
            "observacoes": relationship.notes or "",
            "ativo": bool(relationship.is_active),
        }
        relationship.relationship_type = "nucleo_familiar"
        relationship.notes = _append_note(relationship.notes or "", note)
        relationship.is_active = False
        relationship.save(update_fields=["relationship_type", "notes", "is_active", "synced_at"])
    try:
        record_django_audit_event(
            actor=actor,
            action="ignorar_sugestao_nucleo_familiar_postgres",
            table_name="people_personrelationshipsnapshot",
            record_id=int(relationship.pk or 0),
            organization_id=int(relationship.organization_id or 0),
            source="postgres_native_people",
            summary="Sugestao de familia domiciliar ignorada diretamente no Postgres.",
            before=before,
            after={
                "tipo_relacionamento": relationship.relationship_type,
                "observacoes": relationship.notes or "",
                "ativo": bool(relationship.is_active),
            },
        )
    except Exception:
        pass
    return int(relationship.legacy_id or 0)


def create_family_group_relationships_postgres(person_ids: str | list[int], actor: str = "") -> int:
    ids = [moneyless_int(item) for item in (person_ids.split(",") if isinstance(person_ids, str) else person_ids)]
    ids = sorted({item for item in ids if item})
    if len(ids) < 2:
        raise LegacyWriteError("Escolha pelo menos duas pessoas para formar a familia domiciliar.")
    created = 0
    for index, left_id in enumerate(ids):
        for right_id in ids[index + 1 :]:
            before = (
                PersonRelationshipSnapshot.objects.filter(is_active=True)
                .filter(
                    models.Q(person__legacy_id=left_id, related_person__legacy_id=right_id)
                    | models.Q(person__legacy_id=right_id, related_person__legacy_id=left_id)
                )
                .first()
            )
            if before is not None:
                continue
            create_person_relationship_postgres(
                left_id,
                {
                    "related_person_id": str(right_id),
                    "tipo_relacionamento": "nucleo_familiar",
                    "observacoes": "",
                },
                actor=actor,
            )
            created += 1
    return created


def suppress_family_group_suggestions_postgres(person_ids: str | list[int], actor: str = "") -> int:
    ids = [moneyless_int(item) for item in (person_ids.split(",") if isinstance(person_ids, str) else person_ids)]
    ids = sorted({item for item in ids if item})
    if len(ids) < 2:
        raise LegacyWriteError("Escolha pelo menos duas pessoas para ignorar a sugestao.")
    suppressed = 0
    for index, left_id in enumerate(ids):
        for right_id in ids[index + 1 :]:
            suppress_family_suggestion_postgres(left_id, right_id, actor=actor)
            suppressed += 1
    return suppressed


def _address_keys_for_person(person: PersonSnapshot) -> set[tuple[str, ...]]:
    keys: set[tuple[str, ...]] = set()
    for address in PersonAddressSnapshot.objects.filter(person=person):
        key = family_address_key(
            {
                "cep": address.cep or "",
                "logradouro": address.street or "",
                "numero": address.number or "",
                "complemento": address.complement or "",
                "bairro": address.neighborhood or "",
                "cidade": address.city or "",
                "uf": address.state or "",
            }
        )
        if key:
            keys.add(key)
    return keys


def _is_auto_address_relationship(relationship: PersonRelationshipSnapshot) -> bool:
    return (
        normalize_match_name(relationship.notes or "").find(normalize_match_name("Criado automaticamente por endereco")) >= 0
        and normalize_query(relationship.relationship_type) == "nucleo_familiar"
    )


def _is_manual_family_suppression(relationship: PersonRelationshipSnapshot) -> bool:
    return (
        not relationship.is_active
        and normalize_match_name(relationship.notes or "").find(
            normalize_match_name("Ignorado manualmente pelo operador para nao recriar familia domiciliar por endereco.")
        )
        >= 0
        and normalize_query(relationship.relationship_type) == "nucleo_familiar"
    )


def _active_relationship_between_exists_postgres(organization_id: int, left_person_id: int, right_person_id: int) -> bool:
    return PersonRelationshipSnapshot.objects.filter(
        organization_id=int(organization_id or 0),
        is_active=True,
    ).filter(
        models.Q(person__legacy_id=int(left_person_id or 0), related_person__legacy_id=int(right_person_id or 0))
        | models.Q(person__legacy_id=int(right_person_id or 0), related_person__legacy_id=int(left_person_id or 0))
    ).exists()


def _relationship_pair_has_manual_suppression_postgres(organization_id: int, left_person_id: int, right_person_id: int) -> bool:
    rows = PersonRelationshipSnapshot.objects.filter(
        organization_id=int(organization_id or 0),
        is_active=False,
    ).filter(
        models.Q(person__legacy_id=int(left_person_id or 0), related_person__legacy_id=int(right_person_id or 0))
        | models.Q(person__legacy_id=int(right_person_id or 0), related_person__legacy_id=int(left_person_id or 0))
    )
    return any(_is_manual_family_suppression(row) for row in rows)


def sync_person_household_relationships_postgres(person_id: int, actor: str = "") -> dict[str, int]:
    person = _active_person_snapshot(person_id)
    current_keys = _address_keys_for_person(person)
    deactivated = 0
    active_relationships = PersonRelationshipSnapshot.objects.filter(
        organization_id=int(person.organization_id or 0),
        is_active=True,
    ).filter(models.Q(person=person) | models.Q(related_person=person))
    for relationship in active_relationships:
        if not _is_auto_address_relationship(relationship):
            continue
        related_person = relationship.related_person if relationship.person_id == person.id else relationship.person
        related_keys = _address_keys_for_person(related_person)
        if current_keys and related_keys and current_keys.intersection(related_keys):
            continue
        before = {
            "tipo_relacionamento": relationship.relationship_type,
            "observacoes": relationship.notes or "",
            "ativo": bool(relationship.is_active),
        }
        relationship.is_active = False
        relationship.notes = _append_note(relationship.notes or "", "Desativado automaticamente por endereco divergente.")
        relationship.save(update_fields=["is_active", "notes", "synced_at"])
        deactivated += 1
        try:
            record_django_audit_event(
                actor=actor,
                action="desativar_nucleo_familiar_endereco_postgres",
                table_name="people_personrelationshipsnapshot",
                record_id=int(relationship.pk or 0),
                organization_id=int(relationship.organization_id or 0),
                source="postgres_native_people",
                summary="Relacao familiar automatica desativada por divergencia de endereco.",
                before=before,
                after={
                    "tipo_relacionamento": relationship.relationship_type,
                    "observacoes": relationship.notes or "",
                    "ativo": False,
                },
            )
        except Exception:
            pass
    if not current_keys:
        return {"created": 0, "deactivated": deactivated}
    candidates = (
        PersonSnapshot.objects.filter(organization_id=int(person.organization_id or 0), is_active=True)
        .exclude(pk=person.pk)
        .order_by("legacy_id")
    )
    created = 0
    for candidate in candidates:
        if not _address_keys_for_person(candidate).intersection(current_keys):
            continue
        if _active_relationship_between_exists_postgres(person.organization_id, person.legacy_id, candidate.legacy_id):
            continue
        if _relationship_pair_has_manual_suppression_postgres(person.organization_id, person.legacy_id, candidate.legacy_id):
            continue
        create_person_relationship_postgres(
            int(person.legacy_id or 0),
            {
                "related_person_id": str(int(candidate.legacy_id or 0)),
                "tipo_relacionamento": "nucleo_familiar",
                "observacoes": "Criado automaticamente por endereco completo exatamente igual.",
            },
            actor=actor,
        )
        created += 1
    return {"created": created, "deactivated": deactivated}


def _person_full_snapshot(person: PersonSnapshot) -> dict[str, object]:
    addresses = list(
        PersonAddressSnapshot.objects.filter(person=person)
        .order_by("-is_primary", "legacy_id")
        .values("legacy_id", "cep", "street", "number", "complement", "neighborhood", "city", "state", "is_primary")
    )
    contacts = list(
        PersonContactSnapshot.objects.filter(person=person)
        .order_by("-is_primary", "contact_type", "legacy_id")
        .values("legacy_id", "contact_type", "value", "is_primary")
    )
    profiles = list(
        PersonProfileSnapshot.objects.filter(person=person)
        .order_by("-is_active", "profile", "legacy_id")
        .values("legacy_id", "profile", "is_active", "start_date_raw", "end_date_raw")
    )
    relationships = list(
        PersonRelationshipSnapshot.objects.filter(models.Q(person=person) | models.Q(related_person=person))
        .order_by("-is_active", "relationship_type", "legacy_id")
        .values("legacy_id", "person_id", "related_person_id", "relationship_type", "notes", "is_active")
    )
    return {
        "pessoa": _person_snapshot_dict(person),
        "enderecos": addresses,
        "contatos": contacts,
        "perfis": profiles,
        "relacionamentos": relationships,
    }


def _next_trash_legacy_id() -> int:
    last = PersonSecureTrashSnapshot.objects.order_by("-legacy_id").only("legacy_id").first()
    return int((last.legacy_id if last else 0) or 0) + 1


def _next_purge_legacy_id() -> int:
    last = PersonSecurePurgeSnapshot.objects.order_by("-legacy_id").only("legacy_id").first()
    return int((last.legacy_id if last else 0) or 0) + 1


def _trash_blockers_postgres(person_id: int) -> dict[str, int]:
    person_id = int(person_id or 0)
    contributions = PersonContributionSnapshot.objects.filter(person__legacy_id=person_id, is_active=True).count()
    receipts = ReceiptSnapshot.objects.filter(person_legacy_id=person_id).count()
    statement_refs = (
        StatementImportPilotMovement.objects.filter(
            models.Q(suggested_person_legacy_id=person_id) | models.Q(resolved_person_legacy_id=person_id)
        )
        .values("id")
        .distinct()
        .count()
    )
    return {
        "contribuicoes": int(contributions or 0),
        "recibos": int(receipts or 0),
        "lancamentos_financeiros": int(statement_refs or 0),
    }


def _blocker_text(blockers: dict[str, int]) -> str:
    return ", ".join(f"{value} {label}" for label, value in blockers.items() if value)


def list_secure_people_trash_postgres(limit: int = 200) -> dict[str, object]:
    rows = list(PersonSecureTrashSnapshot.objects.order_by("-created_at", "-legacy_id")[: max(1, int(limit or 200))])
    items: list[dict[str, object]] = []
    for row in rows:
        blockers = _trash_blockers_postgres(int(row.person_legacy_id or 0))
        items.append(
            {
                "id": int(row.legacy_id or 0),
                "person_id": int(row.person_legacy_id or 0),
                "nome": row.person_name or "",
                "cpf": row.person_cpf or "",
                "motivo": row.reason or "",
                "operador": row.operator or "",
                "restaurado": "Sim" if row.restored else "Nao",
                "can_purge": (not row.restored) and not any(blockers.values()),
                "purge_blockers": _blocker_text(blockers),
                "criado_em": datetime.strftime(row.created_at, "%d/%m/%Y %H:%M") if row.created_at else "",
                "codigo_original": row.original_code or "",
                "status_original": row.original_status or "",
            }
        )
    return {"items": items, "total": len(items), "shown": len(items)}


def _secure_trash_item_or_error(trash_id: int) -> PersonSecureTrashSnapshot:
    row = PersonSecureTrashSnapshot.objects.filter(legacy_id=int(trash_id or 0)).first()
    if row is None:
        raise LegacyWriteError("Registro da lixeira nao encontrado.")
    return row


def soft_delete_person_postgres(person_id: int, reason: str, actor: str = "") -> int:
    person = _active_person_snapshot(person_id)
    reason = normalize_query(reason)
    if len(reason) < 8:
        raise LegacyWriteError("Informe uma justificativa com pelo menos 8 caracteres para excluir a ficha.")
    snapshot = _person_full_snapshot(person)
    trash_row = PersonSecureTrashSnapshot.objects.create(
        legacy_id=_next_trash_legacy_id(),
        organization_id=int(person.organization_id or 0),
        person_legacy_id=int(person.legacy_id or 0),
        person_name=person.name or "",
        person_cpf=person.cpf or "",
        original_status=person.status or "",
        original_code=person.internal_code or "",
        reason=reason,
        operator=actor,
        snapshot_data=snapshot,
        restored=False,
    )
    person.is_active = False
    person.is_archived = True
    person.notes = _append_note(person.notes or "", f"Ficha enviada para a lixeira segura. Motivo: {reason}")
    person.save(update_fields=["is_active", "is_archived", "notes", "synced_at"])
    try:
        record_django_audit_event(
            actor=actor,
            action="excluir_ficha_lixeira_segura_postgres",
            table_name="people_personsnapshot",
            record_id=int(person.pk or 0),
            organization_id=int(person.organization_id or 0),
            source="postgres_native_people",
            summary="Ficha enviada para a lixeira segura no Postgres.",
            before=snapshot,
            after={
                "ativo": False,
                "lixeira_segura_id": int(trash_row.legacy_id or 0),
                "motivo_exclusao": reason,
            },
        )
    except Exception:
        pass
    return int(trash_row.legacy_id or 0)


def purge_secure_person_trash_postgres(trash_id: int, reason: str, actor: str = "") -> int:
    row = _secure_trash_item_or_error(trash_id)
    reason = normalize_query(reason)
    if len(reason) < 8:
        raise LegacyWriteError("Informe uma justificativa com pelo menos 8 caracteres para a purga final.")
    if row.restored:
        raise LegacyWriteError("Ficha restaurada nao pode ser purgada pela lixeira.")
    person = PersonSnapshot.objects.filter(legacy_id=int(row.person_legacy_id or 0)).first()
    if person is None:
        raise LegacyWriteError("A ficha original nao foi encontrada. Revise a auditoria antes de purgar.")
    if person.is_active:
        raise LegacyWriteError("A ficha ainda esta ativa. Envie para a lixeira segura antes da purga final.")
    blockers = _trash_blockers_postgres(int(row.person_legacy_id or 0))
    blocker_detail = _blocker_text(blockers)
    if blocker_detail:
        raise LegacyWriteError(f"Purga bloqueada: existe {blocker_detail}.")
    nome_hash = hashlib.sha256(normalize_query(row.person_name or person.name).encode("utf-8")).hexdigest()
    cpf_hash = hashlib.sha256(str(row.person_cpf or person.cpf or "").encode("utf-8")).hexdigest() if (row.person_cpf or person.cpf) else ""
    tombstone = {
        "pessoa_id_original": int(row.person_legacy_id or 0),
        "lixeira_id": int(row.legacy_id or 0),
        "nome_hash": nome_hash,
        "cpf_hash": cpf_hash,
        "motivo_purga": reason,
        "operador": actor,
        "fotos_removidas": 0,
        "bloqueios_financeiros": blockers,
    }
    removed_photos = 0
    for photo_path in list_member_photo_variants(int(row.person_legacy_id or 0)):
        try:
            photo_path.unlink(missing_ok=True)
            removed_photos += 1
        except OSError:
            pass
    tombstone["fotos_removidas"] = removed_photos
    PersonContactSnapshot.objects.filter(person=person).delete()
    PersonAddressSnapshot.objects.filter(person=person).delete()
    PersonProfileSnapshot.objects.filter(person=person).delete()
    PersonRelationshipSnapshot.objects.filter(models.Q(person=person) | models.Q(related_person=person)).delete()
    PersonSecurePurgeSnapshot.objects.create(
        legacy_id=_next_purge_legacy_id(),
        organization_id=int(row.organization_id or 0),
        person_legacy_id=int(row.person_legacy_id or 0),
        trash_legacy_id=int(row.legacy_id or 0),
        name_hash=nome_hash,
        cpf_hash=cpf_hash,
        reason=reason,
        operator=actor,
        tombstone_data=tombstone,
    )
    row.delete()
    person.delete()
    try:
        record_django_audit_event(
            actor=actor,
            action="purgar_ficha_lixeira_segura_postgres",
            table_name="people_personsecurepurgesnapshot",
            record_id=int(tombstone["pessoa_id_original"]),
            organization_id=int(row.organization_id or 0),
            source="postgres_native_people",
            summary="Ficha purgada com seguranca no Postgres.",
            before={
                "lixeira_id": int(row.legacy_id or 0),
                "pessoa_id": int(row.person_legacy_id or 0),
                "nome_hash": nome_hash,
                "cpf_hash": cpf_hash,
                "cpf_presente": bool(row.person_cpf),
                "bloqueios_financeiros": blockers,
            },
            after={"purgado": True, **tombstone},
        )
    except Exception:
        pass
    return int(tombstone["pessoa_id_original"])

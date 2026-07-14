from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from django.db import transaction
from django.utils.dateparse import parse_date

from scripts.importar_membros_xlsx import (
    HEADER_TO_DESTINATION,
    canonicalize_member_row,
    desired_status_from_row,
    digits,
    mask_cpf,
    normalize_address_number,
    normalize_estado_civil,
    normalize_sex,
    parse_date_value,
    profile_from_row,
    read_xlsx,
    valid_cpf,
    yes_no,
)
from power_church_core.normalization import clean_cpf, normalize_match_name, normalize_query
from power_church_django.apps.people.models import (
    NativePeopleImportLine,
    NativePeopleImportLot,
    NativePeopleImportPending,
    PersonAddressSnapshot,
    PersonContactSnapshot,
    PersonHistorySnapshot,
    PersonProfileSnapshot,
    PersonSnapshot,
)
from power_church_django.services.django_audit import record_django_audit_event
from power_church_django.services.lot_labels import lot_public_label, month_year_from_any
from power_church_django.services.runtime_errors import LegacyWriteError


def _people_import_type_label(value: object) -> str:
    labels = {
        "pessoas_membros": "Importacao inicial de membros",
        "pessoas_complementar_incremental": "Importacao incremental de pessoas",
    }
    return labels.get(str(value or ""), str(value or "Importacao de pessoas"))


def _next_legacy_id(model) -> int:
    row = model.objects.order_by("-legacy_id").only("legacy_id").first()
    return int((row.legacy_id if row else 0) or 0) + 1


def _default_organization_id() -> int:
    row = PersonSnapshot.objects.order_by("organization_id", "legacy_id").only("organization_id").first()
    return int(row.organization_id or 1) if row else 1


def _default_unit_id() -> int | None:
    row = (
        PersonSnapshot.objects.exclude(preferred_unit_id__isnull=True)
        .order_by("preferred_unit_id", "legacy_id")
        .only("preferred_unit_id")
        .first()
    )
    return int(row.preferred_unit_id or 0) or None if row else None


def _person_by_internal_code(code: str) -> PersonSnapshot | None:
    normalized = normalize_query(code)
    if not normalized:
        return None
    return PersonSnapshot.objects.filter(internal_code=normalized, is_active=True).order_by("legacy_id").first()


def _person_by_cpf(cpf: str) -> PersonSnapshot | None:
    normalized = clean_cpf(cpf)
    if not normalized:
        return None
    return PersonSnapshot.objects.filter(cpf=normalized, is_active=True).order_by("legacy_id").first()


def _person_by_name_birth(name: str, birth_iso: str) -> PersonSnapshot | None:
    if not normalize_query(name) or not normalize_query(birth_iso):
        return None
    rows = list(
        PersonSnapshot.objects.filter(
            normalized_name=normalize_match_name(name),
            birth_date=parse_date(birth_iso),
            is_active=True,
        ).order_by("legacy_id")[:2]
    )
    return rows[0] if len(rows) == 1 else None


def _append_pending(bucket: list[dict[str, Any]], *, line_number: int, severity: str, issue_type: str, description: str, suggested_action: str, person_name: str = "") -> None:
    bucket.append(
        {
            "line_number": int(line_number or 0),
            "severity": severity,
            "issue_type": issue_type,
            "description": description,
            "suggested_action": suggested_action,
            "person_name": person_name,
        }
    )


def _has_contact(person: PersonSnapshot, contact_type: str, value: str) -> bool:
    cleaned = normalize_query(value)
    if not cleaned:
        return True
    expected = digits(cleaned) if contact_type in {"telefone", "celular"} else cleaned.casefold()
    for row in person.contacts.filter(contact_type=contact_type):
        current = digits(row.value) if contact_type in {"telefone", "celular"} else normalize_query(row.value).casefold()
        if current and current == expected:
            return True
    return False


def _add_contact_if_missing(person: PersonSnapshot, *, contact_type: str, value: str, is_primary: bool = False) -> int:
    if _has_contact(person, contact_type, value):
        return 0
    normalized_value = normalize_match_name(value) if contact_type != "email" else normalize_query(value).lower()
    PersonContactSnapshot.objects.create(
        legacy_id=_next_legacy_id(PersonContactSnapshot),
        organization_id=int(person.organization_id or _default_organization_id()),
        person=person,
        contact_type=contact_type,
        value=normalize_query(value),
        normalized_value=normalized_value,
        is_primary=is_primary,
    )
    return 1


def _add_address_if_missing(person: PersonSnapshot, row: dict[str, str]) -> int:
    if person.addresses.filter(is_primary=True).exists():
        return 0
    if not any(normalize_query(row.get(key, "")) for key in ["Endereco", "Numero", "Complemento", "Bairro", "CEP", "Cidade", "UF"]):
        return 0
    PersonAddressSnapshot.objects.create(
        legacy_id=_next_legacy_id(PersonAddressSnapshot),
        organization_id=int(person.organization_id or _default_organization_id()),
        person=person,
        address_type="residencial",
        cep=digits(row.get("CEP", "")),
        street=normalize_query(row.get("Endereco", "")),
        number=normalize_address_number(row.get("Numero", "")),
        complement=normalize_query(row.get("Complemento", "")),
        neighborhood=normalize_query(row.get("Bairro", "")),
        city=normalize_query(row.get("Cidade", "")),
        state=normalize_query(row.get("UF", "")).upper(),
        is_primary=True,
        normalized_address=normalize_match_name(
            " ".join(
                part
                for part in [
                    row.get("Endereco", ""),
                    row.get("Numero", ""),
                    row.get("Complemento", ""),
                    row.get("Bairro", ""),
                    row.get("Cidade", ""),
                    row.get("UF", ""),
                    row.get("CEP", ""),
                ]
                if normalize_query(part)
            )
        ),
    )
    return 1


def _add_profile_if_missing(person: PersonSnapshot, profile: str) -> int:
    profile = normalize_query(profile)
    if not profile:
        return 0
    if person.profiles.filter(profile=profile, is_active=True).exists():
        return 0
    PersonProfileSnapshot.objects.create(
        legacy_id=_next_legacy_id(PersonProfileSnapshot),
        organization_id=int(person.organization_id or _default_organization_id()),
        person=person,
        profile=profile,
        is_active=True,
    )
    return 1


def _add_history_if_missing(person: PersonSnapshot, *, event_type: str, event_date_raw: str, title: str, description: str) -> int:
    event_date_raw = normalize_query(event_date_raw)
    title = normalize_query(title)
    if not event_date_raw or not title:
        return 0
    if person.history_entries.filter(event_type=event_type, event_date_raw=event_date_raw, title=title).exists():
        return 0
    PersonHistorySnapshot.objects.create(
        legacy_id=_next_legacy_id(PersonHistorySnapshot),
        organization_id=int(person.organization_id or _default_organization_id()),
        person=person,
        event_type=event_type,
        event_date_raw=event_date_raw,
        title=title,
        description=normalize_query(description),
    )
    return 1


def _update_blank_person_fields(person: PersonSnapshot, row: dict[str, str], *, birth_iso: str, status: str) -> int:
    changed = 0
    updates: dict[str, Any] = {}
    if not normalize_query(person.internal_code) and normalize_query(row.get("Numero de membro", "")):
        updates["internal_code"] = normalize_query(row.get("Numero de membro", ""))
    if not normalize_query(person.name) or normalize_query(person.name) == "Nome nao informado":
        if normalize_query(row.get("Nome completo", "")):
            updates["name"] = normalize_query(row.get("Nome completo", ""))
            updates["normalized_name"] = normalize_match_name(row.get("Nome completo", ""))
    if not normalize_query(person.cpf) and clean_cpf(row.get("CPF", "")):
        updates["cpf"] = clean_cpf(row.get("CPF", ""))
    if not normalize_query(person.rg) and normalize_query(row.get("Documento de Identificacao", "")):
        updates["rg"] = normalize_query(row.get("Documento de Identificacao", ""))
    if person.birth_date is None and birth_iso:
        updates["birth_date"] = parse_date(birth_iso)
        updates["birth_date_raw"] = birth_iso
    if not normalize_query(person.sex) and normalize_sex(row.get("Sexo", "")):
        updates["sex"] = normalize_sex(row.get("Sexo", ""))
    if not normalize_query(person.marital_status) and normalize_estado_civil(row.get("Estado Civil", "")):
        updates["marital_status"] = normalize_estado_civil(row.get("Estado Civil", ""))
    if not normalize_query(person.primary_email) and normalize_query(row.get("E-Mail", "")):
        updates["primary_email"] = normalize_query(row.get("E-Mail", ""))
        updates["normalized_email"] = normalize_query(row.get("E-Mail", "")).lower()
    if not normalize_query(person.primary_phone):
        fallback_phone = normalize_query(row.get("Celular", "")) or normalize_query(row.get("Telefone", ""))
        if fallback_phone:
            updates["primary_phone"] = fallback_phone
    if not normalize_query(person.primary_whatsapp) and yes_no(row.get("WhatsApp?", "")) == "S" and normalize_query(row.get("Celular", "")):
        updates["primary_whatsapp"] = normalize_query(row.get("Celular", ""))
    if not normalize_query(person.status) and status:
        updates["status"] = status
        updates["is_archived"] = status == "arquivo_morto"
    for field, value in updates.items():
        setattr(person, field, value)
        changed += 1
    if changed:
        person.save()
    return changed


def _materialize_row_related(person: PersonSnapshot, lot_id: int, row: dict[str, str]) -> Counter:
    stats = Counter()
    profile = profile_from_row(row)
    stats["profiles_added"] += _add_profile_if_missing(person, profile)
    if yes_no(row.get("E pastor?", "")) == "S":
        stats["profiles_added"] += _add_profile_if_missing(person, "pastor")
    if yes_no(row.get("Faz parte da lideranca?", "")) == "S":
        stats["profiles_added"] += _add_profile_if_missing(person, "lider")
    stats["contacts_added"] += _add_contact_if_missing(person, contact_type="email", value=row.get("E-Mail", ""), is_primary=True)
    stats["contacts_added"] += _add_contact_if_missing(person, contact_type="telefone", value=row.get("Telefone", ""))
    stats["contacts_added"] += _add_contact_if_missing(person, contact_type="celular", value=row.get("Celular", ""), is_primary=True)
    stats["addresses_added"] += _add_address_if_missing(person, row)
    accepted_date, accepted_invalid = parse_date_value(row.get("Data que aceitou Jesus", ""))
    if accepted_date and not accepted_invalid:
        stats["history_added"] += _add_history_if_missing(
            person,
            event_type="aceitou_jesus",
            event_date_raw=accepted_date,
            title="Aceitou Jesus",
            description="Evento importado pela planilha complementar.",
        )
    entry_date, entry_invalid = parse_date_value(row.get("Data de entrada", ""))
    if entry_date and not entry_invalid:
        stats["history_added"] += _add_history_if_missing(
            person,
            event_type="entrada_membresia",
            event_date_raw=entry_date,
            title="Entrada na membresia",
            description=normalize_query(row.get("Forma de entrada", "")) or "Entrada importada pela planilha complementar.",
        )
    baptism_date, baptism_invalid = parse_date_value(row.get("Data de Batismo", ""))
    if baptism_date and not baptism_invalid:
        stats["history_added"] += _add_history_if_missing(
            person,
            event_type="batismo",
            event_date_raw=baptism_date,
            title="Batismo",
            description=normalize_query(row.get("Tipo de batismo", "")) or "Batismo importado pela planilha complementar.",
        )
    inactive_date, inactive_invalid = parse_date_value(row.get("Data de inatividade", ""))
    if inactive_date and not inactive_invalid:
        stats["history_added"] += _add_history_if_missing(
            person,
            event_type="inatividade",
            event_date_raw=inactive_date,
            title="Membro inativo",
            description=normalize_query(row.get("Motivo de inatividade", "")) or "Inatividade importada pela planilha complementar.",
        )
    if normalize_query(row.get("Data de criacao", "")) or normalize_query(row.get("Criado por", "")):
        description = " | ".join(
            part
            for part in [
                f"Data de criacao origem: {normalize_query(row.get('Data de criacao', ''))}" if normalize_query(row.get("Data de criacao", "")) else "",
                f"Criado por origem: {normalize_query(row.get('Criado por', ''))}" if normalize_query(row.get("Criado por", "")) else "",
                f"Lote nativo: {lot_id}",
            ]
            if part
        )
        stats["history_added"] += _add_history_if_missing(
            person,
            event_type="importacao_complementar",
            event_date_raw=datetime.now().date().isoformat(),
            title="Importacao complementar de pessoas",
            description=description,
        )
    return stats


def import_people_from_upload_postgres(
    filename: str,
    payload: bytes,
    allow_duplicate_file: bool = False,
) -> dict[str, object]:
    if not payload:
        raise LegacyWriteError("Selecione uma planilha Excel antes de importar pessoas.")
    if not str(filename or "").lower().endswith(".xlsx"):
        raise LegacyWriteError("Envie uma planilha Excel no formato .xlsx.")
    file_hash = __import__("hashlib").sha256(payload).hexdigest()
    if not allow_duplicate_file and NativePeopleImportLot.objects.filter(file_hash=file_hash).exists():
        existing = NativePeopleImportLot.objects.filter(file_hash=file_hash).order_by("-legacy_id").first()
        raise LegacyWriteError(f"Esta planilha ja foi importada no lote de pessoas #{int(existing.legacy_id or 0)}.")

    upload_dir = Path(__file__).resolve().parents[2] / "data" / "people_uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{Path(filename).stem}_{file_hash[:10]}.xlsx"
    stored_path = upload_dir / stored_name
    stored_path.write_bytes(payload)

    sheet_name, headers, raw_records = read_xlsx(stored_path)
    records = [canonicalize_member_row(row) for row in raw_records]
    organization_id = _default_organization_id()
    unit_id = _default_unit_id()
    lot_legacy_id = _next_legacy_id(NativePeopleImportLot)
    line_legacy_id = _next_legacy_id(NativePeopleImportLine)
    pending_legacy_id = _next_legacy_id(NativePeopleImportPending)
    mapping_rows = []
    for header in headers:
        destination = HEADER_TO_DESTINATION.get(header, "revisar")
        action = "revisar_depois" if destination == "revisar" else ("criar_campo_personalizado" if destination.startswith("campo_personalizado:") else "mapear_campo")
        mapping_rows.append(
            {
                "coluna_origem": header,
                "campo_destino": destination,
                "acao": action,
                "acao_label": "Revisar" if action == "revisar_depois" else "Mapeado",
            }
        )
    review_mappings = sum(1 for row in mapping_rows if row["acao"] == "revisar_depois")

    pending_rows_data: list[dict[str, Any]] = []
    line_rows_data: list[dict[str, Any]] = []
    created_people = 0
    updated_people = 0
    error_lines = 0
    duplicate_cpfs = {clean_cpf(row.get("CPF", "")) for row in records if clean_cpf(row.get("CPF", "")) and sum(1 for candidate in records if clean_cpf(candidate.get("CPF", "")) == clean_cpf(row.get("CPF", ""))) > 1}
    duplicate_codes = {normalize_query(row.get("Numero de membro", "")) for row in records if normalize_query(row.get("Numero de membro", "")) and sum(1 for candidate in records if normalize_query(candidate.get("Numero de membro", "")) == normalize_query(row.get("Numero de membro", ""))) > 1}

    with transaction.atomic():
        lot = NativePeopleImportLot.objects.create(
            legacy_id=lot_legacy_id,
            import_type="pessoas_complementar_incremental",
            file_name=str(filename or ""),
            file_hash=file_hash,
            status="confirmado",
            total_lines=len(records),
            created_at_display=datetime.now().strftime("%d/%m/%Y %H:%M"),
            confirmed_at_display=datetime.now().strftime("%d/%m/%Y %H:%M"),
            mapping_rows_json=mapping_rows,
            review_mappings=review_mappings,
        )

        for index, row in enumerate(records, start=1):
            line_number = int(row.get("__row_number") or index + 1)
            name = normalize_query(row.get("Nome completo", "")) or "Nome nao informado"
            cpf_digits = clean_cpf(row.get("CPF", ""))
            cpf_valid = bool(cpf_digits and valid_cpf(cpf_digits))
            cpf_match_value = cpf_digits if cpf_valid and cpf_digits not in duplicate_cpfs else ""
            if cpf_digits and not cpf_match_value:
                _append_pending(
                    pending_rows_data,
                    line_number=line_number,
                    severity="aviso",
                    issue_type="cpf_invalido_ou_duplicado",
                    description=f"CPF nao foi usado como chave automatica ({mask_cpf(cpf_digits)}).",
                    suggested_action="Conferir CPF antes de vincular manualmente.",
                    person_name=name,
                )
            birth_iso, birth_invalid = parse_date_value(row.get("Aniversario", ""))
            if birth_invalid:
                _append_pending(
                    pending_rows_data,
                    line_number=line_number,
                    severity="aviso",
                    issue_type="data_invalida",
                    description="Data de nascimento invalida.",
                    suggested_action="Revisar aniversario.",
                    person_name=name,
                )
            internal_code = normalize_query(row.get("Numero de membro", ""))
            if internal_code and internal_code in duplicate_codes:
                _append_pending(
                    pending_rows_data,
                    line_number=line_number,
                    severity="aviso",
                    issue_type="numero_membro_duplicado_no_arquivo",
                    description="Numero de membro repetido no proprio arquivo complementar.",
                    suggested_action="Conferir manualmente antes de usar como chave de atualizacao.",
                    person_name=name,
                )
                internal_code = ""

            by_cpf = _person_by_cpf(cpf_match_value) if cpf_match_value else None
            by_code = _person_by_internal_code(internal_code) if internal_code else None
            by_name_birth = None if by_cpf or by_code else _person_by_name_birth(name, birth_iso)
            candidates = {int(item.legacy_id or 0) for item in [by_cpf, by_code, by_name_birth] if item is not None}
            desired_status = desired_status_from_row(row)

            line_status = "sem_alteracao"
            normalized_action = "sem_mudancas"
            person: PersonSnapshot | None = None

            if len(candidates) > 1:
                _append_pending(
                    pending_rows_data,
                    line_number=line_number,
                    severity="aviso",
                    issue_type="conflito_chaves",
                    description="CPF, numero de membro ou nome/data apontam para pessoas diferentes.",
                    suggested_action="Resolver manualmente antes de importar esta linha.",
                    person_name=name,
                )
                line_status = "conflito"
                normalized_action = "conflito_chaves"
                error_lines += 1
            else:
                person = by_cpf or by_code or by_name_birth
                if person is None:
                    person = PersonSnapshot.objects.create(
                        legacy_id=_next_legacy_id(PersonSnapshot),
                        organization_id=organization_id,
                        preferred_unit_id=unit_id,
                        internal_code=internal_code,
                        name=name,
                        normalized_name=normalize_match_name(name),
                        cpf=cpf_match_value,
                        rg=normalize_query(row.get("Documento de Identificacao", "")),
                        birth_date=parse_date(birth_iso) if birth_iso else None,
                        birth_date_raw=birth_iso,
                        sex=normalize_sex(row.get("Sexo", "")),
                        marital_status=normalize_estado_civil(row.get("Estado Civil", "")),
                        primary_email=normalize_query(row.get("E-Mail", "")),
                        normalized_email=normalize_query(row.get("E-Mail", "")).lower(),
                        primary_phone=normalize_query(row.get("Celular", "")) or normalize_query(row.get("Telefone", "")),
                        primary_whatsapp=normalize_query(row.get("Celular", "")) if yes_no(row.get("WhatsApp?", "")) == "S" else "",
                        status=desired_status,
                        is_archived=desired_status == "arquivo_morto",
                        is_active=True,
                        notes="Importado por complemento incremental nativo em Postgres.",
                        import_lot_id=lot_legacy_id,
                    )
                    created_people += 1
                    line_status = "importado"
                    normalized_action = "criado"
                else:
                    if normalize_query(person.status) and desired_status and desired_status != normalize_query(person.status):
                        _append_pending(
                            pending_rows_data,
                            line_number=line_number,
                            severity="aviso",
                            issue_type="mudanca_status_detectada",
                            description=f"Complemento sugere status '{desired_status}', mas a ficha atual esta como '{normalize_query(person.status)}'.",
                            suggested_action="Conferir promocao, inativacao ou mudanca de perfil antes de alterar automaticamente.",
                            person_name=person.name,
                        )
                    changed = _update_blank_person_fields(person, row, birth_iso=birth_iso, status=desired_status)
                    if changed:
                        updated_people += 1
                        line_status = "atualizado"
                        normalized_action = "campos_vazios_preenchidos"
                if person is not None:
                    _materialize_row_related(person, lot_legacy_id, row)

            line_rows_data.append(
                {
                    "legacy_id": line_legacy_id,
                    "line_number": line_number,
                    "status": line_status,
                    "original_name": name,
                    "normalized_action": normalized_action,
                    "person_legacy_id": int(person.legacy_id or 0) if person is not None else None,
                    "person_name": person.name if person is not None else "Sem ficha ativa",
                    "person_cpf": person.cpf if person is not None else "",
                    "person_status": person.status if person is not None else "",
                    "person_active": bool(person.is_active) if person is not None else False,
                }
            )
            line_legacy_id += 1

        status_counter = Counter(
            row["person_status"] or "sem ficha"
            for row in line_rows_data
            if row["person_legacy_id"]
        )
        status_rows = [
            {"status": key, "status_raw": key, "count": int(value)}
            for key, value in sorted(status_counter.items(), key=lambda item: (-item[1], item[0]))
        ]

        NativePeopleImportPending.objects.bulk_create(
            [
                NativePeopleImportPending(
                    legacy_id=pending_legacy_id + index,
                    lot=lot,
                    line_number=int(row["line_number"]),
                    severity=str(row["severity"]),
                    issue_type=str(row["issue_type"]),
                    description=str(row["description"]),
                    suggested_action=str(row["suggested_action"]),
                    resolved=False,
                    person_name=str(row["person_name"]),
                )
                for index, row in enumerate(pending_rows_data)
            ]
        )
        NativePeopleImportLine.objects.bulk_create(
            [
                NativePeopleImportLine(
                    legacy_id=int(row["legacy_id"]),
                    lot=lot,
                    line_number=int(row["line_number"]),
                    status=str(row["status"]),
                    original_name=str(row["original_name"]),
                    normalized_action=str(row["normalized_action"]),
                    person_legacy_id=int(row["person_legacy_id"]) if row["person_legacy_id"] is not None else None,
                    person_name=str(row["person_name"]),
                    person_cpf=str(row["person_cpf"]),
                    person_status=str(row["person_status"]),
                    person_active=bool(row["person_active"]),
                )
                for row in line_rows_data
            ]
        )
        lot.imported_lines = created_people + updated_people
        lot.ignored_lines = max(len(records) - len(line_rows_data), 0)
        lot.error_lines = error_lines
        lot.open_pendencies = len(pending_rows_data)
        lot.active_people = sum(1 for row in line_rows_data if row["person_active"])
        lot.without_name = sum(1 for row in line_rows_data if normalize_query(row["person_name"]) == "Nome nao informado")
        lot.status_rows_json = status_rows
        lot.save(
            update_fields=[
                "imported_lines",
                "ignored_lines",
                "error_lines",
                "open_pendencies",
                "active_people",
                "without_name",
                "status_rows_json",
                "synced_at",
            ]
        )

    try:
        record_django_audit_event(
            actor="django:people_import_native",
            action="importar_pessoas_planilha_postgres",
            table_name="people_nativepeopleimportlot",
            record_id=int(lot_legacy_id),
            organization_id=organization_id,
            source="postgres_native_people_import",
            summary=f"Importacao de pessoas processada no Postgres no lote #{lot_legacy_id}.",
            after={
                "arquivo": filename,
                "linhas": len(records),
                "criados": created_people,
                "atualizados": updated_people,
                "pendencias": len(pending_rows_data),
                "erros": error_lines,
            },
        )
    except Exception:
        pass
    return {
        "lote_id": lot_legacy_id,
        "pendencias": len(pending_rows_data),
        "linhas_importadas": created_people + updated_people,
        "linhas_com_erro": error_lines,
    }


def people_import_dashboard_postgres(limit: int = 12) -> dict[str, Any]:
    lots = list(NativePeopleImportLot.objects.order_by("-legacy_id")[:limit])
    return {
        "total_people": PersonSnapshot.objects.filter(is_active=True).count(),
        "open_pendencies": sum(int(lot.open_pendencies or 0) for lot in lots),
        "total_lots": NativePeopleImportLot.objects.count(),
        "shown": len(lots),
        "lots": [
            {
                "id": int(lot.legacy_id or 0),
                "label": lot_public_label(
                    int(lot.legacy_id or 0),
                    month_year=month_year_from_any(lot.created_at_display, lot.confirmed_at_display),
                ),
                "type": lot.import_type or "",
                "type_label": _people_import_type_label(lot.import_type),
                "arquivo_nome": lot.file_name or "",
                "status": lot.status or "",
                "total_linhas": int(lot.total_lines or 0),
                "linhas_importadas": int(lot.imported_lines or 0),
                "linhas_ignoradas": int(lot.ignored_lines or 0),
                "linhas_com_erro": int(lot.error_lines or 0),
                "pendencias_abertas": int(lot.open_pendencies or 0),
                "pessoas_ativas": int(lot.active_people or 0),
                "pessoas_sem_nome": int(lot.without_name or 0),
                "criado_em": lot.created_at_display or "",
                "confirmado_em": lot.confirmed_at_display or "",
                "detail_url": f"/people/imports/{int(lot.legacy_id or 0)}/",
            }
            for lot in lots
        ],
    }


def get_people_import_lot_detail_postgres(
    lot_id: int,
    line_limit: int = 250,
    pending_limit: int = 200,
    pending_issue: str = "",
    pending_severity: str = "",
    pending_status: str = "",
) -> dict[str, Any] | None:
    lot = NativePeopleImportLot.objects.filter(legacy_id=int(lot_id or 0)).first()
    if lot is None:
        return None
    pending_issue = normalize_query(pending_issue)
    pending_severity = normalize_query(pending_severity)
    pending_status = normalize_query(pending_status)
    pending_qs = lot.pendings.order_by("resolved", "-severity", "line_number", "legacy_id")
    all_pending_entries = list(pending_qs)
    pending_issue_options = sorted({str(row.issue_type or "") for row in all_pending_entries if str(row.issue_type or "")})
    pending_severity_options = sorted({str(row.severity or "") for row in all_pending_entries if str(row.severity or "")})
    pending_summary_by_type = dict(
        Counter(str(row.issue_type or "sem_tipo") for row in all_pending_entries)
    )
    if pending_issue:
        pending_qs = pending_qs.filter(issue_type=pending_issue)
    if pending_severity:
        pending_qs = pending_qs.filter(severity=pending_severity)
    if pending_status == "abertas":
        pending_qs = pending_qs.filter(resolved=False)
    elif pending_status == "resolvidas":
        pending_qs = pending_qs.filter(resolved=True)
    pending_entries = list(pending_qs if int(pending_limit or 0) <= 0 else pending_qs[: int(pending_limit or 0)])
    pending_line_numbers = [int(row.line_number or 0) for row in pending_entries if int(row.line_number or 0) > 0]
    pending_lines = list(
        lot.lines.filter(line_number__in=pending_line_numbers).order_by("line_number", "legacy_id")
    )
    pending_line_map = {int(row.line_number or 0): row for row in pending_lines}
    pending_name_lists_by_type: dict[str, dict[str, Any]] = {}
    for row in pending_entries:
        line_row = pending_line_map.get(int(row.line_number or 0))
        person_id = int(line_row.person_legacy_id or 0) if line_row else 0
        person_name = (line_row.person_name if line_row else "") or row.person_name or (line_row.original_name if line_row else "") or "Sem ficha vinculada"
        issue_type = str(row.issue_type or "sem_tipo")
        bucket = pending_name_lists_by_type.setdefault(
            issue_type,
            {"tipo": issue_type, "nomes": [], "person_ids": set()},
        )
        if person_id:
            if person_id in bucket["person_ids"]:
                continue
            bucket["person_ids"].add(person_id)
        if person_name not in bucket["nomes"]:
            bucket["nomes"].append(person_name)
    pending_name_lists = []
    for item in pending_name_lists_by_type.values():
        names = list(item["nomes"])
        pending_name_lists.append(
            {
                "tipo": item["tipo"],
                "total": len(names),
                "nomes": names,
                "texto": "\n".join(names),
            }
        )
    pending_name_lists.sort(key=lambda item: (0 if item["tipo"] == pending_issue else 1, item["tipo"]))
    line_rows = list(lot.lines.order_by("line_number", "legacy_id")[:line_limit]) if int(line_limit or 0) > 0 else []
    return {
        "lot": {
            "id": int(lot.legacy_id or 0),
            "label": lot_public_label(
                int(lot.legacy_id or 0),
                month_year=month_year_from_any(lot.created_at_display, lot.confirmed_at_display),
            ),
            "type": lot.import_type or "",
            "type_label": _people_import_type_label(lot.import_type),
            "arquivo_nome": lot.file_name or "",
            "status": lot.status or "",
            "total_linhas": int(lot.total_lines or 0),
            "linhas_importadas": int(lot.imported_lines or 0),
            "linhas_ignoradas": int(lot.ignored_lines or 0),
            "linhas_com_erro": int(lot.error_lines or 0),
            "pendencias_abertas": int(lot.open_pendencies or 0),
            "pessoas_ativas": int(lot.active_people or 0),
            "pessoas_sem_nome": int(lot.without_name or 0),
            "criado_em": lot.created_at_display or "",
            "confirmado_em": lot.confirmed_at_display or "",
            "detail_url": f"/people/imports/{int(lot.legacy_id or 0)}/",
        },
        "cards": {
            "total_lines": int(lot.total_lines or 0),
            "active_people": int(lot.active_people or 0),
            "open_pendencies": int(lot.open_pendencies or 0),
            "without_name": int(lot.without_name or 0),
            "review_mappings": int(lot.review_mappings or 0),
        },
        "status_rows": list(lot.status_rows_json or []),
        "mapping_rows": list(lot.mapping_rows_json or []),
        "pending_filters": {
            "issue": pending_issue,
            "severity": pending_severity,
            "status": pending_status or "abertas",
            "issue_options": pending_issue_options,
            "severity_options": pending_severity_options,
            "summary_by_type": pending_summary_by_type,
            "shown": len(pending_entries),
            "total": len(all_pending_entries),
            "truncated": bool(int(pending_limit or 0) > 0 and len(all_pending_entries) > len(pending_entries)),
        },
        "pending_rows": [
            {
                "id": int(row.legacy_id or 0),
                "linha": row.line_number or "",
                "severidade": row.severity or "",
                "tipo": row.issue_type or "",
                "descricao": row.description or "",
                "acao_sugerida": row.suggested_action or "",
                "resolvido": bool(row.resolved),
                "status": "Resolvida" if row.resolved else "Aberta",
                "pessoa_nome": row.person_name or "",
                "person_id": (
                    int(pending_line_map.get(int(row.line_number or 0)).person_legacy_id or 0)
                    if pending_line_map.get(int(row.line_number or 0))
                    else ""
                ),
                "person_name": (
                    pending_line_map.get(int(row.line_number or 0)).person_name or row.person_name or "Sem ficha vinculada"
                    if pending_line_map.get(int(row.line_number or 0))
                    else (row.person_name or "Sem ficha vinculada")
                ),
                "person_cpf": (
                    pending_line_map.get(int(row.line_number or 0)).person_cpf or ""
                    if pending_line_map.get(int(row.line_number or 0))
                    else ""
                ),
                "person_status": (
                    pending_line_map.get(int(row.line_number or 0)).person_status or ""
                    if pending_line_map.get(int(row.line_number or 0))
                    else ""
                ),
                "person_active": bool(
                    pending_line_map.get(int(row.line_number or 0)).person_active
                    if pending_line_map.get(int(row.line_number or 0))
                    else False
                ),
                "original_name": (
                    pending_line_map.get(int(row.line_number or 0)).original_name or ""
                    if pending_line_map.get(int(row.line_number or 0))
                    else ""
                ),
            }
            for row in pending_entries
        ],
        "pending_name_lists": pending_name_lists,
        "line_rows": [
            {
                "id": int(row.legacy_id or 0),
                "linha": row.line_number or "",
                "status": row.status or "",
                "original_name": row.original_name or "-",
                "normalized_action": row.normalized_action or "-",
                "person_id": int(row.person_legacy_id or 0) or "",
                "person_name": row.person_name or "Sem ficha ativa",
                "person_cpf": row.person_cpf or "",
                "person_status": row.person_status or "",
                "person_active": bool(row.person_active),
            }
            for row in line_rows
        ],
        "line_limit": line_limit,
    }

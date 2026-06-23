from __future__ import annotations

from collections import Counter, defaultdict
from difflib import SequenceMatcher
from typing import Any

from django.db.models import Q
from django.utils import timezone

from power_church_core.normalization import document_query_matches, format_cpf, moneyless_int, normalize_match_name, normalize_query
from power_church_django.apps.contributions.models import NativeAuxContributor, NativeContribution
from power_church_django.apps.people.models import (
    PersonAddressSnapshot,
    PersonContactSnapshot,
    PersonContributorSnapshot,
    PersonRelationshipSnapshot,
    PersonSnapshot,
)
from power_church_django.services.contributor_runtime_support import (
    _format_contribution_row,
    _format_dashboard_contributor,
    build_contributor_family_groups,
    build_contributor_family_links,
    clean_digits,
    contributor_family_keys,
)
from power_church_django.services.django_audit import record_django_audit_event
from power_church_django.services.runtime_formatting import _money, format_status, status_sigla


def _contributor_queryset():
    return NativeAuxContributor.objects.filter(is_active=True).order_by("name", "id")


def _person_rows_for_links() -> list[dict[str, Any]]:
    return [
        {
            "id": int(row.legacy_id or 0),
            "nome": row.name or "",
            "status": row.status or "",
            "codigo_interno": row.internal_code or "",
            "cpf": row.cpf or "",
        }
        for row in PersonSnapshot.objects.filter(is_active=True).only("legacy_id", "name", "status", "internal_code", "cpf")
    ]


def list_contributors_postgres(
    q: str = "",
    status: str = "",
    tipo: str = "",
    mode: str = "todos",
    tags: list[str] | tuple[str, ...] | set[str] | None = None,
    section: str = "",
    limit: int | None = None,
) -> dict[str, Any]:
    q = normalize_query(q)
    status = normalize_query(status)
    tipo = normalize_query(tipo)
    mode = normalize_query(mode) or "todos"
    section = normalize_query(section)
    tag_set = {normalize_query(item).lower() for item in (tags or []) if normalize_query(item)}

    contributors = list(_contributor_queryset())
    aux_ids = [int(item.id or 0) for item in contributors]
    person_index = {
        int(row.legacy_id or 0): row
        for row in PersonSnapshot.objects.filter(is_active=True).only("legacy_id", "name", "status", "internal_code", "cpf")
    }
    contributions = list(
        NativeContribution.objects.filter(is_active=True, native_aux_contributor_id__in=aux_ids).order_by("received_at_raw", "legacy_id")
    )
    stats: dict[int, dict[str, Any]] = {}
    for row in contributions:
        aux_id = int(row.native_aux_contributor_id or 0)
        if not aux_id:
            continue
        bucket = stats.setdefault(
            aux_id,
            {
                "contribuicoes_qtd": 0,
                "total_contribuido": 0.0,
                "primeira_contribuicao": "",
                "ultima_contribuicao": "",
                "competencias_set": set(),
                "semanas_set": set(),
                "meses_set": set(),
                "contribuicoes_sem_pessoa": 0,
            },
        )
        bucket["contribuicoes_qtd"] += 1
        bucket["total_contribuido"] += float(row.amount or 0)
        if row.received_at_raw and (not bucket["primeira_contribuicao"] or row.received_at_raw < bucket["primeira_contribuicao"]):
            bucket["primeira_contribuicao"] = row.received_at_raw
        if row.received_at_raw and (not bucket["ultima_contribuicao"] or row.received_at_raw > bucket["ultima_contribuicao"]):
            bucket["ultima_contribuicao"] = row.received_at_raw
        if row.competence:
            bucket["competencias_set"].add(row.competence)
        if row.received_at_raw:
            bucket["meses_set"].add(str(row.received_at_raw)[:7])
            if len(str(row.received_at_raw)) >= 10:
                bucket["semanas_set"].add(str(row.received_at_raw)[:7])
        if not int(row.person_legacy_id or 0):
            bucket["contribuicoes_sem_pessoa"] += 1

    all_items = []
    for row in contributors:
        stat = stats.get(int(row.id or 0), {})
        payload = {
            "id": int(row.legacy_reference_id or row.id or 0),
            "nome": row.name or "",
            "documento_principal": row.primary_document or "",
            "documento_tipo": row.document_type or "",
            "tipo": row.contributor_type or "",
            "status": row.status or "",
            "origem": row.origin or "",
            "qualidade": row.quality or "",
            "pessoa_id": int(row.person_legacy_id or 0),
            "pessoa_nome": (person_index.get(int(row.person_legacy_id or 0)).name if person_index.get(int(row.person_legacy_id or 0)) else ""),
            "pessoa_status": (person_index.get(int(row.person_legacy_id or 0)).status if person_index.get(int(row.person_legacy_id or 0)) else ""),
            "contribuicoes_qtd": int(stat.get("contribuicoes_qtd") or 0),
            "total_contribuido": float(stat.get("total_contribuido") or 0),
            "primeira_contribuicao": stat.get("primeira_contribuicao") or "",
            "ultima_contribuicao": stat.get("ultima_contribuicao") or "",
            "competencias_qtd": len(stat.get("competencias_set") or []),
            "semanas_qtd": len(stat.get("semanas_set") or []),
            "meses_recebimento_qtd": len(stat.get("meses_set") or []),
            "contribuicoes_sem_pessoa": int(stat.get("contribuicoes_sem_pessoa") or 0),
            "pix_pendentes": 0,
            "pix_pendentes_pessoa": 0,
            "pix_pendentes_destinacao": 0,
            "pix_pendentes_duplicidade": 0,
            "pendencias_total": int(stat.get("contribuicoes_sem_pessoa") or 0),
            "identificadores_texto": row.primary_document or "",
        }
        all_items.append(_format_dashboard_contributor(payload))

    def matches_query(item: dict[str, Any]) -> bool:
        if not q:
            return True
        query_lower = q.lower()
        if q.isdigit() and moneyless_int(q) == moneyless_int(item.get("id")):
            return True
        text_candidates = [item.get("nome"), item.get("documento_principal"), item.get("pessoa_nome"), item.get("identificadores_texto")]
        if any(query_lower in normalize_query(value).lower() for value in text_candidates if value):
            return True
        if document_query_matches(q, item.get("documento_principal")):
            return True
        return False

    filtered = []
    for item in all_items:
        if status and normalize_query(item["status"]) != status:
            continue
        if tipo and normalize_query(item["tipo"]) != tipo:
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

    filtered.sort(key=lambda item: (0 if item.get("group_kind") == "nome" else 1, str(item.get("sort_key") or normalize_match_name(item["nome"])), moneyless_int(item["id"]), str(item["nome"]).casefold()))
    limit_value = moneyless_int(limit) if limit is not None else 0
    limited_items = filtered[:limit_value] if limit_value > 0 else list(filtered)
    people_rows = _person_rows_for_links()
    family_groups = build_contributor_family_groups(filtered)
    family_links = build_contributor_family_links(filtered, people_rows)
    status_counter = Counter(normalize_query(item.status) for item in contributors)
    type_counter = Counter(normalize_query(item.contributor_type) for item in contributors)
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
        "tag_options": [
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
        ],
        "status_options": [{"value": key, "count": value} for key, value in sorted(status_counter.items(), key=lambda item: (-item[1], item[0]))],
        "type_options": [{"value": key, "count": value} for key, value in sorted(type_counter.items(), key=lambda item: (-item[1], item[0]))],
        "family_groups": family_groups,
        "family_links": family_links,
        "family_links_smart_summary": [],
        "summary": {
            "total": len(all_items),
            "linked": sum(1 for item in all_items if moneyless_int(item["pessoa_id"])),
            "pf": sum(1 for item in all_items if item["tipo"] == "pf"),
            "pj": sum(1 for item in all_items if item["tipo"] == "pj"),
            "pending_contributors": sum(1 for item in all_items if moneyless_int(item["pendencias_total"]) > 0),
            "pending_unlaunched": 0,
            "pending_without_person": sum(1 for item in all_items if moneyless_int(item["contribuicoes_sem_pessoa"]) > 0),
            "recurring_unlinked": sum(1 for item in all_items if moneyless_int(item["sugestao_integracao"]) > 0),
            "family_links": len(family_links),
            "family_groups": len(family_groups),
        },
        "limit": limit_value or len(filtered),
    }


def _resolve_aux_contributor(contributor_id: int) -> NativeAuxContributor | None:
    contributor_id = int(contributor_id or 0)
    if contributor_id <= 0:
        return None
    row = NativeAuxContributor.objects.filter(legacy_reference_id=contributor_id, is_active=True).first()
    if row is not None:
        return row
    return NativeAuxContributor.objects.filter(pk=contributor_id, is_active=True).first()


def contributor_possible_people_postgres(contributor_id: int, limit: int = 12) -> list[dict[str, Any]]:
    contributor = _resolve_aux_contributor(contributor_id)
    if contributor is None:
        return []
    doc_digits = clean_digits(contributor.primary_document)
    contributor_norm = normalize_match_name(contributor.name)
    rows = []
    for person in PersonSnapshot.objects.filter(is_active=True).only("legacy_id", "name", "status", "internal_code", "cpf"):
        person_norm = normalize_match_name(person.name)
        exact_name = bool(contributor_norm and contributor_norm == person_norm)
        doc_match = bool(doc_digits and clean_digits(person.cpf) == doc_digits)
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
                "id": int(person.legacy_id or 0),
                "nome": person.name or "",
                "status": person.status or "",
                "status_label": format_status(person.status),
                "sigla": status_sigla(person.status, True),
                "codigo_interno": person.internal_code or "",
                "cpf": format_cpf(person.cpf),
                "score": round(score, 4),
                "reason": reason,
            }
        )
    rows.sort(key=lambda item: (-float(item["score"]), str(item["nome"])))
    return rows[:limit]


def get_contributor_detail_postgres(contributor_id: int) -> dict[str, Any] | None:
    contributor = _resolve_aux_contributor(contributor_id)
    if contributor is None:
        return None
    contributions = list(
        NativeContribution.objects.filter(
            is_active=True,
        ).filter(
            Q(native_aux_contributor_id=int(contributor.id or 0))
            | Q(contributor_legacy_id=int(contributor.legacy_reference_id or 0))
        ).order_by("-competence_order", "-received_at_raw", "-legacy_id")[:60]
    )
    summary_map: dict[str, dict[str, Any]] = {}
    total_value = 0.0
    person_name_index = {
        int(row.legacy_id or 0): row.name or ""
        for row in PersonSnapshot.objects.filter(is_active=True).only("legacy_id", "name")
    }
    for row in contributions:
        total_value += float(row.amount or 0)
        key = row.competence or "Sem competencia"
        bucket = summary_map.setdefault(key, {"competencia": key, "remessas": 0, "total": 0.0, "ordem": int(row.competence_order or 0)})
        bucket["remessas"] += 1
        bucket["total"] += float(row.amount or 0)
    person = PersonSnapshot.objects.filter(legacy_id=int(contributor.person_legacy_id or 0)).first()
    return {
        "contributor": {
            "id": int(contributor.legacy_reference_id or contributor.id or 0),
            "person_id": int(contributor.person_legacy_id or 0),
            "tipo": (contributor.contributor_type or "").upper(),
            "nome": contributor.name or "",
            "documento": contributor.primary_document or "",
            "documento_tipo": contributor.document_type or "",
            "origem": contributor.origin or "",
            "qualidade": contributor.quality or "",
            "status": contributor.status or "",
            "observacoes": contributor.notes or "",
            "criado_em": timezone.localtime(contributor.created_at).strftime("%d/%m/%Y %H:%M") if contributor.created_at else "",
            "atualizado_em": timezone.localtime(contributor.updated_at).strftime("%d/%m/%Y %H:%M") if contributor.updated_at else "",
            "pessoa_nome": person.name if person else "",
            "pessoa_sigla": status_sigla(person.status if person else "", bool(person)),
            "pessoa_cpf": person.cpf if person else "",
        },
        "identifiers": [{"tipo": contributor.document_type or "documento", "valor": contributor.primary_document or "", "principal": True, "observacoes": ""}] if contributor.primary_document else [],
        "possible_people": contributor_possible_people_postgres(contributor_id),
        "contributions": [
            _format_contribution_row(
                {
                    "id": int(row.legacy_id or 0),
                    "data_recebimento": row.received_at_raw or "",
                    "competencia": row.competence or "",
                    "valor": float(row.amount or 0),
                    "status_operacional": row.operational_status or "",
                    "tipo_nome": row.contribution_type_name or "",
                    "forma_nome": row.receipt_method_name or "",
                    "origem_nome": person_name_index.get(int(row.person_legacy_id or 0), ""),
                }
            )
            for row in contributions
        ],
        "summary": [
            {"competencia": item["competencia"], "remessas": int(item["remessas"]), "total_fmt": _money(item["total"])}
            for item in sorted(summary_map.values(), key=lambda item: (-int(item["ordem"]), str(item["competencia"])))
        ],
        "total_contributions_fmt": _money(total_value),
    }


def lookup_envelope_people_postgres(phone: str = "", address: str = "", limit: int = 8) -> dict[str, Any]:
    phone_digits = clean_digits(phone)
    address_query = normalize_query(address).lower()
    phone_matches: list[dict[str, Any]] = []
    address_matches: list[dict[str, Any]] = []
    seen_phone: set[int] = set()
    seen_address: set[int] = set()
    if phone_digits:
        contacts = PersonContactSnapshot.objects.filter(
            person__is_active=True,
            normalized_value__icontains=phone_digits[-8:],
        ).select_related("person").order_by("person__normalized_name", "legacy_id")[: limit * 4]
        for row in contacts:
            person = row.person
            person_id = int(person.legacy_id or 0)
            if not person_id or person_id in seen_phone:
                continue
            seen_phone.add(person_id)
            phone_matches.append(
                {
                    "nome": person.name or "",
                    "sigla": status_sigla(person.status, True),
                    "codigo": person.internal_code or "",
                    "cpf": format_cpf(person.cpf),
                    "matched_value": row.value or "",
                    "label": f"{person.name} ({person.internal_code or person.legacy_id})",
                    "source": row.contact_type or "Telefone",
                }
            )
            if len(phone_matches) >= limit:
                break
    if address_query and len(address_query) >= 10:
        addresses = PersonAddressSnapshot.objects.filter(
            person__is_active=True,
            normalized_address__icontains=address_query,
        ).select_related("person").order_by("person__normalized_name", "legacy_id")[: limit * 4]
        for row in addresses:
            person = row.person
            person_id = int(person.legacy_id or 0)
            if not person_id or person_id in seen_address:
                continue
            seen_address.add(person_id)
            matched = " | ".join(
                part for part in [row.street or "", row.number or "", row.complement or "", row.neighborhood or "", row.city or ""] if part
            )
            address_matches.append(
                {
                    "nome": person.name or "",
                    "sigla": status_sigla(person.status, True),
                    "codigo": person.internal_code or "",
                    "cpf": format_cpf(person.cpf),
                    "matched_value": matched,
                    "label": f"{person.name} ({person.internal_code or person.legacy_id})",
                    "source": "Endereco",
                }
            )
            if len(address_matches) >= limit:
                break
    return {"phone_matches": phone_matches, "address_matches": address_matches}


def update_person_email_from_manual_delivery_postgres(
    person_id: int,
    *,
    email_value: str,
    reason: str = "",
    actor: str = "",
    source: str = "",
) -> bool:
    email_value = normalize_query(email_value).lower()
    if not int(person_id or 0) or "@" not in email_value:
        return False
    person = PersonSnapshot.objects.filter(legacy_id=int(person_id or 0), is_active=True).first()
    if person is None:
        return False
    changed = False
    if person.primary_email != email_value:
        person.primary_email = email_value
        person.normalized_email = email_value
        changed = True
    if changed:
        person.save(update_fields=["primary_email", "normalized_email", "synced_at"])
        contact = PersonContactSnapshot.objects.filter(person=person, contact_type="email", is_primary=True).first()
        if contact is None:
            contact = PersonContactSnapshot(
                legacy_id=_next_person_contact_legacy_id(),
                organization_id=int(person.organization_id or 0),
                person=person,
                contact_type="email",
                is_primary=True,
            )
        contact.value = email_value
        contact.normalized_value = email_value
        contact.notes = normalize_query(reason)
        contact.save()
        try:
            record_django_audit_event(
                actor=actor,
                action="atualizar_email_pessoa_envio_manual_django",
                table_name="people_personsnapshot",
                record_id=int(person.pk or 0),
                organization_id=int(person.organization_id or 0),
                source=source or "manual_delivery",
                summary=f"E-mail da ficha atualizado para {email_value}",
                after={"person_legacy_id": int(person.legacy_id or 0), "email": email_value, "reason": reason},
            )
        except Exception:
            pass
    return changed


def _next_person_legacy_id() -> int:
    return int(PersonSnapshot.objects.order_by("-legacy_id").values_list("legacy_id", flat=True).first() or 0) + 1


def _next_person_contact_legacy_id() -> int:
    return int(PersonContactSnapshot.objects.order_by("-legacy_id").values_list("legacy_id", flat=True).first() or 0) + 1


def link_contributor_to_person_by_id_postgres(contributor_id: int, person_id: int, actor: str = "") -> bool:
    contributor = _resolve_aux_contributor(contributor_id)
    person = PersonSnapshot.objects.filter(legacy_id=int(person_id or 0), is_active=True).first()
    if contributor is None or person is None:
        return False
    contributor.person_legacy_id = int(person.legacy_id or 0)
    contributor.save(update_fields=["person_legacy_id", "updated_at"])
    NativeContribution.objects.filter(native_aux_contributor_id=int(contributor.id or 0), is_active=True).update(person_legacy_id=int(person.legacy_id or 0))
    if contributor.legacy_reference_id:
        PersonContributorSnapshot.objects.update_or_create(
            legacy_id=int(contributor.legacy_reference_id or 0),
            defaults={
                "organization_id": int(person.organization_id or contributor.organization_id or 0),
                "person": person,
                "name": contributor.name or "",
                "contributor_type": contributor.contributor_type or "",
                "primary_document": contributor.primary_document or "",
                "document_type": contributor.document_type or "",
                "origin": contributor.origin or "",
                "quality": contributor.quality or "",
                "status": contributor.status or "",
                "is_active": True,
            },
        )
    try:
        record_django_audit_event(
            actor=actor,
            action="vincular_contribuinte_auxiliar_postgres",
            table_name="contributions_nativeauxcontributor",
            record_id=int(contributor.pk or 0),
            organization_id=int(contributor.organization_id or 0),
            source="contributors_postgres",
            summary=f"Contribuinte auxiliar vinculado a {person.name}",
            after={"contributor_id": int(contributor.pk or 0), "person_legacy_id": int(person.legacy_id or 0)},
        )
    except Exception:
        pass
    return True


def create_frequentador_from_contributor_postgres(contributor_id: int, family_person_id: int = 0, actor: str = "") -> int:
    contributor = _resolve_aux_contributor(contributor_id)
    if contributor is None:
        return 0
    if int(contributor.person_legacy_id or 0):
        return int(contributor.person_legacy_id or 0)
    person = PersonSnapshot.objects.create(
        legacy_id=_next_person_legacy_id(),
        organization_id=int(contributor.organization_id or 1),
        internal_code="",
        name=contributor.name or "Frequentador sem nome",
        normalized_name=normalize_match_name(contributor.name or "Frequentador sem nome"),
        cpf=contributor.primary_document if normalize_query(contributor.document_type).lower() == "cpf" else "",
        primary_email="",
        normalized_email="",
        primary_phone="",
        primary_whatsapp="",
        status="frequentador",
        is_archived=False,
        is_active=True,
        notes=f"Criado a partir do contribuinte auxiliar #{int(contributor.legacy_reference_id or contributor.id or 0)}",
    )
    contributor.person_legacy_id = int(person.legacy_id or 0)
    contributor.save(update_fields=["person_legacy_id", "updated_at"])
    NativeContribution.objects.filter(native_aux_contributor_id=int(contributor.id or 0), is_active=True).update(person_legacy_id=int(person.legacy_id or 0))
    if contributor.legacy_reference_id:
        PersonContributorSnapshot.objects.update_or_create(
            legacy_id=int(contributor.legacy_reference_id or 0),
            defaults={
                "organization_id": int(person.organization_id or contributor.organization_id or 0),
                "person": person,
                "name": contributor.name or "",
                "contributor_type": contributor.contributor_type or "",
                "primary_document": contributor.primary_document or "",
                "document_type": contributor.document_type or "",
                "origin": contributor.origin or "",
                "quality": contributor.quality or "",
                "status": contributor.status or "",
                "is_active": True,
            },
        )
    if int(family_person_id or 0):
        related = PersonSnapshot.objects.filter(legacy_id=int(family_person_id or 0), is_active=True).first()
        if related is not None:
            next_rel_id = int(PersonRelationshipSnapshot.objects.order_by("-legacy_id").values_list("legacy_id", flat=True).first() or 0) + 1
            PersonRelationshipSnapshot.objects.create(
                legacy_id=next_rel_id,
                organization_id=int(person.organization_id or 1),
                person=person,
                related_person=related,
                relationship_type="nucleo_familiar",
                notes="Criado automaticamente a partir de contribuinte auxiliar.",
                is_active=True,
            )
    try:
        record_django_audit_event(
            actor=actor,
            action="criar_frequentador_de_contribuinte_postgres",
            table_name="people_personsnapshot",
            record_id=int(person.pk or 0),
            organization_id=int(person.organization_id or 0),
            source="contributors_postgres",
            summary=f"Frequentador criado a partir do contribuinte auxiliar {contributor.name}",
            after={"person_legacy_id": int(person.legacy_id or 0), "contributor_id": int(contributor.pk or 0)},
        )
    except Exception:
        pass
    return int(person.legacy_id or 0)

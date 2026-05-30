from __future__ import annotations

from datetime import date
from typing import Any

from django.apps import apps

from power_church_core.normalization import normalize_match_name, normalize_query, title_case_name
from power_church_django.services.django_audit import record_django_audit_event


def _profile_model():
    return apps.get_model("people", "HouseholdProfile")


def household_signature(person_ids: list[int] | tuple[int, ...]) -> str:
    ordered = sorted({int(value) for value in person_ids if int(value)})
    return ",".join(str(value) for value in ordered)


def _safe_birth_date(raw_value: object) -> date | None:
    text = normalize_query(raw_value)
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _person_sort_key(person: dict[str, Any]) -> tuple[object, str, int]:
    born = _safe_birth_date(person.get("data_nascimento_raw"))
    return (born or date.max, normalize_match_name(person.get("nome")), int(person.get("id") or 0))


def _family_surname(value: object) -> str:
    particles = {"A", "AS", "DA", "DAS", "DE", "DI", "DO", "DOS", "E"}
    tokens = [token for token in normalize_match_name(value).split() if token]
    surname_tokens = [token for token in tokens[1:] if token not in particles and len(token) > 1]
    if not surname_tokens:
        surname_tokens = [token for token in tokens[1:] if len(token) > 1]
    if not surname_tokens:
        surname_tokens = tokens[-1:] if tokens else []
    if len(surname_tokens) >= 2:
        return " ".join(surname_tokens[-2:])
    return surname_tokens[0] if surname_tokens else ""


def resolve_household_head(people: list[dict[str, Any]], preferred_head_id: int = 0) -> dict[str, Any] | None:
    if not people:
        return None
    preferred = next((person for person in people if int(person.get("id") or 0) == int(preferred_head_id or 0)), None)
    if preferred:
        return preferred
    dated = [person for person in people if _safe_birth_date(person.get("data_nascimento_raw"))]
    if dated:
        return sorted(dated, key=_person_sort_key)[0]
    return sorted(people, key=_person_sort_key)[0]


def automatic_household_name(people: list[dict[str, Any]], preferred_head_id: int = 0) -> str:
    head = resolve_household_head(people, preferred_head_id)
    if not head:
        return "Familia domiciliar"
    head_name = title_case_name(head.get("nome") or "Cabeca da familia")
    surname = title_case_name(_family_surname(head_name)) or head_name
    return f"Familia {surname} · {head_name}"


def household_profile_context(person_ids: list[int], people: list[dict[str, Any]]) -> dict[str, Any]:
    signature = household_signature(person_ids)
    default = {
        "signature": signature,
        "head_person_id": int((resolve_household_head(people) or {}).get("id") or 0),
        "display_name_override": "",
        "display_name_auto": automatic_household_name(people),
        "display_name_effective": automatic_household_name(people),
        "display_name_sort": normalize_match_name(automatic_household_name(people)),
        "head_person_name": title_case_name((resolve_household_head(people) or {}).get("nome") or ""),
    }
    if not signature:
        return default
    try:
        profile = _profile_model().objects.filter(signature=signature).first()
    except Exception:
        return default
    head_person_id = int(getattr(profile, "head_person_id", 0) or 0) if profile else default["head_person_id"]
    head = resolve_household_head(people, head_person_id)
    auto_name = automatic_household_name(people, head_person_id)
    override = normalize_query(getattr(profile, "display_name_override", "") if profile else "")
    effective = override or auto_name
    return {
        "signature": signature,
        "head_person_id": int((head or {}).get("id") or 0),
        "display_name_override": override,
        "display_name_auto": auto_name,
        "display_name_effective": effective,
        "display_name_sort": normalize_match_name(effective or auto_name),
        "head_person_name": title_case_name((head or {}).get("nome") or ""),
    }


def update_household_profile(
    *,
    person_ids: list[int],
    people: list[dict[str, Any]],
    head_person_id: int = 0,
    display_name_override: str = "",
    actor: str = "",
) -> dict[str, Any]:
    signature = household_signature(person_ids)
    if not signature:
        raise ValueError("Nao foi possivel identificar o nucleo domiciliar para salvar o perfil.")
    head = resolve_household_head(people, head_person_id)
    if not head:
        raise ValueError("Nao foi possivel localizar a cabeca da familia informada.")
    model = _profile_model()
    profile = model.objects.filter(signature=signature).first()
    before = None
    if profile:
        before = {
            "head_person_id": profile.head_person_id,
            "display_name_override": profile.display_name_override,
        }
    else:
        profile = model(signature=signature)
    profile.head_person_id = int(head.get("id") or 0)
    profile.display_name_override = normalize_query(display_name_override)
    profile.save()
    context = household_profile_context(person_ids, people)
    record_django_audit_event(
        actor=actor or "django",
        action="atualizar_perfil_familia_domiciliar_django",
        table_name="people_household_profile",
        record_id=int(profile.pk or 0),
        source="household_profile",
        summary=f"Perfil do nucleo {context['display_name_effective']} atualizado.",
        before=before,
        after={
            "signature": signature,
            "head_person_id": context["head_person_id"],
            "display_name_override": context["display_name_override"],
            "display_name_auto": context["display_name_auto"],
            "display_name_effective": context["display_name_effective"],
        },
    )
    return context

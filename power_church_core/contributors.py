from __future__ import annotations

from collections.abc import Callable

from .matching import pix_name_has_company_hint
from .normalization import moneyless_int, normalize_query


def looks_like_cnpj(value: object) -> bool:
    text = normalize_query(value)
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) == 14:
        return True
    return "/" in text and "-" in text


def contributor_kind_for_identity(
    name: object,
    document_type: object = "",
    document_value: object = "",
    identifier_pairs: list[tuple[str, str]] | None = None,
    company_name_detector: Callable[[object], bool] | None = None,
) -> str:
    document_type_norm = normalize_query(document_type).lower()
    if document_type_norm.startswith("cnpj") or looks_like_cnpj(document_value):
        return "pj"
    for kind, value in identifier_pairs or []:
        kind_norm = normalize_query(kind).lower()
        if kind_norm.startswith("cnpj") or looks_like_cnpj(value):
            return "pj"
    detector = company_name_detector or pix_name_has_company_hint
    return "pj" if detector(name) else "pf"


def contributor_membership_sigla(status: object, person_id: object) -> tuple[str, str, str]:
    if moneyless_int(person_id) <= 0:
        return ("NR", "Sem vinculo", "warn")
    mapping = {
        "membro_ativo": ("SA", "Membro ativo", "ok"),
        "membro_inativo": ("SI", "Membro inativo", "warn"),
        "frequentador": ("NF", "Frequentador", "info"),
        "visitante": ("NV", "Visitante", "info"),
        "arquivo_morto": ("NM", "Arquivo morto", "danger"),
    }
    return mapping.get(normalize_query(status), ("NR", "Sem vinculo", "warn"))


def contributor_membership_legend() -> list[tuple[str, str]]:
    return [
        ("SA", "Membro ativo"),
        ("SI", "Membro inativo"),
        ("NF", "Frequentador"),
        ("NV", "Visitante"),
        ("NM", "Arquivo morto"),
        ("NR", "Sem vinculo"),
    ]

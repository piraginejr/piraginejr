from __future__ import annotations

import re
from difflib import SequenceMatcher

from .normalization import masked_document_matches, moneyless_int, normalize_match_name, normalize_query


DEFAULT_COMPANY_HINTS = (
    "LTDA",
    "ME",
    "EPP",
    "AGENCIA",
    "AGÊNCIA",
    "TREINAMENTO",
    "PUBLICIDADE",
    "SERVICOS",
    "SERVIÇOS",
    "INSTITUTO",
    "ASSOCIACAO",
    "ASSOCIAÇÃO",
    "IGREJA",
    "MINISTERIO",
    "MINISTÉRIO",
)


def active_status_allows_auto_match(status: object) -> bool:
    return normalize_query(status) in {"membro_ativo", "membro_inativo", "frequentador", "visitante"}


def derived_pix_name_aliases(value: object) -> list[str]:
    name_norm = normalize_match_name(value)
    tokens = [token for token in name_norm.split() if token]
    if len(tokens) < 3:
        return []
    variants: set[str] = set()

    def short(token: str) -> str:
        return token[:1] if token else ""

    def add_variant(parts: list[str]) -> None:
        alias = " ".join(item for item in parts if item).strip()
        if alias and alias != name_norm:
            variants.add(alias)

    for tail_size in (1, 2):
        if len(tokens) <= tail_size + 1:
            continue
        first = tokens[0]
        middle = tokens[1:-tail_size]
        tail = tokens[-tail_size:]
        if not middle:
            continue
        add_variant([first] + [short(token) for token in middle] + tail)
        if len(middle) >= 2:
            add_variant([first, middle[0]] + [short(token) for token in middle[1:]] + tail)
            add_variant([first] + [short(token) for token in middle[:-1]] + [middle[-1]] + tail)
        if len(middle) >= 3:
            add_variant([first, middle[0], middle[1]] + [short(token) for token in middle[2:]] + tail)
    return sorted(variants)


def pix_name_is_expanded_variant(donor_name: object, candidate_name: object) -> bool:
    donor_norm = normalize_match_name(donor_name)
    candidate_norm = normalize_match_name(candidate_name)
    if not donor_norm or not candidate_norm:
        return False
    if donor_norm == candidate_norm:
        return True
    if candidate_norm.startswith(donor_norm + " "):
        return True
    donor_tokens = donor_norm.split()
    candidate_tokens = candidate_norm.split()
    if len(candidate_tokens) < len(donor_tokens):
        return False
    return candidate_tokens[: len(donor_tokens)] == donor_tokens


def pix_name_has_company_hint(value: object, company_hints: tuple[str, ...] = DEFAULT_COMPANY_HINTS) -> bool:
    donor_norm = normalize_match_name(value)
    tokens = [token for token in re.split(r"[^A-Z0-9]+", donor_norm) if token]
    token_set = set(tokens)
    for hint in company_hints:
        hint_tokens = [token for token in re.split(r"[^A-Z0-9]+", normalize_match_name(hint)) if token]
        if not hint_tokens:
            continue
        if len(hint_tokens) == 1:
            if hint_tokens[0] in token_set:
                return True
            continue
        for index in range(0, max(len(tokens) - len(hint_tokens) + 1, 0)):
            if tokens[index:index + len(hint_tokens)] == hint_tokens:
                return True
    return False


def pix_origin_is_company(
    document_type: object,
    donor_name: object,
    company_hints: tuple[str, ...] = DEFAULT_COMPANY_HINTS,
) -> bool:
    return normalize_query(document_type) == "cnpj" or pix_name_has_company_hint(donor_name, company_hints)


def _match_context(person: dict[str, object], donor_norm: str, document_mask: str) -> dict[str, object]:
    aliases = list(person.get("financial_aliases") or [])
    alias_exact = (
        next(
            (
                alias
                for alias in aliases
                if str(alias.get("name_norm")) == donor_norm and str(alias.get("alias_kind") or "") != "derivado"
            ),
            None,
        )
        if donor_norm
        else None
    )
    derived_alias_exact = (
        next(
            (
                alias
                for alias in aliases
                if str(alias.get("name_norm")) == donor_norm and str(alias.get("alias_kind") or "") == "derivado"
            ),
            None,
        )
        if donor_norm
        else None
    )
    person_exact = bool(donor_norm and str(person.get("name_norm", "")) == donor_norm)
    best_ratio = SequenceMatcher(None, donor_norm, str(person.get("name_norm", ""))).ratio() if donor_norm else 0.0
    best_alias = None
    for alias in aliases:
        alias_ratio = SequenceMatcher(None, donor_norm, str(alias.get("name_norm", ""))).ratio() if donor_norm else 0.0
        if alias_ratio > best_ratio:
            best_ratio = alias_ratio
            best_alias = alias
    matched_identifiers = [
        item
        for item in list(person.get("identifiers") or [])
        if document_mask and masked_document_matches(document_mask, item.get("value"))
    ]
    return {
        "person_exact": person_exact,
        "alias_exact": alias_exact,
        "derived_alias_exact": derived_alias_exact,
        "best_ratio": best_ratio,
        "best_alias": best_alias,
        "matched_identifiers": matched_identifiers,
    }


def match_pix_entry(
    donor_name: str,
    document_mask: str,
    document_type: str,
    people_cache: list[dict[str, object]],
    company_hints: tuple[str, ...] = DEFAULT_COMPANY_HINTS,
) -> dict[str, object]:
    donor_norm = normalize_match_name(donor_name)
    context_map = {moneyless_int(person["id"]): _match_context(person, donor_norm, document_mask) for person in people_cache}
    exact_name = (
        [
            person
            for person in people_cache
            if context_map[moneyless_int(person["id"])]["person_exact"]
            or context_map[moneyless_int(person["id"])]["alias_exact"]
        ]
        if donor_norm
        else []
    )
    derived_exact_name = (
        [person for person in people_cache if context_map[moneyless_int(person["id"])]["derived_alias_exact"]]
        if donor_norm
        else []
    )
    doc_matches: list[dict[str, object]] = []
    if document_mask:
        for person in people_cache:
            if context_map[moneyless_int(person["id"])]["matched_identifiers"]:
                doc_matches.append(person)
    confidence = "sem_match"
    score = 0.0
    chosen: dict[str, object] | None = None
    notes = ""
    if len(doc_matches) == 1:
        chosen = doc_matches[0]
        chosen_context = context_map[moneyless_int(chosen["id"])]
        if exact_name and any(moneyless_int(person["id"]) == moneyless_int(chosen["id"]) for person in exact_name):
            confidence = "forte_doc_nome"
            score = 0.99
            if chosen_context["alias_exact"]:
                alias = chosen_context["alias_exact"]
                notes = f"Documento mascarado unico e nome financeiro associado '{alias['name']}' confirmou a ficha."
        elif len(exact_name) == 1 and pix_name_is_expanded_variant(donor_norm, chosen.get("name_norm")):
            confidence = "forte_doc_nome"
            score = 0.97
            notes = "Documento mascarado unico e nome do cadastro representa uma variante expandida do nome bancario."
        elif chosen_context["best_alias"] and float(chosen_context["best_ratio"]) >= 0.97:
            confidence = "forte_doc_nome"
            score = 0.97
            alias = chosen_context["best_alias"]
            notes = f"Documento mascarado unico e nome financeiro associado muito proximo de '{alias['name']}'."
        elif len(derived_exact_name) == 1 and any(
            moneyless_int(person["id"]) == moneyless_int(chosen["id"]) for person in derived_exact_name
        ):
            confidence = "forte_doc_nome"
            score = 0.96
            alias = chosen_context["derived_alias_exact"]
            notes = f"Documento mascarado unico e nome bancario resumido compativel com '{alias['source_name']}'."
        elif not exact_name:
            confidence = "forte_doc"
            score = 0.96
        elif len(exact_name) == 1:
            confidence = "conflito_doc_nome"
            score = 0.65
            notes = "Documento mascarado unico, mas o nome encontrado diverge da unica ficha nominal."
        else:
            confidence = "forte_doc"
            score = 0.95
    elif len(doc_matches) > 1:
        if len(exact_name) == 1 and any(
            moneyless_int(person["id"]) == moneyless_int(exact_name[0]["id"]) for person in doc_matches
        ):
            chosen = exact_name[0]
            confidence = "forte_doc_nome"
            score = 0.97
            chosen_context = context_map[moneyless_int(chosen["id"])]
            if chosen_context["alias_exact"]:
                notes = "Entre os documentos compativeis, apenas uma ficha confirmou o nome financeiro associado."
            else:
                notes = "Entre os documentos compativeis, apenas uma ficha tambem confirmou o nome bancario."
        else:
            confidence = "ambiguo"
            score = 0.45
            notes = f"Documento mascarado corresponde a {len(doc_matches)} fichas."
    elif len(exact_name) == 1:
        chosen = exact_name[0]
        confidence = "forte_nome"
        score = 0.93
        chosen_context = context_map[moneyless_int(chosen["id"])]
        if chosen_context["alias_exact"]:
            notes = f"Nome financeiro associado '{chosen_context['alias_exact']['name']}' confirmou a ficha."
    elif len(exact_name) > 1:
        confidence = "ambiguo"
        score = 0.42
        notes = f"Nome exato corresponde a {len(exact_name)} fichas."
    elif len(derived_exact_name) == 1:
        chosen = derived_exact_name[0]
        confidence = "provavel_nome"
        score = 0.91
        chosen_context = context_map[moneyless_int(chosen["id"])]
        if chosen_context["derived_alias_exact"]:
            notes = (
                "Nome bancario resumido coincide com uma variante derivada da ficha "
                f"'{chosen_context['derived_alias_exact']['source_name']}'."
            )
    elif len(derived_exact_name) > 1:
        confidence = "ambiguo"
        score = 0.40
        notes = f"Nome resumido corresponde a {len(derived_exact_name)} fichas."
    else:
        fuzzy: list[tuple[float, dict[str, object]]] = []
        for person in people_cache:
            ratio = float(context_map[moneyless_int(person["id"])]["best_ratio"])
            if ratio >= 0.93:
                fuzzy.append((ratio, person))
        fuzzy.sort(key=lambda item: (-item[0], str(item[1]["nome"])))
        if len(fuzzy) == 1:
            chosen = fuzzy[0][1]
            confidence = "provavel_nome"
            score = float(fuzzy[0][0])
            chosen_context = context_map[moneyless_int(chosen["id"])]
            if chosen_context["best_alias"] and not chosen_context["person_exact"]:
                notes = (
                    "Nome muito proximo da identidade financeira associada "
                    f"'{chosen_context['best_alias']['name']}' ({score:.3f})."
                )
            else:
                notes = f"Nome muito proximo da ficha sugerida ({score:.3f})."
    if chosen and not active_status_allows_auto_match(chosen.get("status")):
        confidence = "sem_match"
        score = 0.0
        notes = "A ficha sugerida nao esta operacional para vinculo automatico."
        chosen = None
    is_company = pix_origin_is_company(document_type, donor_name, company_hints)
    if chosen is None and confidence == "sem_match" and is_company:
        confidence = "pj_ou_externo"
        score = 0.75
        notes = "Pagador com caracteristica de PJ ou doador externo."
    return {
        "confidence": confidence,
        "score": score,
        "person_id": moneyless_int(chosen["id"]) if chosen else 0,
        "person_name": str(chosen["nome"]) if chosen else "",
        "notes": notes,
    }


def pix_candidate_suggestions(
    donor_name: str,
    document_mask: str,
    document_type: str,
    people_cache: list[dict[str, object]],
    limit: int = 12,
) -> list[dict[str, object]]:
    donor_norm = normalize_match_name(donor_name)
    suggestions: list[dict[str, object]] = []
    for person in people_cache:
        identifiers = list(person.get("identifiers") or [])
        matched_identifiers = [
            item for item in identifiers if document_mask and masked_document_matches(document_mask, item.get("value"))
        ]
        aliases = list(person.get("financial_aliases") or [])
        alias_exact = next((alias for alias in aliases if donor_norm and str(alias.get("name_norm", "")) == donor_norm), None)
        alias_best = None
        alias_ratio = 0.0
        for alias in aliases:
            current_ratio = (
                SequenceMatcher(None, donor_norm, str(alias.get("name_norm", ""))).ratio() if donor_norm else 0.0
            )
            if current_ratio > alias_ratio:
                alias_ratio = current_ratio
                alias_best = alias
        doc_match = bool(matched_identifiers)
        exact_name = bool(donor_norm) and str(person.get("name_norm", "")) == donor_norm
        ratio = SequenceMatcher(None, donor_norm, str(person.get("name_norm", ""))).ratio() if donor_norm else 0.0
        best_ratio = max(ratio, alias_ratio)
        if not doc_match and not exact_name and not alias_exact and best_ratio < 0.86:
            continue
        score = 0.0
        reasons: list[str] = []
        if doc_match:
            score += 70
            identity_bits = []
            first_identifier = matched_identifiers[0]
            if first_identifier.get("source_name"):
                identity_bits.append(str(first_identifier["source_name"]))
            if first_identifier.get("kind") or first_identifier.get("value"):
                identity_bits.append(f"{first_identifier.get('kind')}: {first_identifier.get('value')}")
            reasons.append(
                "documento mascarado compativel" + (f" via {' | '.join(identity_bits)}" if identity_bits else "")
            )
        if exact_name:
            score += 24
            reasons.append("nome exato")
        elif alias_exact:
            score += 24
            if str(alias_exact.get("alias_kind") or "") == "derivado":
                reasons.append(f"nome bancario resumido compativel ({alias_exact['name']})")
            else:
                reasons.append(f"nome financeiro associado exato ({alias_exact['name']})")
        elif best_ratio >= 0.97:
            score += 18
            if alias_best and alias_ratio >= ratio:
                reasons.append(f"nome financeiro muito proximo ({alias_best['name']} | {best_ratio:.2f})")
            else:
                reasons.append(f"nome muito proximo ({best_ratio:.2f})")
        elif best_ratio >= 0.93:
            score += 12
            if alias_best and alias_ratio >= ratio:
                reasons.append(f"nome financeiro proximo ({alias_best['name']} | {best_ratio:.2f})")
            else:
                reasons.append(f"nome proximo ({best_ratio:.2f})")
        elif best_ratio >= 0.88:
            score += 6
            if alias_best and alias_ratio >= ratio:
                reasons.append(f"nome financeiro parcial ({alias_best['name']} | {best_ratio:.2f})")
            else:
                reasons.append(f"nome parcial ({best_ratio:.2f})")
        if document_type == "cnpj":
            score += 1
        if not active_status_allows_auto_match(person.get("status")):
            score -= 10
            reasons.append("status exige revisao manual")
        if score <= 0:
            continue
        suggestions.append(
            {
                "id": moneyless_int(person["id"]),
                "nome": str(person["nome"]),
                "status": str(person["status"]),
                "score": round(score, 2),
                "doc_match": doc_match,
                "exact_name": exact_name or bool(alias_exact),
                "fuzzy_ratio": round(best_ratio, 4),
                "matched_identity_kind": str(matched_identifiers[0].get("kind", "")) if matched_identifiers else "",
                "matched_identity_value": str(matched_identifiers[0].get("value", "")) if matched_identifiers else "",
                "matched_identity_source": str(matched_identifiers[0].get("source_name", "")) if matched_identifiers else "",
                "matched_alias_name": str(
                    alias_exact["name"]
                    if alias_exact
                    else (alias_best["name"] if alias_best and alias_ratio >= ratio and best_ratio >= 0.86 else "")
                ),
                "reason": ", ".join(reasons) or "candidato relevante",
            }
        )
    suggestions.sort(
        key=lambda item: (
            -int(bool(item["doc_match"])),
            -int(bool(item["exact_name"])),
            -float(item["score"]),
            -float(item["fuzzy_ratio"]),
            str(item["nome"]),
        )
    )
    return suggestions[:limit]

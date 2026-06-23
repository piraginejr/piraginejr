from __future__ import annotations

from collections import defaultdict
from typing import Any

from power_church_core.formatting import br_date
from power_church_core.normalization import contribution_report_identity, format_cpf, moneyless_int, normalize_match_name
from power_church_django.services.runtime_formatting import _money, format_status, status_sigla
from power_church_django.services.smart_audit import classify_contributor_link_block


def clean_digits(value: object) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


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


def _format_contribution_row(row: dict[str, Any]) -> dict[str, Any]:
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

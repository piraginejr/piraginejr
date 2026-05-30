from __future__ import annotations

from datetime import datetime
from typing import Any

from django.http import HttpResponse
from import_export.formats import base_formats
from tablib import Dataset

from power_church_core.formatting import br_date, br_datetime
from power_church_core.normalization import format_cpf, moneyless_int, normalize_match_name
from power_church_django.services.legacy import (
    connect_legacy,
    format_status,
    organized_family_nuclei,
    status_sigla,
)


EXPORT_FORMATS = {
    "csv": base_formats.CSV,
    "xlsx": base_formats.XLSX,
}

PEOPLE_EXPORT_GROUPS = [
    ("cadastro", "Dados cadastrais"),
    ("contato", "Contato"),
    ("endereco", "Endereco"),
    ("familia", "Familia e votacao"),
    ("financeiro", "Financeiro"),
    ("historico", "Historico e auditoria"),
]

PEOPLE_EXPORT_FIELDS = [
    {"key": "id", "label": "ID", "group": "cadastro"},
    {"key": "codigo", "label": "Codigo", "group": "cadastro"},
    {"key": "nome", "label": "Nome", "group": "cadastro"},
    {"key": "nome_social", "label": "Nome social", "group": "cadastro"},
    {"key": "cpf", "label": "CPF", "group": "cadastro"},
    {"key": "rg", "label": "RG", "group": "cadastro"},
    {"key": "data_nascimento", "label": "Data nascimento", "group": "cadastro"},
    {"key": "sexo", "label": "Sexo", "group": "cadastro"},
    {"key": "estado_civil", "label": "Estado civil", "group": "cadastro"},
    {"key": "status", "label": "Status", "group": "cadastro"},
    {"key": "sigla", "label": "Sigla", "group": "cadastro"},
    {"key": "ativo", "label": "Ativo", "group": "cadastro"},
    {"key": "email", "label": "Email", "group": "contato"},
    {"key": "telefone", "label": "Telefone", "group": "contato"},
    {"key": "whatsapp", "label": "WhatsApp", "group": "contato"},
    {"key": "endereco_tipo", "label": "Tipo endereco", "group": "endereco"},
    {"key": "endereco_completo", "label": "Endereco completo", "group": "endereco"},
    {"key": "cep", "label": "CEP", "group": "endereco"},
    {"key": "logradouro", "label": "Logradouro", "group": "endereco"},
    {"key": "numero", "label": "Numero", "group": "endereco"},
    {"key": "complemento", "label": "Complemento", "group": "endereco"},
    {"key": "bairro", "label": "Bairro", "group": "endereco"},
    {"key": "cidade", "label": "Cidade", "group": "endereco"},
    {"key": "uf", "label": "UF", "group": "endereco"},
    {"key": "tem_familia_domiciliar", "label": "Tem familia domiciliar", "group": "familia"},
    {"key": "familia_domiciliar", "label": "Familia domiciliar", "group": "familia"},
    {"key": "familia_sobrenome", "label": "Familia sobrenome", "group": "familia"},
    {"key": "familia_qtd_membros", "label": "Familia qtd membros", "group": "familia"},
    {"key": "familia_membros", "label": "Familia membros", "group": "familia"},
    {"key": "familia_alinhamento", "label": "Familia alinhamento", "group": "familia"},
    {"key": "familia_precisa_auditoria", "label": "Familia precisa auditoria", "group": "familia"},
    {"key": "familia_tem_contribuinte", "label": "Familia tem contribuinte", "group": "familia"},
    {"key": "familia_resumo_financeiro", "label": "Familia resumo financeiro", "group": "familia"},
    {"key": "contribuintes_vinculados", "label": "Contribuintes vinculados", "group": "financeiro"},
    {"key": "nomes_contribuintes_vinculados", "label": "Nomes contribuintes vinculados", "group": "financeiro"},
    {"key": "contribuicoes_qtd", "label": "Contribuicoes qtd", "group": "financeiro"},
    {"key": "contribuicoes_total", "label": "Contribuicoes total", "group": "financeiro"},
    {"key": "primeira_contribuicao_data", "label": "Primeira contribuicao", "group": "financeiro"},
    {"key": "ultima_contribuicao_data", "label": "Ultima contribuicao", "group": "financeiro"},
    {"key": "ultima_competencia", "label": "Ultima competencia", "group": "financeiro"},
    {"key": "import_lote_id", "label": "Import lote ID", "group": "historico"},
    {"key": "criado_em", "label": "Criado em", "group": "historico"},
    {"key": "atualizado_em", "label": "Atualizado em", "group": "historico"},
    {"key": "observacoes", "label": "Observacoes", "group": "historico"},
]

PEOPLE_EXPORT_FIELD_MAP = {field["key"]: field for field in PEOPLE_EXPORT_FIELDS}

PEOPLE_EXPORT_PRESETS = {
    "cadastro_basico": {
        "label": "Cadastro basico",
        "description": "Identificacao principal e status cadastral.",
        "columns": ["id", "codigo", "nome", "cpf", "status", "sigla", "ativo", "telefone", "email"],
    },
    "contatos": {
        "label": "Contatos",
        "description": "Telefones, e-mail e status para comunicacao.",
        "columns": ["id", "codigo", "nome", "status", "telefone", "whatsapp", "email"],
    },
    "enderecos": {
        "label": "Enderecos",
        "description": "Endereco principal completo da ficha.",
        "columns": ["id", "codigo", "nome", "status", "endereco_tipo", "endereco_completo", "cep", "bairro", "cidade", "uf"],
    },
    "familias_votacao": {
        "label": "Familias e votacao",
        "description": "Nucleo domiciliar, membros e sinalizacao financeira da familia.",
        "columns": [
            "id",
            "codigo",
            "nome",
            "status",
            "tem_familia_domiciliar",
            "familia_domiciliar",
            "familia_sobrenome",
            "familia_qtd_membros",
            "familia_membros",
            "familia_alinhamento",
            "familia_precisa_auditoria",
            "familia_tem_contribuinte",
            "familia_resumo_financeiro",
            "contribuicoes_qtd",
            "contribuicoes_total",
            "ultima_contribuicao_data",
            "ultima_competencia",
        ],
    },
    "financeiro": {
        "label": "Financeiro",
        "description": "Vinculos financeiros e resumo de contribuicoes por pessoa.",
        "columns": [
            "id",
            "codigo",
            "nome",
            "status",
            "contribuintes_vinculados",
            "nomes_contribuintes_vinculados",
            "contribuicoes_qtd",
            "contribuicoes_total",
            "primeira_contribuicao_data",
            "ultima_contribuicao_data",
            "ultima_competencia",
        ],
    },
    "completo": {
        "label": "Completo",
        "description": "Base ampliada com cadastro, contato, endereco, familia e financeiro.",
        "columns": [field["key"] for field in PEOPLE_EXPORT_FIELDS],
    },
}

DEFAULT_PEOPLE_EXPORT_PRESET = "cadastro_basico"


def _bool_text(value: bool) -> str:
    return "Sim" if value else "Nao"


def _resolve_people_export_preset(preset: str) -> str:
    return preset if preset in PEOPLE_EXPORT_PRESETS else DEFAULT_PEOPLE_EXPORT_PRESET


def resolve_people_export_columns(columns: list[str] | tuple[str, ...] | None, preset: str = "") -> list[str]:
    unique_columns: list[str] = []
    for value in columns or []:
        if value in PEOPLE_EXPORT_FIELD_MAP and value not in unique_columns:
            unique_columns.append(value)
    if unique_columns:
        return unique_columns
    preset_key = _resolve_people_export_preset(preset)
    return list(PEOPLE_EXPORT_PRESETS[preset_key]["columns"])


def people_export_form_context(
    *,
    selected_columns: list[str] | tuple[str, ...] | None = None,
    preset: str = DEFAULT_PEOPLE_EXPORT_PRESET,
    export_format: str = "xlsx",
) -> dict[str, Any]:
    preset_key = _resolve_people_export_preset(preset)
    resolved_columns = resolve_people_export_columns(list(selected_columns or []), preset_key)
    groups: list[dict[str, Any]] = []
    for group_key, group_label in PEOPLE_EXPORT_GROUPS:
        fields = [
            {
                **field,
                "selected": field["key"] in resolved_columns,
            }
            for field in PEOPLE_EXPORT_FIELDS
            if field["group"] == group_key
        ]
        groups.append({"key": group_key, "label": group_label, "fields": fields})
    presets = [
        {
            "key": key,
            "label": item["label"],
            "description": item["description"],
            "columns_csv": ",".join(item["columns"]),
            "selected": key == preset_key,
        }
        for key, item in PEOPLE_EXPORT_PRESETS.items()
    ]
    return {
        "groups": groups,
        "presets": presets,
        "selected_preset": preset_key,
        "selected_columns": resolved_columns,
        "selected_format": export_format if export_format in EXPORT_FORMATS else "xlsx",
    }


def _people_filters(q: str = "", status: str = "", city: str = "") -> tuple[str, list[Any]]:
    q = (q or "").strip()
    status = (status or "").strip()
    city = (city or "").strip()
    clauses = ["p.ativo = 1"]
    params: list[Any] = []
    if status:
        clauses.append("COALESCE(p.status, '') = ?")
        params.append(status)
    if city:
        clauses.append(
            """
            EXISTS (
                SELECT 1
                  FROM pessoa_enderecos pe
                 WHERE pe.pessoa_id = p.id
                   AND NORMALIZE_MATCH(COALESCE(pe.cidade, '')) = ?
            )
            """
        )
        params.append(normalize_match_name(city))
    if q:
        like = f"%{q}%"
        normalized_like = f"%{normalize_match_name(q)}%"
        digits = "".join(ch for ch in q if ch.isdigit())
        clauses.append(
            """
            (
                NORMALIZE_MATCH(COALESCE(p.nome, '')) LIKE ?
                OR NORMALIZE_MATCH(COALESCE(p.nome_social, '')) LIKE ?
                OR COALESCE(p.codigo_interno, '') LIKE ?
                OR COALESCE(p.cpf, '') LIKE ?
                OR NORMALIZE_MATCH(COALESCE(p.email_principal, '')) LIKE ?
                OR COALESCE(p.telefone_principal, '') LIKE ?
            )
            """
        )
        params.extend([normalized_like, normalized_like, like, f"%{digits or q}%", normalized_like, like])
    return " AND ".join(clauses), params


def _primary_address_line(row: dict[str, Any]) -> str:
    city_uf = "/".join(part for part in [str(row.get("cidade") or "").strip(), str(row.get("uf") or "").strip()] if part)
    parts = [
        " ".join(part for part in [str(row.get("logradouro") or "").strip(), str(row.get("numero") or "").strip()] if part),
        str(row.get("complemento") or "").strip(),
        str(row.get("bairro") or "").strip(),
        city_uf,
        str(row.get("cep") or "").strip(),
    ]
    return " | ".join(part for part in parts if part)


def _people_export_rows(q: str = "", status: str = "", city: str = "") -> tuple[list[dict[str, Any]], int]:
    where, params = _people_filters(q=q, status=status, city=city)
    with connect_legacy() as conn:
        total = int(conn.execute(f"SELECT COUNT(*) FROM pessoas p WHERE {where}", tuple(params)).fetchone()[0] or 0)
        rows = conn.execute(
            f"""
            SELECT p.id, p.codigo_interno, p.nome, p.nome_social, p.cpf, p.rg,
                   p.data_nascimento, p.sexo, p.estado_civil, p.email_principal,
                   p.telefone_principal, p.whatsapp_principal, p.status,
                   p.observacoes, p.import_lote_id, p.ativo, p.criado_em, p.atualizado_em,
                   e.tipo AS endereco_tipo, e.cep, e.logradouro, e.numero, e.complemento, e.bairro, e.cidade, e.uf
              FROM pessoas p
              LEFT JOIN pessoa_enderecos e
                ON e.id = (
                    SELECT pe.id
                      FROM pessoa_enderecos pe
                     WHERE pe.pessoa_id = p.id
                     ORDER BY COALESCE(pe.principal, 0) DESC, pe.id ASC
                     LIMIT 1
                )
             WHERE {where}
             ORDER BY p.nome COLLATE NOCASE ASC, p.id ASC
            """,
            tuple(params),
        ).fetchall()
        contributor_rows = conn.execute(
            """
            SELECT pessoa_id,
                   COUNT(*) AS quantidade,
                   GROUP_CONCAT(COALESCE(nome, ''), ' / ') AS nomes
              FROM contribuintes
             WHERE ativo = 1
               AND pessoa_id IS NOT NULL
             GROUP BY pessoa_id
            """
        ).fetchall()
        contribution_rows = conn.execute(
            """
            SELECT c1.pessoa_id,
                   COUNT(*) AS quantidade,
                   COALESCE(SUM(c1.valor), 0) AS total_valor,
                   MIN(COALESCE(c1.data_recebimento, '')) AS primeira_data,
                   MAX(COALESCE(c1.data_recebimento, '')) AS ultima_data,
                   (
                       SELECT COALESCE(c2.competencia, '')
                         FROM contribuicoes c2
                        WHERE c2.ativo = 1
                          AND c2.pessoa_id = c1.pessoa_id
                        ORDER BY COALESCE(c2.competencia_ordem, 0) DESC,
                                 COALESCE(c2.data_recebimento, '') DESC,
                                 c2.id DESC
                        LIMIT 1
                   ) AS ultima_competencia
              FROM contribuicoes c1
             WHERE c1.ativo = 1
               AND c1.pessoa_id IS NOT NULL
             GROUP BY c1.pessoa_id
            """
        ).fetchall()
    contributor_index = {
        moneyless_int(row["pessoa_id"]): {
            "count": moneyless_int(row["quantidade"]),
            "names": row["nomes"] or "",
        }
        for row in contributor_rows
    }
    contribution_index = {
        moneyless_int(row["pessoa_id"]): {
            "count": moneyless_int(row["quantidade"]),
            "total": round(float(row["total_valor"] or 0), 2),
            "first_date": row["primeira_data"] or "",
            "last_date": row["ultima_data"] or "",
            "last_competencia": row["ultima_competencia"] or "",
        }
        for row in contribution_rows
    }
    family_index: dict[int, dict[str, Any]] = {}
    all_nuclei = organized_family_nuclei(q="", cep="", review="all")["items"]
    for nucleus in all_nuclei:
        family_payload = {
            "tem_familia_domiciliar": "Sim",
            "familia_domiciliar": nucleus.get("label") or "",
            "familia_sobrenome": nucleus.get("surname_label") or "",
            "familia_qtd_membros": moneyless_int(nucleus.get("member_count")),
            "familia_membros": nucleus.get("member_names") or "",
            "familia_alinhamento": nucleus.get("alignment_badge") or "",
            "familia_precisa_auditoria": _bool_text(bool(nucleus.get("needs_review"))),
            "familia_tem_contribuinte": _bool_text(bool(nucleus.get("has_financial_member"))),
            "familia_resumo_financeiro": nucleus.get("financial_summary", {}).get("note") or "",
        }
        for person in nucleus.get("people") or []:
            family_index[moneyless_int(person.get("id"))] = family_payload
    export_rows: list[dict[str, Any]] = []
    for row in rows:
        person_id = moneyless_int(row["id"])
        contributor = contributor_index.get(person_id, {})
        contribution = contribution_index.get(person_id, {})
        family = family_index.get(
            person_id,
            {
                "tem_familia_domiciliar": "Nao",
                "familia_domiciliar": "",
                "familia_sobrenome": "",
                "familia_qtd_membros": 0,
                "familia_membros": "",
                "familia_alinhamento": "",
                "familia_precisa_auditoria": "Nao",
                "familia_tem_contribuinte": "Nao",
                "familia_resumo_financeiro": "",
            },
        )
        export_rows.append(
            {
                "id": person_id,
                "codigo": row["codigo_interno"] or "",
                "nome": row["nome"] or "",
                "nome_social": row["nome_social"] or "",
                "cpf": format_cpf(row["cpf"]),
                "rg": row["rg"] or "",
                "data_nascimento": br_date(row["data_nascimento"]),
                "sexo": row["sexo"] or "",
                "estado_civil": row["estado_civil"] or "",
                "status": format_status(row["status"]),
                "sigla": status_sigla(row["status"], True),
                "ativo": _bool_text(bool(row["ativo"])),
                "email": row["email_principal"] or "",
                "telefone": row["telefone_principal"] or "",
                "whatsapp": row["whatsapp_principal"] or "",
                "endereco_tipo": row["endereco_tipo"] or "",
                "endereco_completo": _primary_address_line(dict(row)),
                "cep": row["cep"] or "",
                "logradouro": row["logradouro"] or "",
                "numero": row["numero"] or "",
                "complemento": row["complemento"] or "",
                "bairro": row["bairro"] or "",
                "cidade": row["cidade"] or "",
                "uf": row["uf"] or "",
                "tem_familia_domiciliar": family["tem_familia_domiciliar"],
                "familia_domiciliar": family["familia_domiciliar"],
                "familia_sobrenome": family["familia_sobrenome"],
                "familia_qtd_membros": family["familia_qtd_membros"],
                "familia_membros": family["familia_membros"],
                "familia_alinhamento": family["familia_alinhamento"],
                "familia_precisa_auditoria": family["familia_precisa_auditoria"],
                "familia_tem_contribuinte": family["familia_tem_contribuinte"],
                "familia_resumo_financeiro": family["familia_resumo_financeiro"],
                "contribuintes_vinculados": moneyless_int(contributor.get("count")),
                "nomes_contribuintes_vinculados": contributor.get("names", ""),
                "contribuicoes_qtd": moneyless_int(contribution.get("count")),
                "contribuicoes_total": contribution.get("total", 0.0),
                "primeira_contribuicao_data": br_date(contribution.get("first_date")),
                "ultima_contribuicao_data": br_date(contribution.get("last_date")),
                "ultima_competencia": contribution.get("last_competencia", ""),
                "import_lote_id": moneyless_int(row["import_lote_id"]),
                "criado_em": br_datetime(row["criado_em"]),
                "atualizado_em": br_datetime(row["atualizado_em"]),
                "observacoes": row["observacoes"] or "",
            }
        )
    return export_rows, total


def people_export_dataset(
    *,
    q: str = "",
    status: str = "",
    city: str = "",
    columns: list[str] | tuple[str, ...] | None = None,
    preset: str = DEFAULT_PEOPLE_EXPORT_PRESET,
) -> dict[str, Any]:
    preset_key = _resolve_people_export_preset(preset)
    selected_columns = resolve_people_export_columns(list(columns or []), preset_key)
    rows, total = _people_export_rows(q=q, status=status, city=city)
    dataset = Dataset(title="Pessoas")
    dataset.headers = [PEOPLE_EXPORT_FIELD_MAP[column]["label"] for column in selected_columns]
    for row in rows:
        dataset.append([row.get(column, "") for column in selected_columns])
    return {
        "dataset": dataset,
        "total": total,
        "shown": len(rows),
        "q": q,
        "status": status,
        "city": city,
        "preset": preset_key,
        "columns": selected_columns,
    }


def dataset_download_response(dataset: Dataset, export_format: str, filename_prefix: str) -> HttpResponse:
    export_format = (export_format or "xlsx").lower()
    format_class = EXPORT_FORMATS.get(export_format, EXPORT_FORMATS["xlsx"])
    exporter = format_class()
    payload = exporter.export_data(dataset)
    extension = exporter.get_extension()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{filename_prefix}_{timestamp}.{extension}"
    response = HttpResponse(payload, content_type=exporter.get_content_type())
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response

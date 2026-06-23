from __future__ import annotations

from datetime import datetime
from typing import Any

from django.http import HttpResponse
from django.db.models import Q
from import_export.formats import base_formats
from tablib import Dataset

from power_church_core.formatting import br_date, br_datetime
from power_church_core.normalization import format_cpf, moneyless_int, normalize_match_name
from power_church_django.apps.people.models import (
    PersonAddressSnapshot,
    PersonContributionSnapshot,
    PersonContributorSnapshot,
    PersonSnapshot,
)
from power_church_django.services.people_read_native import organized_family_nuclei
from power_church_django.services.runtime_formatting import format_status, status_sigla


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


def _people_export_rows_from_snapshots(q: str = "", status: str = "", city: str = "") -> tuple[list[dict[str, Any]], int]:
    queryset = PersonSnapshot.objects.filter(is_active=True)
    if status:
        queryset = queryset.filter(status=status)
    if city:
        normalized_city = normalize_match_name(city)
        city_ids = list(
            {
                row["person_id"]
                for row in PersonAddressSnapshot.objects.values("person_id", "city")
                if normalize_match_name(row["city"] or "") == normalized_city
            }
        )
        queryset = queryset.filter(id__in=city_ids)
    if q:
        normalized = normalize_match_name(q)
        digits = "".join(ch for ch in q if ch.isdigit())
        queryset = queryset.filter(
            Q(normalized_name__icontains=normalized)
            | Q(name__icontains=q)
            | Q(social_name__icontains=q)
            | Q(internal_code__icontains=q)
            | Q(cpf__icontains=digits or q)
            | Q(normalized_email__icontains=q.lower())
            | Q(primary_email__icontains=q)
            | Q(primary_phone__icontains=digits or q)
            | Q(primary_whatsapp__icontains=digits or q)
        )
    total = int(queryset.count())
    people = list(queryset.order_by("normalized_name", "legacy_id"))
    person_ids = [int(person.id) for person in people]

    address_index: dict[int, dict[str, Any]] = {}
    for address in PersonAddressSnapshot.objects.filter(person_id__in=person_ids).order_by("person_id", "-is_primary", "legacy_id"):
        if int(address.person_id) in address_index:
            continue
        address_index[int(address.person_id)] = {
            "endereco_tipo": address.address_type or "",
            "cep": address.cep or "",
            "logradouro": address.street or "",
            "numero": address.number or "",
            "complemento": address.complement or "",
            "bairro": address.neighborhood or "",
            "cidade": address.city or "",
            "uf": address.state or "",
        }

    contributor_index: dict[int, dict[str, Any]] = {}
    contribution_index: dict[int, dict[str, Any]] = {}
    if person_ids:
        contributor_groups: dict[int, list[str]] = {}
        for contributor in (
            PersonContributorSnapshot.objects.filter(person_id__in=person_ids, is_active=True)
            .only("person_id", "name")
            .order_by("person_id", "name", "legacy_id")
        ):
            contributor_groups.setdefault(int(contributor.person_id), []).append(contributor.name or "")
        contributor_index = {
            person_id: {
                "count": len(names),
                "names": " / ".join(name for name in names if name),
            }
            for person_id, names in contributor_groups.items()
        }
        contributions_by_person: dict[int, dict[str, Any]] = {}
        for contribution in (
            PersonContributionSnapshot.objects.filter(person_id__in=person_ids, is_active=True)
            .only("person_id", "amount", "received_at_raw", "competence", "competence_order", "legacy_id")
            .order_by("person_id", "-competence_order", "-received_at", "-legacy_id")
        ):
            person_id = int(contribution.person_id)
            item = contributions_by_person.setdefault(
                person_id,
                {
                    "count": 0,
                    "total": 0.0,
                    "first_date": "",
                    "last_date": "",
                    "last_competencia": "",
                    "_best_order": -1,
                },
            )
            item["count"] += 1
            item["total"] += round(float(contribution.amount or 0), 2)
            received_at_raw = str(contribution.received_at_raw or "")
            if received_at_raw and (not item["first_date"] or received_at_raw < item["first_date"]):
                item["first_date"] = received_at_raw
            if received_at_raw and (not item["last_date"] or received_at_raw > item["last_date"]):
                item["last_date"] = received_at_raw
            competence_order = int(contribution.competence_order or 0)
            if competence_order > int(item["_best_order"]):
                item["_best_order"] = competence_order
                item["last_competencia"] = contribution.competence or ""
        contribution_index = {
            person_id: {
                "count": int(item["count"] or 0),
                "total": round(float(item["total"] or 0), 2),
                "first_date": item["first_date"] or "",
                "last_date": item["last_date"] or "",
                "last_competencia": item["last_competencia"] or "",
            }
            for person_id, item in contributions_by_person.items()
        }

    family_index: dict[int, dict[str, Any]] = {}
    all_nuclei = organized_family_nuclei(q="", cep="", review="all", person_status="all")["items"]
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
    for person in people:
        person_id = int(person.id)
        address = address_index.get(person_id, {})
        contributor = contributor_index.get(person.legacy_id, {})
        contribution = contribution_index.get(person.legacy_id, {})
        family = family_index.get(
            person.legacy_id,
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
                "id": moneyless_int(person.legacy_id),
                "codigo": person.internal_code or "",
                "nome": person.name or "",
                "nome_social": person.social_name or "",
                "cpf": format_cpf(person.cpf),
                "rg": person.rg or "",
                "data_nascimento": br_date(person.birth_date_raw or person.birth_date),
                "sexo": person.sex or "",
                "estado_civil": person.marital_status or "",
                "status": format_status(person.status),
                "sigla": status_sigla(person.status, True),
                "ativo": _bool_text(bool(person.is_active)),
                "email": person.primary_email or "",
                "telefone": person.primary_phone or "",
                "whatsapp": person.primary_whatsapp or "",
                "endereco_tipo": address.get("endereco_tipo", ""),
                "endereco_completo": _primary_address_line(address),
                "cep": address.get("cep", ""),
                "logradouro": address.get("logradouro", ""),
                "numero": address.get("numero", ""),
                "complemento": address.get("complemento", ""),
                "bairro": address.get("bairro", ""),
                "cidade": address.get("cidade", ""),
                "uf": address.get("uf", ""),
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
                "import_lote_id": moneyless_int(person.import_lot_id),
                "criado_em": br_datetime(person.created_at_legacy),
                "atualizado_em": br_datetime(person.updated_at_legacy),
                "observacoes": person.notes or "",
            }
        )
    return export_rows, total


def _people_export_rows(q: str = "", status: str = "", city: str = "") -> tuple[list[dict[str, Any]], int]:
    return _people_export_rows_from_snapshots(q=q, status=status, city=city)


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

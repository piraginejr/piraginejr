from __future__ import annotations

from typing import Any

from power_church_core.normalization import clean_cpf, normalize_query, valid_cpf


ALLOWED_PERSON_STATUSES = {
    "membro_ativo",
    "membro_inativo",
    "frequentador",
    "visitante",
    "arquivo_morto",
}

PERSON_STATUS_OPTIONS = [
    ("membro_ativo", "Membro ativo"),
    ("membro_inativo", "Membro inativo"),
    ("frequentador", "Frequentador"),
    ("visitante", "Visitante"),
    ("arquivo_morto", "Arquivo morto"),
]

PERSON_SEX_OPTIONS = [
    ("", "Nao informado"),
    ("masculino", "Masculino"),
    ("feminino", "Feminino"),
]

PERSON_MARITAL_STATUS_OPTIONS = [
    ("", "Nao informado"),
    ("solteiro", "Solteiro(a)"),
    ("casado", "Casado(a)"),
    ("uniao_estavel", "Uniao estavel"),
    ("divorciado", "Divorciado(a)"),
    ("viuvo", "Viuvo(a)"),
    ("noivo", "Noivo(a)"),
]

ALLOWED_FAMILY_RELATIONSHIP_TYPES = {
    "nucleo_familiar",
    "familia_estendida",
    "conjuge",
    "filho",
    "pai_mae",
    "irmao",
    "neto",
    "genro_nora",
    "outro_familiar",
}


def clean_member_code(value: object) -> str:
    compact = str(value or "").strip().replace(" ", "")
    upper = compact.upper()
    for prefix in ("MEM-", "MBR-", "NM-"):
        if upper.startswith(prefix):
            compact = compact[len(prefix) :]
            break
    compact = compact.replace("-", "")
    digits = "".join(ch for ch in compact if ch.isdigit())
    return digits or normalize_query(compact)


def status_grants_member_code(value: object) -> bool:
    return normalize_query(value) in {"membro_ativo", "membro_inativo"}


def _normalize_choice(value: object, allowed: set[str]) -> str:
    normalized = normalize_query(value).lower()
    return normalized if normalized in allowed else ""


def manual_cpf_or_error(value: object, error_cls: type[Exception]) -> str | None:
    text = normalize_query(value)
    if not text:
        return None
    digits = "".join(ch for ch in text if ch.isdigit())
    if not valid_cpf(digits):
        raise error_cls("CPF invalido. Corrija o numero antes de salvar a ficha.")
    return digits


def manual_email_or_error(value: object, error_cls: type[Exception]) -> str:
    email = normalize_query(value).lower()
    if not email:
        return ""
    if len(email) > 254 or any(ch.isspace() for ch in email) or email.count("@") != 1:
        raise error_cls("E-mail invalido. Corrija o endereco antes de salvar a ficha.")
    local, domain = email.rsplit("@", 1)
    labels = domain.split(".")
    if not local or not domain or len(labels) < 2 or len(labels[-1]) < 2:
        raise error_cls("E-mail invalido. Corrija o endereco antes de salvar a ficha.")
    return email


def _form_value(data: Any, key: str, default: str = "") -> str:
    getter = getattr(data, "get", None)
    value = getter(key, default) if getter else default
    return normalize_query(value)


def person_form_payload(data: Any) -> dict[str, str]:
    payload = {
        "codigo_interno": clean_member_code(_form_value(data, "codigo_interno")),
        "nome": _form_value(data, "nome"),
        "nome_social": _form_value(data, "nome_social"),
        "cpf": clean_cpf(_form_value(data, "cpf")),
        "rg": _form_value(data, "rg"),
        "data_nascimento": _form_value(data, "data_nascimento"),
        "sexo": _normalize_choice(_form_value(data, "sexo"), {value for value, _label in PERSON_SEX_OPTIONS if value}),
        "estado_civil": _normalize_choice(
            _form_value(data, "estado_civil"),
            {value for value, _label in PERSON_MARITAL_STATUS_OPTIONS if value},
        ),
        "email_principal": _form_value(data, "email_principal"),
        "telefone_principal": _form_value(data, "telefone_principal"),
        "whatsapp_principal": _form_value(data, "whatsapp_principal"),
        "status": _form_value(data, "status", "frequentador"),
        "observacoes": _form_value(data, "observacoes"),
        "cep": _form_value(data, "cep"),
        "logradouro": _form_value(data, "logradouro"),
        "numero": _form_value(data, "numero"),
        "complemento": _form_value(data, "complemento"),
        "bairro": _form_value(data, "bairro"),
        "cidade": _form_value(data, "cidade"),
        "uf": _form_value(data, "uf").upper(),
        "allow_member_code_edit": "1" if _form_value(data, "allow_member_code_edit") == "1" else "",
    }
    if payload["status"] not in ALLOWED_PERSON_STATUSES:
        payload["status"] = "frequentador"
    return payload


def empty_person_form() -> dict[str, str]:
    payload = {key: "" for key in person_form_payload({}).keys()}
    payload["status"] = "frequentador"
    return payload

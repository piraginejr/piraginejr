from __future__ import annotations

from dataclasses import dataclass
import re

from .normalization import cleaned_document_token, normalize_match_name, normalize_query, santander_document_type


STATEMENT_LAYOUT_LABELS = {
    "BRADESCO_EXTRATO": "Bradesco Extrato PJ",
    "SICOOB_RECEBIMENTOS": "Sicoob Extrato de Recebimentos",
    "SICOOB_CONTA_CORRENTE": "Sicoob Extrato Completo",
    "SANTANDER_AUTO": "Santander Extrato",
    "SANTANDER_CONSOLIDADO": "Santander Consolidado",
    "SANTANDER_NAO_CONSOLIDADO": "Santander Nao Consolidado",
}
SUPPORTED_STATEMENT_LAYOUTS = frozenset(STATEMENT_LAYOUT_LABELS)
STATEMENT_BENEFICIARY_PATTERNS = (
    re.compile(
        r"\b(?:d[ií]zimo|oferta|contribui(?:cao|ção))\s+d[eo]\s+(.+?)(?=(?:\s+\b(?:cpf|cnpj|doc|documento|pix|ted|rem|ref|referente)\b|[|]|$))",
        flags=re.IGNORECASE,
    ),
)


@dataclass(frozen=True)
class BankMovement:
    layout_code: str
    bank_key: str
    received_on: str
    amount: float
    source_name: str = ""
    document_value: str = ""
    document_type: str = ""
    movement_kind: str = ""
    receiving_code: str = ""
    raw_text: str = ""


def statement_layout_label(layout_code: object) -> str:
    code = normalize_query(layout_code).upper()
    return STATEMENT_LAYOUT_LABELS.get(code, code or "Extrato bancario")


def statement_layout_contributor_source(layout_code: object) -> str:
    code = normalize_query(layout_code).upper()
    if code in {"SICOOB_RECEBIMENTOS", "SICOOB_CONTA_CORRENTE"}:
        return "extrato_sicoob"
    if code.startswith("SANTANDER"):
        return "extrato_santander"
    return "extrato_bradesco"


def statement_layout_is_santander(layout_code: object) -> bool:
    return normalize_query(layout_code).upper().startswith("SANTANDER")


def statement_layout_is_supported(layout_code: object) -> bool:
    return normalize_query(layout_code).upper() in SUPPORTED_STATEMENT_LAYOUTS


def santander_document_display(document_value: object, document_type: object = "") -> str:
    digits = "".join(ch for ch in str(document_value or "") if ch.isdigit())
    doc_type = normalize_query(document_type).upper() or santander_document_type(digits).upper()
    if not digits:
        return "Documento Santander"
    return f"{doc_type} {digits}"


def santander_identity_source_label(document_value: object, document_type: object = "") -> str:
    return f"Santander {santander_document_display(document_value, document_type)}"


def statement_document_identity_label(layout_code: object, document_value: object, document_type: object = "") -> str:
    if statement_layout_is_santander(layout_code):
        return santander_identity_source_label(document_value, document_type)
    document_label = santander_document_display(document_value, document_type)
    return document_label if document_label != "Documento Santander" else "Documento bancario"


def statement_contributor_name_for_identity(
    layout_code: object,
    source_name: object,
    document_value: object = "",
    document_type: object = "",
    person_name: object = "",
) -> str:
    if normalize_query(source_name):
        return normalize_query(source_name)
    if normalize_query(person_name):
        return normalize_query(person_name)
    if cleaned_document_token(document_value):
        return statement_document_identity_label(layout_code, document_value, document_type)
    return ""


def statement_declared_beneficiary_name(
    layout_code: object,
    *,
    detail_text: object = "",
    raw_text: object = "",
    source_name: object = "",
) -> str:
    _layout = normalize_query(layout_code).upper()
    candidates = [
        normalize_query(detail_text),
        normalize_query(raw_text),
    ]
    source_norm = normalize_match_name(source_name)
    for text in candidates:
        if not text:
            continue
        for pattern in STATEMENT_BENEFICIARY_PATTERNS:
            match = pattern.search(text)
            if not match:
                continue
            beneficiary = normalize_query(match.group(1))
            beneficiary = re.sub(r"\s+", " ", beneficiary).strip(" -:;,")
            if not beneficiary:
                continue
            if source_norm and normalize_match_name(beneficiary) == source_norm:
                continue
            return beneficiary
    return ""


def statement_is_same_organization_origin(source_name: object, organization_name: object) -> bool:
    source_norm = normalize_match_name(source_name)
    organization_norm = normalize_match_name(organization_name)
    if not source_norm or not organization_norm:
        return False
    if source_norm == organization_norm:
        return True
    if organization_norm.startswith(source_norm) or source_norm.startswith(organization_norm):
        return True
    source_tokens = source_norm.split()
    org_tokens = organization_norm.split()
    if len(source_tokens) >= 3 and source_tokens[:3] == org_tokens[:3]:
        return True
    return False


def statement_same_organization_review_note(source_name: object, organization_name: object) -> str:
    if not statement_is_same_organization_origin(source_name, organization_name):
        return ""
    display_name = normalize_query(source_name) or "a propria organizacao"
    return (
        f"Origem financeira coincide com a propria organizacao ({display_name}). "
        "Confira se e remessa interna entre contas e, se for, marque como ignorado para nao entrar como contribuicao."
    )

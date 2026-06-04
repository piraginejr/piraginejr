from __future__ import annotations

import cgi
import hashlib
import html
import json
import mimetypes
import os
import re
import sqlite3
import sys
import threading
import urllib.parse
import webbrowser
from datetime import date, datetime
from difflib import SequenceMatcher
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from power_church_core import banking as core_banking
from power_church_core import bank_lots as core_bank_lots
from power_church_core import bank_parsers as core_bank_parsers
from power_church_core import contributors as core_contributors
from power_church_core import designations as core_designations
from power_church_core import formatting as core_formatting
from power_church_core import matching as core_matching
from power_church_core import normalization as core_normalization
from power_church_core import pdf_text as core_pdf_text
from power_church_core import signatures as core_signatures


ROOT = Path(__file__).resolve().parent


def env_path(name: str, default: Path) -> Path:
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    path = Path(value).expanduser()
    return path if path.is_absolute() else ROOT / path


DB_PATH = env_path("POWER_CHURCH_DB_PATH", ROOT / "data" / "power_church_membros_importado.db")
PHOTO_DIR = ROOT / "data" / "fotos_membros"
BRAND_DIR = ROOT / "data" / "branding"
BRAND_LOGO_PATH = BRAND_DIR / "power_church_logo.jpg"
BRAND_LOGO_URL = "/branding/logo"
PIX_UPLOAD_DIR = ROOT / "data" / "pix_uploads"
STATEMENT_UPLOAD_DIR = ROOT / "data" / "statement_uploads"
PEOPLE_UPLOAD_DIR = ROOT / "data" / "people_uploads"
APP_TITLE = "Power Church"
APP_SUBTITLE = "Sistema Operacional"
PHOTO_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".heif")
PIX_COMPANY_HINTS = (
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
PIX_RULE_DEFAULTS = [
    ("01", "Acao Social", "ACAO_SOCIAL"),
    ("02", "Missoes Nacionais", "MISSOES_NACIONAIS"),
    ("03", "Missoes Mundiais", "MISSOES_MUNDIAIS"),
    ("04", "Missoes Igreja", "MISSOES_IGREJA"),
    ("05", "Juventude", "JUVENTUDE"),
    ("06", "Adolescentes", "ADOLESCENTES"),
    ("07", "Musica", "MUSICA"),
    ("08", "Homens", "HOMENS"),
    ("09", "Embaixadores", "EMBAIXADORES"),
    ("10", "Mensageiras", "MENSAGEIRAS"),
    ("11", "Campanha Especial", "CAMPANHA_ESPECIAL"),
    ("12", "Mulheres", "MULHERES"),
]
PIX_RULE_DEFAULT_TYPE_CODES = {code: type_code for code, _label, type_code in PIX_RULE_DEFAULTS}
CENT_RULE_PLAN_ACCOUNT_PREFIX = core_designations.CENT_RULE_PLAN_ACCOUNT_PREFIX
CENT_RULE_TYPE_PREFIX = core_designations.CENT_RULE_TYPE_PREFIX
BRADESCO_CREDIT_PREFIXES = core_bank_parsers.BRADESCO_CREDIT_PREFIXES
SICOOB_RECEIVING_PREFIXES = core_bank_parsers.SICOOB_RECEIVING_PREFIXES
BRADESCO_KNOWN_PREFIXES = core_bank_parsers.BRADESCO_KNOWN_PREFIXES
STATEMENT_LAYOUT_LABELS = core_banking.STATEMENT_LAYOUT_LABELS
MONTHS_PT = [
    "",
    "Janeiro",
    "Fevereiro",
    "Marco",
    "Abril",
    "Maio",
    "Junho",
    "Julho",
    "Agosto",
    "Setembro",
    "Outubro",
    "Novembro",
    "Dezembro",
]
MONTH_ABBR_PT = core_bank_parsers.MONTH_ABBR_PT


def moneyless_int(value: object) -> int:
    return core_normalization.moneyless_int(value)


def br_date(value: object) -> str:
    return core_formatting.br_date(value)


def br_datetime(value: object) -> str:
    return core_formatting.br_datetime(value)


def br_money(value: object) -> str:
    return core_formatting.br_money(value)


def parse_money(value: object) -> float:
    return core_formatting.parse_money(value)


def competencia_from_date(value: object) -> tuple[str, int]:
    return core_formatting.competencia_from_date(value)


def h(value: object) -> str:
    return html.escape("" if value is None else str(value))


def normalize_query(value: object) -> str:
    return core_normalization.normalize_query(value)


def format_cpf(value: object) -> str:
    return core_normalization.format_cpf(value)


def format_document(value: object) -> str:
    return core_normalization.format_document(value)


def contribution_report_identity(person_name: object, contributor_name: object, document: object) -> dict[str, str]:
    return core_normalization.contribution_report_identity(person_name, contributor_name, document)


def clean_cpf(value: object) -> str:
    return core_normalization.clean_cpf(value)


def clean_member_code(value: object) -> str:
    compact = str(value or "").strip().replace(" ", "")
    upper = compact.upper()
    for prefix in ("MEM-", "MBR-", "NM-"):
        if upper.startswith(prefix):
            compact = compact[len(prefix):]
            break
    compact = compact.replace("-", "")
    digits = "".join(ch for ch in compact if ch.isdigit())
    return digits or normalize_query(compact)


def format_system_id(value: object) -> str:
    number = moneyless_int(value)
    return f"ID-{number:06d}" if number else ""


def format_member_code(value: object) -> str:
    code = clean_member_code(value)
    return f"MEM-{code}" if code else ""


def person_edit_url(person_id: int, audit_mode: bool = False) -> str:
    base = f"/pessoa/editar?id={moneyless_int(person_id)}"
    return f"{base}&modo=auditoria" if audit_mode else base


def status_grants_member_code(value: object) -> bool:
    status = normalize_query(value)
    return status in {"membro_ativo", "membro_inativo"}


def first_form_value(form: dict[str, list[str]], key: str, default: str = "") -> str:
    return normalize_query(form.get(key, [default])[0])


def safe_redirect_path(value: str, fallback: str = "/") -> str:
    value = str(value or "").strip()
    if not value.startswith("/") or value.startswith("//"):
        return fallback
    return value


def pix_association_pending_expr(
    movement_alias: str = "m",
    contribution_alias: str = "ico",
    contributor_alias: str = "ict",
) -> str:
    return (
        f"({movement_alias}.imported_contribution_id IS NOT NULL "
        f"AND COALESCE({movement_alias}.association_reviewed, 0) = 0 "
        f"AND COALESCE({contribution_alias}.pessoa_id, {contributor_alias}.pessoa_id, "
        f"{movement_alias}.resolved_person_id, {movement_alias}.suggested_person_id) IS NULL)"
    )


def statement_association_pending_expr(
    movement_alias: str = "em",
    contribution_alias: str = "ic",
    contributor_alias: str = "ict",
) -> str:
    return (
        f"({contribution_alias}.id IS NOT NULL "
        f"AND COALESCE({movement_alias}.association_reviewed, 0) = 0 "
        f"AND COALESCE({contribution_alias}.pessoa_id, {contributor_alias}.pessoa_id, "
        f"{movement_alias}.resolved_person_id, {movement_alias}.suggested_person_id) IS NULL)"
    )


def brand_logo_available() -> bool:
    return BRAND_LOGO_PATH.exists()


def jpeg_image_info(payload: bytes) -> tuple[int, int, int]:
    if len(payload) < 4 or payload[:2] != b"\xff\xd8":
        raise ValueError("Arquivo JPEG invalido.")
    index = 2
    sof_markers = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
    while index < len(payload):
        while index < len(payload) and payload[index] == 0xFF:
            index += 1
        if index >= len(payload):
            break
        marker = payload[index]
        index += 1
        if marker in {0xD8, 0xD9}:
            continue
        if index + 1 >= len(payload):
            break
        segment_length = int.from_bytes(payload[index:index + 2], "big")
        if segment_length < 2 or index + segment_length > len(payload):
            break
        if marker in sof_markers and segment_length >= 8:
            height = int.from_bytes(payload[index + 3:index + 5], "big")
            width = int.from_bytes(payload[index + 5:index + 7], "big")
            components = payload[index + 7]
            if width and height:
                return width, height, components
        index += segment_length
    raise ValueError("Nao foi possivel ler o tamanho da logo JPEG.")


def slugify_filename_text(value: object, fallback: str = "sem_nome", limit: int = 48) -> str:
    return core_bank_lots.slugify_filename_text(value, fallback=fallback, limit=limit)


def normalize_match_name(value: object) -> str:
    return core_normalization.normalize_match_name(value)


NAME_STOPWORDS = {"A", "AS", "DA", "DAS", "DE", "DO", "DOS", "E", "O", "OS"}


def significant_name_tokens(value: object) -> set[str]:
    return {
        token
        for token in normalize_match_name(value).split()
        if len(token) >= 3 and token not in NAME_STOPWORDS
    }


def cent_rule_plan_account_code(code: object) -> str:
    return core_designations.cent_rule_plan_account_code(code)


def cent_rule_type_code(code: object) -> str:
    return core_designations.cent_rule_type_code(code)


def statement_layout_label(layout_code: object) -> str:
    return core_banking.statement_layout_label(layout_code)


def statement_layout_contributor_source(layout_code: object) -> str:
    return core_banking.statement_layout_contributor_source(layout_code)


def statement_layout_is_santander(layout_code: object) -> bool:
    return core_banking.statement_layout_is_santander(layout_code)


def statement_layout_is_supported(layout_code: object) -> bool:
    return core_banking.statement_layout_is_supported(layout_code)


def santander_document_type(document_value: object) -> str:
    return core_normalization.santander_document_type(document_value)


def santander_document_display(document_value: object, document_type: object = "") -> str:
    return core_banking.santander_document_display(document_value, document_type)


def santander_identity_source_label(document_value: object, document_type: object = "") -> str:
    return core_banking.santander_identity_source_label(document_value, document_type)


def statement_document_identity_label(layout_code: object, document_value: object, document_type: object = "") -> str:
    return core_banking.statement_document_identity_label(layout_code, document_value, document_type)


def statement_contributor_name_for_identity(
    layout_code: object,
    source_name: object,
    document_value: object = "",
    document_type: object = "",
    person_name: object = "",
) -> str:
    return core_banking.statement_contributor_name_for_identity(
        layout_code,
        source_name,
        document_value,
        document_type,
        person_name,
    )


def statement_person_document_compare_badge(bank_document: object, person_cpf: object) -> str:
    bank_token = cleaned_document_token(bank_document)
    person_token = clean_cpf(person_cpf)
    if not bank_token:
        return badge("Sem documento bancario", "info")
    if len(bank_token) == 14:
        return badge("Banco trouxe CNPJ", "info")
    if not person_token:
        return badge("CPF da ficha ausente", "warn")
    if document_query_matches(bank_token, person_token):
        return badge("CPF confere", "ok")
    return badge("CPF diferente", "danger")


def statement_person_document_compare_note(bank_document: object, person_cpf: object) -> str:
    bank_token = cleaned_document_token(bank_document)
    person_token = clean_cpf(person_cpf)
    if not bank_token:
        return "O banco nao informou documento comparavel."
    if len(bank_token) == 14:
        return "O banco informou CNPJ. Compare com o vinculo financeiro/PJ; o CPF da ficha fica exibido apenas para conferencia cadastral."
    if not person_token:
        return "A ficha nao tem CPF cadastrado para comparacao direta."
    if document_query_matches(bank_token, person_token):
        return "O documento bancario confere com o CPF cadastrado na ficha."
    return "Atencao: o documento bancario nao confere com o CPF cadastrado na ficha."


def sicoob_receiving_kind_metadata(history: object) -> dict[str, object]:
    return core_bank_parsers.sicoob_receiving_kind_metadata(history)


def sicoob_receiving_kind_metadata_norm(history: object) -> dict[str, object]:
    return core_bank_parsers.sicoob_receiving_kind_metadata_norm(history)


def derived_pix_name_aliases(value: object) -> list[str]:
    return core_matching.derived_pix_name_aliases(value)


def pix_name_is_expanded_variant(donor_name: object, candidate_name: object) -> bool:
    return core_matching.pix_name_is_expanded_variant(donor_name, candidate_name)


def cleaned_document_token(value: object) -> str:
    return core_normalization.cleaned_document_token(value)


def document_query_matches(query_value: object, candidate_value: object) -> bool:
    return core_normalization.document_query_matches(query_value, candidate_value)


def masked_document_matches(masked_value: object, candidate_value: object) -> bool:
    return core_normalization.masked_document_matches(masked_value, candidate_value)


def active_status_allows_auto_match(status: object) -> bool:
    return core_matching.active_status_allows_auto_match(status)


def pix_code_from_amount(value: float) -> str:
    return core_normalization.pix_code_from_amount(value)


def pix_competencia_for_date(value: str) -> tuple[str, int]:
    return competencia_from_date(value)


def infer_pdf_statement_date(month_token: str, day: int, period_start_iso: str, period_end_iso: str) -> str:
    return core_bank_parsers.infer_pdf_statement_date(month_token, day, period_start_iso, period_end_iso)


def pix_confidence_group(value: object) -> str:
    confidence = normalize_query(value)
    mapping = {
        "forte_doc_nome": "forte",
        "forte_doc": "forte",
        "forte_nome": "forte",
        "provavel_doc_amb_nome": "provavel",
        "provavel_nome": "provavel",
        "ambiguo": "ambiguo",
        "conflito_doc_nome": "ambiguo",
        "sem_match": "sem_match",
        "pj_ou_externo": "pj_externo",
    }
    return mapping.get(confidence, confidence or "outros")


def pix_signature_component(value: object) -> str:
    return core_signatures.signature_component(value)


def pix_global_signature(
    received_on: object,
    amount: object,
    donor_name_normalized: object,
    document_mask: object,
    document_type: object,
    raw_text: object,
) -> str:
    return core_signatures.pix_global_signature(
        received_on,
        amount,
        donor_name_normalized,
        document_mask,
        document_type,
        raw_text,
    )


def statement_global_signature(
    bank_key: object,
    received_on: object,
    amount: object,
    donor_name_normalized: object,
    movement_kind: object,
    bank_document: object,
    raw_text: object,
) -> str:
    return core_signatures.statement_global_signature(
        bank_key,
        received_on,
        amount,
        donor_name_normalized,
        movement_kind,
        bank_document,
        raw_text,
    )


def extract_pdf_pages(pdf_path: Path) -> list[str]:
    return core_pdf_text.extract_pdf_pages(pdf_path)


def extract_pdf_line_selections(pdf_path: Path) -> list[list[dict[str, object]]]:
    return core_pdf_text.extract_pdf_line_selections(pdf_path)


def infer_statement_date_from_br_token(day_month: str, period_start_iso: str, period_end_iso: str) -> str:
    return core_bank_parsers.infer_statement_date_from_br_token(day_month, period_start_iso, period_end_iso)


def bradesco_match_prefix(value: object) -> str:
    return core_bank_parsers.bradesco_match_prefix(value)


def bradesco_source_name_is_noise(value: object) -> bool:
    return core_bank_parsers.bradesco_source_name_is_noise(value)


def normalize_contributor_source_name(name: object, source: object = "") -> str:
    return core_bank_parsers.normalize_contributor_source_name(name, source)


def contributor_name_is_noise(name: object, source: object = "") -> bool:
    return core_bank_parsers.contributor_name_is_noise(name, source)


def bradesco_extract_source_name(prefix: str, detail_lines: list[str]) -> tuple[str, str, str]:
    return core_bank_parsers.bradesco_extract_source_name(prefix, detail_lines)


def bradesco_credit_kind_metadata(prefix: str) -> dict[str, object]:
    return core_bank_parsers.bradesco_credit_kind_metadata(prefix)


def bradesco_credit_display_label(prefix: str, source_name: str, detail_text: str) -> str:
    return core_bank_parsers.bradesco_credit_display_label(prefix, source_name, detail_text)


def bradesco_period_from_text(full_text: str) -> tuple[str, str]:
    return core_bank_parsers.bradesco_period_from_text(full_text)


def bradesco_detail_amount_parts(detail_line: str) -> tuple[str, str, str]:
    return core_bank_parsers.bradesco_detail_amount_parts(detail_line)


def parse_bradesco_statement_text_entries(
    pages: list[str],
    period_start: str,
    period_end: str,
    period_start_br: str,
) -> list[dict[str, object]]:
    return core_bank_parsers.parse_bradesco_statement_text_entries(pages, period_start, period_end, period_start_br)
    entries: list[dict[str, object]] = []
    order = 0
    carry_date_token = period_start_br
    pending: dict[str, object] | None = None

    def finalize(detail_line: str) -> None:
        nonlocal order, carry_date_token, pending
        if pending is None:
            return
        detail_text, document, credit_text = bradesco_detail_amount_parts(detail_line)
        if not credit_text:
            return
        prefix = str(pending["prefix"])
        metadata = bradesco_credit_kind_metadata(prefix)
        if not metadata:
            pending = None
            return
        source_name, explicit_date_token, extracted_detail = bradesco_extract_source_name(prefix, [detail_text])
        date_token = normalize_query(explicit_date_token or pending.get("date_token") or carry_date_token)
        movement_date = infer_statement_date_from_br_token(date_token, period_start, period_end)
        competencia, competencia_ordem = competencia_from_date(movement_date)
        amount = parse_money(credit_text)
        order += 1
        entries.append(
            {
                "page_number": moneyless_int(pending.get("page_number")) or 1,
                "order_in_file": order,
                "received_on": movement_date,
                "competencia": competencia,
                "competencia_ordem": competencia_ordem,
                "amount": amount,
                "movement_kind": str(metadata.get("movement_kind") or ""),
                "receiving_code": str(metadata.get("receiving_code") or ""),
                "bank_document": document,
                "source_name": normalize_query(source_name),
                "source_name_normalized": normalize_match_name(source_name),
                "origin_label": bradesco_credit_display_label(prefix, source_name, extracted_detail),
                "allow_without_name": bool(metadata.get("allow_without_name")),
                "prefix": prefix,
                "detail_text": extracted_detail,
                "raw_text": " | ".join(item for item in [prefix, extracted_detail] if item),
            }
        )
        carry_date_token = date_token or carry_date_token
        pending = None

    in_latest_transactions = False
    for page_number, page_text in enumerate(pages, start=1):
        for raw_line in page_text.splitlines():
            line = normalize_query(raw_line)
            if not line:
                continue
            if "ULTIMOS LANCAMENTOS" in normalize_match_name(line):
                in_latest_transactions = True
                pending = None
                continue
            if in_latest_transactions:
                continue
            if any(token in normalize_match_name(line) for token in {"EXTRATO MENSAL", "PRIMEIRA IGREJA", "NOME DO USUARIO", "DATA DA OPERACAO"}):
                continue
            date_match = re.match(r"^(\d{2}/\d{2}(?:/\d{4})?)\s+(.+)$", line)
            line_date = ""
            body = line
            if date_match:
                line_date = normalize_query(date_match.group(1))
                body = normalize_query(date_match.group(2))
                carry_date_token = line_date
            prefix = bradesco_match_prefix(body)
            if prefix:
                pending = {
                    "prefix": prefix,
                    "date_token": line_date or carry_date_token,
                    "page_number": page_number,
                }
                remainder = normalize_query(body[len(prefix):])
                if remainder and re.search(r"\d{1,3}(?:\.\d{3})*,\d{2}", remainder):
                    finalize(remainder)
                continue
            if pending is not None and re.search(r"\d{1,3}(?:\.\d{3})*,\d{2}", body):
                finalize(body)
    return entries


def bradesco_is_same_organization_origin(source_name: object, organization_name: object) -> bool:
    return statement_is_same_organization_origin(source_name, organization_name)


def bradesco_same_organization_review_note(source_name: object, organization_name: object) -> str:
    return statement_same_organization_review_note(source_name, organization_name)


def statement_is_same_organization_origin(source_name: object, organization_name: object) -> bool:
    return core_banking.statement_is_same_organization_origin(source_name, organization_name)


def statement_same_organization_review_note(source_name: object, organization_name: object) -> str:
    return core_banking.statement_same_organization_review_note(source_name, organization_name)


def sicoob_receiving_identity_document(detail_lines: list[str]) -> tuple[str, str]:
    return core_bank_parsers.sicoob_receiving_identity_document(detail_lines)


def sicoob_receiving_bank_reference(detail_lines: list[str]) -> str:
    return core_bank_parsers.sicoob_receiving_bank_reference(detail_lines)


def sicoob_receiving_extract_source_name(
    history: str,
    detail_lines: list[str],
    identity_document: str = "",
) -> tuple[str, str]:
    return core_bank_parsers.sicoob_receiving_extract_source_name(history, detail_lines, identity_document)


def sicoob_receiving_display_label(history: str, source_name: str, detail_text: str) -> str:
    return core_bank_parsers.sicoob_receiving_display_label(history, source_name, detail_text)


def statement_display_label(layout_code: object, prefix: str, source_name: str, detail_text: str) -> str:
    return core_bank_parsers.statement_display_label(layout_code, prefix, source_name, detail_text)


def parse_sicoob_receipts_pdf(pdf_path: Path) -> dict[str, object]:
    return core_bank_parsers.parse_sicoob_receipts_pdf(pdf_path)
    pages = extract_pdf_pages(pdf_path)
    full_text = "\n".join(pages)
    range_match = re.search(r"PER[ÍI]ODO:\s*(\d{2}/\d{2}/\d{4})\s*-\s*(\d{2}/\d{2}/\d{4})", full_text, flags=re.IGNORECASE)
    if not range_match:
        raise ValueError("Nao foi possivel identificar o periodo do extrato Sicoob.")
    period_start_br, period_end_br = range_match.groups()

    def br_to_iso(value: str) -> str:
        day, month, year = value.split("/")
        return f"{year}-{month}-{day}"

    period_start = br_to_iso(period_start_br)
    period_end = br_to_iso(period_end_br)
    main_re = re.compile(r"^(\d{2}/\d{2})\s+(.+?)\s+([\d.]+,\d{2}[CD\*])$")
    excluded = {"SALDO DO DIA", "SALDO ANTERIOR", "SALDO BLOQ.ANTERIOR"}
    blocks: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    for page_number, page in enumerate(pages, start=1):
        for raw_line in page.splitlines():
            line = normalize_query(raw_line)
            match = main_re.match(line)
            if match:
                if current is not None:
                    blocks.append(current)
                current = {
                    "page_number": page_number,
                    "date_token": normalize_query(match.group(1)),
                    "history": normalize_query(match.group(2)),
                    "value_text": normalize_query(match.group(3)),
                    "details": [],
                }
                continue
            if current is not None and line:
                current["details"].append(line)
    if current is not None:
        blocks.append(current)
    entries: list[dict[str, object]] = []
    order = 0
    for block in blocks:
        history = normalize_query(block["history"])
        if history in excluded:
            continue
        metadata = sicoob_receiving_kind_metadata(history) or sicoob_receiving_kind_metadata_norm(history)
        if not metadata:
            continue
        value_text = normalize_query(block["value_text"])
        if value_text.endswith("D"):
            continue
        details = [normalize_query(item) for item in block["details"] if normalize_query(item)]
        amount = parse_money(value_text.replace("C", "").replace("D", "").replace("*", ""))
        identity_document, document_type = sicoob_receiving_identity_document(details)
        bank_reference = sicoob_receiving_bank_reference(details)
        source_name, detail_text = sicoob_receiving_extract_source_name(history, details, identity_document)
        order += 1
        movement_date = infer_statement_date_from_br_token(str(block["date_token"]), period_start, period_end)
        competencia, competencia_ordem = competencia_from_date(movement_date)
        entries.append(
            {
                "page_number": moneyless_int(block["page_number"]) or 1,
                "order_in_file": order,
                "received_on": movement_date,
                "competencia": competencia,
                "competencia_ordem": competencia_ordem,
                "amount": round(amount, 2),
                "movement_kind": str(metadata.get("movement_kind") or ""),
                "receiving_code": str(metadata.get("receiving_code") or ""),
                "bank_document": identity_document or bank_reference,
                "document_type": document_type,
                "source_name": normalize_query(source_name),
                "source_name_normalized": normalize_match_name(source_name),
                "origin_label": sicoob_receiving_display_label(history, source_name, detail_text),
                "allow_without_name": bool(metadata.get("allow_without_name")),
                "prefix": history,
                "detail_text": detail_text,
                "raw_text": " | ".join([history, *details]),
            }
        )
    if not entries:
        raise ValueError("Nao foi possivel localizar recebimentos validos no extrato Sicoob.")
    return {
        "bank_name": "Sicoob",
        "statement_kind": "extrato_sicoob_recebimentos",
        "file_hash": hashlib.sha256(pdf_path.read_bytes()).hexdigest(),
        "period_start": period_start,
        "period_end": period_end,
        "entries": entries,
    }


SANTANDER_MONTHS_PT = core_bank_parsers.SANTANDER_MONTHS_PT


def santander_detect_layout(full_text: object) -> str:
    return core_bank_parsers.santander_detect_layout(full_text)


def santander_period_from_text(full_text: object, layout_code: object) -> tuple[str, str]:
    return core_bank_parsers.santander_period_from_text(full_text, layout_code)


def parse_santander_statement_pdf(pdf_path: Path, requested_layout_code: str = "SANTANDER_AUTO") -> dict[str, object]:
    return core_bank_parsers.parse_santander_statement_pdf(pdf_path, requested_layout_code)
    pages = extract_pdf_pages(pdf_path)
    full_text = "\n".join(pages)
    detected_layout = santander_detect_layout(full_text)
    requested = normalize_query(requested_layout_code).upper() or "SANTANDER_AUTO"
    if requested in {"SANTANDER_CONSOLIDADO", "SANTANDER_NAO_CONSOLIDADO"} and detected_layout != requested:
        raise ValueError(
            f"O PDF parece ser {statement_layout_label(detected_layout)}, mas o layout escolhido foi {statement_layout_label(requested)}."
        )
    layout_code = detected_layout if detected_layout != "SANTANDER_AUTO" else requested
    period_start, period_end = santander_period_from_text(full_text, layout_code)
    line_re = re.compile(
        r"^(?:(\d{2}/\d{2}(?:/\d{4})?)\s+)?Pix\s+Recebido\s+(\d{11}|\d{14})(?:\s+\d{4,})?\s+-?\s*([0-9.]+,\d{2})\b",
        flags=re.IGNORECASE,
    )
    entries: list[dict[str, object]] = []
    current_date_token = ""
    order = 0
    for page_number, page in enumerate(pages, start=1):
        for raw_line in page.splitlines():
            line = normalize_query(raw_line)
            if not line:
                continue
            date_match = re.match(r"^(\d{2}/\d{2}(?:/\d{4})?)\b", line)
            if date_match:
                current_date_token = normalize_query(date_match.group(1))
            if "PIX RECEBIDO" not in line.upper():
                continue
            match = line_re.search(line)
            if not match:
                continue
            line_date_token = normalize_query(match.group(1)) or current_date_token
            if not line_date_token:
                raise ValueError("Nao foi possivel identificar a data de um PIX recebido no extrato Santander.")
            document_value = normalize_query(match.group(2))
            document_type = santander_document_type(document_value)
            amount = parse_money(match.group(3))
            movement_date = infer_statement_date_from_br_token(line_date_token, period_start, period_end)
            competencia, competencia_ordem = competencia_from_date(movement_date)
            order += 1
            detail_text = santander_document_display(document_value, document_type)
            entries.append(
                {
                    "page_number": page_number,
                    "order_in_file": order,
                    "received_on": movement_date,
                    "competencia": competencia,
                    "competencia_ordem": competencia_ordem,
                    "amount": amount,
                    "movement_kind": "pix",
                    "receiving_code": "PIX",
                    "bank_document": document_value,
                    "document_type": document_type,
                    "source_name": "",
                    "source_name_normalized": "",
                    "origin_label": santander_identity_source_label(document_value, document_type),
                    "allow_without_name": False,
                    "prefix": "Pix Recebido",
                    "detail_text": detail_text,
                    "raw_text": line,
                }
            )
    if not entries:
        raise ValueError("Nao foi possivel localizar PIX recebidos validos no extrato Santander.")
    return {
        "bank_name": "Santander",
        "statement_kind": "extrato_santander",
        "layout_code": layout_code,
        "file_hash": hashlib.sha256(pdf_path.read_bytes()).hexdigest(),
        "period_start": period_start,
        "period_end": period_end,
        "entries": entries,
    }


def parse_statement_pdf_by_layout(layout_code: str, pdf_path: Path) -> dict[str, object]:
    return core_bank_parsers.parse_statement_pdf_by_layout(layout_code, pdf_path)


def statement_should_skip_bradesco_entry(entry: dict[str, object]) -> bool:
    return core_bank_parsers.statement_should_skip_bradesco_entry(entry)


def statement_should_skip_sicoob_entry(entry: dict[str, object]) -> bool:
    return core_bank_parsers.statement_should_skip_sicoob_entry(entry)


def statement_should_skip_entry(layout_code: object, entry: dict[str, object]) -> bool:
    return core_bank_parsers.statement_should_skip_entry(layout_code, entry)


def statement_row_should_be_excluded(row: sqlite3.Row | dict[str, object]) -> bool:
    return core_bank_parsers.statement_row_should_be_excluded(row)


def merge_statement_review_notes(current_note: object, extra_note: object) -> str:
    return core_bank_parsers.merge_statement_review_notes(current_note, extra_note)


def statement_association_name_key(value: object) -> str:
    return core_bank_parsers.statement_association_name_key(value)


def statement_association_identity_key(source_name: object, bank_document: object = "") -> str:
    return core_bank_parsers.statement_association_identity_key(source_name, bank_document)


def pix_association_key(name: object, document_mask: object) -> str:
    name_part = normalize_match_name(name)
    doc_part = cleaned_document_token(document_mask)
    if not name_part and not doc_part:
        return ""
    return f"{name_part}|{doc_part}"


def parse_bradesco_statement_pdf(pdf_path: Path) -> dict[str, object]:
    return core_bank_parsers.parse_bradesco_statement_pdf(pdf_path)
    pages = extract_pdf_pages(pdf_path)
    page_lines = extract_pdf_line_selections(pdf_path)
    full_text = "\n".join(pages)
    period_start_br, period_end_br = bradesco_period_from_text(full_text)

    def br_to_iso(value: str) -> str:
        day, month, year = value.split("/")
        return f"{year}-{month}-{day}"

    period_start = br_to_iso(period_start_br)
    period_end = br_to_iso(period_end_br)
    entries: list[dict[str, object]] = []
    carry_date_token = period_start_br[:5]
    order = 0
    pending_row: dict[str, object] | None = None

    def finalize_row(row: dict[str, object]) -> None:
        nonlocal order, carry_date_token
        if not row:
            return
        row["detail_lines"] = sorted(row.get("detail_lines") or [], key=lambda item: -float(item["y"]))
        prefix = bradesco_match_prefix(row.get("prefix_line", {}).get("text", "")) if row.get("prefix_line") else ""
        if not prefix:
            return
        metadata = bradesco_credit_kind_metadata(prefix)
        if not metadata:
            return
        source_name, explicit_date_token, detail_text = bradesco_extract_source_name(
            prefix,
            [str(item.get("text") or "") for item in row.get("detail_lines") or []],
        )
        date_token = normalize_query(explicit_date_token or row.get("date_token")) or carry_date_token
        if not date_token:
            return
        credit_text = normalize_query(row.get("credit_text"))
        if not credit_text:
            return
        amount = parse_money(credit_text)
        order += 1
        movement_date = infer_statement_date_from_br_token(date_token, period_start, period_end)
        competencia, competencia_ordem = competencia_from_date(movement_date)
        history_parts = [prefix]
        if detail_text:
            history_parts.append(detail_text)
        entries.append(
            {
                "page_number": moneyless_int(row.get("page_number")) or 1,
                "order_in_file": order,
                "received_on": movement_date,
                "competencia": competencia,
                "competencia_ordem": competencia_ordem,
                "amount": amount,
                "movement_kind": str(metadata.get("movement_kind") or ""),
                "receiving_code": str(metadata.get("receiving_code") or ""),
                "bank_document": normalize_query(row.get("docto")),
                "source_name": normalize_query(source_name),
                "source_name_normalized": normalize_match_name(source_name),
                "origin_label": bradesco_credit_display_label(prefix, source_name, detail_text),
                "allow_without_name": bool(metadata.get("allow_without_name")),
                "prefix": prefix,
                "detail_text": detail_text,
                "raw_text": " | ".join(item for item in history_parts if item),
            }
        )
        carry_date_token = date_token or carry_date_token

    for page_number, lines in enumerate(page_lines, start=1):
        left_lines = [
            row
            for row in lines
            if 70 <= float(row.get("x") or 0) < 235
            and "Mod.:" not in str(row.get("text") or "")
            and str(row.get("text") or "") not in {"Data", "Histórico"}
        ]
        right_lines = [
            row
            for row in lines
            if 250 <= float(row.get("x") or 0) < 520
            and str(row.get("text") or "") not in {"Docto", "Crédito", "Débito", "Saldo"}
            and "Extrato Unificado" not in str(row.get("text") or "")
            and "Agência:" not in str(row.get("text") or "")
        ]
        date_marks = sorted(
            [
                row
                for row in lines
                if float(row.get("x") or 0) < 70
                and re.fullmatch(r"\d{2}/\d{2}", normalize_query(row.get("text")))
            ],
            key=lambda item: -float(item["y"]),
        )
        docto_ys = sorted(
            {
                round(float(row["y"]), 1)
                for row in right_lines
                if re.fullmatch(r"\d{7}", normalize_query(row.get("text")))
            },
            reverse=True,
        )
        if not docto_ys:
            if pending_row is not None:
                continuation_lines = [
                    item
                    for item in sorted(left_lines, key=lambda item: -float(item["y"]))
                    if not bradesco_match_prefix(item.get("text"))
                ]
                if continuation_lines:
                    pending_row["left_lines"].extend(continuation_lines)
                    pending_row["detail_lines"].extend(continuation_lines)
                finalize_row(pending_row)
                pending_row = None
            continue
        rows: list[dict[str, object]] = []
        for y in docto_ys:
            docto = next(
                (
                    normalize_query(item["text"])
                    for item in right_lines
                    if abs(float(item["y"]) - y) < 0.8 and re.fullmatch(r"\d{7}", normalize_query(item.get("text")))
                ),
                "",
            )
            credit_text = next(
                (
                    normalize_query(item["text"])
                    for item in right_lines
                    if abs(float(item["y"]) - y) < 0.8
                    and re.fullmatch(r"[0-9\.]+,\d{2}", normalize_query(item.get("text")))
                    and float(item["x"]) < 410
                ),
                "",
            )
            debit_text = next(
                (
                    normalize_query(item["text"])
                    for item in right_lines
                    if abs(float(item["y"]) - y) < 0.8
                    and re.fullmatch(r"[0-9\.]+,\d{2}", normalize_query(item.get("text")))
                    and 410 <= float(item["x"]) < 500
                ),
                "",
            )
            rows.append(
                {
                    "y": y,
                    "docto": docto,
                    "credit_text": credit_text,
                    "debit_text": debit_text,
                    "prefix_line": None,
                    "left_lines": [],
                    "detail_lines": [],
                    "page_number": page_number,
                }
            )
        if not rows:
            continue
        highest_row_y = float(rows[0]["y"])
        leading_carry_lines: list[dict[str, object]] = []
        effective_left_lines: list[dict[str, object]] = []
        for item in sorted(left_lines, key=lambda item: -float(item["y"])):
            if float(item["y"]) > highest_row_y + 0.8 and not bradesco_match_prefix(item.get("text")):
                leading_carry_lines.append(item)
                continue
            effective_left_lines.append(item)
        if pending_row is not None:
            if leading_carry_lines:
                pending_row["left_lines"].extend(leading_carry_lines)
                pending_row["detail_lines"].extend(leading_carry_lines)
            finalize_row(pending_row)
            pending_row = None
        for item in effective_left_lines:
            prefix_text = bradesco_match_prefix(item.get("text"))
            if prefix_text:
                nearest_prefix_row = min(rows, key=lambda row: abs(float(row["y"]) - float(item["y"])))
                if abs(float(nearest_prefix_row["y"]) - float(item["y"])) < 1.2 and nearest_prefix_row.get("prefix_line") is None:
                    nearest_prefix_row["prefix_line"] = item
                    nearest_prefix_row["left_lines"].append(item)
                    continue
            rows_above = [row for row in rows if float(row["y"]) > float(item["y"]) + 0.8]
            if not rows_above:
                continue
            target_row = min(rows_above, key=lambda row: float(row["y"]) - float(item["y"]))
            target_row["left_lines"].append(item)
            target_row["detail_lines"].append(item)
        for row in rows:
            row["detail_lines"] = sorted(row.get("detail_lines") or [], key=lambda item: -float(item["y"]))
        date_index = 0
        current_date_token = carry_date_token
        for row in rows:
            while date_index < len(date_marks) and float(row["y"]) <= float(date_marks[date_index]["y"]) + 1.5:
                current_date_token = normalize_query(date_marks[date_index]["text"])
                date_index += 1
            row["date_token"] = current_date_token
        for row in rows[:-1]:
            finalize_row(row)
        pending_row = rows[-1]
    if pending_row is not None:
        finalize_row(pending_row)
    if not entries:
        entries = parse_bradesco_statement_text_entries(pages, period_start, period_end, period_start_br)
    if not entries:
        raise ValueError("Nao foi possivel localizar creditos de terceiros no extrato Bradesco.")
    return {
        "bank_name": "Bradesco",
        "statement_kind": "extrato_bradesco",
        "file_hash": hashlib.sha256(pdf_path.read_bytes()).hexdigest(),
        "period_start": period_start,
        "period_end": period_end,
        "entries": entries,
    }


def sicoob_block_page_number(block_rows: list[dict[str, object]]) -> int:
    for row in block_rows:
        page_number = moneyless_int(row.get("page_number"))
        if page_number:
            return page_number
    return 0


def sicoob_merge_block_lines(lines: list[str]) -> list[str]:
    merged: list[str] = []
    for line in lines:
        text = normalize_query(line)
        if not text:
            continue
        if merged and re.fullmatch(r"\*+", text):
            merged[-1] += text
        else:
            merged.append(text)
    return merged


def parse_sicoob_pix_block(
    block_rows: list[dict[str, object]],
    page_number: int,
    period_start: str,
    period_end: str,
    order: int,
) -> dict[str, object] | None:
    month_pattern = "|".join(MONTH_ABBR_PT.keys())
    cpf_re = re.compile(r"([*0-9]{3}\.[*0-9]{3}\.[*0-9]{3}-\s*[*0-9]{2})")
    cnpj_re = re.compile(r"([*0-9]{2}\.[*0-9]{3}\.[*0-9]{3}/[*0-9]{4}-\s*[*0-9]{2})")
    amount_re = re.compile(r"[+-]\s*R\$\s*[0-9\.]+,\d{2}")
    date_re = re.compile(rf"\b({month_pattern})\s+(\d{{1,2}})\b")

    normalized_lines = [normalize_query(row.get("text")) for row in block_rows]
    normalized_lines = [line for line in normalized_lines if line]
    if not normalized_lines:
        return None
    if not any("RECEBIMENTO PIX" in normalize_match_name(line) for line in normalized_lines):
        return None
    joined = " ".join(normalized_lines)
    amount_match = amount_re.search(joined)
    date_match = date_re.search(joined)
    if amount_match is None or date_match is None:
        return None

    cleaned_lines: list[str] = []
    found_receipt_start = False
    for line in normalized_lines:
        if "RECEBIMENTO PIX" in normalize_match_name(line):
            found_receipt_start = True
        if not found_receipt_start:
            continue
        cleaned = amount_re.sub(" ", line)
        cleaned = date_re.sub(" ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if cleaned:
            cleaned_lines.append(cleaned)
    merged_lines = sicoob_merge_block_lines(cleaned_lines)
    text_body = re.sub(r"\s+", " ", " ".join(merged_lines)).strip()
    if not text_body:
        return None
    document_match = cnpj_re.search(text_body) or cpf_re.search(text_body)
    document = re.sub(r"\s+", "", document_match.group(1)) if document_match else ""
    document_type = "cnpj" if "/" in document else "cpf" if document else ""
    donor = re.sub(r"(?i)\bRecebimento Pix\b", " ", text_body)
    if document_match:
        donor = donor.replace(document_match.group(0), " ")
    donor = re.sub(r"\s+", " ", donor).strip(" -")
    donor = re.sub(r"^(\d+[\./-]?)+\s+", "", donor).strip()
    if not donor:
        return None

    month_token = str(date_match.group(1))
    day_number = moneyless_int(date_match.group(2))
    received_on = infer_pdf_statement_date(month_token, day_number, period_start, period_end)
    amount_text = amount_match.group(0)
    amount = parse_money(amount_text)
    competence, competence_order = pix_competencia_for_date(received_on)
    code = pix_code_from_amount(amount)
    return {
        "order_in_file": order,
        "page_number": page_number,
        "received_on": received_on,
        "competencia": competence,
        "competencia_ordem": competence_order,
        "amount": amount,
        "amount_text": amount_text,
        "cent_code": code,
        "donor_name": donor,
        "donor_name_normalized": normalize_match_name(donor),
        "document_mask": document,
        "document_type": document_type,
        "raw_text": text_body,
    }


def parse_sicoob_pix_pdf(pdf_path: Path) -> dict[str, object]:
    pages = extract_pdf_pages(pdf_path)
    page_lines = extract_pdf_line_selections(pdf_path)
    full_text = "\n".join(pages)
    range_match = re.search(r"(\d{2}/\d{2}/\d{4})\s+a\s+(\d{2}/\d{2}/\d{4})", full_text)
    if not range_match:
        raise ValueError("Nao foi possivel identificar o periodo do extrato PIX.")
    period_start_br, period_end_br = range_match.groups()

    def br_to_iso(value: str) -> str:
        day, month, year = value.split("/")
        return f"{year}-{month}-{day}"

    period_start = br_to_iso(period_start_br)
    period_end = br_to_iso(period_end_br)
    entries: list[dict[str, object]] = []
    order = 0
    pending_block: list[dict[str, object]] = []
    separator_re = re.compile(r"(?i)\bPIX RECEBIDO\b")
    for page_number, page_rows in enumerate(page_lines, start=1):
        rows = sorted(page_rows, key=lambda item: (-float(item["y"]), float(item["x"])))
        for item in rows:
            text = normalize_query(item.get("text"))
            if not text:
                continue
            if re.fullmatch(r"\d{2}/\d{2}/\d{4}\s+a\s+\d{2}/\d{2}/\d{4}", text):
                continue
            row = dict(item)
            row["page_number"] = page_number
            if separator_re.search(text):
                parsed_entry = parse_sicoob_pix_block(
                    pending_block,
                    sicoob_block_page_number(pending_block) or page_number,
                    period_start,
                    period_end,
                    order,
                )
                if parsed_entry is not None:
                    entries.append(parsed_entry)
                    order += 1
                residue = re.sub(separator_re, " ", text)
                residue = re.sub(r"\s+", " ", residue).strip(" -")
                pending_block = []
                if residue:
                    residue_row = dict(row)
                    residue_row["text"] = residue
                    pending_block.append(residue_row)
                continue
            pending_block.append(row)
    if pending_block:
        parsed_entry = parse_sicoob_pix_block(
            pending_block,
            sicoob_block_page_number(pending_block),
            period_start,
            period_end,
            order,
        )
        if parsed_entry is not None:
            entries.append(parsed_entry)
    if not entries:
        raise ValueError("Nao foi possivel localizar movimentos PIX no PDF enviado.")
    return {
        "bank_name": "Sicoob",
        "period_start": period_start,
        "period_end": period_end,
        "entries": entries,
        "file_hash": hashlib.sha256(pdf_path.read_bytes()).hexdigest(),
    }


def searchable_person_id(value: object) -> int:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if not digits:
        return 0
    if len(digits) > 1 and digits.startswith("0"):
        return 0
    try:
        return int(digits)
    except ValueError:
        return 0


def clean_system_id(value: object) -> int:
    compact = str(value or "").strip().replace(" ", "")
    upper = compact.upper()
    if upper.startswith("ID-"):
        compact = compact[3:]
    digits = "".join(ch for ch in compact if ch.isdigit())
    if not digits:
        return 0
    try:
        return int(digits)
    except ValueError:
        return 0


def is_numeric_search(value: object) -> bool:
    compact = str(value or "").replace(" ", "").strip()
    return bool(compact) and compact.isdigit()


def is_system_id_search(value: object) -> bool:
    return str(value or "").strip().upper().startswith("ID-")


def is_member_code_search(value: object) -> bool:
    upper = str(value or "").strip().upper()
    return upper.startswith("MEM-") or upper.startswith("MBR-") or upper.startswith("NM-")


def member_photo_stem(person_id: int, cpf: object, name: object) -> str:
    name_slug = slugify_filename_text(name)
    cpf_digits = "".join(ch for ch in str(cpf or "") if ch.isdigit())
    stem = f"membro_{person_id:06d}__{name_slug}"
    if cpf_digits:
        stem += f"__cpf_{cpf_digits}"
    return stem


def member_photo_example_filename(person_id: int, cpf: object, name: object, extension: str = ".jpg") -> str:
    return f"{member_photo_stem(person_id, cpf, name)}{extension}"


def find_member_photo(person_id: int, cpf: object, name: object) -> Path | None:
    stem = member_photo_stem(person_id, cpf, name)
    for extension in PHOTO_EXTENSIONS:
        candidate = PHOTO_DIR / f"{stem}{extension}"
        if candidate.exists():
            return candidate
    fallback_matches = sorted(PHOTO_DIR.glob(f"membro_{person_id:06d}__*"))
    for match in fallback_matches:
        if match.suffix.lower() in PHOTO_EXTENSIONS and match.is_file():
            return match
    return None


def list_member_photo_variants(person_id: int) -> list[Path]:
    matches = []
    for match in sorted(PHOTO_DIR.glob(f"membro_{person_id:06d}__*")):
        if match.is_file() and match.suffix.lower() in PHOTO_EXTENSIONS:
            matches.append(match)
    return matches


def detect_photo_extension(filename: str, content_type: str = "") -> str:
    extension = Path(str(filename or "")).suffix.lower()
    if extension in PHOTO_EXTENSIONS:
        return extension
    by_content_type = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "image/heic": ".heic",
        "image/heif": ".heif",
    }
    guessed = by_content_type.get(str(content_type or "").lower(), "")
    if guessed in PHOTO_EXTENSIONS:
        return guessed
    raise ValueError("Formato de foto nao suportado. Use JPG, JPEG, PNG, WEBP, GIF, HEIC ou HEIF.")


def save_member_photo(
    person_id: int,
    cpf: object,
    name: object,
    filename: str,
    payload: bytes,
    content_type: str = "",
) -> Path:
    if not payload:
        raise ValueError("Selecione um arquivo de foto antes de enviar.")
    if len(payload) > 8 * 1024 * 1024:
        raise ValueError("A foto excede o limite de 8 MB.")
    PHOTO_DIR.mkdir(parents=True, exist_ok=True)
    extension = detect_photo_extension(filename, content_type)
    for existing in list_member_photo_variants(person_id):
        existing.unlink(missing_ok=True)
    target = PHOTO_DIR / member_photo_example_filename(person_id, cpf, name, extension)
    target.write_bytes(payload)
    return target


def rename_member_photo_files(person_id: int, old_cpf: object, new_cpf: object, old_name: object, new_name: object) -> None:
    old_stem = member_photo_stem(person_id, old_cpf, old_name)
    new_stem = member_photo_stem(person_id, new_cpf, new_name)
    if old_stem == new_stem:
        return
    variants = list_member_photo_variants(person_id)
    for index, current in enumerate(variants):
        target_name = f"{new_stem}{current.suffix.lower()}"
        target = PHOTO_DIR / target_name
        if index == 0:
            current.replace(target)
        else:
            current.unlink(missing_ok=True)


class PowerChurchDB:
    def __init__(self, path: Path):
        self.path = path
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.ensure_contribution_catalogs()
        self.ensure_pix_support()
        self.ensure_statement_support()

    def close(self) -> None:
        self.conn.close()

    def scalar(self, sql: str, params: tuple = ()) -> int:
        row = self.conn.execute(sql, params).fetchone()
        return moneyless_int(row[0] if row else 0)

    def ensure_contribution_catalogs(self) -> None:
        org_rows = self.conn.execute("SELECT id FROM organizacoes ORDER BY id").fetchall()
        org_ids = [moneyless_int(row["id"]) for row in org_rows] or [1]
        type_defaults = [
            ("DIZIMO", "Dizimo", 1, "receita_operacional"),
            ("OFERTA_IDENT", "Oferta Identificada", 1, "receita_operacional"),
            ("OFERTA_AVULSA", "Oferta Avulsa", 0, "receita_operacional"),
            ("DOACAO", "Doacao", 0, "receita_operacional"),
            ("CAMPANHA", "Campanha", 0, "receita_operacional"),
            ("ACAO_SOCIAL", "Acao Social", 0, "receita_operacional"),
            ("MISSOES_NACIONAIS", "Missoes Nacionais", 0, "receita_operacional"),
            ("MISSOES_MUNDIAIS", "Missoes Mundiais", 0, "receita_operacional"),
            ("MISSOES_IGREJA", "Missoes Igreja", 0, "receita_operacional"),
            ("JUVENTUDE", "Juventude", 0, "receita_operacional"),
            ("ADOLESCENTES", "Adolescentes", 0, "receita_operacional"),
            ("MUSICA", "Musica", 0, "receita_operacional"),
            ("HOMENS", "Homens", 0, "receita_operacional"),
            ("EMBAIXADORES", "Embaixadores", 0, "receita_operacional"),
            ("MENSAGEIRAS", "Mensageiras", 0, "receita_operacional"),
            ("CAMPANHA_ESPECIAL", "Campanha Especial", 0, "receita_operacional"),
            ("MULHERES", "Mulheres", 0, "receita_operacional"),
        ]
        receiving_defaults = [
            ("DINHEIRO", "Dinheiro"),
            ("PIX", "PIX"),
            ("TRANSFERENCIA", "Transferencia"),
            ("CARTAO", "Cartao"),
            ("CHEQUE", "Cheque"),
        ]
        for org_id in org_ids:
            for code, name, requires_person, revenue_kind in type_defaults:
                self.conn.execute(
                    """
                    INSERT OR IGNORE INTO tipos_contribuicao (
                        organizacao_id, codigo, nome, exige_pessoa, natureza_receita, ativo
                    ) VALUES (?, ?, ?, ?, ?, 1)
                    """,
                    (org_id, code, name, requires_person, revenue_kind),
                )
            for code, name in receiving_defaults:
                self.conn.execute(
                    """
                    INSERT OR IGNORE INTO formas_recebimento (
                        organizacao_id, codigo, nome, ativo
                    ) VALUES (?, ?, ?, 1)
                    """,
                    (org_id, code, name),
                )
        self.conn.commit()

    def column_exists(self, table_name: str, column_name: str) -> bool:
        rows = self.conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        return any(str(row["name"]) == column_name for row in rows)

    def ensure_pix_support(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pix_centavo_regras (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                organizacao_id INTEGER NOT NULL,
                codigo_centavos TEXT NOT NULL,
                nome_destinacao TEXT NOT NULL,
                tipo_contribuicao_id INTEGER,
                campanha_id INTEGER,
                plano_conta_id INTEGER,
                ativo INTEGER NOT NULL DEFAULT 1,
                observacoes TEXT,
                criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                atualizado_em TEXT,
                FOREIGN KEY (organizacao_id) REFERENCES organizacoes(id),
                FOREIGN KEY (tipo_contribuicao_id) REFERENCES tipos_contribuicao(id),
                FOREIGN KEY (campanha_id) REFERENCES campanhas(id),
                FOREIGN KEY (plano_conta_id) REFERENCES plano_contas(id)
            )
            """
        )
        self.conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_pix_centavo_regras_codigo ON pix_centavo_regras(organizacao_id, codigo_centavos)"
        )
        if not self.column_exists("pix_centavo_regras", "campanha_id"):
            self.conn.execute("ALTER TABLE pix_centavo_regras ADD COLUMN campanha_id INTEGER")
        if not self.column_exists("pix_centavo_regras", "plano_conta_id"):
            self.conn.execute("ALTER TABLE pix_centavo_regras ADD COLUMN plano_conta_id INTEGER")
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS contribuintes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                organizacao_id INTEGER NOT NULL,
                pessoa_id INTEGER,
                tipo TEXT NOT NULL DEFAULT 'pf',
                nome TEXT NOT NULL,
                nome_normalizado TEXT NOT NULL,
                documento_principal TEXT,
                documento_tipo TEXT,
                origem TEXT NOT NULL DEFAULT 'manual',
                qualidade TEXT NOT NULL DEFAULT 'doador',
                status TEXT NOT NULL DEFAULT 'ativo',
                observacoes TEXT,
                ativo INTEGER NOT NULL DEFAULT 1,
                criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                atualizado_em TEXT,
                FOREIGN KEY (organizacao_id) REFERENCES organizacoes(id),
                FOREIGN KEY (pessoa_id) REFERENCES pessoas(id)
            )
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_contribuintes_org_nome ON contribuintes(organizacao_id, nome_normalizado, ativo)"
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS contribuintes_identificadores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                organizacao_id INTEGER NOT NULL,
                pessoa_id INTEGER,
                contribuinte_id INTEGER,
                tipo TEXT NOT NULL,
                valor TEXT NOT NULL,
                principal INTEGER NOT NULL DEFAULT 0,
                ativo INTEGER NOT NULL DEFAULT 1,
                observacoes TEXT,
                criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                atualizado_em TEXT,
                FOREIGN KEY (organizacao_id) REFERENCES organizacoes(id),
                FOREIGN KEY (pessoa_id) REFERENCES pessoas(id),
                FOREIGN KEY (contribuinte_id) REFERENCES contribuintes(id)
            )
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_contribuintes_identificadores_lookup ON contribuintes_identificadores(organizacao_id, tipo, valor, ativo)"
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pix_lotes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                organizacao_id INTEGER NOT NULL,
                banco TEXT NOT NULL DEFAULT 'Sicoob',
                nome_arquivo TEXT NOT NULL,
                caminho_arquivo TEXT,
                hash_arquivo TEXT,
                periodo_inicio TEXT,
                periodo_fim TEXT,
                total_movimentos INTEGER NOT NULL DEFAULT 0,
                total_valor REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'carregado',
                observacoes TEXT,
                criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                atualizado_em TEXT,
                FOREIGN KEY (organizacao_id) REFERENCES organizacoes(id)
            )
            """
        )
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_pix_lotes_hash ON pix_lotes(organizacao_id, hash_arquivo)")
        self.conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_pix_lotes_hash ON pix_lotes(organizacao_id, hash_arquivo)")
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pix_movimentos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lote_id INTEGER NOT NULL,
                organizacao_id INTEGER NOT NULL,
                pagina INTEGER,
                ordem_no_lote INTEGER NOT NULL,
                data_recebimento TEXT,
                competencia TEXT,
                competencia_ordem INTEGER,
                valor REAL NOT NULL,
                codigo_centavos TEXT NOT NULL,
                nome_origem TEXT NOT NULL,
                nome_normalizado TEXT NOT NULL,
                documento_mascarado TEXT,
                documento_tipo TEXT,
                tipo_sugerido TEXT NOT NULL DEFAULT 'dizimo',
                regra_id INTEGER,
                confidence TEXT NOT NULL DEFAULT 'sem_match',
                match_score REAL NOT NULL DEFAULT 0,
                suggested_person_id INTEGER,
                suggested_contribuinte_id INTEGER,
                resolved_person_id INTEGER,
                resolved_contribuinte_id INTEGER,
                resolved_tipo_contribuicao_id INTEGER,
                association_reviewed INTEGER NOT NULL DEFAULT 0,
                review_status TEXT NOT NULL DEFAULT 'pendente',
                review_notes TEXT,
                raw_text TEXT,
                fingerprint TEXT NOT NULL,
                signature_global TEXT,
                duplicate_movement_id INTEGER,
                duplicate_contribution_id INTEGER,
                duplicate_reason TEXT,
                imported_contribution_id INTEGER,
                ativo INTEGER NOT NULL DEFAULT 1,
                criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                atualizado_em TEXT,
                FOREIGN KEY (lote_id) REFERENCES pix_lotes(id),
                FOREIGN KEY (organizacao_id) REFERENCES organizacoes(id),
                FOREIGN KEY (regra_id) REFERENCES pix_centavo_regras(id),
                FOREIGN KEY (suggested_person_id) REFERENCES pessoas(id),
                FOREIGN KEY (suggested_contribuinte_id) REFERENCES contribuintes(id),
                FOREIGN KEY (resolved_person_id) REFERENCES pessoas(id),
                FOREIGN KEY (resolved_contribuinte_id) REFERENCES contribuintes(id),
                FOREIGN KEY (resolved_tipo_contribuicao_id) REFERENCES tipos_contribuicao(id),
                FOREIGN KEY (duplicate_movement_id) REFERENCES pix_movimentos(id),
                FOREIGN KEY (duplicate_contribution_id) REFERENCES contribuicoes(id),
                FOREIGN KEY (imported_contribution_id) REFERENCES contribuicoes(id)
            )
            """
        )
        self.conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_pix_movimentos_fingerprint ON pix_movimentos(lote_id, fingerprint)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_pix_movimentos_status ON pix_movimentos(lote_id, review_status, confidence)")
        if not self.column_exists("pix_movimentos", "signature_global"):
            self.conn.execute("ALTER TABLE pix_movimentos ADD COLUMN signature_global TEXT")
        if not self.column_exists("pix_movimentos", "duplicate_movement_id"):
            self.conn.execute("ALTER TABLE pix_movimentos ADD COLUMN duplicate_movement_id INTEGER")
        if not self.column_exists("pix_movimentos", "duplicate_contribution_id"):
            self.conn.execute("ALTER TABLE pix_movimentos ADD COLUMN duplicate_contribution_id INTEGER")
        if not self.column_exists("pix_movimentos", "duplicate_reason"):
            self.conn.execute("ALTER TABLE pix_movimentos ADD COLUMN duplicate_reason TEXT")
        if not self.column_exists("pix_movimentos", "association_reviewed"):
            self.conn.execute("ALTER TABLE pix_movimentos ADD COLUMN association_reviewed INTEGER NOT NULL DEFAULT 0")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_pix_movimentos_signature ON pix_movimentos(organizacao_id, signature_global, ativo)")
        if not self.column_exists("contribuicoes", "contribuinte_id"):
            self.conn.execute("ALTER TABLE contribuicoes ADD COLUMN contribuinte_id INTEGER")
        if not self.column_exists("contribuicoes", "pix_movimento_id"):
            self.conn.execute("ALTER TABLE contribuicoes ADD COLUMN pix_movimento_id INTEGER")
        if not self.column_exists("contribuicoes", "status_operacional"):
            self.conn.execute("ALTER TABLE contribuicoes ADD COLUMN status_operacional TEXT")
        self.conn.execute(
            """
            UPDATE contribuicoes
            SET status_operacional = 'regular'
            WHERE status_operacional IS NULL OR TRIM(status_operacional) = ''
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_contribuicoes_contribuinte ON contribuicoes(organizacao_id, contribuinte_id, data_recebimento)"
        )
        self.conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_contribuicoes_pix_movimento ON contribuicoes(pix_movimento_id) WHERE pix_movimento_id IS NOT NULL"
        )
        self.sync_person_identifiers()
        self.seed_pix_rules()
        self.backfill_pix_signatures()
        self.ensure_pix_financial_entries()
        self.conn.commit()

    def ensure_statement_support(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS extrato_lotes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                organizacao_id INTEGER NOT NULL,
                banco TEXT NOT NULL,
                layout_codigo TEXT NOT NULL,
                nome_arquivo TEXT NOT NULL,
                caminho_arquivo TEXT,
                hash_arquivo TEXT,
                periodo_inicio TEXT,
                periodo_fim TEXT,
                total_movimentos INTEGER NOT NULL DEFAULT 0,
                total_valor REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'carregado',
                observacoes TEXT,
                criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                atualizado_em TEXT,
                FOREIGN KEY (organizacao_id) REFERENCES organizacoes(id)
            )
            """
        )
        self.conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_extrato_lotes_hash ON extrato_lotes(organizacao_id, layout_codigo, hash_arquivo)"
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS extrato_movimentos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lote_id INTEGER NOT NULL,
                organizacao_id INTEGER NOT NULL,
                pagina INTEGER,
                ordem_no_lote INTEGER NOT NULL,
                data_movimento TEXT,
                competencia TEXT,
                competencia_ordem INTEGER,
                valor REAL NOT NULL,
                codigo_centavos TEXT,
                movement_kind TEXT NOT NULL,
                receiving_code TEXT NOT NULL,
                bank_document TEXT,
                prefixo_historico TEXT,
                nome_origem TEXT,
                nome_normalizado TEXT,
                origin_label TEXT,
                tipo_sugerido TEXT NOT NULL DEFAULT 'dizimo',
                regra_id INTEGER,
                confidence TEXT NOT NULL DEFAULT 'sem_match',
                match_score REAL NOT NULL DEFAULT 0,
                suggested_person_id INTEGER,
                suggested_contribuinte_id INTEGER,
                resolved_person_id INTEGER,
                resolved_contribuinte_id INTEGER,
                resolved_tipo_contribuicao_id INTEGER,
                association_reviewed INTEGER NOT NULL DEFAULT 0,
                review_status TEXT NOT NULL DEFAULT 'pendente',
                review_notes TEXT,
                raw_text TEXT,
                fingerprint TEXT NOT NULL,
                signature_global TEXT,
                duplicate_movement_id INTEGER,
                duplicate_contribution_id INTEGER,
                duplicate_reason TEXT,
                imported_contribution_id INTEGER,
                ativo INTEGER NOT NULL DEFAULT 1,
                criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                atualizado_em TEXT,
                FOREIGN KEY (lote_id) REFERENCES extrato_lotes(id),
                FOREIGN KEY (organizacao_id) REFERENCES organizacoes(id),
                FOREIGN KEY (suggested_person_id) REFERENCES pessoas(id),
                FOREIGN KEY (suggested_contribuinte_id) REFERENCES contribuintes(id),
                FOREIGN KEY (resolved_person_id) REFERENCES pessoas(id),
                FOREIGN KEY (resolved_contribuinte_id) REFERENCES contribuintes(id),
                FOREIGN KEY (regra_id) REFERENCES pix_centavo_regras(id),
                FOREIGN KEY (resolved_tipo_contribuicao_id) REFERENCES tipos_contribuicao(id),
                FOREIGN KEY (duplicate_movement_id) REFERENCES extrato_movimentos(id),
                FOREIGN KEY (duplicate_contribution_id) REFERENCES contribuicoes(id),
                FOREIGN KEY (imported_contribution_id) REFERENCES contribuicoes(id)
            )
            """
        )
        self.conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_extrato_movimentos_fingerprint ON extrato_movimentos(lote_id, fingerprint)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_extrato_movimentos_status ON extrato_movimentos(lote_id, review_status, confidence)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_extrato_movimentos_signature ON extrato_movimentos(organizacao_id, signature_global, ativo)"
        )
        if not self.column_exists("extrato_movimentos", "codigo_centavos"):
            self.conn.execute("ALTER TABLE extrato_movimentos ADD COLUMN codigo_centavos TEXT")
        if not self.column_exists("extrato_movimentos", "tipo_sugerido"):
            self.conn.execute("ALTER TABLE extrato_movimentos ADD COLUMN tipo_sugerido TEXT NOT NULL DEFAULT 'dizimo'")
        if not self.column_exists("extrato_movimentos", "regra_id"):
            self.conn.execute("ALTER TABLE extrato_movimentos ADD COLUMN regra_id INTEGER")
        if not self.column_exists("extrato_movimentos", "resolved_tipo_contribuicao_id"):
            self.conn.execute("ALTER TABLE extrato_movimentos ADD COLUMN resolved_tipo_contribuicao_id INTEGER")
        if not self.column_exists("extrato_movimentos", "association_reviewed"):
            self.conn.execute("ALTER TABLE extrato_movimentos ADD COLUMN association_reviewed INTEGER NOT NULL DEFAULT 0")
        if not self.column_exists("contribuicoes", "extrato_movimento_id"):
            self.conn.execute("ALTER TABLE contribuicoes ADD COLUMN extrato_movimento_id INTEGER")
        self.conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_contribuicoes_extrato_movimento ON contribuicoes(extrato_movimento_id) WHERE extrato_movimento_id IS NOT NULL"
        )
        self.backfill_statement_cent_rules()
        self.backfill_statement_signatures()
        self.ensure_statement_financial_entries()
        self.conn.commit()

    def sync_person_identifiers(self) -> None:
        rows = self.conn.execute(
            """
            SELECT id, organizacao_id, cpf
            FROM pessoas
            WHERE ativo = 1 AND cpf IS NOT NULL AND TRIM(cpf) <> ''
            """
        ).fetchall()
        for row in rows:
            cpf_digits = "".join(ch for ch in str(row["cpf"] or "") if ch.isdigit())
            if len(cpf_digits) != 11:
                continue
            existing = self.conn.execute(
                """
                SELECT 1
                FROM contribuintes_identificadores
                WHERE pessoa_id = ? AND tipo = 'cpf' AND valor = ? AND ativo = 1
                LIMIT 1
                """,
                (row["id"], cpf_digits),
            ).fetchone()
            if existing is None:
                self.conn.execute(
                    """
                    INSERT INTO contribuintes_identificadores (
                        organizacao_id, pessoa_id, contribuinte_id, tipo, valor, principal, ativo, observacoes
                    ) VALUES (?, ?, NULL, 'cpf', ?, 1, 1, 'Sincronizado da ficha da pessoa.')
                    """,
                    (row["organizacao_id"], row["id"], cpf_digits),
                )

    def seed_pix_rules(self) -> None:
        org_rows = self.conn.execute("SELECT id FROM organizacoes ORDER BY id").fetchall()
        org_ids = [moneyless_int(row["id"]) for row in org_rows] or [1]
        for org_id in org_ids:
            type_map = {
                str(row["codigo"]): moneyless_int(row["id"])
                for row in self.conn.execute(
                    "SELECT id, codigo FROM tipos_contribuicao WHERE organizacao_id = ? AND ativo = 1",
                    (org_id,),
                ).fetchall()
            }
            for code, label, type_code in PIX_RULE_DEFAULTS:
                self.conn.execute(
                    """
                    INSERT OR IGNORE INTO pix_centavo_regras (
                        organizacao_id, codigo_centavos, nome_destinacao, tipo_contribuicao_id,
                        campanha_id, plano_conta_id, ativo, observacoes
                    ) VALUES (?, ?, ?, ?, NULL, NULL, 1, 'Regra PIX padrao do sistema.')
                    """,
                    (org_id, code, label, type_map.get(type_code)),
                )
        self.sync_cent_rule_destinations()

    def backfill_pix_signatures(self) -> None:
        rows = self.conn.execute(
            """
            SELECT id, data_recebimento, valor, nome_normalizado, documento_mascarado, documento_tipo, raw_text
            FROM pix_movimentos
            WHERE signature_global IS NULL OR TRIM(COALESCE(signature_global, '')) = ''
            """
        ).fetchall()
        for row in rows:
            signature = pix_global_signature(
                row["data_recebimento"],
                row["valor"],
                row["nome_normalizado"],
                row["documento_mascarado"],
                row["documento_tipo"],
                row["raw_text"],
            )
            self.conn.execute(
                """
                UPDATE pix_movimentos
                SET signature_global = ?
                WHERE id = ?
                """,
                (signature, row["id"]),
            )

    def default_organization_id(self) -> int:
        row = self.conn.execute("SELECT id FROM organizacoes ORDER BY id LIMIT 1").fetchone()
        return moneyless_int(row["id"] if row else 1)

    def ensure_cent_rule_plan_account(self, organization_id: int, code: str, label: str) -> int:
        account_code = cent_rule_plan_account_code(code)
        account = self.conn.execute(
            """
            SELECT *
            FROM plano_contas
            WHERE organizacao_id = ? AND codigo = ?
            LIMIT 1
            """,
            (organization_id, account_code),
        ).fetchone()
        if account is None:
            cursor = self.conn.execute(
                """
                INSERT INTO plano_contas (
                    organizacao_id, codigo, nome, pai_id, nivel, tipo, grupo_estrategico,
                    aceita_lancamento, ativo, atualizado_em
                ) VALUES (?, ?, ?, NULL, 1, 'receita', 'centavos', 1, 1, CURRENT_TIMESTAMP)
                """,
                (organization_id, account_code, label),
            )
            return moneyless_int(cursor.lastrowid)
        account_id = moneyless_int(account["id"])
        if normalize_query(account["nome"]) != label or not moneyless_int(account["ativo"]):
            self.conn.execute(
                """
                UPDATE plano_contas
                SET nome = ?, tipo = 'receita', grupo_estrategico = 'centavos',
                    aceita_lancamento = 1, ativo = 1, atualizado_em = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (label, account_id),
            )
        return account_id

    def ensure_cent_rule_campaign(self, organization_id: int, label: str, plan_account_id: int, current_campaign_id: int = 0) -> int:
        campaign = None
        if current_campaign_id:
            campaign = self.conn.execute(
                """
                SELECT *
                FROM campanhas
                WHERE id = ? AND organizacao_id = ?
                LIMIT 1
                """,
                (current_campaign_id, organization_id),
            ).fetchone()
        if campaign is None and plan_account_id:
            campaign = self.conn.execute(
                """
                SELECT *
                FROM campanhas
                WHERE organizacao_id = ? AND plano_conta_id = ?
                ORDER BY id
                LIMIT 1
                """,
                (organization_id, plan_account_id),
            ).fetchone()
        if campaign is None:
            cursor = self.conn.execute(
                """
                INSERT INTO campanhas (
                    organizacao_id, nome, descricao, status, plano_conta_id, atualizado_em
                ) VALUES (?, ?, 'Campanha criada automaticamente pela regra de centavos.', 'ativa', ?, CURRENT_TIMESTAMP)
                """,
                (organization_id, label, plan_account_id or None),
            )
            return moneyless_int(cursor.lastrowid)
        campaign_id = moneyless_int(campaign["id"])
        if (
            normalize_query(campaign["nome"]) != label
            or moneyless_int(campaign["plano_conta_id"]) != moneyless_int(plan_account_id)
            or normalize_query(campaign["status"]) != "ativa"
        ):
            self.conn.execute(
                """
                UPDATE campanhas
                SET nome = ?, status = 'ativa', plano_conta_id = ?, atualizado_em = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (label, plan_account_id or None, campaign_id),
            )
        return campaign_id

    def ensure_cent_rule_type(
        self,
        organization_id: int,
        code: str,
        label: str,
        plan_account_id: int,
        requested_type_id: int = 0,
    ) -> int:
        selected = None
        if requested_type_id:
            selected = self.conn.execute(
                """
                SELECT *
                FROM tipos_contribuicao
                WHERE id = ? AND organizacao_id = ? AND ativo = 1
                LIMIT 1
                """,
                (requested_type_id, organization_id),
            ).fetchone()
        if selected is not None:
            selected_code = normalize_query(selected["codigo"]).upper()
            if core_designations.cent_rule_type_is_system_managed(selected_code, code, PIX_RULE_DEFAULT_TYPE_CODES):
                self.conn.execute(
                    """
                    UPDATE tipos_contribuicao
                    SET nome = ?, plano_conta_id = ?, ativo = 1, atualizado_em = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (label, plan_account_id or None, moneyless_int(selected["id"])),
                )
            return moneyless_int(selected["id"])

        type_code = cent_rule_type_code(code)
        existing = self.conn.execute(
            """
            SELECT *
            FROM tipos_contribuicao
            WHERE organizacao_id = ? AND codigo = ?
            LIMIT 1
            """,
            (organization_id, type_code),
        ).fetchone()
        if existing is None:
            cursor = self.conn.execute(
                """
                INSERT INTO tipos_contribuicao (
                    organizacao_id, codigo, nome, exige_pessoa, natureza_receita, plano_conta_id,
                    ativo, atualizado_em
                ) VALUES (?, ?, ?, 0, 'receita_operacional', ?, 1, CURRENT_TIMESTAMP)
                """,
                (organization_id, type_code, label, plan_account_id or None),
            )
            return moneyless_int(cursor.lastrowid)
        type_id = moneyless_int(existing["id"])
        self.conn.execute(
            """
            UPDATE tipos_contribuicao
            SET nome = ?, plano_conta_id = ?, ativo = 1, atualizado_em = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (label, plan_account_id or None, type_id),
        )
        return type_id

    def sync_cent_rule_destination(self, rule_id: int, requested_type_id: int = 0) -> None:
        rule = self.conn.execute("SELECT * FROM pix_centavo_regras WHERE id = ?", (rule_id,)).fetchone()
        if rule is None:
            return
        organization_id = moneyless_int(rule["organizacao_id"])
        code = str(rule["codigo_centavos"] or "").zfill(2)
        label = normalize_query(rule["nome_destinacao"])
        if not organization_id or not code or not label:
            return
        plan_account_id = self.ensure_cent_rule_plan_account(organization_id, code, label)
        campaign_id = self.ensure_cent_rule_campaign(
            organization_id,
            label,
            plan_account_id,
            current_campaign_id=moneyless_int(rule["campanha_id"]),
        )
        type_id = self.ensure_cent_rule_type(
            organization_id,
            code,
            label,
            plan_account_id,
            requested_type_id=requested_type_id or moneyless_int(rule["tipo_contribuicao_id"]),
        )
        self.conn.execute(
            """
            UPDATE pix_centavo_regras
            SET tipo_contribuicao_id = ?, campanha_id = ?, plano_conta_id = ?, atualizado_em = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (type_id or None, campaign_id or None, plan_account_id or None, rule_id),
        )

    def sync_cent_rule_destinations(self) -> None:
        rows = self.conn.execute(
            """
            SELECT id
            FROM pix_centavo_regras
            WHERE ativo = 1
            ORDER BY codigo_centavos
            """
        ).fetchall()
        for row in rows:
            self.sync_cent_rule_destination(moneyless_int(row["id"]))

    def flag_existing_contributions_for_cent_rule_audit(self, rule_id: int, previous_type_id: int = 0) -> int:
        if not rule_id:
            return 0
        rule = self.conn.execute("SELECT * FROM pix_centavo_regras WHERE id = ?", (rule_id,)).fetchone()
        if rule is None:
            return 0
        note = (
            f"Regra de centavos {rule['codigo_centavos']} / {rule['nome_destinacao']} foi criada ou alterada. "
            "Confirmar novamente a destinacao para preservar a auditoria das associacoes ja feitas."
        )
        updated = 0
        pix_rows = self.conn.execute(
            """
            SELECT m.*, c.pessoa_id AS contribution_person_id
            FROM pix_movimentos m
            LEFT JOIN contribuicoes c ON c.id = m.imported_contribution_id
            WHERE m.ativo = 1
              AND m.regra_id = ?
              AND m.imported_contribution_id IS NOT NULL
              AND m.review_status NOT IN ('ignorado', 'revisar_duplicidade')
            ORDER BY m.id
            """,
            (rule_id,),
        ).fetchall()
        for row in pix_rows:
            current_status = normalize_query(row["review_status"])
            desired_status = current_status if current_status == "revisar_pessoa" else "revisar_destinacao"
            current_type_id = moneyless_int(row["resolved_tipo_contribuicao_id"])
            next_type_id = None if previous_type_id and current_type_id == previous_type_id else current_type_id or None
            before = self.movement_snapshot(moneyless_int(row["id"]))
            self.conn.execute(
                """
                UPDATE pix_movimentos
                SET review_status = ?, resolved_tipo_contribuicao_id = ?,
                    review_notes = ?, atualizado_em = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    desired_status,
                    next_type_id,
                    merge_statement_review_notes(row["review_notes"], note),
                    moneyless_int(row["id"]),
                ),
            )
            contribution_person_id = (
                moneyless_int(row["contribution_person_id"])
                or moneyless_int(row["resolved_person_id"])
                or (0 if moneyless_int(row["association_reviewed"]) else moneyless_int(row["suggested_person_id"]))
            )
            contribution_status = "classificacao_pendente" if desired_status == "revisar_destinacao" and contribution_person_id else "sem_associacao"
            self.conn.execute(
                """
                UPDATE contribuicoes
                SET status_operacional = ?, atualizado_em = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (contribution_status, moneyless_int(row["imported_contribution_id"])),
            )
            after = self.movement_snapshot(moneyless_int(row["id"]))
            self.write_audit_log(
                moneyless_int(row["organizacao_id"]),
                "reenviar_destinacao_pix_por_regra_centavos",
                "pix_movimentos",
                moneyless_int(row["id"]),
                before,
                after,
            )
            updated += 1
        statement_rows = self.conn.execute(
            """
            SELECT m.*, c.pessoa_id AS contribution_person_id
            FROM extrato_movimentos m
            LEFT JOIN contribuicoes c ON c.id = m.imported_contribution_id
            WHERE m.ativo = 1
              AND m.regra_id = ?
              AND m.imported_contribution_id IS NOT NULL
              AND m.review_status NOT IN ('ignorado', 'revisar_duplicidade')
            ORDER BY m.id
            """,
            (rule_id,),
        ).fetchall()
        for row in statement_rows:
            current_status = normalize_query(row["review_status"])
            desired_status = current_status if current_status == "revisar_pessoa" else "revisar_destinacao"
            current_type_id = moneyless_int(row["resolved_tipo_contribuicao_id"])
            next_type_id = None if previous_type_id and current_type_id == previous_type_id else current_type_id or None
            before = self.statement_movement_snapshot(moneyless_int(row["id"]))
            self.conn.execute(
                """
                UPDATE extrato_movimentos
                SET review_status = ?, resolved_tipo_contribuicao_id = ?,
                    review_notes = ?, atualizado_em = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    desired_status,
                    next_type_id,
                    merge_statement_review_notes(row["review_notes"], note),
                    moneyless_int(row["id"]),
                ),
            )
            contribution_person_id = (
                moneyless_int(row["contribution_person_id"])
                or moneyless_int(row["resolved_person_id"])
                or (0 if moneyless_int(row["association_reviewed"]) else moneyless_int(row["suggested_person_id"]))
            )
            contribution_status = "classificacao_pendente" if desired_status == "revisar_destinacao" and contribution_person_id else "sem_associacao"
            self.conn.execute(
                """
                UPDATE contribuicoes
                SET status_operacional = ?, atualizado_em = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (contribution_status, moneyless_int(row["imported_contribution_id"])),
            )
            after = self.statement_movement_snapshot(moneyless_int(row["id"]))
            self.write_audit_log(
                moneyless_int(row["organizacao_id"]),
                "reenviar_destinacao_extrato_por_regra_centavos",
                "extrato_movimentos",
                moneyless_int(row["id"]),
                before,
                after,
            )
            updated += 1
        return updated

    def pix_rules(self, organization_id: int = 0) -> list[sqlite3.Row]:
        organization_id = organization_id or self.default_organization_id()
        return self.conn.execute(
            """
            SELECT
                r.*,
                tc.nome AS tipo_nome,
                tc.codigo AS tipo_codigo,
                ca.nome AS campanha_nome,
                pc.codigo AS plano_conta_codigo,
                pc.nome AS plano_conta_nome
            FROM pix_centavo_regras r
            LEFT JOIN tipos_contribuicao tc ON tc.id = r.tipo_contribuicao_id
            LEFT JOIN campanhas ca ON ca.id = r.campanha_id
            LEFT JOIN plano_contas pc ON pc.id = COALESCE(r.plano_conta_id, ca.plano_conta_id, tc.plano_conta_id)
            WHERE r.organizacao_id = ?
            ORDER BY r.codigo_centavos
            """,
            (organization_id,),
        ).fetchall()

    def save_pix_rule_from_form(self, form: dict[str, list[str]]) -> int:
        organization_id = self.default_organization_id()
        rule_id = moneyless_int(form.get("rule_id", ["0"])[0])
        code = "".join(ch for ch in first_form_value(form, "codigo_centavos") if ch.isdigit())[-2:]
        code = code.zfill(2) if code else ""
        if not code:
            raise ValueError("Informe o codigo de centavos com dois digitos.")
        label = first_form_value(form, "nome_destinacao")
        if not label:
            raise ValueError("Informe o nome da destinacao.")
        type_raw = first_form_value(form, "tipo_contribuicao_id")
        requested_type_id = moneyless_int(type_raw)
        use_auto_type = not type_raw
        active = 0 if first_form_value(form, "ativo") == "0" else 1
        before = None
        if rule_id:
            current = self.conn.execute("SELECT * FROM pix_centavo_regras WHERE id = ?", (rule_id,)).fetchone()
            before = dict(current) if current else None
            self.conn.execute(
                """
                UPDATE pix_centavo_regras
                SET codigo_centavos = ?, nome_destinacao = ?, ativo = ?, atualizado_em = CURRENT_TIMESTAMP
                WHERE id = ? AND organizacao_id = ?
                """,
                (code, label, active, rule_id, organization_id),
            )
            saved_id = rule_id
        else:
            cursor = self.conn.execute(
                """
                INSERT INTO pix_centavo_regras (
                    organizacao_id, codigo_centavos, nome_destinacao, tipo_contribuicao_id,
                    campanha_id, plano_conta_id, ativo, atualizado_em
                ) VALUES (?, ?, ?, NULL, NULL, NULL, ?, CURRENT_TIMESTAMP)
                """,
                (organization_id, code, label, active),
            )
            saved_id = moneyless_int(cursor.lastrowid)
        if use_auto_type:
            self.conn.execute(
                "UPDATE pix_centavo_regras SET tipo_contribuicao_id = NULL WHERE id = ?",
                (saved_id,),
            )
        self.sync_cent_rule_destination(saved_id, requested_type_id=requested_type_id)
        previous_type_id = moneyless_int(before.get("tipo_contribuicao_id") if before else 0)
        after_row = self.conn.execute("SELECT * FROM pix_centavo_regras WHERE id = ?", (saved_id,)).fetchone()
        destination_changed = False
        if before and after_row:
            destination_changed = any(
                [
                    normalize_query(before.get("codigo_centavos")) != normalize_query(after_row["codigo_centavos"]),
                    normalize_query(before.get("nome_destinacao")) != normalize_query(after_row["nome_destinacao"]),
                    moneyless_int(before.get("tipo_contribuicao_id")) != moneyless_int(after_row["tipo_contribuicao_id"]),
                    moneyless_int(before.get("campanha_id")) != moneyless_int(after_row["campanha_id"]),
                    moneyless_int(before.get("plano_conta_id")) != moneyless_int(after_row["plano_conta_id"]),
                ]
            )
        affected_contributions = (
            self.flag_existing_contributions_for_cent_rule_audit(saved_id, previous_type_id=previous_type_id)
            if destination_changed
            else 0
        )
        after_payload = dict(after_row) if after_row else None
        if after_payload is not None:
            after_payload["contribuicoes_reenviadas_para_auditoria"] = affected_contributions
        self.write_audit_log(organization_id, "salvar_regra_pix", "pix_centavo_regras", saved_id, before, after_payload)
        self.conn.commit()
        return saved_id

    def contribution_type_by_code(self, organization_id: int, code: str) -> sqlite3.Row | None:
        return self.conn.execute(
            """
            SELECT *
            FROM tipos_contribuicao
            WHERE organizacao_id = ? AND codigo = ? AND ativo = 1
            LIMIT 1
            """,
            (organization_id, code),
        ).fetchone()

    def cent_rule_campaign_id(self, rule_id: int) -> int | None:
        if not rule_id:
            return None
        row = self.conn.execute(
            """
            SELECT campanha_id
            FROM pix_centavo_regras
            WHERE id = ? AND ativo = 1
            LIMIT 1
            """,
            (rule_id,),
        ).fetchone()
        return moneyless_int(row["campanha_id"] if row else 0) or None

    def cent_rule_type_id(self, rule_id: int) -> int:
        if not rule_id:
            return 0
        row = self.conn.execute(
            """
            SELECT tipo_contribuicao_id
            FROM pix_centavo_regras
            WHERE id = ? AND ativo = 1
            LIMIT 1
            """,
            (rule_id,),
        ).fetchone()
        return moneyless_int(row["tipo_contribuicao_id"] if row else 0)

    def cent_rule_campaign_id_for_type(self, rule_id: int, type_id: int) -> int | None:
        rule_type_id = self.cent_rule_type_id(rule_id)
        if not rule_type_id:
            return None
        if moneyless_int(type_id) and moneyless_int(type_id) != rule_type_id:
            return None
        return self.cent_rule_campaign_id(rule_id)

    def cent_rule_override_decision(
        self,
        organization_id: int,
        rule_id: int,
        selected_type_id: int,
        current_type_suggestion: object = "",
    ) -> tuple[int | None, str, bool]:
        rule_id = moneyless_int(rule_id)
        selected_type_id = moneyless_int(selected_type_id)
        if not rule_id:
            return None, normalize_query(current_type_suggestion) or "dizimo", False
        rule_type_id = self.cent_rule_type_id(rule_id)
        if rule_type_id and selected_type_id and selected_type_id != rule_type_id:
            default_type_id = self.pix_default_type_id(organization_id)
            suggestion = "dizimo" if selected_type_id == default_type_id else "manual"
            return None, suggestion, True
        return rule_id, "destinacao_especial", False

    def pix_default_type_id(self, organization_id: int) -> int:
        row = self.contribution_type_by_code(organization_id, "DIZIMO")
        return moneyless_int(row["id"] if row else 0)

    def pix_receiving_form_id(self, organization_id: int) -> int:
        row = self.conn.execute(
            """
            SELECT id
            FROM formas_recebimento
            WHERE organizacao_id = ? AND codigo = 'PIX' AND ativo = 1
            LIMIT 1
            """,
            (organization_id,),
        ).fetchone()
        return moneyless_int(row["id"] if row else 0)

    def upsert_contributor(
        self,
        organization_id: int,
        name: str,
        contributor_kind: str,
        document_value: str = "",
        document_type: str = "",
        person_id: int = 0,
        source: str = "pix",
        quality: str = "doador",
    ) -> int:
        clean_name = normalize_contributor_source_name(name, source)
        if contributor_name_is_noise(clean_name, source):
            return 0
        normalized_name = normalize_match_name(clean_name)
        existing = self.conn.execute(
            """
            SELECT *
            FROM contribuintes
            WHERE organizacao_id = ? AND nome_normalizado = ? AND COALESCE(documento_principal, '') = ? AND COALESCE(documento_tipo, '') = ? AND ativo = 1
            ORDER BY id
            LIMIT 1
            """,
            (organization_id, normalized_name, document_value, document_type),
        ).fetchone()
        if existing is None:
            cursor = self.conn.execute(
                """
                INSERT INTO contribuintes (
                    organizacao_id, pessoa_id, tipo, nome, nome_normalizado, documento_principal,
                    documento_tipo, origem, qualidade, status, observacoes, ativo, atualizado_em
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ativo', NULL, 1, CURRENT_TIMESTAMP)
                """,
                (
                    organization_id,
                    person_id or None,
                    contributor_kind,
                    clean_name,
                    normalized_name,
                    document_value or None,
                    document_type or None,
                    source,
                    quality,
                ),
            )
            contributor_id = moneyless_int(cursor.lastrowid)
        else:
            contributor_id = moneyless_int(existing["id"])
            updates: list[str] = ["atualizado_em = CURRENT_TIMESTAMP"]
            params: list[object] = []
            if person_id and not moneyless_int(existing["pessoa_id"]):
                updates.append("pessoa_id = ?")
                params.append(person_id)
            self.conn.execute(
                f"UPDATE contribuintes SET {', '.join(updates)} WHERE id = ?",
                [*params, contributor_id],
            )
        if document_value:
            existing_identifier = self.conn.execute(
                """
                SELECT 1
                FROM contribuintes_identificadores
                WHERE contribuinte_id = ? AND tipo = ? AND valor = ? AND ativo = 1
                LIMIT 1
                """,
                (contributor_id, document_type or "documento", document_value),
            ).fetchone()
            if existing_identifier is None:
                self.conn.execute(
                    """
                    INSERT INTO contribuintes_identificadores (
                        organizacao_id, pessoa_id, contribuinte_id, tipo, valor, principal, ativo, observacoes
                    ) VALUES (?, ?, ?, ?, ?, 1, 1, ?)
                    """,
                    (
                        organization_id,
                        person_id or None,
                        contributor_id,
                        document_type or "documento",
                        document_value,
                        f"Registrado automaticamente pela origem {source}.",
                    ),
                )
        return contributor_id

    def people_for_matching(self, organization_id: int, include_arquivo_morto: bool = False) -> list[dict[str, object]]:
        status_clause = "" if include_arquivo_morto else "AND status <> 'arquivo_morto'"
        people = self.conn.execute(
            """
            SELECT id, nome, cpf, status
            FROM pessoas
            WHERE organizacao_id = ? AND ativo = 1
            """ + status_clause + """
            ORDER BY nome
            """,
            (organization_id,),
        ).fetchall()
        identifier_rows = self.conn.execute(
            """
            SELECT ci.pessoa_id, ci.tipo, ci.valor, c.nome AS contribuinte_nome
            FROM contribuintes_identificadores ci
            LEFT JOIN contribuintes c ON c.id = ci.contribuinte_id
            WHERE ci.organizacao_id = ? AND ci.pessoa_id IS NOT NULL AND ci.ativo = 1
            """,
            (organization_id,),
        ).fetchall()
        contributor_rows = self.conn.execute(
            """
            SELECT pessoa_id, nome, nome_normalizado, documento_principal, documento_tipo
            FROM contribuintes
            WHERE organizacao_id = ? AND pessoa_id IS NOT NULL AND ativo = 1
            """,
            (organization_id,),
        ).fetchall()
        identifier_map: dict[int, list[dict[str, str]]] = {}
        identifier_seen: dict[int, set[tuple[str, str]]] = {}
        alias_map: dict[int, list[dict[str, str]]] = {}
        alias_seen: dict[int, set[str]] = {}

        def add_identifier(person_id: int, kind: object, value: object, source_name: object = "") -> None:
            kind_text = normalize_query(kind)
            value_text = normalize_query(value)
            if not kind_text or not value_text:
                return
            seen = identifier_seen.setdefault(person_id, set())
            key = (kind_text, value_text)
            if key in seen:
                return
            seen.add(key)
            identifier_map.setdefault(person_id, []).append(
                {
                    "kind": kind_text,
                    "value": value_text,
                    "source_name": normalize_query(source_name),
                }
            )

        def add_alias(
            person_id: int,
            alias_name: object,
            source_name: object = "",
            identifier_kind: object = "",
            identifier_value: object = "",
            alias_kind: str = "financeiro",
        ) -> None:
            alias_text = normalize_query(alias_name)
            alias_norm = normalize_match_name(alias_text)
            if not alias_norm:
                return
            seen = alias_seen.setdefault(person_id, set())
            if alias_norm in seen:
                return
            seen.add(alias_norm)
            alias_map.setdefault(person_id, []).append(
                {
                    "name": alias_text,
                    "name_norm": alias_norm,
                    "source_name": normalize_query(source_name) or alias_text,
                    "identifier_kind": normalize_query(identifier_kind),
                    "identifier_value": normalize_query(identifier_value),
                    "alias_kind": normalize_query(alias_kind) or "financeiro",
                }
            )

        for row in contributor_rows:
            person_id = moneyless_int(row["pessoa_id"])
            add_alias(
                person_id,
                row["nome"],
                source_name=row["nome"],
                identifier_kind=row["documento_tipo"],
                identifier_value=row["documento_principal"],
            )
            add_identifier(person_id, row["documento_tipo"], row["documento_principal"], row["nome"])
        for row in identifier_rows:
            person_id = moneyless_int(row["pessoa_id"])
            add_identifier(person_id, row["tipo"], row["valor"], row["contribuinte_nome"])
        data: list[dict[str, object]] = []
        for row in people:
            person_id = moneyless_int(row["id"])
            identifiers = list(identifier_map.get(person_id, []))
            financial_aliases = list(alias_map.get(person_id, []))
            cpf_digits = "".join(ch for ch in str(row["cpf"] or "") if ch.isdigit())
            if cpf_digits:
                add_identifier(person_id, "cpf", cpf_digits, row["nome"])
                identifiers = list(identifier_map.get(person_id, []))
            for derived_alias in derived_pix_name_aliases(row["nome"]):
                add_alias(
                    person_id,
                    derived_alias,
                    source_name=f"Nome resumido derivado de {row['nome']}",
                    alias_kind="derivado",
                )
            financial_aliases = list(alias_map.get(person_id, []))
            data.append(
                {
                    "id": person_id,
                    "nome": str(row["nome"]),
                    "name_norm": normalize_match_name(row["nome"]),
                    "status": str(row["status"]),
                    "identifiers": identifiers,
                    "financial_aliases": financial_aliases,
                }
            )
        return data

    def people_for_pix_matching(self, organization_id: int) -> list[dict[str, object]]:
        return self.people_for_matching(organization_id, include_arquivo_morto=False)

    def people_for_audit_matching(self, organization_id: int) -> list[dict[str, object]]:
        return self.people_for_matching(organization_id, include_arquivo_morto=True)

    def match_pix_entry(
        self,
        organization_id: int,
        donor_name: str,
        document_mask: str,
        document_type: str,
        people_cache: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        people_cache = people_cache or self.people_for_pix_matching(organization_id)
        return core_matching.match_pix_entry(
            donor_name,
            document_mask,
            document_type,
            people_cache,
            company_hints=PIX_COMPANY_HINTS,
        )

    def pix_candidate_suggestions(
        self,
        organization_id: int,
        donor_name: str,
        document_mask: str,
        document_type: str,
        people_cache: list[dict[str, object]] | None = None,
        limit: int = 12,
    ) -> list[dict[str, object]]:
        people_cache = people_cache or self.people_for_pix_matching(organization_id)
        return core_matching.pix_candidate_suggestions(
            donor_name,
            document_mask,
            document_type,
            people_cache,
            limit=limit,
        )

    def classify_pix_review_status(self, confidence: str, special_rule: bool) -> str:
        if special_rule:
            return "revisar_destinacao"
        if confidence in {"forte_doc_nome", "forte_doc", "forte_nome", "pj_ou_externo"}:
            return "pronto"
        return "revisar_pessoa"

    def pix_lots(self, limit: int = 20) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT *
            FROM pix_lotes
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    def get_pix_lot(self, lot_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM pix_lotes WHERE id = ?", (lot_id,)).fetchone()

    def pix_lot_counts(self, lot_id: int) -> dict[str, int]:
        rows = self.conn.execute(
            """
            SELECT review_status, COUNT(*) AS quantidade
            FROM pix_movimentos
            WHERE lote_id = ? AND ativo = 1
            GROUP BY review_status
            """,
            (lot_id,),
        ).fetchall()
        return {str(row["review_status"]): moneyless_int(row["quantidade"]) for row in rows}

    def pix_lot_financial_counts(self, lot_id: int) -> dict[str, int]:
        association_expr = pix_association_pending_expr("m", "ico", "ict")
        row = self.conn.execute(
            f"""
            SELECT
                COUNT(*) AS total,
                COALESCE(SUM(CASE WHEN m.imported_contribution_id IS NOT NULL THEN 1 ELSE 0 END), 0) AS lancados,
                COALESCE(SUM(CASE WHEN m.imported_contribution_id IS NULL AND m.review_status <> 'ignorado' THEN 1 ELSE 0 END), 0) AS sem_financeiro,
                COALESCE(SUM(CASE WHEN {association_expr} THEN 1 ELSE 0 END), 0) AS sem_associacao,
                COALESCE(SUM(CASE WHEN m.imported_contribution_id IS NOT NULL AND m.review_status = 'revisar_destinacao' THEN 1 ELSE 0 END), 0) AS classificacao_pendente,
                COALESCE(SUM(CASE WHEN m.imported_contribution_id IS NOT NULL AND m.review_status = 'revisar_duplicidade' THEN 1 ELSE 0 END), 0) AS duplicidade_suspeita,
                COALESCE(SUM(
                    CASE
                        WHEN m.imported_contribution_id IS NOT NULL
                         AND NOT {association_expr}
                         AND m.review_status NOT IN ('revisar_destinacao', 'revisar_duplicidade', 'ignorado')
                        THEN 1
                        ELSE 0
                    END
                ), 0) AS regulares
            FROM pix_movimentos m
            LEFT JOIN contribuicoes ico ON ico.id = m.imported_contribution_id AND ico.ativo = 1
            LEFT JOIN contribuintes ict ON ict.id = ico.contribuinte_id
            WHERE m.lote_id = ? AND m.ativo = 1
            """,
            (lot_id,),
        ).fetchone()
        return {
            "total": moneyless_int(row["total"] if row else 0),
            "lancados": moneyless_int(row["lancados"] if row else 0),
            "sem_financeiro": moneyless_int(row["sem_financeiro"] if row else 0),
            "sem_associacao": moneyless_int(row["sem_associacao"] if row else 0),
            "classificacao_pendente": moneyless_int(row["classificacao_pendente"] if row else 0),
            "duplicidade_suspeita": moneyless_int(row["duplicidade_suspeita"] if row else 0),
            "regulares": moneyless_int(row["regulares"] if row else 0),
        }

    def pix_lot_association_counts(self, lot_id: int) -> dict[str, int]:
        association_expr = pix_association_pending_expr("pm", "co", "ct")
        rows = self.conn.execute(
            f"""
            SELECT
                pm.documento_tipo,
                pm.nome_origem,
                ct.tipo AS contribuinte_tipo
            FROM pix_movimentos pm
            JOIN contribuicoes co ON co.id = pm.imported_contribution_id AND co.ativo = 1
            LEFT JOIN contribuintes ct ON ct.id = co.contribuinte_id
            WHERE pm.lote_id = ? AND pm.ativo = 1
              AND {association_expr}
            """,
            (lot_id,),
        ).fetchall()
        total = 0
        pj = 0
        for row in rows:
            total += 1
            if pix_origin_is_company(row["documento_tipo"], row["nome_origem"]):
                pj += 1
        return {"associacao": total, "associacao_pj": pj, "associacao_pf": max(total - pj, 0)}

    def pix_review_person_groups(self, lot_id: int) -> dict[str, int]:
        rows = self.conn.execute(
            """
            SELECT confidence, COUNT(*) AS quantidade
            FROM pix_movimentos
            WHERE lote_id = ? AND ativo = 1 AND review_status = 'revisar_pessoa'
            GROUP BY confidence
            """,
            (lot_id,),
        ).fetchall()
        groups: dict[str, int] = {}
        for row in rows:
            group = pix_confidence_group(row["confidence"])
            groups[group] = groups.get(group, 0) + moneyless_int(row["quantidade"])
        return groups

    def refresh_pix_lot_status(self, lot_id: int) -> str:
        current = self.get_pix_lot(lot_id)
        counts = self.pix_lot_counts(lot_id)
        financial = self.pix_lot_financial_counts(lot_id)
        imported = financial.get("lancados", 0)
        ignored = counts.get("ignorado", 0)
        pending = counts.get("revisar_pessoa", 0) + counts.get("revisar_destinacao", 0) + counts.get("revisar_duplicidade", 0) + financial.get("sem_associacao", 0)
        ready = counts.get("pronto", 0) + counts.get("aprovado", 0)
        total = financial.get("total", 0)
        if current is not None and str(current["status"]) == "encerrado" and total and imported + ignored >= total:
            status = "encerrado"
        elif total and imported + ignored >= total and pending == 0:
            status = "concluido"
        elif imported and pending:
            status = "parcial"
        elif pending:
            status = "auditando"
        elif financial.get("sem_financeiro", 0):
            status = "pronto_importacao"
        elif ready:
            status = "pronto_importacao"
        elif imported:
            status = "parcial"
        else:
            status = "carregado"
        self.conn.execute(
            "UPDATE pix_lotes SET status = ?, atualizado_em = CURRENT_TIMESTAMP WHERE id = ?",
            (status, lot_id),
        )
        return status

    def pix_lot_movements(
        self,
        lot_id: int,
        status_filter: str = "",
        confidence_group: str = "",
        limit: int = 500,
    ) -> list[sqlite3.Row]:
        clauses = ["m.lote_id = ?", "m.ativo = 1"]
        params: list[object] = [lot_id]
        status_filter = normalize_query(status_filter)
        confidence_group = normalize_query(confidence_group)
        padded_name_expr = "' ' || COALESCE(m.nome_normalizado, '') || ' '"
        company_like_parts = []
        for hint in PIX_COMPANY_HINTS:
            escaped_hint = normalize_match_name(hint).replace("'", "''")
            company_like_parts.append(f"{padded_name_expr} LIKE '% {escaped_hint} %'")
        company_expr = "(m.documento_tipo = 'cnpj' OR " + " OR ".join(company_like_parts) + ")"
        association_expr = pix_association_pending_expr("m", "ico", "ict")
        if status_filter == "pendencias":
            clauses.append(f"(m.review_status IN ('revisar_pessoa', 'revisar_destinacao', 'revisar_duplicidade') OR {association_expr})")
        elif status_filter == "associacao":
            clauses.append(association_expr)
        elif status_filter == "associacao_pj":
            clauses.append(f"({association_expr} AND {company_expr})")
        elif status_filter == "associacao_pf":
            clauses.append(f"({association_expr} AND NOT {company_expr})")
        elif status_filter == "destinacoes_especiais":
            clauses.append("m.regra_id IS NOT NULL")
        elif status_filter and status_filter != "todos":
            clauses.append("m.review_status = ?")
            params.append(status_filter)
        if confidence_group:
            group_map = {
                "forte": ["forte_doc_nome", "forte_doc", "forte_nome"],
                "provavel": ["provavel_doc_amb_nome", "provavel_nome"],
                "ambiguo": ["ambiguo", "conflito_doc_nome"],
                "sem_match": ["sem_match"],
                "pj_externo": ["pj_ou_externo"],
            }
            confidence_values = group_map.get(confidence_group, [confidence_group])
            placeholders = ", ".join("?" for _ in confidence_values)
            clauses.append(f"m.confidence IN ({placeholders})")
            params.extend(confidence_values)
        order_clause = "m.data_recebimento DESC, m.ordem_no_lote"
        if status_filter in {"pendencias", "revisar_pessoa"}:
            order_clause = """
                CASE
                    WHEN """ + association_expr + """ AND """ + company_expr + """ THEN 0
                    WHEN """ + association_expr + """ THEN 1
                    WHEN m.review_status = 'revisar_pessoa' AND m.confidence IN ('forte_doc_nome', 'forte_doc', 'forte_nome') THEN 2
                    WHEN m.review_status = 'revisar_pessoa' AND m.confidence IN ('provavel_doc_amb_nome', 'provavel_nome') THEN 3
                    WHEN m.review_status = 'revisar_pessoa' AND m.confidence IN ('ambiguo', 'conflito_doc_nome') THEN 4
                    WHEN m.review_status = 'revisar_destinacao' THEN 5
                    WHEN m.review_status = 'revisar_duplicidade' THEN 6
                    WHEN m.review_status = 'revisar_pessoa' THEN 7
                    ELSE 8
                END,
                CASE WHEN COALESCE(m.resolved_person_id, m.suggested_person_id) IS NOT NULL THEN 0 ELSE 1 END,
                m.match_score DESC,
                m.data_recebimento DESC,
                m.ordem_no_lote
            """
        elif status_filter in {"associacao", "associacao_pj", "associacao_pf"}:
            order_clause = f"""
                CASE
                    WHEN {company_expr} THEN 0
                    ELSE 1
                END,
                m.data_recebimento DESC,
                m.ordem_no_lote
            """
        params.append(limit)
        return self.conn.execute(
            f"""
            SELECT
                m.*,
                sp.nome AS suggested_person_name,
                rp.nome AS resolved_person_name,
                sc.nome AS suggested_contributor_name,
                rc.nome AS resolved_contributor_name,
                ico.pessoa_id AS imported_person_id,
                ico.contribuinte_id AS imported_contributor_id,
                ict.nome AS imported_contributor_name,
                ict.tipo AS imported_contributor_tipo,
                ict.pessoa_id AS imported_contributor_person_id,
                CASE WHEN {association_expr} THEN 1 ELSE 0 END AS association_pending,
                CASE WHEN {association_expr} AND {company_expr} THEN 'pj'
                     WHEN {association_expr} THEN 'pf'
                     ELSE '' END AS association_kind,
                tc.nome AS resolved_tipo_nome,
                r.nome_destinacao AS regra_nome
            FROM pix_movimentos m
            LEFT JOIN pessoas sp ON sp.id = m.suggested_person_id
            LEFT JOIN pessoas rp ON rp.id = m.resolved_person_id
            LEFT JOIN contribuintes sc ON sc.id = m.suggested_contribuinte_id
            LEFT JOIN contribuintes rc ON rc.id = m.resolved_contribuinte_id
            LEFT JOIN contribuicoes ico ON ico.id = m.imported_contribution_id
            LEFT JOIN contribuintes ict ON ict.id = ico.contribuinte_id
            LEFT JOIN tipos_contribuicao tc ON tc.id = m.resolved_tipo_contribuicao_id
            LEFT JOIN pix_centavo_regras r ON r.id = m.regra_id
            WHERE {' AND '.join(clauses)}
            ORDER BY {order_clause}
            LIMIT ?
            """,
            params,
        ).fetchall()

    def get_pix_movement(self, movement_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            """
            SELECT
                m.*,
                l.nome_arquivo,
                l.banco,
                r.nome_destinacao AS regra_nome,
                tc.nome AS resolved_tipo_nome,
                sp.nome AS suggested_person_name,
                rp.nome AS resolved_person_name
            FROM pix_movimentos m
            JOIN pix_lotes l ON l.id = m.lote_id
            LEFT JOIN pix_centavo_regras r ON r.id = m.regra_id
            LEFT JOIN tipos_contribuicao tc ON tc.id = m.resolved_tipo_contribuicao_id
            LEFT JOIN pessoas sp ON sp.id = m.suggested_person_id
            LEFT JOIN pessoas rp ON rp.id = m.resolved_person_id
            WHERE m.id = ?
            """,
            (movement_id,),
        ).fetchone()

    def find_pix_duplicate_targets(
        self,
        organization_id: int,
        signature_global: str,
        received_on: str,
        amount: float,
        person_id: int = 0,
        contributor_id: int = 0,
        occurrence_index: int = 1,
        ignore_movement_id: int = 0,
        ignore_lot_id: int = 0,
    ) -> dict[str, object]:
        duplicate_movements = self.conn.execute(
            """
            SELECT *
            FROM pix_movimentos
            WHERE organizacao_id = ? AND signature_global = ? AND ativo = 1
              AND (? = 0 OR id <> ?)
              AND (? = 0 OR lote_id <> ?)
            ORDER BY CASE WHEN imported_contribution_id IS NOT NULL THEN 0 ELSE 1 END, id
            """,
            (organization_id, signature_global, ignore_movement_id, ignore_movement_id, ignore_lot_id, ignore_lot_id),
        ).fetchall()
        duplicate_movement = None
        duplicate_contribution = None
        external_movement_count = len(duplicate_movements)
        if occurrence_index <= external_movement_count:
            duplicate_movement = duplicate_movements[occurrence_index - 1]
        manual_duplicate_contributions: list[sqlite3.Row] = []
        if duplicate_movement is None:
            clauses = [
                "c.organizacao_id = ?",
                "c.ativo = 1",
                "c.data_recebimento = ?",
                "ABS(c.valor - ?) < 0.005",
                "c.pix_movimento_id IS NULL",
                "c.forma_recebimento_id = ?",
            ]
            params: list[object] = [
                organization_id,
                received_on,
                float(amount),
                self.pix_receiving_form_id(organization_id),
            ]
            if person_id:
                clauses.append("c.pessoa_id = ?")
                params.append(person_id)
            elif contributor_id:
                clauses.append("c.contribuinte_id = ?")
                params.append(contributor_id)
            else:
                clauses.append("1 = 0")
            if ignore_lot_id:
                clauses.append("(pm.id IS NULL OR pm.lote_id <> ?)")
                params.append(ignore_lot_id)
            manual_duplicate_contributions = self.conn.execute(
                f"""
                SELECT c.*
                FROM contribuicoes c
                LEFT JOIN pix_movimentos pm ON pm.id = c.pix_movimento_id
                WHERE {' AND '.join(clauses)}
                ORDER BY c.id
                """,
                params,
            ).fetchall()
            manual_duplicate_count = len(manual_duplicate_contributions)
            remaining_index = occurrence_index - external_movement_count
            if remaining_index > 0 and remaining_index <= manual_duplicate_count:
                duplicate_contribution = manual_duplicate_contributions[remaining_index - 1]
        if duplicate_movement is not None:
            lot = self.get_pix_lot(moneyless_int(duplicate_movement["lote_id"]))
            lot_label = f"lote #{duplicate_movement['lote_id']}"
            if lot is not None and lot["nome_arquivo"]:
                lot_label += f" ({lot['nome_arquivo']})"
            reason = (
                f"Ja existem {external_movement_count} ocorrencia(s) equivalentes em outro(s) documento(s). "
                f"Esta e a ocorrencia {occurrence_index} deste novo lote e coincide com {lot_label}."
            )
            return {
                "duplicate_movement_id": moneyless_int(duplicate_movement["id"]),
                "duplicate_contribution_id": moneyless_int(duplicate_movement["imported_contribution_id"]),
                "duplicate_reason": reason,
                "review_status": "revisar_duplicidade",
            }
        if duplicate_contribution is not None:
            total_known = external_movement_count + len(manual_duplicate_contributions)
            reason = (
                f"Ja existem {total_known} ocorrencia(s) equivalentes em documentos anteriores ou em PIX manual ja registrado. "
                f"Esta e a ocorrencia {occurrence_index} deste novo lote e coincide com a contribuicao #{duplicate_contribution['id']}."
            )
            return {
                "duplicate_movement_id": 0,
                "duplicate_contribution_id": moneyless_int(duplicate_contribution["id"]),
                "duplicate_reason": reason,
                "review_status": "revisar_duplicidade",
            }
        return {
            "duplicate_movement_id": 0,
            "duplicate_contribution_id": 0,
            "duplicate_reason": "",
            "review_status": "",
        }

    def pix_lot_occurrence_index(self, lot_id: int, signature_global: str, order_in_lot: int, movement_id: int = 0) -> int:
        row = self.conn.execute(
            """
            SELECT COUNT(*) AS quantidade
            FROM pix_movimentos
            WHERE lote_id = ? AND ativo = 1 AND review_status <> 'ignorado'
              AND signature_global = ?
              AND (
                    ordem_no_lote < ?
                    OR (ordem_no_lote = ? AND (? = 0 OR id <= ?))
              )
            """,
            (lot_id, signature_global, order_in_lot, order_in_lot, movement_id, movement_id),
        ).fetchone()
        return moneyless_int(row["quantidade"] if row else 0)

    def list_contributors(
        self,
        q: str = "",
        limit: int = 120,
        mode: str = "",
        tags: list[str] | tuple[str, ...] | set[str] | None = None,
    ) -> list[dict[str, object]]:
        rows = self.conn.execute(
            """
            WITH contrib_stats AS (
                SELECT
                    co.contribuinte_id,
                    COUNT(*) AS contribuicoes_qtd,
                    COALESCE(SUM(co.valor), 0) AS total_contribuido,
                    MIN(co.data_recebimento) AS primeira_contribuicao,
                    MAX(co.data_recebimento) AS ultima_contribuicao,
                    COUNT(DISTINCT CASE
                        WHEN co.competencia IS NOT NULL AND TRIM(co.competencia) <> '' THEN co.competencia
                    END) AS competencias_qtd,
                    COUNT(DISTINCT CASE
                        WHEN co.data_recebimento IS NOT NULL AND TRIM(co.data_recebimento) <> '' THEN strftime('%Y-%W', co.data_recebimento)
                    END) AS semanas_qtd,
                    COUNT(DISTINCT CASE
                        WHEN co.data_recebimento IS NOT NULL AND TRIM(co.data_recebimento) <> '' THEN substr(co.data_recebimento, 1, 7)
                    END) AS meses_recebimento_qtd,
                    SUM(CASE WHEN co.pessoa_id IS NULL THEN 1 ELSE 0 END) AS contribuicoes_sem_pessoa
                FROM contribuicoes co
                WHERE co.ativo = 1 AND co.contribuinte_id IS NOT NULL
                GROUP BY co.contribuinte_id
            ),
            identifier_stats AS (
                SELECT
                    ci.contribuinte_id,
                    GROUP_CONCAT(DISTINCT ci.valor) AS identificadores_texto
                FROM contribuintes_identificadores ci
                WHERE ci.ativo = 1 AND ci.contribuinte_id IS NOT NULL
                GROUP BY ci.contribuinte_id
            ),
            pix_stats AS (
                SELECT
                    pm.contribuinte_id,
                    SUM(CASE
                        WHEN pm.review_status IN ('revisar_pessoa', 'revisar_destinacao', 'revisar_duplicidade') THEN 1
                        ELSE 0
                    END) AS pix_pendentes,
                    SUM(CASE WHEN pm.review_status = 'revisar_pessoa' THEN 1 ELSE 0 END) AS pix_pendentes_pessoa,
                    SUM(CASE WHEN pm.review_status = 'revisar_destinacao' THEN 1 ELSE 0 END) AS pix_pendentes_destinacao,
                    SUM(CASE WHEN pm.review_status = 'revisar_duplicidade' THEN 1 ELSE 0 END) AS pix_pendentes_duplicidade
                FROM (
                    SELECT
                        COALESCE(resolved_contribuinte_id, suggested_contribuinte_id) AS contribuinte_id,
                        review_status
                    FROM pix_movimentos
                    WHERE ativo = 1
                      AND COALESCE(resolved_contribuinte_id, suggested_contribuinte_id) IS NOT NULL
                ) pm
                GROUP BY pm.contribuinte_id
            )
            SELECT
                c.*,
                p.nome AS pessoa_nome,
                p.status AS pessoa_status,
                COALESCE(cs.contribuicoes_qtd, 0) AS contribuicoes_qtd,
                COALESCE(cs.total_contribuido, 0) AS total_contribuido,
                cs.primeira_contribuicao,
                cs.ultima_contribuicao,
                COALESCE(cs.competencias_qtd, 0) AS competencias_qtd,
                COALESCE(cs.semanas_qtd, 0) AS semanas_qtd,
                COALESCE(cs.meses_recebimento_qtd, 0) AS meses_recebimento_qtd,
                ids.identificadores_texto,
                COALESCE(cs.contribuicoes_sem_pessoa, 0) AS contribuicoes_sem_pessoa,
                COALESCE(ps.pix_pendentes, 0) AS pix_pendentes,
                COALESCE(ps.pix_pendentes_pessoa, 0) AS pix_pendentes_pessoa,
                COALESCE(ps.pix_pendentes_destinacao, 0) AS pix_pendentes_destinacao,
                COALESCE(ps.pix_pendentes_duplicidade, 0) AS pix_pendentes_duplicidade
            FROM contribuintes c
            LEFT JOIN pessoas p ON p.id = c.pessoa_id
            LEFT JOIN contrib_stats cs ON cs.contribuinte_id = c.id
            LEFT JOIN identifier_stats ids ON ids.contribuinte_id = c.id
            LEFT JOIN pix_stats ps ON ps.contribuinte_id = c.id
            WHERE c.ativo = 1
            ORDER BY
                COALESCE(cs.ultima_contribuicao, '') DESC,
                c.nome
            """
        ).fetchall()
        mode = normalize_query(mode) or "todos"
        tag_set = {normalize_query(item).lower() for item in (tags or []) if normalize_query(item)}
        operational_tags = {
            "integracao",
            "familia_sugerida",
            "recorrente",
            "semanal",
            "multicompetencia",
            "pendencias",
            "pix_saneamento",
            "sem_pessoa",
        }
        use_operational_sort = mode != "todos" or bool(tag_set & operational_tags)

        def contributor_alpha_key(row: dict[str, object]) -> tuple[str, str, int]:
            return (
                normalize_match_name(row.get("nome")),
                str(row.get("nome") or "").lower(),
                moneyless_int(row.get("id")),
            )

        def contributor_operational_key(row: dict[str, object]) -> tuple[object, ...]:
            return (
                -moneyless_int(row.get("prioridade_integracao")),
                -moneyless_int(row["pendencias_total"]) if "pendencias_total" in row.keys() else 0,
                -moneyless_int(row.get("recorrencia_semanas")),
                -moneyless_int(row.get("recorrencia_competencias")),
                -moneyless_int(row["pix_pendentes"]) if "pix_pendentes" in row.keys() else 0,
                -moneyless_int(row["contribuicoes_sem_pessoa"]) if "contribuicoes_sem_pessoa" in row.keys() else 0,
                str(row["ultima_contribuicao"] or ""),
                *contributor_alpha_key(row),
            )
        enriched_rows: list[dict[str, object]] = []
        for row in rows:
            row_data = dict(row)
            row_data["pendencias_total"] = moneyless_int(row_data.get("pix_pendentes")) + moneyless_int(row_data.get("contribuicoes_sem_pessoa"))
            recurrence = contributor_recurrence_flags(row_data)
            family_keys = contributor_family_keys(row_data.get("nome"))
            row_data["recorrencia_semanal"] = 1 if recurrence["weekly"] else 0
            row_data["recorrencia_multicompetencia"] = 1 if recurrence["multi_competencia"] else 0
            row_data["sugestao_integracao"] = 1 if recurrence["candidate"] else 0
            row_data["prioridade_integracao"] = moneyless_int(recurrence["priority"])
            row_data["recorrencia_semanas"] = moneyless_int(recurrence["weeks"])
            row_data["recorrencia_competencias"] = moneyless_int(recurrence["competencias"])
            row_data["familia_sugerida"] = 1 if family_keys.get("nuclear") or family_keys.get("broad") else 0
            if mode == "pendentes" and moneyless_int(row_data["pendencias_total"]) <= 0:
                continue
            if mode == "nao_lancados" and moneyless_int(row_data.get("pix_pendentes")) <= 0:
                continue
            if mode == "sem_pessoa" and moneyless_int(row_data.get("contribuicoes_sem_pessoa")) <= 0:
                continue
            if mode == "recorrentes" and not moneyless_int(row_data.get("sugestao_integracao")):
                continue
            if "pf" in tag_set and str(row_data.get("tipo") or "") != "pf":
                continue
            if "pj" in tag_set and str(row_data.get("tipo") or "") != "pj":
                continue
            if "vinculado" in tag_set and moneyless_int(row_data.get("pessoa_id")) <= 0:
                continue
            if "sem_vinculo" in tag_set and moneyless_int(row_data.get("pessoa_id")) > 0:
                continue
            if "recorrente" in tag_set and moneyless_int(row_data.get("contribuicoes_qtd")) < 2:
                continue
            if "semanal" in tag_set and moneyless_int(row_data.get("recorrencia_semanal")) <= 0:
                continue
            if "multicompetencia" in tag_set and moneyless_int(row_data.get("recorrencia_multicompetencia")) <= 0:
                continue
            if "integracao" in tag_set and moneyless_int(row_data.get("sugestao_integracao")) <= 0:
                continue
            if "pendencias" in tag_set and moneyless_int(row_data.get("pendencias_total")) <= 0:
                continue
            if "pix_saneamento" in tag_set and moneyless_int(row_data.get("pix_pendentes")) <= 0:
                continue
            if "sem_pessoa" in tag_set and moneyless_int(row_data.get("contribuicoes_sem_pessoa")) <= 0:
                continue
            if "familia_sugerida" in tag_set and moneyless_int(row_data.get("familia_sugerida")) <= 0:
                continue
            enriched_rows.append(row_data)
        if not q:
            ordered = sorted(
                enriched_rows,
                key=contributor_operational_key if use_operational_sort else contributor_alpha_key,
            )
            return ordered[:limit]
        query_text = normalize_query(q)
        query_text_lower = query_text.lower()
        contributor_id = moneyless_int(q) if str(q).strip().isdigit() else 0
        filtered: list[dict[str, object]] = []
        for row in enriched_rows:
            if contributor_id and moneyless_int(row["id"]) == contributor_id:
                filtered.append(row)
                continue
            text_candidates = [
                str(row["nome"] or ""),
                str(row["documento_principal"] or ""),
                str(row["pessoa_nome"] or ""),
                str(row["identificadores_texto"] or ""),
            ]
            if any(query_text_lower in normalize_query(value).lower() for value in text_candidates if value):
                filtered.append(row)
                continue
            if document_query_matches(query_text, row["documento_principal"]):
                filtered.append(row)
                continue
            identifiers = [item.strip() for item in str(row["identificadores_texto"] or "").split(",") if item.strip()]
            if any(document_query_matches(query_text, item) for item in identifiers):
                filtered.append(row)
                continue
        filtered.sort(key=contributor_operational_key if use_operational_sort else contributor_alpha_key)
        return filtered[:limit]

    def contributor_pending_pix(self, contributor_id: int, limit: int = 40) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT
                pm.*,
                pl.nome_arquivo
            FROM pix_movimentos pm
            JOIN pix_lotes pl ON pl.id = pm.lote_id
            WHERE COALESCE(pm.resolved_contribuinte_id, pm.suggested_contribuinte_id) = ?
              AND pm.ativo = 1
              AND pm.review_status IN ('revisar_pessoa', 'revisar_destinacao', 'revisar_duplicidade')
            ORDER BY pm.data_recebimento DESC, pm.id DESC
            LIMIT ?
            """,
            (contributor_id, limit),
        ).fetchall()

    def pix_contributor_suggestions(
        self,
        organization_id: int,
        donor_name: str,
        document_mask: str,
        document_type: str,
        limit: int = 12,
    ) -> list[dict[str, object]]:
        donor_norm = normalize_match_name(donor_name)
        rows = self.conn.execute(
            """
            SELECT
                c.*,
                p.nome AS pessoa_nome,
                p.codigo_interno,
                p.cpf,
                GROUP_CONCAT(DISTINCT ci.valor) AS identificadores_texto
            FROM contribuintes c
            LEFT JOIN pessoas p ON p.id = c.pessoa_id
            LEFT JOIN contribuintes_identificadores ci ON ci.contribuinte_id = c.id AND ci.ativo = 1
            WHERE c.organizacao_id = ? AND c.ativo = 1
            GROUP BY c.id
            ORDER BY c.nome
            """,
            (organization_id,),
        ).fetchall()
        suggestions: list[dict[str, object]] = []
        for row in rows:
            all_docs = [str(row["documento_principal"] or "")]
            all_docs.extend(item.strip() for item in str(row["identificadores_texto"] or "").split(",") if item.strip())
            doc_match = bool(document_mask) and any(document_query_matches(document_mask, item) for item in all_docs if item)
            exact_name = bool(donor_norm) and normalize_match_name(row["nome"]) == donor_norm
            ratio = SequenceMatcher(None, donor_norm, normalize_match_name(row["nome"])).ratio() if donor_norm else 0.0
            if not doc_match and not exact_name and ratio < 0.86:
                continue
            score = 0.0
            reasons: list[str] = []
            if doc_match:
                score += 70
                reasons.append("documento financeiro compativel")
            if exact_name:
                score += 22
                reasons.append("nome financeiro exato")
            elif ratio >= 0.97:
                score += 16
                reasons.append(f"nome financeiro muito proximo ({ratio:.2f})")
            elif ratio >= 0.93:
                score += 10
                reasons.append(f"nome financeiro proximo ({ratio:.2f})")
            elif ratio >= 0.88:
                score += 5
                reasons.append(f"nome financeiro parcial ({ratio:.2f})")
            if str(row["tipo"] or "") == "pj" or str(document_type or "") == "cnpj":
                score += 1
            suggestions.append(
                {
                    **dict(row),
                    "doc_match": doc_match,
                    "exact_name": exact_name,
                    "ratio": round(ratio, 4),
                    "score": round(score, 2),
                    "reason": ", ".join(reasons) or "contribuinte relacionado",
                }
            )
        suggestions.sort(
            key=lambda item: (
                -int(bool(item["doc_match"])),
                -int(bool(item["exact_name"])),
                -float(item["score"]),
                -float(item["ratio"]),
                str(item["nome"]),
            )
        )
        return suggestions[:limit]

    def normalize_contributor_types(self) -> dict[str, int]:
        rows = self.conn.execute(
            """
            SELECT *
            FROM contribuintes
            WHERE ativo = 1
            ORDER BY id
            """
        ).fetchall()
        updated = 0
        for row in rows:
            identifiers = self.contributor_identifiers(moneyless_int(row["id"]))
            expected_kind = contributor_kind_for_identity(
                row["nome"],
                document_type=row["documento_tipo"],
                document_value=row["documento_principal"],
                identifier_pairs=[(str(item["tipo"]), str(item["valor"])) for item in identifiers],
            )
            current_kind = normalize_query(row["tipo"]).lower() or "pf"
            if current_kind == expected_kind:
                continue
            before = dict(row)
            self.conn.execute(
                """
                UPDATE contribuintes
                SET tipo = ?, atualizado_em = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (expected_kind, row["id"]),
            )
            after = self.get_contributor(moneyless_int(row["id"]))
            self.write_audit_log(
                moneyless_int(row["organizacao_id"]),
                "normalizar_tipo_contribuinte",
                "contribuintes",
                moneyless_int(row["id"]),
                before,
                dict(after) if after else None,
            )
            updated += 1
        self.conn.commit()
        return {"updated": updated, "total": len(rows)}

    def get_contributor(self, contributor_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            """
            SELECT
                c.*,
                p.nome AS pessoa_nome,
                p.status AS pessoa_status,
                p.codigo_interno AS pessoa_codigo_interno,
                p.cpf AS pessoa_cpf
            FROM contribuintes c
            LEFT JOIN pessoas p ON p.id = c.pessoa_id
            WHERE c.id = ?
            """,
            (contributor_id,),
        ).fetchone()

    def contributor_identifiers(self, contributor_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT *
            FROM contribuintes_identificadores
            WHERE contribuinte_id = ? AND ativo = 1
            ORDER BY principal DESC, tipo, valor
            """,
            (contributor_id,),
        ).fetchall()

    def contributor_summary(self, contributor_id: int) -> sqlite3.Row:
        return self.conn.execute(
            """
            SELECT
                COUNT(*) AS quantidade,
                COALESCE(SUM(valor), 0) AS total,
                MIN(data_recebimento) AS primeira_data,
                MAX(data_recebimento) AS ultima_data,
                COUNT(DISTINCT CASE WHEN competencia IS NOT NULL AND TRIM(competencia) <> '' THEN competencia END) AS competencias,
                COUNT(DISTINCT CASE WHEN data_recebimento IS NOT NULL AND TRIM(data_recebimento) <> '' THEN strftime('%Y-%W', data_recebimento) END) AS semanas,
                COUNT(DISTINCT CASE WHEN data_recebimento IS NOT NULL AND TRIM(data_recebimento) <> '' THEN substr(data_recebimento, 1, 7) END) AS meses,
                COUNT(DISTINCT CASE WHEN pessoa_id IS NOT NULL THEN pessoa_id END) AS pessoas_relacionadas
            FROM contribuicoes
            WHERE contribuinte_id = ? AND ativo = 1
            """,
            (contributor_id,),
        ).fetchone()

    def contributor_contributions(self, contributor_id: int, limit: int = 80) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT
                c.*,
                p.nome AS pessoa_nome,
                tc.nome AS tipo_nome,
                fr.nome AS forma_nome
            FROM contribuicoes c
            LEFT JOIN pessoas p ON p.id = c.pessoa_id
            JOIN tipos_contribuicao tc ON tc.id = c.tipo_contribuicao_id
            LEFT JOIN formas_recebimento fr ON fr.id = c.forma_recebimento_id
            WHERE c.contribuinte_id = ? AND c.ativo = 1
            ORDER BY c.data_recebimento DESC, c.id DESC
            LIMIT ?
            """,
            (contributor_id, limit),
        ).fetchall()

    def contributor_possible_people(self, contributor_id: int, limit: int = 12) -> list[dict[str, object]]:
        contributor = self.get_contributor(contributor_id)
        if contributor is None:
            return []
        people_cache = self.people_for_pix_matching(moneyless_int(contributor["organizacao_id"]))
        doc_value = str(contributor["documento_principal"] or "")
        doc_type = str(contributor["documento_tipo"] or "")
        contributor_name = str(contributor["nome"] or "")
        contributor_norm = normalize_match_name(contributor_name)
        rows: list[dict[str, object]] = []
        seen: set[int] = set()
        for person in people_cache:
            person_id = moneyless_int(person["id"])
            if person_id in seen:
                continue
            exact_name = bool(contributor_norm and contributor_norm == str(person["name_norm"]))
            doc_match = False
            if doc_value:
                doc_digits = clean_cpf(doc_value)
                for identifier in person["identifiers"]:
                    identifier_kind = str(identifier.get("kind", ""))
                    identifier_value = str(identifier.get("value", ""))
                    if doc_digits and identifier_kind == "cpf" and clean_cpf(identifier_value) == doc_digits:
                        doc_match = True
                        break
                    if masked_document_matches(doc_value, identifier_value):
                        doc_match = True
                        break
            similarity_ratio = SequenceMatcher(None, contributor_norm, str(person["name_norm"])).ratio() if contributor_norm else 0.0
            if not doc_match and not exact_name and similarity_ratio < 0.72:
                continue
            seen.add(person_id)
            if doc_match and exact_name:
                source = "doc+nome"
                reason = "Documento e nome coincidem com a ficha."
                score = 0.99
            elif doc_match:
                source = "documento"
                reason = "Documento associado ao contribuinte coincide com a ficha."
                score = 0.96
            elif exact_name:
                source = "nome_exato"
                reason = "Nome financeiro coincide integralmente com a ficha."
                score = 0.93
            else:
                source = "nome_proximo"
                reason = f"Nome com semelhanca relevante ({similarity_ratio:.2f})."
                score = similarity_ratio
            rows.append(
                {
                    "id": person_id,
                    "nome": str(person["nome"]),
                    "status": str(person["status"]),
                    "codigo_interno": self.get_person(person_id)["codigo_interno"] if self.get_person(person_id) else "",
                    "cpf": self.get_person(person_id)["cpf"] if self.get_person(person_id) else "",
                    "similarity_ratio": similarity_ratio,
                    "engine_doc_match": doc_match,
                    "engine_exact_name": exact_name,
                    "engine_source": source,
                    "engine_reason": reason,
                    "engine_score": score,
                }
            )
        rows.sort(
            key=lambda item: (
                0 if item["engine_doc_match"] and item["engine_exact_name"] else
                1 if item["engine_doc_match"] else
                2 if item["engine_exact_name"] else
                3,
                -float(item["engine_score"]),
                str(item["nome"]),
            )
        )
        return rows[:limit]

    def new_people_association_candidates(
        self,
        import_lot_ids: list[int] | tuple[int, ...] | set[int] | None = None,
        limit: int = 300,
    ) -> dict[str, object]:
        selected_lot_ids = self.recent_people_import_lot_ids(list(import_lot_ids or []))
        if not selected_lot_ids:
            return {"import_lot_ids": [], "people_count": 0, "rows": [], "summary": {}}
        placeholders = ", ".join("?" for _ in selected_lot_ids)
        people_rows = self.conn.execute(
            f"""
            SELECT DISTINCT p.id
            FROM import_linhas il
            JOIN pessoas p ON p.id = il.registro_id
            WHERE il.lote_id IN ({placeholders})
              AND il.registro_tipo = 'pessoa'
              AND p.ativo = 1
            """,
            tuple(selected_lot_ids),
        ).fetchall()
        people_ids = {moneyless_int(row["id"]) for row in people_rows if moneyless_int(row["id"])}
        if not people_ids:
            return {"import_lot_ids": selected_lot_ids, "people_count": 0, "rows": [], "summary": {}}
        organization_id = self.default_organization_id()
        people_cache = [
            person
            for person in self.people_for_audit_matching(organization_id)
            if moneyless_int(person["id"]) in people_ids
        ]
        if not people_cache:
            return {"import_lot_ids": selected_lot_ids, "people_count": 0, "rows": [], "summary": {}}
        contribution_rows = self.conn.execute(
            """
            SELECT
                c.id AS contribuicao_id,
                c.data_recebimento,
                c.competencia,
                c.valor,
                COALESCE(c.status_operacional, '') AS status_operacional,
                c.pix_movimento_id,
                c.extrato_movimento_id,
                ct.id AS contribuinte_id,
                ct.nome AS contribuinte_nome,
                ct.documento_principal AS contribuinte_documento,
                ct.documento_tipo AS contribuinte_documento_tipo,
                pm.lote_id AS pix_lote_id,
                pm.nome_origem AS pix_nome,
                pm.documento_mascarado AS pix_documento,
                pm.documento_tipo AS pix_documento_tipo,
                pl.nome_arquivo AS pix_arquivo,
                em.lote_id AS extrato_lote_id,
                em.nome_origem AS extrato_nome,
                em.bank_document AS extrato_documento,
                el.nome_arquivo AS extrato_arquivo,
                el.banco AS extrato_banco
            FROM contribuicoes c
            LEFT JOIN contribuintes ct ON ct.id = c.contribuinte_id
            LEFT JOIN pix_movimentos pm ON pm.id = c.pix_movimento_id
            LEFT JOIN pix_lotes pl ON pl.id = pm.lote_id
            LEFT JOIN extrato_movimentos em ON em.id = c.extrato_movimento_id
            LEFT JOIN extrato_lotes el ON el.id = em.lote_id
            WHERE c.ativo = 1
              AND COALESCE(c.pessoa_id, 0) = 0
              AND COALESCE(c.contribuinte_id, 0) > 0
              AND COALESCE(c.status_operacional, '') NOT IN ('ignorado')
            ORDER BY c.data_recebimento, c.id
            """,
        ).fetchall()
        strong_confidences = {"forte_doc_nome", "forte_doc", "forte_nome"}
        probable_confidences = {"provavel_nome", "provavel_doc_amb_nome"}
        grouped: dict[tuple[int, int], dict[str, object]] = {}
        for row in contribution_rows:
            name = normalize_query(row["contribuinte_nome"])
            document = normalize_query(row["contribuinte_documento"])
            document_type = normalize_query(row["contribuinte_documento_tipo"]).lower()
            source_label = "Contribuicao"
            lot_label = ""
            if moneyless_int(row["pix_movimento_id"]):
                name = normalize_query(row["pix_nome"]) or name
                document = normalize_query(row["pix_documento"]) or document
                document_type = normalize_query(row["pix_documento_tipo"]).lower() or document_type
                source_label = "PIX Sicoob"
                lot_label = f"PIX lote {moneyless_int(row['pix_lote_id'])}"
            elif moneyless_int(row["extrato_movimento_id"]):
                name = normalize_query(row["extrato_nome"]) or name
                document = normalize_query(row["extrato_documento"]) or document
                document_type = document_type or santander_document_type(document)
                source_label = normalize_query(row["extrato_banco"]) or "Extrato"
                lot_label = f"Extrato lote {moneyless_int(row['extrato_lote_id'])}"
            match = self.match_pix_entry(
                organization_id,
                name,
                document,
                document_type,
                people_cache=people_cache,
            )
            confidence = str(match.get("confidence") or "")
            person_id = moneyless_int(match.get("person_id"))
            person_name = str(match.get("person_name") or "")
            score = float(match.get("score") or 0.0)
            reason = str(match.get("notes") or "")
            category = ""
            if person_id and confidence in strong_confidences:
                category = "forte"
            elif person_id and confidence in probable_confidences:
                category = "provavel"
            else:
                suggestions = self.pix_candidate_suggestions(
                    organization_id,
                    name,
                    document,
                    document_type,
                    people_cache=people_cache,
                    limit=1,
                )
                if not suggestions:
                    continue
                suggestion = suggestions[0]
                person_id = moneyless_int(suggestion.get("id"))
                person_name = str(suggestion.get("nome") or "")
                confidence = "sugestao_auditoria"
                score = float(suggestion.get("score") or 0.0)
                reason = str(suggestion.get("reason") or "")
                category = "auditoria"
            if not person_id:
                continue
            contributor_id = moneyless_int(row["contribuinte_id"])
            key = (contributor_id, person_id)
            bucket = grouped.setdefault(
                key,
                {
                    "contributor_id": contributor_id,
                    "contributor_name": normalize_query(row["contribuinte_nome"]) or name,
                    "document": normalize_query(row["contribuinte_documento"]) or document,
                    "person_id": person_id,
                    "person_name": person_name,
                    "category": category,
                    "confidence": confidence,
                    "score": score,
                    "reason": reason,
                    "count": 0,
                    "total": 0.0,
                    "first_date": "",
                    "last_date": "",
                    "sources": set(),
                    "lot_labels": set(),
                    "sample_contribution_ids": [],
                },
            )
            category_rank = {"forte": 3, "provavel": 2, "auditoria": 1}
            if category_rank.get(category, 0) > category_rank.get(str(bucket["category"]), 0):
                bucket["category"] = category
                bucket["confidence"] = confidence
                bucket["score"] = score
                bucket["reason"] = reason
                bucket["person_name"] = person_name
            bucket["count"] = moneyless_int(bucket["count"]) + 1
            bucket["total"] = round(float(bucket["total"]) + float(row["valor"] or 0), 2)
            current_date = str(row["data_recebimento"] or "")
            if current_date:
                if not bucket["first_date"] or current_date < str(bucket["first_date"]):
                    bucket["first_date"] = current_date
                if not bucket["last_date"] or current_date > str(bucket["last_date"]):
                    bucket["last_date"] = current_date
            bucket["sources"].add(source_label)
            if lot_label:
                bucket["lot_labels"].add(lot_label)
            if len(bucket["sample_contribution_ids"]) < 6:
                bucket["sample_contribution_ids"].append(moneyless_int(row["contribuicao_id"]))
        rows = []
        for bucket in grouped.values():
            bucket["sources"] = sorted(str(item) for item in bucket["sources"])
            bucket["lot_labels"] = sorted(str(item) for item in bucket["lot_labels"])
            rows.append(bucket)
        rows.sort(
            key=lambda item: (
                {"forte": 0, "provavel": 1, "auditoria": 2}.get(str(item["category"]), 9),
                -float(item["total"]),
                -moneyless_int(item["count"]),
                str(item["contributor_name"]),
            )
        )
        summary = {
            "total_rows": len(rows),
            "strong": sum(1 for row in rows if row["category"] == "forte"),
            "probable": sum(1 for row in rows if row["category"] == "provavel"),
            "audit": sum(1 for row in rows if row["category"] == "auditoria"),
            "events": sum(moneyless_int(row["count"]) for row in rows),
            "value": round(sum(float(row["total"]) for row in rows), 2),
        }
        return {
            "import_lot_ids": selected_lot_ids,
            "people_count": len(people_ids),
            "rows": rows[:limit],
            "summary": summary,
        }

    def contributor_snapshot(self, contributor_id: int) -> dict[str, object]:
        contributor = self.get_contributor(contributor_id)
        if contributor is None:
            return {}
        return {
            "contribuinte": dict(contributor),
            "identificadores": [dict(row) for row in self.contributor_identifiers(contributor_id)],
            "resumo": dict(self.contributor_summary(contributor_id)),
        }

    def link_contributor_to_person(self, contributor_id: int, person_id: int, note: str = "", commit: bool = False) -> bool:
        contributor = self.get_contributor(contributor_id)
        person = self.get_person(person_id)
        if contributor is None or person is None:
            return False
        if moneyless_int(contributor["organizacao_id"]) != moneyless_int(person["organizacao_id"]):
            return False
        if moneyless_int(contributor["pessoa_id"]) == person_id:
            return False
        before = self.contributor_snapshot(contributor_id)
        self.conn.execute(
            """
            UPDATE contribuintes
            SET pessoa_id = ?, atualizado_em = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (person_id, contributor_id),
        )
        self.conn.execute(
            """
            UPDATE contribuintes_identificadores
            SET pessoa_id = ?, atualizado_em = CURRENT_TIMESTAMP
            WHERE contribuinte_id = ? AND ativo = 1 AND (pessoa_id IS NULL OR pessoa_id = ?)
            """,
            (person_id, contributor_id, person_id),
        )
        self.conn.execute(
            """
            UPDATE contribuicoes
            SET pessoa_id = ?, atualizado_em = CURRENT_TIMESTAMP
            WHERE contribuinte_id = ? AND ativo = 1 AND pessoa_id IS NULL
            """,
            (person_id, contributor_id),
        )
        pix_rows = self.conn.execute(
            """
            SELECT id
            FROM pix_movimentos
            WHERE imported_contribution_id IS NOT NULL
              AND ativo = 1
              AND COALESCE(resolved_contribuinte_id, suggested_contribuinte_id) = ?
            """,
            (contributor_id,),
        ).fetchall()
        for pix_row in pix_rows:
            self.sync_imported_contribution_with_pix_movement(moneyless_int(pix_row["id"]), refresh_lot=False)
        self.conn.execute(
            """
            UPDATE pix_movimentos
            SET suggested_person_id = COALESCE(suggested_person_id, ?), atualizado_em = CURRENT_TIMESTAMP
            WHERE suggested_contribuinte_id = ? AND ativo = 1
            """,
            (person_id, contributor_id),
        )
        self.conn.execute(
            """
            UPDATE pix_movimentos
            SET resolved_person_id = COALESCE(resolved_person_id, ?), atualizado_em = CURRENT_TIMESTAMP
            WHERE resolved_contribuinte_id = ? AND ativo = 1
            """,
            (person_id, contributor_id),
        )
        after = self.contributor_snapshot(contributor_id)
        if note:
            after["nota_vinculo"] = note
        self.write_audit_log(
            moneyless_int(person["organizacao_id"]),
            "vincular_contribuinte_pessoa",
            "contribuintes",
            contributor_id,
            before,
            after,
        )
        affected_lots = self.conn.execute(
            """
            SELECT DISTINCT lote_id
            FROM pix_movimentos
            WHERE ativo = 1
              AND COALESCE(resolved_contribuinte_id, suggested_contribuinte_id) = ?
            """,
            (contributor_id,),
        ).fetchall()
        for lot_row in affected_lots:
            self.refresh_pix_lot_status(moneyless_int(lot_row["lote_id"]))
        if commit:
            self.conn.commit()
        return True

    def ensure_person_contributor(self, person_id: int, source: str = "cadastro") -> int:
        person = self.get_person(person_id)
        if person is None:
            return 0
        organization_id = moneyless_int(person["organizacao_id"])
        existing = self.conn.execute(
            """
            SELECT id
            FROM contribuintes
            WHERE organizacao_id = ? AND pessoa_id = ? AND ativo = 1
            ORDER BY id
            LIMIT 1
            """,
            (organization_id, person_id),
        ).fetchone()
        cpf_digits = clean_cpf(person["cpf"])
        if existing is None:
            contributor_id = self.upsert_contributor(
                organization_id,
                str(person["nome"]),
                "pf",
                document_value=cpf_digits,
                document_type="cpf" if cpf_digits else "",
                person_id=person_id,
                source=source,
                quality="doador",
            )
        else:
            contributor_id = moneyless_int(existing["id"])
            self.conn.execute(
                """
                UPDATE contribuintes
                SET nome = ?, nome_normalizado = ?, documento_principal = COALESCE(?, documento_principal),
                    documento_tipo = CASE WHEN ? <> '' THEN ? ELSE documento_tipo END,
                    atualizado_em = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    str(person["nome"]),
                    normalize_match_name(person["nome"]),
                    cpf_digits or None,
                    "cpf" if cpf_digits else "",
                    "cpf" if cpf_digits else "",
                    contributor_id,
                ),
            )
        if cpf_digits:
            exists = self.conn.execute(
                """
                SELECT 1
                FROM contribuintes_identificadores
                WHERE contribuinte_id = ? AND tipo = 'cpf' AND valor = ? AND ativo = 1
                LIMIT 1
                """,
                (contributor_id, cpf_digits),
            ).fetchone()
            if exists is None:
                self.conn.execute(
                    """
                    INSERT INTO contribuintes_identificadores (
                        organizacao_id, pessoa_id, contribuinte_id, tipo, valor, principal, ativo, observacoes
                    ) VALUES (?, ?, ?, 'cpf', ?, 1, 1, ?)
                    """,
                    (organization_id, person_id, contributor_id, cpf_digits, f"Sincronizado a partir do {source}."),
                )
        self.conn.execute(
            """
            UPDATE contribuicoes
            SET contribuinte_id = ?, atualizado_em = CURRENT_TIMESTAMP
            WHERE pessoa_id = ? AND ativo = 1 AND contribuinte_id IS NULL
            """,
            (contributor_id, person_id),
        )
        return contributor_id

    def reconcile_contributors_for_person(self, person_id: int, source: str = "cadastro") -> dict[str, int]:
        person = self.get_person(person_id)
        if person is None:
            return {"linked": 0, "created": 0}
        organization_id = moneyless_int(person["organizacao_id"])
        before_count = self.scalar(
            "SELECT COUNT(*) FROM contribuintes WHERE organizacao_id = ? AND pessoa_id = ? AND ativo = 1",
            (organization_id, person_id),
        )
        person_contributor_id = self.ensure_person_contributor(person_id, source=source)
        linked = 0
        person_norm = normalize_match_name(person["nome"])
        cpf_digits = clean_cpf(person["cpf"])
        identifier_values: set[tuple[str, str]] = set()
        if cpf_digits:
            identifier_values.add(("cpf", cpf_digits))
        for row in self.conn.execute(
            """
            SELECT tipo, valor
            FROM contribuintes_identificadores
            WHERE organizacao_id = ? AND pessoa_id = ? AND ativo = 1
            """,
            (organization_id, person_id),
        ).fetchall():
            identifier_values.add((str(row["tipo"]), str(row["valor"])))
        unique_exact_name = sum(1 for item in self.people_for_pix_matching(organization_id) if str(item["name_norm"]) == person_norm) == 1
        candidates = self.conn.execute(
            """
            SELECT *
            FROM contribuintes
            WHERE organizacao_id = ? AND ativo = 1 AND (pessoa_id IS NULL OR pessoa_id = 0) AND id <> ?
            ORDER BY id
            """,
            (organization_id, person_contributor_id),
        ).fetchall()
        for contributor in candidates:
            should_link = False
            reason = ""
            contributor_doc = str(contributor["documento_principal"] or "")
            contributor_doc_type = str(contributor["documento_tipo"] or "")
            if cpf_digits and contributor_doc_type == "cpf" and clean_cpf(contributor_doc) == cpf_digits:
                should_link = True
                reason = f"Reconciliado automaticamente via {source}: CPF exato."
            elif any(
                match_type == contributor_doc_type and match_value == contributor_doc
                for match_type, match_value in identifier_values
                if contributor_doc
            ):
                should_link = True
                reason = f"Reconciliado automaticamente via {source}: identificador associado exato."
            elif unique_exact_name and normalize_match_name(contributor["nome"]) == person_norm:
                should_link = True
                reason = f"Reconciliado automaticamente via {source}: nome exato unico."
            if should_link and self.link_contributor_to_person(moneyless_int(contributor["id"]), person_id, note=reason, commit=False):
                linked += 1
        created = 1 if before_count == 0 and person_contributor_id else 0
        return {"linked": linked, "created": created, "person_contributor_id": person_contributor_id}

    def create_pix_lot_from_upload(self, filename: str, payload: bytes) -> int:
        if not payload:
            raise ValueError("Selecione um PDF de extrato PIX antes de importar.")
        if not filename.lower().endswith(".pdf"):
            raise ValueError("Envie um arquivo PDF de extrato PIX.")
        organization_id = self.default_organization_id()
        PIX_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        file_hash = hashlib.sha256(payload).hexdigest()
        existing = self.conn.execute(
            "SELECT id FROM pix_lotes WHERE organizacao_id = ? AND hash_arquivo = ? ORDER BY id DESC LIMIT 1",
            (organization_id, file_hash),
        ).fetchone()
        if existing is not None:
            raise ValueError(f"Este PDF ja foi carregado no lote PIX #{existing['id']}. Use o reprocessamento do lote existente.")
        target_name = f"{date.today().isoformat()}_{slugify_filename_text(Path(filename).stem, fallback='pix')}_{file_hash[:10]}.pdf"
        stored_path = PIX_UPLOAD_DIR / target_name
        stored_path.write_bytes(payload)
        parsed = parse_sicoob_pix_pdf(stored_path)
        cursor = self.conn.execute(
            """
            INSERT INTO pix_lotes (
                organizacao_id, banco, nome_arquivo, caminho_arquivo, hash_arquivo,
                periodo_inicio, periodo_fim, total_movimentos, total_valor, status, observacoes, atualizado_em
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, 'carregado', NULL, CURRENT_TIMESTAMP)
            """,
            (
                organization_id,
                parsed["bank_name"],
                filename,
                str(stored_path),
                parsed["file_hash"],
                parsed["period_start"],
                parsed["period_end"],
            ),
        )
        lot_id = moneyless_int(cursor.lastrowid)
        people_cache = self.people_for_pix_matching(organization_id)
        rules_by_code = {str(row["codigo_centavos"]): row for row in self.pix_rules(organization_id) if moneyless_int(row["ativo"])}
        total_value = 0.0
        lot_signature_occurrences: dict[str, int] = {}
        for entry in parsed["entries"]:
            donor_name = str(entry["donor_name"])
            donor_doc = str(entry["document_mask"])
            document_type = str(entry["document_type"])
            match = self.match_pix_entry(
                organization_id,
                donor_name,
                donor_doc,
                document_type,
                people_cache=people_cache,
            )
            contributor_kind = contributor_kind_for_identity(
                donor_name,
                document_type=document_type,
                document_value=donor_doc,
            )
            suggested_person_id = moneyless_int(match["person_id"])
            suggested_contributor_id = self.upsert_contributor(
                organization_id,
                donor_name,
                contributor_kind,
                document_value=donor_doc,
                document_type=document_type or ("cnpj_mascarado" if contributor_kind == "pj" else "cpf_mascarado"),
                person_id=suggested_person_id,
                source="pix",
                quality="doador",
            )
            cent_code = str(entry["cent_code"])
            rule_row = rules_by_code.get(cent_code)
            type_suggested = core_designations.suggested_type_for_cent_rule(rule_row)
            review_status = self.classify_pix_review_status(str(match["confidence"]), special_rule=rule_row is not None)
            signature_global = pix_global_signature(
                entry["received_on"],
                entry["amount"],
                entry["donor_name_normalized"],
                donor_doc,
                document_type,
                entry["raw_text"],
            )
            occurrence_index = lot_signature_occurrences.get(signature_global, 0) + 1
            lot_signature_occurrences[signature_global] = occurrence_index
            duplicate_state = self.find_pix_duplicate_targets(
                organization_id,
                signature_global,
                str(entry["received_on"]),
                float(entry["amount"]),
                person_id=suggested_person_id,
                contributor_id=suggested_contributor_id,
                occurrence_index=occurrence_index,
                ignore_lot_id=lot_id,
            )
            if duplicate_state["review_status"]:
                review_status = str(duplicate_state["review_status"])
            fingerprint_source = "|".join(
                [
                    str(entry["received_on"]),
                    f"{float(entry['amount']):.2f}",
                    normalize_match_name(donor_name),
                    donor_doc,
                    str(entry["page_number"]),
                    str(entry["order_in_file"]),
                ]
            )
            fingerprint = hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest()
            self.conn.execute(
                """
                INSERT INTO pix_movimentos (
                    lote_id, organizacao_id, pagina, ordem_no_lote, data_recebimento, competencia, competencia_ordem,
                    valor, codigo_centavos, nome_origem, nome_normalizado, documento_mascarado, documento_tipo,
                    tipo_sugerido, regra_id, confidence, match_score, suggested_person_id, suggested_contribuinte_id,
                    resolved_person_id, resolved_contribuinte_id, resolved_tipo_contribuicao_id, review_status,
                    review_notes, raw_text, fingerprint, signature_global, duplicate_movement_id, duplicate_contribution_id,
                    duplicate_reason, imported_contribution_id, ativo, atualizado_em
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 1, CURRENT_TIMESTAMP)
                """,
                (
                    lot_id,
                    organization_id,
                    moneyless_int(entry["page_number"]),
                    moneyless_int(entry["order_in_file"]),
                    str(entry["received_on"]),
                    str(entry["competencia"]),
                    moneyless_int(entry["competencia_ordem"]),
                    float(entry["amount"]),
                    cent_code,
                    donor_name,
                    str(entry["donor_name_normalized"]),
                    donor_doc or None,
                    document_type or None,
                    type_suggested,
                    moneyless_int(rule_row["id"]) if rule_row else None,
                    str(match["confidence"]),
                    float(match["score"]),
                    suggested_person_id or None,
                    suggested_contributor_id or None,
                    review_status,
                    str(duplicate_state["duplicate_reason"] or match["notes"]),
                    str(entry["raw_text"]),
                    fingerprint,
                    signature_global,
                    moneyless_int(duplicate_state["duplicate_movement_id"]) or None,
                    moneyless_int(duplicate_state["duplicate_contribution_id"]) or None,
                    str(duplicate_state["duplicate_reason"] or "") or None,
                ),
            )
            total_value += float(entry["amount"])
        movement_count = self.scalar("SELECT COUNT(*) FROM pix_movimentos WHERE lote_id = ?", (lot_id,))
        self.conn.execute(
            """
            UPDATE pix_lotes
            SET total_movimentos = ?, total_valor = ?, status = 'auditando', atualizado_em = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (movement_count, round(total_value, 2), lot_id),
        )
        saved = self.get_pix_lot(lot_id)
        self.write_audit_log(organization_id, "criar_lote_pix", "pix_lotes", lot_id, None, dict(saved) if saved else {"id": lot_id})
        self.ensure_pix_financial_entries(lot_id)
        self.refresh_pix_lot_status(lot_id)
        self.conn.commit()
        return lot_id

    def pix_lot_manual_association_map(self, lot_id: int) -> dict[str, dict[str, object]]:
        rows = self.conn.execute(
            """
            SELECT nome_origem, documento_mascarado, resolved_person_id, resolved_contribuinte_id, review_notes,
                   COALESCE(association_reviewed, 0) AS association_reviewed
            FROM pix_movimentos
            WHERE lote_id = ? AND ativo = 1 AND (resolved_person_id IS NOT NULL OR COALESCE(association_reviewed, 0) = 1)
            ORDER BY id
            """,
            (lot_id,),
        ).fetchall()
        grouped: dict[str, dict[str, object]] = {}
        for row in rows:
            key = pix_association_key(row["nome_origem"], row["documento_mascarado"])
            if not key:
                continue
            bucket = grouped.setdefault(
                key,
                {
                    "person_ids": set(),
                    "contributor_ids": set(),
                    "notes": [],
                    "reviewed_without_person": False,
                },
            )
            bucket["person_ids"].add(moneyless_int(row["resolved_person_id"]))
            contributor_id = moneyless_int(row["resolved_contribuinte_id"])
            if contributor_id:
                bucket["contributor_ids"].add(contributor_id)
            if moneyless_int(row["association_reviewed"]) and not moneyless_int(row["resolved_person_id"]):
                bucket["reviewed_without_person"] = True
            note_text = normalize_query(row["review_notes"])
            if note_text:
                bucket["notes"].append(note_text)
        result: dict[str, dict[str, object]] = {}
        for key, bucket in grouped.items():
            person_ids = {item for item in bucket["person_ids"] if item}
            contributor_ids = {item for item in bucket["contributor_ids"] if item}
            if person_ids:
                if len(person_ids) != 1:
                    continue
                result[key] = {
                    "person_id": next(iter(person_ids)),
                    "contributor_id": next(iter(contributor_ids)) if len(contributor_ids) == 1 else 0,
                    "note": bucket["notes"][0] if bucket["notes"] else "",
                    "reviewed_without_person": False,
                }
            elif bucket["reviewed_without_person"]:
                result[key] = {
                    "person_id": 0,
                    "contributor_id": next(iter(contributor_ids)) if len(contributor_ids) == 1 else 0,
                    "note": bucket["notes"][0] if bucket["notes"] else "",
                    "reviewed_without_person": True,
                }
        return result

    def reprocess_pix_lot(self, lot_id: int) -> int:
        lot = self.get_pix_lot(lot_id)
        if lot is None:
            raise ValueError("Lote PIX nao encontrado.")
        if str(lot["status"]) == "encerrado":
            raise ValueError("Este lote ja foi encerrado. Daqui em diante, siga pela fila de contribuintes pendentes de associacao.")
        organization_id = moneyless_int(lot["organizacao_id"])
        people_cache = self.people_for_pix_matching(organization_id)
        rules_by_code = {str(row["codigo_centavos"]): row for row in self.pix_rules(organization_id) if moneyless_int(row["ativo"])}
        reparsed = parse_sicoob_pix_pdf(Path(str(lot["caminho_arquivo"])))
        reparsed_entries_by_order = {
            moneyless_int(entry["order_in_file"]): entry
            for entry in reparsed["entries"]
        }
        manual_association_map = self.pix_lot_manual_association_map(lot_id)
        rows = self.conn.execute(
            """
            SELECT *
            FROM pix_movimentos
            WHERE lote_id = ? AND ativo = 1 AND review_status <> 'ignorado'
            ORDER BY ordem_no_lote
            """,
            (lot_id,),
        ).fetchall()
        if len(reparsed_entries_by_order) != len(rows):
            raise ValueError(
                f"O PDF reprocessado trouxe {len(reparsed_entries_by_order)} movimento(s), mas o lote possui {len(rows)} ativo(s). "
                "Antes de regravar o lote, use a auditoria de importacoes para revisar esse desvio."
            )
        updated = 0
        for row in rows:
            before = dict(row)
            entry = reparsed_entries_by_order.get(moneyless_int(row["ordem_no_lote"]))
            if entry is None:
                continue
            donor_name = str(entry["donor_name"])
            donor_doc = str(entry["document_mask"] or "")
            document_type = str(entry["document_type"] or "")
            match = self.match_pix_entry(
                organization_id,
                donor_name,
                donor_doc,
                document_type,
                people_cache=people_cache,
            )
            suggested_person_id = moneyless_int(match["person_id"])
            contributor_kind = contributor_kind_for_identity(
                donor_name,
                document_type=document_type,
                document_value=donor_doc,
            )
            suggested_contributor_id = self.upsert_contributor(
                organization_id,
                donor_name,
                contributor_kind,
                document_value=donor_doc,
                document_type=document_type or ("cnpj_mascarado" if contributor_kind == "pj" else "cpf_mascarado"),
                person_id=suggested_person_id,
                source="pix",
                quality="doador",
            )
            cent_code = str(entry["cent_code"])
            rule_row = rules_by_code.get(cent_code)
            review_status = self.classify_pix_review_status(str(match["confidence"]), special_rule=rule_row is not None)
            signature_global = pix_global_signature(
                entry["received_on"],
                entry["amount"],
                entry["donor_name_normalized"],
                donor_doc,
                document_type,
                entry["raw_text"],
            )
            occurrence_index = self.pix_lot_occurrence_index(
                lot_id,
                signature_global,
                moneyless_int(row["ordem_no_lote"]),
                movement_id=moneyless_int(row["id"]),
            )
            duplicate_state = self.find_pix_duplicate_targets(
                organization_id,
                signature_global,
                str(entry["received_on"]),
                float(entry["amount"]),
                person_id=suggested_person_id,
                contributor_id=suggested_contributor_id,
                occurrence_index=occurrence_index,
                ignore_movement_id=moneyless_int(row["id"]),
                ignore_lot_id=lot_id,
            )
            if duplicate_state["review_status"]:
                review_status = str(duplicate_state["review_status"])
            resolved_person_id = 0
            resolved_contributor_id = 0
            association_reviewed = 0
            preserved_manual = manual_association_map.get(pix_association_key(donor_name, donor_doc), {})
            if preserved_manual:
                resolved_person_id = moneyless_int(preserved_manual.get("person_id"))
                association_reviewed = 1 if preserved_manual.get("reviewed_without_person") and not resolved_person_id else 0
                if resolved_person_id:
                    resolved_contributor_id = self.upsert_contributor(
                        organization_id,
                        donor_name,
                        contributor_kind,
                        document_value=donor_doc,
                        document_type=document_type or ("cnpj_mascarado" if contributor_kind == "pj" else "cpf_mascarado"),
                        person_id=resolved_person_id,
                        source="pix",
                        quality="doador",
                    )
                    self.link_contributor_to_person(
                        resolved_contributor_id,
                        resolved_person_id,
                        note="Reaplicado automaticamente durante o reprocessamento do lote PIX.",
                        commit=False,
                    )
                elif association_reviewed:
                    resolved_contributor_id = moneyless_int(preserved_manual.get("contributor_id"))
                    if not resolved_contributor_id:
                        resolved_contributor_id = self.upsert_contributor(
                            organization_id,
                            donor_name,
                            contributor_kind,
                            document_value=donor_doc,
                            document_type=document_type or ("cnpj_mascarado" if contributor_kind == "pj" else "cpf_mascarado"),
                            source="pix",
                            quality="doador",
                        )
                review_status = "aprovado"
            row_notes = str(duplicate_state["duplicate_reason"] or match["notes"])
            if preserved_manual.get("note"):
                row_notes = merge_statement_review_notes(row_notes, preserved_manual.get("note"))
            self.conn.execute(
                """
                UPDATE pix_movimentos
                SET pagina = ?, data_recebimento = ?, competencia = ?, competencia_ordem = ?,
                    valor = ?, codigo_centavos = ?, nome_origem = ?, nome_normalizado = ?,
                    documento_mascarado = ?, documento_tipo = ?, tipo_sugerido = ?, regra_id = ?,
                    confidence = ?, match_score = ?, suggested_person_id = ?, suggested_contribuinte_id = ?,
                    resolved_person_id = ?, resolved_contribuinte_id = ?, association_reviewed = ?,
                    review_status = ?, review_notes = ?, raw_text = ?, signature_global = ?,
                    duplicate_movement_id = ?, duplicate_contribution_id = ?, duplicate_reason = ?,
                    atualizado_em = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    moneyless_int(entry["page_number"]),
                    str(entry["received_on"]),
                    str(entry["competencia"]),
                    moneyless_int(entry["competencia_ordem"]),
                    float(entry["amount"]),
                    cent_code,
                    donor_name,
                    str(entry["donor_name_normalized"]),
                    donor_doc or None,
                    document_type or None,
                    core_designations.suggested_type_for_cent_rule(rule_row),
                    moneyless_int(rule_row["id"]) if rule_row else None,
                    str(match["confidence"]),
                    float(match["score"]),
                    suggested_person_id or None,
                    suggested_contributor_id or None,
                    resolved_person_id or None,
                    resolved_contributor_id or None,
                    association_reviewed,
                    review_status,
                    row_notes,
                    str(entry["raw_text"]),
                    signature_global,
                    moneyless_int(duplicate_state["duplicate_movement_id"]) or None,
                    moneyless_int(duplicate_state["duplicate_contribution_id"]) or None,
                    str(duplicate_state["duplicate_reason"] or "") or None,
                    row["id"],
                ),
            )
            after_row = self.conn.execute("SELECT * FROM pix_movimentos WHERE id = ?", (row["id"],)).fetchone()
            self.write_audit_log(organization_id, "reprocessar_movimento_pix", "pix_movimentos", moneyless_int(row["id"]), before, dict(after_row) if after_row else None)
            updated += 1
        self.ensure_pix_financial_entries(lot_id)
        self.refresh_pix_lot_status(lot_id)
        self.conn.commit()
        return updated

    def apply_pix_no_person_to_same_name_in_lot(
        self,
        movement_id: int,
        resolved_contributor_id: int,
        review_notes: str = "",
    ) -> int:
        anchor = self.get_pix_movement(movement_id)
        if anchor is None:
            return 0
        lot_id = moneyless_int(anchor["lote_id"])
        layout_code = normalize_query(anchor["layout_codigo"]).upper()
        contributor_source = statement_layout_contributor_source(anchor["layout_codigo"])
        normalized_name = normalize_query(anchor["nome_normalizado"])
        if not lot_id or not normalized_name:
            return 0
        rows = self.conn.execute(
            """
            SELECT *
            FROM pix_movimentos
            WHERE lote_id = ? AND ativo = 1 AND id <> ? AND COALESCE(nome_normalizado, '') = ?
              AND review_status <> 'ignorado'
              AND COALESCE(association_reviewed, 0) = 0
              AND COALESCE(resolved_person_id, 0) = 0
            ORDER BY ordem_no_lote, id
            """,
            (lot_id, movement_id, normalized_name),
        ).fetchall()
        applied = 0
        batch_note = (
            f"Decisao em lote aplicada a partir do movimento #{movement_id}: manter como NR / contribuinte auxiliar "
            f"para o nome bancario '{normalize_query(anchor['nome_origem']) or normalized_name}'."
        )
        combined_note = merge_statement_review_notes(review_notes, batch_note)
        for row in rows:
            before = dict(row)
            contributor_id = resolved_contributor_id or moneyless_int(row["resolved_contribuinte_id"]) or moneyless_int(row["suggested_contribuinte_id"])
            if not contributor_id and normalize_query(row["nome_origem"]):
                contributor_kind = contributor_kind_for_identity(
                    row["nome_origem"],
                    document_type=row["documento_tipo"],
                    document_value=row["documento_mascarado"],
                )
                contributor_id = self.upsert_contributor(
                    moneyless_int(row["organizacao_id"]),
                    str(row["nome_origem"]),
                    contributor_kind,
                    document_value=str(row["documento_mascarado"] or ""),
                    document_type=str(row["documento_tipo"] or "") or ("cnpj_mascarado" if contributor_kind == "pj" else "cpf_mascarado"),
                    source="pix",
                    quality="doador",
                )
            row_notes = merge_statement_review_notes(row["review_notes"], combined_note)
            self.conn.execute(
                """
                UPDATE pix_movimentos
                SET resolved_person_id = NULL,
                    resolved_contribuinte_id = ?,
                    review_status = 'aprovado',
                    association_reviewed = 1,
                    review_notes = ?,
                    atualizado_em = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    contributor_id or None,
                    row_notes,
                    row["id"],
                ),
            )
            after = self.movement_snapshot(moneyless_int(row["id"]))
            self.write_audit_log(
                moneyless_int(row["organizacao_id"]),
                "marcar_pix_nr_em_lote",
                "pix_movimentos",
                moneyless_int(row["id"]),
                before,
                after,
            )
            if moneyless_int(row["imported_contribution_id"]):
                self.sync_imported_contribution_with_pix_movement(moneyless_int(row["id"]), refresh_lot=False)
            else:
                self.import_single_pix_movement(moneyless_int(row["id"]), refresh_lot=False)
            applied += 1
        return applied

    def movement_snapshot(self, movement_id: int) -> dict[str, object]:
        row = self.get_pix_movement(movement_id)
        return dict(row) if row else {}

    def resolved_pix_type_id_for_row(self, row: sqlite3.Row) -> int:
        organization_id = moneyless_int(row["organizacao_id"])
        type_id = moneyless_int(row["resolved_tipo_contribuicao_id"])
        if not type_id and moneyless_int(row["regra_id"]):
            rule = self.conn.execute("SELECT tipo_contribuicao_id FROM pix_centavo_regras WHERE id = ?", (row["regra_id"],)).fetchone()
            type_id = moneyless_int(rule["tipo_contribuicao_id"] if rule else 0)
        if not type_id:
            type_id = self.pix_default_type_id(organization_id)
        return type_id

    def pix_contribution_person_id_for_row(self, row: sqlite3.Row) -> int | None:
        resolved_person_id = moneyless_int(row["resolved_person_id"])
        suggested_person_id = moneyless_int(row["suggested_person_id"])
        review_status = str(row["review_status"] or "")
        if resolved_person_id:
            return resolved_person_id
        if moneyless_int(row["association_reviewed"]):
            return None
        if review_status in {"pronto", "aprovado", "importado", "revisar_destinacao"}:
            return suggested_person_id or None
        return None

    def pix_contribution_contributor_id_for_row(self, row: sqlite3.Row) -> int | None:
        return moneyless_int(row["resolved_contribuinte_id"]) or moneyless_int(row["suggested_contribuinte_id"]) or None

    def pix_contribution_status_for_row(self, row: sqlite3.Row, person_id: int = 0) -> str:
        review_status = str(row["review_status"] or "")
        if review_status == "ignorado":
            return "ignorado"
        if review_status == "revisar_duplicidade":
            return "duplicidade_suspeita"
        if not person_id:
            return "sem_associacao"
        if review_status == "revisar_destinacao":
            return "classificacao_pendente"
        return "regular"

    def pix_notes_for_row(self, row: sqlite3.Row) -> str:
        notes_parts = [
            f"Importado do lote PIX #{row['lote_id']}",
            f"Origem: {row['nome_origem']}",
        ]
        if row["documento_mascarado"]:
            notes_parts.append(f"Documento mascarado: {row['documento_mascarado']}")
        if row["review_notes"]:
            notes_parts.append(str(row["review_notes"]))
        return " | ".join(notes_parts)

    def sync_imported_contribution_with_pix_movement(self, movement_id: int, refresh_lot: bool = True) -> int:
        row = self.conn.execute(
            """
            SELECT *
            FROM pix_movimentos
            WHERE id = ? AND ativo = 1
            """,
            (movement_id,),
        ).fetchone()
        if row is None:
            raise ValueError("Movimento PIX nao encontrado.")
        contribution_id = moneyless_int(row["imported_contribution_id"])
        if not contribution_id:
            return 0
        contribution = self.get_contribution(contribution_id)
        if contribution is None:
            return 0
        before = dict(contribution)
        organization_id = moneyless_int(row["organizacao_id"])
        pix_form_id = self.pix_receiving_form_id(organization_id) or None
        type_id = self.resolved_pix_type_id_for_row(row)
        if not type_id:
            raise ValueError("Nao foi possivel determinar o tipo de contribuicao para este movimento PIX.")
        person_id = self.pix_contribution_person_id_for_row(row)
        contributor_id = self.pix_contribution_contributor_id_for_row(row)
        status_operacional = self.pix_contribution_status_for_row(row, moneyless_int(person_id))
        campaign_id = self.cent_rule_campaign_id_for_type(moneyless_int(row["regra_id"]), type_id)
        self.conn.execute(
            """
            UPDATE contribuicoes
            SET pessoa_id = ?, contribuinte_id = ?, tipo_contribuicao_id = ?, data_recebimento = ?,
                competencia = ?, competencia_ordem = ?, valor = ?, forma_recebimento_id = ?,
                campanha_id = ?, observacoes = ?, status_operacional = ?, atualizado_em = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                person_id,
                contributor_id,
                type_id,
                str(row["data_recebimento"]),
                str(row["competencia"]),
                moneyless_int(row["competencia_ordem"]),
                float(row["valor"]),
                pix_form_id,
                campaign_id,
                self.pix_notes_for_row(row),
                status_operacional,
                contribution_id,
            ),
        )
        after = self.get_contribution(contribution_id)
        self.write_audit_log(
            organization_id,
            "atualizar_contribuicao_pix_importada",
            "contribuicoes",
            contribution_id,
            before,
            dict(after) if after else None,
        )
        if refresh_lot:
            self.refresh_pix_lot_status(moneyless_int(row["lote_id"]))
        return contribution_id

    def auto_import_ready_pix_lot(self, lot_id: int) -> int:
        rows = self.conn.execute(
            """
            SELECT id
            FROM pix_movimentos
            WHERE lote_id = ? AND ativo = 1 AND imported_contribution_id IS NULL AND review_status = 'pronto'
            ORDER BY data_recebimento, ordem_no_lote
            """,
            (lot_id,),
        ).fetchall()
        imported = 0
        for row in rows:
            self.import_single_pix_movement(moneyless_int(row["id"]), refresh_lot=False)
            imported += 1
        self.refresh_pix_lot_status(lot_id)
        return imported

    def import_single_pix_movement(self, movement_id: int, refresh_lot: bool = True) -> int:
        row = self.conn.execute(
            """
            SELECT *
            FROM pix_movimentos
            WHERE id = ? AND ativo = 1
            """,
            (movement_id,),
        ).fetchone()
        if row is None:
            raise ValueError("Movimento PIX nao encontrado.")
        if moneyless_int(row["imported_contribution_id"]):
            return self.sync_imported_contribution_with_pix_movement(movement_id, refresh_lot=refresh_lot)
        organization_id = moneyless_int(row["organizacao_id"])
        pix_form_id = self.pix_receiving_form_id(organization_id) or None
        type_id = self.resolved_pix_type_id_for_row(row)
        if not type_id:
            raise ValueError("Nao foi possivel determinar o tipo de contribuicao para este movimento PIX.")
        person_id = self.pix_contribution_person_id_for_row(row)
        contributor_id = self.pix_contribution_contributor_id_for_row(row)
        status_operacional = self.pix_contribution_status_for_row(row, moneyless_int(person_id))
        campaign_id = self.cent_rule_campaign_id_for_type(moneyless_int(row["regra_id"]), type_id)
        cursor = self.conn.execute(
            """
            INSERT INTO contribuicoes (
                organizacao_id, unidade_id, pessoa_id, contribuinte_id, tipo_contribuicao_id,
                campanha_id, data_recebimento, competencia, competencia_ordem,
                valor, forma_recebimento_id, conta_financeira_id, observacoes,
                import_lote_id, pix_movimento_id, status_operacional, ativo, atualizado_em
            ) VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL, ?, ?, 1, CURRENT_TIMESTAMP)
            """,
            (
                organization_id,
                person_id,
                contributor_id,
                type_id,
                campaign_id,
                str(row["data_recebimento"]),
                str(row["competencia"]),
                moneyless_int(row["competencia_ordem"]),
                float(row["valor"]),
                pix_form_id,
                self.pix_notes_for_row(row),
                movement_id,
                status_operacional,
            ),
        )
        contribution_id = moneyless_int(cursor.lastrowid)
        saved = self.get_contribution(contribution_id)
        self.write_audit_log(
            organization_id,
            "importar_movimento_pix",
            "contribuicoes",
            contribution_id,
            None,
            dict(saved) if saved else {"id": contribution_id},
        )
        self.conn.execute(
            """
            UPDATE pix_movimentos
            SET imported_contribution_id = ?, atualizado_em = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (contribution_id, movement_id),
        )
        if refresh_lot:
            self.refresh_pix_lot_status(moneyless_int(row["lote_id"]))
        return contribution_id

    def ensure_pix_financial_entries(self, lot_id: int = 0) -> int:
        clauses = ["ativo = 1", "review_status <> 'ignorado'"]
        params: list[object] = []
        if lot_id:
            clauses.append("lote_id = ?")
            params.append(lot_id)
        rows = self.conn.execute(
            f"""
            SELECT id, lote_id, imported_contribution_id
            FROM pix_movimentos
            WHERE {' AND '.join(clauses)}
            ORDER BY lote_id, ordem_no_lote, id
            """,
            params,
        ).fetchall()
        created = 0
        affected_lots: set[int] = set()
        for row in rows:
            movement_id = moneyless_int(row["id"])
            affected_lots.add(moneyless_int(row["lote_id"]))
            if moneyless_int(row["imported_contribution_id"]):
                self.sync_imported_contribution_with_pix_movement(movement_id, refresh_lot=False)
                continue
            self.import_single_pix_movement(movement_id, refresh_lot=False)
            created += 1
        for affected_lot in sorted(item for item in affected_lots if item):
            self.refresh_pix_lot_status(affected_lot)
        return created

    def promote_linked_pix_sem_associacao(self, lot_id: int = 0) -> int:
        clauses = [
            "m.ativo = 1",
            "c.ativo = 1",
            "c.status_operacional = 'sem_associacao'",
            "COALESCE(m.association_reviewed, 0) = 1",
            "COALESCE(m.resolved_person_id, 0) = 0",
            "COALESCE(m.suggested_person_id, 0) > 0",
            "ct.pessoa_id = m.suggested_person_id",
            "COALESCE(m.resolved_contribuinte_id, m.suggested_contribuinte_id) = ct.id",
        ]
        params: list[object] = []
        if lot_id:
            clauses.append("m.lote_id = ?")
            params.append(lot_id)
        rows = self.conn.execute(
            f"""
            SELECT m.*, c.id AS contribution_id, ct.id AS contributor_id, ct.pessoa_id AS linked_person_id
            FROM pix_movimentos m
            JOIN contribuicoes c ON c.id = m.imported_contribution_id
            JOIN contribuintes ct ON ct.id = COALESCE(m.resolved_contribuinte_id, m.suggested_contribuinte_id)
            WHERE {' AND '.join(clauses)}
            ORDER BY m.lote_id, m.ordem_no_lote, m.id
            """,
            params,
        ).fetchall()
        promoted = 0
        affected_lots: set[int] = set()
        promotion_note = (
            "Vinculo promovido automaticamente apos auditoria: o contribuinte financeiro ja estava "
            "vinculado a mesma pessoa sugerida pelo movimento PIX."
        )
        for row in rows:
            movement_id = moneyless_int(row["id"])
            lot_id_row = moneyless_int(row["lote_id"])
            linked_person_id = moneyless_int(row["linked_person_id"])
            contributor_id = moneyless_int(row["contributor_id"])
            if not movement_id or not linked_person_id or not contributor_id:
                continue
            before = self.movement_snapshot(movement_id)
            merged_notes = merge_statement_review_notes(row["review_notes"], promotion_note)
            self.conn.execute(
                """
                UPDATE pix_movimentos
                SET resolved_person_id = ?,
                    resolved_contribuinte_id = COALESCE(resolved_contribuinte_id, ?),
                    association_reviewed = 0,
                    review_notes = ?,
                    atualizado_em = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    linked_person_id,
                    contributor_id,
                    merged_notes,
                    movement_id,
                ),
            )
            after = self.movement_snapshot(movement_id)
            self.write_audit_log(
                moneyless_int(row["organizacao_id"]),
                "promover_vinculo_pix_por_contribuinte",
                "pix_movimentos",
                movement_id,
                before,
                after,
            )
            self.sync_imported_contribution_with_pix_movement(movement_id, refresh_lot=False)
            affected_lots.add(lot_id_row)
            promoted += 1
        for affected_lot in sorted(item for item in affected_lots if item):
            self.refresh_pix_lot_status(affected_lot)
        return promoted

    def promote_strong_pix_sem_associacao(self, lot_id: int = 0) -> int:
        clauses = [
            "m.ativo = 1",
            "c.ativo = 1",
            "c.status_operacional = 'sem_associacao'",
            "COALESCE(m.association_reviewed, 0) = 1",
            "COALESCE(m.resolved_person_id, 0) = 0",
        ]
        params: list[object] = []
        if lot_id:
            clauses.append("m.lote_id = ?")
            params.append(lot_id)
        rows = self.conn.execute(
            f"""
            SELECT m.*, c.id AS contribution_id, COALESCE(m.resolved_contribuinte_id, m.suggested_contribuinte_id, c.contribuinte_id) AS contributor_id
            FROM pix_movimentos m
            JOIN contribuicoes c ON c.id = m.imported_contribution_id
            WHERE {' AND '.join(clauses)}
            ORDER BY m.lote_id, m.ordem_no_lote, m.id
            """,
            params,
        ).fetchall()
        promoted = 0
        affected_lots: set[int] = set()
        cache_by_org: dict[int, list[sqlite3.Row]] = {}
        for row in rows:
            organization_id = moneyless_int(row["organizacao_id"])
            if organization_id not in cache_by_org:
                cache_by_org[organization_id] = self.people_for_pix_matching(organization_id)
            match = self.match_pix_entry(
                organization_id,
                str(row["nome_origem"]),
                str(row["documento_mascarado"] or ""),
                str(row["documento_tipo"] or ""),
                people_cache=cache_by_org[organization_id],
            )
            confidence = str(match["confidence"] or "")
            person_id = moneyless_int(match["person_id"])
            contributor_id = moneyless_int(row["contributor_id"])
            if confidence not in {"forte_doc_nome", "forte_doc", "forte_nome"} or not person_id or not contributor_id:
                continue
            contributor = self.get_contributor(contributor_id)
            if contributor is None:
                continue
            existing_person_id = moneyless_int(contributor["pessoa_id"])
            if existing_person_id and existing_person_id != person_id:
                continue
            if not existing_person_id:
                self.link_contributor_to_person(
                    contributor_id,
                    person_id,
                    note="Vinculo forte promovido automaticamente apos saneamento do lote PIX.",
                    commit=False,
                )
            movement_id = moneyless_int(row["id"])
            before = self.movement_snapshot(movement_id)
            promotion_note = (
                "Vinculo forte promovido automaticamente apos saneamento: documento mascarado unico "
                "e nome financeiro confirmaram a ficha."
            )
            merged_notes = merge_statement_review_notes(row["review_notes"], promotion_note)
            self.conn.execute(
                """
                UPDATE pix_movimentos
                SET suggested_person_id = ?,
                    resolved_person_id = ?,
                    resolved_contribuinte_id = COALESCE(resolved_contribuinte_id, ?),
                    association_reviewed = 0,
                    review_notes = ?,
                    atualizado_em = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    person_id,
                    person_id,
                    contributor_id,
                    merged_notes,
                    movement_id,
                ),
            )
            after = self.movement_snapshot(movement_id)
            self.write_audit_log(
                organization_id,
                "promover_vinculo_forte_pix",
                "pix_movimentos",
                movement_id,
                before,
                after,
            )
            self.sync_imported_contribution_with_pix_movement(movement_id, refresh_lot=False)
            affected_lots.add(moneyless_int(row["lote_id"]))
            promoted += 1
        for affected_lot in sorted(item for item in affected_lots if item):
            self.refresh_pix_lot_status(affected_lot)
        return promoted

    def update_pix_movement_from_form(self, movement_id: int, form: dict[str, list[str]]) -> int:
        movement = self.get_pix_movement(movement_id)
        if movement is None:
            raise ValueError("Movimento PIX nao encontrado.")
        organization_id = moneyless_int(movement["organizacao_id"])
        action = first_form_value(form, "action", "approve")
        before = self.movement_snapshot(movement_id)
        imported_contribution_id = 0
        if action == "ignore":
            if moneyless_int(movement["imported_contribution_id"]):
                raise ValueError("Este movimento ja foi importado para contribuicoes. Em vez de ignorar, ajuste o vinculo ou a classificacao para manter o historico consistente.")
            self.conn.execute(
                """
                UPDATE pix_movimentos
                SET review_status = 'ignorado', review_notes = ?, atualizado_em = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (first_form_value(form, "review_notes"), movement_id),
            )
        else:
            selected_person_id = moneyless_int(form.get("resolved_person_id", ["0"])[0])
            selected_type_id = moneyless_int(form.get("resolved_tipo_contribuicao_id", ["0"])[0])
            if not selected_type_id:
                selected_type_id = moneyless_int(movement["resolved_tipo_contribuicao_id"]) or moneyless_int(
                    self.pix_default_type_id(organization_id)
                )
                if moneyless_int(movement["regra_id"]):
                    rule = self.conn.execute("SELECT tipo_contribuicao_id FROM pix_centavo_regras WHERE id = ?", (movement["regra_id"],)).fetchone()
                    if rule and moneyless_int(rule["tipo_contribuicao_id"]):
                        selected_type_id = moneyless_int(rule["tipo_contribuicao_id"])
            effective_rule_id, type_suggestion, rule_overridden = self.cent_rule_override_decision(
                organization_id,
                moneyless_int(movement["regra_id"]),
                selected_type_id,
                movement["tipo_sugerido"],
            )
            review_notes = first_form_value(form, "review_notes")
            if rule_overridden:
                review_notes = merge_statement_review_notes(
                    review_notes,
                    "Regra de centavos substituida manualmente pelo operador nesta auditoria.",
                )
            contributor_kind = contributor_kind_for_identity(
                movement["nome_origem"],
                document_type=movement["documento_tipo"],
                document_value=movement["documento_mascarado"],
            )
            resolved_contributor_id = self.upsert_contributor(
                organization_id,
                str(movement["nome_origem"]),
                contributor_kind,
                document_value=str(movement["documento_mascarado"] or ""),
                document_type=str(movement["documento_tipo"] or "") or ("cnpj_mascarado" if contributor_kind == "pj" else "cpf_mascarado"),
                person_id=selected_person_id,
                source="pix",
                quality="doador",
            )
            if selected_person_id and first_form_value(form, "associate_masked_document") == "1" and movement["documento_mascarado"]:
                identifier_type = "cnpj_mascarado" if str(movement["documento_tipo"] or "") == "cnpj" else "cpf_mascarado"
                exists = self.conn.execute(
                    """
                    SELECT 1
                    FROM contribuintes_identificadores
                    WHERE pessoa_id = ? AND tipo = ? AND valor = ? AND ativo = 1
                    LIMIT 1
                    """,
                    (selected_person_id, identifier_type, str(movement["documento_mascarado"])),
                ).fetchone()
                if exists is None:
                    self.conn.execute(
                        """
                        INSERT INTO contribuintes_identificadores (
                            organizacao_id, pessoa_id, contribuinte_id, tipo, valor, principal, ativo, observacoes
                        ) VALUES (?, ?, ?, ?, ?, 0, 1, 'Associado a partir da auditoria PIX.')
                        """,
                        (
                            organization_id,
                            selected_person_id,
                            resolved_contributor_id,
                            identifier_type,
                            str(movement["documento_mascarado"]),
                        ),
                    )
            self.conn.execute(
                """
                UPDATE pix_movimentos
                SET resolved_person_id = ?, resolved_contribuinte_id = ?, resolved_tipo_contribuicao_id = ?,
                    regra_id = ?, tipo_sugerido = ?,
                    review_status = 'aprovado', association_reviewed = ?, review_notes = ?, atualizado_em = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    selected_person_id or None,
                    resolved_contributor_id or None,
                    selected_type_id or None,
                    effective_rule_id,
                    type_suggestion,
                    1 if not selected_person_id else 0,
                    review_notes,
                    movement_id,
                ),
            )
        after = self.movement_snapshot(movement_id)
        self.write_audit_log(organization_id, "auditar_movimento_pix", "pix_movimentos", movement_id, before, after)
        if action != "ignore":
            propagated = 0
            if not selected_person_id:
                propagated = self.apply_pix_no_person_to_same_name_in_lot(
                    movement_id,
                    resolved_contributor_id,
                    review_notes=first_form_value(form, "review_notes"),
                )
            imported_contribution_id = self.import_single_pix_movement(movement_id)
            if propagated:
                updated_after = self.movement_snapshot(movement_id)
                current_note = merge_statement_review_notes(
                    updated_after.get("review_notes") if updated_after else first_form_value(form, "review_notes"),
                    f"Decisao NR replicada automaticamente para {propagated} ocorrencia(s) iguais no mesmo lote.",
                )
                self.conn.execute(
                    """
                    UPDATE pix_movimentos
                    SET review_notes = ?, atualizado_em = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (current_note, movement_id),
                )
                imported_contribution_id = self.sync_imported_contribution_with_pix_movement(movement_id, refresh_lot=False)
        else:
            self.refresh_pix_lot_status(moneyless_int(movement["lote_id"]))
        self.conn.commit()
        return imported_contribution_id

    def import_ready_pix_lot(self, lot_id: int) -> int:
        lot = self.get_pix_lot(lot_id)
        if lot is None:
            raise ValueError("Lote PIX nao encontrado.")
        if str(lot["status"]) == "encerrado":
            raise ValueError("Este lote ja foi encerrado. Use a fila de contribuintes pendentes de associacao para o trabalho futuro.")
        imported = self.ensure_pix_financial_entries(lot_id)
        self.conn.commit()
        return imported

    def close_pix_lot(self, lot_id: int) -> dict[str, int]:
        lot = self.get_pix_lot(lot_id)
        if lot is None:
            raise ValueError("Lote PIX nao encontrado.")
        if str(lot["status"]) == "encerrado":
            return {"importados": 0, "movidos_contribuintes": 0}
        duplicate_count = self.scalar(
            """
            SELECT COUNT(*)
            FROM pix_movimentos
            WHERE lote_id = ? AND ativo = 1 AND imported_contribution_id IS NULL AND review_status = 'revisar_duplicidade'
            """,
            (lot_id,),
        )
        if duplicate_count:
            raise ValueError(
                "Ainda existem casos em Revisar duplicidade. Resolva essas ocorrencias antes de encerrar o lote para nao correr risco de lancamento repetido."
            )
        organization_id = moneyless_int(lot["organizacao_id"])
        imported_now = self.ensure_pix_financial_entries(lot_id)
        rows = self.conn.execute(
            """
            SELECT *
            FROM pix_movimentos
            WHERE lote_id = ? AND ativo = 1 AND review_status IN ('revisar_pessoa', 'revisar_destinacao')
            ORDER BY data_recebimento, ordem_no_lote
            """,
            (lot_id,),
        ).fetchall()
        moved_to_contributors = 0
        for row in rows:
            before = self.movement_snapshot(moneyless_int(row["id"]))
            contributor_kind = contributor_kind_for_identity(
                row["nome_origem"],
                document_type=row["documento_tipo"],
                document_value=row["documento_mascarado"],
            )
            contributor_id = moneyless_int(row["resolved_contribuinte_id"]) or moneyless_int(row["suggested_contribuinte_id"])
            if not contributor_id:
                contributor_id = self.upsert_contributor(
                    organization_id,
                    str(row["nome_origem"]),
                    contributor_kind,
                    document_value=str(row["documento_mascarado"] or ""),
                    document_type=str(row["documento_tipo"] or "") or ("cnpj_mascarado" if contributor_kind == "pj" else "cpf_mascarado"),
                    source="pix",
                    quality="doador",
                )
            existing_notes = normalize_query(row["review_notes"])
            close_note = "Lote encerrado pelo operador. O valor foi preservado no contribuinte auxiliar e segue para associacao futura na aba de contribuintes."
            combined_notes = f"{existing_notes} | {close_note}" if existing_notes else close_note
            self.conn.execute(
                """
                UPDATE pix_movimentos
                SET resolved_person_id = NULL,
                    resolved_contribuinte_id = ?,
                    resolved_tipo_contribuicao_id = ?,
                    review_notes = ?,
                    atualizado_em = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    contributor_id or None,
                    self.resolved_pix_type_id_for_row(row) or None,
                    combined_notes,
                    row["id"],
                ),
            )
            imported_contribution_id = self.import_single_pix_movement(moneyless_int(row["id"]))
            after = self.movement_snapshot(moneyless_int(row["id"]))
            self.write_audit_log(
                organization_id,
                "encerrar_movimento_pix_para_contribuinte",
                "pix_movimentos",
                moneyless_int(row["id"]),
                before,
                after,
            )
            if imported_contribution_id:
                moved_to_contributors += 1
        before_lot = dict(lot)
        lot_close_note = merge_statement_review_notes(
            lot["observacoes"],
            "Lote encerrado. O que restou sem pessoa vinculada foi preservado para associacao futura na aba de contribuintes.",
        )
        self.conn.execute(
            """
            UPDATE pix_lotes
            SET status = 'encerrado',
                observacoes = ?,
                atualizado_em = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                lot_close_note,
                lot_id,
            ),
        )
        after_lot = self.get_pix_lot(lot_id)
        self.write_audit_log(
            organization_id,
            "encerrar_lote_pix",
            "pix_lotes",
            lot_id,
            before_lot,
            dict(after_lot) if after_lot else None,
        )
        self.conn.commit()
        return {"importados": imported_now, "movidos_contribuintes": moved_to_contributors}

    def statement_receiving_form_id(self, organization_id: int, code: str) -> int:
        row = self.conn.execute(
            """
            SELECT id
            FROM formas_recebimento
            WHERE organizacao_id = ? AND codigo = ? AND ativo = 1
            LIMIT 1
            """,
            (organization_id, normalize_query(code).upper()),
        ).fetchone()
        return moneyless_int(row["id"] if row else 0)

    def statement_lots(self, limit: int = 20) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT *
            FROM extrato_lotes
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    def get_statement_lot(self, lot_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM extrato_lotes WHERE id = ?", (lot_id,)).fetchone()

    def refresh_statement_lot_totals(self, lot_id: int) -> None:
        row = self.conn.execute(
            """
            SELECT
                COUNT(*) AS total_movimentos,
                COALESCE(SUM(valor), 0) AS total_valor
            FROM extrato_movimentos
            WHERE lote_id = ? AND ativo = 1
            """,
            (lot_id,),
        ).fetchone()
        self.conn.execute(
            """
            UPDATE extrato_lotes
            SET total_movimentos = ?, total_valor = ?, atualizado_em = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                moneyless_int(row["total_movimentos"] if row else 0),
                round(float(row["total_valor"] if row else 0.0), 2),
                lot_id,
            ),
        )

    def statement_lot_review_counts(self, lot_id: int) -> dict[str, int]:
        rows = self.conn.execute(
            """
            SELECT review_status, COUNT(*) AS quantidade
            FROM extrato_movimentos
            WHERE lote_id = ? AND ativo = 1
            GROUP BY review_status
            """,
            (lot_id,),
        ).fetchall()
        return {str(row["review_status"]): moneyless_int(row["quantidade"]) for row in rows}

    def statement_lot_financial_counts(self, lot_id: int) -> dict[str, int]:
        association_expr = statement_association_pending_expr("em", "ic", "ict")
        row = self.conn.execute(
            f"""
            SELECT
                COALESCE(SUM(CASE WHEN imported_contribution_id IS NOT NULL AND ic.id IS NOT NULL THEN 1 ELSE 0 END), 0) AS lancados,
                COALESCE(SUM(CASE WHEN imported_contribution_id IS NULL AND review_status <> 'ignorado' THEN 1 ELSE 0 END), 0) AS sem_financeiro,
                COALESCE(SUM(CASE WHEN imported_contribution_id IS NOT NULL AND ic.id IS NOT NULL AND review_status = 'revisar_destinacao' THEN 1 ELSE 0 END), 0) AS classificacao_pendente,
                COALESCE(SUM(CASE WHEN imported_contribution_id IS NOT NULL AND ic.id IS NOT NULL AND review_status = 'revisar_duplicidade' THEN 1 ELSE 0 END), 0) AS duplicidade_suspeita,
                COALESCE(
                    SUM(
                        CASE
                            WHEN imported_contribution_id IS NOT NULL AND ic.id IS NOT NULL
                             AND {association_expr}
                            THEN 1 ELSE 0
                        END
                    ),
                    0
                ) AS sem_associacao,
                COALESCE(
                    SUM(
                        CASE
                            WHEN imported_contribution_id IS NOT NULL AND ic.id IS NOT NULL
                             AND review_status NOT IN ('revisar_destinacao', 'revisar_duplicidade', 'ignorado')
                             AND NOT {association_expr}
                            THEN 1 ELSE 0
                        END
                    ),
                    0
                ) AS regulares
            FROM extrato_movimentos em
            LEFT JOIN contribuicoes ic ON ic.id = em.imported_contribution_id AND ic.ativo = 1
            LEFT JOIN contribuintes ict ON ict.id = ic.contribuinte_id
            WHERE em.lote_id = ? AND em.ativo = 1
            """,
            (lot_id,),
        ).fetchone()
        if row is None:
            return {
                "lancados": 0,
                "sem_financeiro": 0,
                "classificacao_pendente": 0,
                "duplicidade_suspeita": 0,
                "sem_associacao": 0,
                "regulares": 0,
            }
        return {
            "lancados": moneyless_int(row["lancados"]),
            "sem_financeiro": moneyless_int(row["sem_financeiro"]),
            "classificacao_pendente": moneyless_int(row["classificacao_pendente"]),
            "duplicidade_suspeita": moneyless_int(row["duplicidade_suspeita"]),
            "sem_associacao": moneyless_int(row["sem_associacao"]),
            "regulares": moneyless_int(row["regulares"]),
        }

    def refresh_statement_lot_status(self, lot_id: int) -> str:
        current = self.get_statement_lot(lot_id)
        self.refresh_statement_lot_totals(lot_id)
        counts = self.statement_lot_review_counts(lot_id)
        financial = self.statement_lot_financial_counts(lot_id)
        imported = financial.get("lancados", 0)
        ignored = counts.get("ignorado", 0)
        total = self.scalar("SELECT COUNT(*) FROM extrato_movimentos WHERE lote_id = ? AND ativo = 1", (lot_id,))
        pending = (
            moneyless_int(counts.get("revisar_pessoa"))
            + moneyless_int(counts.get("revisar_destinacao"))
            + moneyless_int(counts.get("revisar_duplicidade"))
            + moneyless_int(financial.get("sem_associacao"))
        )
        if current is not None and str(current["status"]) == "encerrado" and total and imported + ignored >= total:
            status = "encerrado"
        elif total and imported + ignored >= total and pending == 0:
            status = "concluido"
        elif financial.get("sem_financeiro", 0):
            status = "pronto_importacao" if not pending else "auditando"
        elif pending:
            status = "parcial"
        else:
            status = "concluido"
        self.conn.execute(
            "UPDATE extrato_lotes SET status = ?, atualizado_em = CURRENT_TIMESTAMP WHERE id = ?",
            (status, lot_id),
        )
        return status

    def statement_lot_movements(self, lot_id: int, status_filter: str = "", limit: int = 500) -> list[sqlite3.Row]:
        clauses = ["m.lote_id = ?", "m.ativo = 1"]
        params: list[object] = [lot_id]
        status_filter = normalize_query(status_filter)
        association_expr = statement_association_pending_expr("m", "ic", "ict")
        if status_filter == "pendencias":
            clauses.append(f"(m.review_status IN ('revisar_pessoa', 'revisar_destinacao', 'revisar_duplicidade') OR {association_expr})")
        elif status_filter == "associacao":
            clauses.append(association_expr)
        elif status_filter == "destinacoes_especiais":
            clauses.append("m.regra_id IS NOT NULL")
        elif status_filter and status_filter != "todos":
            clauses.append("m.review_status = ?")
            params.append(status_filter)
        order_clause = "m.data_movimento DESC, m.ordem_no_lote"
        if status_filter in {"pendencias", "revisar_pessoa"}:
            order_clause = f"""
                CASE
                    WHEN {association_expr} THEN 0
                    WHEN m.review_status = 'revisar_pessoa' AND m.confidence IN ('forte_nome', 'forte_doc', 'forte_doc_nome') THEN 1
                    WHEN m.review_status = 'revisar_pessoa' AND m.confidence = 'provavel_nome' THEN 2
                    WHEN m.review_status = 'revisar_destinacao' THEN 3
                    WHEN m.review_status = 'revisar_duplicidade' THEN 4
                    WHEN m.review_status = 'revisar_pessoa' THEN 5
                    ELSE 6
                END,
                CASE WHEN COALESCE(m.resolved_person_id, m.suggested_person_id) IS NOT NULL THEN 0 ELSE 1 END,
                m.match_score DESC,
                m.data_movimento DESC,
                m.ordem_no_lote
            """
        elif status_filter == "associacao":
            order_clause = """
                m.data_movimento DESC,
                m.ordem_no_lote
            """
        params.append(limit)
        return self.conn.execute(
            f"""
            SELECT
                m.*,
                sp.nome AS suggested_person_name,
                sp.cpf AS suggested_person_cpf,
                rp.nome AS resolved_person_name,
                rp.cpf AS resolved_person_cpf,
                sc.nome AS suggested_contributor_name,
                rc.nome AS resolved_contributor_name,
                ic.pessoa_id AS imported_person_id,
                ict.nome AS imported_contributor_name,
                ict.pessoa_id AS imported_contributor_person_id,
                CASE WHEN {association_expr} THEN 1 ELSE 0 END AS association_pending
                ,
                tc.nome AS resolved_tipo_nome,
                r.nome_destinacao AS regra_nome
            FROM extrato_movimentos m
            LEFT JOIN pessoas sp ON sp.id = m.suggested_person_id
            LEFT JOIN pessoas rp ON rp.id = m.resolved_person_id
            LEFT JOIN contribuintes sc ON sc.id = m.suggested_contribuinte_id
            LEFT JOIN contribuintes rc ON rc.id = m.resolved_contribuinte_id
            LEFT JOIN contribuicoes ic ON ic.id = m.imported_contribution_id AND ic.ativo = 1
            LEFT JOIN contribuintes ict ON ict.id = ic.contribuinte_id
            LEFT JOIN tipos_contribuicao tc ON tc.id = m.resolved_tipo_contribuicao_id
            LEFT JOIN pix_centavo_regras r ON r.id = m.regra_id
            WHERE {' AND '.join(clauses)}
            ORDER BY {order_clause}
            LIMIT ?
            """,
            params,
        ).fetchall()

    def get_statement_movement(self, movement_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            """
            SELECT
                m.*,
                l.nome_arquivo,
                l.banco,
                l.layout_codigo,
                r.nome_destinacao AS regra_nome,
                tc.nome AS resolved_tipo_nome,
                sp.nome AS suggested_person_name,
                sp.cpf AS suggested_person_cpf,
                rp.nome AS resolved_person_name,
                rp.cpf AS resolved_person_cpf
            FROM extrato_movimentos m
            JOIN extrato_lotes l ON l.id = m.lote_id
            LEFT JOIN pix_centavo_regras r ON r.id = m.regra_id
            LEFT JOIN tipos_contribuicao tc ON tc.id = m.resolved_tipo_contribuicao_id
            LEFT JOIN pessoas sp ON sp.id = m.suggested_person_id
            LEFT JOIN pessoas rp ON rp.id = m.resolved_person_id
            WHERE m.id = ?
            """,
            (movement_id,),
        ).fetchone()

    def statement_movement_snapshot(self, movement_id: int) -> dict[str, object]:
        row = self.get_statement_movement(movement_id)
        return dict(row) if row else {}

    def classify_statement_review_status(self, confidence: str, source_name: str, special_rule: bool = False) -> str:
        if special_rule:
            return "revisar_destinacao"
        if confidence in {"forte_doc_nome", "forte_doc", "forte_nome", "pj_ou_externo"}:
            return "pronto"
        if not normalize_query(source_name):
            return "revisar_pessoa"
        return "revisar_pessoa"

    def find_statement_duplicate_targets(
        self,
        organization_id: int,
        signature_global: str,
        occurrence_index: int = 1,
        ignore_movement_id: int = 0,
        ignore_lot_id: int = 0,
    ) -> dict[str, object]:
        duplicate_movements = self.conn.execute(
            """
            SELECT *
            FROM extrato_movimentos
            WHERE organizacao_id = ? AND signature_global = ? AND ativo = 1
              AND (? = 0 OR id <> ?)
              AND (? = 0 OR lote_id <> ?)
            ORDER BY CASE WHEN imported_contribution_id IS NOT NULL THEN 0 ELSE 1 END, id
            """,
            (organization_id, signature_global, ignore_movement_id, ignore_movement_id, ignore_lot_id, ignore_lot_id),
        ).fetchall()
        duplicate_movement = None
        occurrence_index = max(1, moneyless_int(occurrence_index))
        if occurrence_index <= len(duplicate_movements):
            duplicate_movement = duplicate_movements[occurrence_index - 1]
        if duplicate_movement is not None:
            lot = self.get_statement_lot(moneyless_int(duplicate_movement["lote_id"]))
            lot_label = f"lote #{duplicate_movement['lote_id']}"
            if lot is not None and lot["nome_arquivo"]:
                lot_label += f" ({lot['nome_arquivo']})"
            return {
                "duplicate_movement_id": moneyless_int(duplicate_movement["id"]),
                "duplicate_contribution_id": moneyless_int(duplicate_movement["imported_contribution_id"]),
                "duplicate_reason": (
                    f"Ja existe ocorrencia equivalente em outro documento bancario. "
                    f"Esta entrada coincide com {lot_label}."
                ),
                "review_status": "revisar_duplicidade",
            }
        return {
            "duplicate_movement_id": 0,
            "duplicate_contribution_id": 0,
            "duplicate_reason": "",
            "review_status": "",
        }

    def find_statement_operational_duplicate_targets(
        self,
        organization_id: int,
        layout_code: str,
        received_on: object,
        amount: object,
        source_name: object,
        document_value: object,
        occurrence_index: int = 1,
        ignore_movement_id: int = 0,
        ignore_lot_id: int = 0,
    ) -> dict[str, object]:
        target_key = core_bank_lots.statement_operational_duplicate_key(
            layout_code,
            core_banking.statement_layout_label(layout_code),
            received_on,
            amount,
            source_name,
            document_value,
        )
        if not target_key[-1]:
            return {
                "duplicate_movement_id": 0,
                "duplicate_contribution_id": 0,
                "duplicate_reason": "",
                "review_status": "",
            }
        amount_cents = int(round(float(amount or 0) * 100))
        candidate_rows = self.conn.execute(
            """
            SELECT m.id, m.lote_id, m.data_movimento, m.valor, m.nome_origem, m.nome_normalizado,
                   m.bank_document, m.imported_contribution_id, m.review_status,
                   l.banco, l.layout_codigo, l.nome_arquivo
              FROM extrato_movimentos m
              JOIN extrato_lotes l ON l.id = m.lote_id
             WHERE m.organizacao_id = ?
               AND m.ativo = 1
               AND COALESCE(m.review_status, '') <> 'ignorado'
               AND m.data_movimento = ?
               AND ROUND(m.valor * 100) = ?
               AND (? = 0 OR m.id <> ?)
               AND (? = 0 OR m.lote_id <> ?)
             ORDER BY CASE WHEN m.imported_contribution_id IS NOT NULL THEN 0 ELSE 1 END, m.id
            """,
            (
                organization_id,
                normalize_query(received_on),
                amount_cents,
                ignore_movement_id,
                ignore_movement_id,
                ignore_lot_id,
                ignore_lot_id,
            ),
        ).fetchall()
        matches: list[dict[str, object]] = []
        for row in candidate_rows:
            candidate_key = core_bank_lots.statement_operational_duplicate_key(
                row["layout_codigo"],
                row["banco"],
                row["data_movimento"],
                row["valor"],
                row["nome_origem"] or row["nome_normalizado"],
                row["bank_document"],
            )
            if candidate_key == target_key:
                matches.append(
                    {
                        "kind": "extrato",
                        "movement_id": moneyless_int(row["id"]),
                        "lot_id": moneyless_int(row["lote_id"]),
                        "contribution_id": moneyless_int(row["imported_contribution_id"]),
                        "filename": row["nome_arquivo"] or "",
                    }
                )
        if core_bank_lots.statement_operational_bank_family(layout_code) == "sicoob":
            pix_rows = self.conn.execute(
                """
                SELECT pm.id, pm.lote_id, pm.data_recebimento, pm.valor, pm.nome_origem, pm.nome_normalizado,
                       pm.documento_mascarado, pm.imported_contribution_id, pl.nome_arquivo
                  FROM pix_movimentos pm
                  JOIN pix_lotes pl ON pl.id = pm.lote_id
                 WHERE pm.organizacao_id = ?
                   AND pm.ativo = 1
                   AND COALESCE(pm.review_status, '') <> 'ignorado'
                   AND pm.data_recebimento = ?
                   AND ROUND(pm.valor * 100) = ?
                 ORDER BY CASE WHEN pm.imported_contribution_id IS NOT NULL THEN 0 ELSE 1 END, pm.id
                """,
                (organization_id, normalize_query(received_on), amount_cents),
            ).fetchall()
            for row in pix_rows:
                candidate_key = core_bank_lots.statement_operational_duplicate_key(
                    "SICOOB_RECEBIMENTOS",
                    "Sicoob",
                    row["data_recebimento"],
                    row["valor"],
                    row["nome_origem"] or row["nome_normalizado"],
                    row["documento_mascarado"],
                )
                if candidate_key == target_key:
                    matches.append(
                        {
                            "kind": "pix",
                            "movement_id": moneyless_int(row["id"]),
                            "lot_id": moneyless_int(row["lote_id"]),
                            "contribution_id": moneyless_int(row["imported_contribution_id"]),
                            "filename": row["nome_arquivo"] or "",
                        }
                    )
        occurrence_index = max(1, moneyless_int(occurrence_index))
        if occurrence_index > len(matches):
            return {
                "duplicate_movement_id": 0,
                "duplicate_contribution_id": 0,
                "duplicate_reason": "",
                "review_status": "",
            }
        duplicate = matches[occurrence_index - 1]
        movement_label = "movimento de extrato" if duplicate["kind"] == "extrato" else "movimento PIX"
        lot_label = f"lote #{duplicate['lot_id']}"
        if duplicate.get("filename"):
            lot_label += f" ({duplicate['filename']})"
        return {
            "duplicate_movement_id": moneyless_int(duplicate["movement_id"]) if duplicate["kind"] == "extrato" else 0,
            "duplicate_contribution_id": moneyless_int(duplicate["contribution_id"]),
            "duplicate_reason": (
                "Ja existe ocorrencia operacional equivalente por banco, data, valor e identidade. "
                f"Esta entrada coincide com {movement_label} #{duplicate['movement_id']} no {lot_label}."
            ),
            "review_status": "revisar_duplicidade",
        }

    def statement_lot_occurrence_index(self, lot_id: int, signature_global: str, order_in_lot: int, movement_id: int = 0) -> int:
        row = self.conn.execute(
            """
            SELECT COUNT(*) AS quantidade
            FROM extrato_movimentos
            WHERE lote_id = ? AND ativo = 1 AND review_status <> 'ignorado'
              AND signature_global = ?
              AND (
                    ordem_no_lote < ?
                    OR (ordem_no_lote = ? AND (? = 0 OR id <= ?))
              )
            """,
            (lot_id, signature_global, order_in_lot, order_in_lot, movement_id, movement_id),
        ).fetchone()
        return moneyless_int(row["quantidade"] if row else 0)

    def statement_notes_for_row(self, row: sqlite3.Row) -> str:
        lot = self.get_statement_lot(moneyless_int(row["lote_id"]))
        bank_name = normalize_query(lot["banco"]) if lot is not None else "Extrato bancario"
        parts = [
            f"Importado do lote de extrato #{row['lote_id']}",
            f"Banco: {bank_name}",
            f"Tipo: {normalize_query(row['movement_kind'])}",
        ]
        if normalize_query(row["nome_origem"]):
            parts.append(f"Origem: {row['nome_origem']}")
        elif normalize_query(row["origin_label"]):
            parts.append(f"Origem: {row['origin_label']}")
        if normalize_query(row["bank_document"]):
            parts.append(f"Docto: {row['bank_document']}")
        if normalize_query(row["codigo_centavos"]):
            parts.append(f"Centavos: {row['codigo_centavos']}")
        if normalize_query(row["review_notes"]):
            parts.append(str(row["review_notes"]))
        return " | ".join(parts)

    def statement_contribution_person_id_for_row(self, row: sqlite3.Row) -> int | None:
        resolved_person_id = moneyless_int(row["resolved_person_id"])
        suggested_person_id = moneyless_int(row["suggested_person_id"])
        review_status = str(row["review_status"] or "")
        if resolved_person_id:
            return resolved_person_id
        if moneyless_int(row["association_reviewed"]):
            return None
        if review_status in {"pronto", "aprovado", "importado", "revisar_destinacao"}:
            return suggested_person_id or None
        return None

    def statement_contribution_contributor_id_for_row(self, row: sqlite3.Row) -> int | None:
        return moneyless_int(row["resolved_contribuinte_id"]) or moneyless_int(row["suggested_contribuinte_id"]) or None

    def statement_contribution_status_for_row(self, row: sqlite3.Row, person_id: int = 0) -> str:
        review_status = str(row["review_status"] or "")
        if review_status == "ignorado":
            return "ignorado"
        if review_status == "revisar_duplicidade":
            return "duplicidade_suspeita"
        if not person_id:
            return "sem_associacao"
        if review_status == "revisar_destinacao":
            return "classificacao_pendente"
        return "regular"

    def resolved_statement_type_id_for_row(self, row: sqlite3.Row) -> int:
        organization_id = moneyless_int(row["organizacao_id"])
        type_id = moneyless_int(row["resolved_tipo_contribuicao_id"])
        if not type_id and moneyless_int(row["regra_id"]):
            rule = self.conn.execute("SELECT tipo_contribuicao_id FROM pix_centavo_regras WHERE id = ?", (row["regra_id"],)).fetchone()
            type_id = moneyless_int(rule["tipo_contribuicao_id"] if rule else 0)
        if not type_id:
            type_id = self.pix_default_type_id(organization_id)
        return type_id

    def ensure_statement_internal_origin_contributor(self, row: sqlite3.Row) -> int:
        organization_id = moneyless_int(row["organizacao_id"])
        source_name = normalize_query(row["nome_origem"]) or normalize_query(row["origin_label"]) or "Origem interna"
        lot = self.get_statement_lot(moneyless_int(row["lote_id"]))
        bank_name = normalize_query(lot["banco"]) if lot is not None else "extrato bancario"
        contributor_id = moneyless_int(row["resolved_contribuinte_id"]) or moneyless_int(row["suggested_contribuinte_id"])
        if not contributor_id and source_name:
            contributor_id = self.upsert_contributor(
                organization_id,
                source_name,
                contributor_kind_for_identity(source_name),
                source="origem_interna",
                quality="mesma_titularidade",
            )
        if contributor_id:
            contributor = self.get_contributor(contributor_id)
            if contributor is not None:
                note = merge_statement_review_notes(
                    contributor["observacoes"],
                    f"Classificado como mesma_titularidade / origem_interna a partir da auditoria do extrato {bank_name}.",
                )
                self.conn.execute(
                    """
                    UPDATE contribuintes
                    SET origem = 'origem_interna',
                        qualidade = 'mesma_titularidade',
                        observacoes = ?,
                        atualizado_em = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (note or None, contributor_id),
                )
        return contributor_id

    def statement_receiving_form_id_for_row(self, row: sqlite3.Row) -> int:
        organization_id = moneyless_int(row["organizacao_id"])
        code = normalize_query(row["receiving_code"]).upper() or "TRANSFERENCIA"
        return self.statement_receiving_form_id(organization_id, code)

    def sync_imported_contribution_with_statement_movement(self, movement_id: int, refresh_lot: bool = True) -> int:
        row = self.conn.execute(
            "SELECT * FROM extrato_movimentos WHERE id = ? AND ativo = 1",
            (movement_id,),
        ).fetchone()
        if row is None:
            raise ValueError("Movimento de extrato nao encontrado.")
        contribution_id = moneyless_int(row["imported_contribution_id"])
        if not contribution_id:
            return 0
        contribution = self.get_contribution(contribution_id)
        if contribution is None:
            return 0
        before = dict(contribution)
        person_id = self.statement_contribution_person_id_for_row(row)
        contributor_id = self.statement_contribution_contributor_id_for_row(row)
        receiving_form_id = self.statement_receiving_form_id_for_row(row) or None
        status_operacional = self.statement_contribution_status_for_row(row, moneyless_int(person_id))
        type_id = self.resolved_statement_type_id_for_row(row)
        campaign_id = self.cent_rule_campaign_id_for_type(moneyless_int(row["regra_id"]), type_id)
        self.conn.execute(
            """
            UPDATE contribuicoes
            SET pessoa_id = ?, contribuinte_id = ?, tipo_contribuicao_id = ?, data_recebimento = ?,
                competencia = ?, competencia_ordem = ?, valor = ?, forma_recebimento_id = ?,
                campanha_id = ?, observacoes = ?, status_operacional = ?, atualizado_em = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                person_id,
                contributor_id,
                type_id,
                str(row["data_movimento"]),
                str(row["competencia"]),
                moneyless_int(row["competencia_ordem"]),
                float(row["valor"]),
                receiving_form_id,
                campaign_id,
                self.statement_notes_for_row(row),
                status_operacional,
                contribution_id,
            ),
        )
        after = self.get_contribution(contribution_id)
        self.write_audit_log(
            moneyless_int(row["organizacao_id"]),
            "atualizar_contribuicao_extrato_importada",
            "contribuicoes",
            contribution_id,
            before,
            dict(after) if after else None,
        )
        if refresh_lot:
            self.refresh_statement_lot_status(moneyless_int(row["lote_id"]))
        return contribution_id

    def import_single_statement_movement(self, movement_id: int, refresh_lot: bool = True) -> int:
        row = self.conn.execute(
            "SELECT * FROM extrato_movimentos WHERE id = ? AND ativo = 1",
            (movement_id,),
        ).fetchone()
        if row is None:
            raise ValueError("Movimento de extrato nao encontrado.")
        if moneyless_int(row["imported_contribution_id"]):
            return self.sync_imported_contribution_with_statement_movement(movement_id, refresh_lot=refresh_lot)
        organization_id = moneyless_int(row["organizacao_id"])
        person_id = self.statement_contribution_person_id_for_row(row)
        contributor_id = self.statement_contribution_contributor_id_for_row(row)
        type_id = self.resolved_statement_type_id_for_row(row)
        receiving_form_id = self.statement_receiving_form_id_for_row(row) or None
        status_operacional = self.statement_contribution_status_for_row(row, moneyless_int(person_id))
        campaign_id = self.cent_rule_campaign_id_for_type(moneyless_int(row["regra_id"]), type_id)
        cursor = self.conn.execute(
            """
            INSERT INTO contribuicoes (
                organizacao_id, unidade_id, pessoa_id, contribuinte_id, tipo_contribuicao_id,
                campanha_id, data_recebimento, competencia, competencia_ordem,
                valor, forma_recebimento_id, conta_financeira_id, observacoes,
                import_lote_id, pix_movimento_id, extrato_movimento_id, status_operacional, ativo, atualizado_em
            ) VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL, NULL, ?, ?, 1, CURRENT_TIMESTAMP)
            """,
            (
                organization_id,
                person_id,
                contributor_id,
                type_id,
                campaign_id,
                str(row["data_movimento"]),
                str(row["competencia"]),
                moneyless_int(row["competencia_ordem"]),
                float(row["valor"]),
                receiving_form_id,
                self.statement_notes_for_row(row),
                movement_id,
                status_operacional,
            ),
        )
        contribution_id = moneyless_int(cursor.lastrowid)
        saved = self.get_contribution(contribution_id)
        self.write_audit_log(
            organization_id,
            "importar_movimento_extrato",
            "contribuicoes",
            contribution_id,
            None,
            dict(saved) if saved else {"id": contribution_id},
        )
        self.conn.execute(
            """
            UPDATE extrato_movimentos
            SET imported_contribution_id = ?, atualizado_em = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (contribution_id, movement_id),
        )
        if refresh_lot:
            self.refresh_statement_lot_status(moneyless_int(row["lote_id"]))
        return contribution_id

    def ensure_statement_financial_entries(self, lot_id: int = 0) -> int:
        clauses = ["ativo = 1", "review_status <> 'ignorado'"]
        params: list[object] = []
        if lot_id:
            clauses.append("lote_id = ?")
            params.append(lot_id)
        rows = self.conn.execute(
            f"""
            SELECT id, lote_id, imported_contribution_id
            FROM extrato_movimentos
            WHERE {' AND '.join(clauses)}
            ORDER BY lote_id, ordem_no_lote, id
            """,
            params,
        ).fetchall()
        created = 0
        affected_lots: set[int] = set()
        for row in rows:
            movement_id = moneyless_int(row["id"])
            affected_lots.add(moneyless_int(row["lote_id"]))
            if moneyless_int(row["imported_contribution_id"]):
                self.sync_imported_contribution_with_statement_movement(movement_id, refresh_lot=False)
                continue
            self.import_single_statement_movement(movement_id, refresh_lot=False)
            created += 1
        for affected_lot in sorted(item for item in affected_lots if item):
            self.refresh_statement_lot_status(affected_lot)
        return created

    def backfill_statement_signatures(self) -> None:
        rows = self.conn.execute(
            """
            SELECT id, data_movimento, valor, nome_normalizado, movement_kind, bank_document, raw_text
            FROM extrato_movimentos
            WHERE signature_global IS NULL OR TRIM(COALESCE(signature_global, '')) = ''
            """
        ).fetchall()
        for row in rows:
            signature = statement_global_signature(
                "BRADESCO_EXTRATO",
                row["data_movimento"],
                row["valor"],
                row["nome_normalizado"],
                row["movement_kind"],
                row["bank_document"],
                row["raw_text"],
            )
            self.conn.execute(
                "UPDATE extrato_movimentos SET signature_global = ? WHERE id = ?",
                (signature, row["id"]),
            )

    def backfill_statement_cent_rules(self) -> int:
        org_rows = self.conn.execute("SELECT id FROM organizacoes ORDER BY id").fetchall()
        org_ids = [moneyless_int(row["id"]) for row in org_rows] or [1]
        updated = 0
        for org_id in org_ids:
            rules_by_code = {
                str(row["codigo_centavos"]): row
                for row in self.pix_rules(org_id)
                if moneyless_int(row["ativo"])
            }
            default_type_id = self.pix_default_type_id(org_id)
            rows = self.conn.execute(
                """
                SELECT id, valor, codigo_centavos, regra_id, tipo_sugerido, review_status, resolved_tipo_contribuicao_id,
                       confidence, nome_origem
                FROM extrato_movimentos
                WHERE organizacao_id = ?
                """,
                (org_id,),
            ).fetchall()
            for row in rows:
                code = pix_code_from_amount(float(row["valor"]))
                rule_row = rules_by_code.get(code)
                current_code = normalize_query(row["codigo_centavos"])
                current_rule_id = moneyless_int(row["regra_id"])
                current_tipo = normalize_query(row["tipo_sugerido"])
                current_status = normalize_query(row["review_status"])
                current_resolved_type_id = moneyless_int(row["resolved_tipo_contribuicao_id"])
                rule_type_id = moneyless_int(rule_row["tipo_contribuicao_id"]) if rule_row else 0
                if rule_row and current_resolved_type_id and rule_type_id and current_resolved_type_id != rule_type_id:
                    desired_rule_id = 0
                    tipo_sugerido = "dizimo" if current_resolved_type_id == default_type_id else "manual"
                else:
                    desired_rule_id = moneyless_int(rule_row["id"]) if rule_row else 0
                    tipo_sugerido = core_designations.suggested_type_for_cent_rule(rule_row)
                desired_status = current_status
                if desired_rule_id and not current_resolved_type_id and current_status not in {"aprovado", "ignorado", "revisar_duplicidade"}:
                    desired_status = "revisar_destinacao"
                elif not desired_rule_id and current_status == "revisar_destinacao" and not current_resolved_type_id:
                    desired_status = self.classify_statement_review_status(
                        normalize_query(row["confidence"]),
                        normalize_query(row["nome_origem"]),
                        special_rule=False,
                    )
                if (
                    current_code == code
                    and current_rule_id == desired_rule_id
                    and current_tipo == tipo_sugerido
                    and current_status == desired_status
                ):
                    continue
                self.conn.execute(
                    """
                    UPDATE extrato_movimentos
                    SET codigo_centavos = ?, regra_id = ?, tipo_sugerido = ?, review_status = ?, atualizado_em = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        code,
                        desired_rule_id or None,
                        tipo_sugerido,
                        desired_status,
                        moneyless_int(row["id"]),
                    ),
                )
                updated += 1
        return updated

    def exclude_statement_rows_out_of_scope(self, lot_id: int = 0) -> int:
        clauses = ["ativo = 1"]
        params: list[object] = []
        if lot_id:
            clauses.append("lote_id = ?")
            params.append(lot_id)
        rows = self.conn.execute(
            f"""
            SELECT *
            FROM extrato_movimentos
            WHERE {' AND '.join(clauses)}
            ORDER BY lote_id, ordem_no_lote, id
            """,
            params,
        ).fetchall()
        excluded = 0
        affected_lots: set[int] = set()
        for row in rows:
            if not statement_row_should_be_excluded(row):
                continue
            organization_id = moneyless_int(row["organizacao_id"])
            movement_id = moneyless_int(row["id"])
            contribution_id = moneyless_int(row["imported_contribution_id"])
            before_movement = dict(row)
            if contribution_id:
                contribution_before = self.get_contribution(contribution_id)
                if contribution_before is not None and moneyless_int(contribution_before["ativo"]):
                    current_notes = normalize_query(contribution_before["observacoes"])
                    exclusion_note = "Lancamento retirado do escopo desta etapa: deposito bancario sem nome para conciliacao posterior."
                    merged_notes = exclusion_note if not current_notes else f"{current_notes}\n{exclusion_note}"
                    self.conn.execute(
                        """
                        UPDATE contribuicoes
                        SET ativo = 0, status_operacional = 'ignorado', observacoes = ?, atualizado_em = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (merged_notes, contribution_id),
                    )
                    contribution_after = self.get_contribution(contribution_id)
                    self.write_audit_log(
                        organization_id,
                        "desativar_contribuicao_extrato_fora_escopo",
                        "contribuicoes",
                        contribution_id,
                        dict(contribution_before),
                        dict(contribution_after) if contribution_after else None,
                    )
            self.conn.execute(
                """
                UPDATE extrato_movimentos
                SET ativo = 0,
                    review_status = 'ignorado',
                    review_notes = ?,
                    atualizado_em = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    "Movimento retirado do escopo atual: deposito bancario sem nome sera tratado em conciliacao futura.",
                    movement_id,
                ),
            )
            after_movement = self.conn.execute("SELECT * FROM extrato_movimentos WHERE id = ?", (movement_id,)).fetchone()
            self.write_audit_log(
                organization_id,
                "desativar_movimento_extrato_fora_escopo",
                "extrato_movimentos",
                movement_id,
                before_movement,
                dict(after_movement) if after_movement else None,
            )
            affected_lots.add(moneyless_int(row["lote_id"]))
            excluded += 1
        for affected_lot in sorted(item for item in affected_lots if item):
            self.refresh_statement_lot_status(affected_lot)
        return excluded

    def apply_statement_person_to_same_name_in_lot(
        self,
        movement_id: int,
        selected_person_id: int,
        resolved_contributor_id: int,
        review_notes: str = "",
    ) -> int:
        if not selected_person_id:
            return 0
        anchor = self.get_statement_movement(movement_id)
        if anchor is None:
            return 0
        lot_id = moneyless_int(anchor["lote_id"])
        layout_code = normalize_query(anchor["layout_codigo"]).upper()
        contributor_source = statement_layout_contributor_source(anchor["layout_codigo"])
        normalized_name = normalize_query(anchor["nome_normalizado"])
        document_key = cleaned_document_token(anchor["bank_document"])
        if not lot_id or (not normalized_name and not document_key):
            return 0
        if normalized_name:
            rows = self.conn.execute(
                """
                SELECT *
                FROM extrato_movimentos
                WHERE lote_id = ? AND ativo = 1 AND id <> ? AND COALESCE(nome_normalizado, '') = ?
                  AND review_status <> 'ignorado'
                  AND (resolved_person_id IS NULL OR resolved_person_id = 0 OR resolved_person_id = ?)
                ORDER BY ordem_no_lote, id
                """,
                (lot_id, movement_id, normalized_name, selected_person_id),
            ).fetchall()
            identity_label = normalize_query(anchor["nome_origem"]) or normalized_name
        else:
            rows = self.conn.execute(
                """
                SELECT *
                FROM extrato_movimentos
                WHERE lote_id = ? AND ativo = 1 AND id <> ? AND COALESCE(bank_document, '') = ?
                  AND review_status <> 'ignorado'
                  AND (resolved_person_id IS NULL OR resolved_person_id = 0 OR resolved_person_id = ?)
                ORDER BY ordem_no_lote, id
                """,
                (lot_id, movement_id, document_key, selected_person_id),
            ).fetchall()
            identity_label = statement_document_identity_label(anchor["layout_codigo"], document_key, santander_document_type(document_key))
        applied = 0
        batch_note = (
            f"Associacao em lote aplicada a partir do movimento #{movement_id} para a identidade bancaria "
            f"'{identity_label}'."
        )
        combined_note = merge_statement_review_notes(review_notes, batch_note)
        for row in rows:
            before = dict(row)
            contributor_id = resolved_contributor_id or moneyless_int(row["resolved_contribuinte_id"]) or moneyless_int(row["suggested_contribuinte_id"])
            row_document = normalize_query(row["bank_document"])
            row_document_type = santander_document_type(row_document)
            contributor_name = statement_contributor_name_for_identity(
                layout_code,
                row["nome_origem"],
                row_document,
                row_document_type,
            )
            if not contributor_id and contributor_name:
                contributor_id = self.upsert_contributor(
                    moneyless_int(row["organizacao_id"]),
                    contributor_name,
                    contributor_kind_for_identity(
                        contributor_name,
                        document_type=row_document_type,
                        document_value=row_document,
                    ),
                    document_value=row_document,
                    document_type=row_document_type if row_document else "",
                    person_id=selected_person_id,
                    source=contributor_source,
                    quality="doador",
                )
            row_notes = merge_statement_review_notes(row["review_notes"], combined_note)
            self.conn.execute(
                """
                UPDATE extrato_movimentos
                SET resolved_person_id = ?, resolved_contribuinte_id = ?,
                    review_status = 'aprovado', association_reviewed = 0, review_notes = ?, atualizado_em = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    selected_person_id or None,
                    contributor_id or None,
                    row_notes,
                    row["id"],
                ),
            )
            after = self.statement_movement_snapshot(moneyless_int(row["id"]))
            self.write_audit_log(
                moneyless_int(row["organizacao_id"]),
                "associar_movimento_extrato_em_lote",
                "extrato_movimentos",
                moneyless_int(row["id"]),
                before,
                after,
            )
            if moneyless_int(row["imported_contribution_id"]):
                self.sync_imported_contribution_with_statement_movement(moneyless_int(row["id"]), refresh_lot=False)
            else:
                self.import_single_statement_movement(moneyless_int(row["id"]), refresh_lot=False)
            applied += 1
        return applied

    def apply_statement_no_person_to_same_name_in_lot(
        self,
        movement_id: int,
        resolved_contributor_id: int,
        review_notes: str = "",
    ) -> int:
        anchor = self.get_statement_movement(movement_id)
        if anchor is None:
            return 0
        lot_id = moneyless_int(anchor["lote_id"])
        layout_code = normalize_query(anchor["layout_codigo"]).upper()
        contributor_source = statement_layout_contributor_source(anchor["layout_codigo"])
        normalized_name = normalize_query(anchor["nome_normalizado"])
        document_key = cleaned_document_token(anchor["bank_document"])
        if not lot_id or (not normalized_name and not document_key):
            return 0
        if normalized_name:
            rows = self.conn.execute(
                """
                SELECT *
                FROM extrato_movimentos
                WHERE lote_id = ? AND ativo = 1 AND id <> ? AND COALESCE(nome_normalizado, '') = ?
                  AND review_status <> 'ignorado'
                  AND COALESCE(association_reviewed, 0) = 0
                  AND COALESCE(resolved_person_id, 0) = 0
                ORDER BY ordem_no_lote, id
                """,
                (lot_id, movement_id, normalized_name),
            ).fetchall()
            identity_label = normalize_query(anchor["nome_origem"]) or normalized_name
        else:
            rows = self.conn.execute(
                """
                SELECT *
                FROM extrato_movimentos
                WHERE lote_id = ? AND ativo = 1 AND id <> ? AND COALESCE(bank_document, '') = ?
                  AND review_status <> 'ignorado'
                  AND COALESCE(association_reviewed, 0) = 0
                  AND COALESCE(resolved_person_id, 0) = 0
                ORDER BY ordem_no_lote, id
                """,
                (lot_id, movement_id, document_key),
            ).fetchall()
            identity_label = statement_document_identity_label(anchor["layout_codigo"], document_key, santander_document_type(document_key))
        applied = 0
        batch_note = (
            f"Decisao em lote aplicada a partir do movimento #{movement_id}: manter como NR / contribuinte auxiliar "
            f"para a identidade bancaria '{identity_label}'."
        )
        combined_note = merge_statement_review_notes(review_notes, batch_note)
        for row in rows:
            before = dict(row)
            contributor_id = resolved_contributor_id or moneyless_int(row["resolved_contribuinte_id"]) or moneyless_int(row["suggested_contribuinte_id"])
            row_document = normalize_query(row["bank_document"])
            row_document_type = santander_document_type(row_document)
            contributor_name = statement_contributor_name_for_identity(
                layout_code,
                row["nome_origem"],
                row_document,
                row_document_type,
            )
            if not contributor_id and contributor_name:
                contributor_id = self.upsert_contributor(
                    moneyless_int(row["organizacao_id"]),
                    contributor_name,
                    contributor_kind_for_identity(
                        contributor_name,
                        document_type=row_document_type,
                        document_value=row_document,
                    ),
                    document_value=row_document,
                    document_type=row_document_type if row_document else "",
                    source=contributor_source,
                    quality="doador",
                )
            row_notes = merge_statement_review_notes(row["review_notes"], combined_note)
            self.conn.execute(
                """
                UPDATE extrato_movimentos
                SET resolved_person_id = NULL,
                    resolved_contribuinte_id = ?,
                    review_status = 'aprovado',
                    association_reviewed = 1,
                    review_notes = ?,
                    atualizado_em = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    contributor_id or None,
                    row_notes,
                    row["id"],
                ),
            )
            after = self.statement_movement_snapshot(moneyless_int(row["id"]))
            self.write_audit_log(
                moneyless_int(row["organizacao_id"]),
                "marcar_movimento_extrato_nr_em_lote",
                "extrato_movimentos",
                moneyless_int(row["id"]),
                before,
                after,
            )
            if moneyless_int(row["imported_contribution_id"]):
                self.sync_imported_contribution_with_statement_movement(moneyless_int(row["id"]), refresh_lot=False)
            else:
                self.import_single_statement_movement(moneyless_int(row["id"]), refresh_lot=False)
            applied += 1
        return applied

    def statement_lot_manual_association_map(self, lot_id: int) -> dict[str, dict[str, object]]:
        rows = self.conn.execute(
            """
            SELECT nome_origem, bank_document, resolved_person_id, resolved_contribuinte_id, review_notes, COALESCE(association_reviewed, 0) AS association_reviewed
            FROM extrato_movimentos
            WHERE lote_id = ? AND ativo = 1 AND (resolved_person_id IS NOT NULL OR COALESCE(association_reviewed, 0) = 1)
            ORDER BY id
            """,
            (lot_id,),
        ).fetchall()
        grouped: dict[str, dict[str, object]] = {}
        for row in rows:
            key = statement_association_identity_key(row["nome_origem"], row["bank_document"])
            if not key:
                continue
            bucket = grouped.setdefault(
                key,
                {
                    "person_ids": set(),
                    "contributor_ids": set(),
                    "notes": [],
                    "reviewed_without_person": False,
                },
            )
            bucket["person_ids"].add(moneyless_int(row["resolved_person_id"]))
            contributor_id = moneyless_int(row["resolved_contribuinte_id"])
            if contributor_id:
                bucket["contributor_ids"].add(contributor_id)
            if moneyless_int(row["association_reviewed"]) and not moneyless_int(row["resolved_person_id"]):
                bucket["reviewed_without_person"] = True
            note_text = normalize_query(row["review_notes"])
            if note_text:
                bucket["notes"].append(note_text)
        result: dict[str, dict[str, object]] = {}
        for key, bucket in grouped.items():
            person_ids = {item for item in bucket["person_ids"] if item}
            if len(person_ids) > 1:
                continue
            contributor_ids = {item for item in bucket["contributor_ids"] if item}
            if person_ids:
                result[key] = {
                    "person_id": next(iter(person_ids)),
                    "contributor_id": next(iter(contributor_ids)) if len(contributor_ids) == 1 else 0,
                    "note": bucket["notes"][0] if bucket["notes"] else "",
                    "reviewed_without_person": False,
                }
            elif bucket["reviewed_without_person"]:
                result[key] = {
                    "person_id": 0,
                    "contributor_id": next(iter(contributor_ids)) if len(contributor_ids) == 1 else 0,
                    "note": bucket["notes"][0] if bucket["notes"] else "",
                    "reviewed_without_person": True,
                }
        return result

    def create_statement_lot_from_upload(self, filename: str, payload: bytes, layout_code: str = "BRADESCO_EXTRATO") -> int:
        if not payload:
            raise ValueError("Selecione um PDF de extrato bancario antes de importar.")
        if not filename.lower().endswith(".pdf"):
            raise ValueError("Envie um arquivo PDF de extrato bancario.")
        layout_code = normalize_query(layout_code).upper() or "BRADESCO_EXTRATO"
        if not statement_layout_is_supported(layout_code):
            raise ValueError("Layout bancario ainda nao suportado.")
        organization_id = self.default_organization_id()
        organization_name_row = self.conn.execute(
            "SELECT nome FROM organizacoes WHERE id = ? LIMIT 1",
            (organization_id,),
        ).fetchone()
        organization_name = str(organization_name_row["nome"] if organization_name_row else "")
        STATEMENT_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        file_hash = core_bank_lots.uploaded_file_hash(payload)
        if core_bank_lots.statement_duplicate_scope(layout_code) == "santander_family":
            existing = self.conn.execute(
                """
                SELECT id
                FROM extrato_lotes
                WHERE organizacao_id = ? AND hash_arquivo = ? AND layout_codigo LIKE 'SANTANDER%'
                ORDER BY id DESC
                LIMIT 1
                """,
                (organization_id, file_hash),
            ).fetchone()
        else:
            existing = self.conn.execute(
                """
                SELECT id
                FROM extrato_lotes
                WHERE organizacao_id = ? AND layout_codigo = ? AND hash_arquivo = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (organization_id, layout_code, file_hash),
            ).fetchone()
        if existing is not None:
            raise ValueError(f"Este PDF ja foi carregado no lote de extrato #{existing['id']}.")
        target_name = core_bank_lots.statement_upload_target_name(filename, file_hash)
        stored_path = STATEMENT_UPLOAD_DIR / target_name
        stored_path.write_bytes(payload)
        parsed = parse_statement_pdf_by_layout(layout_code, stored_path)
        stored_layout_code = normalize_query(parsed.get("layout_code") or layout_code).upper() or layout_code
        contributor_source = statement_layout_contributor_source(stored_layout_code)
        cursor = self.conn.execute(
            """
            INSERT INTO extrato_lotes (
                organizacao_id, banco, layout_codigo, nome_arquivo, caminho_arquivo, hash_arquivo,
                periodo_inicio, periodo_fim, total_movimentos, total_valor, status, observacoes, atualizado_em
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 'carregado', NULL, CURRENT_TIMESTAMP)
            """,
            (
                organization_id,
                parsed["bank_name"],
                stored_layout_code,
                filename,
                str(stored_path),
                parsed["file_hash"],
                parsed["period_start"],
                parsed["period_end"],
            ),
        )
        lot_id = moneyless_int(cursor.lastrowid)
        people_cache = self.people_for_pix_matching(organization_id)
        rules_by_code = {str(row["codigo_centavos"]): row for row in self.pix_rules(organization_id) if moneyless_int(row["ativo"])}
        total_value = 0.0
        lot_signature_occurrences: dict[str, int] = {}
        for entry in parsed["entries"]:
            if statement_should_skip_entry(stored_layout_code, entry):
                continue
            entry_plan = core_bank_lots.statement_entry_plan(stored_layout_code, entry)
            source_name = entry_plan.source_name
            document_value = entry_plan.bank_document
            document_type = entry_plan.document_type
            match = {"confidence": "sem_match", "score": 0.0, "person_id": 0, "notes": ""}
            suggested_person_id = 0
            suggested_contributor_id = 0
            contributor_kind = "pf"
            same_org_origin = bool(source_name) and statement_is_same_organization_origin(source_name, organization_name)
            if source_name and not same_org_origin:
                match = self.match_pix_entry(
                    organization_id,
                    source_name,
                    document_value,
                    document_type,
                    people_cache=people_cache,
                )
                suggested_person_id = moneyless_int(match["person_id"])
                contributor_kind = contributor_kind_for_identity(
                    source_name,
                    document_type=document_type,
                    document_value=document_value,
                )
                suggested_contributor_id = self.upsert_contributor(
                    organization_id,
                    source_name,
                    contributor_kind,
                    document_value=document_value,
                    document_type=document_type if document_value else "",
                    person_id=suggested_person_id,
                    source=contributor_source,
                    quality="doador",
                )
            elif statement_layout_is_santander(stored_layout_code) and document_value:
                document_type = document_type or santander_document_type(document_value)
                match = self.match_pix_entry(
                    organization_id,
                    "",
                    document_value,
                    document_type,
                    people_cache=people_cache,
                )
                suggested_person_id = moneyless_int(match["person_id"])
                contributor_name = statement_contributor_name_for_identity(
                    stored_layout_code,
                    "",
                    document_value,
                    document_type,
                    match.get("person_name"),
                )
                contributor_kind = contributor_kind_for_identity(
                    contributor_name,
                    document_type=document_type,
                    document_value=document_value,
                )
                suggested_contributor_id = self.upsert_contributor(
                    organization_id,
                    contributor_name,
                    contributor_kind,
                    document_value=document_value,
                    document_type=document_type,
                    person_id=suggested_person_id,
                    source=contributor_source,
                    quality="doador",
                )
            elif same_org_origin:
                match = {
                    "confidence": "mesma_organizacao",
                    "score": 0.0,
                    "person_id": 0,
                    "notes": statement_same_organization_review_note(source_name, organization_name),
                }
            cent_code = entry_plan.cent_code
            rule_row = rules_by_code.get(cent_code)
            type_suggested = core_designations.suggested_type_for_cent_rule(rule_row)
            review_status = self.classify_statement_review_status(str(match["confidence"]), source_name, special_rule=rule_row is not None)
            if core_bank_lots.statement_force_person_review(stored_layout_code, document_value, suggested_person_id, rule_row is not None):
                review_status = "revisar_pessoa"
            signature_global = entry_plan.signature_global
            occurrence_index = lot_signature_occurrences.get(signature_global, 0) + 1
            lot_signature_occurrences[signature_global] = occurrence_index
            duplicate_state = self.find_statement_duplicate_targets(
                organization_id,
                signature_global,
                occurrence_index=occurrence_index,
                ignore_lot_id=lot_id,
            )
            if not duplicate_state["review_status"]:
                duplicate_state = self.find_statement_operational_duplicate_targets(
                    organization_id,
                    stored_layout_code,
                    entry_plan.received_on,
                    entry_plan.amount,
                    source_name,
                    document_value,
                    occurrence_index=occurrence_index,
                    ignore_lot_id=lot_id,
                )
            if duplicate_state["review_status"]:
                review_status = str(duplicate_state["review_status"])
            fingerprint = entry_plan.fingerprint
            self.conn.execute(
                """
                INSERT INTO extrato_movimentos (
                    lote_id, organizacao_id, pagina, ordem_no_lote, data_movimento, competencia, competencia_ordem,
                    valor, codigo_centavos, movement_kind, receiving_code, bank_document, prefixo_historico, nome_origem, nome_normalizado,
                    origin_label, tipo_sugerido, regra_id, confidence, match_score, suggested_person_id, suggested_contribuinte_id,
                    review_status, review_notes, raw_text, fingerprint, signature_global, duplicate_movement_id,
                    duplicate_contribution_id, duplicate_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    lot_id,
                    organization_id,
                    entry_plan.page_number,
                    entry_plan.order_in_file,
                    entry_plan.received_on,
                    entry_plan.competencia,
                    entry_plan.competencia_ordem,
                    entry_plan.amount,
                    cent_code,
                    entry_plan.movement_kind,
                    entry_plan.receiving_code,
                    document_value or None,
                    entry_plan.prefix or None,
                    source_name or None,
                    entry_plan.source_name_normalized or None,
                    entry_plan.origin_label,
                    type_suggested,
                    moneyless_int(rule_row["id"]) if rule_row else None,
                    str(match["confidence"]),
                    float(match["score"]),
                    suggested_person_id or None,
                    suggested_contributor_id or None,
                    review_status,
                    str(duplicate_state["duplicate_reason"] or match["notes"]),
                    entry_plan.raw_text,
                    fingerprint,
                    signature_global,
                    moneyless_int(duplicate_state["duplicate_movement_id"]) or None,
                    moneyless_int(duplicate_state["duplicate_contribution_id"]) or None,
                    str(duplicate_state["duplicate_reason"] or "") or None,
                ),
            )
            total_value += entry_plan.amount
        movement_count = self.scalar("SELECT COUNT(*) FROM extrato_movimentos WHERE lote_id = ? AND ativo = 1", (lot_id,))
        self.conn.execute(
            """
            UPDATE extrato_lotes
            SET total_movimentos = ?, total_valor = ?, status = 'auditando', atualizado_em = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (movement_count, round(total_value, 2), lot_id),
        )
        saved = self.get_statement_lot(lot_id)
        self.write_audit_log(
            organization_id,
            f"criar_lote_extrato_{normalize_query(parsed['bank_name']).lower()}",
            "extrato_lotes",
            lot_id,
            None,
            dict(saved) if saved else {"id": lot_id},
        )
        self.ensure_statement_financial_entries(lot_id)
        self.refresh_statement_lot_status(lot_id)
        self.conn.commit()
        return lot_id

    def reprocess_statement_lot(self, lot_id: int) -> int:
        lot = self.get_statement_lot(lot_id)
        if lot is None:
            raise ValueError("Lote de extrato nao encontrado.")
        organization_id = moneyless_int(lot["organizacao_id"])
        layout_code = normalize_query(lot["layout_codigo"]).upper() or "BRADESCO_EXTRATO"
        contributor_source = statement_layout_contributor_source(layout_code)
        people_cache = self.people_for_pix_matching(organization_id)
        organization_name_row = self.conn.execute(
            "SELECT nome FROM organizacoes WHERE id = ? LIMIT 1",
            (organization_id,),
        ).fetchone()
        organization_name = str(organization_name_row["nome"] if organization_name_row else "")
        manual_association_map = self.statement_lot_manual_association_map(lot_id)
        rules_by_code = {str(row["codigo_centavos"]): row for row in self.pix_rules(organization_id) if moneyless_int(row["ativo"])}
        reparsed = parse_statement_pdf_by_layout(layout_code, Path(str(lot["caminho_arquivo"])))
        reparsed_entries_by_order: dict[int, dict[str, object]] = {}
        for entry in reparsed["entries"]:
            if statement_should_skip_entry(layout_code, entry):
                continue
            reparsed_entries_by_order[moneyless_int(entry["order_in_file"])] = entry
        rows = self.conn.execute(
            """
            SELECT *
            FROM extrato_movimentos
            WHERE lote_id = ? AND ativo = 1 AND review_status <> 'ignorado'
            ORDER BY ordem_no_lote
            """,
            (lot_id,),
        ).fetchall()
        updated = 0
        for row in rows:
            before = dict(row)
            entry = reparsed_entries_by_order.get(moneyless_int(row["ordem_no_lote"]))
            entry_plan = core_bank_lots.statement_reprocess_plan(layout_code, row, entry)
            prefix = entry_plan.prefix
            raw_text = entry_plan.raw_text
            source_name = entry_plan.source_name
            data_movimento = entry_plan.received_on
            competencia = entry_plan.competencia
            competencia_ordem = entry_plan.competencia_ordem
            valor = entry_plan.amount
            movement_kind = entry_plan.movement_kind
            receiving_code = entry_plan.receiving_code
            bank_document = entry_plan.bank_document
            document_type = entry_plan.document_type
            origin_label = entry_plan.origin_label
            match = {"confidence": "sem_match", "score": 0.0, "person_id": 0, "notes": ""}
            suggested_person_id = 0
            suggested_contributor_id = moneyless_int(row["suggested_contribuinte_id"]) if source_name or bank_document else 0
            same_org_origin = bool(source_name) and statement_is_same_organization_origin(source_name, organization_name)
            if source_name and not same_org_origin:
                match = self.match_pix_entry(
                    organization_id,
                    source_name,
                    bank_document,
                    document_type,
                    people_cache=people_cache,
                )
                suggested_person_id = moneyless_int(match["person_id"])
                contributor_kind = contributor_kind_for_identity(
                    source_name,
                    document_type=document_type,
                    document_value=bank_document,
                )
                suggested_contributor_id = self.upsert_contributor(
                    organization_id,
                    source_name,
                    contributor_kind,
                    document_value=bank_document,
                    document_type=document_type if bank_document else "",
                    person_id=suggested_person_id,
                    source=contributor_source,
                    quality="doador",
                )
            elif statement_layout_is_santander(layout_code) and bank_document:
                document_type = document_type or santander_document_type(bank_document)
                match = self.match_pix_entry(
                    organization_id,
                    "",
                    bank_document,
                    document_type,
                    people_cache=people_cache,
                )
                suggested_person_id = moneyless_int(match["person_id"])
                contributor_name = statement_contributor_name_for_identity(
                    layout_code,
                    "",
                    bank_document,
                    document_type,
                    match.get("person_name"),
                )
                contributor_kind = contributor_kind_for_identity(
                    contributor_name,
                    document_type=document_type,
                    document_value=bank_document,
                )
                suggested_contributor_id = self.upsert_contributor(
                    organization_id,
                    contributor_name,
                    contributor_kind,
                    document_value=bank_document,
                    document_type=document_type,
                    person_id=suggested_person_id,
                    source=contributor_source,
                    quality="doador",
                )
            elif same_org_origin:
                suggested_contributor_id = 0
                match = {
                    "confidence": "mesma_organizacao",
                    "score": 0.0,
                    "person_id": 0,
                    "notes": statement_same_organization_review_note(source_name, organization_name),
                }
            cent_code = entry_plan.cent_code
            rule_row = rules_by_code.get(cent_code)
            type_suggested = core_designations.suggested_type_for_cent_rule(rule_row)
            review_status = self.classify_statement_review_status(str(match["confidence"]), source_name, special_rule=rule_row is not None)
            if core_bank_lots.statement_force_person_review(layout_code, bank_document, suggested_person_id, rule_row is not None):
                review_status = "revisar_pessoa"
            signature_global = entry_plan.signature_global
            occurrence_index = self.statement_lot_occurrence_index(
                lot_id,
                signature_global,
                moneyless_int(row["ordem_no_lote"]),
                movement_id=moneyless_int(row["id"]),
            )
            duplicate_state = self.find_statement_duplicate_targets(
                organization_id,
                signature_global,
                occurrence_index=occurrence_index,
                ignore_movement_id=moneyless_int(row["id"]),
                ignore_lot_id=lot_id,
            )
            if not duplicate_state["review_status"]:
                duplicate_state = self.find_statement_operational_duplicate_targets(
                    organization_id,
                    layout_code,
                    entry_plan.received_on,
                    entry_plan.amount,
                    source_name,
                    bank_document,
                    occurrence_index=occurrence_index,
                    ignore_movement_id=moneyless_int(row["id"]),
                    ignore_lot_id=lot_id,
                )
            if duplicate_state["review_status"]:
                review_status = str(duplicate_state["review_status"])
            resolved_person_id = 0
            resolved_contributor_id = 0
            association_reviewed = 0
            association_key = statement_association_identity_key(source_name, bank_document)
            preserved_manual = manual_association_map.get(association_key, {}) if association_key else {}
            if preserved_manual:
                resolved_person_id = moneyless_int(preserved_manual.get("person_id"))
                association_reviewed = 1 if preserved_manual.get("reviewed_without_person") and not resolved_person_id else 0
                identity_name = statement_contributor_name_for_identity(
                    layout_code,
                    source_name,
                    bank_document,
                    document_type,
                    match.get("person_name"),
                )
                if resolved_person_id and identity_name:
                    contributor_kind = contributor_kind_for_identity(
                        identity_name,
                        document_type=document_type,
                        document_value=bank_document,
                    )
                    resolved_contributor_id = self.upsert_contributor(
                        organization_id,
                        identity_name,
                        contributor_kind,
                        document_value=bank_document,
                        document_type=document_type if bank_document else "",
                        person_id=resolved_person_id,
                        source=contributor_source,
                        quality="doador",
                    )
                    self.link_contributor_to_person(
                        resolved_contributor_id,
                        resolved_person_id,
                        note=f"Reaplicado automaticamente durante o realinhamento do extrato {lot['banco']}.",
                        commit=False,
                    )
                elif resolved_person_id:
                    resolved_contributor_id = moneyless_int(preserved_manual.get("contributor_id"))
                elif association_reviewed:
                    resolved_contributor_id = moneyless_int(preserved_manual.get("contributor_id"))
                    if not resolved_contributor_id and identity_name:
                        contributor_kind = contributor_kind_for_identity(
                            identity_name,
                            document_type=document_type,
                            document_value=bank_document,
                        )
                        resolved_contributor_id = self.upsert_contributor(
                            organization_id,
                            identity_name,
                            contributor_kind,
                            document_value=bank_document,
                            document_type=document_type if bank_document else "",
                            source=contributor_source,
                            quality="doador",
                        )
                review_status = "aprovado"
            row_notes = str(duplicate_state["duplicate_reason"] or match["notes"])
            if preserved_manual.get("note"):
                row_notes = merge_statement_review_notes(row_notes, preserved_manual.get("note"))
            self.conn.execute(
                """
                UPDATE extrato_movimentos
                SET data_movimento = ?, competencia = ?, competencia_ordem = ?, valor = ?, codigo_centavos = ?,
                    movement_kind = ?, receiving_code = ?, bank_document = ?, prefixo_historico = ?,
                    nome_origem = ?, nome_normalizado = ?, origin_label = ?, tipo_sugerido = ?, regra_id = ?,
                    confidence = ?, match_score = ?, suggested_person_id = ?, suggested_contribuinte_id = ?,
                    resolved_person_id = ?, resolved_contribuinte_id = ?, resolved_tipo_contribuicao_id = COALESCE(resolved_tipo_contribuicao_id, ?),
                    association_reviewed = ?, review_status = ?, review_notes = ?, raw_text = ?, signature_global = ?,
                    duplicate_movement_id = ?, duplicate_contribution_id = ?, duplicate_reason = ?,
                    atualizado_em = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    data_movimento,
                    competencia,
                    competencia_ordem,
                    valor,
                    cent_code,
                    movement_kind,
                    receiving_code,
                    bank_document or None,
                    prefix or None,
                    source_name or None,
                    normalize_match_name(source_name) or None,
                    origin_label,
                    type_suggested,
                    moneyless_int(rule_row["id"]) if rule_row else None,
                    str(match["confidence"]),
                    float(match["score"]),
                    suggested_person_id or None,
                    suggested_contributor_id or None,
                    resolved_person_id or None,
                    resolved_contributor_id or None,
                    moneyless_int(rule_row["tipo_contribuicao_id"]) if rule_row else None,
                    association_reviewed,
                    review_status,
                    row_notes,
                    raw_text,
                    signature_global,
                    moneyless_int(duplicate_state["duplicate_movement_id"]) or None,
                    moneyless_int(duplicate_state["duplicate_contribution_id"]) or None,
                    str(duplicate_state["duplicate_reason"] or "") or None,
                    row["id"],
                ),
            )
            after_row = self.conn.execute("SELECT * FROM extrato_movimentos WHERE id = ?", (row["id"],)).fetchone()
            self.write_audit_log(
                organization_id,
                "reprocessar_movimento_extrato",
                "extrato_movimentos",
                moneyless_int(row["id"]),
                before,
                dict(after_row) if after_row else None,
            )
            updated += 1
        self.exclude_statement_rows_out_of_scope(lot_id)
        self.ensure_statement_financial_entries(lot_id)
        self.refresh_statement_lot_status(lot_id)
        self.conn.commit()
        return updated

    def update_statement_movement_from_form(self, movement_id: int, form: dict[str, list[str]]) -> int:
        movement = self.get_statement_movement(movement_id)
        if movement is None:
            raise ValueError("Movimento de extrato nao encontrado.")
        organization_id = moneyless_int(movement["organizacao_id"])
        lot_id = moneyless_int(movement["lote_id"])
        action = first_form_value(form, "action", "approve")
        before = self.statement_movement_snapshot(movement_id)
        imported_contribution_id = 0
        selected_person_id = 0
        resolved_contributor_id = 0
        source_name = str(movement["nome_origem"] or "")
        document_value = normalize_query(movement["bank_document"])
        document_type = santander_document_type(document_value)
        contributor_source = statement_layout_contributor_source(movement["layout_codigo"])
        review_notes = first_form_value(form, "review_notes")
        if action == "same_owner":
            contribution_id = moneyless_int(movement["imported_contribution_id"])
            internal_note = merge_statement_review_notes(
                review_notes,
                "Confirmado como mesma_titularidade / origem_interna. Remessa interna entre contas da organizacao.",
            )
            resolved_contributor_id = self.ensure_statement_internal_origin_contributor(movement)
            if contribution_id:
                contribution_before = self.get_contribution(contribution_id)
                if contribution_before is not None:
                    merged_notes = merge_statement_review_notes(
                        contribution_before["observacoes"],
                        internal_note,
                    )
                    self.conn.execute(
                        """
                        UPDATE contribuicoes
                        SET ativo = 0, status_operacional = 'ignorado', contribuinte_id = ?, observacoes = ?, atualizado_em = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (resolved_contributor_id or None, merged_notes, contribution_id),
                    )
                    contribution_after = self.get_contribution(contribution_id)
                    self.write_audit_log(
                        organization_id,
                        "confirmar_mesma_titularidade_contribuicao_extrato",
                        "contribuicoes",
                        contribution_id,
                        dict(contribution_before),
                        dict(contribution_after) if contribution_after else None,
                    )
            self.conn.execute(
                """
                UPDATE extrato_movimentos
                SET resolved_person_id = NULL,
                    resolved_contribuinte_id = ?,
                    association_reviewed = 0,
                    review_status = 'ignorado',
                    review_notes = ?,
                    atualizado_em = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    resolved_contributor_id or None,
                    internal_note,
                    movement_id,
                ),
            )
        elif action == "ignore":
            contribution_id = moneyless_int(movement["imported_contribution_id"])
            if contribution_id:
                contribution_before = self.get_contribution(contribution_id)
                if contribution_before is not None:
                    merged_notes = merge_statement_review_notes(
                        contribution_before["observacoes"],
                        review_notes or "Movimento de extrato marcado como ignorado na auditoria.",
                    )
                    self.conn.execute(
                        """
                        UPDATE contribuicoes
                        SET ativo = 0, status_operacional = 'ignorado', observacoes = ?, atualizado_em = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (merged_notes, contribution_id),
                    )
                    contribution_after = self.get_contribution(contribution_id)
                    self.write_audit_log(
                        organization_id,
                        "ignorar_contribuicao_importada_do_extrato",
                        "contribuicoes",
                        contribution_id,
                        dict(contribution_before),
                        dict(contribution_after) if contribution_after else None,
                    )
            self.conn.execute(
                """
                UPDATE extrato_movimentos
                SET review_status = 'ignorado', association_reviewed = 0, review_notes = ?, atualizado_em = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (review_notes, movement_id),
            )
        else:
            selected_person_id = moneyless_int(form.get("resolved_person_id", ["0"])[0])
            selected_type_id = moneyless_int(form.get("resolved_tipo_contribuicao_id", ["0"])[0])
            if not selected_type_id:
                selected_type_id = self.resolved_statement_type_id_for_row(movement)
            effective_rule_id, type_suggestion, rule_overridden = self.cent_rule_override_decision(
                organization_id,
                moneyless_int(movement["regra_id"]),
                selected_type_id,
                movement["tipo_sugerido"],
            )
            if rule_overridden:
                review_notes = merge_statement_review_notes(
                    review_notes,
                    "Regra de centavos substituida manualmente pelo operador nesta auditoria.",
                )
            if source_name:
                resolved_contributor_id = self.upsert_contributor(
                    organization_id,
                    source_name,
                    contributor_kind_for_identity(source_name),
                    person_id=selected_person_id or 0,
                    source=contributor_source,
                    quality="doador",
                )
                if selected_person_id:
                    self.link_contributor_to_person(
                        resolved_contributor_id,
                        selected_person_id,
                        note=f"Vinculado a partir da auditoria do extrato {movement['banco']}.",
                        commit=False,
                    )
            elif document_value:
                selected_person_name = ""
                if selected_person_id:
                    person_row = self.conn.execute("SELECT nome FROM pessoas WHERE id = ? LIMIT 1", (selected_person_id,)).fetchone()
                    selected_person_name = str(person_row["nome"] if person_row else "")
                contributor_name = statement_contributor_name_for_identity(
                    movement["layout_codigo"],
                    "",
                    document_value,
                    document_type,
                    selected_person_name,
                )
                resolved_contributor_id = self.upsert_contributor(
                    organization_id,
                    contributor_name,
                    contributor_kind_for_identity(
                        contributor_name,
                        document_type=document_type,
                        document_value=document_value,
                    ),
                    document_value=document_value,
                    document_type=document_type,
                    person_id=selected_person_id or 0,
                    source=contributor_source,
                    quality="doador",
                )
                if selected_person_id:
                    self.link_contributor_to_person(
                        resolved_contributor_id,
                        selected_person_id,
                        note=f"Documento financeiro vinculado a partir da auditoria do extrato {movement['banco']}.",
                        commit=False,
                    )
            elif selected_person_id:
                resolved_contributor_id = self.ensure_person_contributor(selected_person_id, source=contributor_source)
            self.conn.execute(
                """
                UPDATE extrato_movimentos
                SET resolved_person_id = ?, resolved_contribuinte_id = ?, resolved_tipo_contribuicao_id = ?,
                    regra_id = ?, tipo_sugerido = ?,
                    review_status = 'aprovado', association_reviewed = ?, review_notes = ?, atualizado_em = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    selected_person_id or None,
                    resolved_contributor_id or None,
                    selected_type_id or None,
                    effective_rule_id,
                    type_suggestion,
                    1 if not selected_person_id else 0,
                    review_notes,
                    movement_id,
                ),
            )
        after = self.statement_movement_snapshot(movement_id)
        self.write_audit_log(
            organization_id,
            "auditar_movimento_extrato",
            "extrato_movimentos",
            movement_id,
            before,
            after,
        )
        if action not in {"ignore", "same_owner"}:
            propagated = 0
            can_propagate_identity = bool(normalize_query(source_name) or cleaned_document_token(document_value))
            if selected_person_id and can_propagate_identity:
                propagated = self.apply_statement_person_to_same_name_in_lot(
                    movement_id,
                    selected_person_id,
                    resolved_contributor_id,
                    review_notes=review_notes,
                )
            elif not selected_person_id and can_propagate_identity:
                propagated = self.apply_statement_no_person_to_same_name_in_lot(
                    movement_id,
                    resolved_contributor_id,
                    review_notes=review_notes,
                )
            imported_contribution_id = self.import_single_statement_movement(movement_id, refresh_lot=False)
            if propagated:
                updated_after = self.statement_movement_snapshot(movement_id)
                propagation_note = (
                    f"Associacao replicada automaticamente para {propagated} ocorrencia(s) iguais no mesmo lote."
                    if selected_person_id
                    else f"Decisao NR replicada automaticamente para {propagated} ocorrencia(s) iguais no mesmo lote."
                )
                current_note = merge_statement_review_notes(
                    updated_after.get("review_notes") if updated_after else review_notes,
                    propagation_note,
                )
                self.conn.execute(
                    """
                    UPDATE extrato_movimentos
                    SET review_notes = ?, atualizado_em = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (current_note, movement_id),
                )
                imported_contribution_id = self.sync_imported_contribution_with_statement_movement(movement_id, refresh_lot=False)
            self.refresh_statement_lot_status(lot_id)
        else:
            self.refresh_statement_lot_status(lot_id)
        self.conn.commit()
        return imported_contribution_id

    def close_statement_lot(self, lot_id: int) -> dict[str, int]:
        lot = self.get_statement_lot(lot_id)
        if lot is None:
            raise ValueError("Lote de extrato nao encontrado.")
        if str(lot["status"]) == "encerrado":
            return {"importados": 0, "movidos_contribuintes": 0}
        duplicate_count = self.scalar(
            """
            SELECT COUNT(*)
            FROM extrato_movimentos
            WHERE lote_id = ? AND ativo = 1 AND imported_contribution_id IS NULL AND review_status = 'revisar_duplicidade'
            """,
            (lot_id,),
        )
        if duplicate_count:
            raise ValueError(
                "Ainda existem casos em Revisar duplicidade. Resolva essas ocorrencias antes de encerrar o lote para nao correr risco de lancamento repetido."
            )
        organization_id = moneyless_int(lot["organizacao_id"])
        imported_now = self.ensure_statement_financial_entries(lot_id)
        rows = self.conn.execute(
            """
            SELECT *
            FROM extrato_movimentos
            WHERE lote_id = ? AND ativo = 1 AND review_status IN ('revisar_pessoa', 'revisar_destinacao')
            ORDER BY data_movimento, ordem_no_lote
            """,
            (lot_id,),
        ).fetchall()
        moved_to_contributors = 0
        contributor_source = statement_layout_contributor_source(lot["layout_codigo"])
        for row in rows:
            before = self.statement_movement_snapshot(moneyless_int(row["id"]))
            contributor_id = moneyless_int(row["resolved_contribuinte_id"]) or moneyless_int(row["suggested_contribuinte_id"])
            if not contributor_id and normalize_query(row["nome_origem"]):
                contributor_id = self.upsert_contributor(
                    organization_id,
                    str(row["nome_origem"]),
                    contributor_kind_for_identity(row["nome_origem"]),
                    source=contributor_source,
                    quality="doador",
                )
            existing_notes = normalize_query(row["review_notes"])
            close_note = "Lote encerrado pelo operador. O credito foi preservado no contribuinte auxiliar e segue para associacao futura na central de contribuintes."
            combined_notes = merge_statement_review_notes(existing_notes, close_note)
            self.conn.execute(
                """
                UPDATE extrato_movimentos
                SET resolved_person_id = NULL,
                    resolved_contribuinte_id = ?,
                    resolved_tipo_contribuicao_id = ?,
                    review_notes = ?,
                    atualizado_em = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    contributor_id or None,
                    self.resolved_statement_type_id_for_row(row) or None,
                    combined_notes,
                    row["id"],
                ),
            )
            imported_contribution_id = self.import_single_statement_movement(moneyless_int(row["id"]), refresh_lot=False)
            after = self.statement_movement_snapshot(moneyless_int(row["id"]))
            self.write_audit_log(
                organization_id,
                "encerrar_movimento_extrato_para_contribuinte",
                "extrato_movimentos",
                moneyless_int(row["id"]),
                before,
                after,
            )
            if imported_contribution_id:
                moved_to_contributors += 1
        before_lot = dict(lot)
        lot_close_note = merge_statement_review_notes(
            lot["observacoes"],
            "Lote encerrado. O que restou sem pessoa vinculada foi preservado para associacao futura na central de contribuintes.",
        )
        self.conn.execute(
            """
            UPDATE extrato_lotes
            SET status = 'encerrado',
                observacoes = ?,
                atualizado_em = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                lot_close_note,
                lot_id,
            ),
        )
        after_lot = self.get_statement_lot(lot_id)
        self.write_audit_log(
            organization_id,
            "encerrar_lote_extrato",
            "extrato_lotes",
            lot_id,
            before_lot,
            dict(after_lot) if after_lot else None,
        )
        self.conn.commit()
        return {"importados": imported_now, "movidos_contribuintes": moved_to_contributors}

    def dashboard(self) -> dict[str, int]:
        summary_rows = self.audit_summary()
        total_pendencias = sum(moneyless_int(row["quantidade"]) for row in summary_rows)
        total_avisos = sum(moneyless_int(row["quantidade"]) for row in summary_rows if row["severidade"] == "aviso")
        return {
            "pessoas": self.scalar("SELECT COUNT(*) FROM pessoas WHERE ativo = 1"),
            "membros_ativos": self.scalar("SELECT COUNT(*) FROM pessoas WHERE status = 'membro_ativo' AND ativo = 1"),
            "membros_inativos": self.scalar("SELECT COUNT(*) FROM pessoas WHERE status = 'membro_inativo' AND ativo = 1"),
            "pastores": self.scalar("SELECT COUNT(*) FROM pessoa_perfis WHERE perfil = 'pastor' AND ativo = 1"),
            "lideres": self.scalar("SELECT COUNT(*) FROM pessoa_perfis WHERE perfil = 'lider' AND ativo = 1"),
            "pendencias": total_pendencias,
            "avisos": total_avisos,
            "contribuicoes": self.scalar("SELECT COUNT(*) FROM contribuicoes WHERE ativo = 1"),
            "recibos": self.scalar("SELECT COUNT(*) FROM recibos WHERE status <> 'cancelado'"),
        }

    def people_import_lots(self, limit: int = 10) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT
                il.*,
                (
                    SELECT COUNT(*)
                    FROM import_pendencias ip
                    WHERE ip.lote_id = il.id AND ip.resolvido = 0
                ) AS pendencias_abertas
                ,
                (
                    SELECT COUNT(*)
                    FROM import_linhas linha
                    JOIN pessoas p ON p.id = linha.registro_id
                    WHERE linha.lote_id = il.id
                      AND linha.registro_tipo = 'pessoa'
                      AND p.ativo = 1
                ) AS pessoas_ativas,
                (
                    SELECT COUNT(*)
                    FROM import_linhas linha
                    JOIN pessoas p ON p.id = linha.registro_id
                    WHERE linha.lote_id = il.id
                      AND linha.registro_tipo = 'pessoa'
                      AND p.ativo = 1
                      AND p.nome = 'Nome nao informado'
                ) AS pessoas_sem_nome
            FROM import_lotes il
            WHERE il.tipo_importacao IN ('pessoas_membros', 'pessoas_complementar_incremental')
            ORDER BY il.criado_em DESC, il.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    def get_people_import_lot(self, lot_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            """
            SELECT
                il.*,
                (
                    SELECT COUNT(*)
                    FROM import_pendencias ip
                    WHERE ip.lote_id = il.id AND ip.resolvido = 0
                ) AS pendencias_abertas
            FROM import_lotes il
            WHERE il.id = ?
              AND il.tipo_importacao IN ('pessoas_membros', 'pessoas_complementar_incremental')
            """,
            (lot_id,),
        ).fetchone()

    def recent_people_import_lot_ids(
        self,
        requested_ids: list[int] | tuple[int, ...] | set[int] | None = None,
        default_limit: int = 2,
    ) -> list[int]:
        valid_requested = [moneyless_int(item) for item in (requested_ids or []) if moneyless_int(item)]
        if valid_requested:
            placeholders = ", ".join("?" for _ in valid_requested)
            rows = self.conn.execute(
                f"""
                SELECT id
                FROM import_lotes
                WHERE id IN ({placeholders})
                  AND tipo_importacao IN ('pessoas_membros', 'pessoas_complementar_incremental')
                ORDER BY criado_em DESC, id DESC
                """,
                tuple(valid_requested),
            ).fetchall()
            return [moneyless_int(row["id"]) for row in rows]
        rows = self.conn.execute(
            """
            SELECT id
            FROM import_lotes
            WHERE tipo_importacao = 'pessoas_complementar_incremental'
            ORDER BY criado_em DESC, id DESC
            LIMIT ?
            """,
            (default_limit,),
        ).fetchall()
        if not rows:
            rows = self.conn.execute(
                """
                SELECT id
                FROM import_lotes
                WHERE tipo_importacao IN ('pessoas_membros', 'pessoas_complementar_incremental')
                ORDER BY criado_em DESC, id DESC
                LIMIT ?
                """,
                (default_limit,),
            ).fetchall()
        return [moneyless_int(row["id"]) for row in rows]

    def create_people_import_from_upload(
        self,
        filename: str,
        payload: bytes,
        allow_duplicate_file: bool = False,
    ) -> dict[str, object]:
        if not payload:
            raise ValueError("Selecione uma planilha Excel antes de importar pessoas.")
        if not filename.lower().endswith(".xlsx"):
            raise ValueError("Envie uma planilha Excel no formato .xlsx.")
        from scripts.importar_membros_xlsx import import_members_incremental

        PEOPLE_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        reports_dir = ROOT / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        file_hash = hashlib.sha256(payload).hexdigest()
        if not allow_duplicate_file:
            existing = self.conn.execute(
                "SELECT id FROM import_lotes WHERE arquivo_hash = ? ORDER BY id DESC LIMIT 1",
                (file_hash,),
            ).fetchone()
            if existing is not None:
                raise ValueError(f"Esta planilha ja foi importada no lote de pessoas #{existing['id']}.")
        target_name = f"{date.today().isoformat()}_{slugify_filename_text(Path(filename).stem, fallback='pessoas')}_{file_hash[:10]}.xlsx"
        stored_path = PEOPLE_UPLOAD_DIR / target_name
        stored_path.write_bytes(payload)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_path = reports_dir / f"RELATORIO_IMPORTACAO_INCREMENTAL_PESSOAS_{timestamp}_{file_hash[:10]}.md"
        self.conn.commit()
        self.conn.close()
        try:
            return import_members_incremental(
                stored_path,
                self.path,
                report_path,
                allow_duplicate_file=allow_duplicate_file,
            )
        finally:
            self.conn = sqlite3.connect(self.path, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            self.ensure_contribution_catalogs()
            self.ensure_pix_support()
            self.ensure_statement_support()

    def list_people(self, q: str = "", status: str | list[str] = "", perfil: str = "", limit: int | None = None) -> list[sqlite3.Row]:
        clauses = ["p.ativo = 1"]
        params: list[object] = []
        if q:
            clause, lookup_params = self.person_lookup_clause(q, table_alias="p")
            if clause:
                clauses.append(clause)
                params.extend(lookup_params)
        if isinstance(status, list):
            selected_statuses = [normalize_query(value) for value in status if normalize_query(value)]
        else:
            selected_statuses = [normalize_query(status)] if normalize_query(status) else []
        if selected_statuses:
            placeholders = ", ".join("?" for _ in selected_statuses)
            clauses.append(f"p.status IN ({placeholders})")
            params.extend(selected_statuses)
        if perfil:
            clauses.append(
                "EXISTS (SELECT 1 FROM pessoa_perfis pp WHERE pp.pessoa_id = p.id AND pp.perfil = ? AND pp.ativo = 1)"
            )
            params.append(perfil)
        limit_sql = ""
        if limit and limit > 0:
            limit_sql = "LIMIT ?"
            params.append(limit)
        rows = self.conn.execute(
            f"""
            SELECT
                p.*,
                GROUP_CONCAT(pp.perfil, ', ') AS perfis,
                (SELECT COUNT(*) FROM import_pendencias ip JOIN import_linhas il ON il.id = ip.linha_id WHERE il.registro_id = p.id AND il.registro_tipo = 'pessoa' AND ip.resolvido = 0) AS pendencias
            FROM pessoas p
            LEFT JOIN pessoa_perfis pp ON pp.pessoa_id = p.id AND pp.ativo = 1
            WHERE {' AND '.join(clauses)}
            GROUP BY p.id
            ORDER BY p.nome
            {limit_sql}
            """,
            params,
        ).fetchall()
        duplicate_map = self.duplicate_member_number_count_by_person()
        enriched: list[dict[str, object]] = []
        for row in rows:
            data = dict(row)
            data["pendencias"] = moneyless_int(data.get("pendencias")) + duplicate_map.get(moneyless_int(row["id"]), 0)
            enriched.append(data)
        return enriched

    def get_person(self, person_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM pessoas WHERE id = ?", (person_id,)).fetchone()

    def default_organization_id(self) -> int:
        row = self.conn.execute("SELECT id FROM organizacoes ORDER BY id LIMIT 1").fetchone()
        return moneyless_int(row["id"] if row else 1)

    def next_member_code(self, organization_id: int) -> str:
        rows = self.conn.execute(
            """
            SELECT codigo_interno
            FROM pessoas
            WHERE organizacao_id = ? AND codigo_interno IS NOT NULL AND TRIM(codigo_interno) <> ''
            """,
            (organization_id,),
        ).fetchall()
        numeric_codes = [clean_member_code(row["codigo_interno"]) for row in rows if clean_member_code(row["codigo_interno"]).isdigit()]
        if not numeric_codes:
            return "00001"
        width = max(5, max(len(code) for code in numeric_codes))
        next_number = max(int(code) for code in numeric_codes) + 1
        return str(next_number).zfill(max(width, len(str(next_number))))

    def member_code_exists(self, organization_id: int, code: str, ignore_person_id: int = 0) -> bool:
        normalized = clean_member_code(code)
        if not normalized:
            return False
        clauses = ["organizacao_id = ?", "codigo_interno = ?"]
        params: list[object] = [organization_id, normalized]
        if ignore_person_id:
            clauses.append("id <> ?")
            params.append(ignore_person_id)
        row = self.conn.execute(
            f"SELECT 1 FROM pessoas WHERE {' AND '.join(clauses)} LIMIT 1",
            params,
        ).fetchone()
        return row is not None

    def resolved_member_code(self, organization_id: int, requested_code: str = "", ignore_person_id: int = 0) -> str:
        normalized = clean_member_code(requested_code)
        if normalized and not self.member_code_exists(organization_id, normalized, ignore_person_id=ignore_person_id):
            return normalized
        candidate = self.next_member_code(organization_id)
        while self.member_code_exists(organization_id, candidate, ignore_person_id=ignore_person_id):
            candidate = str(int(candidate) + 1).zfill(len(candidate))
        return candidate

    def person_lookup_clause(self, query_text: str, table_alias: str = "p") -> tuple[str, list[object]]:
        q = normalize_query(query_text)
        if not q:
            return "", []
        if is_system_id_search(q):
            person_id_search = clean_system_id(q)
            return (f"{table_alias}.id = ?", [person_id_search]) if person_id_search else ("", [])
        if is_member_code_search(q):
            member_code = clean_member_code(q)
            return (f"{table_alias}.codigo_interno = ?", [member_code]) if member_code else ("", [])
        if is_numeric_search(q):
            member_code = clean_member_code(q)
            cpf_value = clean_cpf(q)
            person_id_search = searchable_person_id(q)
            has_member_code = self.member_code_exists(self.default_organization_id(), member_code)
            if has_member_code:
                return f"{table_alias}.codigo_interno = ?", [member_code]
            if len(cpf_value) == 11:
                row = self.conn.execute(
                    "SELECT 1 FROM pessoas WHERE cpf = ? LIMIT 1",
                    (cpf_value,),
                ).fetchone()
                if row is not None:
                    return f"{table_alias}.cpf = ?", [cpf_value]
            if person_id_search:
                return f"{table_alias}.id = ?", [person_id_search]
        like = f"%{q}%"
        return (
            f"({table_alias}.nome LIKE ? OR {table_alias}.codigo_interno LIKE ? OR {table_alias}.cpf LIKE ? OR {table_alias}.email_principal LIKE ? OR {table_alias}.telefone_principal LIKE ?)",
            [like, like, like, like, like],
        )

    def contribution_types(self, organization_id: int, requires_person_only: bool = False) -> list[sqlite3.Row]:
        clauses = ["organizacao_id = ?", "ativo = 1"]
        params: list[object] = [organization_id]
        if requires_person_only:
            clauses.append("exige_pessoa = 1")
        return self.conn.execute(
            f"""
            SELECT *
            FROM tipos_contribuicao
            WHERE {' AND '.join(clauses)}
            ORDER BY nome
            """,
            params,
        ).fetchall()

    def receiving_forms(self, organization_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT *
            FROM formas_recebimento
            WHERE organizacao_id = ? AND ativo = 1
            ORDER BY nome
            """,
            (organization_id,),
        ).fetchall()

    def contribution_filters(
        self,
        q: str = "",
        competencia: str = "",
        tipo_id: int = 0,
        person_id: int = 0,
    ) -> tuple[list[str], list[object]]:
        clauses = ["c.ativo = 1"]
        params: list[object] = []
        if q:
            like = f"%{normalize_query(q)}%"
            clause, lookup_params = self.person_lookup_clause(q, table_alias="p")
            if clause:
                clauses.append(f"({clause} OR ct.nome LIKE ? OR ct.documento_principal LIKE ?)")
                params.extend([*lookup_params, like, like])
            else:
                clauses.append("(ct.nome LIKE ? OR ct.documento_principal LIKE ?)")
                params.extend([like, like])
        if competencia:
            clauses.append("c.competencia = ?")
            params.append(competencia)
        if tipo_id:
            clauses.append("c.tipo_contribuicao_id = ?")
            params.append(tipo_id)
        if person_id:
            clauses.append("c.pessoa_id = ?")
            params.append(person_id)
        return clauses, params

    def person_contribution_summary(self, person_id: int) -> sqlite3.Row:
        row = self.conn.execute(
            """
            SELECT
                COUNT(*) AS quantidade,
                COALESCE(SUM(valor), 0) AS total,
                MAX(data_recebimento) AS ultima_data
            FROM contribuicoes
            WHERE pessoa_id = ? AND ativo = 1
            """,
            (person_id,),
        ).fetchone()
        return row

    def person_contributions(self, person_id: int, limit: int = 12) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT
                c.*,
                tc.nome AS tipo_nome,
                fr.nome AS forma_nome,
                ct.nome AS contribuinte_nome,
                ct.tipo AS contribuinte_tipo,
                ct.documento_principal AS contribuinte_documento
            FROM contribuicoes c
            JOIN tipos_contribuicao tc ON tc.id = c.tipo_contribuicao_id
            LEFT JOIN formas_recebimento fr ON fr.id = c.forma_recebimento_id
            LEFT JOIN contribuintes ct ON ct.id = c.contribuinte_id
            WHERE c.pessoa_id = ? AND c.ativo = 1
            ORDER BY c.data_recebimento DESC, c.id DESC
            LIMIT ?
            """,
            (person_id, limit),
        ).fetchall()

    def person_linked_contributors(self, person_id: int, limit: int = 12) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT
                c.*,
                COUNT(co.id) AS contribuicoes_qtd,
                COALESCE(SUM(co.valor), 0) AS total_contribuido,
                MAX(co.data_recebimento) AS ultima_contribuicao
            FROM contribuintes c
            LEFT JOIN contribuicoes co ON co.contribuinte_id = c.id AND co.ativo = 1
            WHERE c.pessoa_id = ? AND c.ativo = 1
            GROUP BY c.id
            ORDER BY MAX(co.data_recebimento) DESC, c.nome
            LIMIT ?
            """,
            (person_id, limit),
        ).fetchall()

    def person_financial_identifiers(self, person_id: int, limit: int = 20) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT
                ci.*,
                c.nome AS contribuinte_nome,
                c.tipo AS contribuinte_tipo
            FROM contribuintes_identificadores ci
            LEFT JOIN contribuintes c ON c.id = ci.contribuinte_id
            WHERE ci.pessoa_id = ? AND ci.ativo = 1
            ORDER BY ci.principal DESC, ci.tipo, ci.valor
            LIMIT ?
            """,
            (person_id, limit),
        ).fetchall()

    def save_person_financial_identifier(
        self,
        person_id: int,
        identifier_type: str,
        raw_value: str,
        notes: str = "",
    ) -> int:
        person = self.get_person(person_id)
        if person is None:
            raise ValueError("Pessoa nao encontrada para cadastrar identidade financeira.")
        organization_id = moneyless_int(person["organizacao_id"])
        normalized_type = normalize_query(identifier_type)
        allowed_types = {"cnpj", "cnpj_mascarado", "cpf_mascarado", "documento_bancario"}
        if normalized_type not in allowed_types:
            raise ValueError("Escolha um tipo valido de identidade financeira.")
        raw_text = str(raw_value or "").strip()
        if not raw_text:
            raise ValueError("Informe o CNPJ ou documento que deseja associar.")
        if normalized_type == "cnpj":
            stored_value = "".join(ch for ch in raw_text if ch.isdigit())
            if len(stored_value) != 14:
                raise ValueError("Informe um CNPJ completo com 14 digitos.")
        else:
            stored_value = normalize_query(raw_text)
        existing = self.conn.execute(
            """
            SELECT id
            FROM contribuintes_identificadores
            WHERE pessoa_id = ? AND contribuinte_id IS NULL AND tipo = ? AND valor = ? AND ativo = 1
            LIMIT 1
            """,
            (person_id, normalized_type, stored_value),
        ).fetchone()
        if existing is not None:
            return moneyless_int(existing["id"])
        cursor = self.conn.execute(
            """
            INSERT INTO contribuintes_identificadores (
                organizacao_id, pessoa_id, contribuinte_id, tipo, valor, principal, ativo, observacoes
            ) VALUES (?, ?, NULL, ?, ?, 0, 1, ?)
            """,
            (
                organization_id,
                person_id,
                normalized_type,
                stored_value,
                normalize_query(notes) or "Identidade financeira antecipada cadastrada na ficha da pessoa.",
            ),
        )
        identifier_id = moneyless_int(cursor.lastrowid)
        self.write_audit_log(
            organization_id,
            "salvar_identidade_financeira_pessoa",
            "contribuintes_identificadores",
            identifier_id,
            None,
            {
                "pessoa_id": person_id,
                "tipo": normalized_type,
                "valor": stored_value,
                "observacoes": normalize_query(notes),
            },
        )
        self.conn.commit()
        return identifier_id

    def person_possible_contributors(self, person_id: int, limit: int = 10) -> list[dict[str, object]]:
        person = self.get_person(person_id)
        if person is None:
            return []
        person_norm = normalize_match_name(person["nome"])
        person_cpf = clean_cpf(person["cpf"])
        rows = self.conn.execute(
            """
            SELECT
                c.*,
                COUNT(co.id) AS contribuicoes_qtd,
                COALESCE(SUM(co.valor), 0) AS total_contribuido,
                MAX(co.data_recebimento) AS ultima_contribuicao
            FROM contribuintes c
            LEFT JOIN contribuicoes co ON co.contribuinte_id = c.id AND co.ativo = 1
            WHERE c.ativo = 1 AND (c.pessoa_id IS NULL OR c.pessoa_id = ?)
            GROUP BY c.id
            ORDER BY MAX(co.data_recebimento) DESC, c.nome
            """,
            (person_id,),
        ).fetchall()
        suggestions: list[dict[str, object]] = []
        for row in rows:
            contributor_norm = normalize_match_name(row["nome"])
            exact_name = bool(person_norm and contributor_norm == person_norm)
            similarity_ratio = SequenceMatcher(None, person_norm, contributor_norm).ratio() if person_norm and contributor_norm else 0.0
            doc_match = bool(
                person_cpf
                and str(row["documento_tipo"] or "") == "cpf"
                and clean_cpf(row["documento_principal"]) == person_cpf
            )
            if not doc_match and not exact_name and similarity_ratio < 0.86:
                continue
            if doc_match and exact_name:
                reason = "CPF exato e nome coincidente."
                score = 0.99
            elif doc_match:
                reason = "CPF exato no contribuinte financeiro."
                score = 0.97
            elif exact_name:
                reason = "Nome financeiro coincide com a ficha da pessoa."
                score = 0.93
            else:
                reason = f"Nome com semelhanca relevante ({similarity_ratio:.2f})."
                score = similarity_ratio
            suggestions.append(
                {
                    **dict(row),
                    "suggestion_reason": reason,
                    "suggestion_score": score,
                    "suggestion_doc_match": doc_match,
                    "suggestion_exact_name": exact_name,
                    "suggestion_ratio": similarity_ratio,
                }
            )
        suggestions.sort(
            key=lambda item: (
                0 if item["suggestion_doc_match"] and item["suggestion_exact_name"] else
                1 if item["suggestion_doc_match"] else
                2 if item["suggestion_exact_name"] else
                3,
                -float(item["suggestion_score"]),
                str(item["nome"]),
            )
        )
        return suggestions[:limit]

    def person_contribution_years(self, person_id: int) -> list[str]:
        rows = self.conn.execute(
            """
            SELECT DISTINCT SUBSTR(data_recebimento, 1, 4) AS ano
            FROM contribuicoes
            WHERE pessoa_id = ? AND ativo = 1 AND data_recebimento IS NOT NULL AND data_recebimento <> ''
            ORDER BY ano DESC
            """,
            (person_id,),
        ).fetchall()
        years = [str(row["ano"]) for row in rows if row["ano"]]
        current_year = str(date.today().year)
        if current_year not in years:
            years.insert(0, current_year)
        return years

    def person_contribution_competences(self, person_id: int) -> list[str]:
        rows = self.conn.execute(
            """
            SELECT DISTINCT competencia, competencia_ordem
            FROM contribuicoes
            WHERE pessoa_id = ? AND ativo = 1 AND competencia IS NOT NULL AND competencia <> ''
            ORDER BY competencia_ordem DESC, competencia DESC
            """,
            (person_id,),
        ).fetchall()
        return [str(row["competencia"]) for row in rows if row["competencia"]]

    def person_contribution_statement(
        self,
        person_id: int,
        year: str = "",
        date_start: str = "",
        date_end: str = "",
        competencia: str = "",
        type_ids: list[int] | None = None,
    ) -> list[sqlite3.Row]:
        clauses = ["c.pessoa_id = ?", "c.ativo = 1"]
        params: list[object] = [person_id]
        if year and year.lower() != "todos":
            clauses.append("SUBSTR(c.data_recebimento, 1, 4) = ?")
            params.append(year)
        if date_start:
            clauses.append("c.data_recebimento >= ?")
            params.append(date_start)
        if date_end:
            clauses.append("c.data_recebimento <= ?")
            params.append(date_end)
        if competencia:
            clauses.append("c.competencia = ?")
            params.append(competencia)
        filtered_type_ids = [int(item) for item in (type_ids or []) if moneyless_int(item) > 0]
        if filtered_type_ids:
            placeholders = ", ".join("?" for _ in filtered_type_ids)
            clauses.append(f"c.tipo_contribuicao_id IN ({placeholders})")
            params.extend(filtered_type_ids)
        return self.conn.execute(
            f"""
            SELECT
                c.*,
                tc.nome AS tipo_nome,
                fr.nome AS forma_nome
            FROM contribuicoes c
            JOIN tipos_contribuicao tc ON tc.id = c.tipo_contribuicao_id
            LEFT JOIN formas_recebimento fr ON fr.id = c.forma_recebimento_id
            WHERE {' AND '.join(clauses)}
            ORDER BY c.data_recebimento, c.id
            """,
            params,
        ).fetchall()

    def contributions_summary(
        self,
        q: str = "",
        competencia: str = "",
        tipo_id: int = 0,
        person_id: int = 0,
    ) -> sqlite3.Row:
        clauses, params = self.contribution_filters(q, competencia, tipo_id, person_id)
        return self.conn.execute(
            f"""
            SELECT
                COUNT(*) AS quantidade,
                COALESCE(SUM(c.valor), 0) AS total,
                COUNT(DISTINCT CASE WHEN c.pessoa_id IS NOT NULL THEN 'P:' || c.pessoa_id ELSE 'C:' || COALESCE(c.contribuinte_id, 0) END) AS doadores,
                MAX(c.data_recebimento) AS ultima_data
            FROM contribuicoes c
            LEFT JOIN pessoas p ON p.id = c.pessoa_id
            LEFT JOIN contribuintes ct ON ct.id = c.contribuinte_id
            WHERE {' AND '.join(clauses)}
            """,
            params,
        ).fetchone()

    def list_contributions(
        self,
        q: str = "",
        competencia: str = "",
        tipo_id: int = 0,
        person_id: int = 0,
        limit: int = 150,
    ) -> list[sqlite3.Row]:
        clauses, params = self.contribution_filters(q, competencia, tipo_id, person_id)
        params.append(limit)
        return self.conn.execute(
            f"""
            SELECT
                c.*,
                p.nome AS pessoa_nome,
                p.codigo_interno,
                p.cpf,
                ct.nome AS contribuinte_nome,
                ct.tipo AS contribuinte_tipo,
                ct.documento_principal AS contribuinte_documento,
                tc.nome AS tipo_nome,
                fr.nome AS forma_nome
            FROM contribuicoes c
            LEFT JOIN pessoas p ON p.id = c.pessoa_id
            LEFT JOIN contribuintes ct ON ct.id = c.contribuinte_id
            JOIN tipos_contribuicao tc ON tc.id = c.tipo_contribuicao_id
            LEFT JOIN formas_recebimento fr ON fr.id = c.forma_recebimento_id
            WHERE {' AND '.join(clauses)}
            ORDER BY UPPER(COALESCE(p.nome, ct.nome, 'Contribuinte nao identificado')),
                     c.data_recebimento ASC,
                     c.id ASC
            LIMIT ?
            """,
            params,
        ).fetchall()

    def contributor_report_exact_people(self, query_text: str, limit: int = 12) -> list[sqlite3.Row]:
        q = normalize_query(query_text)
        if not q:
            return []
        is_direct_lookup = is_system_id_search(q) or is_member_code_search(q)
        if is_numeric_search(q):
            member_code = clean_member_code(q)
            cpf_value = clean_cpf(q)
            person_id_search = searchable_person_id(q)
            is_direct_lookup = bool(
                self.member_code_exists(self.default_organization_id(), member_code)
                or len(cpf_value) == 11
                or person_id_search
            )
        if is_direct_lookup:
            return self.list_people(q=q, limit=limit)
        query_norm = normalize_match_name(q)
        if not query_norm:
            return []
        rows = self.conn.execute(
            """
            SELECT id, nome, status, codigo_interno, cpf
            FROM pessoas
            WHERE ativo = 1
            ORDER BY nome
            """
        ).fetchall()
        exact_rows = [row for row in rows if normalize_match_name(row["nome"]) == query_norm]
        return exact_rows[:limit]

    def contributor_report_person_suggestions(self, query_text: str, limit: int = 8) -> list[dict[str, object]]:
        q = normalize_query(query_text)
        query_norm = normalize_match_name(q)
        if not q or not query_norm:
            return []
        query_tokens = [token for token in query_norm.split() if token]
        rows = self.conn.execute(
            """
            SELECT id, nome, status, codigo_interno, cpf
            FROM pessoas
            WHERE ativo = 1
            ORDER BY nome
            """
        ).fetchall()
        suggestions: list[dict[str, object]] = []
        for row in rows:
            candidate_norm = normalize_match_name(row["nome"])
            if not candidate_norm or candidate_norm == query_norm:
                continue
            ratio = SequenceMatcher(None, query_norm, candidate_norm).ratio()
            candidate_tokens = [token for token in candidate_norm.split() if token]
            shared_tokens = [token for token in query_tokens if token in candidate_tokens]
            initial_matches = sum(
                1
                for token in query_tokens
                if len(token) == 1 and any(candidate.startswith(token) for candidate in candidate_tokens)
            )
            first_token_match = bool(query_tokens and candidate_tokens and query_tokens[0] == candidate_tokens[0])
            last_token_match = bool(query_tokens and candidate_tokens and query_tokens[-1] == candidate_tokens[-1])
            prefix_match = candidate_norm.startswith(query_norm) or query_norm.startswith(candidate_norm)
            expanded_variant = pix_name_is_expanded_variant(q, row["nome"]) or pix_name_is_expanded_variant(row["nome"], q)
            if ratio < 0.68 and len(shared_tokens) < 2 and not expanded_variant and not last_token_match and not prefix_match and initial_matches <= 0:
                continue
            score = (ratio * 100.0) + (len(shared_tokens) * 7.0)
            reasons: list[str] = []
            if expanded_variant:
                score += 14.0
                reasons.append("nome expandido/abreviado compativel")
            if prefix_match:
                score += 12.0
                reasons.append("prefixo do nome coincide")
            if first_token_match:
                score += 6.0
                reasons.append("primeiro nome coincide")
            if last_token_match:
                score += 8.0
                reasons.append("sobrenome final coincide")
            if initial_matches:
                score += float(initial_matches * 6)
                reasons.append(f"{initial_matches} inicial(is) compativel(is)")
            if len(shared_tokens) >= 2:
                reasons.append(f"{len(shared_tokens)} token(s) em comum")
            reasons.append(f"aderencia {ratio:.2f}")
            suggestions.append(
                {
                    "id": moneyless_int(row["id"]),
                    "nome": str(row["nome"]),
                    "status": str(row["status"] or ""),
                    "codigo_interno": str(row["codigo_interno"] or ""),
                    "cpf": str(row["cpf"] or ""),
                    "score": round(score, 2),
                    "ratio": round(ratio, 4),
                    "reason": ", ".join(reasons),
                }
            )
        suggestions.sort(key=lambda item: (-float(item["score"]), str(item["nome"])))
        return suggestions[:limit]

    def contributor_report_exact_contributors(self, query_text: str, limit: int = 24) -> list[sqlite3.Row]:
        q = normalize_query(query_text)
        query_norm = normalize_match_name(q)
        if not q:
            return []
        rows = self.conn.execute(
            """
            SELECT
                c.*,
                p.nome AS pessoa_nome,
                p.status AS pessoa_status,
                GROUP_CONCAT(DISTINCT ci.valor) AS identificadores_texto
            FROM contribuintes c
            LEFT JOIN pessoas p ON p.id = c.pessoa_id
            LEFT JOIN contribuintes_identificadores ci ON ci.contribuinte_id = c.id AND ci.ativo = 1
            WHERE c.ativo = 1
            GROUP BY c.id
            ORDER BY c.nome
            """
        ).fetchall()
        exact_rows: list[sqlite3.Row] = []
        for row in rows:
            if query_norm and str(row["nome_normalizado"] or "") == query_norm:
                exact_rows.append(row)
                continue
            docs = [str(row["documento_principal"] or "")]
            docs.extend(item.strip() for item in str(row["identificadores_texto"] or "").split(",") if item.strip())
            if any(document_query_matches(q, item) for item in docs if item):
                exact_rows.append(row)
        return exact_rows[:limit]

    def contributor_period_competences(self) -> list[str]:
        rows = self.conn.execute(
            """
            SELECT DISTINCT competencia, competencia_ordem
            FROM contribuicoes
            WHERE ativo = 1 AND competencia IS NOT NULL AND TRIM(competencia) <> ''
            ORDER BY competencia_ordem DESC, competencia DESC
            """
        ).fetchall()
        return [str(row["competencia"]) for row in rows if row["competencia"]]

    def contributor_period_rows(
        self,
        competencia: str = "",
        date_start: str = "",
        date_end: str = "",
        person_ids: list[int] | None = None,
        contributor_name_norms: list[str] | None = None,
        contributor_ids: list[int] | None = None,
    ) -> list[sqlite3.Row]:
        clauses = ["c.ativo = 1"]
        params: list[object] = []
        if competencia:
            clauses.append("c.competencia = ?")
            params.append(competencia)
        if date_start:
            clauses.append("c.data_recebimento >= ?")
            params.append(date_start)
        if date_end:
            clauses.append("c.data_recebimento <= ?")
            params.append(date_end)
        filtered_person_ids = [moneyless_int(item) for item in (person_ids or []) if moneyless_int(item) > 0]
        filtered_name_norms = [str(item) for item in (contributor_name_norms or []) if str(item).strip()]
        filtered_contributor_ids = [moneyless_int(item) for item in (contributor_ids or []) if moneyless_int(item) > 0]
        if filtered_person_ids:
            person_placeholders = ", ".join("?" for _ in filtered_person_ids)
            association_clauses = [f"COALESCE(c.pessoa_id, ct.pessoa_id, 0) IN ({person_placeholders})"]
            params.extend(filtered_person_ids)
            if filtered_name_norms:
                name_placeholders = ", ".join("?" for _ in filtered_name_norms)
                association_clauses.append(
                    f"(COALESCE(c.pessoa_id, ct.pessoa_id, 0) = 0 AND COALESCE(ct.nome_normalizado, '') IN ({name_placeholders}))"
                )
                params.extend(filtered_name_norms)
            clauses.append("(" + " OR ".join(association_clauses) + ")")
        elif filtered_contributor_ids:
            contributor_placeholders = ", ".join("?" for _ in filtered_contributor_ids)
            clauses.append(f"COALESCE(c.contribuinte_id, 0) IN ({contributor_placeholders})")
            params.extend(filtered_contributor_ids)
        return self.conn.execute(
            f"""
            SELECT
                c.id,
                c.data_recebimento,
                c.competencia,
                c.valor,
                c.status_operacional,
                c.pessoa_id,
                c.contribuinte_id,
                COALESCE(p.nome, ct.nome, 'Contribuinte nao identificado') AS contribuinte_nome,
                ct.nome AS contribuinte_nome_original,
                COALESCE(ct.documento_principal, '') AS contribuinte_documento,
                COALESCE(ct.tipo, 'pf') AS contribuinte_tipo,
                COALESCE(c.pessoa_id, ct.pessoa_id, 0) AS pessoa_efetiva_id,
                p.nome AS pessoa_nome,
                p.status AS pessoa_status,
                p.codigo_interno AS pessoa_codigo_interno,
                p.cpf AS pessoa_cpf
            FROM contribuicoes c
            LEFT JOIN contribuintes ct ON ct.id = c.contribuinte_id
            LEFT JOIN pessoas p ON p.id = COALESCE(c.pessoa_id, ct.pessoa_id)
            WHERE {' AND '.join(clauses)}
            ORDER BY UPPER(COALESCE(p.nome, ct.nome, 'Contribuinte nao identificado')), c.data_recebimento, c.id
            """
            ,
            params,
        ).fetchall()

    def get_contribution(self, contribution_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            """
            SELECT *
            FROM contribuicoes
            WHERE id = ?
            """,
            (contribution_id,),
        ).fetchone()

    def create_contribution_from_form(self, form: dict[str, list[str]]) -> int:
        person_id = moneyless_int(form.get("pessoa_id", ["0"])[0])
        person = self.get_person(person_id)
        if person is None:
            raise ValueError("Escolha uma pessoa valida para registrar a contribuicao.")
        organization_id = moneyless_int(person["organizacao_id"])
        contribution_type_id = moneyless_int(form.get("tipo_contribuicao_id", ["0"])[0])
        contribution_type = self.conn.execute(
            """
            SELECT *
            FROM tipos_contribuicao
            WHERE id = ? AND organizacao_id = ? AND ativo = 1
            """,
            (contribution_type_id, organization_id),
        ).fetchone()
        if contribution_type is None:
            raise ValueError("Tipo de contribuicao invalido.")
        receiving_form_id = moneyless_int(form.get("forma_recebimento_id", ["0"])[0])
        receiving_form_db = None
        if receiving_form_id:
            receiving_form_db = self.conn.execute(
                """
                SELECT *
                FROM formas_recebimento
                WHERE id = ? AND organizacao_id = ? AND ativo = 1
                """,
                (receiving_form_id, organization_id),
            ).fetchone()
            if receiving_form_db is None:
                raise ValueError("Forma de recebimento invalida.")
        received_on = first_form_value(form, "data_recebimento")
        competence, competence_order = competencia_from_date(received_on)
        value = parse_money(form.get("valor", [""])[0])
        notes = first_form_value(form, "observacoes")
        contributor_id = self.ensure_person_contributor(person_id, source="lancamento_manual")
        cursor = self.conn.execute(
            """
            INSERT INTO contribuicoes (
                organizacao_id, unidade_id, pessoa_id, contribuinte_id, tipo_contribuicao_id,
                campanha_id, data_recebimento, competencia, competencia_ordem,
                valor, forma_recebimento_id, conta_financeira_id, observacoes, status_operacional,
                ativo, atualizado_em
            ) VALUES (?, NULL, ?, ?, ?, NULL, ?, ?, ?, ?, ?, NULL, ?, 'regular', 1, CURRENT_TIMESTAMP)
            """,
            (
                organization_id,
                person_id,
                contributor_id or None,
                contribution_type_id,
                received_on,
                competence,
                competence_order,
                value,
                receiving_form_id or None,
                notes,
            ),
        )
        contribution_id = moneyless_int(cursor.lastrowid)
        saved = self.get_contribution(contribution_id)
        self.write_audit_log(
            organization_id,
            "lancar_contribuicao",
            "contribuicoes",
            contribution_id,
            None,
            dict(saved) if saved else {"id": contribution_id},
        )
        self.conn.commit()
        return contribution_id

    def person_receipts_summary(self, person_id: int) -> sqlite3.Row:
        return self.conn.execute(
            """
            SELECT
                COUNT(*) AS quantidade,
                COALESCE(SUM(valor_total), 0) AS total,
                MAX(data_emissao) AS ultima_data
            FROM recibos
            WHERE pessoa_id = ? AND status <> 'cancelado'
            """,
            (person_id,),
        ).fetchone()

    def person_receipts(self, person_id: int, limit: int = 8) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT *
            FROM recibos
            WHERE pessoa_id = ? AND status <> 'cancelado'
            ORDER BY data_emissao DESC, id DESC
            LIMIT ?
            """,
            (person_id, limit),
        ).fetchall()

    def receipt_filters(
        self,
        q: str = "",
        person_id: int = 0,
        status: str = "",
        date_start: str = "",
        date_end: str = "",
    ) -> tuple[list[str], list[object]]:
        clauses = ["r.status <> 'cancelado'"]
        params: list[object] = []
        if q:
            if is_numeric_search(q) or is_system_id_search(q) or is_member_code_search(q):
                clause, lookup_params = self.person_lookup_clause(q, table_alias="p")
                if clause:
                    clauses.append(f"({clause} OR r.numero = ?)")
                    params.extend([*lookup_params, normalize_query(q)])
            else:
                like = f"%{q}%"
                clauses.append("(p.nome LIKE ? OR p.cpf LIKE ? OR p.codigo_interno LIKE ? OR r.numero LIKE ?)")
                params.extend([like, like, like, like])
        if person_id:
            clauses.append("r.pessoa_id = ?")
            params.append(person_id)
        if status:
            clauses.append("r.status = ?")
            params.append(status)
        if date_start:
            clauses.append("r.data_emissao >= ?")
            params.append(date_start)
        if date_end:
            clauses.append("r.data_emissao <= ?")
            params.append(date_end)
        return clauses, params

    def receipts_summary(
        self,
        q: str = "",
        person_id: int = 0,
        status: str = "",
        date_start: str = "",
        date_end: str = "",
    ) -> sqlite3.Row:
        clauses, params = self.receipt_filters(q=q, person_id=person_id, status=status, date_start=date_start, date_end=date_end)
        return self.conn.execute(
            f"""
            SELECT
                COUNT(*) AS quantidade,
                COALESCE(SUM(r.valor_total), 0) AS total,
                COUNT(DISTINCT r.pessoa_id) AS pessoas,
                MAX(r.data_emissao) AS ultima_data
            FROM recibos r
            JOIN pessoas p ON p.id = r.pessoa_id
            WHERE {' AND '.join(clauses)}
            """,
            params,
        ).fetchone()

    def list_receipts(
        self,
        q: str = "",
        person_id: int = 0,
        status: str = "",
        date_start: str = "",
        date_end: str = "",
        limit: int = 120,
    ) -> list[sqlite3.Row]:
        clauses, params = self.receipt_filters(q=q, person_id=person_id, status=status, date_start=date_start, date_end=date_end)
        params.append(limit)
        return self.conn.execute(
            f"""
            SELECT
                r.*,
                p.nome AS pessoa_nome,
                p.codigo_interno,
                p.cpf
            FROM recibos r
            JOIN pessoas p ON p.id = r.pessoa_id
            WHERE {' AND '.join(clauses)}
            ORDER BY r.data_emissao DESC, r.id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()

    def next_receipt_number(self, organization_id: int, emission_date: str) -> str:
        digits = "".join(ch for ch in emission_date if ch.isdigit())
        prefix = f"REC-{digits[:6] or date.today().strftime('%Y%m')}"
        row = self.conn.execute(
            """
            SELECT numero
            FROM recibos
            WHERE organizacao_id = ? AND numero LIKE ?
            ORDER BY numero DESC
            LIMIT 1
            """,
            (organization_id, f"{prefix}-%"),
        ).fetchone()
        next_seq = 1
        if row and row["numero"]:
            try:
                next_seq = int(str(row["numero"]).split("-")[-1]) + 1
            except ValueError:
                next_seq = 1
        return f"{prefix}-{next_seq:04d}"

    def eligible_receipt_contributions(
        self,
        person_id: int,
        date_start: str = "",
        date_end: str = "",
        limit: int = 200,
    ) -> list[sqlite3.Row]:
        clauses = ["c.pessoa_id = ?", "c.ativo = 1"]
        params: list[object] = [person_id]
        if date_start:
            clauses.append("c.data_recebimento >= ?")
            params.append(date_start)
        if date_end:
            clauses.append("c.data_recebimento <= ?")
            params.append(date_end)
        params.append(limit)
        return self.conn.execute(
            f"""
            SELECT
                c.*,
                tc.nome AS tipo_nome,
                fr.nome AS forma_nome
            FROM contribuicoes c
            JOIN tipos_contribuicao tc ON tc.id = c.tipo_contribuicao_id
            LEFT JOIN formas_recebimento fr ON fr.id = c.forma_recebimento_id
            WHERE {' AND '.join(clauses)}
              AND NOT EXISTS (
                SELECT 1
                FROM recibo_itens ri
                JOIN recibos r ON r.id = ri.recibo_id
                WHERE ri.contribuicao_id = c.id
                  AND r.status <> 'cancelado'
                  AND r.cancelado_em IS NULL
              )
            ORDER BY c.data_recebimento, c.id
            LIMIT ?
            """,
            params,
        ).fetchall()

    def get_receipt(self, receipt_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            """
            SELECT
                r.*,
                o.nome AS organizacao_nome,
                o.nome_fantasia AS organizacao_fantasia,
                p.nome AS pessoa_nome,
                p.codigo_interno,
                p.cpf
            FROM recibos r
            JOIN pessoas p ON p.id = r.pessoa_id
            JOIN organizacoes o ON o.id = r.organizacao_id
            WHERE r.id = ?
            """,
            (receipt_id,),
        ).fetchone()

    def receipt_items(self, receipt_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT
                ri.*,
                c.data_recebimento,
                c.competencia,
                c.observacoes,
                tc.nome AS tipo_nome,
                fr.nome AS forma_nome
            FROM recibo_itens ri
            JOIN contribuicoes c ON c.id = ri.contribuicao_id
            JOIN tipos_contribuicao tc ON tc.id = c.tipo_contribuicao_id
            LEFT JOIN formas_recebimento fr ON fr.id = c.forma_recebimento_id
            WHERE ri.recibo_id = ?
            ORDER BY c.data_recebimento, c.id
            """,
            (receipt_id,),
        ).fetchall()

    def create_receipt_from_form(self, form: dict[str, list[str]]) -> int:
        person_id = moneyless_int(form.get("pessoa_id", ["0"])[0])
        person = self.get_person(person_id)
        if person is None:
            raise ValueError("Escolha uma pessoa valida para gerar o recibo.")
        contribution_ids = [moneyless_int(value) for value in form.get("contribuicao_id", []) if moneyless_int(value)]
        if not contribution_ids:
            raise ValueError("Selecione pelo menos uma contribuicao para o recibo.")
        organization_id = moneyless_int(person["organizacao_id"])
        placeholders = ",".join("?" for _ in contribution_ids)
        rows = self.conn.execute(
            f"""
            SELECT
                c.*,
                tc.nome AS tipo_nome
            FROM contribuicoes c
            JOIN tipos_contribuicao tc ON tc.id = c.tipo_contribuicao_id
            WHERE c.id IN ({placeholders})
              AND c.pessoa_id = ?
              AND c.ativo = 1
              AND NOT EXISTS (
                SELECT 1
                FROM recibo_itens ri
                JOIN recibos r ON r.id = ri.recibo_id
                WHERE ri.contribuicao_id = c.id
                  AND r.status <> 'cancelado'
                  AND r.cancelado_em IS NULL
              )
            ORDER BY c.data_recebimento, c.id
            """,
            [*contribution_ids, person_id],
        ).fetchall()
        if len(rows) != len(contribution_ids):
            raise ValueError("Uma ou mais contribuicoes ja estao em recibo ativo ou nao pertencem a pessoa selecionada.")
        emission_date = first_form_value(form, "data_emissao", date.today().isoformat())
        receipt_number = self.next_receipt_number(organization_id, emission_date)
        values = [float(row["valor"]) for row in rows]
        total = round(sum(values), 2)
        period_start = min(str(row["data_recebimento"]) for row in rows)
        period_end = max(str(row["data_recebimento"]) for row in rows)
        notes = first_form_value(form, "observacoes")
        cursor = self.conn.execute(
            """
            INSERT INTO recibos (
                organizacao_id, pessoa_id, numero, data_emissao, periodo_inicio, periodo_fim,
                valor_total, status, arquivo_path, observacoes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'emitido', NULL, ?)
            """,
            (
                organization_id,
                person_id,
                receipt_number,
                emission_date,
                period_start,
                period_end,
                total,
                notes,
            ),
        )
        receipt_id = moneyless_int(cursor.lastrowid)
        for row in rows:
            self.conn.execute(
                """
                INSERT INTO recibo_itens (recibo_id, contribuicao_id, valor)
                VALUES (?, ?, ?)
                """,
                (receipt_id, row["id"], row["valor"]),
            )
        saved = self.get_receipt(receipt_id)
        self.write_audit_log(
            organization_id,
            "gerar_recibo",
            "recibos",
            receipt_id,
            None,
            dict(saved) if saved else {"id": receipt_id, "numero": receipt_number},
        )
        self.conn.commit()
        return receipt_id

    def primary_address(self, person_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM pessoa_enderecos WHERE pessoa_id = ? ORDER BY principal DESC, id LIMIT 1",
            (person_id,),
        ).fetchone()

    def person_profiles(self, person_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM pessoa_perfis WHERE pessoa_id = ? AND ativo = 1 ORDER BY perfil",
            (person_id,),
        ).fetchall()

    def person_contacts(self, person_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM pessoa_contatos WHERE pessoa_id = ? ORDER BY principal DESC, tipo, id",
            (person_id,),
        ).fetchall()

    def person_addresses(self, person_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM pessoa_enderecos WHERE pessoa_id = ? ORDER BY principal DESC, id",
            (person_id,),
        ).fetchall()

    def person_history(self, person_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM pessoa_historico WHERE pessoa_id = ? ORDER BY COALESCE(data_evento, criado_em) DESC, id DESC",
            (person_id,),
        ).fetchall()

    def person_custom_fields(self, person_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT
                v.id AS valor_id,
                cp.id AS campo_id,
                cp.nome,
                cp.chave,
                cp.tipo,
                v.valor_texto,
                v.valor_numero,
                v.valor_data,
                v.valor_json
            FROM valores_campos_personalizados v
            JOIN campos_personalizados cp ON cp.id = v.campo_id
            WHERE v.registro_tipo = 'pessoa' AND v.registro_id = ?
            ORDER BY cp.nome
            """,
            (person_id,),
        ).fetchall()

    def person_snapshot(self, person_id: int) -> dict[str, object]:
        person = self.get_person(person_id)
        if person is None:
            return {}
        address = self.primary_address(person_id)
        custom = self.person_custom_fields(person_id)
        photo = find_member_photo(person_id, person["cpf"], person["nome"])
        return {
            "pessoa": dict(person),
            "endereco_principal": dict(address) if address else None,
            "campos_acessorios": {str(row["valor_id"]): custom_value(row) for row in custom},
            "foto_arquivo": photo.name if photo else None,
        }

    def write_audit_log(
        self,
        organizacao_id: int | None,
        action: str,
        table: str,
        record_id: int | None,
        before: dict[str, object] | None,
        after: dict[str, object] | None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO auditoria (
                organizacao_id, usuario_id, acao, tabela, registro_id, dados_antes_json, dados_depois_json
            ) VALUES (?, NULL, ?, ?, ?, ?, ?)
            """,
            (
                organizacao_id,
                action,
                table,
                record_id,
                json.dumps(before, ensure_ascii=False, default=str) if before is not None else None,
                json.dumps(after, ensure_ascii=False, default=str) if after is not None else None,
            ),
        )

    def update_primary_contact(self, organizacao_id: int, person_id: int, tipo: str, value: str) -> None:
        value = normalize_query(value)
        existing = self.conn.execute(
            """
            SELECT id
            FROM pessoa_contatos
            WHERE pessoa_id = ? AND tipo = ? AND principal = 1
            ORDER BY id
            LIMIT 1
            """,
            (person_id, tipo),
        ).fetchone()
        if existing and value:
            self.conn.execute("UPDATE pessoa_contatos SET valor = ? WHERE id = ?", (value, existing["id"]))
        elif value:
            self.conn.execute(
                """
                INSERT INTO pessoa_contatos (organizacao_id, pessoa_id, tipo, valor, principal)
                VALUES (?, ?, ?, ?, 1)
                """,
                (organizacao_id, person_id, tipo, value),
            )

    def update_primary_address(self, organizacao_id: int, person_id: int, values: dict[str, str]) -> None:
        normalized = {key: normalize_query(value) for key, value in values.items()}
        existing = self.primary_address(person_id)
        has_any = any(normalized.values())
        if existing:
            self.conn.execute(
                """
                UPDATE pessoa_enderecos
                SET cep = ?, logradouro = ?, numero = ?, complemento = ?, bairro = ?, cidade = ?, uf = ?,
                    atualizado_em = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    normalized.get("cep", ""),
                    normalized.get("logradouro", ""),
                    normalized.get("numero", ""),
                    normalized.get("complemento", ""),
                    normalized.get("bairro", ""),
                    normalized.get("cidade", ""),
                    normalized.get("uf", ""),
                    existing["id"],
                ),
            )
        elif has_any:
            self.conn.execute(
                """
                INSERT INTO pessoa_enderecos (
                    organizacao_id, pessoa_id, tipo, cep, logradouro, numero, complemento, bairro, cidade, uf, principal
                ) VALUES (?, ?, 'residencial', ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    organizacao_id,
                    person_id,
                    normalized.get("cep", ""),
                    normalized.get("logradouro", ""),
                    normalized.get("numero", ""),
                    normalized.get("complemento", ""),
                    normalized.get("bairro", ""),
                    normalized.get("cidade", ""),
                    normalized.get("uf", ""),
                ),
            )

    def ensure_member_profile(self, organizacao_id: int, person_id: int, status: str) -> None:
        if not str(status or "").startswith("membro"):
            return
        existing = self.conn.execute(
            """
            SELECT id
            FROM pessoa_perfis
            WHERE pessoa_id = ? AND perfil = 'membro' AND ativo = 1
            LIMIT 1
            """,
            (person_id,),
        ).fetchone()
        if existing is None:
            self.conn.execute(
                """
                INSERT INTO pessoa_perfis (organizacao_id, pessoa_id, perfil, ativo)
                VALUES (?, ?, 'membro', 1)
                """,
                (organizacao_id, person_id),
            )

    def create_person_from_form(self, form: dict[str, list[str]]) -> int:
        name = first_form_value(form, "nome")
        if not name:
            raise ValueError("Nome e obrigatorio.")
        organizacao_id = self.default_organization_id()
        status = first_form_value(form, "status", "frequentador")
        cpf_value = clean_cpf(first_form_value(form, "cpf"))
        cpf_db = cpf_value or None
        requested_member_code = first_form_value(form, "codigo_interno")
        member_code = (
            self.resolved_member_code(organizacao_id, requested_member_code)
            if status_grants_member_code(status)
            else ""
        )
        arquivo_morto = 1 if status == "arquivo_morto" else 0
        cursor = self.conn.execute(
            """
            INSERT INTO pessoas (
                organizacao_id, unidade_preferencial_id, codigo_interno, nome, cpf, rg, data_nascimento,
                sexo, estado_civil, email_principal, telefone_principal, whatsapp_principal, status,
                arquivo_morto, observacoes, import_lote_id, ativo, atualizado_em
            ) VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 1, CURRENT_TIMESTAMP)
            """,
            (
                organizacao_id,
                member_code,
                name,
                cpf_db,
                first_form_value(form, "rg"),
                first_form_value(form, "data_nascimento"),
                first_form_value(form, "sexo"),
                first_form_value(form, "estado_civil"),
                first_form_value(form, "email_principal"),
                first_form_value(form, "telefone_principal"),
                first_form_value(form, "whatsapp_principal"),
                status,
                arquivo_morto,
                first_form_value(form, "observacoes"),
            ),
        )
        person_id = moneyless_int(cursor.lastrowid)
        self.update_primary_contact(organizacao_id, person_id, "email", first_form_value(form, "email_principal"))
        self.update_primary_contact(organizacao_id, person_id, "telefone", first_form_value(form, "telefone_principal"))
        self.update_primary_contact(organizacao_id, person_id, "whatsapp", first_form_value(form, "whatsapp_principal"))
        self.update_primary_address(
            organizacao_id,
            person_id,
            {
                "cep": first_form_value(form, "cep"),
                "logradouro": first_form_value(form, "logradouro"),
                "numero": first_form_value(form, "numero"),
                "complemento": first_form_value(form, "complemento"),
                "bairro": first_form_value(form, "bairro"),
                "cidade": first_form_value(form, "cidade"),
                "uf": first_form_value(form, "uf"),
            },
        )
        self.ensure_member_profile(organizacao_id, person_id, status)
        self.reconcile_contributors_for_person(person_id, source="criacao_de_pessoa")
        saved = self.person_snapshot(person_id)
        self.write_audit_log(organizacao_id, "criar_cadastro", "pessoas", person_id, None, saved)
        self.conn.commit()
        return person_id

    def create_frequentador_from_contributor(self, contributor_id: int, family_person_id: int = 0) -> int:
        contributor = self.get_contributor(contributor_id)
        if contributor is None:
            raise ValueError("Contribuinte nao encontrado.")
        if moneyless_int(contributor["pessoa_id"]):
            raise ValueError("Este contribuinte ja esta vinculado a uma pessoa.")
        organizacao_id = moneyless_int(contributor["organizacao_id"])
        contributor_name = normalize_query(contributor["nome"])
        if not contributor_name:
            raise ValueError("O contribuinte nao tem nome suficiente para criar uma ficha.")

        family_person = None
        if family_person_id:
            family_person = self.get_person(family_person_id)
            if family_person is None:
                raise ValueError("A pessoa de referencia familiar nao foi encontrada.")
            if moneyless_int(family_person["organizacao_id"]) != organizacao_id:
                raise ValueError("A referencia familiar pertence a outra organizacao.")

        document_value = normalize_query(contributor["documento_principal"])
        document_type = normalize_query(contributor["documento_tipo"]).lower()
        document_digits = "".join(ch for ch in document_value if ch.isdigit())
        cpf_db = None
        if document_type == "cpf" and "*" not in document_value and len(document_digits) == 11:
            existing_person = self.conn.execute(
                """
                SELECT id, nome
                FROM pessoas
                WHERE organizacao_id = ? AND cpf = ? AND ativo = 1
                LIMIT 1
                """,
                (organizacao_id, document_digits),
            ).fetchone()
            if existing_person is not None:
                raise ValueError(
                    f"Ja existe uma pessoa com esse CPF: {existing_person['nome']}. Use o vinculo com a ficha existente."
                )
            cpf_db = document_digits

        notes = [
            f"Criado a partir do contribuinte auxiliar #{contributor_id}: {contributor_name}.",
            f"Origem financeira preservada: {contributor_name} | {document_value or 'sem documento principal'}.",
        ]
        if contributor["tipo"] == "pj":
            notes.append("A identidade financeira original estava classificada como PJ / empresa.")
        if family_person is not None:
            notes.append(
                "Criado pela fila de integracao familiar com referencia a "
                f"{family_person['nome']} ({format_system_id(family_person['id'])})."
            )

        cursor = self.conn.execute(
            """
            INSERT INTO pessoas (
                organizacao_id, unidade_preferencial_id, codigo_interno, nome, cpf, rg, data_nascimento,
                sexo, estado_civil, email_principal, telefone_principal, whatsapp_principal, status,
                arquivo_morto, observacoes, import_lote_id, ativo, atualizado_em
            ) VALUES (?, NULL, '', ?, ?, '', '', '', '', '', '', '', 'frequentador', 0, ?, NULL, 1, CURRENT_TIMESTAMP)
            """,
            (
                organizacao_id,
                contributor_name,
                cpf_db,
                " ".join(part for part in notes if normalize_query(part)),
            ),
        )
        person_id = moneyless_int(cursor.lastrowid)
        snapshot = self.person_snapshot(person_id)
        snapshot["criado_do_contribuinte_id"] = contributor_id
        if family_person is not None:
            snapshot["referencia_familiar_id"] = family_person_id
            snapshot["referencia_familiar_nome"] = family_person["nome"]
        self.write_audit_log(
            organizacao_id,
            "criar_frequentador_por_contribuinte",
            "pessoas",
            person_id,
            None,
            snapshot,
        )
        link_note = "Frequentador criado automaticamente a partir do contribuinte auxiliar."
        if family_person is not None:
            link_note += f" Referencia familiar: {family_person['nome']}."
        self.link_contributor_to_person(contributor_id, person_id, note=link_note, commit=False)
        self.conn.commit()
        return person_id

    def update_person_from_form(self, person_id: int, form: dict[str, list[str]]) -> None:
        person = self.get_person(person_id)
        if person is None:
            raise ValueError("Pessoa nao encontrada.")
        if not first_form_value(form, "nome"):
            raise ValueError("Nome e obrigatorio.")
        old_cpf = person["cpf"]
        old_name = person["nome"]
        before = self.person_snapshot(person_id)
        organizacao_id = moneyless_int(person["organizacao_id"])
        cpf_value = clean_cpf(first_form_value(form, "cpf"))
        cpf_db = cpf_value or None
        new_name = first_form_value(form, "nome")
        status = first_form_value(form, "status", person["status"] or "frequentador")
        requested_member_code = first_form_value(form, "codigo_interno")
        allow_member_code_edit = first_form_value(form, "allow_member_code_edit") == "1"
        current_member_code = clean_member_code(person["codigo_interno"])
        if current_member_code:
            if allow_member_code_edit:
                member_code = self.resolved_member_code(
                    organizacao_id,
                    requested_member_code or current_member_code,
                    ignore_person_id=person_id,
                )
            else:
                member_code = current_member_code
        elif status_grants_member_code(status):
            member_code = self.resolved_member_code(
                organizacao_id,
                requested_member_code if allow_member_code_edit else "",
                ignore_person_id=person_id,
            )
        else:
            member_code = ""
        arquivo_morto = 1 if status == "arquivo_morto" else 0
        self.conn.execute(
            """
            UPDATE pessoas
            SET codigo_interno = ?, nome = ?, cpf = ?, rg = ?, data_nascimento = ?, sexo = ?,
                estado_civil = ?, email_principal = ?, telefone_principal = ?, whatsapp_principal = ?,
                status = ?, arquivo_morto = ?, observacoes = ?, atualizado_em = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                member_code,
                new_name,
                cpf_db,
                first_form_value(form, "rg"),
                first_form_value(form, "data_nascimento"),
                first_form_value(form, "sexo"),
                first_form_value(form, "estado_civil"),
                first_form_value(form, "email_principal"),
                first_form_value(form, "telefone_principal"),
                first_form_value(form, "whatsapp_principal"),
                status,
                arquivo_morto,
                first_form_value(form, "observacoes"),
                person_id,
            ),
        )
        self.update_primary_contact(organizacao_id, person_id, "email", first_form_value(form, "email_principal"))
        self.update_primary_contact(organizacao_id, person_id, "telefone", first_form_value(form, "telefone_principal"))
        self.update_primary_contact(organizacao_id, person_id, "whatsapp", first_form_value(form, "whatsapp_principal"))
        self.update_primary_address(
            organizacao_id,
            person_id,
            {
                "cep": first_form_value(form, "cep"),
                "logradouro": first_form_value(form, "logradouro"),
                "numero": first_form_value(form, "numero"),
                "complemento": first_form_value(form, "complemento"),
                "bairro": first_form_value(form, "bairro"),
                "cidade": first_form_value(form, "cidade"),
                "uf": first_form_value(form, "uf"),
            },
        )
        self.ensure_member_profile(organizacao_id, person_id, status)
        self.reconcile_contributors_for_person(person_id, source="atualizacao_de_pessoa")
        for key, values in form.items():
            if not key.startswith("custom_"):
                continue
            custom_value_id = moneyless_int(key.replace("custom_", ""))
            self.conn.execute(
                """
                UPDATE valores_campos_personalizados
                SET valor_texto = ?, valor_numero = NULL, valor_data = NULL, valor_json = NULL,
                    atualizado_em = CURRENT_TIMESTAMP
                WHERE id = ? AND registro_tipo = 'pessoa' AND registro_id = ?
                """,
                (normalize_query(values[0] if values else ""), custom_value_id, person_id),
            )
        for pending_id_text in form.get("resolver_pendencia", []):
            self.resolve_pending(
                moneyless_int(pending_id_text),
                "Resolvido junto com a revisao cadastral.",
                commit=False,
            )
        rename_member_photo_files(person_id, old_cpf, cpf_db, old_name, new_name)
        after = self.person_snapshot(person_id)
        self.write_audit_log(organizacao_id, "atualizar_cadastro", "pessoas", person_id, before, after)
        self.conn.commit()

    def bulk_update_people_status(self, person_ids: list[int], status: str) -> int:
        normalized_status = normalize_query(status)
        allowed_statuses = {"membro_ativo", "membro_inativo", "frequentador", "visitante", "arquivo_morto"}
        if normalized_status not in allowed_statuses:
            raise ValueError("Escolha um status valido para a atualizacao em lote.")

        unique_ids: list[int] = []
        seen: set[int] = set()
        for raw_id in person_ids:
            person_id = moneyless_int(raw_id)
            if not person_id or person_id in seen:
                continue
            seen.add(person_id)
            unique_ids.append(person_id)
        if not unique_ids:
            raise ValueError("Selecione pelo menos uma ficha para atualizar em lote.")

        updated = 0
        for person_id in unique_ids:
            person = self.get_person(person_id)
            if person is None:
                continue
            before = self.person_snapshot(person_id)
            organizacao_id = moneyless_int(person["organizacao_id"])
            current_member_code = clean_member_code(person["codigo_interno"])
            if current_member_code:
                member_code = current_member_code
            elif status_grants_member_code(normalized_status):
                member_code = self.resolved_member_code(organizacao_id, ignore_person_id=person_id)
            else:
                member_code = ""
            arquivo_morto = 1 if normalized_status == "arquivo_morto" else 0
            self.conn.execute(
                """
                UPDATE pessoas
                SET codigo_interno = ?, status = ?, arquivo_morto = ?, atualizado_em = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (member_code, normalized_status, arquivo_morto, person_id),
            )
            self.ensure_member_profile(organizacao_id, person_id, normalized_status)
            after = self.person_snapshot(person_id)
            self.write_audit_log(
                organizacao_id,
                "atualizar_status_em_lote",
                "pessoas",
                person_id,
                before,
                after | {"status_lote": normalized_status},
            )
            updated += 1
        self.conn.commit()
        return updated

    def resolve_pending(self, pending_id: int, resolution: str, commit: bool = True) -> None:
        pending = self.conn.execute(
            """
            SELECT ip.*, il.organizacao_id
            FROM import_pendencias ip
            JOIN import_lotes il ON il.id = ip.lote_id
            WHERE ip.id = ?
            """,
            (pending_id,),
        ).fetchone()
        if pending is None:
            raise ValueError("Pendencia nao encontrada.")
        before = dict(pending)
        self.conn.execute(
            """
            UPDATE import_pendencias
            SET resolvido = 1, resolvido_em = CURRENT_TIMESTAMP, resolucao = ?
            WHERE id = ?
            """,
            (normalize_query(resolution) or "Resolvido por revisao manual.", pending_id),
        )
        after_row = self.conn.execute("SELECT * FROM import_pendencias WHERE id = ?", (pending_id,)).fetchone()
        self.write_audit_log(
            moneyless_int(pending["organizacao_id"]) if pending["organizacao_id"] else None,
            "resolver_pendencia_importacao",
            "import_pendencias",
            pending_id,
            before,
            dict(after_row) if after_row else None,
        )
        if commit:
            self.conn.commit()

    def duplicate_member_number_items(self) -> list[dict[str, object]]:
        rows = self.conn.execute(
            """
            SELECT id, organizacao_id, nome, codigo_interno, status
            FROM pessoas
            WHERE ativo = 1 AND codigo_interno IS NOT NULL AND TRIM(codigo_interno) <> ''
            ORDER BY organizacao_id, codigo_interno, nome, id
            """
        ).fetchall()
        groups: dict[tuple[int, str], list[sqlite3.Row]] = {}
        for row in rows:
            key = (moneyless_int(row["organizacao_id"]), str(row["codigo_interno"]))
            groups.setdefault(key, []).append(row)
        items: list[dict[str, object]] = []
        for (_org_id, codigo_interno), people in groups.items():
            if len(people) <= 1:
                continue
            for person in people:
                others = [f"{row['nome']} ({format_system_id(row['id'])})" for row in people if moneyless_int(row["id"]) != moneyless_int(person["id"])]
                description = (
                    f"Numero de membro {format_member_code(codigo_interno)} repetido em {len(people)} ficha(s): "
                    + ", ".join(others)
                )
                items.append(
                    {
                        "id": f"dup_numero_membro_{person['id']}",
                        "tipo": "numero_membro_duplicado",
                        "severidade": "aviso",
                        "descricao": description,
                        "acao_sugerida": "Na importacao, preserve o numero original como referencia historica e defina um numero operacional exclusivo so para quem ja for membro. Para visitante e frequentador, deixe sem numero de membro ate a mudanca de status, inclusive nas promocoes em lote feitas depois da assembleia. Enquanto isso, use ID do sistema ou CPF nas contribuicoes.",
                        "numero_linha": "cadastro atual",
                        "pessoa_id": moneyless_int(person["id"]),
                        "nome": str(person["nome"]),
                        "codigo_interno": str(person["codigo_interno"]),
                        "status": str(person["status"]),
                        "resolvivel": False,
                        "origem": "cadastro",
                    }
                )
        return items

    def duplicate_member_number_count_by_person(self) -> dict[int, int]:
        counts: dict[int, int] = {}
        for item in self.duplicate_member_number_items():
            person_id = moneyless_int(item["pessoa_id"])
            counts[person_id] = counts.get(person_id, 0) + 1
        return counts

    def audit_count_by_person(self) -> dict[int, int]:
        counts: dict[int, int] = {}
        rows = self.conn.execute(
            """
            SELECT il.registro_id AS pessoa_id, COUNT(*) AS quantidade
            FROM import_pendencias ip
            JOIN import_linhas il ON il.id = ip.linha_id
            WHERE ip.resolvido = 0
              AND il.registro_tipo = 'pessoa'
              AND il.registro_id IS NOT NULL
            GROUP BY il.registro_id
            """
        ).fetchall()
        for row in rows:
            person_id = moneyless_int(row["pessoa_id"])
            if person_id:
                counts[person_id] = counts.get(person_id, 0) + moneyless_int(row["quantidade"])
        for person_id, quantity in self.duplicate_member_number_count_by_person().items():
            counts[person_id] = counts.get(person_id, 0) + moneyless_int(quantity)
        return counts

    def import_audit_items(self, tipo: str = "", severidade: str = "") -> list[dict[str, object]]:
        clauses = ["ip.resolvido = 0"]
        params: list[object] = []
        if tipo:
            clauses.append("ip.tipo = ?")
            params.append(tipo)
        if severidade:
            clauses.append("ip.severidade = ?")
            params.append(severidade)
        rows = self.conn.execute(
            f"""
            SELECT ip.*, il.numero_linha, p.id AS pessoa_id, p.nome, p.codigo_interno, p.status
            FROM import_pendencias ip
            JOIN import_linhas il ON il.id = ip.linha_id
            LEFT JOIN pessoas p ON p.id = il.registro_id AND il.registro_tipo = 'pessoa'
            WHERE {' AND '.join(clauses)}
            ORDER BY CASE ip.severidade WHEN 'aviso' THEN 0 ELSE 1 END, ip.tipo, p.nome
            """,
            params,
        ).fetchall()
        items: list[dict[str, object]] = []
        for row in rows:
            data = dict(row)
            data["resolvivel"] = True
            data["origem"] = "importacao"
            items.append(data)
        return items

    def audit_items(self, tipo: str = "", severidade: str = "") -> list[dict[str, object]]:
        items = self.import_audit_items(tipo=tipo, severidade=severidade)
        duplicate_items = self.duplicate_member_number_items()
        for item in duplicate_items:
            if tipo and item["tipo"] != tipo:
                continue
            if severidade and item["severidade"] != severidade:
                continue
            items.append(item)
        severity_rank = {"aviso": 0, "info": 1}
        return sorted(
            items,
            key=lambda item: (
                severity_rank.get(str(item.get("severidade", "")), 9),
                str(item.get("tipo", "")),
                str(item.get("nome", "")),
                str(item.get("id", "")),
            ),
        )

    def person_audit(self, person_id: int) -> list[dict[str, object]]:
        return [item for item in self.audit_items() if moneyless_int(item.get("pessoa_id")) == person_id]

    def audit_summary(self) -> list[dict[str, object]]:
        grouped: dict[tuple[str, str], int] = {}
        for item in self.audit_items():
            key = (str(item.get("tipo", "")), str(item.get("severidade", "")))
            grouped[key] = grouped.get(key, 0) + 1
        severity_rank = {"aviso": 0, "info": 1}
        rows = [
            {"tipo": tipo, "severidade": severidade, "quantidade": quantidade}
            for (tipo, severidade), quantidade in grouped.items()
        ]
        return sorted(rows, key=lambda row: (severity_rank.get(str(row["severidade"]), 9), -moneyless_int(row["quantidade"]), str(row["tipo"])))

    def audit_rows(self, tipo: str = "", severidade: str = "", limit: int = 200) -> list[dict[str, object]]:
        return self.audit_items(tipo=tipo, severidade=severidade)[:limit]

    def audit_people(self, tipo: str = "", severidade: str = "", limit: int = 120) -> list[dict[str, object]]:
        grouped: dict[int, dict[str, object]] = {}
        for item in self.audit_items(tipo=tipo, severidade=severidade):
            person_id = moneyless_int(item.get("pessoa_id"))
            if not person_id:
                continue
            bucket = grouped.setdefault(
                person_id,
                {
                    "pessoa_id": person_id,
                    "nome": item.get("nome", ""),
                    "codigo_interno": item.get("codigo_interno", ""),
                    "status": item.get("status", ""),
                    "avisos": 0,
                    "infos": 0,
                    "total": 0,
                    "tipos": set(),
                },
            )
            if item.get("severidade") == "aviso":
                bucket["avisos"] = moneyless_int(bucket["avisos"]) + 1
            else:
                bucket["infos"] = moneyless_int(bucket["infos"]) + 1
            bucket["total"] = moneyless_int(bucket["total"]) + 1
            bucket["tipos"].add(str(item.get("tipo", "")))
        rows = []
        for bucket in grouped.values():
            rows.append(
                {
                    "pessoa_id": bucket["pessoa_id"],
                    "nome": bucket["nome"],
                    "codigo_interno": bucket["codigo_interno"],
                    "status": bucket["status"],
                    "avisos": bucket["avisos"],
                    "infos": bucket["infos"],
                    "total": bucket["total"],
                    "tipos": ", ".join(sorted(bucket["tipos"])),
                }
            )
        rows.sort(key=lambda row: (-moneyless_int(row["avisos"]), -moneyless_int(row["total"]), str(row["nome"])))
        return rows[:limit]


def badge(text: object, class_name: str = "") -> str:
    return f"<span class='badge {class_name}'>{h(text)}</span>"


def message_box(query: dict[str, list[str]]) -> str:
    message = normalize_query(query.get("msg", [""])[0])
    if not message:
        return ""
    is_error = query.get("error", ["0"])[0] == "1"
    return f"<div class='panel'>{badge('erro' if is_error else 'ok', 'danger' if is_error else 'ok')} {h(message)}</div>"


def option(value: str, label: str, current: object) -> str:
    selected = "selected" if str(current or "") == value else ""
    return f"<option value='{h(value)}' {selected}>{h(label)}</option>"


def contributor_create_frequentador_form(
    contributor_id: int,
    return_to: str,
    family_person_id: int = 0,
    label: str = "Criar frequentador",
    css_class: str = "button small",
) -> str:
    family_input = (
        f"<input type='hidden' name='family_person_id' value='{moneyless_int(family_person_id)}'>"
        if moneyless_int(family_person_id)
        else ""
    )
    return (
        "<form method='post' action='/contribuinte/criar-frequentador' style='display:inline-block'>"
        f"<input type='hidden' name='contributor_id' value='{moneyless_int(contributor_id)}'>"
        f"{family_input}"
        f"<input type='hidden' name='return_to' value='{h(return_to)}'>"
        f"<button class='{h(css_class)}' type='submit'>{h(label)}</button>"
        "</form>"
    )


def pix_confidence_badge(value: object) -> str:
    mapping = {
        "forte_doc_nome": ("Documento + nome", "ok"),
        "forte_doc": ("Documento", "ok"),
        "forte_nome": ("Nome exato", "ok"),
        "provavel_doc_amb_nome": ("Nome confirmou doc", "warn"),
        "provavel_nome": ("Nome proximo", "warn"),
        "pj_ou_externo": ("PJ / externo", "info"),
        "ambiguo": ("Ambiguo", "danger"),
        "conflito_doc_nome": ("Conflito doc/nome", "danger"),
        "sem_match": ("Sem match", "danger"),
    }
    label, css_class = mapping.get(str(value or ""), (normalize_query(value) or "Sem classificar", "info"))
    return badge(label, css_class)


def audit_candidate_source_badges(source_value: object) -> str:
    raw = normalize_query(source_value)
    if not raw:
        return badge("Manual", "info")
    tokens: list[str] = []
    for part in raw.split("+"):
        token = normalize_query(part)
        if token and token not in tokens:
            tokens.append(token)
    mapping = {
        "busca": ("Busca ampla", "warn"),
        "motor": ("Motor", "ok"),
        "identidade": ("Identidade financeira", "info"),
        "sugerido": ("Ja sugerido", "ok"),
    }
    badges = []
    for token in tokens:
        label, css = mapping.get(token, (token.replace("_", " ") or "Origem", "info"))
        badges.append(badge(label, css))
    return "".join(badges) if badges else badge("Manual", "info")


def pix_review_status_badge(value: object) -> str:
    mapping = {
        "revisar_pessoa": ("Saneamento: pessoa", "danger"),
        "revisar_destinacao": ("Saneamento: destinacao", "warn"),
        "revisar_duplicidade": ("Saneamento: duplicidade", "danger"),
        "pronto": ("Regular", "ok"),
        "aprovado": ("Regularizado", "ok"),
        "importado": ("Regular", "info"),
        "ignorado": ("Ignorado", "warn"),
    }
    label, css_class = mapping.get(str(value or ""), (normalize_query(value) or "Pendente", "info"))
    return badge(label, css_class)


def contribution_operational_status_badge(value: object) -> str:
    mapping = {
        "regular": ("Regular", "ok"),
        "sem_associacao": ("Sem associacao", "danger"),
        "classificacao_pendente": ("Classificacao pendente", "warn"),
        "duplicidade_suspeita": ("Duplicidade suspeita", "danger"),
        "ignorado": ("Ignorado", "warn"),
    }
    label, css_class = mapping.get(str(value or ""), (normalize_query(value) or "Em saneamento", "info"))
    return badge(label, css_class)


def contributor_recurrence_flags(row: object) -> dict[str, object]:
    qty = moneyless_int(getattr(row, "get", lambda _key, _default=None: None)("contribuicoes_qtd", 0))
    if not qty:
        qty = moneyless_int(getattr(row, "get", lambda _key, _default=None: None)("quantidade", 0))
    weeks = moneyless_int(getattr(row, "get", lambda _key, _default=None: None)("semanas_qtd", 0))
    competencias = moneyless_int(getattr(row, "get", lambda _key, _default=None: None)("competencias_qtd", 0))
    if not competencias:
        competencias = moneyless_int(getattr(row, "get", lambda _key, _default=None: None)("competencias", 0))
    months = moneyless_int(getattr(row, "get", lambda _key, _default=None: None)("meses_recebimento_qtd", 0))
    person_id = moneyless_int(getattr(row, "get", lambda _key, _default=None: None)("pessoa_id", 0))
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


def build_contributor_family_groups(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    candidates = [row for row in rows if moneyless_int(row.get("sugestao_integracao"))]
    if len(candidates) < 2:
        return []
    groups: list[dict[str, object]] = []
    used_ids: set[int] = set()
    nuclear_map: dict[str, list[dict[str, object]]] = {}
    for row in candidates:
        keys = contributor_family_keys(row.get("nome"))
        nuclear = keys.get("nuclear", "")
        if nuclear:
            nuclear_map.setdefault(nuclear, []).append(row)
    for key, members in sorted(nuclear_map.items(), key=lambda item: (-len(item[1]), item[0])):
        unique_members = []
        seen_ids: set[int] = set()
        for member in members:
            contributor_id = moneyless_int(member.get("id"))
            if contributor_id in seen_ids:
                continue
            seen_ids.add(contributor_id)
            unique_members.append(member)
        if len(unique_members) < 2:
            continue
        groups.append({"scope": "nuclear", "label": key.title(), "members": unique_members})
        used_ids.update(moneyless_int(member.get("id")) for member in unique_members)
    broad_map: dict[str, list[dict[str, object]]] = {}
    for row in candidates:
        contributor_id = moneyless_int(row.get("id"))
        if contributor_id in used_ids:
            continue
        keys = contributor_family_keys(row.get("nome"))
        broad = keys.get("broad", "")
        if broad:
            broad_map.setdefault(broad, []).append(row)
    for key, members in sorted(broad_map.items(), key=lambda item: (-len(item[1]), item[0])):
        unique_members = []
        seen_ids: set[int] = set()
        for member in members:
            contributor_id = moneyless_int(member.get("id"))
            if contributor_id in seen_ids:
                continue
            seen_ids.add(contributor_id)
            unique_members.append(member)
        if len(unique_members) < 2:
            continue
        groups.append({"scope": "ampliada", "label": key.title(), "members": unique_members})
    groups.sort(key=lambda item: (-len(item["members"]), 0 if item["scope"] == "nuclear" else 1, str(item["label"])))
    return groups


def build_contributor_family_links(
    contributors: list[dict[str, object]],
    people_rows: list[dict[str, object]],
    limit_people: int = 6,
) -> list[dict[str, object]]:
    blocks: list[dict[str, object]] = []
    for contributor in contributors:
        if not moneyless_int(contributor.get("sugestao_integracao")):
            continue
        keys = contributor_family_keys(contributor.get("nome"))
        if not keys.get("broad") and not keys.get("nuclear"):
            continue
        matches: list[dict[str, object]] = []
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
                    "nome": str(person.get("nome") or ""),
                    "status": str(person.get("status") or ""),
                    "codigo_interno": str(person.get("codigo_interno") or ""),
                    "cpf": str(person.get("cpf") or ""),
                    "relation": relation,
                }
            )
        if not matches:
            continue
        matches.sort(key=lambda item: (0 if item["relation"] == "nuclear" else 1, str(item["nome"])))
        recurrence = contributor_recurrence_flags(contributor)
        relation_strength = 2 if any(item["relation"] == "nuclear" for item in matches) else 1
        member_strength = max(
            (
                3 if str(item["status"]) == "membro_ativo"
                else 2 if str(item["status"]) == "membro_inativo"
                else 1 if str(item["status"]) in {"frequentador", "visitante"}
                else 0
            )
            for item in matches
        )
        blocks.append(
            {
                "contributor": contributor,
                "matches": matches[:limit_people],
                "ranking": {
                    "relation_strength": relation_strength,
                    "member_strength": member_strength,
                    "integration_priority": moneyless_int(contributor.get("prioridade_integracao")),
                    "weeks": moneyless_int(recurrence["weeks"]),
                    "competencias": moneyless_int(recurrence["competencias"]),
                    "total": float(contributor.get("total_contribuido") or 0),
                    "matches_count": len(matches),
                },
            }
        )
    blocks.sort(
        key=lambda item: (
            -moneyless_int(item["ranking"].get("relation_strength")),
            -moneyless_int(item["ranking"].get("member_strength")),
            -moneyless_int(item["ranking"].get("integration_priority")),
            -moneyless_int(item["ranking"].get("competencias")),
            -moneyless_int(item["ranking"].get("weeks")),
            -float(item["ranking"].get("total") or 0),
            -moneyless_int(item["ranking"].get("matches_count")),
            str(item["contributor"].get("nome")),
        )
    )
    return blocks


def build_contributors_dashboard_data(
    db: PowerChurchDB,
    q: str = "",
    mode: str = "todos",
    tags: list[str] | tuple[str, ...] | set[str] | None = None,
    limit: int = 10000,
) -> dict[str, object]:
    rows = db.list_contributors(q=q, mode=mode, tags=tags, limit=limit)
    family_groups = build_contributor_family_groups(rows)
    people_rows = [
        dict(row)
        for row in db.conn.execute(
            """
            SELECT id, nome, status, codigo_interno, cpf
            FROM pessoas
            WHERE ativo = 1
            ORDER BY nome
            """
        ).fetchall()
    ]
    family_links = build_contributor_family_links(rows, people_rows)
    return {
        "rows": rows,
        "family_groups": family_groups,
        "family_links": family_links,
        "people_rows": people_rows,
    }


def contributor_report_query_string(
    mode: str = "todos",
    q: str = "",
    tags: list[str] | tuple[str, ...] | set[str] | None = None,
    section: str = "",
    competencia: str = "",
    date_start: str = "",
    date_end: str = "",
    person_query: str = "",
) -> str:
    params: list[tuple[str, str]] = []
    mode_value = normalize_query(mode) or "todos"
    if mode_value != "todos":
        params.append(("mode", mode_value))
    if normalize_query(q):
        params.append(("q", normalize_query(q)))
    for tag in tags or []:
        tag_value = normalize_query(tag).lower()
        if tag_value:
            params.append(("tag", tag_value))
    if normalize_query(section):
        params.append(("section", normalize_query(section).lower()))
    if normalize_query(competencia):
        params.append(("competencia", normalize_query(competencia)))
    if normalize_query(date_start):
        params.append(("date_start", normalize_query(date_start)))
    if normalize_query(date_end):
        params.append(("date_end", normalize_query(date_end)))
    if normalize_query(person_query):
        params.append(("person_query", normalize_query(person_query)))
    return urllib.parse.urlencode(params, doseq=True)


def pix_name_similarity_ratio(donor_name: object, person_name: object) -> float:
    donor_norm = normalize_match_name(donor_name)
    person_norm = normalize_match_name(person_name)
    if not donor_norm or not person_norm:
        return 0.0
    return round(SequenceMatcher(None, donor_norm, person_norm).ratio(), 4)


def pix_candidate_similarity(doc_match: object, exact_name: object, ratio: object) -> tuple[str, str, str]:
    doc_ok = bool(doc_match)
    exact_ok = bool(exact_name)
    ratio_value = float(ratio or 0.0)
    if doc_ok and exact_ok:
        return ("Documento + nome exato", "ok", "aderente")
    if exact_ok:
        return ("Nome exato", "ok", "aderente")
    if doc_ok and ratio_value >= 0.93:
        return ("Documento + nome parecido", "ok", "aderente")
    if ratio_value >= 0.97:
        return (f"Nome muito proximo ({ratio_value:.2f})", "ok", "aderente")
    if ratio_value >= 0.93:
        return (f"Nome proximo ({ratio_value:.2f})", "warn", "aderente")
    if doc_ok and ratio_value >= 0.86:
        return ("Documento + nome parcial", "warn", "parcial")
    if doc_ok:
        return ("Documento compativel", "warn", "parcial")
    if ratio_value >= 0.86:
        return (f"Semelhanca parcial ({ratio_value:.2f})", "warn", "parcial")
    return ("Sem semelhanca clara", "danger", "distante")


def pix_candidate_similarity_badge(doc_match: object, exact_name: object, ratio: object) -> str:
    label, css_class, _bucket = pix_candidate_similarity(doc_match, exact_name, ratio)
    return badge(label, css_class)


def pix_name_has_company_hint(value: object) -> bool:
    return core_matching.pix_name_has_company_hint(value, PIX_COMPANY_HINTS)


def pix_origin_is_company(document_type: object, donor_name: object) -> bool:
    return core_matching.pix_origin_is_company(document_type, donor_name, PIX_COMPANY_HINTS)


def looks_like_cnpj(value: object) -> bool:
    return core_contributors.looks_like_cnpj(value)


def contributor_kind_for_identity(
    name: object,
    document_type: object = "",
    document_value: object = "",
    identifier_pairs: list[tuple[str, str]] | None = None,
) -> str:
    return core_contributors.contributor_kind_for_identity(
        name,
        document_type,
        document_value,
        identifier_pairs,
        company_name_detector=pix_name_has_company_hint,
    )


def pix_lot_status_badge(value: object) -> str:
    mapping = {
        "carregado": ("Carregado", "info"),
        "auditando": ("Em saneamento", "warn"),
        "pronto_importacao": ("Sem financeiro", "warn"),
        "parcial": ("Financeiro em saneamento", "warn"),
        "concluido": ("Concluido", "ok"),
        "encerrado": ("Encerrado", "info"),
    }
    label, css_class = mapping.get(str(value or ""), (normalize_query(value) or "Lote", "info"))
    return badge(label, css_class)


def statement_movement_kind_badge(value: object) -> str:
    mapping = {
        "pix": ("PIX", "info"),
        "ted": ("TED", "warn"),
        "transferencia_intercre": ("Intercre", "warn"),
        "transferencia_pix_sicoob": ("Transf. PIX SI", "warn"),
        "deposit_transfer_bdn": ("Transfer. BDN", "warn"),
        "transferencia_bdn": ("Transf. conta", "warn"),
        "transferencia_conta": ("Transf. conta", "warn"),
        "transferencia_agencias": ("Entre agencias", "warn"),
        "transferencia_poupanca": ("Poupanca", "warn"),
        "deposito_dinheiro": ("Deposito dinheiro", "info"),
        "deposito_cheque": ("Deposito cheque", "info"),
        "liberacao_deposito": ("Liberacao dep.", "info"),
        "estorno_pix": ("Estorno PIX", "danger"),
    }
    label, css_class = mapping.get(str(value or ""), (normalize_query(value) or "Movimento", "info"))
    return badge(label, css_class)


def cent_rule_badge_class(code: object) -> str:
    code_str = "".join(ch for ch in str(code or "") if ch.isdigit()).zfill(2)
    if code_str.isdigit():
        return f"cent cent-{code_str}"
    return "cent cent-generic"


def pix_rule_badge(code: object, name: object) -> str:
    code_str = "".join(ch for ch in str(code or "") if ch.isdigit()).zfill(2)
    return badge(f"{code_str} · {normalize_query(name)}", cent_rule_badge_class(code_str))


def pix_confidence_group_badge(value: object) -> str:
    mapping = {
        "provavel": ("Provaveis", "warn"),
        "ambiguo": ("Ambiguos", "danger"),
        "sem_match": ("Sem match", "danger"),
        "pj_externo": ("PJ / externo", "info"),
        "forte": ("Fortes", "ok"),
    }
    label, css_class = mapping.get(str(value or ""), (normalize_query(value) or "Grupo", "info"))
    return badge(label, css_class)


def contribution_statement_period_label(year: str = "", date_start: str = "", date_end: str = "") -> str:
    if year and (date_start or date_end):
        return f"Ano {year} | {br_date(date_start) or 'Inicio'} ate {br_date(date_end) or 'Hoje'}"
    if date_start or date_end:
        return f"{br_date(date_start) or 'Inicio'} ate {br_date(date_end) or 'Hoje'}"
    if year:
        return f"Ano {year}"
    return "Todo o historico"


def build_contribution_statement_data(
    db: "PowerChurchDB",
    person_id: int,
    year: str = "",
    date_start: str = "",
    date_end: str = "",
    competencia: str = "",
    type_ids: list[int] | None = None,
) -> dict[str, object]:
    person = db.get_person(person_id) if person_id else None
    years = db.person_contribution_years(person_id) if person else []
    competences = db.person_contribution_competences(person_id) if person else []
    if year and year not in years and year.lower() != "todos":
        years = [year, *years]
    if competencia and competencia not in competences:
        competences = [competencia, *competences]
    available_types = db.contribution_types(moneyless_int(person["organizacao_id"])) if person else []
    selected_type_ids = [moneyless_int(item) for item in (type_ids or []) if moneyless_int(item) > 0]
    selected_type_names = [
        str(row["nome"])
        for row in available_types
        if moneyless_int(row["id"]) in selected_type_ids
    ]
    rows = (
        db.person_contribution_statement(
            person_id,
            year=year,
            date_start=date_start,
            date_end=date_end,
            competencia=competencia,
            type_ids=selected_type_ids,
        )
        if person
        else []
    )
    total_general = 0.0
    competence_count = 0
    current_competence = ""
    current_subtotal = 0.0
    entries: list[dict[str, object]] = []
    for row in rows:
        competence = str(row["competencia"] or "")
        value = float(row["valor"] or 0)
        if current_competence and competence != current_competence:
            entries.append(
                {
                    "kind": "subtotal",
                    "competencia": current_competence,
                    "subtotal": current_subtotal,
                }
            )
            current_subtotal = 0.0
        if competence != current_competence:
            competence_count += 1
            current_competence = competence
        current_subtotal += value
        total_general += value
        entries.append(
            {
                "kind": "item",
                "data_recebimento": row["data_recebimento"],
                "competencia": competence,
                "tipo_nome": row["tipo_nome"],
                "forma_nome": row["forma_nome"],
                "observacoes": row["observacoes"],
                "valor": value,
            }
        )
    if current_competence:
        entries.append(
            {
                "kind": "subtotal",
                "competencia": current_competence,
                "subtotal": current_subtotal,
            }
        )
    return {
        "person": person,
        "years": years,
        "rows": rows,
        "entries": entries,
        "total_general": total_general,
        "competence_count": competence_count,
        "period_label": contribution_statement_period_label(year=year, date_start=date_start, date_end=date_end),
        "competences": competences,
        "competencia": competencia,
        "available_types": available_types,
        "selected_type_ids": selected_type_ids,
        "selected_type_names": selected_type_names,
        "type_label": ", ".join(selected_type_names) if selected_type_names else "Todos os tipos",
        "year": year,
        "date_start": date_start,
        "date_end": date_end,
    }


def truncate_text(value: object, limit: int) -> str:
    text = normalize_query(value)
    if len(text) <= limit:
        return text
    return f"{text[: max(limit - 3, 1)].rstrip()}..."


def wrap_text(value: object, limit: int) -> list[str]:
    text = normalize_query(value)
    if not text:
        return []
    words = text.split(" ")
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            lines.append(current)
            current = word
        else:
            while len(word) > limit:
                lines.append(word[:limit])
                word = word[limit:]
            current = word
    if current:
        lines.append(current)
    return lines


def pdf_text_literal(value: object) -> bytes:
    raw = str(value or "").replace("\r", " ").replace("\n", " ").encode("latin-1", "replace")
    escaped = raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")
    return b"(" + escaped + b")"


def pdf_color_command(mode: str, color: tuple[float, float, float]) -> str:
    return f"{color[0]:.3f} {color[1]:.3f} {color[2]:.3f} {mode}"


def pdf_estimated_text_width(text: object, font_size: int, bold: bool = False) -> float:
    factor = 0.56 if bold else 0.51
    return len(str(text or "")) * font_size * factor


def pdf_wrapped_lines(value: object, width: float, font_size: int, bold: bool = False, max_lines: int = 0) -> list[str]:
    char_limit = max(6, int(width / max(font_size * (0.57 if bold else 0.52), 1)))
    lines = wrap_text(value, char_limit)
    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = truncate_text(lines[-1], char_limit)
    return lines or [""]


def build_pdf_from_operations(page_operations: list[list[str]]) -> bytes:
    objects: list[bytes | None] = [None]

    def reserve_object() -> int:
        objects.append(b"")
        return len(objects) - 1

    def set_object(object_id: int, payload: bytes) -> None:
        objects[object_id] = payload

    catalog_id = reserve_object()
    pages_id = reserve_object()
    font_regular_id = reserve_object()
    font_bold_id = reserve_object()
    set_object(font_regular_id, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    set_object(font_bold_id, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")
    logo_resource = ""
    if brand_logo_available():
        logo_bytes = BRAND_LOGO_PATH.read_bytes()
        logo_width, logo_height, logo_components = jpeg_image_info(logo_bytes)
        logo_color_space = "/DeviceGray" if logo_components == 1 else "/DeviceCMYK" if logo_components == 4 else "/DeviceRGB"
        logo_image_id = reserve_object()
        set_object(
            logo_image_id,
            (
                f"<< /Type /XObject /Subtype /Image /Width {logo_width} /Height {logo_height} "
                f"/ColorSpace {logo_color_space} /BitsPerComponent 8 /Filter /DCTDecode /Length {len(logo_bytes)} >>\n"
            ).encode("ascii")
            + b"stream\n"
            + logo_bytes
            + b"\nendstream",
        )
        logo_resource = f" /XObject << /Logo {logo_image_id} 0 R >>"

    page_ids: list[int] = []
    for operations in page_operations:
        content_id = reserve_object()
        page_id = reserve_object()
        stream = "\n".join(operations).encode("latin-1", "replace")
        set_object(
            content_id,
            b"<< /Length "
            + str(len(stream)).encode("ascii")
            + b" >>\nstream\n"
            + stream
            + b"\nendstream",
        )
        set_object(
            page_id,
            (
                f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 595 842] "
                f"/Resources << /Font << /F1 {font_regular_id} 0 R /F2 {font_bold_id} 0 R >>{logo_resource} >> "
                f"/Contents {content_id} 0 R >>"
            ).encode("ascii"),
        )
        page_ids.append(page_id)

    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    set_object(pages_id, f"<< /Type /Pages /Count {len(page_ids)} /Kids [{kids}] >>".encode("ascii"))
    set_object(catalog_id, f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode("ascii"))

    pdf = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
    offsets = [0]
    for object_id in range(1, len(objects)):
        offsets.append(len(pdf))
        pdf += f"{object_id} 0 obj\n".encode("ascii")
        pdf += objects[object_id] or b""
        pdf += b"\nendobj\n"
    xref_offset = len(pdf)
    pdf += f"xref\n0 {len(objects)}\n".encode("ascii")
    pdf += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        pdf += f"{offset:010d} 00000 n \n".encode("ascii")
    pdf += (
        f"trailer\n<< /Size {len(objects)} /Root {catalog_id} 0 R >>\nstartxref\n{xref_offset}\n%%EOF".encode("ascii")
    )
    return pdf


def build_contribution_statement_pdf(
    person: sqlite3.Row,
    entries: list[dict[str, object]],
    total_general: float,
    competence_count: int,
    period_label: str,
    competencia_label: str,
    type_label: str,
) -> bytes:
    page_width = 595.0
    page_height = 842.0
    margin = 34.0
    content_width = page_width - (margin * 2)
    bottom_limit = 790.0
    palette = {
        "ink": (0.125, 0.157, 0.216),
        "muted": (0.400, 0.439, 0.502),
        "line": (0.871, 0.847, 0.808),
        "soft": (0.957, 0.937, 0.902),
        "green": (0.184, 0.451, 0.373),
        "gold": (0.729, 0.482, 0.169),
        "paper": (0.996, 0.992, 0.973),
        "white": (1.0, 1.0, 1.0),
        "green_soft": (0.914, 0.953, 0.937),
    }
    item_count = sum(1 for entry in entries if entry["kind"] == "item")
    logo_info = jpeg_image_info(BRAND_LOGO_PATH.read_bytes()) if brand_logo_available() else None
    pages: list[list[str]] = []
    current_ops: list[str] = []
    current_y = 0.0

    def push(op: str) -> None:
        current_ops.append(op)

    def top_to_pdf_y(top: float) -> float:
        return page_height - top

    def draw_rect(
        x: float,
        top: float,
        width: float,
        height: float,
        stroke: tuple[float, float, float] | None = None,
        fill: tuple[float, float, float] | None = None,
        line_width: float = 1.0,
    ) -> None:
        push(f"{line_width:.2f} w")
        if stroke:
            push(pdf_color_command("RG", stroke))
        if fill:
            push(pdf_color_command("rg", fill))
        op = "B" if stroke and fill else "S" if stroke else "f"
        push(f"{x:.2f} {top_to_pdf_y(top + height):.2f} {width:.2f} {height:.2f} re {op}")

    def draw_line(x1: float, top1: float, x2: float, top2: float, color: tuple[float, float, float], line_width: float = 1.0) -> None:
        push(f"{line_width:.2f} w")
        push(pdf_color_command("RG", color))
        push(f"{x1:.2f} {top_to_pdf_y(top1):.2f} m {x2:.2f} {top_to_pdf_y(top2):.2f} l S")

    def draw_logo(x: float, top: float, width: float) -> None:
        if not logo_info:
            return
        source_width, source_height, _components = logo_info
        height = width * (source_height / source_width)
        push("q")
        push(f"{width:.2f} 0 0 {height:.2f} {x:.2f} {top_to_pdf_y(top + height):.2f} cm")
        push("/Logo Do")
        push("Q")

    def draw_text(
        x: float,
        baseline_top: float,
        text: object,
        font_name: str = "F1",
        font_size: int = 10,
        color: tuple[float, float, float] | None = None,
    ) -> None:
        if color:
            push(pdf_color_command("rg", color))
        push("BT")
        push(f"/{font_name} {font_size} Tf")
        push(f"1 0 0 1 {x:.2f} {top_to_pdf_y(baseline_top):.2f} Tm")
        push((pdf_text_literal(text) + b" Tj").decode("latin-1"))
        push("ET")

    def draw_right_text(
        right_x: float,
        baseline_top: float,
        text: object,
        font_name: str = "F1",
        font_size: int = 10,
        color: tuple[float, float, float] | None = None,
        bold: bool = False,
    ) -> None:
        text_width = pdf_estimated_text_width(text, font_size, bold=bold)
        draw_text(right_x - text_width, baseline_top, text, font_name=font_name, font_size=font_size, color=color)

    def draw_field_box(x: float, top: float, width: float, height: float, label: str, value: object) -> None:
        draw_rect(x, top, width, height, stroke=palette["line"], fill=palette["white"], line_width=0.8)
        draw_text(x + 8, top + 12, label.upper(), font_name="F2", font_size=7, color=palette["muted"])
        value_lines = pdf_wrapped_lines(value or "Nao informado", width - 16, 10, max_lines=2)
        for index, line in enumerate(value_lines):
            draw_text(x + 8, top + 27 + (index * 11), line, font_name="F1", font_size=10, color=palette["ink"])

    def draw_metric_card(x: float, top: float, width: float, height: float, label: str, value: object, accent: tuple[float, float, float]) -> None:
        draw_rect(x, top, width, height, stroke=palette["line"], fill=palette["paper"], line_width=0.9)
        draw_text(x + 10, top + 12, label.upper(), font_name="F2", font_size=7, color=palette["muted"])
        value_lines = pdf_wrapped_lines(value, width - 20, 13, bold=True, max_lines=2)
        for index, line in enumerate(value_lines):
            draw_text(x + 10, top + 31 + (index * 14), line, font_name="F2", font_size=13, color=accent)

    def draw_filter_panel(top: float) -> float:
        line_values = [
            ("Periodo", period_label),
            ("Competencia", competencia_label),
            ("Tipos", type_label),
        ]
        height = 18.0
        wrapped_groups: list[tuple[str, list[str]]] = []
        for label, value in line_values:
            lines = pdf_wrapped_lines(f"{label}: {value}", content_width - 22, 9)
            wrapped_groups.append((label, lines))
            height += (len(lines) * 11) + 4
        draw_rect(margin, top, content_width, height, stroke=palette["line"], fill=palette["paper"], line_width=0.9)
        draw_text(margin + 12, top + 14, "FILTROS APLICADOS", font_name="F2", font_size=8, color=palette["muted"])
        cursor = top + 30
        for _label, lines in wrapped_groups:
            for line in lines:
                draw_text(margin + 12, cursor, line, font_name="F1", font_size=9, color=palette["ink"])
                cursor += 11
            cursor += 4
        return height

    def draw_table_header(top: float, continuation: bool = False) -> float:
        title = "Extrato analitico" if not continuation else "Extrato analitico - continuidade"
        draw_text(margin, top + 2, title, font_name="F2", font_size=13, color=palette["ink"])
        header_top = top + 16
        header_height = 22.0
        draw_rect(margin, header_top, content_width, header_height, stroke=palette["line"], fill=palette["soft"], line_width=0.9)
        columns = [
            ("Data", 0.0, 56.0),
            ("Competencia", 56.0, 76.0),
            ("Tipo", 132.0, 100.0),
            ("Modalidade", 232.0, 90.0),
            ("Observacoes", 322.0, 136.0),
            ("Valor", 458.0, 69.0),
        ]
        for label, offset, width in columns[:-1]:
            draw_text(margin + offset + 6, header_top + 14, label.upper(), font_name="F2", font_size=7, color=palette["ink"])
            draw_line(margin + offset + width, header_top, margin + offset + width, header_top + header_height, palette["line"], 0.6)
        last_label, last_offset, last_width = columns[-1]
        draw_right_text(
            margin + last_offset + last_width - 6,
            header_top + 14,
            last_label.upper(),
            font_name="F2",
            font_size=7,
            color=palette["ink"],
            bold=True,
        )
        return header_top + header_height + 8

    def start_page(first_page: bool) -> None:
        nonlocal current_ops, current_y
        if current_ops:
            pages.append(current_ops)
        current_ops = []
        current_y = 42.0
        logo_width = 72.0 if logo_info else 0.0
        text_x = margin + logo_width + 12.0 if logo_info else margin
        if logo_info:
            draw_logo(margin, current_y - 10, logo_width)
        draw_text(text_x, current_y, "Extrato de contribuicoes", font_name="F2", font_size=19, color=palette["ink"])
        draw_text(text_x, current_y + 18, f"{APP_TITLE} | Documento consolidado de contribuicoes da pessoa.", font_name="F1", font_size=9, color=palette["muted"])
        draw_right_text(page_width - margin, current_y + 2, f"Emitido em {br_date(date.today().isoformat())}", font_name="F1", font_size=9, color=palette["muted"])
        draw_line(margin, current_y + 28, page_width - margin, current_y + 28, palette["line"], 1.0)
        current_y += 42
        if first_page:
            member_panel_height = 92.0
            draw_rect(margin, current_y, content_width, member_panel_height, stroke=palette["line"], fill=palette["paper"], line_width=0.9)
            draw_text(margin + 12, current_y + 14, "MEMBRO", font_name="F2", font_size=8, color=palette["muted"])
            draw_field_box(margin + 12, current_y + 24, content_width - 24, 24, "Nome", person["nome"])
            box_gap = 8.0
            small_width = (content_width - 24 - (box_gap * 2)) / 3
            small_top = current_y + 56
            draw_field_box(margin + 12, small_top, small_width, 24, "Numero de membro", format_member_code(person["codigo_interno"]) or "-")
            draw_field_box(margin + 12 + small_width + box_gap, small_top, small_width, 24, "CPF", format_cpf(person["cpf"]) or "-")
            draw_field_box(margin + 12 + ((small_width + box_gap) * 2), small_top, small_width, 24, "Status", person["status"] or "-")
            current_y += member_panel_height + 14

            card_gap = 10.0
            card_width = (content_width - (card_gap * 2)) / 3
            card_height = 54.0
            card_top = current_y
            draw_metric_card(margin, card_top, card_width, card_height, "Lancamentos", item_count, palette["ink"])
            draw_metric_card(margin + card_width + card_gap, card_top, card_width, card_height, "Competencias", competence_count, palette["blue" if "blue" in palette else "green"])
            draw_metric_card(margin + ((card_width + card_gap) * 2), card_top, card_width, card_height, "Total geral", br_money(total_general), palette["green"])
            current_y += card_height + 14

            current_y += draw_filter_panel(current_y)
            current_y += 16
            current_y = draw_table_header(current_y, continuation=False)
        else:
            compact_height = 44.0
            draw_rect(margin, current_y, content_width, compact_height, stroke=palette["line"], fill=palette["paper"], line_width=0.9)
            draw_text(margin + 12, current_y + 16, truncate_text(person["nome"], 42), font_name="F2", font_size=11, color=palette["ink"])
            draw_text(margin + 12, current_y + 30, truncate_text(f"Periodo: {period_label} | Competencia: {competencia_label}", 88), font_name="F1", font_size=8, color=palette["muted"])
            current_y += compact_height + 14
            current_y = draw_table_header(current_y, continuation=True)

    def draw_total_strip(top: float) -> float:
        height = 22.0
        draw_rect(margin, top, content_width, height, stroke=palette["line"], fill=palette["soft"], line_width=0.9)
        draw_text(margin + 10, top + 14, "TOTAL GERAL", font_name="F2", font_size=9, color=palette["ink"])
        draw_right_text(page_width - margin - 10, top + 14, br_money(total_general), font_name="F2", font_size=10, color=palette["green"], bold=True)
        return height

    row_layouts: list[dict[str, object]] = []
    if not entries:
        row_layouts.append({"kind": "empty", "height": 28.0, "text": "Nenhuma contribuicao encontrada para o filtro informado."})
    else:
        for entry in entries:
            if entry["kind"] == "subtotal":
                row_layouts.append(
                    {
                        "kind": "subtotal",
                        "height": 24.0,
                        "competencia": entry["competencia"],
                        "subtotal": entry["subtotal"],
                    }
                )
                continue
            observation_lines = pdf_wrapped_lines(entry["observacoes"] or "-", 132.0, 8, max_lines=4)
            row_height = max(24.0, 14.0 + (len(observation_lines) * 9.5))
            row_layouts.append(
                {
                    "kind": "item",
                    "height": row_height,
                    "date": br_date(entry["data_recebimento"]),
                    "competencia": truncate_text(entry["competencia"], 14),
                    "tipo": truncate_text(entry["tipo_nome"], 18),
                    "forma": truncate_text(entry["forma_nome"], 16),
                    "valor": br_money(entry["valor"]),
                    "observacoes": observation_lines,
                }
            )

    start_page(first_page=True)
    columns = [
        ("date", margin, 56.0),
        ("competencia", margin + 56.0, 76.0),
        ("tipo", margin + 132.0, 100.0),
        ("forma", margin + 232.0, 90.0),
        ("observacoes", margin + 322.0, 136.0),
        ("valor", margin + 458.0, 69.0),
    ]

    for row in row_layouts:
        row_height = float(row["height"])
        extra_total_space = 30.0 if row is row_layouts[-1] else 0.0
        if current_y + row_height + extra_total_space > bottom_limit:
            start_page(first_page=False)
        if row["kind"] == "empty":
            draw_rect(margin, current_y, content_width, row_height, stroke=palette["line"], fill=palette["white"], line_width=0.8)
            draw_text(margin + 10, current_y + 16, row["text"], font_name="F1", font_size=10, color=palette["muted"])
            current_y += row_height
            continue
        if row["kind"] == "subtotal":
            draw_rect(margin, current_y, content_width, row_height, stroke=palette["line"], fill=palette["green_soft"], line_width=0.8)
            draw_text(margin + 10, current_y + 15, f"Subtotal {row['competencia']}", font_name="F2", font_size=9, color=palette["ink"])
            draw_right_text(page_width - margin - 10, current_y + 15, br_money(row["subtotal"]), font_name="F2", font_size=9, color=palette["green"], bold=True)
            current_y += row_height
            continue
        draw_rect(margin, current_y, content_width, row_height, stroke=palette["line"], fill=palette["white"], line_width=0.6)
        for _key, col_x, col_width in columns[:-1]:
            draw_line(col_x + col_width, current_y, col_x + col_width, current_y + row_height, palette["line"], 0.4)
        text_top = current_y + 14
        draw_text(columns[0][1] + 6, text_top, row["date"], font_name="F1", font_size=8, color=palette["ink"])
        draw_text(columns[1][1] + 6, text_top, row["competencia"], font_name="F1", font_size=8, color=palette["ink"])
        draw_text(columns[2][1] + 6, text_top, row["tipo"], font_name="F1", font_size=8, color=palette["ink"])
        draw_text(columns[3][1] + 6, text_top, row["forma"], font_name="F1", font_size=8, color=palette["ink"])
        for index, line in enumerate(row["observacoes"]):
            draw_text(columns[4][1] + 6, text_top + (index * 9.5), line, font_name="F1", font_size=8, color=palette["ink"])
        draw_right_text(page_width - margin - 8, text_top, row["valor"], font_name="F2", font_size=8, color=palette["ink"], bold=True)
        current_y += row_height

    if current_y + 24.0 > bottom_limit:
        start_page(first_page=False)
    current_y += 8
    current_y += draw_total_strip(current_y)

    if current_ops:
        pages.append(current_ops)
    total_pages = len(pages)
    for index, page_ops in enumerate(pages, start=1):
        footer_y = page_height - 26.0
        page_ops.append(pdf_color_command("rg", palette["muted"]))
        page_ops.append("BT")
        page_ops.append("/F1 8 Tf")
        page_ops.append(f"1 0 0 1 {page_width - margin - 52:.2f} {footer_y:.2f} Tm")
        page_ops.append((pdf_text_literal(f"Pagina {index}/{total_pages}") + b" Tj").decode("latin-1"))
        page_ops.append("ET")
    return build_pdf_from_operations(pages)


def contribution_statement_download_name(
    person_id: int,
    year: str = "",
    date_start: str = "",
    date_end: str = "",
    competencia: str = "",
) -> str:
    if competencia:
        competencia_part = normalize_query(competencia).lower().replace(" ", "_")
        return f"extrato_contribuicoes_{person_id}_{competencia_part}.pdf"
    if date_start or date_end:
        start_part = (date_start or "inicio").replace("-", "")
        end_part = (date_end or "hoje").replace("-", "")
        return f"extrato_contribuicoes_{person_id}_{start_part}_{end_part}.pdf"
    if year:
        return f"extrato_contribuicoes_{person_id}_{year}.pdf"
    return f"extrato_contribuicoes_{person_id}_historico.pdf"


def contributor_membership_sigla(status: object, person_id: object) -> tuple[str, str, str]:
    return core_contributors.contributor_membership_sigla(status, person_id)


def contributor_membership_legend() -> list[tuple[str, str]]:
    return core_contributors.contributor_membership_legend()


def contributor_period_filter_label(competencia: str = "", date_start: str = "", date_end: str = "", person_query: str = "") -> str:
    parts: list[str] = []
    if competencia:
        parts.append(f"Competencia: {competencia}")
    if date_start or date_end:
        parts.append(f"Periodo: {br_date(date_start) or '-'} a {br_date(date_end) or '-'}")
    if person_query:
        parts.append(f"Pessoa / contribuinte: {normalize_query(person_query)}")
    return " | ".join(parts) if parts else "Periodo: todas as contribuicoes cadastradas"


def contributor_period_download_name(competencia: str = "", date_start: str = "", date_end: str = "", person_query: str = "") -> str:
    if competencia:
        return f"contribuicoes_por_periodo_{normalize_query(competencia).lower().replace(' ', '_')}.pdf"
    if date_start or date_end:
        start_part = (date_start or "inicio").replace("-", "")
        end_part = (date_end or "hoje").replace("-", "")
        return f"contribuicoes_por_periodo_{start_part}_{end_part}.pdf"
    if person_query:
        slug = slugify_filename_text(person_query, fallback="pessoa")
        return f"contribuicoes_por_periodo_{slug}.pdf"
    return "contribuicoes_por_periodo.pdf"


def contributor_period_entry_lines(entries: list[dict[str, object]]) -> list[str]:
    if not entries:
        return ["Sem remessas"]
    if len(entries) == 1:
        entry = entries[0]
        return [f"{br_date(entry['data_recebimento'])} | {entry['competencia'] or '-'} | {br_money(entry['valor'])}"]
    return [
        f"{len(entries)} remessas",
        *[f"{br_date(entry['data_recebimento'])} | {entry['competencia'] or '-'} | {br_money(entry['valor'])}" for entry in entries],
    ]


def build_contributor_period_report_data(
    db: PowerChurchDB,
    competencia: str = "",
    date_start: str = "",
    date_end: str = "",
    person_query: str = "",
) -> dict[str, object]:
    person_query = normalize_query(person_query)
    exact_people: list[sqlite3.Row] = []
    exact_contributors: list[sqlite3.Row] = []
    suggestions: list[dict[str, object]] = []
    search_mode = ""
    search_label = ""
    filtered_person_ids: list[int] = []
    filtered_name_norms: list[str] = []
    filtered_contributor_ids: list[int] = []

    if person_query:
        exact_people = db.contributor_report_exact_people(person_query, limit=12)
        if exact_people:
            filtered_person_ids = sorted({moneyless_int(row["id"]) for row in exact_people if moneyless_int(row["id"]) > 0})
            filtered_name_norms = sorted(
                {
                    normalize_match_name(row["nome"])
                    for row in exact_people
                    if normalize_match_name(row["nome"])
                }
            )
            search_mode = "pessoas_exatas"
            if len(exact_people) == 1:
                search_label = f"Filtro fechado pela ficha {exact_people[0]['nome']}."
            else:
                search_label = f"Filtro fechado por {len(exact_people)} ficha(s) com nome exato."
        else:
            exact_contributors = db.contributor_report_exact_contributors(person_query, limit=24)
            if exact_contributors:
                filtered_contributor_ids = [moneyless_int(row["id"]) for row in exact_contributors if moneyless_int(row["id"]) > 0]
                search_mode = "contribuintes_exatos"
                if len(exact_contributors) == 1:
                    search_label = f"Filtro fechado no cadastro auxiliar: {exact_contributors[0]['nome']}."
                else:
                    search_label = f"Filtro fechado em {len(exact_contributors)} contribuinte(s) do cadastro auxiliar."
            else:
                suggestions = db.contributor_report_person_suggestions(person_query, limit=10)
                search_mode = "provaveis"
                search_label = "Nenhum nome exato foi encontrado; a lista abaixo mostra provaveis para conferencia."

    rows: list[sqlite3.Row] = []
    if not (person_query and search_mode == "provaveis"):
        rows = db.contributor_period_rows(
            competencia=competencia,
            date_start=date_start,
            date_end=date_end,
            person_ids=filtered_person_ids,
            contributor_name_norms=filtered_name_norms,
            contributor_ids=filtered_contributor_ids,
        )

    grouped: dict[str, dict[str, object]] = {}
    for row in rows:
        effective_person_id = moneyless_int(row["pessoa_efetiva_id"])
        contributor_id = moneyless_int(row["contribuinte_id"])
        identity = contribution_report_identity(
            row["pessoa_nome"],
            row["contribuinte_nome_original"] or row["contribuinte_nome"],
            row["contribuinte_documento"],
        )
        contributor_name = identity["name"] or "Documento nao identificado"
        group_key = (
            f"p:{effective_person_id}"
            if effective_person_id
            else f"c:{contributor_id}"
            if contributor_id
            else f"n:{normalize_match_name(contributor_name)}"
        )
        sigla, sigla_label, sigla_class = contributor_membership_sigla(row["pessoa_status"], effective_person_id)
        bucket = grouped.setdefault(
            group_key,
            {
                "key": group_key,
                "contribuinte_id": contributor_id,
                "nome": contributor_name,
                "sort_key": identity["sort_key"],
                "group_kind": identity["group_kind"],
                "group_label": identity["group_label"],
                "documento": identity["document"],
                "nome_original": identity["raw_name"],
                "pessoa_id": effective_person_id,
                "pessoa_nome": str(row["pessoa_nome"] or ""),
                "pessoa_codigo_interno": str(row["pessoa_codigo_interno"] or ""),
                "sigla": sigla,
                "sigla_label": sigla_label,
                "sigla_class": sigla_class,
                "total": 0.0,
                "remessas": 0,
                "entries": [],
            },
        )
        bucket["total"] = round(float(bucket["total"]) + float(row["valor"] or 0.0), 2)
        bucket["remessas"] = moneyless_int(bucket["remessas"]) + 1
        bucket["entries"].append(
            {
                "id": moneyless_int(row["id"]),
                "data_recebimento": str(row["data_recebimento"] or ""),
                "competencia": str(row["competencia"] or ""),
                "valor": float(row["valor"] or 0.0),
            }
        )

    groups = sorted(
        grouped.values(),
        key=lambda item: (
            0 if item.get("group_kind") == "nome" else 1,
            str(item.get("sort_key") or normalize_match_name(item["nome"])),
            str(item.get("documento") or ""),
        ),
    )
    summary = {
        "total_geral": round(sum(float(item["total"]) for item in groups), 2),
        "contribuintes": len(groups),
        "remessas": sum(moneyless_int(item["remessas"]) for item in groups),
        "no_rol": sum(1 for item in groups if item["sigla"] in {"SA", "SI"}),
        "fora_rol": sum(1 for item in groups if item["sigla"] in {"NF", "NV", "NM", "NR"}),
        "inativos": sum(1 for item in groups if item["sigla"] == "SI"),
        "sem_vinculo": sum(1 for item in groups if item["sigla"] == "NR"),
        "somente_documento": sum(1 for item in groups if item.get("group_kind") == "documento"),
    }
    return {
        "competencia": competencia,
        "date_start": date_start,
        "date_end": date_end,
        "person_query": person_query,
        "groups": groups,
        "summary": summary,
        "exact_people": [dict(row) for row in exact_people],
        "exact_contributors": [dict(row) for row in exact_contributors],
        "suggestions": suggestions,
        "search_mode": search_mode,
        "search_label": search_label,
        "legend": contributor_membership_legend(),
    }


def build_contributor_period_report_payload(data: dict[str, object]) -> dict[str, object]:
    summary = dict(data["summary"])
    groups: list[dict[str, object]] = [
        {
            "title": "Resumo do periodo",
            "subtitle": contributor_period_filter_label(
                competencia=str(data.get("competencia") or ""),
                date_start=str(data.get("date_start") or ""),
                date_end=str(data.get("date_end") or ""),
                person_query=str(data.get("person_query") or ""),
            ),
            "lines": [
                f"Total geral | {br_money(summary['total_geral'])}",
                f"Contribuintes diferentes | {summary['contribuintes']}",
                f"Remessas | {summary['remessas']}",
                f"No rol | {summary['no_rol']}",
                f"Fora do rol | {summary['fora_rol']}",
                f"Inativos | {summary['inativos']}",
                f"Sem vinculo | {summary['sem_vinculo']}",
                f"Somente documento | {summary.get('somente_documento', 0)}",
                "Legenda | " + " | ".join(f"{code} = {label}" for code, label in data["legend"]),
            ],
        }
    ]
    if normalize_query(data.get("search_label")):
        groups.append(
            {
                "title": "Filtro aplicado",
                "subtitle": "Resolucao da busca por pessoa ou contribuinte.",
                "lines": [str(data["search_label"])],
            }
        )
    if data.get("suggestions"):
        groups.append(
            {
                "title": "Provaveis para a busca informada",
                "subtitle": "Nenhum nome exato foi localizado. Use esta lista para divergencia, auditoria interna e saneamento manual.",
                "lines": [
                    " | ".join(
                        item
                        for item in [
                            str(row["nome"]),
                            contributor_membership_sigla(row["status"], row["id"])[0],
                            format_system_id(row["id"]),
                            format_member_code(row["codigo_interno"]) or "Sem numero",
                            row["reason"],
                        ]
                        if item
                    )
                    for row in data["suggestions"]
                ],
            }
        )
    for item in data["groups"]:
        groups.append(
            {
                "title": f"{item['nome']} | {item['sigla']} | Total {br_money(item['total'])}",
                "subtitle": " | ".join(
                    piece
                    for piece in [
                        f"{item['remessas']} remessa(s)",
                        item["pessoa_nome"] or "Sem pessoa vinculada",
                        format_member_code(item["pessoa_codigo_interno"]) or "",
                    ]
                    if piece
                ),
                "lines": [
                    f"{br_date(entry['data_recebimento'])} | {entry['competencia'] or '-'} | {br_money(entry['valor'])}"
                    for entry in item["entries"]
                ],
            }
        )
    return {
        "title": "Contribuicoes por periodo",
        "subtitle": "Relatorio alfabetico de contribuicoes por contribuinte, com sigla cadastral, remessas e total consolidado.",
        "groups": groups,
        "empty": "Nenhuma contribuicao encontrada para o filtro informado.",
    }


def build_contributor_period_report_pdf(data: dict[str, object]) -> bytes:
    page_width = 595.0
    page_height = 842.0
    margin = 34.0
    content_width = page_width - (margin * 2)
    bottom_limit = 792.0
    palette = {
        "ink": (0.125, 0.157, 0.216),
        "muted": (0.400, 0.439, 0.502),
        "line": (0.871, 0.847, 0.808),
        "soft": (0.957, 0.937, 0.902),
        "green": (0.184, 0.451, 0.373),
        "gold": (0.729, 0.482, 0.169),
        "paper": (0.996, 0.992, 0.973),
        "white": (1.0, 1.0, 1.0),
        "danger": (0.663, 0.267, 0.247),
        "blue": (0.145, 0.310, 0.478),
    }
    summary = dict(data["summary"])
    period_label = contributor_period_filter_label(
        competencia=str(data.get("competencia") or ""),
        date_start=str(data.get("date_start") or ""),
        date_end=str(data.get("date_end") or ""),
        person_query=str(data.get("person_query") or ""),
    )
    logo_info = jpeg_image_info(BRAND_LOGO_PATH.read_bytes()) if brand_logo_available() else None
    pages: list[list[str]] = []
    current_ops: list[str] = []
    current_y = 0.0

    def push(op: str) -> None:
        current_ops.append(op)

    def top_to_pdf_y(top: float) -> float:
        return page_height - top

    def draw_rect(
        x: float,
        top: float,
        width: float,
        height: float,
        stroke: tuple[float, float, float] | None = None,
        fill: tuple[float, float, float] | None = None,
        line_width: float = 1.0,
    ) -> None:
        push(f"{line_width:.2f} w")
        if stroke:
            push(pdf_color_command("RG", stroke))
        if fill:
            push(pdf_color_command("rg", fill))
        op = "B" if stroke and fill else "S" if stroke else "f"
        push(f"{x:.2f} {top_to_pdf_y(top + height):.2f} {width:.2f} {height:.2f} re {op}")

    def draw_line(x1: float, top1: float, x2: float, top2: float, color: tuple[float, float, float], line_width: float = 1.0) -> None:
        push(f"{line_width:.2f} w")
        push(pdf_color_command("RG", color))
        push(f"{x1:.2f} {top_to_pdf_y(top1):.2f} m {x2:.2f} {top_to_pdf_y(top2):.2f} l S")

    def draw_logo(x: float, top: float, width: float) -> None:
        if not logo_info:
            return
        source_width, source_height, _components = logo_info
        height = width * (source_height / source_width)
        push("q")
        push(f"{width:.2f} 0 0 {height:.2f} {x:.2f} {top_to_pdf_y(top + height):.2f} cm")
        push("/Logo Do")
        push("Q")

    def draw_text(
        x: float,
        baseline_top: float,
        text: object,
        font_name: str = "F1",
        font_size: int = 10,
        color: tuple[float, float, float] | None = None,
    ) -> None:
        if color:
            push(pdf_color_command("rg", color))
        push("BT")
        push(f"/{font_name} {font_size} Tf")
        push(f"1 0 0 1 {x:.2f} {top_to_pdf_y(baseline_top):.2f} Tm")
        push((pdf_text_literal(text) + b" Tj").decode("latin-1"))
        push("ET")

    def draw_right_text(
        right_x: float,
        baseline_top: float,
        text: object,
        font_name: str = "F1",
        font_size: int = 10,
        color: tuple[float, float, float] | None = None,
        bold: bool = False,
    ) -> None:
        text_width = pdf_estimated_text_width(text, font_size, bold=bold)
        draw_text(right_x - text_width, baseline_top, text, font_name=font_name, font_size=font_size, color=color)

    def draw_metric_card(x: float, top: float, width: float, height: float, label: str, value: object, accent: tuple[float, float, float]) -> None:
        draw_rect(x, top, width, height, stroke=palette["line"], fill=palette["paper"], line_width=0.8)
        draw_text(x + 10, top + 12, label.upper(), font_name="F2", font_size=7, color=palette["muted"])
        value_lines = pdf_wrapped_lines(value, width - 20, 12, bold=True, max_lines=2)
        for index, line in enumerate(value_lines):
            draw_text(x + 10, top + 29 + (index * 13), line, font_name="F2", font_size=12, color=accent)

    def draw_legend_panel(top: float) -> float:
        legend_text = " | ".join(f"{code} = {label}" for code, label in contributor_membership_legend())
        lines = pdf_wrapped_lines(legend_text, content_width - 24, 9, max_lines=3)
        height = 26.0 + (len(lines) * 11.0)
        draw_rect(margin, top, content_width, height, stroke=palette["line"], fill=palette["paper"], line_width=0.8)
        draw_text(margin + 12, top + 14, "LEGENDA", font_name="F2", font_size=8, color=palette["muted"])
        cursor = top + 30
        for line in lines:
            draw_text(margin + 12, cursor, line, font_name="F1", font_size=9, color=palette["ink"])
            cursor += 11.0
        return height

    def draw_table_header(top: float, continuation: bool = False) -> float:
        title = "Contribuicoes por periodo" if not continuation else "Contribuicoes por periodo - continuidade"
        draw_text(margin, top + 2, title, font_name="F2", font_size=13, color=palette["ink"])
        header_top = top + 16
        header_height = 22.0
        draw_rect(margin, header_top, content_width, header_height, stroke=palette["line"], fill=palette["soft"], line_width=0.9)
        columns = [
            ("Contribuinte", 0.0, 178.0),
            ("Sigla", 178.0, 46.0),
            ("Contribuicoes", 224.0, 227.0),
            ("Total", 451.0, 76.0),
        ]
        for label, offset, width in columns[:-1]:
            draw_text(margin + offset + 6, header_top + 14, label.upper(), font_name="F2", font_size=7, color=palette["ink"])
            draw_line(margin + offset + width, header_top, margin + offset + width, header_top + header_height, palette["line"], 0.6)
        last_label, last_offset, last_width = columns[-1]
        draw_right_text(
            margin + last_offset + last_width - 6,
            header_top + 14,
            last_label.upper(),
            font_name="F2",
            font_size=7,
            color=palette["ink"],
            bold=True,
        )
        return header_top + header_height + 8

    def start_page(first_page: bool) -> None:
        nonlocal current_ops, current_y
        if current_ops:
            pages.append(current_ops)
        current_ops = []
        current_y = 42.0
        logo_width = 72.0 if logo_info else 0.0
        text_x = margin + logo_width + 12.0 if logo_info else margin
        if logo_info:
            draw_logo(margin, current_y - 10, logo_width)
        draw_text(text_x, current_y, "Contribuicoes por periodo", font_name="F2", font_size=19, color=palette["ink"])
        draw_text(text_x, current_y + 18, f"{APP_TITLE} | Relatorio consolidado por contribuinte.", font_name="F1", font_size=9, color=palette["muted"])
        draw_right_text(page_width - margin, current_y + 2, f"Emitido em {br_date(date.today().isoformat())}", font_name="F1", font_size=9, color=palette["muted"])
        draw_line(margin, current_y + 28, page_width - margin, current_y + 28, palette["line"], 1.0)
        current_y += 42
        if first_page:
            card_gap = 10.0
            card_width = (content_width - (card_gap * 3)) / 4
            card_height = 48.0
            first_row = [
                ("Total geral", br_money(summary["total_geral"]), palette["green"]),
                ("Contribuintes", summary["contribuintes"], palette["ink"]),
                ("Remessas", summary["remessas"], palette["blue"]),
                ("No rol", summary["no_rol"], palette["green"]),
            ]
            second_row = [
                ("Fora do rol", summary["fora_rol"], palette["gold"]),
                ("Inativos", summary["inativos"], palette["danger"]),
                ("Sem vinculo", summary["sem_vinculo"], palette["danger"]),
                ("Somente documento", summary.get("somente_documento", 0), palette["gold"]),
            ]
            row_top = current_y
            for index, (label, value, color) in enumerate(first_row):
                draw_metric_card(margin + ((card_width + card_gap) * index), row_top, card_width, card_height, label, value, color)
            current_y += card_height + 10
            second_card_width = (content_width - (card_gap * 3)) / 4
            for index, (label, value, color) in enumerate(second_row):
                draw_metric_card(margin + ((second_card_width + card_gap) * index), current_y, second_card_width, card_height, label, value, color)
            current_y += card_height + 12
            legend_height = draw_legend_panel(current_y)
            current_y += legend_height + 10
            filter_lines = pdf_wrapped_lines(period_label, content_width - 24, 9, max_lines=4)
            filter_height = 26.0 + (len(filter_lines) * 11.0)
            draw_rect(margin, current_y, content_width, filter_height, stroke=palette["line"], fill=palette["paper"], line_width=0.8)
            draw_text(margin + 12, current_y + 14, "FILTROS APLICADOS", font_name="F2", font_size=8, color=palette["muted"])
            cursor = current_y + 30
            for line in filter_lines:
                draw_text(margin + 12, cursor, line, font_name="F1", font_size=9, color=palette["ink"])
                cursor += 11.0
            current_y += filter_height + 14
            if data.get("suggestions"):
                draw_text(margin, current_y + 2, "Provaveis para a busca informada", font_name="F2", font_size=12, color=palette["ink"])
                current_y += 18
                for suggestion in list(data["suggestions"])[:5]:
                    suggestion_line = (
                        f"{suggestion['nome']} | {format_system_id(suggestion['id'])} | "
                        f"{format_member_code(suggestion['codigo_interno']) or 'Sem numero'} | {suggestion['reason']}"
                    )
                    wrapped = pdf_wrapped_lines(suggestion_line, content_width - 14, 8, max_lines=2)
                    box_height = 12.0 + (len(wrapped) * 10.0)
                    draw_rect(margin, current_y, content_width, box_height, stroke=palette["line"], fill=palette["white"], line_width=0.5)
                    for index, line in enumerate(wrapped):
                        draw_text(margin + 8, current_y + 14 + (index * 10.0), line, font_name="F1", font_size=8, color=palette["ink"])
                    current_y += box_height + 4.0
                current_y += 8
            current_y = draw_table_header(current_y, continuation=False)
        else:
            compact_height = 44.0
            draw_rect(margin, current_y, content_width, compact_height, stroke=palette["line"], fill=palette["paper"], line_width=0.9)
            draw_text(margin + 12, current_y + 16, "Contribuicoes por periodo", font_name="F2", font_size=11, color=palette["ink"])
            draw_text(margin + 12, current_y + 30, truncate_text(period_label, 90), font_name="F1", font_size=8, color=palette["muted"])
            current_y += compact_height + 14
            current_y = draw_table_header(current_y, continuation=True)

    start_page(first_page=True)
    columns = {
        "contribuinte": (margin, 178.0),
        "sigla": (margin + 178.0, 46.0),
        "entries": (margin + 224.0, 227.0),
        "total": (margin + 451.0, 76.0),
    }

    groups = list(data["groups"])
    if not groups:
        draw_rect(margin, current_y, content_width, 28.0, stroke=palette["line"], fill=palette["white"], line_width=0.8)
        draw_text(margin + 10, current_y + 16, "Nenhuma contribuicao encontrada para o filtro informado.", font_name="F1", font_size=10, color=palette["muted"])
        current_y += 28.0
    else:
        last_pdf_group_label = ""
        for item in groups:
            if item.get("group_label") != last_pdf_group_label:
                last_pdf_group_label = str(item.get("group_label") or "")
                section_height = 24.0
                if current_y + section_height > bottom_limit:
                    start_page(first_page=False)
                draw_rect(margin, current_y, content_width, section_height, stroke=palette["line"], fill=palette["soft"], line_width=0.7)
                draw_text(margin + 8, current_y + 15, last_pdf_group_label.upper(), font_name="F2", font_size=8, color=palette["ink"])
                current_y += section_height
            contributor_hint = " | ".join(
                bit
                for bit in [
                    str(item["documento"] or ""),
                    f"Origem: {item['nome_original']}" if item.get("nome_original") and item["nome_original"] != item["nome"] else "",
                    f"Pessoa: {item['pessoa_nome']}" if item["pessoa_nome"] else "",
                    format_member_code(item["pessoa_codigo_interno"]) or "",
                ]
                if bit
            )
            contributor_lines = [str(item["nome"])]
            if contributor_hint:
                contributor_lines.extend(pdf_wrapped_lines(contributor_hint, columns["contribuinte"][1] - 12, 7, max_lines=2))
            entry_lines = contributor_period_entry_lines(item["entries"])
            entry_wrapped: list[str] = []
            for index, line in enumerate(entry_lines):
                wrapped = pdf_wrapped_lines(line, columns["entries"][1] - 12, 8, max_lines=2 if index == 0 else 1)
                entry_wrapped.extend(wrapped)
            line_count = max(len(contributor_lines), len(entry_wrapped), 1)
            row_height = 14.0 + (line_count * 10.0)
            if current_y + row_height > bottom_limit:
                start_page(first_page=False)
            draw_rect(margin, current_y, content_width, row_height, stroke=palette["line"], fill=palette["white"], line_width=0.6)
            draw_line(columns["sigla"][0], current_y, columns["sigla"][0], current_y + row_height, palette["line"], 0.4)
            draw_line(columns["entries"][0], current_y, columns["entries"][0], current_y + row_height, palette["line"], 0.4)
            draw_line(columns["total"][0], current_y, columns["total"][0], current_y + row_height, palette["line"], 0.4)
            for index, line in enumerate(contributor_lines):
                draw_text(columns["contribuinte"][0] + 6, current_y + 14 + (index * 10.0), line, font_name="F2" if index == 0 else "F1", font_size=8 if index == 0 else 7, color=palette["ink"] if index == 0 else palette["muted"])
            sigla_color = palette["green"] if item["sigla"] == "SA" else palette["danger"] if item["sigla"] in {"SI", "NR", "NM"} else palette["blue"]
            draw_text(columns["sigla"][0] + 10, current_y + 18, item["sigla"], font_name="F2", font_size=10, color=sigla_color)
            for index, line in enumerate(entry_wrapped):
                draw_text(columns["entries"][0] + 6, current_y + 14 + (index * 10.0), line, font_name="F1", font_size=8, color=palette["ink"] if index else palette["muted"] if len(item["entries"]) > 1 else palette["ink"])
            draw_right_text(margin + content_width - 8, current_y + 18, br_money(item["total"]), font_name="F2", font_size=9, color=palette["ink"], bold=True)
            current_y += row_height

    if current_ops:
        pages.append(current_ops)
    total_pages = len(pages)
    for index, page_ops in enumerate(pages, start=1):
        footer_y = page_height - 26.0
        page_ops.append(pdf_color_command("rg", palette["muted"]))
        page_ops.append("BT")
        page_ops.append("/F1 8 Tf")
        page_ops.append(f"1 0 0 1 {page_width - margin - 52:.2f} {footer_y:.2f} Tm")
        page_ops.append((pdf_text_literal(f"Pagina {index}/{total_pages}") + b" Tj").decode("latin-1"))
        page_ops.append("ET")
    return build_pdf_from_operations(pages)


def contributor_report_filter_label(mode: str = "todos", q: str = "", tags: list[str] | tuple[str, ...] | set[str] | None = None) -> str:
    parts: list[str] = []
    mode_label = {
        "todos": "Modo: todos",
        "pendentes": "Modo: pendentes de associacao",
        "nao_lancados": "Modo: PIX em saneamento",
        "sem_pessoa": "Modo: contribuicoes sem pessoa",
        "recorrentes": "Modo: sugestao de integracao",
    }.get(normalize_query(mode) or "todos", f"Modo: {normalize_query(mode) or 'todos'}")
    parts.append(mode_label)
    if normalize_query(q):
        parts.append(f"Busca: {normalize_query(q)}")
    if tags:
        parts.append("Tags: " + ", ".join(normalize_query(tag).replace("_", " ") for tag in tags if normalize_query(tag)))
    return " | ".join(parts)


def contributor_report_download_name(section: str = "") -> str:
    section_key = normalize_query(section).lower() or "contributors"
    return f"relatorio_contribuintes_{section_key}.pdf"


def build_contributor_report_payload(
    data: dict[str, object],
    section: str = "",
    tags: list[str] | tuple[str, ...] | set[str] | None = None,
) -> dict[str, object]:
    section_key = normalize_query(section).lower() or "contributors"
    tag_set = {normalize_query(tag).lower() for tag in (tags or []) if normalize_query(tag)}
    report_tags = {"integracao", "familia_sugerida"}
    if section_key == "combined":
        groups: list[dict[str, object]] = []
        include_main = not tag_set or any(tag not in report_tags for tag in tag_set)
        if "integracao" in tag_set:
            for block in data.get("family_links", []):
                contributor = dict(block["contributor"])
                recurrence = contributor_recurrence_flags(contributor)
                recurrence_bits = []
                if recurrence["weekly"]:
                    recurrence_bits.append(f"{recurrence['weeks']} semana(s)")
                if recurrence["multi_competencia"]:
                    recurrence_bits.append(f"{recurrence['competencias']} competencia(s)")
                lines = [
                    f"{person['nome']} | {'Nucleo' if person['relation'] == 'nuclear' else 'Familia ampliada'} | {person['status']} | {format_system_id(person['id'])} | {format_member_code(person['codigo_interno']) or 'Sem numero'}"
                    for person in block["matches"]
                ]
                groups.append(
                    {
                        "title": f"Associacao sugerida: {contributor.get('nome') or ''}",
                        "subtitle": " | ".join(
                            item
                            for item in [
                                f"Documento {contributor.get('documento_principal') or 'sem documento'}",
                                f"Total {br_money(contributor.get('total_contribuido'))}",
                                f"Recorrencia {', '.join(recurrence_bits) or 'detectada'}",
                            ]
                            if item
                        ),
                        "lines": lines,
                    }
                )
        if "familia_sugerida" in tag_set:
            for group in data.get("family_groups", []):
                lines = []
                for member in group["members"]:
                    recurrence = contributor_recurrence_flags(member)
                    recurrence_bits = []
                    if recurrence["weekly"]:
                        recurrence_bits.append(f"{recurrence['weeks']} semana(s)")
                    if recurrence["multi_competencia"]:
                        recurrence_bits.append(f"{recurrence['competencias']} competencia(s)")
                    lines.append(
                        f"{member['nome']} | {member.get('documento_principal') or 'Sem documento'} | {br_money(member.get('total_contribuido'))} | {', '.join(recurrence_bits) or 'recorrencia detectada'}"
                    )
                groups.append(
                    {
                        "title": f"Bloco familiar: {group['label']} ({'nucleo' if group['scope'] == 'nuclear' else 'familia ampliada'})",
                        "subtitle": "Sobrenomes sugerem um mesmo bloco familiar de contribuicao.",
                        "lines": lines,
                    }
                )
        if include_main:
            groups.append(
                {
                    "title": "Contribuintes filtrados",
                    "subtitle": "Tabela principal do relatorio de contribuintes.",
                    "lines": [
                        " | ".join(
                            item
                            for item in [
                                str(row["nome"]),
                                "PF" if str(row.get("tipo") or "") == "pf" else "PJ",
                                str(row.get("documento_principal") or "Sem documento"),
                                str(row.get("pessoa_nome") or "Sem vinculo"),
                                f"Lanc. {moneyless_int(row.get('contribuicoes_qtd'))}",
                                br_money(row.get("total_contribuido")),
                                f"Ultimo {br_date(row.get('ultima_contribuicao')) or '-'}",
                            ]
                            if item
                        )
                        for row in data.get("rows", [])
                    ],
                }
            )
        return {
            "section": section_key,
            "title": "Relatorio combinado de contribuintes",
            "subtitle": "Visao aberta a partir dos marcadores escolhidos no dashboard.",
            "groups": groups,
            "empty": "Nenhum dado encontrado para a combinacao escolhida.",
        }
    if section_key == "family_links":
        groups: list[dict[str, object]] = []
        for block in data.get("family_links", []):
            contributor = dict(block["contributor"])
            recurrence = contributor_recurrence_flags(contributor)
            recurrence_bits = []
            if recurrence["weekly"]:
                recurrence_bits.append(f"{recurrence['weeks']} semana(s)")
            if recurrence["multi_competencia"]:
                recurrence_bits.append(f"{recurrence['competencias']} competencia(s)")
            lines = [
                f"{person['nome']} | {'Nucleo' if person['relation'] == 'nuclear' else 'Familia ampliada'} | {person['status']} | {format_system_id(person['id'])} | {format_member_code(person['codigo_interno']) or 'Sem numero'}"
                for person in block["matches"]
            ]
            groups.append(
                {
                    "title": str(contributor.get("nome") or ""),
                    "subtitle": " | ".join(
                        item
                        for item in [
                            f"Documento {contributor.get('documento_principal') or 'sem documento'}",
                            f"Total {br_money(contributor.get('total_contribuido'))}",
                            f"Recorrencia {', '.join(recurrence_bits) or 'detectada'}",
                        ]
                        if item
                    ),
                    "lines": lines,
                }
            )
        return {
            "section": section_key,
            "title": "Contribuintes recorrentes ligados a familias cadastradas",
            "subtitle": "Relatorio para descoberta de conjuge, filhos e outros familiares ainda fora do cadastro principal.",
            "groups": groups,
            "empty": "Nenhuma sugestao familiar encontrada com os filtros atuais.",
        }
    if section_key == "family_groups":
        groups = []
        for group in data.get("family_groups", []):
            lines = []
            for member in group["members"]:
                recurrence = contributor_recurrence_flags(member)
                recurrence_bits = []
                if recurrence["weekly"]:
                    recurrence_bits.append(f"{recurrence['weeks']} semana(s)")
                if recurrence["multi_competencia"]:
                    recurrence_bits.append(f"{recurrence['competencias']} competencia(s)")
                lines.append(
                    f"{member['nome']} | {member.get('documento_principal') or 'Sem documento'} | {br_money(member.get('total_contribuido'))} | {', '.join(recurrence_bits) or 'recorrencia detectada'}"
                )
            groups.append(
                {
                    "title": f"{group['label']} ({'nucleo' if group['scope'] == 'nuclear' else 'familia ampliada'})",
                    "subtitle": "Sobrenomes sugerem um mesmo bloco familiar de contribuicao.",
                    "lines": lines,
                }
            )
        return {
            "section": section_key,
            "title": "Blocos familiares sugeridos",
            "subtitle": "Relatorio estrategico de nucleos que podem estar contribuindo por toda a casa.",
            "groups": groups,
            "empty": "Nenhum bloco familiar sugerido com os filtros atuais.",
        }
    groups = [
        {
            "title": "Contribuintes filtrados",
            "subtitle": "Tabela principal do relatorio de contribuintes.",
            "lines": [
                " | ".join(
                    item
                    for item in [
                        str(row["nome"]),
                        "PF" if str(row.get("tipo") or "") == "pf" else "PJ",
                        str(row.get("documento_principal") or "Sem documento"),
                        str(row.get("pessoa_nome") or "Sem vinculo"),
                        f"Lanc. {moneyless_int(row.get('contribuicoes_qtd'))}",
                        br_money(row.get("total_contribuido")),
                        f"Ultimo {br_date(row.get('ultima_contribuicao')) or '-'}",
                    ]
                    if item
                )
                for row in data.get("rows", [])
            ],
        }
    ]
    return {
        "section": section_key,
        "title": "Tabela principal de contribuintes",
        "subtitle": "Relatorio geral do cadastro auxiliar com filtros estrategicos aplicados.",
        "groups": groups,
        "empty": "Nenhum contribuinte encontrado com os filtros atuais.",
    }


def build_contributor_report_pdf(
    report_title: str,
    report_subtitle: str,
    filter_label: str,
    groups: list[dict[str, object]],
    empty_text: str,
) -> bytes:
    page_width = 595.28
    page_height = 841.89
    margin = 36.0
    top_margin = 40.0
    bottom_margin = 42.0
    content_width = page_width - (margin * 2)
    line_height = 11.2
    palette = {
        "ink": (0.15, 0.18, 0.24),
        "muted": (0.42, 0.46, 0.52),
        "line": (0.86, 0.82, 0.76),
        "panel": (0.98, 0.96, 0.92),
        "accent": (0.16, 0.42, 0.34),
    }
    pages: list[list[str]] = []
    current_ops: list[str] = []
    current_y = 0.0

    def draw_text(x: float, y_top: float, text: str, font: str = "F1", size: int = 10, color: tuple[float, float, float] | None = None) -> None:
        if color:
            current_ops.append(pdf_color_command("rg", color))
        current_ops.append("BT")
        current_ops.append(f"/{font} {size} Tf")
        current_ops.append(f"1 0 0 1 {x:.2f} {page_height - y_top:.2f} Tm")
        current_ops.append((pdf_text_literal(text) + b" Tj").decode("latin-1"))
        current_ops.append("ET")

    def draw_line(x1: float, y1_top: float, x2: float, y2_top: float, color: tuple[float, float, float], width: float = 0.6) -> None:
        current_ops.append(pdf_color_command("RG", color))
        current_ops.append(f"{width:.2f} w")
        current_ops.append(f"{x1:.2f} {page_height - y1_top:.2f} m {x2:.2f} {page_height - y2_top:.2f} l S")

    def start_page() -> None:
        nonlocal current_ops, current_y
        if current_ops:
            pages.append(current_ops)
        current_ops = []
        current_y = top_margin
        draw_text(margin, current_y, report_title, font="F2", size=18, color=palette["ink"])
        current_y += 18
        for line in pdf_wrapped_lines(report_subtitle, content_width, 10, max_lines=3):
            draw_text(margin, current_y, line, font="F1", size=10, color=palette["muted"])
            current_y += line_height
        for line in pdf_wrapped_lines(filter_label, content_width, 9, max_lines=4):
            draw_text(margin, current_y, line, font="F1", size=9, color=palette["accent"])
            current_y += line_height
        current_y += 8
        draw_line(margin, current_y, page_width - margin, current_y, palette["line"], 0.8)
        current_y += 12

    def ensure_space(height: float) -> None:
        nonlocal current_y
        if current_y + height <= page_height - bottom_margin:
            return
        start_page()

    start_page()
    rendered_any = False
    for group in groups:
        lines = list(group.get("lines") or [])
        if not lines:
            continue
        rendered_any = True
        title_lines = pdf_wrapped_lines(group.get("title"), content_width, 12, bold=True, max_lines=3)
        subtitle_lines = pdf_wrapped_lines(group.get("subtitle"), content_width, 9, max_lines=4)
        block_height = (len(title_lines) * 13.0) + (len(subtitle_lines) * 11.0) + 12.0
        ensure_space(block_height)
        for line in title_lines:
            draw_text(margin, current_y, line, font="F2", size=12, color=palette["ink"])
            current_y += 13.0
        for line in subtitle_lines:
            draw_text(margin, current_y, line, font="F1", size=9, color=palette["muted"])
            current_y += 11.0
        current_y += 4
        for item in lines:
            wrapped = pdf_wrapped_lines(f"- {item}", content_width - 6, 9, max_lines=4)
            ensure_space((len(wrapped) * 10.5) + 6.0)
            for index, line in enumerate(wrapped):
                draw_text(margin + 6, current_y, line, font="F1", size=9, color=palette["ink"])
                current_y += 10.5
            current_y += 2
        current_y += 8
        draw_line(margin, current_y, page_width - margin, current_y, palette["line"], 0.5)
        current_y += 10
    if not rendered_any:
        ensure_space(40.0)
        draw_text(margin, current_y, empty_text, font="F1", size=10, color=palette["muted"])
        current_y += 16
    total_pages = len(pages) + (1 if current_ops else 0)
    if current_ops:
        pages.append(current_ops)
    for index, page_ops in enumerate(pages, start=1):
        footer_y = page_height - 26.0
        page_ops.append(pdf_color_command("rg", palette["muted"]))
        page_ops.append("BT")
        page_ops.append("/F1 8 Tf")
        page_ops.append(f"1 0 0 1 {page_width - margin - 52:.2f} {footer_y:.2f} Tm")
        page_ops.append((pdf_text_literal(f"Pagina {index}/{total_pages}") + b" Tj").decode("latin-1"))
        page_ops.append("ET")
    return build_pdf_from_operations(pages)


def input_field(name: str, label: str, value: object, input_type: str = "text", css_class: str = "") -> str:
    return (
        f"<label class='{css_class}'>{h(label)}"
        f"<input type='{h(input_type)}' name='{h(name)}' value='{h(value)}'>"
        "</label>"
    )


def textarea_field(name: str, label: str, value: object, css_class: str = "") -> str:
    return f"<label class='{css_class}'>{h(label)}<textarea name='{h(name)}'>{h(value)}</textarea></label>"


def field_card(label: str, value: object, css_class: str = "") -> str:
    content = h(value) if str(value or "").strip() else "<span class='hint'>Nao informado</span>"
    return f"<div class='field {css_class}'><b>{h(label)}</b><span>{content}</span></div>"


def field_card_html(label: str, value_html: str, css_class: str = "") -> str:
    return f"<div class='field {css_class}'><b>{h(label)}</b><span>{value_html}</span></div>"


def text_or_hint(value: object, fallback: str = "Nao informado") -> str:
    text = str(value or "").strip()
    return h(text) if text else f"<span class='hint'>{h(fallback)}</span>"


def custom_dict(rows: list[sqlite3.Row]) -> dict[str, sqlite3.Row]:
    return {str(row["chave"]): row for row in rows}


def custom_text(rows_by_key: dict[str, sqlite3.Row], key: str) -> str:
    row = rows_by_key.get(key)
    return custom_value(row) if row else ""


def render_layout(title: str, body: str, active: str = "") -> str:
    nav = [
        ("inicio", "/", "Inicio"),
        ("importacoes", "/importacoes", "Importacoes"),
        ("pessoas", "/pessoas", "Pessoas"),
        ("pix", "/pix", "PIX"),
        ("extratos", "/extratos", "Extratos"),
        ("contribuintes", "/contribuintes", "Contribuintes"),
        ("contribuicoes", "/contribuicoes", "Contribuicoes"),
        ("recibos", "/recibos", "Recibos"),
        ("auditoria", "/auditoria", "Auditoria"),
    ]
    links = "".join(
        f"<a class='{'active' if active == key else ''}' href='{url}'>{label}</a>" for key, url, label in nav
    )
    logo_html = f"<img class='brand-logo' src='{BRAND_LOGO_URL}' alt='{h(APP_TITLE)}'>" if brand_logo_available() else ""
    brand_header = f"""
      <div class="brand-shell">
        {logo_html}
        <div class="brand-copy">
          <div class="brand-title">{h(APP_TITLE)}</div>
          <div class="brand-subtitle">{h(APP_SUBTITLE)} | {h(title)}</div>
        </div>
      </div>
    """
    print_brand = f"""
      <div class="print-brand">
        {logo_html}
        <div class="print-brand-copy">
          <div class="print-brand-title">{h(APP_TITLE)}</div>
          <div class="print-brand-subtitle">{h(APP_SUBTITLE)}</div>
          <div class="print-brand-page">{h(title)}</div>
        </div>
      </div>
    """
    return f"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{h(title)} | {h(APP_TITLE)}</title>
  <style>
    :root {{
      --ink:#202837;
      --muted:#667085;
      --line:#ded8ce;
      --paper:#fffdf8;
      --soft:#f4efe6;
      --green:#2f735f;
      --gold:#ba7b2b;
      --red:#a9443f;
      --blue:#254f7a;
    }}
    * {{ box-sizing:border-box; }}
    body {{
      margin:0;
      color:var(--ink);
      background:
        radial-gradient(circle at top left, rgba(47,115,95,.12), transparent 32rem),
        linear-gradient(135deg, #f9f5ed 0%, #fffdf8 45%, #eef5f1 100%);
      font-family: Georgia, "Times New Roman", serif;
    }}
    header {{
      display:flex;
      justify-content:space-between;
      align-items:center;
      padding:22px 34px;
      background:rgba(255,253,248,.86);
      border-bottom:1px solid var(--line);
      position:sticky;
      top:0;
      backdrop-filter:blur(10px);
      z-index:3;
    }}
    .brand-shell {{ display:flex; align-items:center; gap:16px; min-width:0; }}
    .brand-logo {{
      width:108px;
      height:auto;
      border-radius:22px;
      box-shadow:0 12px 28px rgba(33, 40, 56, .14);
      background:rgba(255,255,255,.72);
      flex:0 0 auto;
    }}
    .brand-copy {{ min-width:0; }}
    .brand-title {{ font-size:24px; font-weight:900; letter-spacing:.02em; }}
    .brand-subtitle {{ margin-top:4px; color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.16em; font-weight:800; }}
    .print-brand {{
      display:none;
      align-items:center;
      gap:18px;
      padding:0 0 16px;
      margin-bottom:18px;
      border-bottom:1px solid var(--line);
    }}
    .print-brand .brand-logo {{
      width:110px;
      border-radius:18px;
      box-shadow:none;
      background:white;
    }}
    .print-brand-copy {{ display:grid; gap:4px; }}
    .print-brand-title {{ font-size:22px; font-weight:900; letter-spacing:.02em; }}
    .print-brand-subtitle, .print-brand-page {{ color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:.16em; font-weight:800; }}
    nav {{ display:flex; gap:10px; flex-wrap:wrap; }}
    nav a, .button {{
      display:inline-flex;
      align-items:center;
      justify-content:center;
      min-height:38px;
      padding:9px 14px;
      border:1px solid var(--line);
      border-radius:999px;
      color:var(--ink);
      background:#fffaf1;
      text-decoration:none;
      font-weight:700;
    }}
    nav a.active, .button.primary {{ background:var(--green); color:white; border-color:var(--green); }}
    .button.small {{ min-height:30px; padding:6px 10px; font-size:13px; }}
    main {{ padding:28px 34px 48px; max-width:1440px; margin:0 auto; }}
    h1 {{ margin:0 0 10px; font-size:34px; }}
    h2 {{ margin:0 0 14px; font-size:24px; }}
    h3 {{ margin:0 0 10px; font-size:19px; }}
    .hint {{ color:var(--muted); font-size:15px; line-height:1.45; }}
    .grid {{ display:grid; grid-template-columns:repeat(4, minmax(0, 1fr)); gap:16px; margin:20px 0; }}
    .card, .panel {{
      border:1px solid var(--line);
      border-radius:22px;
      background:rgba(255,253,248,.82);
      box-shadow:0 18px 40px rgba(38,52,66,.08);
      padding:20px;
    }}
    .card .label {{ color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.13em; font-weight:900; }}
    .card .value {{ font-size:34px; font-weight:900; margin-top:8px; overflow-wrap:anywhere; }}
    .card-link {{ text-decoration:none; color:inherit; display:block; }}
    .card-link:hover .card {{
      transform:translateY(-2px);
      box-shadow:0 22px 48px rgba(38,52,66,.12);
      border-color:rgba(47,115,95,.28);
    }}
    .card {{
      transition:transform .18s ease, box-shadow .18s ease, border-color .18s ease;
    }}
    .panel {{ margin-top:18px; }}
    .profile-hero {{
      display:grid;
      grid-template-columns:220px 1fr;
      gap:20px;
      align-items:center;
      padding:24px;
      margin:18px 0;
      border:1px solid rgba(47,115,95,.25);
      border-radius:28px;
      background:
        radial-gradient(circle at top right, rgba(186,123,43,.18), transparent 28rem),
        linear-gradient(135deg, rgba(47,115,95,.13), rgba(255,253,248,.9));
      box-shadow:0 22px 52px rgba(32,40,55,.1);
    }}
    .avatar {{
      width:86px;
      height:86px;
      border-radius:24px;
      display:grid;
      place-items:center;
      color:white;
      background:linear-gradient(135deg, var(--green), var(--blue));
      font-size:34px;
      font-weight:900;
      letter-spacing:.02em;
    }}
    .member-photo-frame {{
      width:220px;
      aspect-ratio:4 / 5;
      border-radius:26px;
      overflow:hidden;
      border:1px solid rgba(37,79,122,.2);
      background:linear-gradient(180deg, rgba(255,255,255,.65), rgba(236,245,241,.92));
      box-shadow:0 18px 38px rgba(32,40,55,.12);
      display:grid;
      place-items:center;
    }}
    .member-photo {{
      width:100%;
      height:100%;
      object-fit:cover;
      display:block;
    }}
    .photo-placeholder {{
      width:100%;
      height:100%;
      display:grid;
      place-items:center;
      gap:10px;
      padding:18px;
      text-align:center;
      color:var(--muted);
      background:
        radial-gradient(circle at top, rgba(186,123,43,.18), transparent 18rem),
        linear-gradient(180deg, rgba(255,255,255,.85), rgba(238,245,241,.96));
    }}
    .photo-placeholder .avatar {{ width:92px; height:92px; margin:0 auto; }}
    .photo-note {{
      margin-top:12px;
      font-size:13px;
      color:var(--muted);
      line-height:1.5;
      overflow-wrap:anywhere;
    }}
    .hero-title {{ display:flex; align-items:center; justify-content:space-between; gap:16px; flex-wrap:wrap; }}
    .hero-title h1 {{ margin:0; }}
    .hero-meta {{ display:flex; gap:8px; flex-wrap:wrap; margin-top:10px; }}
    .status-strip {{ display:grid; grid-template-columns:repeat(4, minmax(0, 1fr)); gap:12px; margin:12px 0 18px; }}
    .mini-card {{
      padding:14px 16px;
      border:1px solid var(--line);
      border-radius:18px;
      background:rgba(255,253,248,.92);
    }}
    .mini-card .label {{ color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:.12em; font-weight:900; }}
    .mini-card .value {{ margin-top:6px; font-size:18px; font-weight:900; }}
    .profile-layout {{ display:grid; grid-template-columns:minmax(0, 1.2fr) minmax(360px, .8fr); gap:18px; align-items:start; }}
    .section-head {{ display:flex; justify-content:space-between; align-items:flex-start; gap:12px; margin-bottom:12px; }}
    .section-head h2 {{ margin:0; }}
    .field-grid {{ display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:10px; }}
    .field {{
      border:1px solid #eee6da;
      border-radius:16px;
      padding:12px;
      background:#fffaf1;
      min-height:72px;
    }}
    .field b {{ display:block; color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:.1em; margin-bottom:6px; }}
    .field span {{ font-size:17px; font-weight:800; overflow-wrap:anywhere; }}
    .field.wide-field {{ grid-column:1 / -1; }}
    .timeline {{ display:grid; gap:10px; }}
    .timeline-item {{ border-left:4px solid var(--green); padding:10px 12px; background:#fffaf1; border-radius:0 14px 14px 0; }}
    .timeline-item b {{ display:block; }}
    .accessory-grid {{ display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:10px; }}
    .accessory-item {{ padding:10px 12px; border:1px solid #eee6da; border-radius:14px; background:white; }}
    .accessory-item b {{ display:block; color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:.1em; margin-bottom:5px; }}
    .filters {{ display:flex; gap:10px; align-items:end; flex-wrap:wrap; margin:18px 0; }}
    .filters .wide {{ flex:1 1 100%; }}
    .checkbox-grid {{
      display:grid;
      grid-template-columns:repeat(auto-fit, minmax(220px, 1fr));
      gap:10px;
      width:100%;
    }}
    .checkbox-grid label {{
      display:flex;
      align-items:center;
      gap:10px;
      padding:12px 14px;
      border:1px solid #eee6da;
      border-radius:16px;
      background:#fffaf1;
      color:var(--ink);
      font-size:14px;
      font-weight:800;
      letter-spacing:0;
      text-transform:none;
    }}
    .checkbox-grid input {{
      min-width:auto;
      width:18px;
      height:18px;
      min-height:auto;
      margin:0;
      accent-color:var(--green);
    }}
    .check-grid {{
      display:grid;
      grid-template-columns:repeat(auto-fit, minmax(220px, 1fr));
      gap:10px;
      margin-top:12px;
    }}
    .check-item {{
      display:flex;
      align-items:center;
      gap:10px;
      padding:12px 14px;
      border:1px solid #eee6da;
      border-radius:16px;
      background:#fffaf1;
      color:var(--ink);
      font-size:14px;
      font-weight:800;
      letter-spacing:0;
      text-transform:none;
    }}
    .check-item input {{
      min-width:auto;
      width:18px;
      height:18px;
      min-height:auto;
      margin:0;
      accent-color:var(--green);
    }}
    label {{ display:grid; gap:6px; color:var(--muted); font-weight:800; font-size:12px; text-transform:uppercase; letter-spacing:.1em; }}
    input, select, textarea {{
      min-height:40px;
      min-width:180px;
      border:1px solid var(--line);
      border-radius:12px;
      padding:8px 10px;
      background:white;
      color:var(--ink);
      font:inherit;
    }}
    textarea {{ min-height:84px; resize:vertical; }}
    .form-grid {{ display:grid; grid-template-columns:repeat(3, minmax(0, 1fr)); gap:14px; }}
    .form-grid label {{ min-width:0; }}
    .form-grid input, .form-grid select, .form-grid textarea {{ width:100%; min-width:0; }}
    .wide {{ grid-column:1 / -1; }}
    table {{ width:100%; border-collapse:collapse; background:white; border-radius:16px; overflow:hidden; }}
    th, td {{ border-bottom:1px solid #ece5da; padding:11px 12px; text-align:left; vertical-align:top; }}
    th {{ background:#f4eadb; font-size:12px; text-transform:uppercase; letter-spacing:.12em; }}
    tr:hover td {{ background:#fbf7ef; }}
    .right {{ text-align:right; }}
    .badge {{
      display:inline-flex;
      align-items:center;
      padding:4px 9px;
      border-radius:999px;
      background:#eee7db;
      color:var(--ink);
      font-size:12px;
      font-weight:850;
      margin:2px;
    }}
    .badge.ok {{ background:#dfeee7; color:#235c4a; }}
    .badge.warn {{ background:#fff1cc; color:#8a5b14; }}
    .badge.danger {{ background:#f8d8d4; color:#8a2f2b; }}
    .badge.info {{ background:#dfeaf6; color:#254f7a; }}
    .badge.cent {{
      color:#fff;
      border:1px solid rgba(0,0,0,.08);
      box-shadow:inset 0 1px 0 rgba(255,255,255,.12);
    }}
    .badge.cent-00 {{ background:#5f5448; }}
    .badge.cent-01 {{ background:#9a2141; }}
    .badge.cent-02 {{ background:#2157b8; }}
    .badge.cent-03 {{ background:#15734f; }}
    .badge.cent-04 {{ background:#b25d19; }}
    .badge.cent-05 {{ background:#3e4fa8; }}
    .badge.cent-06 {{ background:#0e7894; }}
    .badge.cent-07 {{ background:#944d16; }}
    .badge.cent-08 {{ background:#6f5a17; }}
    .badge.cent-09 {{ background:#b03b35; }}
    .badge.cent-10 {{ background:#2e6d53; }}
    .badge.cent-11 {{ background:#7d3b8f; }}
    .badge.cent-12 {{ background:#c04a16; }}
    .badge.cent-generic {{ background:#6b5f56; }}
    .detail-grid {{ display:grid; grid-template-columns:1.1fr .9fr; gap:18px; align-items:start; }}
    .stack {{ display:grid; gap:14px; }}
    .kv {{ display:grid; grid-template-columns:180px 1fr; gap:8px; padding:7px 0; border-bottom:1px solid #eee8df; }}
    .kv b {{ color:var(--muted); }}
    .audit-row aviso, .audit-row info {{ display:block; }}
    .empty {{ padding:18px; color:var(--muted); background:white; border:1px dashed var(--line); border-radius:14px; }}
    .actions {{ display:flex; gap:8px; flex-wrap:wrap; }}
    .print-hide {{ }}
    .period-report {{ display:grid; gap:16px; }}
    .period-summary-grid {{
      display:grid;
      grid-template-columns:repeat(4, minmax(0, 1fr));
      gap:12px;
      margin:18px 0;
    }}
    .period-summary-grid.secondary {{ grid-template-columns:repeat(4, minmax(0, 1fr)); }}
    .legend-strip {{
      display:flex;
      gap:8px;
      flex-wrap:wrap;
    }}
    .period-entry-list {{
      display:grid;
      gap:6px;
    }}
    .period-entry-item {{
      display:flex;
      justify-content:space-between;
      gap:10px;
      align-items:flex-start;
      padding:6px 8px;
      border:1px solid #efe7dc;
      border-radius:12px;
      background:#fffaf3;
    }}
    .period-entry-item.single {{
      display:inline-flex;
      justify-content:flex-start;
      padding:0;
      border:none;
      border-radius:0;
      background:transparent;
      gap:10px;
    }}
    .period-entry-item b {{ white-space:nowrap; }}
    .period-entry-count {{
      color:var(--muted);
      font-size:12px;
      text-transform:uppercase;
      letter-spacing:.1em;
      font-weight:900;
      margin-bottom:4px;
    }}
    .period-section-row td {{
      background:#f5efe4;
      color:var(--ink);
      font-size:12px;
      text-transform:uppercase;
      letter-spacing:.12em;
      font-weight:900;
    }}
    .screen-only {{ }}
    .print-only {{ display:none; }}
    .period-print-sheet {{ display:grid; gap:14px; }}
    @media (max-width: 900px) {{
      header {{ align-items:flex-start; flex-direction:column; gap:12px; }}
      .brand-shell {{ width:100%; }}
      .brand-logo {{ width:92px; }}
      main {{ padding:20px; }}
      .grid, .detail-grid, .profile-layout, .status-strip, .field-grid, .accessory-grid, .period-summary-grid {{ grid-template-columns:1fr; }}
      .profile-hero {{ grid-template-columns:1fr; }}
      table {{ font-size:14px; }}
      th, td {{ padding:9px; }}
    }}
    @media print {{
      header, .actions, .filters {{ display:none !important; }}
      body {{ background:white; }}
      main {{ max-width:none; padding:0; }}
      .panel, .card, .profile-hero {{ box-shadow:none; background:white; }}
      a {{ color:inherit; text-decoration:none; }}
      .print-brand {{
        display:flex !important;
        position:fixed;
        top:0;
        left:0;
        right:0;
        padding:16px 24px 12px;
        margin:0;
        background:white;
        z-index:999;
      }}
      main {{ padding-top:122px; }}
      body {{ -webkit-print-color-adjust:exact; print-color-adjust:exact; }}
      .print-hide {{ display:none !important; }}
      .screen-only {{ display:none !important; }}
      .print-only {{ display:block !important; }}
      .period-report {{ display:block; }}
      .period-summary-grid, .legend-strip, .panel, table {{ break-inside:avoid; page-break-inside:avoid; }}
      .period-print-sheet .panel {{ margin-top:10px; }}
    }}
  </style>
</head>
<body>
  <header>
    {brand_header}
    <nav>{links}</nav>
  </header>
  <main>{print_brand}{body}</main>
</body>
</html>"""


def render_home(db: PowerChurchDB) -> str:
    data = db.dashboard()
    pix_pending = db.scalar(
        "SELECT COUNT(*) FROM pix_movimentos WHERE ativo = 1 AND review_status IN ('revisar_pessoa', 'revisar_destinacao', 'revisar_duplicidade')"
    )
    statement_pending = db.scalar(
        "SELECT COUNT(*) FROM extrato_movimentos WHERE ativo = 1 AND review_status IN ('revisar_pessoa', 'revisar_duplicidade')"
    )
    cards = [
        ("Pessoas importadas", data["pessoas"], ""),
        ("Membros ativos", data["membros_ativos"], "ok"),
        ("Membros inativos", data["membros_inativos"], "warn"),
        ("PIX pendentes", pix_pending, "warn" if pix_pending else "ok"),
        ("Extratos pendentes", statement_pending, "warn" if statement_pending else "ok"),
        ("Contribuicoes", data["contribuicoes"], "info"),
        ("Recibos", data["recibos"], "info"),
        ("Pendencias abertas", data["pendencias"], "danger" if data["avisos"] else "info"),
    ]
    cards_html = "".join(
        f"<div class='card'><div class='label'>{h(label)}</div><div class='value'>{value}</div>{badge(cls, cls) if cls else ''}</div>"
        for label, value, cls in cards
    )
    body = f"""
      <h1>Consulta operacional da membresia</h1>
      <div class="hint">Demo local para visualizar a base importada, corrigir cadastros, acompanhar a auditoria e seguir com a operacao normal da igreja.</div>
      <div class="grid">{cards_html}</div>
      <div class="panel">
        <h2>Fluxo recomendado</h2>
        <p class="hint">Use Pessoas para conferir cadastros de membros, frequentadores e visitantes. Use Auditoria para discutir CPFs invalidos, duplicidades, datas invalidas, membros inativos e demais revisoes sem atrasar a construcao do sistema. Quando uma ficha passa a membro, o sistema gera um numero operacional unico e incremental, inclusive nas futuras promocoes em lote.</p>
        <div class="actions">
          <a class="button primary" href="/pessoas">Ver pessoas importadas</a>
          <a class="button primary" href="/pessoas/importar">Importar pessoas</a>
          <a class="button primary" href="/pessoa/nova">Nova pessoa</a>
          <a class="button primary" href="/importacoes">Central de importacoes</a>
          <a class="button" href="/pix">PIX Sicoob</a>
          <a class="button" href="/extratos">Extratos bancarios</a>
          <a class="button" href="/contribuintes">Abrir contribuintes</a>
          <a class="button" href="/contribuicoes">Abrir contribuicoes</a>
          <a class="button" href="/recibos">Abrir recibos</a>
          <a class="button" href="/auditoria">Ver auditoria do cadastro</a>
        </div>
      </div>
    """
    return render_layout("Inicio", body, "inicio")


def render_imports_center(db: PowerChurchDB, query: dict[str, list[str]]) -> str:
    default_org = db.default_organization_id()
    rules = db.pix_rules(default_org)
    pix_lots = db.pix_lots(12)
    statement_lots = db.statement_lots(12)
    pix_pending = db.scalar(
        "SELECT COUNT(*) FROM pix_movimentos WHERE ativo = 1 AND review_status IN ('revisar_pessoa', 'revisar_destinacao', 'revisar_duplicidade')"
    )
    statement_pending = db.scalar(
        "SELECT COUNT(*) FROM extrato_movimentos WHERE ativo = 1 AND review_status IN ('revisar_pessoa', 'revisar_destinacao', 'revisar_duplicidade')"
    )
    pix_special = db.scalar("SELECT COUNT(*) FROM pix_movimentos WHERE ativo = 1 AND regra_id IS NOT NULL")
    statement_special = db.scalar("SELECT COUNT(*) FROM extrato_movimentos WHERE ativo = 1 AND regra_id IS NOT NULL")
    pix_financial = db.scalar("SELECT COUNT(*) FROM pix_movimentos WHERE ativo = 1 AND imported_contribution_id IS NOT NULL")
    statement_financial = db.scalar("SELECT COUNT(*) FROM extrato_movimentos WHERE ativo = 1 AND imported_contribution_id IS NOT NULL")
    statement_association = db.scalar(
        f"""
        SELECT COUNT(*)
        FROM extrato_movimentos em
        LEFT JOIN contribuicoes ic ON ic.id = em.imported_contribution_id AND ic.ativo = 1
        LEFT JOIN contribuintes ict ON ict.id = ic.contribuinte_id
        WHERE em.ativo = 1
          AND {statement_association_pending_expr('em', 'ic', 'ict')}
        """
    )
    pix_association = db.scalar(
        f"""
        SELECT COUNT(*)
        FROM pix_movimentos m
        LEFT JOIN contribuicoes ico ON ico.id = m.imported_contribution_id
        LEFT JOIN contribuintes ict ON ict.id = ico.contribuinte_id
        WHERE m.ativo = 1
          AND {pix_association_pending_expr('m', 'ico', 'ict')}
        """
    )
    pix_lot_total = db.scalar("SELECT COUNT(*) FROM pix_lotes")
    statement_lot_total = db.scalar("SELECT COUNT(*) FROM extrato_lotes")
    people_lot_total = db.scalar(
        """
        SELECT COUNT(*)
        FROM import_lotes
        WHERE tipo_importacao IN ('pessoas_membros', 'pessoas_complementar_incremental')
        """
    )
    cards = [
        ("Extratos bancarios", statement_lot_total, "info"),
        ("PIX historicos", pix_lot_total, "info"),
        ("Lotes de pessoas", people_lot_total, "ok" if people_lot_total else "warn"),
        ("Movimentos em saneamento", pix_pending + statement_pending, "warn" if pix_pending + statement_pending else "ok"),
        ("Destinacoes especiais", pix_special + statement_special, "warn" if pix_special + statement_special else "ok"),
        ("Pend. associacao", pix_association + statement_association, "danger" if pix_association + statement_association else "ok"),
        ("Lancados financeiramente", pix_financial + statement_financial, "ok"),
        ("Regras por centavos", len(rules), "warn"),
    ]
    cards_html = "".join(
        f"<div class='card'><div class='label'>{h(label)}</div><div class='value'>{h(value)}</div>{badge(cls, cls) if cls else ''}</div>"
        for label, value, cls in cards
    )
    rule_preview = "".join(
        f"<tr><td>{pix_rule_badge(row['codigo_centavos'], row['nome_destinacao'])}</td><td>{h(row['tipo_nome'] or 'Sem tipo')}</td><td>{badge('Ativa' if row['ativo'] else 'Inativa', 'ok' if row['ativo'] else 'warn')}</td></tr>"
        for row in rules[:12]
    )
    unified_lots: list[dict[str, object]] = []
    for row in pix_lots:
        counts = db.pix_lot_counts(moneyless_int(row["id"]))
        association = db.pix_lot_association_counts(moneyless_int(row["id"]))
        unified_lots.append(
            {
                "id": moneyless_int(row["id"]),
                "origem": "PIX Sicoob",
                "arquivo": str(row["nome_arquivo"]),
                "periodo": f"{br_date(row['periodo_inicio'])} ate {br_date(row['periodo_fim'])}",
                "movimentos": moneyless_int(row["total_movimentos"]),
                "valor": br_money(row["total_valor"]),
                "status": pix_lot_status_badge(row["status"]),
                "pendencias": moneyless_int(counts.get("revisar_pessoa")) + moneyless_int(counts.get("revisar_destinacao")) + moneyless_int(counts.get("revisar_duplicidade")) + moneyless_int(association.get("associacao")),
                "dest_especiais": db.scalar("SELECT COUNT(*) FROM pix_movimentos WHERE lote_id = ? AND ativo = 1 AND regra_id IS NOT NULL", (row["id"],)),
                "url": f"/pix/lote?id={row['id']}",
                "atualizado": str(row["atualizado_em"] or ""),
                "criado": str(row["criado_em"] or ""),
            }
        )
    for row in statement_lots:
        counts = db.statement_lot_review_counts(moneyless_int(row["id"]))
        financial = db.statement_lot_financial_counts(moneyless_int(row["id"]))
        unified_lots.append(
            {
                "id": moneyless_int(row["id"]),
                "origem": f"Extrato {row['banco']}",
                "arquivo": str(row["nome_arquivo"]),
                "periodo": f"{br_date(row['periodo_inicio'])} ate {br_date(row['periodo_fim'])}",
                "movimentos": moneyless_int(row["total_movimentos"]),
                "valor": br_money(row["total_valor"]),
                "status": pix_lot_status_badge(row["status"]),
                "pendencias": moneyless_int(counts.get("revisar_pessoa")) + moneyless_int(counts.get("revisar_destinacao")) + moneyless_int(counts.get("revisar_duplicidade")) + moneyless_int(financial.get("sem_associacao")),
                "dest_especiais": db.scalar("SELECT COUNT(*) FROM extrato_movimentos WHERE lote_id = ? AND ativo = 1 AND regra_id IS NOT NULL", (row["id"],)),
                "url": f"/extratos/lote?id={row['id']}",
                "atualizado": str(row["atualizado_em"] or ""),
                "criado": str(row["criado_em"] or ""),
            }
        )
    unified_lots.sort(key=lambda row: (str(row["criado"]), moneyless_int(row["id"])), reverse=True)
    statement_display_lots = [row for row in unified_lots if str(row["url"]).startswith("/extratos/")]
    pix_display_lots = [row for row in unified_lots if str(row["url"]).startswith("/pix/")]

    def import_lot_table_row(row: dict[str, object]) -> str:
        return (
            "<tr>"
            f"<td><b>{h(row['origem'])}</b><div class='hint'>{h(row['arquivo'])}</div></td>"
            f"<td>{h(row['periodo'])}</td>"
            f"<td class='right'>{h(row['movimentos'])}</td>"
            f"<td class='right'>{h(row['valor'])}</td>"
            f"<td>{h(row['dest_especiais'])}</td>"
            f"<td>{h(row['pendencias'])}</td>"
            f"<td>{row['status']}</td>"
            f"<td><a class='button small primary' href='{h(row['url'])}'>Abrir lote</a></td>"
            "</tr>"
        )

    statement_lot_rows = [import_lot_table_row(row) for row in statement_display_lots[:14]]
    pix_lot_rows = [import_lot_table_row(row) for row in pix_display_lots[:14]]
    body = f"""
      <div class="actions">
        <a class="button" href="/">Inicio</a>
        <a class="button primary" href="/pessoas/importar">Importar pessoas</a>
        <a class="button" href="/pix">PIX Sicoob</a>
        <a class="button" href="/extratos">Extratos bancarios</a>
        <a class="button" href="/pix/regras">Regras por centavos</a>
      </div>
      {message_box(query)}
      <h1>Central de importacoes</h1>
      <div class="hint">Aqui concentramos as importacoes do sistema sem misturar os conceitos: extratos bancarios, PIX historicos e lotes de pessoas aparecem separados no resumo. O parser muda conforme a origem, mas a regra operacional, a tabela de centavos, os filtros e a fila de saneamento seguem o mesmo padrao para bancos.</div>
      <div class="grid">{cards_html}</div>
      <div class="detail-grid">
        <div class="panel">
          <div class="section-head"><h2>Pessoas</h2><span>{badge('cadastro', 'ok')}</span></div>
          <div class="hint">Importa complementos de membros, frequentadores e visitantes. A importacao e incremental: nao apaga fichas existentes, preenche campos vazios e envia conflitos para auditoria.</div>
          <form method="post" action="/pessoas/importar" enctype="multipart/form-data">
            <div class="form-grid">
              <label class="wide">Planilha Excel de pessoas<input type="file" name="planilha_xlsx" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" required></label>
            </div>
            <div class="actions">
              <button class="button primary" type="submit">Importar pessoas</button>
              <a class="button" href="/pessoas/importar">Abrir importacao de pessoas</a>
            </div>
          </form>
        </div>
        <div class="panel">
          <div class="section-head"><h2>PIX Sicoob</h2><span>{badge('parser dedicado', 'info')}</span></div>
          <div class="hint">Importa PDF de PIX com CPF/CNPJ mascarado, aplica a tabela de centavos e ja cria os lancamentos financeiros.</div>
          <form method="post" action="/pix/lotes/upload" enctype="multipart/form-data">
            <input type="hidden" name="return_to" value="/importacoes">
            <div class="form-grid">
              <label class="wide">PDF do extrato PIX<input type="file" name="extrato_pdf" accept=".pdf,application/pdf" required></label>
            </div>
            <div class="actions">
              <button class="button primary" type="submit">Criar lote PIX</button>
              <a class="button" href="/pix">Abrir modulo PIX</a>
            </div>
          </form>
        </div>
        <div class="panel">
          <div class="section-head"><h2>Extrato Bradesco</h2><span>{badge('parser dedicado', 'info')}</span></div>
          <div class="hint">Importa extrato bancario de creditos de terceiros, reaproveita a mesma regra de centavos e saneamento do PIX, mas com leitura propria do layout Bradesco.</div>
          <form method="post" action="/extratos/lotes/upload" enctype="multipart/form-data">
            <input type="hidden" name="layout_code" value="BRADESCO_EXTRATO">
            <input type="hidden" name="return_to" value="/importacoes">
            <div class="form-grid">
              <label class="wide">PDF do extrato bancario<input type="file" name="extrato_pdf" accept=".pdf,application/pdf" required></label>
            </div>
            <div class="actions">
              <button class="button primary" type="submit">Criar lote Bradesco</button>
              <a class="button" href="/extratos">Abrir modulo Bradesco</a>
            </div>
          </form>
        </div>
        <div class="panel">
          <div class="section-head"><h2>Extrato Santander</h2><span>{badge('parser automatico', 'info')}</span></div>
          <div class="hint">Importa PIX recebidos do Santander pelos dois formatos do banco: consolidado antigo e nao consolidado recente. Como o banco nao informa nome, a associacao usa CPF/CNPJ completo.</div>
          <form method="post" action="/extratos/lotes/upload" enctype="multipart/form-data">
            <input type="hidden" name="layout_code" value="SANTANDER_AUTO">
            <input type="hidden" name="return_to" value="/importacoes">
            <div class="form-grid">
              <label class="wide">PDF do extrato Santander<input type="file" name="extrato_pdf" accept=".pdf,application/pdf" required></label>
            </div>
            <div class="actions">
              <button class="button primary" type="submit">Criar lote Santander</button>
              <a class="button" href="/extratos">Abrir modulo de extratos</a>
            </div>
          </form>
        </div>
        <div class="panel">
          <div class="section-head"><h2>Extrato Sicoob</h2><span>{badge('homologacao', 'warn')}</span></div>
          <div class="hint">Novo parser para o extrato de recebimentos do Sicoob. Ele cobre PIX, TED e transferencias com uma leitura mais ampla do que o modulo historico de PIX. Nesta fase, ele existe para homologacao controlada e comparacao de consistencia.</div>
          <form method="post" action="/extratos/lotes/upload" enctype="multipart/form-data">
            <input type="hidden" name="layout_code" value="SICOOB_RECEBIMENTOS">
            <input type="hidden" name="return_to" value="/importacoes">
            <div class="form-grid">
              <label class="wide">PDF do extrato de recebimentos<input type="file" name="extrato_pdf" accept=".pdf,application/pdf" required></label>
            </div>
            <div class="actions">
              <button class="button primary" type="submit">Criar lote Sicoob</button>
              <a class="button" href="/extratos">Abrir modulo de extratos</a>
            </div>
          </form>
        </div>
      </div>
      <div class="detail-grid">
        <div class="panel">
          <div class="section-head"><h2>Lotes de extrato recentes</h2><span>{badge(f'{len(statement_display_lots)} extratos', 'info')}</span></div>
          <table>
            <thead><tr><th>Origem</th><th>Periodo</th><th class="right">Mov.</th><th class="right">Valor</th><th>Dest. esp.</th><th>Pend.</th><th>Status</th><th>Acao</th></tr></thead>
            <tbody>{''.join(statement_lot_rows) if statement_lot_rows else "<tr><td colspan='8'>Nenhum lote de extrato importado ainda.</td></tr>"}</tbody>
          </table>
        </div>
        <div class="panel">
          <div class="section-head"><h2>Tabela de centavos ativa</h2><span>{badge(len(rules), 'warn')}</span></div>
          <div class="hint">Esta tabela passa a ser a camada comum entre bancos. Assim, quando entrarmos com novos parsers, a equipe continua usando a mesma regra de destinacao especial.</div>
          <table>
            <thead><tr><th>Codigo</th><th>Destino</th><th>Status</th></tr></thead>
            <tbody>{rule_preview or "<tr><td colspan='3'>Nenhuma regra de centavos cadastrada.</td></tr>"}</tbody>
          </table>
          <div class="actions" style="margin-top:12px">
            <a class="button" href="/pix/regras">Editar regras de centavos</a>
          </div>
        </div>
      </div>
      <div class="panel">
        <div class="section-head"><h2>PIX historicos</h2><span>{badge(f'{len(pix_display_lots)} lotes', 'info')}</span></div>
        <div class="hint">Estes lotes PIX ficam separados para rastreabilidade, especialmente depois da migracao gradual para extratos de recebimentos mais completos.</div>
        <table>
          <thead><tr><th>Origem</th><th>Periodo</th><th class="right">Mov.</th><th class="right">Valor</th><th>Dest. esp.</th><th>Pend.</th><th>Status</th><th>Acao</th></tr></thead>
          <tbody>{''.join(pix_lot_rows) if pix_lot_rows else "<tr><td colspan='8'>Nenhum lote PIX importado ainda.</td></tr>"}</tbody>
        </table>
      </div>
    """
    return render_layout("Central de importacoes", body, "importacoes")


def people_import_type_label(value: object) -> str:
    mapping = {
        "pessoas_membros": "Importacao inicial de pessoas",
        "pessoas_complementar_incremental": "Complemento incremental",
    }
    return mapping.get(str(value or ""), str(value or "Importacao de pessoas"))


def render_people_import(db: PowerChurchDB, query: dict[str, list[str]]) -> str:
    lots = db.people_import_lots(12)
    total_people = db.scalar("SELECT COUNT(*) FROM pessoas WHERE ativo = 1")
    open_pendencies = db.scalar(
        """
        SELECT COUNT(*)
        FROM import_pendencias ip
        JOIN import_lotes il ON il.id = ip.lote_id
        WHERE ip.resolvido = 0
          AND il.tipo_importacao IN ('pessoas_membros', 'pessoas_complementar_incremental')
        """
    )
    cards = [
        ("Pessoas ativas", total_people, "info"),
        ("Lotes de pessoas", len(lots), "ok" if lots else "warn"),
        ("Pendencias abertas", open_pendencies, "danger" if open_pendencies else "ok"),
    ]
    cards_html = "".join(
        f"<div class='card'><div class='label'>{h(label)}</div><div class='value'>{h(value)}</div>{badge(cls, cls) if cls else ''}</div>"
        for label, value, cls in cards
    )
    lot_rows = []
    for row in lots:
        open_count = moneyless_int(row["pendencias_abertas"])
        people_without_name = moneyless_int(row["pessoas_sem_nome"])
        lot_rows.append(
            "<tr>"
            f"<td><b>{h(people_import_type_label(row['tipo_importacao']))}</b><div class='hint'>Lote #{row['id']} | {h(row['arquivo_nome'])}</div></td>"
            f"<td>{h(br_datetime(row['criado_em']))}</td>"
            f"<td class='right'>{h(row['total_linhas'])}</td>"
            f"<td class='right'>{h(row['linhas_importadas'])}</td>"
            f"<td class='right'>{h(row['linhas_ignoradas'])}</td>"
            f"<td class='right'>{h(row['linhas_com_erro'])}</td>"
            f"<td>{badge(open_count, 'danger' if open_count else 'ok')}</td>"
            f"<td>{badge(people_without_name, 'danger' if people_without_name else 'ok')}</td>"
            f"<td><a class='button small primary' href='/pessoas/importar/lote?id={row['id']}'>Auditar lote</a></td>"
            "</tr>"
        )
    body = f"""
      <div class="actions">
        <a class="button" href="/">Inicio</a>
        <a class="button" href="/pessoas">Pessoas importadas</a>
        <a class="button" href="/importacoes">Central de importacoes</a>
        <a class="button" href="/auditoria">Auditoria</a>
      </div>
      {message_box(query)}
      <h1>Importacao de pessoas</h1>
      <div class="hint">Use esta tela para importar complementos de membros, frequentadores e visitantes. O importador e incremental: ele cria novas fichas quando nao encontra correspondencia, preenche apenas campos vazios em fichas existentes e leva mudancas sensiveis para a auditoria.</div>
      <div class="grid">{cards_html}</div>
      <div class="detail-grid">
        <div class="panel">
          <div class="section-head"><h2>Subir planilha Excel</h2><span>{badge('incremental', 'ok')}</span></div>
          <form method="post" action="/pessoas/importar" enctype="multipart/form-data">
            <div class="form-grid">
              <label class="wide">Planilha .xlsx<input type="file" name="planilha_xlsx" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" required></label>
              <label class="wide check-item"><input type="checkbox" name="allow_duplicate_file" value="1"><span>Permitir reimportar a mesma planilha</span></label>
            </div>
            <div class="hint">Por seguranca, deixe a reimportacao desmarcada. Antes de gravar, o sistema faz backup automatico do banco.</div>
            <div class="actions">
              <button class="button primary" type="submit">Importar pessoas</button>
              <a class="button" href="/pessoas">Ver pessoas importadas</a>
            </div>
          </form>
        </div>
        <div class="panel">
          <div class="section-head"><h2>Politica de seguranca</h2><span>{badge('sem substituicao', 'info')}</span></div>
          <div class="hint">A importacao complementar nao substitui a base. Ela incrementa a base, nao apaga pessoas, nao troca status automaticamente quando isso afetar membresia e registra conflitos para revisao.</div>
          <table>
            <tbody>
              <tr><th>Reconhecimento forte</th><td>CPF valido ou numero de membro.</td></tr>
              <tr><th>Reconhecimento auxiliar</th><td>Nome completo com data de nascimento.</td></tr>
              <tr><th>Ficha existente</th><td>Preenche campos vazios e adiciona contatos/historicos faltantes.</td></tr>
              <tr><th>Conflitos</th><td>Ficam na auditoria de importacao para decisao do operador.</td></tr>
            </tbody>
          </table>
        </div>
      </div>
      <div class="panel">
        <div class="section-head"><h2>Lotes recentes de pessoas</h2><span>{badge(len(lots), 'info')}</span></div>
        <div class="hint">Esta auditoria precisa continuar visivel: se uma planilha entrar com colunas novas, aqui o operador enxerga campos nao mapeados, fichas sem nome e pendencias do lote antes de usar essas pessoas para associar contribuicoes.</div>
        <table>
          <thead><tr><th>Lote</th><th>Criado em</th><th class="right">Linhas</th><th class="right">Importadas</th><th class="right">Ignoradas</th><th class="right">Pend.</th><th>Abertas</th><th>Sem nome</th><th>Auditoria</th></tr></thead>
          <tbody>{''.join(lot_rows) if lot_rows else "<tr><td colspan='9'>Nenhum lote de pessoas importado ainda.</td></tr>"}</tbody>
        </table>
      </div>
    """
    return render_layout("Importacao de pessoas", body, "pessoas")


def render_people_import_lot(db: PowerChurchDB, query: dict[str, list[str]]) -> str:
    lot_id = moneyless_int(query.get("id", ["0"])[0])
    lot = db.get_people_import_lot(lot_id)
    if lot is None:
        return render_layout("Lote de pessoas nao encontrado", "<div class='empty'>Lote de pessoas nao encontrado.</div>", "pessoas")
    status_rows = db.conn.execute(
        """
        SELECT
            COALESCE(p.status, 'sem ficha') AS status,
            COUNT(*) AS quantidade
        FROM import_linhas il
        LEFT JOIN pessoas p ON p.id = il.registro_id
        WHERE il.lote_id = ? AND il.registro_tipo = 'pessoa'
        GROUP BY COALESCE(p.status, 'sem ficha')
        ORDER BY quantidade DESC, status
        """,
        (lot_id,),
    ).fetchall()
    mapping_rows = db.conn.execute(
        """
        SELECT coluna_origem, campo_destino, acao
        FROM import_mapeamentos
        WHERE lote_id = ?
        ORDER BY CASE WHEN acao = 'revisar_depois' THEN 0 ELSE 1 END, coluna_origem
        """,
        (lot_id,),
    ).fetchall()
    pending_rows = db.conn.execute(
        """
        SELECT ip.*, il.numero_linha, p.nome AS pessoa_nome
        FROM import_pendencias ip
        JOIN import_linhas il ON il.id = ip.linha_id
        LEFT JOIN pessoas p ON p.id = il.registro_id
        WHERE ip.lote_id = ?
        ORDER BY ip.resolvido, ip.severidade DESC, il.numero_linha
        LIMIT 80
        """,
        (lot_id,),
    ).fetchall()
    line_rows = db.conn.execute(
        """
        SELECT
            il.id,
            il.numero_linha,
            il.status,
            il.dados_originais_json,
            il.dados_normalizados_json,
            p.id AS pessoa_id,
            p.nome AS pessoa_nome,
            p.cpf,
            p.status AS pessoa_status,
            p.ativo AS pessoa_ativa
        FROM import_linhas il
        LEFT JOIN pessoas p ON p.id = il.registro_id
        WHERE il.lote_id = ?
        ORDER BY il.numero_linha
        LIMIT 250
        """,
        (lot_id,),
    ).fetchall()
    total_lines = moneyless_int(lot["total_linhas"])
    active_people = db.scalar(
        """
        SELECT COUNT(*)
        FROM import_linhas il
        JOIN pessoas p ON p.id = il.registro_id
        WHERE il.lote_id = ? AND p.ativo = 1
        """,
        (lot_id,),
    )
    without_name = db.scalar(
        """
        SELECT COUNT(*)
        FROM import_linhas il
        JOIN pessoas p ON p.id = il.registro_id
        WHERE il.lote_id = ? AND p.ativo = 1 AND p.nome = 'Nome nao informado'
        """,
        (lot_id,),
    )
    review_mappings = sum(1 for row in mapping_rows if str(row["acao"]) == "revisar_depois")
    cards = [
        ("Linhas do arquivo", total_lines, "info"),
        ("Pessoas ativas do lote", active_people, "ok"),
        ("Pendencias abertas", moneyless_int(lot["pendencias_abertas"]), "danger" if moneyless_int(lot["pendencias_abertas"]) else "ok"),
        ("Fichas sem nome", without_name, "danger" if without_name else "ok"),
        ("Campos nao mapeados", review_mappings, "warn" if review_mappings else "ok"),
    ]
    cards_html = "".join(
        f"<div class='card'><div class='label'>{h(label)}</div><div class='value'>{h(value)}</div>{badge(css, css)}</div>"
        for label, value, css in cards
    )
    status_table = "".join(
        f"<tr><td>{badge(row['status'], 'ok' if str(row['status']).startswith('membro') else 'info')}</td><td class='right'>{h(row['quantidade'])}</td></tr>"
        for row in status_rows
    )
    mapping_table = "".join(
        "<tr>"
        f"<td>{h(row['coluna_origem'])}</td>"
        f"<td>{h(row['campo_destino'])}</td>"
        f"<td>{badge('Revisar' if row['acao'] == 'revisar_depois' else 'Mapeado', 'warn' if row['acao'] == 'revisar_depois' else 'ok')}</td>"
        "</tr>"
        for row in mapping_rows[:120]
    )
    pending_table = "".join(
        "<tr>"
        f"<td>{h(row['numero_linha'])}</td>"
        f"<td>{badge(row['severidade'], 'danger' if row['severidade'] == 'erro' else 'warn' if row['severidade'] == 'aviso' else 'info')}</td>"
        f"<td>{h(row['tipo'])}</td>"
        f"<td>{h(row['descricao'])}<div class='hint'>{h(row['acao_sugerida'])}</div></td>"
        f"<td>{badge('Resolvida' if row['resolvido'] else 'Aberta', 'ok' if row['resolvido'] else 'danger')}</td>"
        "</tr>"
        for row in pending_rows
    )
    line_table_parts = []
    for row in line_rows:
        original_name = ""
        try:
            original = json.loads(row["dados_originais_json"] or "{}")
            original_name = normalize_query(original.get("Nome completo") or original.get("Nome Completo") or original.get("nome"))
        except json.JSONDecodeError:
            original_name = ""
        normalized = {}
        try:
            normalized = json.loads(row["dados_normalizados_json"] or "{}")
        except json.JSONDecodeError:
            normalized = {}
        person_label = (
            f"<a href='/pessoa?id={row['pessoa_id']}'>{h(row['pessoa_nome'])}</a>"
            if moneyless_int(row["pessoa_id"]) and moneyless_int(row["pessoa_ativa"])
            else h(row["pessoa_nome"] or "Sem ficha ativa")
        )
        line_table_parts.append(
            "<tr>"
            f"<td>{h(row['numero_linha'])}</td>"
            f"<td>{h(original_name or '-')}</td>"
            f"<td>{person_label}<div class='hint'>CPF {h(format_cpf(row['cpf']))} | {h(row['pessoa_status'] or '-')}</div></td>"
            f"<td>{badge(row['status'], 'ok' if row['status'] in {'importado', 'atualizado'} else 'warn')}</td>"
            f"<td>{h(normalized.get('acao', '') or '-')}</td>"
            "</tr>"
        )
    body = f"""
      <div class="actions">
        <a class="button" href="/pessoas/importar">Voltar para importacao de pessoas</a>
        <a class="button" href="/importacoes">Central de importacoes</a>
        <a class="button primary" href="/contribuintes/novos-cadastros?people_lot_id={lot_id}">Ver associacoes por este lote</a>
      </div>
      {message_box(query)}
      <h1>Auditoria da importacao de pessoas</h1>
      <div class="hint">Lote #{h(lot['id'])} | {h(people_import_type_label(lot['tipo_importacao']))} | {h(lot['arquivo_nome'])}. Esta tela existe para impedir que uma planilha complementar entre silenciosamente com nomes, status ou colunas mal interpretadas.</div>
      <div class="grid">{cards_html}</div>
      <div class="detail-grid">
        <div class="panel">
          <div class="section-head"><h2>Resumo por status</h2><span>{badge(len(status_rows), 'info')}</span></div>
          <table><thead><tr><th>Status</th><th class="right">Qtd.</th></tr></thead><tbody>{status_table or "<tr><td colspan='2'>Sem pessoas vinculadas ao lote.</td></tr>"}</tbody></table>
        </div>
        <div class="panel">
          <div class="section-head"><h2>Mapeamento de colunas</h2><span>{badge(review_mappings, 'warn' if review_mappings else 'ok')}</span></div>
          <div class="hint">Campos em “Revisar” nao impedem necessariamente a importacao, mas ajudam a perceber mudanca de layout da planilha.</div>
          <table><thead><tr><th>Coluna original</th><th>Destino</th><th>Status</th></tr></thead><tbody>{mapping_table or "<tr><td colspan='3'>Sem mapeamentos registrados.</td></tr>"}</tbody></table>
        </div>
      </div>
      <div class="panel">
        <div class="section-head"><h2>Pendencias do lote</h2><span>{badge(len(pending_rows), 'danger' if pending_rows else 'ok')}</span></div>
        <table><thead><tr><th>Linha</th><th>Severidade</th><th>Tipo</th><th>Descricao</th><th>Status</th></tr></thead><tbody>{pending_table or "<tr><td colspan='5'>Nenhuma pendencia registrada neste lote.</td></tr>"}</tbody></table>
      </div>
      <div class="panel">
        <div class="section-head"><h2>Linhas importadas</h2><span>{badge(len(line_rows), 'info')}</span></div>
        <table><thead><tr><th>Linha</th><th>Nome no arquivo</th><th>Ficha gerada/atualizada</th><th>Status linha</th><th>Acao</th></tr></thead><tbody>{''.join(line_table_parts) if line_table_parts else "<tr><td colspan='5'>Nenhuma linha encontrada.</td></tr>"}</tbody></table>
      </div>
    """
    return render_layout("Auditoria da importacao de pessoas", body, "pessoas")


def render_new_people_associations(db: PowerChurchDB, query: dict[str, list[str]]) -> str:
    requested_lot_ids = [moneyless_int(item) for item in query.get("people_lot_id", []) if moneyless_int(item)]
    selected_lot_ids = db.recent_people_import_lot_ids(requested_lot_ids)
    data = db.new_people_association_candidates(selected_lot_ids, limit=500)
    rows = list(data["rows"])
    summary = dict(data["summary"] or {})
    lots = db.people_import_lots(20)
    selected_set = {moneyless_int(item) for item in data["import_lot_ids"]}
    lot_options = "".join(
        f"<label><input type='checkbox' name='people_lot_id' value='{row['id']}' {'checked' if moneyless_int(row['id']) in selected_set else ''}> Lote #{h(row['id'])} · {h(row['arquivo_nome'])}</label>"
        for row in lots
    )
    cards = [
        ("Lotes usados", len(selected_set), "info"),
        ("Pessoas novas analisadas", data["people_count"], "info"),
        ("Associacoes sugeridas", summary.get("total_rows", 0), "warn" if summary.get("total_rows", 0) else "ok"),
        ("Matches fortes", summary.get("strong", 0), "ok"),
        ("Provaveis", summary.get("probable", 0), "warn" if summary.get("probable", 0) else "ok"),
        ("Auditoria manual", summary.get("audit", 0), "danger" if summary.get("audit", 0) else "ok"),
        ("Eventos cobertos", summary.get("events", 0), "info"),
        ("Valor coberto", br_money(summary.get("value", 0)), "ok" if summary.get("value", 0) else "info"),
    ]
    cards_html = "".join(
        f"<div class='card'><div class='label'>{h(label)}</div><div class='value'>{h(value)}</div>{badge(css, css)}</div>"
        for label, value, css in cards
    )

    def category_badge(value: object) -> str:
        category = normalize_query(value)
        if category == "forte":
            return badge("Match forte", "ok")
        if category == "provavel":
            return badge("Provavel", "warn")
        return badge("Auditoria", "danger")

    table_rows = []
    return_to = "/contribuintes/novos-cadastros"
    if selected_set:
        return_to += "?" + urllib.parse.urlencode([("people_lot_id", str(item)) for item in sorted(selected_set)], doseq=True)
    for row in rows:
        lot_hint = " | ".join(row.get("lot_labels") or [])
        source_hint = " | ".join(row.get("sources") or [])
        sample_hint = ", ".join(str(item) for item in row.get("sample_contribution_ids") or [])
        score_text = f"{float(row['score']):.2f}"
        table_rows.append(
            "<tr>"
            f"<td><a href='/contribuinte?id={row['contributor_id']}'>{h(row['contributor_name'])}</a><div class='hint'>{h(row['document'] or 'Sem documento')} | {h(source_hint)} | {h(lot_hint)}</div></td>"
            f"<td><a href='/pessoa?id={row['person_id']}'>{h(row['person_name'])}</a><div class='hint'>{h(format_system_id(row['person_id']))}</div></td>"
            f"<td>{category_badge(row['category'])}<div class='hint'>{h(row['confidence'])} | score {h(score_text)}</div></td>"
            f"<td class='right'>{h(row['count'])}</td>"
            f"<td class='right'>{h(br_money(row['total']))}</td>"
            f"<td>{h(br_date(row['first_date']))} a {h(br_date(row['last_date']))}<div class='hint'>Contribs.: {h(sample_hint)}</div></td>"
            f"<td>{h(row['reason'])}</td>"
            f"<td><form method='post' action='/contribuinte/vincular'><input type='hidden' name='contributor_id' value='{row['contributor_id']}'><input type='hidden' name='person_id' value='{row['person_id']}'><input type='hidden' name='return_to' value='{h(return_to)}'><button class='button small primary' type='submit'>Vincular</button></form></td>"
            "</tr>"
        )
    body = f"""
      <div class="actions">
        <a class="button" href="/contribuintes">Voltar para contribuintes</a>
        <a class="button" href="/pessoas/importar">Importacao de pessoas</a>
        <a class="button" href="/importacoes">Central de importacoes</a>
      </div>
      {message_box(query)}
      <h1>Associacoes por novos cadastros</h1>
      <div class="hint">Esta fila mostra somente contribuicoes pendentes que passaram a ter candidato por causa dos lotes de pessoas selecionados. Assim o operador trabalha o ganho dos novos cadastros sem misturar com todos os pendentes historicos.</div>
      <div class="grid">{cards_html}</div>
      <div class="panel">
        <div class="section-head"><h2>Selecionar lotes de pessoas</h2><span>{badge('filtro cirurgico', 'info')}</span></div>
        <form class="filters" method="get" action="/contribuintes/novos-cadastros">
          <div class="checkbox-grid">{lot_options or "<span class='hint'>Nenhum lote de pessoas encontrado.</span>"}</div>
          <div class="actions">
            <button class="button primary" type="submit">Atualizar fila</button>
            <a class="button" href="/contribuintes/novos-cadastros">Usar lotes recentes</a>
          </div>
        </form>
      </div>
      <div class="panel">
        <div class="section-head"><h2>{len(rows)} sugestao(oes) para revisar</h2><span>{badge('somente novos', 'warn')}</span></div>
        <table>
          <thead><tr><th>Contribuinte pendente</th><th>Pessoa nova/atualizada</th><th>Aderencia</th><th class="right">Eventos</th><th class="right">Total</th><th>Periodo</th><th>Motivo</th><th>Acao</th></tr></thead>
          <tbody>{''.join(table_rows) if table_rows else "<tr><td colspan='8'>Nenhuma associacao nova encontrada para os lotes selecionados.</td></tr>"}</tbody>
        </table>
      </div>
    """
    return render_layout("Associacoes por novos cadastros", body, "contribuintes")


def render_pix_home(db: PowerChurchDB, query: dict[str, list[str]]) -> str:
    lots = db.pix_lots(30)
    default_org = db.default_organization_id()
    rules = db.pix_rules(default_org)
    recurring_unlinked = len(db.list_contributors(mode="recorrentes", limit=10000))
    contributors_pending = moneyless_int(
        db.conn.execute(
            """
            SELECT COUNT(*)
            FROM contribuintes c
            WHERE c.ativo = 1
              AND (
                    EXISTS (
                        SELECT 1
                        FROM contribuicoes co
                        WHERE co.contribuinte_id = c.id AND co.ativo = 1 AND co.pessoa_id IS NULL
                    )
                 OR EXISTS (
                        SELECT 1
                        FROM pix_movimentos pm
                        WHERE COALESCE(pm.resolved_contribuinte_id, pm.suggested_contribuinte_id) = c.id
                          AND pm.ativo = 1
                          AND pm.review_status IN ('revisar_pessoa', 'revisar_destinacao', 'revisar_duplicidade')
                    )
              )
            """
        ).fetchone()[0]
    )
    cards = [
        ("Lotes PIX", db.scalar("SELECT COUNT(*) FROM pix_lotes"), ""),
        (
            "Movimentos em saneamento",
            db.scalar(
                "SELECT COUNT(*) FROM pix_movimentos WHERE ativo = 1 AND review_status IN ('revisar_pessoa', 'revisar_destinacao', 'revisar_duplicidade')"
            ),
            "warn",
        ),
        (
            "Sem financeiro",
            db.scalar("SELECT COUNT(*) FROM pix_movimentos WHERE ativo = 1 AND review_status <> 'ignorado' AND imported_contribution_id IS NULL"),
            "warn",
        ),
        (
            "Lancados financeiramente",
            db.scalar("SELECT COUNT(*) FROM pix_movimentos WHERE imported_contribution_id IS NOT NULL"),
            "info",
        ),
        ("Contribuintes auxiliares", db.scalar("SELECT COUNT(*) FROM contribuintes WHERE ativo = 1"), "info"),
        ("Pendentes de associacao", contributors_pending, "warn" if contributors_pending else "ok"),
        ("Sugestoes de integracao", recurring_unlinked, "warn" if recurring_unlinked else "ok"),
        ("Regras por centavos", len(rules), "warn"),
    ]
    cards_html = "".join(
        f"<div class='card'><div class='label'>{h(label)}</div><div class='value'>{h(value)}</div>{badge(cls, cls) if cls else ''}</div>"
        for label, value, cls in cards
    )
    lot_rows = []
    for row in lots:
        lot_rows.append(
            "<tr>"
            f"<td><a href='/pix/lote?id={row['id']}'>Lote #{row['id']}</a><div class='hint'>{h(row['nome_arquivo'])}</div></td>"
            f"<td>{h(br_date(row['periodo_inicio']))} ate {h(br_date(row['periodo_fim']))}</td>"
            f"<td class='right'>{row['total_movimentos']}</td>"
            f"<td class='right'>{h(br_money(row['total_valor']))}</td>"
            f"<td>{pix_lot_status_badge(row['status'])}</td>"
            f"<td>{h(br_date(str(row['atualizado_em'] or '')[:10])) or '-'}</td>"
            f"<td><a class='button small primary' href='/pix/lote?id={row['id']}'>Abrir lote</a></td>"
            "</tr>"
        )
    rule_preview = "".join(
        f"<tr><td>{pix_rule_badge(row['codigo_centavos'], row['nome_destinacao'])}</td><td>{h(row['tipo_nome'] or 'Sem tipo')}</td><td>{badge('Ativa' if row['ativo'] else 'Inativa', 'ok' if row['ativo'] else 'warn')}</td></tr>"
        for row in rules[:12]
    )
    body = f"""
      <div class="actions">
        <a class="button" href="/">Inicio</a>
        <a class="button" href="/importacoes">Central de importacoes</a>
        <a class="button" href="/contribuintes">Contribuintes</a>
        <a class="button" href="/contribuintes?mode=pendentes">Contribuintes pendentes</a>
        <a class="button" href="/contribuintes?mode=recorrentes">Sugestao de integracao</a>
        <a class="button" href="/pix/regras">Regras por centavos</a>
      </div>
      {message_box(query)}
      <h1>Importacao PIX assistida</h1>
      <div class="hint">Fluxo em duas camadas, mas com financeiro imediato: o PDF do banco entra no lote PIX, cada movimento ja vira lancamento financeiro e o lote fica apenas como fila de saneamento operacional. Os casos duvidosos seguem para auditoria executavel sem deixar nenhum valor fora do sistema.</div>
      <div class="grid">{cards_html}</div>
      <div class="panel">
        <h2>Novo extrato PIX</h2>
      <div class="hint">Envie o PDF do banco. O sistema vai interpretar os movimentos, criar o lote, registrar financeiramente cada PIX, sugerir dizimo por default e mandar para saneamento apenas os casos com destinacao por centavos, identificacao incerta ou possivel duplicidade entre documentos. Repeticoes dentro do mesmo extrato continuam sendo tratadas como lancamentos distintos.</div>
        <form method="post" action="/pix/lotes/upload" enctype="multipart/form-data">
          <input type="hidden" name="return_to" value="/pix">
          <div class="form-grid">
            <label class="wide">PDF do extrato PIX<input type="file" name="extrato_pdf" accept=".pdf,application/pdf" required></label>
          </div>
          <div class="actions">
            <button class="button primary" type="submit">Criar lote PIX</button>
            <a class="button" href="/pix/regras">Conferir regras de centavos</a>
          </div>
        </form>
      </div>
      <div class="detail-grid">
        <div class="panel">
          <div class="section-head"><h2>Lotes recentes</h2><span>{badge(len(lots), 'info')}</span></div>
          <table>
            <thead><tr><th>Lote</th><th>Periodo</th><th class="right">Mov.</th><th class="right">Valor</th><th>Status</th><th>Atual.</th><th>Acao</th></tr></thead>
            <tbody>{''.join(lot_rows) if lot_rows else "<tr><td colspan='7'>Nenhum lote PIX criado ainda.</td></tr>"}</tbody>
          </table>
        </div>
        <div class="panel">
          <div class="section-head"><h2>Tabela de centavos ativa</h2><span>{badge(len(rules), 'warn')}</span></div>
          <div class="hint">Qualquer valor fora dos codigos especiais entra como Dizimo por default. Os codigos mapeados continuam visiveis e editaveis pela tela do sistema.</div>
          <table>
            <thead><tr><th>Codigo</th><th>Destino</th><th>Status</th></tr></thead>
            <tbody>{rule_preview or "<tr><td colspan='3'>Nenhuma regra PIX cadastrada.</td></tr>"}</tbody>
          </table>
        </div>
      </div>
    """
    return render_layout("PIX", body, "pix")


def render_cent_rules(db: PowerChurchDB, query: dict[str, list[str]], context: str = "pix") -> str:
    default_org = db.default_organization_id()
    rules = db.pix_rules(default_org)
    types = db.contribution_types(default_org)
    edit_rule_id = moneyless_int(query.get("edit_rule_id", ["0"])[0])
    current = next((row for row in rules if moneyless_int(row["id"]) == edit_rule_id), None)
    is_statement = context == "extratos"
    back_path = "/extratos" if is_statement else "/pix"
    back_label = "Voltar para extratos" if is_statement else "Voltar para PIX"
    form_action = "/extratos/regras/salvar" if is_statement else "/pix/regras/salvar"
    clear_path = "/extratos/regras" if is_statement else "/pix/regras"
    active_tab = "extratos" if is_statement else "pix"
    type_options = "<option value=''>Criar/usar tipo proprio da destinacao</option>" + "".join(
        option(str(row["id"]), str(row["nome"]), moneyless_int(current["tipo_contribuicao_id"]) if current else "")
        for row in types
    )
    current_destination_html = ""
    if current:
        account_label = " · ".join(
            item
            for item in [
                normalize_query(current["plano_conta_codigo"]),
                normalize_query(current["plano_conta_nome"]),
            ]
            if item
        ) or "sera criada ao salvar"
        campaign_label = normalize_query(current["campanha_nome"]) or "sera criada ao salvar"
        current_destination_html = (
            "<div class='hint wide' style='margin-top:8px'>"
            f"Conta vinculada: <b>{h(account_label)}</b><br>"
            f"Campanha vinculada: <b>{h(campaign_label)}</b>"
            "</div>"
        )
    rule_rows = []
    for row in rules:
        destination_bits = []
        if row["plano_conta_codigo"] or row["plano_conta_nome"]:
            destination_bits.append(
                f"<b>{h(row['plano_conta_codigo'] or '')}</b> {h(row['plano_conta_nome'] or '')}".strip()
            )
        if row["campanha_nome"]:
            destination_bits.append(f"<span class='hint'>Campanha: {h(row['campanha_nome'])}</span>")
        destination_html = "<br>".join(destination_bits) if destination_bits else "<span class='hint'>Sem conta/campanha</span>"
        rule_rows.append(
            "<tr>"
            f"<td>{pix_rule_badge(row['codigo_centavos'], row['nome_destinacao'])}</td>"
            f"<td>{h(row['tipo_nome'] or 'Sem tipo vinculado')}</td>"
            f"<td>{destination_html}</td>"
            f"<td>{badge('Ativa' if row['ativo'] else 'Inativa', 'ok' if row['ativo'] else 'warn')}</td>"
            f"<td><a class='button small primary' href='{clear_path}?edit_rule_id={row['id']}'>Editar</a></td>"
            "</tr>"
        )
    body = f"""
      <div class="actions">
        <a class="button" href="{back_path}">{back_label}</a>
        <a class="button" href="/contribuicoes">Tipos de contribuicao</a>
      </div>
      {message_box(query)}
      <h1>Regras por centavos</h1>
      <div class="hint">Esta tabela pode ser ajustada pelo operador e vale para <b>PIX</b> e <b>extratos bancarios</b>. A etiqueta do centavo agora tambem cria/atualiza a <b>conta de receita</b> e a <b>campanha</b> correspondente. Tudo o que estiver fora dos codigos especiais continua entrando como <b>Dizimo default</b>. Para reaplicar uma mudanca em lotes ja carregados, use o botao de reprocessar no lote correspondente.</div>
      <div class="detail-grid">
        <div class="panel">
          <div class="section-head"><h2>{'Editar regra' if current else 'Nova regra'}</h2><span>{badge('operacional', 'info')}</span></div>
          <form method="post" action="{form_action}">
            <input type="hidden" name="rule_id" value="{moneyless_int(current['id']) if current else 0}">
            <div class="form-grid">
              {input_field('codigo_centavos', 'Codigo de centavos', current['codigo_centavos'] if current else '', css_class='')}
              {input_field('nome_destinacao', 'Nome da destinacao', current['nome_destinacao'] if current else '', css_class='wide')}
              <label>Tipo de contribuicao<select name="tipo_contribuicao_id">{type_options}</select></label>
              <label>Status<select name="ativo"><option value="1" {'selected' if not current or current['ativo'] else ''}>Ativa</option><option value="0" {'selected' if current and not current['ativo'] else ''}>Inativa</option></select></label>
              {current_destination_html}
            </div>
            <div class="hint" style="margin-top:10px">Ao salvar, a etiqueta vira tambem a conta de destinacao. Ex.: <b>07 · Musica</b> cria/atualiza a conta <b>CENT.07 · Musica</b> e a campanha <b>Musica</b>.</div>
            <div class="actions">
              <button class="button primary" type="submit">Salvar regra</button>
              <a class="button" href="{clear_path}">Limpar formulario</a>
            </div>
          </form>
        </div>
        <div class="panel">
          <div class="section-head"><h2>Mapa atual</h2><span>{badge(len(rules), 'warn')}</span></div>
          <table>
            <thead><tr><th>Codigo</th><th>Tipo destino</th><th>Conta / campanha</th><th>Status</th><th>Acao</th></tr></thead>
            <tbody>{''.join(rule_rows) if rule_rows else "<tr><td colspan='5'>Nenhuma regra PIX cadastrada.</td></tr>"}</tbody>
          </table>
        </div>
      </div>
    """
    return render_layout("Regras por centavos", body, active_tab)


def render_pix_rules(db: PowerChurchDB, query: dict[str, list[str]]) -> str:
    return render_cent_rules(db, query, "pix")


def render_statement_rules(db: PowerChurchDB, query: dict[str, list[str]]) -> str:
    return render_cent_rules(db, query, "extratos")


def render_contributors(db: PowerChurchDB, query: dict[str, list[str]]) -> str:
    q = normalize_query(query.get("q", [""])[0])
    mode = normalize_query(query.get("mode", ["todos"])[0]) or "todos"
    section = normalize_query(query.get("section", [""])[0]).lower()
    competencia = normalize_query(query.get("competencia", [""])[0])
    date_start = normalize_query(query.get("date_start", [""])[0])
    date_end = normalize_query(query.get("date_end", [""])[0])
    person_query = normalize_query(query.get("person_query", [""])[0])
    selected_tags = [normalize_query(item).lower() for item in query.get("tag", []) if normalize_query(item)]
    selected_tag_set = set(selected_tags)
    if not section and (q or selected_tags or mode != "todos"):
        section = "contributors"
    base_query = contributor_report_query_string(mode=mode, q=q, tags=selected_tags)
    contributors_return_to = f"/contribuintes?{base_query}" if base_query else "/contribuintes"
    dashboard_data = build_contributors_dashboard_data(db, q=q, mode=mode, tags=selected_tags, limit=10000)
    rows = list(dashboard_data["rows"])
    family_groups = list(dashboard_data["family_groups"])
    family_links = list(dashboard_data["family_links"])
    total = db.scalar("SELECT COUNT(*) FROM contribuintes WHERE ativo = 1")
    linked = db.scalar("SELECT COUNT(*) FROM contribuintes WHERE ativo = 1 AND pessoa_id IS NOT NULL")
    pf = db.scalar("SELECT COUNT(*) FROM contribuintes WHERE ativo = 1 AND tipo = 'pf'")
    pj = db.scalar("SELECT COUNT(*) FROM contribuintes WHERE ativo = 1 AND tipo = 'pj'")
    count_rows = rows if not q and mode == "todos" and not selected_tags else db.list_contributors(limit=10000)
    pending_contributors = sum(1 for row in count_rows if moneyless_int(row.get("pendencias_total")) > 0)
    pending_unlaunched = sum(1 for row in count_rows if moneyless_int(row.get("pix_pendentes")) > 0)
    pending_without_person = sum(1 for row in count_rows if moneyless_int(row.get("contribuicoes_sem_pessoa")) > 0)
    recurring_unlinked = sum(1 for row in count_rows if moneyless_int(row.get("sugestao_integracao")) > 0)
    period_competences = db.contributor_period_competences()
    period_data = (
        build_contributor_period_report_data(
            db,
            competencia=competencia,
            date_start=date_start,
            date_end=date_end,
            person_query=person_query,
        )
        if section == "periodo"
        else None
    )
    tag_options = [
        ("integracao", "Sugerir integracao"),
        ("familia_sugerida", "Familia sugerida"),
        ("recorrente", "Recorrente"),
        ("semanal", "Recorrencia semanal"),
        ("multicompetencia", "Multicompetencia"),
        ("pendencias", "Com pendencias"),
        ("pix_saneamento", "PIX em saneamento"),
        ("sem_pessoa", "Contribuicoes sem pessoa"),
        ("sem_vinculo", "Sem vinculo"),
        ("vinculado", "Vinculado"),
        ("pf", "Somente PF"),
        ("pj", "Somente PJ"),
    ]
    active_tag_badges = "".join(
        badge(dict(tag_options).get(tag, tag.replace("_", " ")), "warn")
        for tag in selected_tags
    )
    tag_filter_fields = "".join(
        f"<label><input type='checkbox' name='tag' value='{h(value)}' {'checked' if value in selected_tag_set else ''}> {h(label)}</label>"
        for value, label in tag_options
    )
    def section_href(section_key: str, pdf: bool = False) -> str:
        query_string = contributor_report_query_string(
            mode=mode,
            q=q,
            tags=selected_tags,
            section=section_key,
            competencia=competencia,
            date_start=date_start,
            date_end=date_end,
            person_query=person_query,
        )
        base_path = "/contribuintes.pdf" if pdf else "/contribuintes"
        return f"{base_path}?{query_string}" if query_string else base_path

    def section_pdf_print_href(section_key: str) -> str:
        href = section_href(section_key, pdf=True)
        separator = "&" if "?" in href else "?"
        return f"{href}{separator}inline=1"

    def panel_actions(section_key: str, open_label: str = "Versao de impressao") -> str:
        if section == section_key:
            if section_key == "periodo":
                return (
                    "<div class='actions' style='margin-top:12px'>"
                    f"<a class='button primary' href='{section_pdf_print_href(section_key)}' target='_blank' rel='noopener'>Abrir PDF oficial para imprimir</a>"
                    f"<a class='button' href='{section_href(section_key, pdf=True)}'>Baixar PDF</a>"
                    "</div>"
                )
            return (
                "<div class='actions' style='margin-top:12px'>"
                "<button class='button' type='button' onclick='window.print()'>Imprimir esta lista filtrada</button>"
                f"<a class='button' href='{section_href(section_key, pdf=True)}'>Baixar PDF</a>"
                "</div>"
            )
        return (
            "<div class='actions' style='margin-top:12px'>"
            f"<a class='button small' href='{section_href(section_key)}'>{h(open_label)}</a>"
            f"<a class='button small' href='{section_href(section_key, pdf=True)}'>PDF</a>"
            "</div>"
        )
    dashboard_cards = [
        ("Contribuintes ativos", total, "info", "/contribuintes?section=contributors"),
        ("Vinculados a pessoas", linked, "ok", "/contribuintes?tag=vinculado&section=contributors"),
        ("Fila pendente", pending_contributors, "warn" if pending_contributors else "ok", "/contribuintes?mode=pendentes&section=contributors"),
        ("PIX em saneamento", pending_unlaunched, "warn" if pending_unlaunched else "ok", "/contribuintes?mode=nao_lancados&section=contributors"),
        ("Contribuicoes sem pessoa", pending_without_person, "warn" if pending_without_person else "ok", "/contribuintes?mode=sem_pessoa&section=contributors"),
        ("Contribuicoes por periodo", db.scalar("SELECT COUNT(*) FROM contribuicoes WHERE ativo = 1"), "info", "/contribuintes?section=periodo"),
        ("Associacoes por novos cadastros", "Filtrar", "warn", "/contribuintes/novos-cadastros"),
        ("Sugestao de integracao", recurring_unlinked, "warn" if recurring_unlinked else "ok", "/contribuintes?mode=recorrentes&tag=integracao&section=contributors"),
        ("Associacoes sugeridas", len(family_links), "warn" if family_links else "ok", "/contribuintes?mode=recorrentes&tag=integracao&section=family_links"),
        ("Blocos familiares", len(family_groups), "info" if family_groups else "ok", "/contribuintes?mode=recorrentes&tag=familia_sugerida&section=family_groups"),
        ("Pessoa fisica", pf, "info", "/contribuintes?tag=pf&section=contributors"),
        ("Pessoa juridica / externo", pj, "warn", "/contribuintes?tag=pj&section=contributors"),
    ]
    cards_html = "".join(
        f"<a class='card-link' href='{h(href)}'><div class='card'><div class='label'>{h(label)}</div><div class='value'>{h(value)}</div>{badge(cls, cls) if cls else ''}</div></a>"
        for label, value, cls, href in dashboard_cards
    )
    table_rows = []
    for row in rows:
        recurrence = contributor_recurrence_flags(row)
        person_html = (
            f"<a href='/pessoa?id={row['pessoa_id']}'>{h(row['pessoa_nome'])}</a>"
            if moneyless_int(row["pessoa_id"])
            else "<span class='hint'>Sem vinculo</span>"
        )
        document_html = h(row["documento_principal"]) or "<span class='hint'>Nao informado</span>"
        recurrence_badges = [badge(
            "Recorrente" if moneyless_int(row["contribuicoes_qtd"]) >= 2 else "Pontual",
            "ok" if moneyless_int(row["contribuicoes_qtd"]) >= 2 else "info",
        )]
        if moneyless_int(row.get("sugestao_integracao")):
            recurrence_badges.append(badge("Sugerir integracao", "danger"))
        if moneyless_int(row.get("recorrencia_semanal")):
            recurrence_badges.append(badge(f"{row['recorrencia_semanas']} semana(s)", "warn"))
        if moneyless_int(row.get("recorrencia_multicompetencia")):
            recurrence_badges.append(badge(f"{row['recorrencia_competencias']} competencia(s)", "info"))
        pending_bits = []
        if moneyless_int(row.get("pix_pendentes")):
            pending_bits.append(badge(f"{row['pix_pendentes']} PIX em saneamento", "warn"))
        if moneyless_int(row.get("contribuicoes_sem_pessoa")):
            pending_bits.append(badge(f"{row['contribuicoes_sem_pessoa']} contrib. sem pessoa", "danger"))
        if not pending_bits:
            pending_bits.append(badge("Fila limpa", "ok"))
        family_keys = contributor_family_keys(row.get("nome"))
        family_hint = []
        if family_keys.get("nuclear"):
            family_hint.append(f"Nucleo: {family_keys['nuclear'].title()}")
        elif family_keys.get("broad"):
            family_hint.append(f"Familia ampliada: {family_keys['broad'].title()}")
        if recurrence["candidate"]:
            family_hint.append("Pode haver uma unica pessoa contribuindo por toda a casa.")
        family_hint_html = (
            f"<div class='hint' style='margin-top:6px'>{h(' | '.join(family_hint))}</div>"
            if family_hint
            else ""
        )
        if moneyless_int(row["pessoa_id"]):
            action_bits = [
                f"<a class='button small' href='/contribuinte?id={row['id']}'>Abrir ficha financeira</a>",
                f"<a class='button small' href='/pessoa?id={row['pessoa_id']}'>Abrir pessoa</a>",
            ]
        else:
            action_bits = [
                f"<a class='button small primary' href='/contribuinte?id={row['id']}'>Associar / revisar</a>",
            ]
        table_rows.append(
            "<tr>"
            f"<td><a href='/contribuinte?id={row['id']}'>{h(row['nome'])}</a></td>"
            f"<td>{badge('PF' if row['tipo'] == 'pf' else 'PJ', 'info' if row['tipo'] == 'pf' else 'warn')}</td>"
            f"<td>{document_html}</td>"
            f"<td>{person_html}</td>"
            f"<td>{''.join(recurrence_badges)}<div class='hint' style='margin-top:6px'>{''.join(pending_bits)}</div>{family_hint_html}</td>"
            f"<td class='right'>{h(row['contribuicoes_qtd'])}</td>"
            f"<td class='right'>{h(br_money(row['total_contribuido']))}</td>"
            f"<td>{h(br_date(row['ultima_contribuicao']))}</td>"
            f"<td>{badge(row['qualidade'], 'ok')}</td>"
            f"<td>{h(row['origem'])}</td>"
            f"<td><div class='actions'>{''.join(action_bits)}</div></td>"
            "</tr>"
        )
    family_group_blocks = []
    for group in family_groups:
        member_bits = []
        for member in group["members"]:
            recurrence = contributor_recurrence_flags(member)
            recurrence_bits = []
            if recurrence["weekly"]:
                recurrence_bits.append(f"{recurrence['weeks']} semana(s)")
            if recurrence["multi_competencia"]:
                recurrence_bits.append(f"{recurrence['competencias']} competencia(s)")
            member_actions = contributor_create_frequentador_form(
                moneyless_int(member["id"]),
                contributors_return_to,
                label="Criar frequentador",
            )
            member_bits.append(
                f"<div class='accessory-item'>"
                f"<b>{'Nucleo sugerido' if group['scope'] == 'nuclear' else 'Familia ampliada'}</b>"
                f"<a href='/contribuinte?id={member['id']}'>{h(member['nome'])}</a>"
                f"<div class='hint'>{h(member['documento_principal'] or 'Sem documento principal')} | {h(br_money(member['total_contribuido']))} | {h(', '.join(recurrence_bits) or 'recorrencia detectada')}</div>"
                f"<div class='actions' style='margin-top:10px'>{member_actions}<a class='button small' href='/contribuinte?id={member['id']}'>Abrir ficha</a></div>"
                f"</div>"
            )
        family_group_blocks.append(
            f"<div class='panel'>"
            f"<div class='section-head'><h2>{h(group['label'])}</h2><span>{badge('nucleo' if group['scope'] == 'nuclear' else 'familia ampliada', 'warn' if group['scope'] == 'nuclear' else 'info')}</span></div>"
            f"<div class='hint'>Sobrenomes sugerem relacao familiar. Isto ajuda quando uma unica pessoa com renda contribui por toda a familia e o cliente quer decidir se vincula diretamente ao cadastro existente ou se usa esses nomes para criar frequentadores e relacionamentos futuros.</div>"
            f"<div class='accessory-grid'>{''.join(member_bits)}</div>"
            f"</div>"
        )
    family_panel_html = (
        f"""
        <div class="panel">
          <div class="section-head"><h2>Blocos familiares sugeridos</h2><span>{badge(len(family_groups), 'info')}</span></div>
          <div class="hint">Este painel junta recorrentes sem pessoa vinculada quando os sobrenomes sugerem um mesmo nucleo ou uma familia ampliada. Use isso como pista operacional: muitas vezes quem tem renda contribui pela casa inteira, e esse bloco ajuda a localizar filhos, conjuge, genro, netos e outros frequentadores que ainda nao entraram no cadastro.</div>
          {panel_actions('family_groups', 'Imprimir blocos familiares')}
          <div class="stack">{''.join(family_group_blocks)}</div>
        </div>
        """
        if family_groups
        else ""
    )
    family_link_blocks = []
    for block in family_links:
        contributor = block["contributor"]
        recurrence = contributor_recurrence_flags(contributor)
        block_actions = (
            contributor_create_frequentador_form(
                moneyless_int(contributor["id"]),
                contributors_return_to,
                label="Criar frequentador sem vincular familia",
            )
            + f"<a class='button small' href='/contribuinte?id={contributor['id']}'>Abrir ficha do contribuinte</a>"
        )
        contributor_bits = []
        if recurrence["weekly"]:
            contributor_bits.append(f"{recurrence['weeks']} semana(s)")
        if recurrence["multi_competencia"]:
            contributor_bits.append(f"{recurrence['competencias']} competencia(s)")
        match_rows = "".join(
            "<tr>"
            f"<td><a href='/pessoa?id={person['id']}'>{h(person['nome'])}</a><div class='hint'>{h(format_system_id(person['id']))} | {h(format_member_code(person['codigo_interno'])) or 'Sem numero'} | CPF {h(format_cpf(person['cpf']))}</div></td>"
            f"<td>{badge('nucleo' if person['relation'] == 'nuclear' else 'familia ampliada', 'warn' if person['relation'] == 'nuclear' else 'info')}</td>"
            f"<td>{badge(person['status'], 'warn' if person['status'] == 'membro_inativo' else 'ok' if str(person['status']).startswith('membro') else 'info')}</td>"
            f"<td><div class='actions'>"
            f"<form method='post' action='/contribuinte/vincular' style='display:inline-block'><input type='hidden' name='contributor_id' value='{moneyless_int(contributor['id'])}'><input type='hidden' name='person_id' value='{moneyless_int(person['id'])}'><input type='hidden' name='return_to' value='{h(contributors_return_to)}'><button class='button small primary' type='submit'>Vincular a esta pessoa</button></form>"
            f"{contributor_create_frequentador_form(moneyless_int(contributor['id']), contributors_return_to, family_person_id=moneyless_int(person['id']), label='Criar frequentador desta familia')}"
            f"</div></td>"
            "</tr>"
            for person in block["matches"]
        )
        family_link_blocks.append(
            f"<div class='panel'>"
            f"<div class='section-head'><h2><a href='/contribuinte?id={contributor['id']}'>{h(contributor['nome'])}</a></h2><span>{badge('sugerir integracao', 'danger')}</span></div>"
            f"<div class='hint'>Contribuinte recorrente sem pessoa vinculada. Sobrenomes sugerem relacao com pessoas ja cadastradas, o que pode indicar conjuge, filho, neto, genro ou outro familiar que frequenta a igreja, enquanto uma unica pessoa com renda contribui por toda a casa.</div>"
            f"<div class='hint' style='margin-top:8px'>Documento: {h(contributor['documento_principal'] or 'Sem documento principal')} | Total: {h(br_money(contributor['total_contribuido']))} | Recorrencia: {h(', '.join(contributor_bits) or 'detectada')}</div>"
            f"<div class='actions' style='margin-top:12px'>{block_actions}</div>"
            f"<table style='margin-top:12px'><thead><tr><th>Pessoa do cadastro</th><th>Bloco familiar</th><th>Status</th><th>Acoes</th></tr></thead><tbody>{match_rows}</tbody></table>"
            f"</div>"
        )
    family_link_panel_html = (
        f"""
        <div class="panel">
          <div class="section-head"><h2>Contribuintes recorrentes ligados a familias ja cadastradas</h2><span>{badge(len(family_links), 'warn')}</span></div>
          <div class="hint">Aqui o sistema nao esta dizendo que seja a mesma pessoa. Ele apenas mostra quando um contribuinte recorrente sem cadastro vinculado compartilha sobrenome-base com pessoas ja existentes, para ajudar a secretaria a descobrir relacoes de familia e decidir se cria frequentes, visitantes ou faz o vinculo financeiro ao membro certo.</div>
          {panel_actions('family_links', 'Imprimir relatorio familiar')}
          <div class="stack">{''.join(family_link_blocks)}</div>
        </div>
        """
        if family_links
        else ""
    )
    main_table_panel_html = f"""
      <div class="panel">
        <div class="section-head"><h2>{len(rows)} contribuinte(s) exibido(s)</h2><span>{badge('relatorio', 'info')}</span></div>
        <div class="hint">Esta lista respeita o modo selecionado, a busca livre e todas as tags estrategicas aplicadas acima.</div>
        {panel_actions('contributors', 'Imprimir tabela principal')}
        <table>
          <thead><tr><th>Nome</th><th>Tipo</th><th>Documento</th><th>Pessoa vinculada</th><th>Fila operacional</th><th class='right'>Lanc.</th><th class='right'>Total</th><th>Ultimo receb.</th><th>Qualidade</th><th>Origem</th><th>Acao</th></tr></thead>
          <tbody>{''.join(table_rows) if table_rows else "<tr><td colspan='11'>Nenhum contribuinte encontrado.</td></tr>"}</tbody>
        </table>
      </div>
    """
    period_panel_html = ""
    if period_data is not None:
        period_summary = dict(period_data["summary"])
        period_cards = [
            ("Total geral", br_money(period_summary["total_geral"]), "ok"),
            ("Contribuintes diferentes", period_summary["contribuintes"], "info"),
            ("Remessas", period_summary["remessas"], "info"),
            ("No rol", period_summary["no_rol"], "ok"),
            ("Fora do rol", period_summary["fora_rol"], "warn"),
            ("Inativos", period_summary["inativos"], "warn"),
            ("Sem vinculo", period_summary["sem_vinculo"], "danger" if period_summary["sem_vinculo"] else "info"),
            ("Somente documento", period_summary.get("somente_documento", 0), "warn" if period_summary.get("somente_documento", 0) else "info"),
        ]
        period_cards_row_1 = "".join(
            f"<div class='card'><div class='label'>{h(label)}</div><div class='value'>{h(value)}</div>{badge(css, css) if css else ''}</div>"
            for label, value, css in period_cards[:4]
        )
        period_cards_row_2 = "".join(
            f"<div class='card'><div class='label'>{h(label)}</div><div class='value'>{h(value)}</div>{badge(css, css) if css else ''}</div>"
            for label, value, css in period_cards[4:]
        )
        period_rows_html = []
        last_period_group_label = ""
        for item in period_data["groups"]:
            if item.get("group_label") != last_period_group_label:
                last_period_group_label = str(item.get("group_label") or "")
                period_rows_html.append(
                    f"<tr class='period-section-row'><td colspan='4'>{h(last_period_group_label)}</td></tr>"
                )
            if len(item["entries"]) == 1:
                entry = item["entries"][0]
                entries_html = (
                    "<div class='period-entry-list'>"
                    f"<div class='period-entry-item single'><b>{h(br_money(entry['valor']))}</b><span class='hint'>{h(br_date(entry['data_recebimento']))} | {h(entry['competencia'] or '-')}</span></div>"
                    "</div>"
                )
            else:
                entries_html = (
                    "<div class='period-entry-list'>"
                    f"<div class='period-entry-count'>{h(len(item['entries']))} remessa(s)</div>"
                    + "".join(
                        f"<div class='period-entry-item'><span>{h(br_date(entry['data_recebimento']))} | {h(entry['competencia'] or '-')}</span><b>{h(br_money(entry['valor']))}</b></div>"
                        for entry in item["entries"]
                    )
                    + "</div>"
                )
            contributor_label = (
                f"<a href='/contribuinte?id={item['contribuinte_id']}'>{h(item['nome'])}</a>"
                if moneyless_int(item["contribuinte_id"])
                else h(item["nome"])
            )
            contributor_hint_bits = []
            if item["documento"]:
                contributor_hint_bits.append(str(item["documento"]))
            if item["pessoa_nome"]:
                contributor_hint_bits.append(f"Pessoa vinculada: {item['pessoa_nome']}")
            if item["pessoa_codigo_interno"]:
                contributor_hint_bits.append(format_member_code(item["pessoa_codigo_interno"]) or "")
            if item.get("nome_original") and item["nome_original"] != item["nome"]:
                contributor_hint_bits.append(f"Origem: {item['nome_original']}")
            period_rows_html.append(
                "<tr>"
                f"<td>{contributor_label}<div class='hint'>{h(' | '.join(bit for bit in contributor_hint_bits if bit))}</div></td>"
                f"<td>{badge(item['sigla'], item['sigla_class'])}<div class='hint'>{h(item['sigla_label'])}</div></td>"
                f"<td>{entries_html}</td>"
                f"<td class='right'><b>{h(br_money(item['total']))}</b></td>"
                "</tr>"
            )
        legend_badges = "".join(
            badge(f"{code} = {label}", "ok" if code == "SA" else "warn" if code in {"SI", "NR"} else "info" if code in {"NF", "NV"} else "danger")
            for code, label in period_data["legend"]
        )
        suggestion_rows = []
        if period_data["suggestions"]:
            for suggestion in period_data["suggestions"]:
                suggestion_href = contributor_report_query_string(
                    section="periodo",
                    competencia=competencia,
                    date_start=date_start,
                    date_end=date_end,
                    person_query=format_system_id(suggestion["id"]),
                )
                suggestion_rows.append(
                    "<tr>"
                    f"<td>{h(suggestion['nome'])}<div class='hint'>{h(format_system_id(suggestion['id']))} | {h(format_member_code(suggestion['codigo_interno']) or 'Sem numero')} | CPF {h(format_cpf(suggestion['cpf']))}</div></td>"
                    f"<td>{badge(contributor_membership_sigla(suggestion['status'], suggestion['id'])[0], 'info')}</td>"
                    f"<td>{h(suggestion['reason'])}</td>"
                    f"<td><a class='button small primary' href='/contribuintes?{suggestion_href}'>Usar esta pessoa</a></td>"
                    "</tr>"
                )
        print_period_rows_html = []
        last_print_group_label = ""
        for item in period_data["groups"]:
            if item.get("group_label") != last_print_group_label:
                last_print_group_label = str(item.get("group_label") or "")
                print_period_rows_html.append(
                    f"<tr class='period-section-row'><td colspan='4'>{h(last_print_group_label)}</td></tr>"
                )
            if len(item["entries"]) == 1:
                entry = item["entries"][0]
                print_entries_html = f"<div>{h(br_date(entry['data_recebimento']))} | {h(entry['competencia'] or '-')} | <b>{h(br_money(entry['valor']))}</b></div>"
            else:
                print_entries_html = (
                    f"<div class='period-entry-count'>{h(len(item['entries']))} remessa(s)</div>"
                    + "".join(
                        f"<div>{h(br_date(entry['data_recebimento']))} | {h(entry['competencia'] or '-')} | <b>{h(br_money(entry['valor']))}</b></div>"
                        for entry in item["entries"]
                    )
                )
            print_hint_bits = []
            if item["documento"]:
                print_hint_bits.append(str(item["documento"]))
            if item["pessoa_nome"]:
                print_hint_bits.append(f"Pessoa vinculada: {item['pessoa_nome']}")
            if item["pessoa_codigo_interno"]:
                print_hint_bits.append(format_member_code(item["pessoa_codigo_interno"]) or "")
            if item.get("nome_original") and item["nome_original"] != item["nome"]:
                print_hint_bits.append(f"Origem: {item['nome_original']}")
            print_period_rows_html.append(
                "<tr>"
                f"<td>{h(item['nome'])}<div class='hint'>{h(' | '.join(bit for bit in print_hint_bits if bit))}</div></td>"
                f"<td>{badge(item['sigla'], item['sigla_class'])}</td>"
                f"<td>{print_entries_html}</td>"
                f"<td class='right'><b>{h(br_money(item['total']))}</b></td>"
                "</tr>"
            )
        exact_search_panel_html = (
            f"<div class='panel'><div class='hint'><b>Filtro aplicado.</b> {h(period_data['search_label'])}</div></div>"
            if normalize_query(period_data["search_label"]) and not period_data["suggestions"]
            else ""
        )
        suggestions_panel_html = (
            f"""
            <div class="panel">
              <div class="section-head"><h2>Provaveis para a busca informada</h2><span>{badge(len(period_data['suggestions']), 'warn')}</span></div>
              <div class="hint">Nenhum nome exato foi localizado. Esta lista serve para divergencias e auditoria interna, sem poluir o relatorio principal.</div>
              <table>
                <thead><tr><th>Pessoa provavel</th><th>Sigla</th><th>Motivo</th><th>Acao</th></tr></thead>
                <tbody>{''.join(suggestion_rows)}</tbody>
              </table>
            </div>
            """
            if period_data["suggestions"]
            else ""
        )
        competence_options = "<option value=''>Todas as competencias</option>" + "".join(
            option(item, item, competencia) for item in period_competences
        )
        period_filter_panel_html = f"""
          <form class="filters print-hide" method="get" action="/contribuintes">
            <input type="hidden" name="section" value="periodo">
            <label>Competencia<select name="competencia">{competence_options}</select></label>
            <label>Data inicial<input type="date" name="date_start" value="{h(date_start)}"></label>
            <label>Data final<input type="date" name="date_end" value="{h(date_end)}"></label>
            <label class="wide">Pessoa ou contribuinte<input name="person_query" value="{h(person_query)}" placeholder="nome exato, ID-, MEM- ou CPF"></label>
            <button class="button primary" type="submit">Gerar relatorio</button>
            <a class="button" href="/contribuintes?section=periodo">Limpar</a>
          </form>
        """
        period_panel_html = f"""
          <div class="period-report">
            <div class="screen-only">
              <div class="panel">
                <div class="section-head"><h2>Contribuicoes por periodo</h2><span>{badge('financeiro', 'info')}</span></div>
                <div class="hint">Lista alfabetica por contribuinte, com todas as remessas do periodo, sigla cadastral e total consolidado. A sigla NR ja indica o que ainda nao esta vinculado a uma pessoa.</div>
                {panel_actions('periodo', 'Imprimir contribuicoes por periodo')}
                {period_filter_panel_html}
              </div>
              <div class="period-summary-grid">{period_cards_row_1}</div>
              <div class="period-summary-grid secondary">{period_cards_row_2}</div>
              <div class="panel">
                <div class="section-head"><h2>Legenda</h2><span>{badge('siglas', 'info')}</span></div>
                <div class="legend-strip">{legend_badges}</div>
              </div>
              {exact_search_panel_html}
              {suggestions_panel_html}
              <div class="panel">
                <div class="section-head"><h2>{len(period_data['groups'])} contribuinte(s) no relatorio</h2><span>{badge('periodo', 'warn')}</span></div>
                <div class="hint">{h(contributor_period_filter_label(competencia=competencia, date_start=date_start, date_end=date_end, person_query=person_query))}</div>
                <table>
                  <thead><tr><th>Contribuinte</th><th>Sigla</th><th>Contribuicoes no periodo</th><th class='right'>Total</th></tr></thead>
                  <tbody>{''.join(period_rows_html) if period_rows_html else "<tr><td colspan='4'>Nenhuma contribuicao encontrada para o filtro informado.</td></tr>"}</tbody>
                </table>
              </div>
            </div>
            <div class="period-print-sheet print-only">
              <div class="panel">
                <div class="section-head"><h2>Contribuicoes por periodo</h2><span>{badge('impressao', 'info')}</span></div>
                <div class="hint">{h(contributor_period_filter_label(competencia=competencia, date_start=date_start, date_end=date_end, person_query=person_query))}</div>
              </div>
              <div class="period-summary-grid">{period_cards_row_1}</div>
              <div class="period-summary-grid secondary">{period_cards_row_2}</div>
              <div class="panel">
                <div class="section-head"><h2>Legenda</h2><span>{badge('siglas', 'info')}</span></div>
                <div class="legend-strip">{legend_badges}</div>
              </div>
              {exact_search_panel_html}
              {suggestions_panel_html}
              <div class="panel">
                <div class="section-head"><h2>{len(period_data['groups'])} contribuinte(s) no relatorio</h2><span>{badge('periodo', 'warn')}</span></div>
                <table>
                  <thead><tr><th>Contribuinte</th><th>Sigla</th><th>Contribuicoes no periodo</th><th class='right'>Total</th></tr></thead>
                  <tbody>{''.join(print_period_rows_html) if print_period_rows_html else "<tr><td colspan='4'>Nenhuma contribuicao encontrada para o filtro informado.</td></tr>"}</tbody>
                </table>
              </div>
            </div>
          </div>
        """
    visible_panels = [main_table_panel_html]
    if family_link_panel_html:
        visible_panels.insert(0, family_link_panel_html)
    if family_panel_html:
        visible_panels.append(family_panel_html)
    if section == "family_links":
        visible_panels = [family_link_panel_html or "<div class='panel'><div class='empty'>Nenhuma sugestao familiar encontrada com os filtros atuais.</div></div>"]
    elif section == "family_groups":
        visible_panels = [family_panel_html or "<div class='panel'><div class='empty'>Nenhum bloco familiar sugerido com os filtros atuais.</div></div>"]
    elif section == "contributors":
        visible_panels = [main_table_panel_html]
    elif section == "periodo":
        visible_panels = [period_panel_html]
    elif section == "combined":
        visible_panels = []
        report_tags = {"integracao", "familia_sugerida"}
        include_main = not selected_tag_set or any(tag not in report_tags for tag in selected_tag_set)
        if "integracao" in selected_tag_set and family_link_panel_html:
            visible_panels.append(family_link_panel_html)
        if "familia_sugerida" in selected_tag_set and family_panel_html:
            visible_panels.append(family_panel_html)
        if include_main or not visible_panels:
            visible_panels.append(main_table_panel_html)
    section_top_actions_html = ""
    if section:
        primary_action = (
            f"<a class='button primary' href='{section_pdf_print_href(section or 'contributors')}' target='_blank' rel='noopener'>Abrir PDF p/ imprimir</a>"
            if section == "periodo"
            else "<button class='button' type='button' onclick='window.print()'>Imprimir relatorio</button>"
        )
        section_top_actions_html = (
            primary_action
            + f"<a class='button' href='{section_href(section or 'contributors', pdf=True)}'>Baixar PDF</a>"
        )
    active_filters_panel_html = (
        "<div class='panel'>"
        f"<div class='section-head'><h2>Filtros estrategicos ativos</h2><span>{badge(len(selected_tags), 'warn')}</span></div>"
        "<div class='hint'>O relatorio abaixo ja esta reduzido pelas tags selecionadas.</div>"
        f"<div class='actions'>{active_tag_badges}</div>"
        "</div>"
        if selected_tags and section != "periodo"
        else ""
    )
    filter_panel_html = f"""
      <form class="filters" method="get" action="/contribuintes">
        <input type="hidden" name="mode" value="{h(mode if mode != 'todos' else '')}">
        <input type="hidden" name="section" value="{h(section or 'contributors')}">
        <label class="wide">Busca<input name="q" value="{h(q)}" placeholder="nome, documento principal ou pessoa vinculada"></label>
        <div class="stack" style="width:100%">
          <div class="hint">Tags estrategicas: combine essas leituras para tirar filas operacionais, listas de integracao e relatorios de acompanhamento sem revarrer o banco inteiro.</div>
          <div class="checkbox-grid">{tag_filter_fields}</div>
        </div>
        <button class="button primary" type="submit">Filtrar</button>
        <a class="button" href="/contribuintes">Voltar para central</a>
      </form>
    """
    dashboard_filter_panel_html = f"""
      <div class="panel">
        <div class="section-head"><h2>Marcadores estrategicos</h2><span>{badge('dashboard', 'info')}</span></div>
        <div class="hint">Use os checks abaixo para abrir uma ou mais visoes em sequencia. Quando houver mais de um marcador relevante, o sistema abre os relatorios combinados na mesma tela.</div>
        <form class="filters" method="get" action="/contribuintes">
          <input type="hidden" name="section" value="combined">
          <div class="checkbox-grid">{tag_filter_fields}</div>
          <div class="actions">
            <button class="button primary" type="submit">Abrir selecao</button>
            <a class="button" href="/contribuintes">Limpar</a>
          </div>
        </form>
      </div>
    """
    quick_search_panel_html = f"""
      <div class="panel">
        <div class="section-head"><h2>Busca rapida por nome</h2><span>{badge('busca', 'info')}</span></div>
        <div class="hint">Procure diretamente um contribuinte pelo nome, documento financeiro, pessoa vinculada, ID do sistema ou numero de membro. Quando a busca e acionada daqui, o sistema abre a lista completa filtrada, sem ficar parado so no dashboard.</div>
        <form class="filters" method="get" action="/contribuintes">
          <input type="hidden" name="section" value="contributors">
          <label class="wide">Nome, documento, ID- ou MEM-<input name="q" value="{h(q)}" placeholder="ex: Maria Souza, 123.456.789-00, ID-001078, MEM-00003"></label>
          <button class="button primary" type="submit">Buscar contribuinte</button>
        </form>
      </div>
    """
    hub_html = f"""
      <div class="panel">
        <div class="section-head"><h2>Central de visualizacao e relatorios</h2><span>{badge('dashboard', 'info')}</span></div>
        <div class="hint">Clique diretamente em um card do resumo. O dashboard foi mantido limpo: voce usa os boxes para abrir cada visualizacao individualmente, inclusive PF, PJ, Associacoes sugeridas e Blocos familiares em sequencia, sem despejar listas na abertura.</div>
      </div>
      {quick_search_panel_html}
      {dashboard_filter_panel_html}
    """
    section_nav_html = (
        f"<div class='actions'>"
        f"{section_top_actions_html}"
        f"<a class='button {'primary' if mode == 'todos' else ''}' href='/contribuintes?section=contributors'>Todos</a>"
        f"<a class='button {'primary' if mode == 'pendentes' else ''}' href='/contribuintes?mode=pendentes&section=contributors'>Pendentes de associacao</a>"
        f"<a class='button {'primary' if mode == 'nao_lancados' else ''}' href='/contribuintes?mode=nao_lancados&section=contributors'>PIX em saneamento</a>"
        f"<a class='button {'primary' if mode == 'sem_pessoa' else ''}' href='/contribuintes?mode=sem_pessoa&section=contributors'>Contribuicoes sem pessoa</a>"
        f"<a class='button {'primary' if mode == 'recorrentes' else ''}' href='/contribuintes?mode=recorrentes&section=contributors'>Sugestao de integracao</a>"
        f"<a class='button {'primary' if section == 'periodo' else ''}' href='/contribuintes?section=periodo'>Contribuicoes por periodo</a>"
        f"</div>"
        if section
        else ""
    )
    top_wrapper_class = "print-hide" if section == "periodo" else ""
    body = f"""
      <div class="actions {top_wrapper_class}">
        <a class="button" href="/pix">Voltar para PIX</a>
        <a class="button" href="/pessoas">Pessoas</a>
        {f"<a class='button' href='{contributors_return_to}'>Voltar para painel completo</a>" if section else ""}
      </div>
      {message_box(query)}
      <div class="{top_wrapper_class}">
        <h1>{'Relatorio estrategico de contribuintes' if section else 'Contribuintes auxiliares'}</h1>
        <div class="hint">Este cadastro guarda a identidade financeira que aparece nas remessas e nas contribuicoes. Ele nao substitui a ficha da pessoa; serve para preservar PJ, doadores externos, documentos mascarados, abreviacoes bancarias e tambem os casos em que a contribuicao pode vir de conjuge, filho ou empresa da familia. Quando um lote PIX e encerrado, o trabalho futuro de associacao e saneamento continua aqui.</div>
        <div class="grid">{cards_html}</div>
        {section_nav_html}
      </div>
      {filter_panel_html if section and section != 'periodo' else hub_html if not section else ''}
      {active_filters_panel_html if section else ''}
      {''.join(visible_panels) if section else ''}
    """
    return render_layout("Contribuintes", body, "contribuintes")


def render_contributor(db: PowerChurchDB, query: dict[str, list[str]]) -> str:
    contributor_id = moneyless_int(query.get("id", ["0"])[0])
    lookup = normalize_query(query.get("lookup", [""])[0])
    contributor = db.get_contributor(contributor_id)
    if contributor is None:
        return render_layout("Contribuinte nao encontrado", "<div class='empty'>Contribuinte nao encontrado.</div>", "contribuintes")
    summary = db.contributor_summary(contributor_id)
    identifiers = db.contributor_identifiers(contributor_id)
    contributions = db.contributor_contributions(contributor_id, limit=120)
    pending_pix = db.contributor_pending_pix(contributor_id, limit=40)
    suggestions = db.contributor_possible_people(contributor_id, limit=10) if not moneyless_int(contributor["pessoa_id"]) else []
    search_rows = db.list_people(
        q=lookup,
        status=["membro_ativo", "membro_inativo", "frequentador", "visitante"],
        limit=20,
    ) if (lookup and not moneyless_int(contributor["pessoa_id"])) else []
    suggested_ids = {moneyless_int(row["id"]) for row in suggestions}
    filtered_search_rows = [row for row in search_rows if moneyless_int(row["id"]) not in suggested_ids]
    linked_person_html = (
        f"<a class='button primary' href='/pessoa?id={contributor['pessoa_id']}'>Abrir pessoa vinculada</a>"
        if moneyless_int(contributor["pessoa_id"])
        else ""
    )
    create_frequentador_html = (
        contributor_create_frequentador_form(
            contributor_id,
            f"/contribuinte?id={contributor_id}",
            label="Criar frequentador deste contribuinte",
            css_class="button primary",
        )
        if not moneyless_int(contributor["pessoa_id"])
        else ""
    )
    identifier_rows = "".join(
        "<tr>"
        f"<td>{h(row['tipo'])}</td>"
        f"<td>{h(row['valor'])}</td>"
        f"<td>{'Sim' if row['principal'] else ''}</td>"
        f"<td>{h(row['observacoes'])}</td>"
        "</tr>"
        for row in identifiers
    )
    contribution_rows = "".join(
        "<tr>"
        f"<td>{h(br_date(row['data_recebimento']))}</td>"
        f"<td>{h(row['competencia'])}</td>"
        f"<td>{contribution_operational_status_badge(row['status_operacional'])}</td>"
        f"<td>{h(row['tipo_nome'])}</td>"
        f"<td>{h(row['forma_nome'])}</td>"
        f"<td>{text_or_hint(row['pessoa_nome'], 'Sem pessoa')}</td>"
        f"<td class='right'>{h(br_money(row['valor']))}</td>"
        "</tr>"
        for row in contributions
    )
    pending_rows = "".join(
        "<tr>"
        f"<td>{h(br_date(row['data_recebimento']))}</td>"
        f"<td>{h(row['competencia'])}</td>"
        f"<td>{pix_review_status_badge(row['review_status'])}</td>"
        f"<td>{h(br_money(row['valor']))}</td>"
        f"<td>{h(row['nome_arquivo'])}</td>"
        f"<td><a class='button small primary' href='/pix/movimento?id={row['id']}'>Abrir PIX</a></td>"
        "</tr>"
        for row in pending_pix
    )
    suggestion_rows = "".join(
        "<tr>"
        f"<td><a href='/pessoa?id={row['id']}'>{h(row['nome'])}</a><div class='hint'>{h(format_system_id(row['id']))} | {h(format_member_code(row['codigo_interno'])) or 'Sem numero'} | CPF {h(format_cpf(row['cpf']))}</div></td>"
        f"<td>{pix_candidate_similarity_badge(row.get('engine_doc_match'), row.get('engine_exact_name'), row.get('similarity_ratio'))}</td>"
        f"<td>{h(row['status'])}</td>"
        f"<td>{h(row['engine_reason'])}<div class='hint'>{h(row['engine_source'])} | score {float(row['engine_score']):.2f}</div></td>"
        f"<td><form method='post' action='/contribuinte/vincular'><input type='hidden' name='contributor_id' value='{contributor_id}'><input type='hidden' name='person_id' value='{row['id']}'><input type='hidden' name='return_to' value='{h(f'/contribuinte?id={contributor_id}')}'><button class='button small primary' type='submit'>Vincular a esta pessoa</button></form></td>"
        "</tr>"
        for row in suggestions
    )
    search_result_rows = "".join(
        "<tr>"
        f"<td><a href='/pessoa?id={row['id']}'>{h(row['nome'])}</a><div class='hint'>{h(format_system_id(row['id']))} | {h(format_member_code(row['codigo_interno'])) or 'Sem numero'} | CPF {h(format_cpf(row['cpf']))}</div></td>"
        f"<td>{badge(row['status'], 'warn' if row['status'] == 'membro_inativo' else 'ok' if str(row['status']).startswith('membro') else 'info')}</td>"
        f"<td>{badge(row['pendencias'], 'danger' if moneyless_int(row['pendencias']) else 'ok')}</td>"
        f"<td><form method='post' action='/contribuinte/vincular'><input type='hidden' name='contributor_id' value='{contributor_id}'><input type='hidden' name='person_id' value='{row['id']}'><input type='hidden' name='return_to' value='{h(f'/contribuinte?id={contributor_id}&lookup={urllib.parse.quote(lookup)}')}'><button class='button small primary' type='submit'>Vincular a esta pessoa</button></form></td>"
        "</tr>"
        for row in filtered_search_rows
    )
    if not moneyless_int(contributor["pessoa_id"]):
        search_panel_html = f"""
          <div class='panel' style='margin-top:16px'>
            <h3>Busca complementar para vinculo</h3>
            <div class='hint'>Use esta busca quando a pessoa correta nao estiver nas sugestoes. O vinculo preserva a origem financeira do contribuinte e passa a permitir que as contribuicoes contem no extrato do membro.</div>
            <form class='filters' method='get' action='/contribuinte'>
              <input type='hidden' name='id' value='{contributor_id}'>
              <label class='wide'>Buscar pessoa<input name='lookup' value='{h(lookup)}' placeholder='nome, ID-001078, MEM-00003 ou CPF'></label>
              <button class='button primary' type='submit'>Pesquisar</button>
              <a class='button' href='/contribuinte?id={contributor_id}'>Limpar</a>
            </form>
            <table><thead><tr><th>Pessoa</th><th>Status</th><th>Pend.</th><th>Acao</th></tr></thead><tbody>{search_result_rows or "<tr><td colspan='4'>Pesquise uma pessoa para habilitar o vinculo manual.</td></tr>"}</tbody></table>
          </div>
        """
    else:
        search_panel_html = (
            f"<div class='panel' style='margin-top:16px'><div class='hint'>Este contribuinte ja esta vinculado a {h(contributor['pessoa_nome'])}. "
            "Nesta versao o vinculo direto foi travado para evitar trocas globais acidentais; se quisermos, no proximo passo eu crio uma tela segura de reatribuicao.</div></div>"
        )
    contributor_recurrence = contributor_recurrence_flags(
        {
            "quantidade": summary["quantidade"],
            "semanas_qtd": summary["semanas"],
            "competencias": summary["competencias"],
            "meses_recebimento_qtd": summary["meses"],
            "pessoa_id": contributor["pessoa_id"],
        }
    )
    body = f"""
      <div class="actions">
        <a class="button" href="/contribuintes">Voltar para contribuintes</a>
        <a class="button" href="/pix">Voltar para PIX</a>
        {linked_person_html}
        {create_frequentador_html}
      </div>
      {message_box(query)}
      <h1>{h(contributor['nome'])}</h1>
      <div class="hint">Esta ficha preserva a identidade financeira do pagador. Mesmo quando houver vinculo com pessoa, o nome bancario, os documentos vistos e a trilha da remessa continuam guardados para conferencia futura.</div>
      <div class="grid">
        <div class="card"><div class="label">Tipo</div><div class="value">{'PF' if contributor['tipo'] == 'pf' else 'PJ'}</div>{badge('vinculado' if contributor['pessoa_id'] else 'sem vinculo', 'ok' if contributor['pessoa_id'] else 'warn')}</div>
        <div class="card"><div class="label">Lancamentos</div><div class="value">{summary['quantidade']}</div>{badge('recorrente' if moneyless_int(summary['quantidade']) >= 2 else 'pontual', 'ok' if moneyless_int(summary['quantidade']) >= 2 else 'info')}</div>
        <div class="card"><div class="label">Total contribuido</div><div class="value">{h(br_money(summary['total']))}</div>{badge(contributor['origem'], 'info')}</div>
        <div class="card"><div class="label">Ultima contribuicao</div><div class="value">{h(br_date(summary['ultima_data']))}</div>{badge(f"{summary['competencias']} competencia(s)", 'info')}</div>
        <div class="card"><div class="label">Recorrencia detectada</div><div class="value">{summary['semanas']} semana(s)</div>{badge(f"{summary['meses']} mes(es)", 'info') if contributor_recurrence['multi_competencia'] else badge('pontual', 'info')}</div>
        <div class="card"><div class="label">PIX em saneamento</div><div class="value">{len(pending_pix)}</div>{badge('pendente' if pending_pix else 'limpo', 'warn' if pending_pix else 'ok')}</div>
      </div>
      <div class="detail-grid">
        <div class="panel">
          <div class="section-head"><h2>Identidade financeira</h2><span>{badge(contributor['qualidade'], 'ok')}</span></div>
          <div class="field-grid">
            {field_card('Nome bancario', contributor['nome'], 'wide-field')}
            {field_card('Documento principal', contributor['documento_principal'])}
            {field_card('Tipo de documento', contributor['documento_tipo'])}
            {field_card('Origem', contributor['origem'])}
            {field_card('Status operacional', contributor['status'])}
            {field_card('Pessoa vinculada', contributor['pessoa_nome'] or 'Sem vinculo')}
          </div>
          <p class="hint" style="margin-top:14px">Use esta camada quando o pagador do banco for cônjuge, filho, empresa da família ou doador ainda não cadastrado. Assim a contribuição não se perde, e o vínculo com pessoa fica para quando houver segurança suficiente.</p>
        </div>
        <div class="panel">
          <div class="section-head"><h2>Identificadores vistos</h2><span>{badge(len(identifiers), 'info')}</span></div>
          <table><thead><tr><th>Tipo</th><th>Valor</th><th>Principal</th><th>Observacao</th></tr></thead><tbody>{identifier_rows or "<tr><td colspan='4'>Nenhum identificador registrado.</td></tr>"}</tbody></table>
        </div>
      </div>
      <div class="detail-grid">
        <div class="panel">
          <div class="section-head"><h2>Historico financeiro</h2><span>{badge(summary['quantidade'], 'info')}</span></div>
          <table><thead><tr><th>Data</th><th>Competencia</th><th>Status</th><th>Tipo</th><th>Forma</th><th>Pessoa</th><th class='right'>Valor</th></tr></thead><tbody>{contribution_rows or "<tr><td colspan='7'>Nenhuma contribuicao registrada para este contribuinte.</td></tr>"}</tbody></table>
        </div>
        <div class="panel">
          <div class="section-head"><h2>Possiveis pessoas relacionadas</h2><span>{badge(len(suggestions), 'warn' if suggestions else 'ok')}</span></div>
          <div class="hint">Estas sugestoes nao criam vinculo automatico. Elas apenas ajudam a auditoria quando o banco usa abreviacoes, empresa da familia ou conta de conjuge/filho.</div>
          <table><thead><tr><th>Pessoa</th><th>Aderencia</th><th>Status</th><th>Motivo</th><th>Acao</th></tr></thead><tbody>{suggestion_rows or "<tr><td colspan='5'>Sem sugestoes fortes no momento.</td></tr>"}</tbody></table>
          {search_panel_html}
        </div>
      </div>
      <div class="panel">
        <div class="section-head"><h2>Pendencias de saneamento PIX</h2><span>{badge(len(pending_pix), 'warn' if pending_pix else 'ok')}</span></div>
        <div class="hint">Esta grade mostra movimentos PIX que ja entraram no financeiro, mas ainda dependem de associacao, classificacao ou revisao de duplicidade. Encerrar o lote apenas tira a pendencia da fila do lote; o saneamento continua aqui no contribuinte auxiliar.</div>
        <table><thead><tr><th>Recebimento</th><th>Competencia</th><th>Status</th><th>Valor</th><th>Lote</th><th>Acao</th></tr></thead><tbody>{pending_rows or "<tr><td colspan='6'>Nenhuma pendencia PIX para este contribuinte.</td></tr>"}</tbody></table>
      </div>
    """
    return render_layout(contributor["nome"], body, "contribuintes")


def render_pix_lot(db: PowerChurchDB, query: dict[str, list[str]]) -> str:
    lot_id = moneyless_int(query.get("id", ["0"])[0])
    status_filter = normalize_query(query.get("status", ["pendencias"])[0]) or "pendencias"
    confidence_group = normalize_query(query.get("grupo", [""])[0])
    current_lot_params: list[tuple[str, str]] = [("id", str(lot_id)), ("status", status_filter)]
    if confidence_group:
        current_lot_params.append(("grupo", confidence_group))
    current_lot_url = f"/pix/lote?{urllib.parse.urlencode(current_lot_params)}"
    lot = db.get_pix_lot(lot_id)
    if lot is None:
        return render_layout("Lote PIX", "<div class='empty'>Lote PIX nao encontrado.</div>", "pix")
    lot_closed = str(lot["status"]) == "encerrado"
    counts = db.pix_lot_counts(lot_id)
    financial_counts = db.pix_lot_financial_counts(lot_id)
    association_counts = db.pix_lot_association_counts(lot_id)
    review_groups = db.pix_review_person_groups(lot_id)
    special_destinations = db.scalar(
        "SELECT COUNT(*) FROM pix_movimentos WHERE lote_id = ? AND ativo = 1 AND regra_id IS NOT NULL",
        (lot_id,),
    )
    movements = db.pix_lot_movements(lot_id, status_filter=status_filter, confidence_group=confidence_group, limit=1000)
    imported_count = db.scalar("SELECT COUNT(*) FROM pix_movimentos WHERE lote_id = ? AND imported_contribution_id IS NOT NULL", (lot_id,))
    cards = [
        ("Movimentos", lot["total_movimentos"], ""),
        ("Valor do lote", br_money(lot["total_valor"]), "ok"),
        ("Lancados no financeiro", financial_counts.get("lancados", 0), "info"),
        ("Pend. associacao", association_counts.get("associacao", 0), "danger" if association_counts.get("associacao", 0) else "ok"),
        ("Pend. associacao PJ", association_counts.get("associacao_pj", 0), "warn" if association_counts.get("associacao_pj", 0) else "ok"),
        ("Saneamento pessoa", counts.get("revisar_pessoa", 0), "danger" if counts.get("revisar_pessoa", 0) else "ok"),
        ("Saneamento destinacao", counts.get("revisar_destinacao", 0), "warn" if counts.get("revisar_destinacao", 0) else "ok"),
        ("Destinacoes especiais", special_destinations, "warn" if special_destinations else "ok"),
        ("Saneamento duplicidade", counts.get("revisar_duplicidade", 0), "danger" if counts.get("revisar_duplicidade", 0) else "ok"),
        ("Regulares", financial_counts.get("regulares", 0), "ok"),
        ("Sem financeiro", financial_counts.get("sem_financeiro", 0), "warn" if financial_counts.get("sem_financeiro", 0) else "ok"),
    ]
    cards_html = "".join(
        f"<div class='card'><div class='label'>{h(label)}</div><div class='value'>{h(value)}</div>{badge(cls, cls) if cls else ''}</div>"
        for label, value, cls in cards
    )
    filter_links = [
        ("pendencias", "Saneamento geral"),
        ("associacao_pj", f"PJ p/ associar ({association_counts.get('associacao_pj', 0)})"),
        ("associacao", f"Pend. associacao ({association_counts.get('associacao', 0)})"),
        ("revisar_pessoa", "Saneamento pessoa"),
        ("revisar_destinacao", "Saneamento destinacao"),
        ("destinacoes_especiais", f"Destinacoes especiais ({special_destinations})"),
        ("revisar_duplicidade", "Saneamento duplicidade"),
        ("pronto", "Regulares auto"),
        ("aprovado", "Regularizados"),
        ("importado", "Legado importado"),
        ("ignorado", "Ignorados"),
        ("todos", "Todos"),
    ]
    filter_html = "".join(
        f"<a class='button {'primary' if status_filter == value else ''}' href='/pix/lote?id={lot_id}&status={urllib.parse.quote(value)}'>{label}</a>"
        for value, label in filter_links
    )
    review_group_links = [
        ("", f"Todos ({counts.get('revisar_pessoa', 0)})"),
        ("provavel", f"Provaveis ({review_groups.get('provavel', 0)})"),
        ("ambiguo", f"Ambiguos ({review_groups.get('ambiguo', 0)})"),
        ("pj_externo", f"PJ / identidade ({review_groups.get('pj_externo', 0)})"),
        ("sem_match", f"Sem match ({review_groups.get('sem_match', 0)})"),
    ]
    review_group_html = "".join(
        f"<a class='button {'primary' if confidence_group == value else ''}' href='/pix/lote?id={lot_id}&status=revisar_pessoa{'&grupo=' + urllib.parse.quote(value) if value else ''}'>{label}</a>"
        for value, label in review_group_links
    )
    movement_rows = []
    for row in movements:
        person_target = (
            row["resolved_person_name"]
            if moneyless_int(row["association_reviewed"])
            else row["resolved_person_name"] or row["suggested_person_name"] or ""
        )
        contributor_target = row["resolved_contributor_name"] or row["suggested_contributor_name"] or ""
        is_pj_origin = bool(moneyless_int(row["association_pending"]) and str(row["association_kind"]) == "pj") or pix_origin_is_company(
            row["documento_tipo"],
            row["nome_origem"],
        )
        origin_badges = [
            badge("PJ / identidade financeira", "warn") if is_pj_origin else badge("PF / conta pessoal", "info")
        ]
        if str(row["documento_tipo"] or "") == "cnpj":
            origin_badges.append(badge("CNPJ", "warn"))
        target_html = (
            f"{h(person_target)}<div class='hint'>{h(format_system_id(row['resolved_person_id'] or row['suggested_person_id']))}</div>"
            if person_target
            else (
                f"{h(contributor_target or row['imported_contributor_name'])}<div class='hint'>Cadastro auxiliar"
                + (" | pendencia de associacao" if moneyless_int(row["association_pending"]) else "")
                + "</div>"
                if (contributor_target or row["imported_contributor_name"])
                else "<span class='hint'>Sem alvo sugerido</span>"
            )
        )
        destination_html = h(row["resolved_tipo_nome"] or row["regra_nome"] or "Dizimo")
        action_label = "Conferir" if moneyless_int(row["imported_contribution_id"]) else "Revisar"
        movement_url = (
            f"/pix/movimento?id={row['id']}&return_to={urllib.parse.quote(current_lot_url, safe='')}"
        )
        action_html = f"<a class='button small primary' href='{movement_url}'> {action_label} </a>"
        if moneyless_int(row["imported_contribution_id"]):
            action_html += f" <span class='hint'>Contribuicao #{row['imported_contribution_id']}</span>"
        status_html = (
            badge("Pend. associacao PJ", "warn")
            if moneyless_int(row["association_pending"]) and str(row["association_kind"]) == "pj"
            else badge("Pend. associacao", "danger")
            if moneyless_int(row["association_pending"])
            else badge("NR revisado", "info")
            if moneyless_int(row["imported_contribution_id"]) and moneyless_int(row["association_reviewed"])
            else contribution_operational_status_badge("duplicidade_suspeita")
            if moneyless_int(row["imported_contribution_id"]) and str(row["review_status"]) == "revisar_duplicidade"
            else contribution_operational_status_badge("classificacao_pendente")
            if moneyless_int(row["imported_contribution_id"]) and str(row["review_status"]) == "revisar_destinacao"
            else contribution_operational_status_badge("regular")
            if moneyless_int(row["imported_contribution_id"]) and str(row["review_status"]) in {"pronto", "aprovado", "importado"}
            else pix_review_status_badge(row["review_status"])
        )
        movement_rows.append(
            "<tr>"
            f"<td>{h(br_date(row['data_recebimento']))}</td>"
            f"<td class='right'>{h(br_money(row['valor']))}</td>"
            f"<td>{h(row['nome_origem'])}<div class='hint'>{h(row['documento_mascarado'])}</div><div class='hint' style='margin-top:6px'>{''.join(origin_badges)}</div></td>"
            f"<td>{pix_rule_badge(row['codigo_centavos'], row['regra_nome'] or ('Dizimo default' if row['tipo_sugerido'] == 'dizimo' else 'Especial'))}</td>"
            f"<td>{pix_confidence_badge(row['confidence'])}</td>"
            f"<td>{target_html}</td>"
            f"<td>{h(destination_html)}</td>"
            f"<td>{status_html}</td>"
            f"<td>{action_html}</td>"
            "</tr>"
        )
    lot_actions_html = (
        f"""
        <form method="post" action="/pix/lote/reprocessar" style="display:inline">
          <input type="hidden" name="lot_id" value="{lot_id}">
          <input type="hidden" name="return_to" value="{h(current_lot_url)}">
          <button class="button" type="submit">Reprocessar pendentes</button>
        </form>
        <form method="post" action="/pix/lote/importar" style="display:inline">
          <input type="hidden" name="lot_id" value="{lot_id}">
          <input type="hidden" name="return_to" value="{h(current_lot_url)}">
          <button class="button primary" type="submit">Sincronizar financeiro</button>
        </form>
        <form method="post" action="/pix/lote/encerrar" style="display:inline">
          <input type="hidden" name="lot_id" value="{lot_id}">
          <input type="hidden" name="return_to" value="{h(current_lot_url)}">
          <button class="button" type="submit">Encerrar processamento do lote</button>
        </form>
        """
        if not lot_closed
        else "<span class='hint'>Lote encerrado: o trabalho futuro segue pela fila de contribuintes pendentes.</span>"
    )
    closed_panel_html = (
        "<div class='panel'><div class='hint'><b>Lote encerrado.</b> O que restou sem pessoa vinculada saiu da fila do lote e agora deve ser tratado pela aba <b>Contribuintes pendentes</b>. O lote fica apenas como memoria da remessa.</div></div>"
        if lot_closed
        else ""
    )
    body = f"""
      <div class="actions">
        <a class="button" href="/pix">Voltar para PIX</a>
        <a class="button" href="/pix/regras">Regras</a>
        <a class="button" href="/contribuintes?mode=pendentes">Contribuintes pendentes</a>
        {lot_actions_html}
      </div>
      {message_box(query)}
      <h1>Lote PIX #{lot_id}</h1>
      <div class="hint">Arquivo original: <b>{h(lot['nome_arquivo'])}</b>. Periodo {h(br_date(lot['periodo_inicio']))} ate {h(br_date(lot['periodo_fim']))}. Status atual {pix_lot_status_badge(lot['status'])}. Neste modelo, o lote e apenas fila de saneamento: cada movimento PIX ja entra no financeiro, e o que fica aqui sao ajustes de pessoa, destinacao ou duplicidade. A fila <b>Revisar pessoa</b> significa: o sistema ainda nao conseguiu afirmar com seguranca quem e o pagador daquele PIX. A fila <b>Revisar duplicidade</b> significa: o sistema encontrou ocorrencias equivalentes em outro documento, no banco auxiliar ou em contribuicoes PIX ja registradas. Repeticoes dentro deste mesmo lote nao entram aqui so por serem iguais.</div>
      {closed_panel_html}
      <div class="grid">{cards_html}</div>
      <div class="panel">
        <h2>Filtros do lote</h2>
        <div class="actions">{filter_html}</div>
        {'<div class="hint" style="margin-top:10px">Dentro de revisar pessoa, a fila prioriza primeiro os casos com maior aderencia e sugestao mais facil de resolver. Voce tambem pode separar por natureza do problema, inclusive isolar os casos de <b>PJ / identidade financeira</b> para revisar CNPJs em bloco.</div><div class="actions" style="margin-top:10px">' + review_group_html + '</div>' if status_filter == 'revisar_pessoa' else ''}
        {'<div class="hint" style="margin-top:10px">Estas linhas ja estao lancadas no financeiro, mas continuam sem pessoa vinculada. Elas aparecem aqui para facilitar a associacao antes de voce depender apenas da aba de contribuintes.</div>' if status_filter in {'associacao', 'associacao_pj', 'pendencias'} and association_counts.get('associacao', 0) else ''}
        {'<div class="hint" style="margin-top:10px">Este filtro reune todas as destinacoes especiais do lote, inclusive as ja aprovadas. Assim os lancamentos 01..12 nao ficam escondidos dentro do filtro Todos.</div>' if status_filter == 'destinacoes_especiais' else ''}
      </div>
      <div class="panel">
        <h2>{len(movements)} movimento(s) exibido(s)</h2>
        <table>
          <thead><tr><th>Recebimento</th><th class="right">Valor</th><th>Origem</th><th>Centavos</th><th>Match</th><th>Destino pessoa/contribuinte</th><th>Tipo</th><th>Status</th><th>Acao</th></tr></thead>
          <tbody>{''.join(movement_rows) if movement_rows else "<tr><td colspan='9'>Nenhum movimento encontrado para este filtro.</td></tr>"}</tbody>
        </table>
      </div>
    """
    return render_layout(f"Lote PIX #{lot_id}", body, "pix")


def render_pix_movement(db: PowerChurchDB, query: dict[str, list[str]]) -> str:
    movement_id = moneyless_int(query.get("id", ["0"])[0])
    movement = db.get_pix_movement(movement_id)
    if movement is None:
        return render_layout("Movimento PIX", "<div class='empty'>Movimento PIX nao encontrado.</div>", "pix")
    organization_id = moneyless_int(movement["organizacao_id"])
    types = db.contribution_types(organization_id)
    lot_id = moneyless_int(movement["lote_id"])
    default_return_to = f"/pix/lote?{urllib.parse.urlencode([('id', str(lot_id)), ('status', 'pendencias')])}"
    return_to = safe_redirect_path(query.get("return_to", [default_return_to])[0], default_return_to)
    lookup = normalize_query(query.get("lookup", [""])[0])
    audit_people_cache = db.people_for_audit_matching(organization_id)
    engine_candidates = db.pix_candidate_suggestions(
        organization_id,
        str(movement["nome_origem"]),
        str(movement["documento_mascarado"] or ""),
        str(movement["documento_tipo"] or ""),
        people_cache=audit_people_cache,
        limit=18,
    )
    manual_candidates = db.list_people(q=lookup, limit=80) if lookup else []
    pending_count_map = db.audit_count_by_person()
    candidate_map: dict[int, dict[str, object]] = {}
    for item in engine_candidates:
        person = db.get_person(moneyless_int(item["id"]))
        if person is None:
            continue
        person_data = dict(person)
        person_data["engine_score"] = float(item["score"])
        person_data["engine_reason"] = str(item["reason"])
        person_data["engine_source"] = "motor"
        person_data["engine_doc_match"] = bool(item.get("doc_match"))
        person_data["engine_exact_name"] = bool(item.get("exact_name"))
        person_data["engine_ratio"] = float(item.get("fuzzy_ratio", 0))
        person_data["engine_identity_hint"] = " | ".join(
            bit for bit in [
                str(item.get("matched_alias_name") or ""),
                str(item.get("matched_identity_source") or ""),
                f"{item.get('matched_identity_kind')}: {item.get('matched_identity_value')}"
                if item.get("matched_identity_value")
                else "",
            ] if bit
        )
        person_data["pendencias"] = pending_count_map.get(moneyless_int(person["id"]), 0)
        candidate_map[moneyless_int(person["id"])] = person_data
    for row in manual_candidates:
        person_id = moneyless_int(row["id"])
        if person_id in candidate_map:
            candidate_map[person_id]["engine_source"] = "motor+busca"
            candidate_map[person_id]["engine_reason"] = "tambem retornou na busca ampla do cadastro"
            continue
        manual_data = dict(row)
        manual_data["engine_score"] = 0.0
        manual_data["engine_reason"] = "resultado da busca ampla no cadastro"
        manual_data["engine_source"] = "busca"
        manual_data["engine_doc_match"] = False
        manual_data["engine_exact_name"] = normalize_match_name(movement["nome_origem"]) == normalize_match_name(row["nome"])
        manual_data["engine_ratio"] = pix_name_similarity_ratio(movement["nome_origem"], row["nome"])
        manual_data["engine_identity_hint"] = ""
        manual_data["pendencias"] = pending_count_map.get(person_id, 0)
        candidate_map[person_id] = manual_data
    highlighted_person_id = (
        moneyless_int(movement["resolved_person_id"])
        if moneyless_int(movement["association_reviewed"])
        else moneyless_int(movement["resolved_person_id"]) or moneyless_int(movement["suggested_person_id"])
    )
    suggested_person = db.get_person(highlighted_person_id)
    if suggested_person is not None:
        person_id = moneyless_int(suggested_person["id"])
        if person_id not in candidate_map:
            candidate_map[person_id] = {
                **dict(suggested_person),
                "engine_score": 0.0,
                "engine_reason": "pessoa ja sugerida neste movimento",
                "engine_source": "sugerido",
                "engine_doc_match": False,
                "engine_exact_name": normalize_match_name(movement["nome_origem"]) == normalize_match_name(suggested_person["nome"]),
                "engine_ratio": pix_name_similarity_ratio(movement["nome_origem"], suggested_person["nome"]),
                "engine_identity_hint": "",
                "pendencias": pending_count_map.get(person_id, 0),
            }
    candidate_rows = list(candidate_map.values())
    for row in candidate_rows:
        ratio = float(row.get("engine_ratio", 0) or 0)
        exact_name = bool(row.get("engine_exact_name"))
        doc_match = bool(row.get("engine_doc_match"))
        label, css_class, bucket = pix_candidate_similarity(doc_match, exact_name, ratio)
        row["similarity_ratio"] = ratio
        row["similarity_label"] = label
        row["similarity_class"] = css_class
        row["similarity_bucket"] = bucket
    candidate_rows.sort(
        key=lambda row: (
            0 if moneyless_int(row["id"]) == highlighted_person_id else 1,
            0 if lookup and "busca" in str(row.get("engine_source", "")) else 1,
            {"aderente": 0, "parcial": 1, "distante": 2}.get(str(row.get("similarity_bucket")), 3),
            0 if str(row.get("engine_source")) in {"motor", "motor+busca", "sugerido"} else 1,
            -float(row.get("engine_score", 0)),
            str(row["nome"]),
        )
    )
    candidate_rows = candidate_rows[:60]
    related_candidates = [row for row in candidate_rows if str(row.get("similarity_bucket")) == "aderente"]
    partial_candidates = [row for row in candidate_rows if str(row.get("similarity_bucket")) == "parcial"]
    distant_candidates = [row for row in candidate_rows if str(row.get("similarity_bucket")) == "distante"]
    candidate_summary_parts = [
        badge(f"{len(related_candidates)} com aderencia nominal/documental", "ok"),
        badge(f"{len(partial_candidates)} com semelhanca parcial", "warn" if partial_candidates else "info"),
        badge(f"{len(distant_candidates)} sem semelhanca clara", "danger" if distant_candidates else "info"),
    ]
    default_selected_person_id = (
        0
        if moneyless_int(movement["association_reviewed"])
        else moneyless_int(movement["resolved_person_id"] or movement["suggested_person_id"] or "0")
    )
    selected_person_id = moneyless_int(query.get("resolved_person_id", [str(default_selected_person_id)])[0])
    rule_type_id = 0
    if moneyless_int(movement["regra_id"]):
        rule_row = db.conn.execute("SELECT tipo_contribuicao_id FROM pix_centavo_regras WHERE id = ?", (movement["regra_id"],)).fetchone()
        rule_type_id = moneyless_int(rule_row["tipo_contribuicao_id"] if rule_row else 0)
    duplicate_movement = db.get_pix_movement(moneyless_int(movement["duplicate_movement_id"])) if moneyless_int(movement["duplicate_movement_id"]) else None
    duplicate_contribution = db.get_contribution(moneyless_int(movement["duplicate_contribution_id"])) if moneyless_int(movement["duplicate_contribution_id"]) else None
    duplicate_panel = ""
    if movement["duplicate_reason"] or duplicate_movement is not None or duplicate_contribution is not None:
        references: list[str] = []
        if duplicate_movement is not None:
            references.append(
                f"Movimento relacionado: <a href='/pix/movimento?id={duplicate_movement['id']}'>PIX #{duplicate_movement['id']}</a>"
            )
        if duplicate_contribution is not None:
            person_ref = (
                f" na ficha <a href='/pessoa?id={duplicate_contribution['pessoa_id']}'>{h(format_system_id(duplicate_contribution['pessoa_id']))}</a>"
                if moneyless_int(duplicate_contribution["pessoa_id"])
                else ""
            )
            references.append(
                f"Contribuicao relacionada: #{duplicate_contribution['id']}{person_ref}"
            )
        duplicate_panel = (
            "<div class='panel' style='margin-top:16px'>"
            f"<h3>{pix_review_status_badge('revisar_duplicidade')} Possivel duplicidade</h3>"
            f"<div class='hint'>{h(movement['duplicate_reason'] or 'O sistema encontrou um registro muito parecido ja armazenado.')}</div>"
            f"<div class='hint' style='margin-top:8px'>{' | '.join(references) if references else 'Sem referencia adicional.'}</div>"
            "</div>"
        )
    selected_type_id = moneyless_int(
        query.get(
            "resolved_tipo_contribuicao_id",
            [str(movement["resolved_tipo_contribuicao_id"] or rule_type_id or db.pix_default_type_id(organization_id))],
        )[0]
    )
    person_return_to = f"/pix/movimento?id={movement_id}&return_to={urllib.parse.quote(return_to, safe='')}"
    financial_origin_label = (
        "PJ / empresa"
        if pix_origin_is_company(movement["documento_tipo"], movement["nome_origem"])
        else "PF / conta pessoal"
    )
    type_options = "".join(option(str(row["id"]), str(row["nome"]), selected_type_id) for row in types)
    candidate_table_rows = [
        "<tr>"
        f"<td><input type='radio' name='resolved_person_id' value='0' {'checked' if selected_person_id == 0 else ''}></td>"
        "<td><b>Sem vincular pessoa agora</b><div class='hint'>Importa como contribuinte/doador auxiliar. Ainda nao conta para o extrato do membro; fica preservado para vinculo retroativo depois.</div></td>"
        f"<td>{badge('Controle manual', 'info')}</td><td>-</td><td>-</td><td>-</td><td>-</td>"
        "</tr>"
    ]
    def add_candidate_section(title: str, hint: str, rows: list[dict[str, object]]) -> None:
        if not rows:
            return
        candidate_table_rows.append(
            "<tr>"
            f"<td colspan='7' style='background:#f8f2e7'><b>{h(title)}</b><div class='hint'>{h(hint)}</div></td>"
            "</tr>"
        )
        for row in rows:
            pendencias = moneyless_int(row.get("pendencias"))
            source_badges = audit_candidate_source_badges(row.get("engine_source"))
            identity_hint_html = (
                f"<div class='hint'>Identidade financeira relacionada: {h(str(row.get('engine_identity_hint') or ''))}</div>"
                if row.get("engine_identity_hint")
                else ""
            )
            candidate_table_rows.append(
                "<tr>"
                f"<td><input type='radio' name='resolved_person_id' value='{row['id']}' {'checked' if selected_person_id == moneyless_int(row['id']) else ''}></td>"
                f"<td><b>{h(row['nome'])}</b><div class='hint'>{h(format_system_id(row['id']))} | {h(format_member_code(row.get('codigo_interno'))) or 'Sem numero'} | CPF {h(format_cpf(row.get('cpf')))}</div></td>"
                f"<td>{pix_candidate_similarity_badge(row.get('engine_doc_match'), row.get('engine_exact_name'), row.get('similarity_ratio'))}</td>"
                f"<td>{h(row.get('status', ''))}</td>"
                f"<td>{h(str(row.get('engine_reason') or ''))}<div class='actions' style='margin-top:6px'>{source_badges}</div><div class='hint'>score {row.get('engine_score', 0):.2f} | semelhanca {float(row.get('similarity_ratio', 0)):.2f}</div>{identity_hint_html}</td>"
                f"<td>{badge(pendencias, 'danger' if pendencias else 'ok')}</td>"
                f"<td><a class='button small' href='/pessoa?id={row['id']}&return_to={urllib.parse.quote(person_return_to, safe='')}'>Abrir ficha</a></td>"
                "</tr>"
            )
    add_candidate_section(
        "Sugestoes com alguma aderencia",
        "Aqui ficam as fichas com documento compativel, nome exato ou alguma semelhanca nominal suficiente para ajudar na decisao.",
        related_candidates,
    )
    add_candidate_section(
        "Sugestoes com semelhanca parcial",
        "Aqui ficam fichas com algum sinal util, mas ainda sem aderencia forte. Normalmente sao os melhores casos para checagem humana antes dos resultados distantes.",
        partial_candidates,
    )
    add_candidate_section(
        "Resultados sem semelhanca clara",
        "Aqui ficam resultados de busca que nao apresentam aderencia nominal evidente. Eles continuam disponiveis para consulta, mas exigem mais cautela.",
        distant_candidates,
    )
    body = f"""
      <div class="actions">
        <a class="button" href="{h(return_to)}">Voltar para lote</a>
        <a class="button" href="/pix">Voltar para PIX</a>
      </div>
      {message_box(query)}
      <h1>Auditoria do movimento PIX #{movement_id}</h1>
      <div class="hint">Aqui a correção vira acao executavel: voce pode confirmar a pessoa, manter como contribuinte externo, aplicar ou trocar a destinacao e ainda associar o documento mascarado para melhorar as proximas remessas.</div>
      <div class="hint" style="margin-top:8px">Se o pagador do banco for conjuge, filho, empresa da familia ou outro nome recorrente ainda nao confirmado, nao force o vinculo com o membro. Voce pode manter o registro como contribuinte auxiliar e decidir isso depois, sem perder o historico financeiro.</div>
      <div class="hint" style="margin-top:8px">As sugestoes abaixo agora consideram tres camadas: <b>nome da pessoa</b>, <b>nome financeiro associado</b> e <b>CNPJ/CPF/documento financeiro associado</b>. Isso ajuda quando a empresa usa nome de pessoa ou quando a familia contribui por outra identidade bancaria. Se voce usar a busca complementar, ela consulta o <b>cadastro inteiro</b>, incluindo membro ativo, inativo, frequentador, visitante e arquivo morto.</div>
      <div class="detail-grid">
        <div class="panel">
          <div class="section-head"><h2>Movimento bancario</h2><span>{pix_review_status_badge(movement['review_status'])}</span></div>
          <div class="field-grid">
            {field_card('Recebimento', br_date(movement['data_recebimento']))}
            {field_card('Competencia', movement['competencia'])}
            {field_card('Valor', br_money(movement['valor']))}
            {field_card_html('Confianca do match', pix_confidence_badge(movement['confidence']))}
            {field_card('Pagador no banco', movement['nome_origem'], 'wide-field')}
            {field_card('Documento mascarado', movement['documento_mascarado'])}
            {field_card_html('Origem financeira', badge(financial_origin_label, 'warn' if financial_origin_label.startswith('PJ') else 'info'))}
            {field_card_html('Regra de centavos', pix_rule_badge(movement['codigo_centavos'], movement['regra_nome'] or ('Dizimo default' if movement['tipo_sugerido'] == 'dizimo' else 'Especial')))}
            {field_card('Observacao do motor', movement['review_notes'], 'wide-field')}
          </div>
          <div class="panel" style="margin-top:16px">
            <h3>Como este PIX pode ser tratado</h3>
            <table>
              <thead><tr><th>Escolha na auditoria</th><th>Resultado operacional</th></tr></thead>
              <tbody>
                <tr><td><b>Selecionar uma pessoa na lista</b></td><td>Credita a contribuicao ao membro/frequentador escolhido e preserva o pagador bancario como origem financeira. Isso permite, por exemplo, contar uma doacao feita pelo CNPJ da empresa no extrato do membro.</td></tr>
                <tr><td><b>Sem vincular pessoa agora</b></td><td>Mantem o registro so no cadastro de contribuintes auxiliares. O valor nao some, mas tambem nao entra ainda no extrato do membro ate a vinculacao posterior.</td></tr>
              </tbody>
            </table>
          </div>
          <div class="panel" style="margin-top:16px">
            <h3>Texto bruto da remessa</h3>
            <pre style="white-space:pre-wrap; margin:0">{h(movement['raw_text'])}</pre>
          </div>
          {duplicate_panel}
        </div>
        <div class="panel">
          <div class="section-head"><h2>Opcoes de pessoa para este PIX</h2><span>{badge(len(candidate_rows), 'info')}</span></div>
          <div class="hint">A lista abaixo agora abre primeiro com as sugestoes do proprio motor de match. Ela tambem separa o que tem aderencia forte, semelhanca parcial e ausencia de semelhanca clara, para a auditoria ficar mais objetiva.</div>
          <div class="actions" style="margin:10px 0">{''.join(candidate_summary_parts)}</div>
          <form class="filters" method="get" action="/pix/movimento">
            <input type="hidden" name="id" value="{movement_id}">
            <input type="hidden" name="return_to" value="{h(return_to)}">
            <label class="wide">Busca complementar<input name="lookup" value="{h(lookup)}" placeholder="reforce a busca por nome, MEM-00003, ID-001078 ou CPF"></label>
            <button class="button primary" type="submit">Pesquisar pessoas</button>
          </form>
          <form method="post" action="/pix/movimento/salvar">
            <input type="hidden" name="movement_id" value="{movement_id}">
            <input type="hidden" name="return_to" value="{h(return_to)}">
            <table>
              <thead><tr><th>Usar</th><th>Pessoa sugerida</th><th>Aderencia</th><th>Status</th><th>Motivo da sugestao</th><th>Pend.</th><th>Ficha</th></tr></thead>
              <tbody>{''.join(candidate_table_rows) if candidate_table_rows else "<tr><td colspan='7'>Nenhuma opcao encontrada. Use a busca complementar ou mantenha como contribuinte auxiliar.</td></tr>"}</tbody>
            </table>
            <div class="form-grid" style="margin-top:16px">
              <label>Tipo de contribuicao<select name="resolved_tipo_contribuicao_id">{type_options}</select></label>
              <label>Associar documento mascarado?
                <select name="associate_masked_document">
                  <option value="1" {'selected' if movement['documento_mascarado'] else ''}>Sim</option>
                  <option value="0">Nao</option>
                </select>
              </label>
              {textarea_field('review_notes', 'Observacoes da auditoria', movement['review_notes'] or '', css_class='wide')}
            </div>
            <div class="actions">
              <button class="button primary" type="submit" name="action" value="approve">Confirmar movimento</button>
              <button class="button" type="submit" name="action" value="ignore">Ignorar este movimento</button>
            </div>
          </form>
          <p class="hint">Se voce nao vincular uma pessoa agora, o lancamento ainda pode entrar como contribuinte auxiliar. Isso preserva a doacao e permite reprocessar depois, quando o cadastro estiver mais completo.</p>
        </div>
      </div>
    """
    return render_layout(f"Movimento PIX #{movement_id}", body, "pix")


def build_statement_candidate_rows(
    db: PowerChurchDB,
    movement: sqlite3.Row,
    lookup: str = "",
) -> list[dict[str, object]]:
    organization_id = moneyless_int(movement["organizacao_id"])
    source_name = normalize_query(movement["nome_origem"])
    source_norm = normalize_match_name(source_name)
    source_tokens = significant_name_tokens(source_name)
    statement_document = normalize_query(movement["bank_document"])
    audit_people_cache = db.people_for_audit_matching(organization_id)
    pending_count_map = db.audit_count_by_person()
    if not source_name and not lookup:
        if not statement_document:
            return []
    candidate_map: dict[int, dict[str, object]] = {}

    def pendencias_for_person(person_id: int) -> int:
        return pending_count_map.get(person_id, 0)

    def upsert_candidate(
        person_row: sqlite3.Row | dict[str, object],
        *,
        source: str,
        reason: str,
        score: float,
        ratio: float,
        exact_name: bool,
        alias_name: str = "",
    ) -> None:
        person_id = moneyless_int(person_row["id"])
        current = candidate_map.get(person_id)
        payload = {
            **dict(person_row),
            "engine_source": source,
            "engine_reason": reason,
            "engine_score": float(score),
            "engine_ratio": float(ratio),
            "engine_exact_name": bool(exact_name),
            "engine_alias_name": alias_name,
            "pendencias": pendencias_for_person(person_id),
        }
        if current is None or float(payload["engine_score"]) > float(current.get("engine_score", 0)):
            candidate_map[person_id] = payload
        elif source not in str(current.get("engine_source", "")):
            current["engine_source"] = f"{current['engine_source']}+{source}"

    search_terms: list[str] = []
    if source_name:
        search_terms.append(source_name)
    if lookup and lookup not in search_terms:
        search_terms.append(lookup)
    for term in search_terms:
        for row in db.list_people(q=term, limit=80):
            exact_name = normalize_match_name(row["nome"]) == source_norm and bool(source_norm)
            ratio = pix_name_similarity_ratio(source_name or term, row["nome"])
            reason = "resultado da busca ampla no cadastro"
            if exact_name:
                reason = "nome exato encontrado no cadastro"
            upsert_candidate(
                row,
                source="busca",
                reason=reason,
                score=max(0.7, ratio),
                ratio=ratio,
                exact_name=exact_name,
            )

    for item in audit_people_cache:
        person_id = moneyless_int(item["id"])
        best_reason = ""
        best_source = "motor"
        best_exact = str(item["name_norm"]) == source_norm and bool(source_norm)
        person_name = str(item.get("nome") or "")
        person_has_overlap = bool(source_name) and (
            not source_tokens or bool(source_tokens & significant_name_tokens(person_name))
        )
        best_ratio = pix_name_similarity_ratio(source_name, person_name) if source_name and (best_exact or person_has_overlap) else 0.0
        best_alias_name = ""
        identifier_match = None
        if statement_document:
            for identifier in item.get("identifiers", []):
                identifier_value = normalize_query(identifier.get("value"))
                if document_query_matches(statement_document, identifier_value):
                    identifier_match = identifier
                    break
        if identifier_match is not None:
            best_ratio = max(best_ratio, 1.0 if best_exact else 0.98)
            best_reason = f"documento financeiro compativel com '{identifier_match.get('value')}'"
            best_source = "identidade"
        elif best_exact:
            best_reason = "nome exato do cadastro"
        for alias in item.get("financial_aliases", []):
            alias_name = str(alias.get("name") or "")
            alias_norm = str(alias.get("name_norm") or "")
            alias_exact = alias_norm == source_norm and bool(source_norm)
            alias_has_overlap = bool(source_name) and (
                not source_tokens or bool(source_tokens & significant_name_tokens(alias_name))
            )
            if not alias_exact and not alias_has_overlap and identifier_match is None:
                continue
            alias_ratio = pix_name_similarity_ratio(source_name, alias_name) if source_name and (alias_exact or alias_has_overlap) else 0.0
            if alias_exact:
                best_ratio = 1.0
                best_exact = True
                best_reason = f"nome bate com identidade financeira '{alias_name}'"
                best_source = "identidade"
                best_alias_name = alias_name
                break
            if alias_ratio > best_ratio:
                best_ratio = alias_ratio
                best_reason = f"nome proximo de identidade financeira '{alias_name}'"
                best_source = "identidade"
                best_alias_name = alias_name
        if not best_reason:
            if not person_has_overlap and not best_alias_name:
                continue
            if best_ratio >= 0.97:
                best_reason = f"nome muito proximo do cadastro ({best_ratio:.2f})"
            elif best_ratio >= 0.9:
                best_reason = f"nome proximo do cadastro ({best_ratio:.2f})"
            elif best_ratio >= 0.84:
                best_reason = f"nome parcialmente compativel ({best_ratio:.2f})"
            else:
                continue
        person = db.get_person(person_id)
        if person is None:
            continue
        upsert_candidate(
            person,
            source=best_source,
            reason=best_reason,
            score=1.0 if best_exact or identifier_match is not None else best_ratio,
            ratio=max(best_ratio, 0.99 if identifier_match is not None else best_ratio),
            exact_name=best_exact or identifier_match is not None,
            alias_name=best_alias_name,
        )

    rows = list(candidate_map.values())
    highlighted_person_id = (
        moneyless_int(movement["resolved_person_id"])
        if moneyless_int(movement["association_reviewed"])
        else moneyless_int(movement["resolved_person_id"] or movement["suggested_person_id"])
    )
    for row in rows:
        label, css_class, bucket = pix_candidate_similarity(False, row.get("engine_exact_name"), row.get("engine_ratio"))
        row["similarity_label"] = label
        row["similarity_class"] = css_class
        row["similarity_bucket"] = bucket
    rows.sort(
        key=lambda row: (
            0 if moneyless_int(row["id"]) == highlighted_person_id else 1,
            0 if lookup and "busca" in str(row.get("engine_source", "")) else 1,
            {"aderente": 0, "parcial": 1, "distante": 2}.get(str(row.get("similarity_bucket")), 3),
            0 if str(row.get("engine_source")) in {"identidade", "motor", "busca+identidade", "busca+motor"} else 1,
            -float(row.get("engine_score", 0)),
            str(row.get("nome") or ""),
        )
    )
    return rows[:60]


def render_statement_home(db: PowerChurchDB, query: dict[str, list[str]]) -> str:
    lots = db.statement_lots(30)
    default_org = db.default_organization_id()
    rules = db.pix_rules(default_org)
    special_destinations = db.scalar("SELECT COUNT(*) FROM extrato_movimentos WHERE ativo = 1 AND regra_id IS NOT NULL")
    cards = [
        ("Lotes de extrato", db.scalar("SELECT COUNT(*) FROM extrato_lotes"), "info"),
        (
            "Movimentos em saneamento",
            db.scalar("SELECT COUNT(*) FROM extrato_movimentos WHERE ativo = 1 AND review_status IN ('revisar_pessoa', 'revisar_destinacao', 'revisar_duplicidade')"),
            "warn",
        ),
        (
            "Lancados financeiramente",
            db.scalar("SELECT COUNT(*) FROM extrato_movimentos WHERE ativo = 1 AND imported_contribution_id IS NOT NULL"),
            "ok",
        ),
        (
            "Sem nome de origem",
            db.scalar("SELECT COUNT(*) FROM extrato_movimentos WHERE ativo = 1 AND TRIM(COALESCE(nome_origem, '')) = ''"),
            "warn",
        ),
        (
            "Sem associacao",
            db.scalar(
                f"""
                SELECT COUNT(*)
                FROM extrato_movimentos em
                LEFT JOIN contribuicoes c ON c.id = em.imported_contribution_id
                LEFT JOIN contribuintes ct ON ct.id = c.contribuinte_id
                WHERE em.ativo = 1
                  AND {statement_association_pending_expr('em', 'c', 'ct')}
                """
            ),
            "danger",
        ),
        ("Contribuintes auxiliares", db.scalar("SELECT COUNT(*) FROM contribuintes WHERE ativo = 1"), "info"),
        ("Destinacoes especiais", special_destinations, "warn" if special_destinations else "ok"),
        ("Regras por centavos", len(rules), "warn"),
    ]
    cards_html = "".join(
        f"<div class='card'><div class='label'>{h(label)}</div><div class='value'>{h(value)}</div>{badge(cls, cls) if cls else ''}</div>"
        for label, value, cls in cards
    )
    lot_rows = []
    for row in lots:
        lot_rows.append(
            "<tr>"
            f"<td><a href='/extratos/lote?id={row['id']}'>Lote #{row['id']}</a><div class='hint'>{h(row['nome_arquivo'])}</div></td>"
            f"<td>{h(row['banco'])}<div class='hint'>{h(statement_layout_label(row['layout_codigo']))}</div></td>"
            f"<td>{h(br_date(row['periodo_inicio']))} ate {h(br_date(row['periodo_fim']))}</td>"
            f"<td class='right'>{row['total_movimentos']}</td>"
            f"<td class='right'>{h(br_money(row['total_valor']))}</td>"
            f"<td>{pix_lot_status_badge(row['status'])}</td>"
            f"<td><a class='button small primary' href='/extratos/lote?id={row['id']}'>Abrir lote</a></td>"
            "</tr>"
        )
    rule_preview = "".join(
        f"<tr><td>{pix_rule_badge(row['codigo_centavos'], row['nome_destinacao'])}</td><td>{h(row['tipo_nome'] or 'Sem tipo')}</td><td>{badge('Ativa' if row['ativo'] else 'Inativa', 'ok' if row['ativo'] else 'warn')}</td></tr>"
        for row in rules[:12]
    )
    body = f"""
      <div class="actions">
        <a class="button" href="/">Inicio</a>
        <a class="button" href="/importacoes">Central de importacoes</a>
        <a class="button" href="/pix">PIX Sicoob</a>
        <a class="button" href="/extratos/regras">Regras por centavos</a>
        <a class="button" href="/contribuintes">Contribuintes</a>
        <a class="button" href="/contribuicoes">Contribuicoes</a>
      </div>
      {message_box(query)}
      <h1>Importacao de extrato bancario</h1>
      <div class="hint">Este modulo concentra os parsers de <b>extrato bancario</b>. O motor de saneamento e financeiro e compartilhado; o que muda de banco para banco e a leitura do PDF. Aqui entram creditos de terceiros que precisam virar contribuicao, contribuinte auxiliar, mesma titularidade ou item ignorado operacionalmente.</div>
      <div class="grid">{cards_html}</div>
      <div class="detail-grid">
        <div class="panel">
          <h2>Novo lote Bradesco</h2>
          <div class="hint">Fluxo de producao ja homologado para o extrato Bradesco.</div>
          <form method="post" action="/extratos/lotes/upload" enctype="multipart/form-data">
            <input type="hidden" name="layout_code" value="BRADESCO_EXTRATO">
            <input type="hidden" name="return_to" value="/extratos">
            <div class="form-grid">
              {field_card_html('Layout ativo', badge('Bradesco Extrato PJ', 'info'))}
              <label class="wide">PDF do extrato bancario<input type="file" name="extrato_pdf" accept=".pdf,application/pdf" required></label>
            </div>
            <div class="actions">
              <button class="button primary" type="submit">Criar lote Bradesco</button>
              <a class="button" href="/extratos/regras">Conferir regras de centavos</a>
            </div>
          </form>
        </div>
        <div class="panel">
          <h2>Novo lote Santander</h2>
          <div class="hint">Parser automatico para o extrato consolidado e nao consolidado. A associacao e feita por CPF/CNPJ completo, ja que o Santander nao informa nome do remetente.</div>
          <form method="post" action="/extratos/lotes/upload" enctype="multipart/form-data">
            <input type="hidden" name="layout_code" value="SANTANDER_AUTO">
            <input type="hidden" name="return_to" value="/extratos">
            <div class="form-grid">
              {field_card_html('Layout automatico', badge('Santander CPF/CNPJ', 'info'))}
              <label class="wide">PDF do extrato Santander<input type="file" name="extrato_pdf" accept=".pdf,application/pdf" required></label>
            </div>
            <div class="actions">
              <button class="button primary" type="submit">Criar lote Santander</button>
              <a class="button" href="/extratos/regras">Conferir regras de centavos</a>
            </div>
          </form>
        </div>
        <div class="panel">
          <h2>Novo lote Sicoob</h2>
          <div class="hint">Parser novo para o <b>extrato de recebimentos</b> do Sicoob. Nesta fase, ele existe para homologacao controlada e comparacao com os lotes historicos de PIX.</div>
          <form method="post" action="/extratos/lotes/upload" enctype="multipart/form-data">
            <input type="hidden" name="layout_code" value="SICOOB_RECEBIMENTOS">
            <input type="hidden" name="return_to" value="/extratos">
            <div class="form-grid">
              {field_card_html('Layout em homologacao', badge('Sicoob Extrato de Recebimentos', 'warn'))}
              <label class="wide">PDF do extrato de recebimentos<input type="file" name="extrato_pdf" accept=".pdf,application/pdf" required></label>
            </div>
            <div class="actions">
              <button class="button primary" type="submit">Criar lote Sicoob</button>
              <a class="button" href="/extratos/regras">Conferir regras de centavos</a>
            </div>
          </form>
        </div>
      </div>
      <div class="detail-grid">
        <div class="panel">
          <div class="section-head"><h2>Lotes recentes</h2><span>{badge(len(lots), 'info')}</span></div>
          <table>
            <thead><tr><th>Lote</th><th>Banco</th><th>Periodo</th><th class="right">Mov.</th><th class="right">Valor</th><th>Status</th><th>Acao</th></tr></thead>
            <tbody>{''.join(lot_rows) if lot_rows else "<tr><td colspan='7'>Nenhum lote de extrato criado ainda.</td></tr>"}</tbody>
          </table>
        </div>
        <div class="panel">
          <div class="section-head"><h2>Tabela de centavos ativa</h2><span>{badge(len(rules), 'warn')}</span></div>
          <div class="hint">Os extratos bancarios usam a mesma tabela de centavos do PIX Sicoob. Assim, so muda o parser do banco: a interpretacao das destinacoes especiais e a interface de conferencia continuam iguais.</div>
          <table>
            <thead><tr><th>Codigo</th><th>Destino</th><th>Status</th></tr></thead>
            <tbody>{rule_preview or "<tr><td colspan='3'>Nenhuma regra de centavos cadastrada.</td></tr>"}</tbody>
          </table>
        </div>
      </div>
    """
    return render_layout("Extratos", body, "extratos")


def render_statement_lot(db: PowerChurchDB, query: dict[str, list[str]]) -> str:
    lot_id = moneyless_int(query.get("id", ["0"])[0])
    status_filter = normalize_query(query.get("status", ["pendencias"])[0]) or "pendencias"
    current_lot_url = f"/extratos/lote?{urllib.parse.urlencode([('id', str(lot_id)), ('status', status_filter)])}"
    lot = db.get_statement_lot(lot_id)
    if lot is None:
        return render_layout("Lote de extrato", "<div class='empty'>Lote de extrato nao encontrado.</div>", "extratos")
    lot_closed = str(lot["status"]) == "encerrado"
    layout_code = normalize_query(lot["layout_codigo"]).upper()
    default_org = db.default_organization_id()
    rules = db.pix_rules(default_org)
    counts = db.statement_lot_review_counts(lot_id)
    financial_counts = db.statement_lot_financial_counts(lot_id)
    special_destinations = db.scalar(
        "SELECT COUNT(*) FROM extrato_movimentos WHERE lote_id = ? AND ativo = 1 AND regra_id IS NOT NULL",
        (lot_id,),
    )
    movements = db.statement_lot_movements(lot_id, status_filter=status_filter, limit=1000)
    cards = [
        ("Movimentos", lot["total_movimentos"], "info"),
        ("Valor do lote", br_money(lot["total_valor"]), "ok"),
        ("Lancados no financeiro", financial_counts.get("lancados", 0), "ok"),
        ("Sem associacao", financial_counts.get("sem_associacao", 0), "danger" if financial_counts.get("sem_associacao", 0) else "ok"),
        ("Saneamento pessoa", counts.get("revisar_pessoa", 0), "warn" if counts.get("revisar_pessoa", 0) else "ok"),
        ("Saneamento destinacao", counts.get("revisar_destinacao", 0), "warn" if counts.get("revisar_destinacao", 0) else "ok"),
        ("Destinacoes especiais", special_destinations, "warn" if special_destinations else "ok"),
        ("Saneamento duplicidade", counts.get("revisar_duplicidade", 0), "danger" if counts.get("revisar_duplicidade", 0) else "ok"),
        ("Ignorados", counts.get("ignorado", 0), "info" if counts.get("ignorado", 0) else "ok"),
        ("Classificacao pendente", financial_counts.get("classificacao_pendente", 0), "warn" if financial_counts.get("classificacao_pendente", 0) else "ok"),
        ("Regulares", financial_counts.get("regulares", 0), "ok"),
        ("Sem financeiro", financial_counts.get("sem_financeiro", 0), "warn" if financial_counts.get("sem_financeiro", 0) else "ok"),
    ]
    cards_html = "".join(
        f"<div class='card'><div class='label'>{h(label)}</div><div class='value'>{h(value)}</div>{badge(cls, cls) if cls else ''}</div>"
        for label, value, cls in cards
    )
    filter_links = [
        ("pendencias", "Saneamento geral"),
        ("associacao", "Pend. associacao"),
        ("revisar_pessoa", "Saneamento pessoa"),
        ("revisar_destinacao", "Saneamento destinacao"),
        ("destinacoes_especiais", f"Destinacoes especiais ({special_destinations})"),
        ("revisar_duplicidade", "Saneamento duplicidade"),
        ("pronto", "Regulares auto"),
        ("aprovado", "Regularizados"),
        ("importado", "Legado importado"),
        ("ignorado", "Ignorados"),
        ("todos", "Todos"),
    ]
    filter_html = "".join(
        f"<a class='button {'primary' if status_filter == value else ''}' href='/extratos/lote?id={lot_id}&status={urllib.parse.quote(value)}'>{label}</a>"
        for value, label in filter_links
    )
    rule_preview = "".join(
        f"<tr><td>{pix_rule_badge(row['codigo_centavos'], row['nome_destinacao'])}</td><td>{h(row['tipo_nome'] or 'Sem tipo')}</td><td>{badge('Ativa' if row['ativo'] else 'Inativa', 'ok' if row['ativo'] else 'warn')}</td></tr>"
        for row in rules[:8]
    )
    movement_rows = []
    for row in movements:
        person_id = moneyless_int(row["resolved_person_id"] or row["suggested_person_id"])
        person_cpf = row["resolved_person_cpf"] or row["suggested_person_cpf"] or ""
        person_target = (
            row["resolved_person_name"]
            if moneyless_int(row["association_reviewed"])
            else row["resolved_person_name"] or row["suggested_person_name"] or ""
        )
        contributor_target = row["resolved_contributor_name"] or row["suggested_contributor_name"] or row["imported_contributor_name"] or ""
        target_html = (
            f"{h(person_target)}<div class='hint'>{h(format_system_id(person_id))} | CPF {h(format_cpf(person_cpf) or 'Nao informado')} {statement_person_document_compare_badge(row['bank_document'], person_cpf)}</div>"
            if person_target
            else (
                f"{h(contributor_target)}<div class='hint'>Contribuinte auxiliar</div>"
                if contributor_target
                else "<span class='hint'>Sem pessoa / sem contribuinte</span>"
            )
        )
        if moneyless_int(row["association_pending"]):
            status_html = contribution_operational_status_badge("sem_associacao")
        elif moneyless_int(row["imported_contribution_id"]) and moneyless_int(row["association_reviewed"]):
            status_html = badge("NR revisado", "info")
        elif moneyless_int(row["imported_contribution_id"]) and str(row["review_status"]) == "revisar_duplicidade":
            status_html = contribution_operational_status_badge("duplicidade_suspeita")
        elif moneyless_int(row["imported_contribution_id"]) and str(row["review_status"]) == "revisar_destinacao":
            status_html = contribution_operational_status_badge("classificacao_pendente")
        elif moneyless_int(row["imported_contribution_id"]):
            status_html = contribution_operational_status_badge("regular")
        else:
            status_html = pix_review_status_badge(row["review_status"])
        origin_name = str(row["nome_origem"] or "") or str(row["origin_label"] or "")
        destination_html = h(row["resolved_tipo_nome"] or row["regra_nome"] or "Dizimo")
        movement_rows.append(
            "<tr>"
            f"<td>{h(br_date(row['data_movimento']))}</td>"
            f"<td class='right'>{h(br_money(row['valor']))}</td>"
            f"<td>{h(origin_name or 'Sem nome identificado')}<div class='hint'>Docto {h(row['bank_document'] or '-')}</div></td>"
            f"<td>{statement_movement_kind_badge(row['movement_kind'])}</td>"
            f"<td>{pix_rule_badge(row['codigo_centavos'], row['regra_nome'] or ('Dizimo default' if row['tipo_sugerido'] == 'dizimo' else 'Especial'))}</td>"
            f"<td>{pix_confidence_badge(row['confidence'])}</td>"
            f"<td>{target_html}</td>"
            f"<td>{destination_html}</td>"
            f"<td>{status_html}</td>"
            f"<td><a class='button small primary' href='/extratos/movimento?id={row['id']}&return_to={urllib.parse.quote(current_lot_url, safe='')}'>Conferir</a></td>"
            "</tr>"
        )
    lot_actions_html = (
        f"""
        <form method="post" action="/extratos/lote/reprocessar" style="display:inline">
          <input type="hidden" name="lot_id" value="{lot_id}">
          <input type="hidden" name="return_to" value="{h(current_lot_url)}">
          <button class="button" type="submit">Reprocessar lote</button>
        </form>
        <form method="post" action="/extratos/lote/encerrar" style="display:inline">
          <input type="hidden" name="lot_id" value="{lot_id}">
          <input type="hidden" name="return_to" value="{h(current_lot_url)}">
          <button class="button" type="submit">Encerrar processamento do lote</button>
        </form>
        """
        if not lot_closed
        else "<span class='hint'>Lote encerrado: o trabalho futuro segue pela central de contribuintes.</span>"
    )
    lot_workflow_panel_html = (
        f"""
        <div class="panel">
          <div class="section-head"><h2>Processamento do lote</h2><span>{pix_lot_status_badge(lot['status'])}</span></div>
          <div class="hint">Use este bloco quando terminar a auditoria local do lote. Encerrar o processamento tira o lote da fila operacional e deixa as pendencias remanescentes para tratamento posterior na central de contribuintes, sem apagar os movimentos nem o historico financeiro.</div>
          <div class="actions" style="margin-top:12px">
            <form method="post" action="/extratos/lote/reprocessar" style="display:inline">
              <input type="hidden" name="lot_id" value="{lot_id}">
              <input type="hidden" name="return_to" value="{h(current_lot_url)}">
              <button class="button" type="submit">Reprocessar lote</button>
            </form>
            <form method="post" action="/extratos/lote/encerrar" style="display:inline">
              <input type="hidden" name="lot_id" value="{lot_id}">
              <input type="hidden" name="return_to" value="{h(current_lot_url)}">
              <button class="button primary" type="submit">Encerrar processamento do lote</button>
            </form>
          </div>
        </div>
        """
        if not lot_closed
        else ""
    )
    closed_panel_html = (
        "<div class='panel'><div class='hint'><b>Lote encerrado.</b> O que restou sem pessoa vinculada saiu da fila do lote e agora deve ser tratado pela central de contribuintes. O lote fica apenas como memoria da remessa.</div></div>"
        if lot_closed
        else ""
    )
    if layout_code == "SICOOB_RECEBIMENTOS":
        bank_specific_hint = "No Sicoob, o campo de documento costuma trazer CPF/CNPJ do remetente em varios recebimentos. Isso ajuda bastante na associacao e na deduplicacao. Depositos em especie, cheque bloqueado, liberacoes de deposito e estornos continuam fora do escopo desta etapa."
    elif statement_layout_is_santander(layout_code):
        bank_specific_hint = "No Santander, o banco nao traz nome do remetente: a conferencia principal e <b>documento do banco x CPF da ficha</b>. Por isso, quando houver pessoa sugerida, a tabela do lote ja exibe o CPF da ficha e uma etiqueta de comparacao."
    else:
        bank_specific_hint = "No Bradesco, o campo <b>Docto</b> esta sendo preservado como referencia operacional da transacao. Ele ajuda em conferencias e duplicidade, mas nao e tratado como CPF/CNPJ mascarado. A regra de centavos agora usa a mesma tabela do PIX: sempre que os centavos baterem com uma destinacao especial, o movimento segue para <b>Saneamento destinacao</b>."
    body = f"""
      <div class="actions">
        <a class="button" href="/extratos">Voltar para extratos</a>
        <a class="button" href="/extratos/regras">Regras por centavos</a>
        <a class="button" href="/contribuintes?mode=pendentes">Contribuintes pendentes</a>
        {lot_actions_html}
      </div>
      {message_box(query)}
      <h1>Lote de extrato #{lot_id}</h1>
      <div class="hint">Arquivo original: <b>{h(lot['nome_arquivo'])}</b>. Banco <b>{h(lot['banco'])}</b>. Periodo {h(br_date(lot['periodo_inicio']))} ate {h(br_date(lot['periodo_fim']))}. O financeiro entra automaticamente; o lote funciona como fila de saneamento para nome, pessoa, destinacao por centavos e possivel duplicidade entre documentos.</div>
      {closed_panel_html}
      <div class="grid">{cards_html}</div>
      {lot_workflow_panel_html}
      <div class="panel">
        <h2>Filtros do lote</h2>
        <div class="actions">{filter_html}</div>
        <div class="hint" style="margin-top:10px">{bank_specific_hint}</div>
        {'<div class="hint" style="margin-top:10px">Este filtro reune todas as destinacoes especiais do lote, inclusive as que ja foram aprovadas. Ele existe para que nenhum lancamento 01..12 pareca "sumido" dentro do filtro Todos.</div>' if status_filter == 'destinacoes_especiais' else ''}
      </div>
      <div class="detail-grid">
        <div class="panel">
          <div class="section-head"><h2>Tabela de centavos ativa</h2><span>{badge(len(rules), 'warn')}</span></div>
          <div class="hint">A mesma tabela do PIX Sicoob vale aqui. Isso permite trocar o banco sem trocar a logica operacional da equipe.</div>
          <table>
            <thead><tr><th>Codigo</th><th>Destino</th><th>Status</th></tr></thead>
            <tbody>{rule_preview or "<tr><td colspan='3'>Nenhuma regra de centavos cadastrada.</td></tr>"}</tbody>
          </table>
          <div class="actions" style="margin-top:12px">
            <a class="button" href="/extratos/regras">Editar regras de centavos</a>
          </div>
        </div>
        <div class="panel">
          <div class="section-head"><h2>Resumo de destinacoes especiais</h2><span>{badge(special_destinations, 'warn' if special_destinations else 'ok')}</span></div>
          <div class="hint">Esta etiqueta reune todos os creditos que carregam regra especial por centavos neste lote, estejam eles ainda em saneamento ou ja aprovados.</div>
          <div class="actions" style="margin-top:8px">
            {badge(f"{counts.get('revisar_destinacao', 0)} aguardando confirmacao", 'warn' if counts.get('revisar_destinacao', 0) else 'ok')}
            {badge(f"{max(special_destinations - counts.get('revisar_destinacao', 0), 0)} ja regularizadas", 'info' if max(special_destinations - counts.get('revisar_destinacao', 0), 0) else 'ok')}
          </div>
          <div class="actions" style="margin-top:12px">
            <a class="button" href="/extratos/lote?id={lot_id}&status=destinacoes_especiais">Abrir destinacoes especiais</a>
          </div>
        </div>
      </div>
      <div class="panel">
        <h2>{len(movements)} movimento(s) exibido(s)</h2>
        <table>
          <thead><tr><th>Data</th><th class="right">Valor</th><th>Origem</th><th>Canal</th><th>Centavos</th><th>Match</th><th>Destino pessoa/contribuinte</th><th>Tipo</th><th>Status</th><th>Acao</th></tr></thead>
          <tbody>{''.join(movement_rows) if movement_rows else "<tr><td colspan='10'>Nenhum movimento encontrado para este filtro.</td></tr>"}</tbody>
        </table>
      </div>
    """
    return render_layout(f"Lote de extrato #{lot_id}", body, "extratos")


def render_statement_movement(db: PowerChurchDB, query: dict[str, list[str]]) -> str:
    movement_id = moneyless_int(query.get("id", ["0"])[0])
    movement = db.get_statement_movement(movement_id)
    if movement is None:
        return render_layout("Movimento de extrato", "<div class='empty'>Movimento de extrato nao encontrado.</div>", "extratos")
    organization_id = moneyless_int(movement["organizacao_id"])
    types = db.contribution_types(organization_id)
    lot_id = moneyless_int(movement["lote_id"])
    default_return_to = f"/extratos/lote?{urllib.parse.urlencode([('id', str(lot_id)), ('status', 'pendencias')])}"
    return_to = safe_redirect_path(query.get("return_to", [default_return_to])[0], default_return_to)
    lookup = normalize_query(query.get("lookup", [""])[0])
    layout_code = normalize_query(movement["layout_codigo"]).upper()
    candidate_rows = build_statement_candidate_rows(db, movement, lookup=lookup)
    related_candidates = [row for row in candidate_rows if str(row.get("similarity_bucket")) == "aderente"]
    partial_candidates = [row for row in candidate_rows if str(row.get("similarity_bucket")) == "parcial"]
    distant_candidates = [row for row in candidate_rows if str(row.get("similarity_bucket")) == "distante"]
    candidate_summary = "".join(
        [
            badge(f"{len(related_candidates)} com aderencia", "ok"),
            badge(f"{len(partial_candidates)} com semelhanca parcial", "warn" if partial_candidates else "info"),
            badge(f"{len(distant_candidates)} sem semelhanca clara", "danger" if distant_candidates else "info"),
        ]
    )
    default_selected_person_id = (
        0
        if moneyless_int(movement["association_reviewed"])
        else moneyless_int(movement["resolved_person_id"] or movement["suggested_person_id"] or "0")
    )
    selected_person_id = moneyless_int(query.get("resolved_person_id", [str(default_selected_person_id)])[0])
    selected_person = db.get_person(selected_person_id) if selected_person_id else None
    selected_person_compare_html = ""
    if selected_person is not None:
        selected_person_compare_html = f"""
          <div class="panel" style="margin-top:16px">
            <h3>Comparacao documental da pessoa sugerida</h3>
            <div class="field-grid">
              {field_card('Pessoa', selected_person['nome'])}
              {field_card('ID da ficha', format_system_id(selected_person['id']))}
              {field_card('CPF da ficha', format_cpf(selected_person['cpf']) or 'Nao informado')}
              {field_card('Documento do banco', movement['bank_document'] or '-')}
              {field_card_html('Resultado', statement_person_document_compare_badge(movement['bank_document'], selected_person['cpf']))}
              {field_card('Leitura para auditoria', statement_person_document_compare_note(movement['bank_document'], selected_person['cpf']), 'wide-field')}
            </div>
          </div>
        """
    duplicate_panel = ""
    if movement["duplicate_reason"]:
        duplicate_panel = (
            "<div class='panel' style='margin-top:16px'>"
            f"<h3>{pix_review_status_badge('revisar_duplicidade')} Possivel duplicidade</h3>"
            f"<div class='hint'>{h(movement['duplicate_reason'])}</div>"
            "</div>"
        )
    same_org_action_html = (
        "<button class='button' type='submit' name='action' value='same_owner'>Confirmar mesma titularidade / origem interna</button>"
        if str(movement["confidence"] or "") == "mesma_organizacao"
        else ""
    )
    action_buttons_html = (
        "<button class='button primary' type='submit' name='action' value='approve'>Confirmar saneamento</button>"
        f"{same_org_action_html}"
        "<button class='button' type='submit' name='action' value='ignore'>Ignorar movimento</button>"
    )
    imported_hint_html = (
        "<p class='hint'>Este movimento ja esta no financeiro. Se voce marcar como ignorado, a contribuicao importada tambem sera desativada e ficara apenas registrada no historico de auditoria.</p>"
        if moneyless_int(movement["imported_contribution_id"])
        else ""
    )
    rule_type_id = 0
    if moneyless_int(movement["regra_id"]):
        rule_row = db.conn.execute("SELECT tipo_contribuicao_id FROM pix_centavo_regras WHERE id = ?", (movement["regra_id"],)).fetchone()
        rule_type_id = moneyless_int(rule_row["tipo_contribuicao_id"] if rule_row else 0)
    selected_type_id = moneyless_int(
        query.get(
            "resolved_tipo_contribuicao_id",
            [str(movement["resolved_tipo_contribuicao_id"] or rule_type_id or db.resolved_statement_type_id_for_row(movement))],
        )[0]
    )
    person_return_to = f"/extratos/movimento?id={movement_id}&return_to={urllib.parse.quote(return_to, safe='')}"
    type_options = "".join(option(str(row["id"]), str(row["nome"]), selected_type_id) for row in types)
    same_org_hint_html = (
        "<div class='panel' style='margin-top:16px'><h3>Mesma titularidade / remessa interna</h3><div class='hint'>O motor identificou que a origem financeira coincide com a propria organizacao. Se isso for apenas transferencia entre contas da igreja, use <b>Confirmar mesma titularidade / origem interna</b>. O movimento saira da fila de pendencias, continuara no historico de auditoria e aparecera dentro de <b>Ignorados</b> no resumo do lote. Se nao for esse o caso, voce ainda pode tratar como contribuicao normal.</div></div>"
        if str(movement["confidence"] or "") == "mesma_organizacao"
        else ""
    )
    if layout_code == "SICOOB_RECEBIMENTOS":
        candidate_hint = "Como o Sicoob costuma trazer CPF/CNPJ em varios recebimentos, as sugestoes podem ser sustentadas tanto por nome quanto por identidade financeira/documental. Quando voce usar a busca complementar, o sistema consulta o cadastro inteiro, incluindo membro ativo, inativo, frequentador, visitante e arquivo morto."
    elif statement_layout_is_santander(layout_code):
        candidate_hint = "Como o Santander nao traz nome, a validacao principal e documental. Confira o <b>Docto do banco</b> contra o <b>CPF da ficha</b>; a tela agora mostra esta comparacao sem precisar abrir a ficha da pessoa."
    else:
        candidate_hint = "Como o Bradesco nao trouxe CPF/CNPJ mascarado nestas linhas, as sugestoes se apoiam sobretudo em <b>nome exato</b>, <b>nome muito proximo</b> e <b>identidades financeiras ja associadas</b>. Quando voce usar a busca complementar, o sistema agora consulta o <b>cadastro inteiro</b>, incluindo membro ativo, inativo, frequentador, visitante e arquivo morto."
    candidate_rows_html = []
    candidate_rows_html.append(
        "<tr>"
        f"<td><input type='radio' name='resolved_person_id' value='0' {'checked' if selected_person_id == 0 else ''}></td>"
        "<td><b>Manter sem pessoa vinculada</b><div class='hint'>O credito continua no financeiro e fica apenas no contribuinte auxiliar / NR.</div></td>"
        f"<td>{badge('Sem vinculo formal', 'warn')}</td>"
        f"<td>{badge('sem pessoa', 'info')}</td>"
        "<td>Use quando a contribuicao ja e valida, mas ainda nao ha seguranca para creditar a uma ficha.</td>"
        "<td>-</td>"
        "<td>-</td>"
        "</tr>"
    )
    for row in candidate_rows:
        source_hint = str(row.get("engine_source") or "manual")
        source_badges = audit_candidate_source_badges(source_hint)
        alias_name = normalize_query(row.get("engine_alias_name"))
        cpf_compare = statement_person_document_compare_badge(movement["bank_document"], row.get("cpf"))
        detail_hint = f"score {float(row.get('engine_score', 0)):.2f} | semelhanca {float(row.get('engine_ratio', 0)):.2f}"
        if alias_name:
            detail_hint += f" | identidade {alias_name}"
        candidate_rows_html.append(
            "<tr>"
            f"<td><input type='radio' name='resolved_person_id' value='{row['id']}' {'checked' if selected_person_id == moneyless_int(row['id']) else ''}></td>"
            f"<td><b>{h(row['nome'])}</b><div class='hint'>{h(format_system_id(row['id']))} | {h(format_member_code(row.get('codigo_interno'))) or 'Sem numero'} | CPF {h(format_cpf(row.get('cpf')) or 'Nao informado')} {cpf_compare}</div></td>"
            f"<td>{pix_candidate_similarity_badge(False, row.get('engine_exact_name'), row.get('engine_ratio'))}</td>"
            f"<td>{h(str(row.get('status') or ''))}</td>"
            f"<td>{h(str(row.get('engine_reason') or ''))}<div class='actions' style='margin-top:6px'>{source_badges}</div><div class='hint'>{h(detail_hint)}</div></td>"
            f"<td>{badge(row.get('pendencias', 0), 'danger' if moneyless_int(row.get('pendencias', 0)) else 'ok')}</td>"
            f"<td><a class='button small' href='/pessoa?id={row['id']}&return_to={urllib.parse.quote(person_return_to, safe='')}'>Abrir ficha</a></td>"
            "</tr>"
        )
    body = f"""
      <div class="actions">
        <a class="button" href="{h(return_to)}">Voltar para lote</a>
        <a class="button" href="/extratos">Voltar para extratos</a>
      </div>
      {message_box(query)}
      <h1>Auditoria do movimento de extrato #{movement_id}</h1>
      <div class="hint">Aqui tratamos creditos do extrato {h(movement['banco'])} que ja entraram no financeiro e agora precisam de saneamento cadastral. A contribuicao nao some: o que estamos decidindo e apenas se ela deve ser vinculada a uma pessoa do rol, frequentador, visitante ou permanecer como contribuinte auxiliar.</div>
      <div class="detail-grid">
        <div class="panel">
          <div class="section-head"><h2>Movimento bancario</h2><span>{pix_review_status_badge(movement['review_status'])}</span></div>
          <div class="field-grid">
            {field_card('Data do credito', br_date(movement['data_movimento']))}
            {field_card('Competencia', movement['competencia'])}
            {field_card('Valor', br_money(movement['valor']))}
            {field_card_html('Canal', statement_movement_kind_badge(movement['movement_kind']))}
            {field_card('Nome identificado', movement['nome_origem'] or 'Sem nome identificado', 'wide-field')}
            {field_card('Historico de origem', movement['origin_label'])}
            {field_card('Docto do banco', movement['bank_document'])}
            {field_card_html('Confianca do motor', pix_confidence_badge(movement['confidence']))}
            {field_card_html('Regra de centavos', pix_rule_badge(movement['codigo_centavos'], movement['regra_nome'] or ('Dizimo default' if movement['tipo_sugerido'] == 'dizimo' else 'Especial')))}
            {field_card('Observacao do motor', movement['review_notes'], 'wide-field')}
          </div>
          {same_org_hint_html}
          {selected_person_compare_html}
          <div class="panel" style="margin-top:16px">
            <h3>Texto bruto do extrato</h3>
            <pre style="white-space:pre-wrap; margin:0">{h(movement['raw_text'])}</pre>
          </div>
          {duplicate_panel}
        </div>
        <div class="panel">
          <div class="section-head"><h2>Sugestoes de pessoa</h2><span>{badge(len(candidate_rows), 'info')}</span></div>
          <div class="hint">{candidate_hint}</div>
          <div class="actions" style="margin:10px 0">{candidate_summary}</div>
          <form class="filters" method="get" action="/extratos/movimento">
            <input type="hidden" name="id" value="{movement_id}">
            <input type="hidden" name="return_to" value="{h(return_to)}">
            <label class="wide">Busca complementar<input name="lookup" value="{h(lookup)}" placeholder="reforce a busca por nome, MEM-00003, ID-001078 ou CPF"></label>
            <button class="button primary" type="submit">Pesquisar pessoas</button>
          </form>
          <form method="post" action="/extratos/movimento/salvar">
            <input type="hidden" name="movement_id" value="{movement_id}">
            <input type="hidden" name="return_to" value="{h(return_to)}">
            <table>
              <thead><tr><th>Usar</th><th>Pessoa sugerida</th><th>Aderencia</th><th>Status</th><th>Motivo da sugestao</th><th>Pend.</th><th>Ficha</th></tr></thead>
              <tbody>{''.join(candidate_rows_html) if candidate_rows_html else "<tr><td colspan='7'>Sem sugestoes no momento. Use a busca complementar ou mantenha como contribuinte auxiliar.</td></tr>"}</tbody>
            </table>
            <div class="form-grid" style="margin-top:16px">
              <label>Tipo de contribuicao<select name="resolved_tipo_contribuicao_id">{type_options}</select></label>
              {textarea_field('review_notes', 'Observacoes da auditoria', movement['review_notes'] or '', css_class='wide')}
            </div>
            <div class="actions">
              {action_buttons_html}
            </div>
          </form>
          {imported_hint_html}
          <p class="hint">Se a origem do credito for um nome recorrente ainda fora do cadastro, o melhor caminho e manter como contribuinte auxiliar. Depois podemos transformar esse historico em frequentador ou vincular ao membro correto sem perder nenhuma remessa.</p>
        </div>
      </div>
    """
    return render_layout(f"Movimento de extrato #{movement_id}", body, "extratos")


def render_people(db: PowerChurchDB, query: dict[str, list[str]]) -> str:
    q = normalize_query(query.get("q", [""])[0])
    selected_statuses = [normalize_query(value) for value in query.get("status", []) if normalize_query(value)]
    perfil = normalize_query(query.get("perfil", [""])[0])
    rows = db.list_people(q, selected_statuses, perfil)
    current_params: list[tuple[str, str]] = []
    if q:
        current_params.append(("q", q))
    for status_value in selected_statuses:
        current_params.append(("status", status_value))
    if perfil:
        current_params.append(("perfil", perfil))
    current_url = "/pessoas" + (f"?{urllib.parse.urlencode(current_params, doseq=True)}" if current_params else "")
    status_options = [
        ("membro_ativo", "Membro ativo"),
        ("membro_inativo", "Membro inativo"),
        ("frequentador", "Frequentador"),
        ("visitante", "Visitante"),
        ("arquivo_morto", "Arquivo morto"),
    ]
    status_filter_html = "".join(
        (
            f"<label class='check-item'>"
            f"<input type='checkbox' name='status' value='{h(value)}' {'checked' if value in selected_statuses else ''}>"
            f"<span>{h(label)}</span>"
            "</label>"
        )
        for value, label in status_options
    )
    batch_status_html = "".join(option(value, label, "") for value, label in status_options if value)
    trs = []
    for row in rows:
        pend = moneyless_int(row["pendencias"])
        pend_badge = badge(pend, "danger" if pend else "ok")
        status_badge = badge(row["status"], "warn" if row["status"] == "membro_inativo" else "ok" if str(row["status"]).startswith("membro") else "info")
        trs.append(
            "<tr>"
            f"<td><input type='checkbox' name='pessoa_id' value='{row['id']}'></td>"
            f"<td><a href='/pessoa?id={row['id']}'>{h(row['nome'])}</a><div class='hint'>{h(format_system_id(row['id']))}</div></td>"
            f"<td>{h(format_member_code(row['codigo_interno']))}</td>"
            f"<td>{format_cpf(row['cpf'])}</td>"
            f"<td>{status_badge}</td>"
            f"<td>{h(row['perfis'])}</td>"
            f"<td>{h(row['email_principal'])}<div class='hint'>{h(row['telefone_principal'])}</div></td>"
            f"<td class='right'>{pend_badge}</td>"
            "</tr>"
        )
    body = f"""
      <h1>Pessoas</h1>
      <div class="hint">Busca operacional por nome, ID do sistema, numero de membro, CPF e contato. O cadastro cobre membros, frequentadores, visitantes e arquivo morto. Para evitar confusao, o sistema padroniza a exibicao como <b>ID-000000</b> e <b>MEM-00000</b>. CPF completo exibido nesta versao local.</div>
      <form class="filters" method="get" action="/pessoas">
        <label>Busca<input name="q" value="{h(q)}" placeholder="nome, ID-001078, MEM-00003, CPF, email ou telefone"></label>
        <label>Perfil
          <select name="perfil">
            <option value="">Todos</option>
            <option value="membro" {'selected' if perfil == 'membro' else ''}>Membro</option>
            <option value="lider" {'selected' if perfil == 'lider' else ''}>Lider</option>
            <option value="pastor" {'selected' if perfil == 'pastor' else ''}>Pastor</option>
          </select>
        </label>
        <button class="button primary" type="submit">Filtrar</button>
        <a class="button primary" href="/pessoa/nova">Nova pessoa</a>
        <a class="button primary" href="/pessoas/importar">Importar pessoas</a>
        <a class="button" href="/pessoas">Limpar</a>
        <div class="wide">
          <div class="hint">Status: marque um ou mais grupos. Se nada for marcado, o sistema mostra todos.</div>
          <div class="check-grid">{status_filter_html}</div>
        </div>
      </form>
      <div class="panel">
        <h2>{len(rows)} pessoa(s) exibida(s)</h2>
        <div class="hint">Atualizacao em lote: selecione as fichas abaixo e aplique o novo status. Se uma pessoa virar membro e ainda nao tiver numero operacional, o sistema cria o proximo numero unico automaticamente. Se ja houver numero, ele e preservado e nunca reutilizado.</div>
        <form method="post" action="/pessoas/status-lote">
          <input type="hidden" name="return_to" value="{h(current_url)}">
          <div class="filters">
            <label>Novo status<select name="novo_status">{batch_status_html}</select></label>
            <button class="button primary" type="submit">Aplicar status em lote</button>
          </div>
          <table>
            <thead><tr><th>Sel.</th><th>Nome</th><th>Numero</th><th>CPF</th><th>Status</th><th>Perfis</th><th>Contato</th><th class="right">Pend.</th></tr></thead>
            <tbody>{''.join(trs) if trs else "<tr><td colspan='8'>Nenhuma pessoa encontrada.</td></tr>"}</tbody>
          </table>
        </form>
      </div>
    """
    return render_layout("Pessoas", body, "pessoas")


def custom_value(row: sqlite3.Row) -> str:
    if row["chave"] == "cpf_original_revisao":
        return format_cpf(row["valor_texto"])
    if row["valor_data"]:
        return br_date(row["valor_data"])
    if row["valor_numero"] is not None:
        return str(row["valor_numero"])
    if row["valor_texto"]:
        return str(row["valor_texto"])
    if row["valor_json"]:
        try:
            data = json.loads(row["valor_json"])
            if isinstance(data, dict) and data.get("valor_original"):
                return str(data["valor_original"])
        except json.JSONDecodeError:
            pass
        return str(row["valor_json"])
    return ""


def render_person(db: PowerChurchDB, query: dict[str, list[str]]) -> str:
    person_id = moneyless_int(query.get("id", ["0"])[0])
    contributor_lookup = normalize_query(query.get("contributor_lookup", [""])[0])
    return_to = safe_redirect_path(query.get("return_to", [""])[0], "")
    person = db.get_person(person_id)
    if person is None:
        return render_layout("Pessoa nao encontrada", "<div class='empty'>Pessoa nao encontrada.</div>", "pessoas")
    profiles = db.person_profiles(person_id)
    contacts = db.person_contacts(person_id)
    addresses = db.person_addresses(person_id)
    history = db.person_history(person_id)
    custom = db.person_custom_fields(person_id)
    audit = db.person_audit(person_id)
    contribution_summary = db.person_contribution_summary(person_id)
    contribution_rows = db.person_contributions(person_id, limit=8)
    linked_contributors = db.person_linked_contributors(person_id, limit=8)
    financial_identifiers = db.person_financial_identifiers(person_id, limit=12)
    contributor_suggestions = db.person_possible_contributors(person_id, limit=8)
    contributor_search_rows = db.list_contributors(q=contributor_lookup, limit=20) if contributor_lookup else []
    receipt_summary = db.person_receipts_summary(person_id)
    receipt_rows = db.person_receipts(person_id, limit=6)
    custom_by_key = custom_dict(custom)

    cpf_display = format_cpf(person["cpf"])
    cpf_review = ""
    for row in custom:
        if row["chave"] == "cpf_original_revisao":
            cpf_review = custom_value(row)
            break
    if cpf_display:
        cpf_html = h(cpf_display)
    elif cpf_review:
        cpf_html = f"{h(cpf_review)} {badge('em revisao', 'warn')}"
    else:
        cpf_html = "<span class='hint'>Nao informado</span>"

    profile_html = "".join(badge(row["perfil"], "ok" if row["perfil"] == "membro" else "info") for row in profiles) or "<span class='hint'>Sem perfil</span>"
    pending_count = len(audit)
    missing = []
    if not person["codigo_interno"]:
        missing.append("numero")
    if not cpf_display and not cpf_review:
        missing.append("CPF")
    if not person["data_nascimento"]:
        missing.append("nascimento")
    if not (person["telefone_principal"] or person["whatsapp_principal"] or contacts):
        missing.append("contato")
    if not addresses:
        missing.append("endereco")

    status_class = "warn" if person["status"] == "membro_inativo" else "ok"
    data_quality = "Dados completos" if not missing else f"Dados incompletos: {len(missing)}"
    data_quality_class = "ok" if not missing else "warn"
    cpf_status = "Em revisao" if cpf_review and not person["cpf"] else ("Informado" if person["cpf"] else "Nao informado")
    cpf_status_class = "warn" if cpf_review or not person["cpf"] else "ok"
    pending_class = "danger" if pending_count else "ok"
    pending_label = f"{pending_count} aberta(s)" if pending_count else "Sem pendencias"
    initial = h(str(person["nome"] or "?").strip()[:1].upper() or "?")
    photo_path = find_member_photo(person_id, person["cpf"], person["nome"])
    photo_filename = photo_path.name if photo_path else member_photo_example_filename(person_id, person["cpf"], person["nome"])
    photo_html = (
        f"<img class='member-photo' src='/foto/pessoa?id={person_id}' alt='Foto de {h(person['nome'])}'>"
        if photo_path
        else (
            "<div class='photo-placeholder'>"
            f"<div class='avatar'>{initial}</div>"
            "<div><b>Foto da pessoa</b><div class='hint'>Sem foto cadastrada</div></div>"
            "</div>"
        )
    )
    photo_note = (
        f"Arquivo vinculado: {h(photo_filename)}"
        if photo_path
        else f"Use o nome {h(photo_filename)} dentro de {h(PHOTO_DIR)}."
    )

    status_strip = "".join(
        [
            f"<div class='mini-card'><div class='label'>Situacao</div><div class='value'>{badge(person['status'], status_class)}</div></div>",
            f"<div class='mini-card'><div class='label'>Pendencias</div><div class='value'>{badge(pending_label, pending_class)}</div></div>",
            f"<div class='mini-card'><div class='label'>CPF</div><div class='value'>{badge(cpf_status, cpf_status_class)}</div></div>",
            f"<div class='mini-card'><div class='label'>Qualidade</div><div class='value'>{badge(data_quality, data_quality_class)}</div></div>",
        ]
    )

    address = addresses[0] if addresses else None
    address_summary = ""
    if address:
        address_summary = ", ".join(
            part
            for part in [
                str(address["logradouro"] or "").strip(),
                str(address["numero"] or "").strip(),
                str(address["complemento"] or "").strip(),
            ]
            if part
        )
    contacts_table = "".join(
        f"<tr><td>{h(row['tipo'])}</td><td>{h(row['valor'])}</td><td>{'Sim' if row['principal'] else ''}</td></tr>"
        for row in contacts
    )
    address_table = "".join(
        f"<tr><td>{h(row['logradouro'])}, {h(row['numero'])}<div class='hint'>{h(row['complemento'])}</div></td><td>{h(row['bairro'])}</td><td>{h(row['cidade'])}/{h(row['uf'])}</td><td>{h(row['cep'])}</td></tr>"
        for row in addresses
    )

    church_keys = [
        "batizado",
        "tipo_batismo",
        "forma_entrada",
        "igreja_origem",
        "aceitou_jesus_contexto",
        "recem_convertido",
        "status_origem",
        "data_criacao_origem",
    ]
    church_cards = "".join(
        field_card(label, custom_text(custom_by_key, key))
        for key, label in [
            ("batizado", "Batizado"),
            ("tipo_batismo", "Tipo de batismo"),
            ("forma_entrada", "Forma de entrada"),
            ("igreja_origem", "Igreja de origem"),
            ("aceitou_jesus_contexto", "Aceitou Jesus em"),
            ("recem_convertido", "Recem-convertido"),
            ("status_origem", "Status na origem"),
            ("data_criacao_origem", "Criado na origem"),
        ]
    )
    accessory_rows = [row for row in custom if row["chave"] not in set(church_keys)]
    accessory_items_parts = []
    for row in accessory_rows:
        value = h(custom_value(row)) or "<span class='hint'>Nao informado</span>"
        accessory_items_parts.append(f"<div class='accessory-item'><b>{h(row['nome'])}</b>{value}</div>")
    accessory_items = "".join(accessory_items_parts)
    timeline_items = "".join(
        "<div class='timeline-item'>"
        f"<b>{h(row['titulo']) or h(row['tipo_evento'])}</b>"
        f"<div class='hint'>{h(br_date(row['data_evento'])) or 'Sem data'} | {h(row['tipo_evento'])} | {h(row['origem'])}</div>"
        f"<div>{h(row['descricao'])}</div>"
        "</div>"
        for row in history[:8]
    )
    audit_rows_parts = []
    for row in audit:
        if row.get("resolvivel"):
            action_html = (
                "<form method='post' action='/pendencia/resolver'>"
                f"<input type='hidden' name='pendencia_id' value='{row['id']}'>"
                f"<input type='hidden' name='return_to' value='/pessoa?id={person_id}'>"
                "<button class='button small' type='submit'>Marcar resolvida</button></form>"
            )
        else:
            action_html = "<span class='hint'>Corrija a ficha; o alerta some automaticamente.</span>"
        audit_rows_parts.append(
            "<tr>"
            f"<td>{badge(row['severidade'], 'danger' if row['severidade'] == 'aviso' else 'info')}</td>"
            f"<td>{h(row['tipo'])}</td>"
            f"<td>{h(row['descricao'])}<div class='hint'>{h(row['acao_sugerida'])}</div></td>"
            f"<td>{h(row['numero_linha'])}</td>"
            f"<td>{action_html}</td>"
            "</tr>"
        )
    audit_rows = "".join(audit_rows_parts)
    contribution_table = "".join(
        "<tr>"
        f"<td>{h(br_date(row['data_recebimento']))}</td>"
        f"<td>{contribution_operational_status_badge(row['status_operacional'])}</td>"
        f"<td>{h(row['tipo_nome'])}<div class='hint'>{h(('Origem financeira: ' + str(row['contribuinte_nome']) + ' | ' + (((row['contribuinte_tipo'] or '').upper()) or 'PF') + ' | ' + str(row['contribuinte_documento'] or 'Sem documento principal')) if row['contribuinte_nome'] else 'Origem direta da pessoa')}</div></td>"
        f"<td>{h(row['forma_nome'])}</td>"
        f"<td class='right'>{h(br_money(row['valor']))}</td>"
        "</tr>"
        for row in contribution_rows
    )
    contributor_table = "".join(
        "<tr>"
        f"<td><a href='/contribuinte?id={row['id']}'>{h(row['nome'])}</a></td>"
        f"<td>{badge('PF' if row['tipo'] == 'pf' else 'PJ', 'info' if row['tipo'] == 'pf' else 'warn')}</td>"
        f"<td>{text_or_hint(row['documento_principal'])}</td>"
        f"<td>{h(row['origem'])}</td>"
        f"<td class='right'>{h(row['contribuicoes_qtd'])}</td>"
        f"<td class='right'>{h(br_money(row['total_contribuido']))}</td>"
        "</tr>"
        for row in linked_contributors
    )
    suggestion_contributor_rows_parts = []
    for row in contributor_suggestions:
        action_html = (
            "<form method='post' action='/pessoa/vincular-contribuinte'>"
            f"<input type='hidden' name='person_id' value='{person_id}'>"
            f"<input type='hidden' name='contributor_id' value='{row['id']}'>"
            f"<input type='hidden' name='return_to' value='{h(f'/pessoa?id={person_id}')}'>"
            "<button class='button small primary' type='submit'>Vincular a esta pessoa</button>"
            "</form>"
        )
        suggestion_contributor_rows_parts.append(
            "<tr>"
            f"<td><a href='/contribuinte?id={row['id']}'>{h(row['nome'])}</a><div class='hint'>{h(row['suggestion_reason'])}</div></td>"
            f"<td>{badge('PF' if row['tipo'] == 'pf' else 'PJ', 'info' if row['tipo'] == 'pf' else 'warn')}</td>"
            f"<td>{text_or_hint(row['documento_principal'])}</td>"
            f"<td>{pix_candidate_similarity_badge(row.get('suggestion_doc_match'), row.get('suggestion_exact_name'), row.get('suggestion_ratio'))}</td>"
            f"<td class='right'>{h(br_money(row['total_contribuido']))}</td>"
            f"<td>{action_html}</td>"
            "</tr>"
        )
    suggestion_contributor_rows = "".join(suggestion_contributor_rows_parts)
    suggestion_ids = {moneyless_int(row["id"]) for row in contributor_suggestions}
    search_result_contributors = []
    for row in contributor_search_rows:
        row_id = moneyless_int(row["id"])
        current_person_id = moneyless_int(row["pessoa_id"])
        if row_id in suggestion_ids:
            continue
        if current_person_id not in {0, person_id}:
            continue
        search_result_contributors.append(row)
    contributor_search_rows_parts = []
    for row in search_result_contributors:
        if moneyless_int(row["pessoa_id"]) == person_id:
            action_html = "<span class='hint'>Ja refletido na ficha.</span>"
        else:
            action_html = (
                "<form method='post' action='/pessoa/vincular-contribuinte'>"
                f"<input type='hidden' name='person_id' value='{person_id}'>"
                f"<input type='hidden' name='contributor_id' value='{row['id']}'>"
                f"<input type='hidden' name='return_to' value='{h(f'/pessoa?id={person_id}&contributor_lookup={urllib.parse.quote(contributor_lookup)}')}'>"
                "<button class='button small primary' type='submit'>Vincular a esta pessoa</button>"
                "</form>"
            )
        contributor_search_rows_parts.append(
            "<tr>"
            f"<td><a href='/contribuinte?id={row['id']}'>{h(row['nome'])}</a></td>"
            f"<td>{badge('PF' if row['tipo'] == 'pf' else 'PJ', 'info' if row['tipo'] == 'pf' else 'warn')}</td>"
            f"<td>{text_or_hint(row['documento_principal'])}</td>"
            f"<td>{badge('Ja vinculado a esta pessoa', 'ok') if moneyless_int(row['pessoa_id']) == person_id else badge('Disponivel p/ vinculo', 'warn')}</td>"
            f"<td class='right'>{h(br_money(row['total_contribuido']))}</td>"
            f"<td>{action_html}</td>"
            "</tr>"
        )
    contributor_search_table = "".join(contributor_search_rows_parts)
    financial_identity_seed_panel = f"""
      <div class="panel">
        <div class="section-head"><h2>Cadastrar identidade financeira antecipada</h2><span>{badge('antes da 1a contribuicao', 'info')}</span></div>
        <div class="hint">Use este bloco quando a pessoa ainda nao tem contribuicoes no sistema, mas voce ja sabe que ela pode doar por um CNPJ, empresa ou documento bancario especifico. Isso nao cria contribuicao agora; apenas prepara a ficha para futuras remessas.</div>
        <form class="filters" method="post" action="/pessoa/identidade-financeira/salvar">
          <input type="hidden" name="person_id" value="{person_id}">
          <input type="hidden" name="return_to" value="{h(f'/pessoa?id={person_id}')}">
          <label>Tipo
            <select name="identifier_type">
              <option value="cnpj">CNPJ completo</option>
              <option value="cnpj_mascarado">CNPJ mascarado</option>
              <option value="cpf_mascarado">CPF mascarado</option>
              <option value="documento_bancario">Outro documento bancario</option>
            </select>
          </label>
          <label class="wide">Documento / identidade<input name="identifier_value" value="" placeholder="ex: 12345678000199 ou **.**6.356/0001-**"></label>
          <label class="wide">Observacao<input name="notes" value="" placeholder="ex: CNPJ da empresa do membro / conta da familia"></label>
          <button class="button primary" type="submit">Cadastrar identidade</button>
        </form>
      </div>
    """
    contributor_link_panel = f"""
      <div class="panel">
        <div class="section-head"><h2>Vincular CNPJ / empresa / contribuinte financeiro</h2><span>{badge(len(contributor_suggestions), 'warn' if contributor_suggestions else 'info')}</span></div>
        <div class="hint">Este bloco serve para quando o contribuinte financeiro ja existe no sistema, porque ja houve ao menos uma remessa ou contribuicao registrada. O vinculo preserva a origem bancaria e faz os valores passarem a contar no extrato do membro.</div>
        <div class="panel" style="margin-top:16px">
          <h3>Vinculo manual imediato</h3>
          <div class="hint">Se voce ja sabe o ID do contribuinte financeiro, pode vincular aqui sem depender da busca abaixo.</div>
          <form class="filters" method="post" action="/pessoa/vincular-contribuinte">
            <input type="hidden" name="person_id" value="{person_id}">
            <input type="hidden" name="return_to" value="{h(f'/pessoa?id={person_id}')}">
            <label>ID do contribuinte<input name="contributor_id" value="" placeholder="ex: 52"></label>
            <button class="button primary" type="submit">Vincular agora</button>
          </form>
        </div>
        <table><thead><tr><th>Sugestao</th><th>Tipo</th><th>Documento</th><th>Aderencia</th><th class='right'>Total</th><th>Acao</th></tr></thead><tbody>{suggestion_contributor_rows or "<tr><td colspan='6'>Sem sugestoes automaticas no momento.</td></tr>"}</tbody></table>
        <div class="panel" style="margin-top:16px">
          <h3>Busca complementar de contribuinte</h3>
          <div class="hint">A pesquisa so localiza o contribuinte. Para efetivar o vinculo, use o botao <b>Vincular a esta pessoa</b> na linha do resultado.</div>
          <form class="filters" method="get" action="/pessoa">
            <input type="hidden" name="id" value="{person_id}">
            <label class="wide">Buscar contribuinte / empresa<input name="contributor_lookup" value="{h(contributor_lookup)}" placeholder="empresa, CNPJ mascarado, CPF mascarado ou nome do contribuinte"></label>
            <button class="button primary" type="submit">Pesquisar</button>
            <a class="button" href="/pessoa?id={person_id}">Limpar</a>
          </form>
          <table><thead><tr><th>Contribuinte</th><th>Tipo</th><th>Documento</th><th>Status</th><th class='right'>Total</th><th>Acao</th></tr></thead><tbody>{contributor_search_table or "<tr><td colspan='6'>Pesquise um contribuinte para habilitar o vinculo manual.</td></tr>"}</tbody></table>
        </div>
      </div>
    """
    identifier_table = "".join(
        "<tr>"
        f"<td>{h(row['tipo'])}</td>"
        f"<td>{h(row['valor'])}</td>"
        f"<td>{text_or_hint(row['contribuinte_nome'], 'Sem contribuinte vinculado')}</td>"
        f"<td>{'Sim' if row['principal'] else ''}</td>"
        "</tr>"
        for row in financial_identifiers
    )
    receipt_table = "".join(
        "<tr>"
        f"<td><a href='/recibo?id={row['id']}'>{h(row['numero'])}</a></td>"
        f"<td>{h(br_date(row['data_emissao']))}</td>"
        f"<td>{h(br_date(row['periodo_inicio']))} ate {h(br_date(row['periodo_fim']))}</td>"
        f"<td class='right'>{h(br_money(row['valor_total']))}</td>"
        "</tr>"
        for row in receipt_rows
    )
    return_to_button = f'<a class="button primary" href="{h(return_to)}">Voltar para auditoria</a>' if return_to else ""
    body = f"""
      <div class="actions">{return_to_button}<a class="button" href="/pessoas">Voltar para pessoas</a><a class="button" href="/auditoria">Ver auditoria</a><a class="button" href="/contribuicoes?person_id={person_id}">Ver contribuicoes</a><a class="button" href="/extrato/contribuicoes?person_id={person_id}">Extrato</a><a class="button" href="/recibos?person_id={person_id}">Ver recibos</a><a class="button primary" href="{person_edit_url(person_id)}">Editar cadastro</a></div>
      {message_box(query)}
      <div class="profile-hero">
        <div>
          <div class="member-photo-frame">{photo_html}</div>
          <div class="photo-note">{photo_note}</div>
        </div>
        <div>
          <div class="hero-title">
            <h1>{h(person['nome'])}</h1>
            <div class="actions"><a class="button small primary" href="{person_edit_url(person_id, audit_mode=True)}">Corrigir ficha</a></div>
          </div>
          <div class="hint">{h(format_system_id(person_id))} | {h(format_member_code(person['codigo_interno']) or 'MEM-nao-informado')} | CPF completo exibido nesta versao local</div>
          <div class="hero-meta">{badge(person['status'], status_class)}{profile_html}{badge('Com pendencias', 'danger') if pending_count else badge('Sem pendencias', 'ok')}{badge('CPF em revisao', 'warn') if cpf_review else ''}</div>
        </div>
      </div>
      <div class="status-strip">{status_strip}</div>
      <div class="profile-layout">
        <div class="stack">
          <div class="panel">
            <div class="section-head"><h2>Cadastro oficial</h2><span>{badge(data_quality, data_quality_class)}</span></div>
            <div class="field-grid">
              {field_card('ID do sistema', format_system_id(person_id))}
              {field_card('Numero de membro', format_member_code(person['codigo_interno']))}
              {field_card_html('CPF', cpf_html)}
              {field_card_html('Status', badge(person['status'], status_class))}
              {field_card_html('Perfis', profile_html)}
              {field_card('Nascimento', br_date(person['data_nascimento']))}
              {field_card('Sexo', person['sexo'])}
              {field_card('Estado civil', person['estado_civil'])}
              {field_card('RG', person['rg'])}
            </div>
          </div>
          <div class="panel">
            <div class="section-head"><h2>Contato e endereco</h2><span>{badge('Operacional', 'info')}</span></div>
            <div class="field-grid">
              {field_card('Email principal', person['email_principal'])}
              {field_card('Telefone principal', person['telefone_principal'])}
              {field_card('WhatsApp principal', person['whatsapp_principal'])}
              {field_card('Endereco principal', address_summary, 'wide-field')}
              {field_card('Bairro', address['bairro'] if address else '')}
              {field_card('Cidade/UF', f"{address['cidade']}/{address['uf']}" if address else '')}
              {field_card('CEP', address['cep'] if address else '')}
            </div>
          </div>
          <div class="panel">
            <div class="section-head"><h2>Vida eclesiastica</h2><span>{badge('Da importacao', 'info')}</span></div>
            <div class="field-grid">{church_cards}</div>
          </div>
          <div class="panel">
            <h2>Historico eclesiastico</h2>
            <div class="timeline">{timeline_items or '<div class="empty">Sem historico eclesiastico registrado.</div>'}</div>
          </div>
        </div>
        <div class="stack">
          <div class="panel">
            <div class="section-head"><h2>Acoes rapidas</h2><span>{badge('Ficha central', 'ok')}</span></div>
            <div class="actions">
              <a class="button primary" href="/contribuicao/nova?person_id={person_id}">Lancar contribuicao</a>
              <a class="button primary" href="/extrato/contribuicoes?person_id={person_id}">Extrato de contribuicoes</a>
              <a class="button primary" href="/recibo/novo?person_id={person_id}">Gerar recibo</a>
              <a class="button primary" href="{person_edit_url(person_id)}">Editar cadastro</a>
              <a class="button" href="/contribuicoes?person_id={person_id}">Historico de contribuicoes</a>
              <a class="button" href="/recibos?person_id={person_id}">Historico de recibos</a>
              <a class="button" href="/auditoria">Ver auditoria geral</a>
              <a class="button" href="/pessoas">Voltar para lista</a>
            </div>
            <p class="hint">Proximas etapas previstas: cancelamento de recibo, historico pastoral e relacionamentos familiares.</p>
          </div>
          <div class="panel">
            <div class="section-head"><h2>Contribuicoes da pessoa</h2><span>{badge(contribution_summary['quantidade'], 'info')}</span></div>
            <div class="field-grid">
              {field_card('Total contribuido', br_money(contribution_summary['total']))}
              {field_card('Ultima contribuicao', br_date(contribution_summary['ultima_data']))}
            </div>
            <table><thead><tr><th>Data</th><th>Status</th><th>Tipo</th><th>Forma</th><th class="right">Valor</th></tr></thead><tbody>{contribution_table or "<tr><td colspan='5'>Nenhuma contribuicao registrada.</td></tr>"}</tbody></table>
          </div>
          <div class="panel">
            <div class="section-head"><h2>Identidades financeiras vinculadas</h2><span>{badge(len(linked_contributors), 'info')}</span></div>
            <div class="hint">Aqui aparecem CNPJ, abreviacoes bancarias e outras identidades financeiras que o operador optou por vincular a esta pessoa. Isso nao substitui a ficha da pessoa; serve para dar rastreabilidade as remessas.</div>
            <table><thead><tr><th>Contribuinte</th><th>Tipo</th><th>Documento</th><th>Origem</th><th class='right'>Lanc.</th><th class='right'>Total</th></tr></thead><tbody>{contributor_table or "<tr><td colspan='6'>Nenhuma identidade financeira vinculada.</td></tr>"}</tbody></table>
          </div>
          {financial_identity_seed_panel}
          {contributor_link_panel}
          <div class="panel">
            <div class="section-head"><h2>Documentos financeiros associados</h2><span>{badge(len(financial_identifiers), 'info')}</span></div>
            <div class="hint">Se houver CNPJ associado, CPF mascarado ou outro identificador visto nas remessas, ele aparece aqui para conferencia do operador.</div>
            <table><thead><tr><th>Tipo</th><th>Valor</th><th>Contribuinte</th><th>Principal</th></tr></thead><tbody>{identifier_table or "<tr><td colspan='4'>Nenhum documento financeiro associado.</td></tr>"}</tbody></table>
          </div>
          <div class="panel">
            <div class="section-head"><h2>Recibos da pessoa</h2><span>{badge(receipt_summary['quantidade'], 'info')}</span></div>
            <div class="field-grid">
              {field_card('Valor total recebido', br_money(receipt_summary['total']))}
              {field_card('Ultimo recibo', br_date(receipt_summary['ultima_data']))}
            </div>
            <table><thead><tr><th>Numero</th><th>Emissao</th><th>Periodo</th><th class="right">Valor</th></tr></thead><tbody>{receipt_table or "<tr><td colspan='4'>Nenhum recibo emitido.</td></tr>"}</tbody></table>
          </div>
          <div class="panel">
            <div class="section-head"><h2>Pendencias abertas</h2><span>{badge(pending_label, pending_class)}</span></div>
            <table><thead><tr><th>Sev.</th><th>Tipo</th><th>Descricao</th><th>Acao</th></tr></thead><tbody>{audit_rows or "<tr><td colspan='4'>Sem pendencias abertas.</td></tr>"}</tbody></table>
          </div>
          <div class="panel">
            <h2>Contatos importados</h2>
            <table><thead><tr><th>Tipo</th><th>Valor</th><th>Principal</th></tr></thead><tbody>{contacts_table or "<tr><td colspan='3'>Sem contatos.</td></tr>"}</tbody></table>
          </div>
          <div class="panel">
            <h2>Enderecos importados</h2>
            <table><thead><tr><th>Endereco</th><th>Bairro</th><th>Cidade</th><th>CEP</th></tr></thead><tbody>{address_table or "<tr><td colspan='4'>Sem endereco.</td></tr>"}</tbody></table>
          </div>
          <div class="panel">
            <div class="section-head"><h2>Campos acessorios</h2><span>{badge(len(accessory_rows), 'info')}</span></div>
            <div class="hint">Dados preservados da planilha que ainda nao viraram campos oficiais.</div>
            <div class="accessory-grid">{accessory_items or '<div class="empty">Sem campos acessorios.</div>'}</div>
          </div>
        </div>
      </div>
    """
    return render_layout(person["nome"], body, "pessoas")


def render_person_new(db: PowerChurchDB, query: dict[str, list[str]]) -> str:
    organization_id = db.default_organization_id()
    generated_code = db.next_member_code(organization_id)
    generated_code_display = format_member_code(generated_code)
    status_values = [
        ("membro_ativo", "Membro ativo"),
        ("membro_inativo", "Membro inativo"),
        ("frequentador", "Frequentador"),
        ("visitante", "Visitante"),
        ("arquivo_morto", "Arquivo morto"),
    ]
    status_current = query.get("status", ["frequentador"])[0]
    status_select = "".join(option(value, label, status_current) for value, label in status_values)
    body = f"""
      <div class="actions">
        <a class="button" href="/pessoas">Voltar para pessoas</a>
        <a class="button" href="/">Inicio</a>
      </div>
      {message_box(query)}
      <h1>Nova pessoa</h1>
      <div class="hint">O cadastro agora atende membros, frequentadores e visitantes. O numero de membro e reservado apenas quando o status virar membro, inclusive em futuras alteracoes em lote. Uma vez gerado, ele continua unico e nao sera reutilizado.</div>
      <form method="post" action="/pessoa/salvar">
        <input type="hidden" name="id" value="0">
        <input type="hidden" name="codigo_interno" value="">
        <div class="panel">
          <h2>Identificacao principal</h2>
          <div class="form-grid">
            {input_field('nome', 'Nome', query.get('nome', [''])[0], css_class='wide')}
            <label>Numero de membro<input type="text" value="Sera gerado quando o status virar membro" readonly></label>
            <label>ID do sistema<input type="text" value="Sera gerado ao salvar" readonly></label>
            {input_field('cpf', 'CPF', query.get('cpf', [''])[0])}
            {input_field('rg', 'RG', query.get('rg', [''])[0])}
            {input_field('data_nascimento', 'Nascimento', query.get('data_nascimento', [''])[0])}
            {input_field('sexo', 'Sexo', query.get('sexo', [''])[0])}
            {input_field('estado_civil', 'Estado civil', query.get('estado_civil', [''])[0])}
            <label>Status<select name="status">{status_select}</select></label>
            {input_field('email_principal', 'Email principal', query.get('email_principal', [''])[0])}
            {input_field('telefone_principal', 'Telefone principal', query.get('telefone_principal', [''])[0])}
            {input_field('whatsapp_principal', 'WhatsApp principal', query.get('whatsapp_principal', [''])[0])}
            {textarea_field('observacoes', 'Observacoes', query.get('observacoes', [''])[0], css_class='wide')}
          </div>
        </div>
        <div class="panel">
          <h2>Endereco principal</h2>
          <div class="form-grid">
            {input_field('cep', 'CEP', query.get('cep', [''])[0])}
            {input_field('logradouro', 'Logradouro', query.get('logradouro', [''])[0], css_class='wide')}
            {input_field('numero', 'Numero', query.get('numero', [''])[0])}
            {input_field('complemento', 'Complemento', query.get('complemento', [''])[0])}
            {input_field('bairro', 'Bairro', query.get('bairro', [''])[0])}
            {input_field('cidade', 'Cidade', query.get('cidade', [''])[0])}
            {input_field('uf', 'UF', query.get('uf', [''])[0])}
          </div>
        </div>
        <div class="panel">
          <h2>Regra de numeracao</h2>
          <div class="field-grid">
            {field_card('Proximo numero de membro previsto', generated_code_display)}
            {field_card('ID do sistema', 'Sera criado automaticamente')}
            {field_card('Regra operacional', 'Visitante e frequentador nao recebem numero de membro ate a mudanca de status', 'wide-field')}
            {field_card('Reutilizacao', 'Nao reutiliza numero antigo, mesmo em arquivo morto', 'wide-field')}
          </div>
        </div>
        <div class="actions">
          <button class="button primary" type="submit">Criar cadastro</button>
          <a class="button" href="/pessoas">Cancelar</a>
        </div>
      </form>
    """
    return render_layout("Nova pessoa", body, "pessoas")


def render_person_edit(db: PowerChurchDB, query: dict[str, list[str]]) -> str:
    person_id = moneyless_int(query.get("id", ["0"])[0])
    audit_mode = normalize_query(query.get("modo", [""])[0]) == "auditoria"
    person = db.get_person(person_id)
    if person is None:
        return render_layout("Pessoa nao encontrada", "<div class='empty'>Pessoa nao encontrada.</div>", "pessoas")
    address = db.primary_address(person_id)
    custom = db.person_custom_fields(person_id)
    audit = db.person_audit(person_id)
    photo_path = find_member_photo(person_id, person["cpf"], person["nome"])
    photo_filename = photo_path.name if photo_path else member_photo_example_filename(person_id, person["cpf"], person["nome"])
    photo_preview = (
        f"<img class='member-photo' src='/foto/pessoa?id={person_id}' alt='Foto de {h(person['nome'])}'>"
        if photo_path
        else (
            "<div class='photo-placeholder'>"
            f"<div class='avatar'>{h(str(person['nome'] or '?').strip()[:1].upper() or '?')}</div>"
            "<div><b>Sem foto cadastrada</b><div class='hint'>Adicione o arquivo na pasta oficial</div></div>"
            "</div>"
        )
    )

    status_values = [
        ("membro_ativo", "Membro ativo"),
        ("membro_inativo", "Membro inativo"),
        ("frequentador", "Frequentador"),
        ("visitante", "Visitante"),
        ("arquivo_morto", "Arquivo morto"),
    ]
    status_current = person["status"]
    if status_current and status_current not in [value for value, _ in status_values]:
        status_values.append((status_current, status_current))
    status_select = "".join(option(value, label, status_current) for value, label in status_values)
    current_edit_url = person_edit_url(person_id, audit_mode=audit_mode)
    member_code_display = format_member_code(person["codigo_interno"]) or "Geracao automatica conforme o status"
    member_code_input = (
        input_field("codigo_interno", "Numero de membro", format_member_code(person["codigo_interno"]))
        if audit_mode
        else (
            f"<label>Numero de membro<input type='text' value='{h(member_code_display)}' readonly></label>"
            "<input type='hidden' name='codigo_interno' value=''>"
        )
    )
    audit_mode_hidden = "<input type='hidden' name='edit_mode' value='auditoria'>" if audit_mode else ""
    member_code_mode_hidden = f"<input type='hidden' name='allow_member_code_edit' value='{'1' if audit_mode else '0'}'>"
    edit_hint = (
        "Modo de auditoria ativo: aqui o numero de membro pode ser corrigido para saneamento da importacao, mantendo a trilha de auditoria."
        if audit_mode
        else "Edicao operacional: o numero de membro fica bloqueado nesta tela. Para corrigi-lo, use o fluxo da auditoria/importacao."
    )

    custom_inputs = "".join(
        input_field(f"custom_{row['valor_id']}", row["nome"], custom_value(row), css_class="wide")
        for row in custom
    )
    pending_rows_parts = []
    for row in audit:
        resolve_cell = (
            f"<input type='checkbox' name='resolver_pendencia' value='{row['id']}'>"
            if row.get("resolvivel")
            else "<span class='hint'>automatico</span>"
        )
        pending_rows_parts.append(
            "<tr>"
            f"<td>{resolve_cell}</td>"
            f"<td>{badge(row['severidade'], 'danger' if row['severidade'] == 'aviso' else 'info')}</td>"
            f"<td>{h(row['tipo'])}</td>"
            f"<td>{h(row['descricao'])}<div class='hint'>{h(row['acao_sugerida'])}</div></td>"
            "</tr>"
        )
    pending_rows = "".join(pending_rows_parts)

    body = f"""
      <div class="actions">
        <a class="button" href="/pessoa?id={person_id}">Voltar para ficha</a>
        <a class="button" href="/auditoria">Ver auditoria</a>
      </div>
      {message_box(query)}
      <h1>Editar cadastro</h1>
      <div class="hint">Corrija os dados da pessoa e, quando fizer sentido, marque como resolvidas apenas as pendencias de importacao. Alertas dinamicos do cadastro, como numero de membro duplicado, somem automaticamente depois da correcao.</div>
      <div class="hint"><b>{h(edit_hint)}</b></div>
      <div class="panel">
        <h2>Foto da pessoa</h2>
        <div class="detail-grid">
          <div><div class="member-photo-frame">{photo_preview}</div></div>
          <div class="stack">
            <div class="hint">As fotos ficam arquivadas em <b>{h(PHOTO_DIR)}</b>, vinculadas pelo <b>ID</b>, com <b>nome</b> no sufixo e <b>CPF</b> opcional.</div>
            <div class="hint">Nome recomendado do arquivo: <b>{h(photo_filename)}</b></div>
            <div class="hint">Formatos aceitos: JPG, JPEG, PNG, WEBP, GIF, HEIC e HEIF. Se o nome ou o CPF mudarem, o sistema ainda tenta localizar pela parte do ID.</div>
            <form method="post" action="/pessoa/foto/upload" enctype="multipart/form-data">
              <input type="hidden" name="id" value="{person_id}">
              <input type="hidden" name="return_to" value="{h(current_edit_url)}">
              <div class="form-grid">
                <label class="wide">Selecionar foto<input type="file" name="foto" accept=".jpg,.jpeg,.png,.webp,.gif,.heic,.heif,image/*"></label>
              </div>
              <div class="actions">
                <button class="button primary" type="submit">Enviar foto</button>
                <a class="button" href="/pessoa?id={person_id}">Ver ficha</a>
              </div>
            </form>
          </div>
        </div>
      </div>
      <form method="post" action="/pessoa/salvar">
        <input type="hidden" name="id" value="{person_id}">
        {audit_mode_hidden}
        {member_code_mode_hidden}
        <div class="panel">
          <h2>Dados principais</h2>
          <div class="form-grid">
            {input_field('nome', 'Nome', person['nome'], css_class='wide')}
            {member_code_input}
            {input_field('cpf', 'CPF', format_cpf(person['cpf']))}
            {input_field('rg', 'RG', person['rg'])}
            {input_field('data_nascimento', 'Nascimento', person['data_nascimento'])}
            {input_field('sexo', 'Sexo', person['sexo'])}
            {input_field('estado_civil', 'Estado civil', person['estado_civil'])}
            <label>Status<select name="status">{status_select}</select></label>
            {input_field('email_principal', 'Email principal', person['email_principal'])}
            {input_field('telefone_principal', 'Telefone principal', person['telefone_principal'])}
            {input_field('whatsapp_principal', 'WhatsApp principal', person['whatsapp_principal'])}
            {textarea_field('observacoes', 'Observacoes', person['observacoes'], css_class='wide')}
          </div>
        </div>
        <div class="panel">
          <h2>Endereco principal</h2>
          <div class="form-grid">
            {input_field('cep', 'CEP', address['cep'] if address else '')}
            {input_field('logradouro', 'Logradouro', address['logradouro'] if address else '', css_class='wide')}
            {input_field('numero', 'Numero', address['numero'] if address else '')}
            {input_field('complemento', 'Complemento', address['complemento'] if address else '')}
            {input_field('bairro', 'Bairro', address['bairro'] if address else '')}
            {input_field('cidade', 'Cidade', address['cidade'] if address else '')}
            {input_field('uf', 'UF', address['uf'] if address else '')}
          </div>
        </div>
        <div class="panel">
          <h2>Campos acessorios importados</h2>
          <div class="hint">Campos preservados da planilha original. Nesta versao eles podem ser ajustados como texto; depois decidimos quais viram campos oficiais.</div>
          <div class="form-grid">{custom_inputs or '<div class="empty wide">Sem campos acessorios.</div>'}</div>
        </div>
        <div class="panel">
          <h2>Pendencias abertas desta ficha</h2>
          <div class="hint">Marque somente o que foi realmente conferido ou corrigido. Itens marcados como automaticos dependem da correcao do cadastro e nao de baixa manual.</div>
          <table>
            <thead><tr><th>Resolver</th><th>Sev.</th><th>Tipo</th><th>Descricao / Acao sugerida</th></tr></thead>
            <tbody>{pending_rows or "<tr><td colspan='4'>Sem pendencias abertas.</td></tr>"}</tbody>
          </table>
        </div>
        <div class="actions">
          <button class="button primary" type="submit">Salvar cadastro</button>
          <a class="button" href="/pessoa?id={person_id}">Cancelar</a>
        </div>
      </form>
    """
    return render_layout("Editar cadastro", body, "pessoas")


def render_contribution_new(db: PowerChurchDB, query: dict[str, list[str]]) -> str:
    person_id = moneyless_int(query.get("person_id", ["0"])[0])
    person = db.get_person(person_id) if person_id else None
    body = f"""
      <div class="actions"><a class="button" href="/contribuicoes">Voltar para contribuicoes</a><a class="button" href="/pessoas">Escolher pessoa</a></div>
      {message_box(query)}
      <h1>Novo lancamento de contribuicao</h1>
      <div class="hint">Este primeiro fluxo registra contribuicoes identificadas vinculadas ao cadastro da pessoa.</div>
    """
    if person is None:
        body += """
          <div class="panel">
            <h2>Escolha uma pessoa</h2>
            <div class="hint">Abra a ficha da pessoa e use o botao de contribuicoes para lancar Dizimo, Oferta Identificada e demais entradas vinculadas ao cadastro correto.</div>
            <div class="actions"><a class="button primary" href="/pessoas">Ir para pessoas</a></div>
          </div>
        """
        return render_layout("Nova contribuicao", body, "contribuicoes")

    organization_id = moneyless_int(person["organizacao_id"])
    types = db.contribution_types(organization_id)
    forms = db.receiving_forms(organization_id)
    type_options = "".join(option(str(row["id"]), str(row["nome"]), query.get("tipo_contribuicao_id", [""])[0]) for row in types)
    form_options = "".join(option(str(row["id"]), str(row["nome"]), query.get("forma_recebimento_id", [""])[0]) for row in forms)
    summary = db.person_contribution_summary(person_id)
    body += f"""
      <div class="panel">
        <h2>Membro selecionado</h2>
        <div class="field-grid">
          {field_card('Nome', person['nome'])}
          {field_card('ID do sistema', format_system_id(person_id))}
          {field_card('Numero de membro', format_member_code(person['codigo_interno']))}
          {field_card('CPF', format_cpf(person['cpf']))}
          {field_card('Contribuicoes ja registradas', summary['quantidade'])}
        </div>
      </div>
      <form method="post" action="/contribuicao/salvar">
        <input type="hidden" name="pessoa_id" value="{person_id}">
        <div class="panel">
          <h2>Dados da contribuicao</h2>
          <div class="form-grid">
            <label>Data do recebimento<input type="date" name="data_recebimento" value="{h(query.get('data_recebimento', [date.today().isoformat()])[0])}"></label>
            <label>Tipo<select name="tipo_contribuicao_id"><option value="">Selecione</option>{type_options}</select></label>
            <label>Forma de recebimento<select name="forma_recebimento_id"><option value="">Selecione</option>{form_options}</select></label>
            {input_field('valor', 'Valor', query.get('valor', [''])[0])}
            {textarea_field('observacoes', 'Observacoes', query.get('observacoes', [''])[0], css_class='wide')}
          </div>
          <div class="hint">A competencia sera calculada automaticamente com base na data de recebimento.</div>
        </div>
        <div class="actions">
          <button class="button primary" type="submit">Salvar contribuicao</button>
          <a class="button" href="/pessoa?id={person_id}">Voltar para ficha</a>
        </div>
      </form>
    """
    return render_layout("Nova contribuicao", body, "contribuicoes")


def render_contributions(db: PowerChurchDB, query: dict[str, list[str]]) -> str:
    q = normalize_query(query.get("q", [""])[0])
    competencia = normalize_query(query.get("competencia", [""])[0])
    person_id = moneyless_int(query.get("person_id", ["0"])[0])
    tipo_id = moneyless_int(query.get("tipo_id", ["0"])[0])
    default_org = db.default_organization_id()
    types = db.contribution_types(default_org)
    selected_person = db.get_person(person_id) if person_id else None
    row_limit = 5000 if (q or competencia or tipo_id or person_id) else 300
    rows = db.list_contributions(
        q=q,
        competencia=competencia,
        tipo_id=tipo_id,
        person_id=person_id,
        limit=row_limit,
    )
    summary = db.contributions_summary(q=q, competencia=competencia, tipo_id=tipo_id, person_id=person_id)
    cards = [
        ("Lancamentos", summary["quantidade"], ""),
        ("Valor total", br_money(summary["total"]), "ok"),
        ("Doadores", summary["doadores"], "info"),
        ("Ultimo recebimento", br_date(summary["ultima_data"]) or "Nao registrado", "warn" if summary["ultima_data"] else ""),
    ]
    cards_html = "".join(
        f"<div class='card'><div class='label'>{h(label)}</div><div class='value'>{h(value)}</div>{badge(cls, cls) if cls else ''}</div>"
        for label, value, cls in cards
    )
    type_options = "".join(option(str(row["id"]), str(row["nome"]), str(tipo_id or "")) for row in types)
    table_rows = []
    for row in rows:
        if row["pessoa_id"]:
            financial_origin = ""
            if row["contribuinte_nome"]:
                financial_origin = (
                    f"Origem financeira: {row['contribuinte_nome']} | "
                    f"{((row['contribuinte_tipo'] or '').upper() or 'PF')} | "
                    f"{row['contribuinte_documento'] or 'Sem documento principal'}"
                )
            financial_origin_html = f"<div class='hint'>{h(financial_origin)}</div>" if financial_origin else ""
            member_cell = (
                f"<a href='/pessoa?id={row['pessoa_id']}'>{h(row['pessoa_nome'])}</a>"
                f"<div class='hint'>{h(format_system_id(row['pessoa_id']))} | {h(format_member_code(row['codigo_interno']))} | CPF {h(format_cpf(row['cpf']))}</div>"
                f"{financial_origin_html}"
            )
            action_html = f"<a class='button small' href='/pessoa?id={row['pessoa_id']}'>Abrir ficha</a>"
        elif row["contribuinte_nome"]:
            member_cell = (
                f"{h(row['contribuinte_nome'])}"
                f"<div class='hint'>Contribuinte auxiliar {h((row['contribuinte_tipo'] or '').upper() or 'PF')} | {h(row['contribuinte_documento']) or 'Sem documento principal'}</div>"
            )
            action_html = f"<a class='button small' href='/contribuintes?q={urllib.parse.quote(str(row['contribuinte_nome']))}'>Ver contribuinte</a>"
        else:
            member_cell = "<span class='hint'>Nao identificado</span>"
            action_html = ""
        table_rows.append(
            "<tr>"
            f"<td>{h(br_date(row['data_recebimento']))}</td>"
            f"<td>{h(row['competencia'])}</td>"
            f"<td>{member_cell}</td>"
            f"<td>{contribution_operational_status_badge(row['status_operacional'])}</td>"
            f"<td>{h(row['tipo_nome'])}</td>"
            f"<td>{h(row['forma_nome'])}</td>"
            f"<td class='right'>{h(br_money(row['valor']))}</td>"
            f"<td>{h(row['observacoes'])}</td>"
            f"<td>{action_html}</td>"
            "</tr>"
        )
    action_html = (
        f"<a class='button primary' href='/contribuicao/nova?person_id={person_id}'>Lancar para {h(selected_person['nome'])}</a> <a class='button' href='/recibo/novo?person_id={person_id}'>Gerar recibo</a> <a class='button' href='/extrato/contribuicoes?person_id={person_id}'>Extrato</a>"
        if selected_person
        else "<a class='button primary' href='/pessoas'>Escolher pessoa para lancar</a>"
    )
    period_report_params = {"section": "periodo"}
    if competencia:
        period_report_params["competencia"] = competencia
    if q:
        period_report_params["person_query"] = q
    period_report_href = "/contribuintes?" + urllib.parse.urlencode(period_report_params)
    body = f"""
      <div class="actions">{action_html}<a class="button" href="/pessoas">Pessoas</a><a class="button primary" href="{h(period_report_href)}">Relatorio alfabetico por periodo</a><button class="button" type="button" onclick="window.print()">Imprimir esta lista filtrada</button></div>
      {message_box(query)}
      <h1>Contribuicoes</h1>
      <div class="hint">Cada lancamento pode guardar duas camadas ao mesmo tempo: a pessoa creditada e a origem financeira real que apareceu no banco. Isso permite registrar, por exemplo, um PIX vindo do CNPJ da empresa, mas ainda contar a contribuicao no extrato do membro.</div>
      {'<div class="panel"><h2>Membro filtrado</h2><div class="hint">Historico operacional de ' + h(selected_person['nome']) + '.</div></div>' if selected_person else ''}
      <div class="grid">{cards_html}</div>
      <div class="panel">
        <div class="section-head"><h2>Busca rapida por nome</h2><span>{badge('busca', 'info')}</span></div>
        <div class="hint">Use esta busca tanto para localizar a pessoa creditada quanto a origem financeira que apareceu no banco. Ela entende nome, CPF, `ID-`, `MEM-` e tambem o nome do contribuinte auxiliar.</div>
      </div>
      <form class="filters" method="get" action="/contribuicoes">
        {'<input type="hidden" name="person_id" value="' + str(person_id) + '">' if person_id else ''}
        <label>Busca<input name="q" value="{h(q)}" placeholder="pessoa, contribuinte financeiro, ID-001078, MEM-00003 ou CPF"></label>
        <label>Competencia<input name="competencia" value="{h(competencia)}" placeholder="ex: Abril 26"></label>
        <label>Tipo<select name="tipo_id"><option value="">Todos</option>{type_options}</select></label>
        <button class="button primary" type="submit">Filtrar</button>
        <a class="button" href="/contribuicoes">Limpar</a>
      </form>
      <div class="panel">
        <h2>{len(rows)} contribuicao(oes) exibida(s)</h2>
        <table>
          <thead><tr><th>Data</th><th>Competencia</th><th>Membro</th><th>Status</th><th>Tipo</th><th>Forma</th><th class="right">Valor</th><th>Observacoes</th><th>Acao</th></tr></thead>
          <tbody>{''.join(table_rows) if table_rows else "<tr><td colspan='9'>Nenhuma contribuicao encontrada.</td></tr>"}</tbody>
        </table>
      </div>
    """
    return render_layout("Contribuicoes", body, "contribuicoes")


def render_contribution_statement(db: PowerChurchDB, query: dict[str, list[str]]) -> str:
    person_id = moneyless_int(query.get("person_id", ["0"])[0])
    year = normalize_query(query.get("year", [""])[0])
    date_start = normalize_query(query.get("date_start", [""])[0])
    date_end = normalize_query(query.get("date_end", [""])[0])
    competencia = normalize_query(query.get("competencia", [""])[0])
    type_ids = [moneyless_int(item) for item in query.get("tipo_id", []) if moneyless_int(item) > 0]
    statement = build_contribution_statement_data(
        db,
        person_id,
        year=year,
        date_start=date_start,
        date_end=date_end,
        competencia=competencia,
        type_ids=type_ids,
    )
    person = statement["person"]
    body = f"""
      <div class="actions"><a class="button" href="/contribuicoes">Voltar para contribuicoes</a><a class="button" href="/pessoas">Pessoas</a></div>
      {message_box(query)}
      <h1>Extrato de contribuicoes</h1>
      <div class="hint">Documento consolidado por pessoa, com filtro por tipos de contribuicao, competencia, ano e intervalo de datas, mantendo ordem cronologica e subtotal por competencia.</div>
    """
    if person is None:
        body += """
          <div class="panel">
            <h2>Escolha uma pessoa</h2>
            <div class="hint">Abra a ficha da pessoa ou a tela de contribuicoes para gerar o extrato correto.</div>
            <div class="actions"><a class="button primary" href="/pessoas">Ir para pessoas</a></div>
          </div>
        """
        return render_layout("Extrato de contribuicoes", body, "contribuicoes")

    years = statement["years"]
    rows = statement["rows"]
    entries = statement["entries"]
    total_general = float(statement["total_general"])
    competence_count = moneyless_int(statement["competence_count"])
    period_label = str(statement["period_label"])
    competences = statement["competences"]
    available_types = statement["available_types"]
    selected_type_ids = statement["selected_type_ids"]
    type_label = str(statement["type_label"])
    year_options = "<option value=''>Todos os anos</option>" + "".join(option(item, item, year) for item in years)
    competence_options = "<option value=''>Todas as competencias</option>" + "".join(
        option(item, item, competencia) for item in competences
    )
    type_checks = "".join(
        f"<label class='check-item'><input type='checkbox' name='tipo_id' value='{row['id']}' {'checked' if moneyless_int(row['id']) in selected_type_ids else ''}>{h(row['nome'])}</label>"
        for row in available_types
    )
    statement_rows = []
    for entry in entries:
        if entry["kind"] == "subtotal":
            statement_rows.append(
                "<tr>"
                f"<td colspan='5'><b>Subtotal {h(entry['competencia'])}</b></td>"
                f"<td class='right'><b>{h(br_money(entry['subtotal']))}</b></td>"
                "</tr>"
            )
            continue
        statement_rows.append(
            "<tr>"
            f"<td>{h(br_date(entry['data_recebimento']))}</td>"
            f"<td>{h(entry['competencia'])}</td>"
            f"<td>{h(entry['tipo_nome'])}</td>"
            f"<td>{h(entry['forma_nome'])}</td>"
            f"<td>{h(entry['observacoes'])}</td>"
            f"<td class='right'>{h(br_money(entry['valor']))}</td>"
            "</tr>"
        )
    cards = [
        ("Lancamentos", len(rows), ""),
        ("Competencias", competence_count, "info"),
        ("Tipos", type_label, "info" if selected_type_ids else ""),
        ("Competencia", competencia or "Todas", "warn" if competencia else ""),
        ("Periodo", period_label, "warn" if (year or date_start or date_end) else ""),
        ("Total geral", br_money(total_general), "ok"),
    ]
    cards_html = "".join(
        f"<div class='card'><div class='label'>{h(label)}</div><div class='value'>{h(value)}</div>{badge(cls, cls) if cls else ''}</div>"
        for label, value, cls in cards
    )
    pdf_query = urllib.parse.urlencode(
        {
            "person_id": person_id,
            "year": year,
            "date_start": date_start,
            "date_end": date_end,
            "competencia": competencia,
            "tipo_id": selected_type_ids,
        },
        doseq=True,
    )
    pdf_href = f"/extrato/contribuicoes.pdf?{pdf_query}"
    pdf_print_href = f"{pdf_href}&inline=1" if pdf_query else "/extrato/contribuicoes.pdf?inline=1"
    body += f"""
      <div class="actions">
        <a class="button primary" href="/pessoa?id={person_id}">Voltar para ficha</a>
        <a class="button" href="/contribuicao/nova?person_id={person_id}">Lancar contribuicao</a>
        <a class="button primary" href="{h(pdf_print_href)}" target="_blank" rel="noopener">Abrir PDF do extrato</a>
        <a class="button" href="{h(pdf_href)}">Baixar PDF</a>
        <button class="button" type="button" onclick="window.print()">Imprimir esta tela</button>
      </div>
      <div class="panel">
        <h2>Membro</h2>
        <div class="field-grid">
          {field_card('Nome', person['nome'])}
          {field_card('Numero de membro', format_member_code(person['codigo_interno']))}
          {field_card('CPF', format_cpf(person['cpf']))}
          {field_card('Status', person['status'])}
        </div>
      </div>
      <form class="filters" method="get" action="/extrato/contribuicoes">
        <input type="hidden" name="person_id" value="{person_id}">
        <label>Ano<select name="year">{year_options}</select></label>
        <label>Competencia<select name="competencia">{competence_options}</select></label>
        <label>Recebimento de<input type="date" name="date_start" value="{h(date_start)}"></label>
        <label>Recebimento ate<input type="date" name="date_end" value="{h(date_end)}"></label>
        <div class="wide">
          <div class="hint">Tipos de contribuicao: marque um ou mais itens. Se nada for marcado, o extrato lista todos.</div>
          <div class="check-grid">{type_checks or "<div class='hint'>Nenhum tipo cadastrado.</div>"}</div>
        </div>
        <button class="button primary" type="submit">Gerar extrato</button>
        <a class="button" href="/extrato/contribuicoes?person_id={person_id}">Limpar</a>
      </form>
      <div class="grid">{cards_html}</div>
      <div class="panel">
        <h2>Extrato analitico</h2>
        <table>
          <thead><tr><th>Data</th><th>Competencia</th><th>Tipo</th><th>Modalidade</th><th>Observacoes</th><th class="right">Valor</th></tr></thead>
          <tbody>{''.join(statement_rows) if statement_rows else "<tr><td colspan='6'>Nenhuma contribuicao encontrada para o filtro informado.</td></tr>"}</tbody>
          <tfoot><tr><th colspan='5'>Total geral</th><th class='right'>{h(br_money(total_general))}</th></tr></tfoot>
        </table>
      </div>
    """
    return render_layout("Extrato de contribuicoes", body, "contribuicoes")


def render_receipt_new(db: PowerChurchDB, query: dict[str, list[str]]) -> str:
    person_id = moneyless_int(query.get("person_id", ["0"])[0])
    person = db.get_person(person_id) if person_id else None
    date_start = normalize_query(query.get("date_start", [""])[0])
    date_end = normalize_query(query.get("date_end", [""])[0])
    body = f"""
      <div class="actions"><a class="button" href="/recibos">Voltar para recibos</a><a class="button" href="/contribuicoes">Ver contribuicoes</a></div>
      {message_box(query)}
      <h1>Gerar recibo individual</h1>
      <div class="hint">Selecione as contribuicoes da pessoa que devem compor o recibo. O sistema impede reutilizar contribuicoes que ja estejam em recibo ativo.</div>
    """
    if person is None:
        body += """
          <div class="panel">
            <h2>Escolha uma pessoa</h2>
            <div class="hint">Abra a ficha da pessoa ou a tela de contribuicoes para gerar o recibo a partir do cadastro correto.</div>
            <div class="actions"><a class="button primary" href="/pessoas">Ir para pessoas</a></div>
          </div>
        """
        return render_layout("Novo recibo", body, "recibos")

    eligible = db.eligible_receipt_contributions(person_id, date_start=date_start, date_end=date_end)
    total_eligible = sum(float(row["valor"]) for row in eligible)
    contribution_rows = []
    for row in eligible:
        contribution_rows.append(
            "<tr>"
            f"<td><input type='checkbox' name='contribuicao_id' value='{row['id']}' checked></td>"
            f"<td>{h(br_date(row['data_recebimento']))}</td>"
            f"<td>{h(row['competencia'])}</td>"
            f"<td>{h(row['tipo_nome'])}</td>"
            f"<td>{h(row['forma_nome'])}</td>"
            f"<td class='right'>{h(br_money(row['valor']))}</td>"
            "</tr>"
        )
    body += f"""
      <div class="panel">
        <h2>Membro selecionado</h2>
        <div class="field-grid">
          {field_card('Nome', person['nome'])}
          {field_card('ID do sistema', format_system_id(person_id))}
          {field_card('Numero de membro', format_member_code(person['codigo_interno']))}
          {field_card('CPF', format_cpf(person['cpf']))}
          {field_card('Contribuicoes elegiveis', len(eligible))}
          {field_card('Total elegivel', br_money(total_eligible), 'wide-field')}
        </div>
      </div>
      <form class="filters" method="get" action="/recibo/novo">
        <input type="hidden" name="person_id" value="{person_id}">
        <label>Recebimento de<input type="date" name="date_start" value="{h(date_start)}"></label>
        <label>Recebimento ate<input type="date" name="date_end" value="{h(date_end)}"></label>
        <button class="button primary" type="submit">Atualizar selecao</button>
        <a class="button" href="/recibo/novo?person_id={person_id}">Limpar</a>
      </form>
      <form method="post" action="/recibo/salvar">
        <input type="hidden" name="pessoa_id" value="{person_id}">
        <div class="panel">
          <h2>Dados do recibo</h2>
          <div class="form-grid">
            <label>Data de emissao<input type="date" name="data_emissao" value="{h(query.get('data_emissao', [date.today().isoformat()])[0])}"></label>
            {textarea_field('observacoes', 'Observacoes', query.get('observacoes', [''])[0], css_class='wide')}
          </div>
        </div>
        <div class="panel">
          <h2>Contribuicoes selecionaveis</h2>
          <table>
            <thead><tr><th>Usar</th><th>Data</th><th>Competencia</th><th>Tipo</th><th>Forma</th><th class="right">Valor</th></tr></thead>
            <tbody>{''.join(contribution_rows) if contribution_rows else "<tr><td colspan='6'>Nenhuma contribuicao elegivel encontrada para o periodo.</td></tr>"}</tbody>
          </table>
        </div>
        <div class="actions">
          <button class="button primary" type="submit">Gerar recibo</button>
          <a class="button" href="/pessoa?id={person_id}">Voltar para ficha</a>
        </div>
      </form>
    """
    return render_layout("Novo recibo", body, "recibos")


def render_receipts(db: PowerChurchDB, query: dict[str, list[str]]) -> str:
    q = normalize_query(query.get("q", [""])[0])
    person_id = moneyless_int(query.get("person_id", ["0"])[0])
    date_start = normalize_query(query.get("date_start", [""])[0])
    date_end = normalize_query(query.get("date_end", [""])[0])
    selected_person = db.get_person(person_id) if person_id else None
    rows = db.list_receipts(q=q, person_id=person_id, date_start=date_start, date_end=date_end)
    summary = db.receipts_summary(q=q, person_id=person_id, date_start=date_start, date_end=date_end)
    cards = [
        ("Recibos", summary["quantidade"], ""),
        ("Valor total", br_money(summary["total"]), "ok"),
        ("Pessoas", summary["pessoas"], "info"),
        ("Ultima emissao", br_date(summary["ultima_data"]) or "Nao registrada", "warn" if summary["ultima_data"] else ""),
    ]
    cards_html = "".join(
        f"<div class='card'><div class='label'>{h(label)}</div><div class='value'>{h(value)}</div>{badge(cls, cls) if cls else ''}</div>"
        for label, value, cls in cards
    )
    receipt_rows = []
    for row in rows:
        person_cell = f"<a href='/pessoa?id={row['pessoa_id']}'>{h(row['pessoa_nome'])}</a><div class='hint'>{h(format_system_id(row['pessoa_id']))} | {h(format_member_code(row['codigo_interno']))} | CPF {h(format_cpf(row['cpf']))}</div>"
        receipt_rows.append(
            "<tr>"
            f"<td><a href='/recibo?id={row['id']}'>{h(row['numero'])}</a></td>"
            f"<td>{h(br_date(row['data_emissao']))}</td>"
            f"<td>{person_cell}</td>"
            f"<td>{h(br_date(row['periodo_inicio']))} ate {h(br_date(row['periodo_fim']))}</td>"
            f"<td class='right'>{h(br_money(row['valor_total']))}</td>"
            f"<td>{badge(row['status'], 'ok' if row['status'] == 'emitido' else 'warn')}</td>"
            f"<td><a class='button small' href='/recibo?id={row['id']}'>Abrir recibo</a></td>"
            "</tr>"
        )
    action_html = (
        f"<a class='button primary' href='/recibo/novo?person_id={person_id}'>Gerar recibo de {h(selected_person['nome'])}</a>"
        if selected_person
        else "<a class='button primary' href='/pessoas'>Escolher pessoa para recibo</a>"
    )
    body = f"""
      <div class="actions">{action_html}<a class="button" href="/contribuicoes">Contribuicoes</a></div>
      {message_box(query)}
      <h1>Recibos</h1>
      <div class="hint">Recibos individuais emitidos a partir das contribuicoes registradas da pessoa. Cada contribuicao so entra em um recibo ativo.</div>
      {'<div class="panel"><h2>Membro filtrado</h2><div class="hint">Recibos de ' + h(selected_person['nome']) + '.</div></div>' if selected_person else ''}
      <div class="grid">{cards_html}</div>
      <form class="filters" method="get" action="/recibos">
        {'<input type="hidden" name="person_id" value="' + str(person_id) + '">' if person_id else ''}
        <label>Busca<input name="q" value="{h(q)}" placeholder="pessoa, ID-001078, MEM-00003, CPF ou recibo"></label>
        <label>Emissao de<input type="date" name="date_start" value="{h(date_start)}"></label>
        <label>Emissao ate<input type="date" name="date_end" value="{h(date_end)}"></label>
        <button class="button primary" type="submit">Filtrar</button>
        <a class="button" href="/recibos">Limpar</a>
      </form>
      <div class="panel">
        <h2>{len(rows)} recibo(s) exibido(s)</h2>
        <table>
          <thead><tr><th>Numero</th><th>Emissao</th><th>Membro</th><th>Periodo</th><th class="right">Valor</th><th>Status</th><th>Acao</th></tr></thead>
          <tbody>{''.join(receipt_rows) if receipt_rows else "<tr><td colspan='7'>Nenhum recibo encontrado.</td></tr>"}</tbody>
        </table>
      </div>
    """
    return render_layout("Recibos", body, "recibos")


def render_receipt(db: PowerChurchDB, query: dict[str, list[str]]) -> str:
    receipt_id = moneyless_int(query.get("id", ["0"])[0])
    receipt = db.get_receipt(receipt_id)
    if receipt is None:
        return render_layout("Recibo nao encontrado", "<div class='empty'>Recibo nao encontrado.</div>", "recibos")
    items = db.receipt_items(receipt_id)
    item_rows = "".join(
        "<tr>"
        f"<td>{h(br_date(row['data_recebimento']))}</td>"
        f"<td>{h(row['tipo_nome'])}</td>"
        f"<td>{h(row['forma_nome'])}</td>"
        f"<td>{h(row['competencia'])}</td>"
        f"<td>{h(row['observacoes'])}</td>"
        f"<td class='right'>{h(br_money(row['valor']))}</td>"
        "</tr>"
        for row in items
    )
    body = f"""
      <div class="actions">
        <a class="button" href="/recibos?person_id={receipt['pessoa_id']}">Voltar para recibos</a>
        <a class="button" href="/pessoa?id={receipt['pessoa_id']}">Voltar para ficha</a>
        <button class="button primary" type="button" onclick="window.print()">Imprimir recibo</button>
      </div>
      {message_box(query)}
      <div class="panel">
        <div class="section-head"><h1>Recibo {h(receipt['numero'])}</h1><span>{badge(receipt['status'], 'ok' if receipt['status'] == 'emitido' else 'warn')}</span></div>
        <div class="hint">{h(receipt['organizacao_fantasia'] or receipt['organizacao_nome'])}</div>
        <div class="field-grid">
          {field_card('Membro', receipt['pessoa_nome'])}
          {field_card('ID do sistema', format_system_id(receipt['pessoa_id']))}
          {field_card('Numero de membro', format_member_code(receipt['codigo_interno']))}
          {field_card('CPF', format_cpf(receipt['cpf']))}
          {field_card('Data de emissao', br_date(receipt['data_emissao']))}
          {field_card('Periodo inicial', br_date(receipt['periodo_inicio']))}
          {field_card('Periodo final', br_date(receipt['periodo_fim']))}
          {field_card('Valor total', br_money(receipt['valor_total']))}
          {field_card('Observacoes', receipt['observacoes'], 'wide-field')}
        </div>
      </div>
      <div class="panel">
        <h2>Itens do recibo</h2>
        <table>
          <thead><tr><th>Recebimento</th><th>Tipo</th><th>Forma</th><th>Competencia</th><th>Observacoes</th><th class="right">Valor</th></tr></thead>
          <tbody>{item_rows or "<tr><td colspan='6'>Sem itens vinculados.</td></tr>"}</tbody>
        </table>
      </div>
    """
    return render_layout(f"Recibo {receipt['numero']}", body, "recibos")


def render_audit(db: PowerChurchDB, query: dict[str, list[str]]) -> str:
    tipo = normalize_query(query.get("tipo", [""])[0])
    severidade = normalize_query(query.get("severidade", [""])[0])
    summary = db.audit_summary()
    rows = db.audit_rows(tipo, severidade)
    people = db.audit_people(tipo, severidade)
    summary_rows = "".join(
        f"<tr><td><a href='/auditoria?tipo={urllib.parse.quote(row['tipo'])}&severidade={urllib.parse.quote(row['severidade'])}'>{h(row['tipo'])}</a></td><td>{badge(row['severidade'], 'danger' if row['severidade'] == 'aviso' else 'info')}</td><td class='right'>{row['quantidade']}</td></tr>"
        for row in summary
    )
    people_rows = []
    for row in people:
        person_url = f"/pessoa?id={row['pessoa_id']}"
        warning_badge = badge(f"{row['avisos']} aviso(s)", "danger" if row["avisos"] else "")
        info_badge = badge(f"{row['infos']} info", "info" if row["infos"] else "")
        people_rows.append(
            "<tr>"
            f"<td><a href='{person_url}'>{h(row['nome'])}</a><div class='hint'>{h(format_system_id(row['pessoa_id']))} | {h(format_member_code(row['codigo_interno']))} | Status {h(row['status'])}</div></td>"
            f"<td class='right'>{badge(row['total'], 'danger' if row['avisos'] else 'info')}</td>"
            f"<td>{warning_badge}{info_badge}</td>"
            f"<td>{h(row['tipos'])}</td>"
            f"<td><a class='button small' href='{person_url}'>Abrir ficha</a> <a class='button small primary' href='{person_edit_url(row['pessoa_id'], audit_mode=True)}'>Corrigir</a></td>"
            "</tr>"
        )
    audit_rows = []
    for row in rows:
        person_url = f"/pessoa?id={row['pessoa_id']}" if row["pessoa_id"] else ""
        person_link = f"<a href='{person_url}'>{h(row['nome'])}</a>" if person_url else ""
        person_action = ""
        if person_url:
            person_action = f"<a class='button small' href='{person_url}'>Abrir ficha</a> <a class='button small primary' href='{person_edit_url(row['pessoa_id'], audit_mode=True)}'>Corrigir</a> "
            if row.get("resolvivel"):
                person_action += (
                    f"<form method='post' action='/pendencia/resolver' style='display:inline'>"
                    f"<input type='hidden' name='pendencia_id' value='{row['id']}'>"
                    "<input type='hidden' name='return_to' value='/auditoria'>"
                    "<button class='button small' type='submit'>Resolver</button></form>"
                )
            else:
                person_action += "<span class='hint'>Corrija a ficha para retirar este alerta.</span>"
        audit_rows.append(
            "<tr>"
            f"<td>{badge(row['severidade'], 'danger' if row['severidade'] == 'aviso' else 'info')}</td>"
            f"<td>{h(row['tipo'])}</td>"
            f"<td>{person_link}<div class='hint'>{h(format_system_id(row['pessoa_id']))} | {h(format_member_code(row['codigo_interno']))} | Status {h(row['status'])} | Origem {h(row['numero_linha'])}</div></td>"
            f"<td>{h(row['descricao'])}<div class='hint'>{h(row['acao_sugerida'])}</div></td>"
            f"<td>{person_action}</td>"
            "</tr>"
        )
    body = f"""
      <h1>Auditoria do cadastro</h1>
      <div class="hint">Pendencias de importacao e alertas dinamicos do cadastro para conversar com o cliente. Elas nao impedem o projeto; servem para orientar saneamento e regras futuras.</div>
      <form class="filters" method="get" action="/auditoria">
        <label>Tipo<input name="tipo" value="{h(tipo)}" placeholder="ex: cpf_invalido"></label>
        <label>Severidade
          <select name="severidade">
            <option value="">Todas</option>
            <option value="aviso" {'selected' if severidade == 'aviso' else ''}>Aviso</option>
            <option value="info" {'selected' if severidade == 'info' else ''}>Info</option>
          </select>
        </label>
        <button class="button primary" type="submit">Filtrar</button>
        <a class="button" href="/auditoria">Limpar</a>
      </form>
      <div class="detail-grid">
        <div class="panel">
          <h2>Resumo por tipo</h2>
          <table><thead><tr><th>Tipo</th><th>Severidade</th><th class="right">Qtd</th></tr></thead><tbody>{summary_rows}</tbody></table>
        </div>
        <div class="panel">
          <h2>Como usar com o cliente</h2>
          <p class="hint">Os avisos merecem revisao objetiva, como CPF invalido, numero de membro duplicado ou campo essencial vazio. Alguns itens sao baixados manualmente; outros, como duplicidade de numero de membro, desaparecem assim que a ficha e corrigida.</p>
        </div>
      </div>
      <div class="panel">
        <h2>{len(people)} ficha(s) com ajuste ou revisao</h2>
        <div class="hint">Lista operacional das pessoas afetadas pelo filtro atual. Clique em Abrir ficha para conferir os dados completos e as pendencias daquela pessoa.</div>
        <table>
          <thead><tr><th>Pessoa</th><th class="right">Pend.</th><th>Nivel</th><th>Tipos</th><th>Acao</th></tr></thead>
          <tbody>{''.join(people_rows) if people_rows else "<tr><td colspan='5'>Nenhuma ficha pendente encontrada.</td></tr>"}</tbody>
        </table>
      </div>
      <div class="panel">
        <h2>{len(rows)} pendencia(s) exibida(s)</h2>
        <table>
          <thead><tr><th>Sev.</th><th>Tipo</th><th>Pessoa</th><th>Descricao / Acao sugerida</th><th>Acao</th></tr></thead>
          <tbody>{''.join(audit_rows) if audit_rows else "<tr><td colspan='5'>Nenhuma pendencia encontrada.</td></tr>"}</tbody>
        </table>
      </div>
    """
    return render_layout("Auditoria", body, "auditoria")


class Handler(BaseHTTPRequestHandler):
    server_version = "PowerChurchDemo/0.1"

    def read_form(self) -> dict[str, list[str]]:
        length = moneyless_int(self.headers.get("Content-Length"))
        payload = self.rfile.read(length).decode("utf-8", "replace") if length else ""
        return urllib.parse.parse_qs(payload, keep_blank_values=True)

    def read_multipart_form(self) -> cgi.FieldStorage:
        environ = {
            "REQUEST_METHOD": "POST",
            "CONTENT_TYPE": self.headers.get("Content-Type", ""),
            "CONTENT_LENGTH": self.headers.get("Content-Length", "0"),
        }
        return cgi.FieldStorage(fp=self.rfile, headers=self.headers, environ=environ, keep_blank_values=True)

    def raw_form_value(self, form: object, key: str, default: str = "") -> str:
        if hasattr(form, "getfirst"):
            return str(form.getfirst(key, default))
        if isinstance(form, dict):
            values = form.get(key, [default])
            return str(values[0] if values else default)
        return default

    def send_file(self, file_path: Path) -> None:
        payload = file_path.read_bytes()
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def send_bytes(self, payload: bytes, content_type: str, filename: str = "", inline: bool = False) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        if filename:
            disposition = "inline" if inline else "attachment"
            self.send_header("Content-Disposition", f'{disposition}; filename="{filename}"')
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def redirect(self, path: str) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", path)
        self.end_headers()

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        form: object = {}
        person_id = 0
        edit_mode = ""
        fallback = "/auditoria"
        try:
            if parsed.path == "/pessoa/salvar":
                form = self.read_form()
                person_id = moneyless_int(form.get("id", ["0"])[0])
                edit_mode = normalize_query(form.get("edit_mode", [""])[0])
                if person_id:
                    fallback = person_edit_url(person_id, audit_mode=edit_mode == "auditoria")
                    self.server.db.update_person_from_form(person_id, form)
                    msg = urllib.parse.quote("Cadastro salvo com sucesso.")
                    self.redirect(f"/pessoa?id={person_id}&msg={msg}")
                else:
                    fallback = "/pessoa/nova"
                    created_id = self.server.db.create_person_from_form(form)
                    msg = urllib.parse.quote("Nova pessoa criada com sucesso.")
                    self.redirect(f"/pessoa?id={created_id}&msg={msg}")
                return
            if parsed.path == "/contribuinte/criar-frequentador":
                form = self.read_form()
                contributor_id = moneyless_int(form.get("contributor_id", ["0"])[0])
                family_person_id = moneyless_int(form.get("family_person_id", ["0"])[0])
                return_to = safe_redirect_path(
                    form.get("return_to", [f"/contribuinte?id={contributor_id}"])[0],
                    f"/contribuinte?id={contributor_id}",
                )
                fallback = return_to
                created_id = self.server.db.create_frequentador_from_contributor(
                    contributor_id,
                    family_person_id=family_person_id,
                )
                msg_text = "Frequentador criado e contribuinte vinculado com sucesso."
                if family_person_id:
                    msg_text += " A referencia familiar ficou registrada na observacao da ficha."
                msg = urllib.parse.quote(msg_text)
                self.redirect(f"/pessoa/editar?id={created_id}&msg={msg}")
                return
            if parsed.path == "/pessoas/status-lote":
                form = self.read_form()
                return_to = safe_redirect_path(form.get("return_to", ["/pessoas"])[0], "/pessoas")
                fallback = return_to
                selected_ids = [moneyless_int(value) for value in form.get("pessoa_id", [])]
                updated = self.server.db.bulk_update_people_status(selected_ids, form.get("novo_status", [""])[0])
                separator = "&" if "?" in return_to else "?"
                msg = urllib.parse.quote(f"Atualizacao em lote aplicada em {updated} ficha(s).")
                self.redirect(f"{return_to}{separator}msg={msg}")
                return
            if parsed.path == "/pessoas/importar":
                form = self.read_multipart_form()
                fallback = "/pessoas/importar"
                if "planilha_xlsx" not in form:
                    raise ValueError("Selecione uma planilha Excel antes de importar pessoas.")
                sheet_item = form["planilha_xlsx"]
                if not getattr(sheet_item, "filename", ""):
                    raise ValueError("Selecione uma planilha Excel antes de importar pessoas.")
                summary = self.server.db.create_people_import_from_upload(
                    str(sheet_item.filename),
                    sheet_item.file.read(),
                    allow_duplicate_file=self.raw_form_value(form, "allow_duplicate_file", "") == "1",
                )
                stats = dict(summary.get("stats", {}))
                msg_text = (
                    f"Lote de pessoas #{summary['lote_id']} importado. "
                    f"{stats.get('criados', 0)} nova(s) ficha(s), "
                    f"{stats.get('existentes_encontrados', 0)} ficha(s) reconhecida(s), "
                    f"{summary.get('pendencias', 0)} pendencia(s) para auditoria."
                )
                msg = urllib.parse.quote(msg_text)
                self.redirect(f"/pessoas/importar?msg={msg}")
                return
            if parsed.path == "/contribuicao/salvar":
                form = self.read_form()
                person_id = moneyless_int(form.get("pessoa_id", ["0"])[0])
                fallback = f"/contribuicao/nova?person_id={person_id}"
                contribution_id = self.server.db.create_contribution_from_form(form)
                msg = urllib.parse.quote(f"Contribuicao #{contribution_id} registrada com sucesso.")
                self.redirect(f"/contribuicoes?person_id={person_id}&msg={msg}")
                return
            if parsed.path == "/pix/lotes/upload":
                form = self.read_multipart_form()
                fallback = safe_redirect_path(self.raw_form_value(form, "return_to", "/pix"), "/pix")
                if "extrato_pdf" not in form:
                    raise ValueError("Selecione um PDF de extrato PIX antes de continuar.")
                pdf_item = form["extrato_pdf"]
                if not getattr(pdf_item, "filename", ""):
                    raise ValueError("Selecione um PDF de extrato PIX antes de continuar.")
                lot_id = self.server.db.create_pix_lot_from_upload(
                    str(pdf_item.filename),
                    pdf_item.file.read(),
                )
                imported_now = self.server.db.scalar(
                    "SELECT COUNT(*) FROM pix_movimentos WHERE lote_id = ? AND imported_contribution_id IS NOT NULL",
                    (lot_id,),
                )
                pending_now = self.server.db.scalar(
                    "SELECT COUNT(*) FROM pix_movimentos WHERE lote_id = ? AND review_status IN ('revisar_pessoa', 'revisar_destinacao', 'revisar_duplicidade')",
                    (lot_id,),
                )
                msg = urllib.parse.quote(
                    f"Lote PIX #{lot_id} criado com sucesso. {imported_now} movimento(s) ja entraram no financeiro e {pending_now} seguem em saneamento."
                )
                self.redirect(f"/pix/lote?id={lot_id}&msg={msg}")
                return
            if parsed.path == "/extratos/lotes/upload":
                form = self.read_multipart_form()
                fallback = safe_redirect_path(self.raw_form_value(form, "return_to", "/extratos"), "/extratos")
                if "extrato_pdf" not in form:
                    raise ValueError("Selecione um PDF de extrato bancario antes de continuar.")
                pdf_item = form["extrato_pdf"]
                if not getattr(pdf_item, "filename", ""):
                    raise ValueError("Selecione um PDF de extrato bancario antes de continuar.")
                layout_code = self.raw_form_value(form, "layout_code", "BRADESCO_EXTRATO")
                lot_id = self.server.db.create_statement_lot_from_upload(
                    str(pdf_item.filename),
                    pdf_item.file.read(),
                    layout_code=layout_code,
                )
                imported_now = self.server.db.scalar(
                    "SELECT COUNT(*) FROM extrato_movimentos WHERE lote_id = ? AND imported_contribution_id IS NOT NULL",
                    (lot_id,),
                )
                pending_now = self.server.db.scalar(
                    "SELECT COUNT(*) FROM extrato_movimentos WHERE lote_id = ? AND review_status IN ('revisar_pessoa', 'revisar_destinacao', 'revisar_duplicidade')",
                    (lot_id,),
                )
                layout_label = statement_layout_label(layout_code)
                msg = urllib.parse.quote(
                    f"Lote de extrato #{lot_id} ({layout_label}) criado com sucesso. {imported_now} credito(s) ja entraram no financeiro e {pending_now} seguem em saneamento."
                )
                self.redirect(f"/extratos/lote?id={lot_id}&msg={msg}")
                return
            if parsed.path in {"/pix/regras/salvar", "/extratos/regras/salvar"}:
                form = self.read_form()
                fallback = "/extratos/regras" if parsed.path.startswith("/extratos") else "/pix/regras"
                saved_id = self.server.db.save_pix_rule_from_form(form)
                msg = urllib.parse.quote(f"Regra de centavos #{saved_id} salva com sucesso.")
                target = "/extratos/regras" if parsed.path.startswith("/extratos") else "/pix/regras"
                self.redirect(f"{target}?edit_rule_id={saved_id}&msg={msg}")
                return
            if parsed.path == "/pix/movimento/salvar":
                form = self.read_form()
                movement_id = moneyless_int(form.get("movement_id", ["0"])[0])
                movement = self.server.db.get_pix_movement(movement_id)
                if movement is None:
                    raise ValueError("Movimento PIX nao encontrado.")
                lot_id = moneyless_int(movement["lote_id"])
                default_return_to = f"/pix/lote?{urllib.parse.urlencode([('id', str(lot_id)), ('status', 'pendencias')])}"
                return_to = safe_redirect_path(self.raw_form_value(form, "return_to", default_return_to), default_return_to)
                fallback = f"/pix/movimento?id={movement_id}&return_to={urllib.parse.quote(return_to, safe='')}"
                imported_contribution_id = self.server.db.update_pix_movement_from_form(movement_id, form)
                msg_text = (
                    "Movimento PIX ignorado."
                    if first_form_value(form, "action") == "ignore"
                    else (
                        f"Movimento PIX confirmado e sincronizado com a contribuicao #{imported_contribution_id}."
                        if imported_contribution_id
                        else "Movimento PIX confirmado com sucesso."
                    )
                )
                msg = urllib.parse.quote(msg_text)
                separator = "&" if "?" in return_to else "?"
                self.redirect(f"{return_to}{separator}msg={msg}")
                return
            if parsed.path == "/pix/lote/reprocessar":
                form = self.read_form()
                lot_id = moneyless_int(form.get("lot_id", ["0"])[0])
                default_return_to = f"/pix/lote?{urllib.parse.urlencode([('id', str(lot_id)), ('status', 'pendencias')])}"
                return_to = safe_redirect_path(self.raw_form_value(form, "return_to", default_return_to), default_return_to)
                fallback = return_to
                updated = self.server.db.reprocess_pix_lot(lot_id)
                msg = urllib.parse.quote(
                    f"Lote PIX reprocessado. {updated} movimento(s) foram revistos e o financeiro do lote foi sincronizado."
                )
                separator = "&" if "?" in return_to else "?"
                self.redirect(f"{return_to}{separator}msg={msg}")
                return
            if parsed.path == "/pix/lote/importar":
                form = self.read_form()
                lot_id = moneyless_int(form.get("lot_id", ["0"])[0])
                default_return_to = f"/pix/lote?{urllib.parse.urlencode([('id', str(lot_id)), ('status', 'pendencias')])}"
                return_to = safe_redirect_path(self.raw_form_value(form, "return_to", default_return_to), default_return_to)
                fallback = return_to
                imported = self.server.db.import_ready_pix_lot(lot_id)
                msg = urllib.parse.quote(f"Financeiro do lote sincronizado. {imported} movimento(s) que ainda nao tinham contribuicao passaram a ter lancamento financeiro.")
                separator = "&" if "?" in return_to else "?"
                self.redirect(f"{return_to}{separator}msg={msg}")
                return
            if parsed.path == "/extratos/lote/reprocessar":
                form = self.read_form()
                lot_id = moneyless_int(form.get("lot_id", ["0"])[0])
                default_return_to = f"/extratos/lote?{urllib.parse.urlencode([('id', str(lot_id)), ('status', 'pendencias')])}"
                return_to = safe_redirect_path(self.raw_form_value(form, "return_to", default_return_to), default_return_to)
                fallback = return_to
                updated = self.server.db.reprocess_statement_lot(lot_id)
                msg = urllib.parse.quote(
                    f"Lote de extrato reprocessado. {updated} movimento(s) foram revistos e o financeiro do lote foi sincronizado."
                )
                separator = "&" if "?" in return_to else "?"
                self.redirect(f"{return_to}{separator}msg={msg}")
                return
            if parsed.path == "/extratos/lote/encerrar":
                form = self.read_form()
                lot_id = moneyless_int(form.get("lot_id", ["0"])[0])
                default_return_to = f"/extratos/lote?{urllib.parse.urlencode([('id', str(lot_id)), ('status', 'pendencias')])}"
                return_to = safe_redirect_path(self.raw_form_value(form, "return_to", default_return_to), default_return_to)
                fallback = return_to
                result = self.server.db.close_statement_lot(lot_id)
                msg = urllib.parse.quote(
                    f"Lote de extrato encerrado. {result['importados']} movimento(s) que ainda nao tinham contribuicao receberam lancamento financeiro e {result['movidos_contribuintes']} pendencia(s) seguiram para a central de contribuintes sem pessoa vinculada."
                )
                separator = "&" if "?" in return_to else "?"
                self.redirect(f"{return_to}{separator}msg={msg}")
                return
            if parsed.path == "/pix/lote/encerrar":
                form = self.read_form()
                lot_id = moneyless_int(form.get("lot_id", ["0"])[0])
                default_return_to = f"/pix/lote?{urllib.parse.urlencode([('id', str(lot_id)), ('status', 'pendencias')])}"
                return_to = safe_redirect_path(self.raw_form_value(form, "return_to", default_return_to), default_return_to)
                fallback = return_to
                result = self.server.db.close_pix_lot(lot_id)
                msg = urllib.parse.quote(
                    f"Lote PIX encerrado. {result['importados']} movimento(s) que ainda nao tinham contribuicao receberam lancamento financeiro e {result['movidos_contribuintes']} pendencia(s) seguiram para a fila de contribuintes sem pessoa vinculada."
                )
                separator = "&" if "?" in return_to else "?"
                self.redirect(f"{return_to}{separator}msg={msg}")
                return
            if parsed.path == "/extratos/movimento/salvar":
                form = self.read_form()
                movement_id = moneyless_int(form.get("movement_id", ["0"])[0])
                movement = self.server.db.get_statement_movement(movement_id)
                if movement is None:
                    raise ValueError("Movimento de extrato nao encontrado.")
                lot_id = moneyless_int(movement["lote_id"])
                default_return_to = f"/extratos/lote?{urllib.parse.urlencode([('id', str(lot_id)), ('status', 'pendencias')])}"
                return_to = safe_redirect_path(self.raw_form_value(form, "return_to", default_return_to), default_return_to)
                fallback = f"/extratos/movimento?id={movement_id}&return_to={urllib.parse.quote(return_to, safe='')}"
                imported_contribution_id = self.server.db.update_statement_movement_from_form(movement_id, form)
                action = first_form_value(form, "action")
                msg_text = (
                    "Movimento de extrato classificado como mesma titularidade / origem interna."
                    if action == "same_owner"
                    else "Movimento de extrato ignorado."
                    if action == "ignore"
                    else (
                        f"Movimento de extrato confirmado e sincronizado com a contribuicao #{imported_contribution_id}."
                        if imported_contribution_id
                        else "Movimento de extrato confirmado com sucesso."
                    )
                )
                msg = urllib.parse.quote(msg_text)
                separator = "&" if "?" in return_to else "?"
                self.redirect(f"{return_to}{separator}msg={msg}")
                return
            if parsed.path == "/contribuinte/vincular":
                form = self.read_form()
                contributor_id = moneyless_int(form.get("contributor_id", ["0"])[0])
                person_id = moneyless_int(form.get("person_id", ["0"])[0])
                return_to = safe_redirect_path(
                    form.get("return_to", [f"/contribuinte?id={contributor_id}"])[0],
                    f"/contribuinte?id={contributor_id}",
                )
                fallback = return_to
                contributor = self.server.db.get_contributor(contributor_id)
                person = self.server.db.get_person(person_id)
                if contributor is None:
                    raise ValueError("Contribuinte nao encontrado.")
                if person is None:
                    raise ValueError("Pessoa nao encontrada para vinculo.")
                current_person_id = moneyless_int(contributor["pessoa_id"])
                if current_person_id and current_person_id != person_id:
                    raise ValueError("Este contribuinte ja esta vinculado a outra pessoa. Vamos tratar reatribuicao em um fluxo seguro separado.")
                changed = self.server.db.link_contributor_to_person(
                    contributor_id,
                    person_id,
                    note="Vinculado manualmente pela ficha do contribuinte.",
                    commit=True,
                )
                msg_text = (
                    f"Contribuinte vinculado a {person['nome']} com sucesso. A origem financeira foi preservada."
                    if changed
                    else "Nenhuma alteracao foi feita; este contribuinte ja estava vinculado a essa pessoa."
                )
                separator = "&" if "?" in return_to else "?"
                msg = urllib.parse.quote(msg_text)
                self.redirect(f"{return_to}{separator}msg={msg}")
                return
            if parsed.path == "/pessoa/vincular-contribuinte":
                form = self.read_form()
                contributor_id = moneyless_int(form.get("contributor_id", ["0"])[0])
                person_id = moneyless_int(form.get("person_id", ["0"])[0])
                return_to = safe_redirect_path(
                    form.get("return_to", [f"/pessoa?id={person_id}"])[0],
                    f"/pessoa?id={person_id}",
                )
                fallback = return_to
                contributor = self.server.db.get_contributor(contributor_id)
                person = self.server.db.get_person(person_id)
                if contributor is None:
                    raise ValueError("Contribuinte nao encontrado.")
                if person is None:
                    raise ValueError("Pessoa nao encontrada para o vinculo.")
                current_person_id = moneyless_int(contributor["pessoa_id"])
                if current_person_id and current_person_id != person_id:
                    raise ValueError("Este contribuinte ja esta vinculado a outra pessoa. Se quiser, no proximo passo eu crio uma tela segura de reatribuicao.")
                changed = self.server.db.link_contributor_to_person(
                    contributor_id,
                    person_id,
                    note="Vinculado manualmente pela ficha da pessoa.",
                    commit=True,
                )
                msg_text = (
                    f"Contribuinte financeiro vinculado a {person['nome']} com sucesso."
                    if changed
                    else "Nenhuma alteracao foi feita; este contribuinte ja estava vinculado a essa pessoa."
                )
                separator = "&" if "?" in return_to else "?"
                msg = urllib.parse.quote(msg_text)
                self.redirect(f"{return_to}{separator}msg={msg}")
                return
            if parsed.path == "/pessoa/identidade-financeira/salvar":
                form = self.read_form()
                person_id = moneyless_int(form.get("person_id", ["0"])[0])
                return_to = safe_redirect_path(
                    form.get("return_to", [f"/pessoa?id={person_id}"])[0],
                    f"/pessoa?id={person_id}",
                )
                fallback = return_to
                identifier_id = self.server.db.save_person_financial_identifier(
                    person_id,
                    first_form_value(form, "identifier_type"),
                    first_form_value(form, "identifier_value"),
                    first_form_value(form, "notes"),
                )
                separator = "&" if "?" in return_to else "?"
                msg = urllib.parse.quote(f"Identidade financeira #{identifier_id} cadastrada com sucesso.")
                self.redirect(f"{return_to}{separator}msg={msg}")
                return
            if parsed.path == "/recibo/salvar":
                form = self.read_form()
                person_id = moneyless_int(form.get("pessoa_id", ["0"])[0])
                fallback = f"/recibo/novo?person_id={person_id}"
                receipt_id = self.server.db.create_receipt_from_form(form)
                msg = urllib.parse.quote(f"Recibo #{receipt_id} gerado com sucesso.")
                self.redirect(f"/recibo?id={receipt_id}&msg={msg}")
                return
            if parsed.path == "/pessoa/foto/upload":
                form = self.read_multipart_form()
                person_id = moneyless_int(self.raw_form_value(form, "id", "0"))
                fallback = f"/pessoa/editar?id={person_id}"
                return_to_raw = self.raw_form_value(form, "return_to", fallback)
                return_to = safe_redirect_path(return_to_raw, f"/pessoa/editar?id={person_id}")
                fallback = return_to
                person = self.server.db.get_person(person_id)
                if person is None:
                    raise ValueError("Pessoa nao encontrada.")
                if "foto" not in form:
                    raise ValueError("Selecione um arquivo de foto antes de enviar.")
                photo_item = form["foto"]
                if not getattr(photo_item, "filename", ""):
                    raise ValueError("Selecione um arquivo de foto antes de enviar.")
                before = self.server.db.person_snapshot(person_id)
                saved = save_member_photo(
                    person_id,
                    person["cpf"],
                    person["nome"],
                    str(photo_item.filename),
                    photo_item.file.read(),
                    getattr(photo_item, "type", ""),
                )
                after = self.server.db.person_snapshot(person_id)
                self.server.db.write_audit_log(
                    moneyless_int(person["organizacao_id"]),
                    "upload_foto_membro",
                    "pessoas",
                    person_id,
                    before,
                    after | {"foto_upload": saved.name},
                )
                self.server.db.conn.commit()
                separator = "&" if "?" in return_to else "?"
                msg = urllib.parse.quote("Foto enviada com sucesso.")
                self.redirect(f"{return_to}{separator}msg={msg}")
                return
            if parsed.path == "/pendencia/resolver":
                form = self.read_form()
                pending_id = moneyless_int(form.get("pendencia_id", ["0"])[0])
                return_to = safe_redirect_path(form.get("return_to", ["/auditoria"])[0], "/auditoria")
                fallback = return_to
                self.server.db.resolve_pending(pending_id, "Resolvido por revisao manual.")
                separator = "&" if "?" in return_to else "?"
                msg = urllib.parse.quote("Pendencia marcada como resolvida.")
                self.redirect(f"{return_to}{separator}msg={msg}")
                return
            self.send_error(HTTPStatus.NOT_FOUND, "Acao nao encontrada")
        except sqlite3.IntegrityError:
            self.server.db.conn.rollback()
            if parsed.path == "/pessoa/salvar":
                msg_text = "Nao foi possivel salvar: CPF ou numero de membro ja existe em outra ficha."
                target = person_edit_url(person_id, audit_mode=edit_mode == "auditoria") if person_id else "/pessoa/nova"
            elif parsed.path == "/pix/lotes/upload":
                msg_text = "Este PDF PIX ja foi carregado anteriormente. Use o lote existente ou o reprocessamento."
                target = "/pix"
            elif parsed.path == "/extratos/lotes/upload":
                msg_text = "Este PDF de extrato ja foi carregado anteriormente. Use o lote existente ou o reprocessamento."
                target = "/extratos"
            elif parsed.path in {"/pix/regras/salvar", "/extratos/regras/salvar"}:
                msg_text = "Nao foi possivel salvar a regra de centavos: o codigo ja esta em uso."
                target = "/extratos/regras" if parsed.path.startswith("/extratos") else "/pix/regras"
            else:
                msg_text = "Nao foi possivel concluir a operacao por conflito de dados."
                target = fallback
            msg = urllib.parse.quote(msg_text)
            separator = "&" if "?" in target else "?"
            self.redirect(f"{target}{separator}error=1&msg={msg}")
        except Exception as exc:  # noqa: BLE001 - demo local should keep the workflow recoverable
            self.server.db.conn.rollback()
            error_text = str(exc) or "Erro ao processar acao."
            if parsed.path == "/pix/lotes/upload":
                match = re.search(r"lote PIX #(\\d+)", error_text)
                if match:
                    msg = urllib.parse.quote(error_text)
                    self.redirect(f"/pix/lote?id={match.group(1)}&msg={msg}")
                    return
            if parsed.path == "/extratos/lotes/upload":
                match = re.search(r"lote de extrato #(\\d+)", error_text)
                if match:
                    msg = urllib.parse.quote(error_text)
                    self.redirect(f"/extratos/lote?id={match.group(1)}&msg={msg}")
                    return
            msg = urllib.parse.quote(error_text)
            self.redirect(f"{fallback}&error=1&msg={msg}" if "?" in fallback else f"{fallback}?error=1&msg={msg}")

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        try:
            if parsed.path == "/":
                page = render_home(self.server.db)
            elif parsed.path == "/importacoes":
                page = render_imports_center(self.server.db, query)
            elif parsed.path == "/pix":
                page = render_pix_home(self.server.db, query)
            elif parsed.path == "/extratos":
                page = render_statement_home(self.server.db, query)
            elif parsed.path == "/extratos/lote":
                page = render_statement_lot(self.server.db, query)
            elif parsed.path == "/extratos/movimento":
                page = render_statement_movement(self.server.db, query)
            elif parsed.path == "/extratos/regras":
                page = render_statement_rules(self.server.db, query)
            elif parsed.path == "/pix/regras":
                page = render_pix_rules(self.server.db, query)
            elif parsed.path == "/pix/lote":
                page = render_pix_lot(self.server.db, query)
            elif parsed.path == "/pix/movimento":
                page = render_pix_movement(self.server.db, query)
            elif parsed.path == "/contribuintes":
                page = render_contributors(self.server.db, query)
            elif parsed.path == "/contribuintes/novos-cadastros":
                page = render_new_people_associations(self.server.db, query)
            elif parsed.path == "/contribuintes.pdf":
                q = normalize_query(query.get("q", [""])[0])
                mode = normalize_query(query.get("mode", ["todos"])[0]) or "todos"
                section = normalize_query(query.get("section", ["contributors"])[0]) or "contributors"
                tags = [normalize_query(item).lower() for item in query.get("tag", []) if normalize_query(item)]
                inline_pdf = query.get("inline", ["0"])[0] == "1"
                competencia = normalize_query(query.get("competencia", [""])[0])
                date_start = normalize_query(query.get("date_start", [""])[0])
                date_end = normalize_query(query.get("date_end", [""])[0])
                person_query = normalize_query(query.get("person_query", [""])[0])
                if normalize_query(section).lower() == "periodo":
                    report_data = build_contributor_period_report_data(
                        self.server.db,
                        competencia=competencia,
                        date_start=date_start,
                        date_end=date_end,
                        person_query=person_query,
                    )
                    payload = build_contributor_period_report_payload(report_data)
                    download_name = contributor_period_download_name(
                        competencia=competencia,
                        date_start=date_start,
                        date_end=date_end,
                        person_query=person_query,
                    )
                    pdf_bytes = build_contributor_period_report_pdf(report_data)
                else:
                    report_data = build_contributors_dashboard_data(self.server.db, q=q, mode=mode, tags=tags)
                    payload = build_contributor_report_payload(report_data, section=section, tags=tags)
                    filter_label = contributor_report_filter_label(mode=mode, q=q, tags=tags)
                    download_name = contributor_report_download_name(section=section)
                    pdf_bytes = build_contributor_report_pdf(
                        str(payload["title"]),
                        str(payload["subtitle"]),
                        filter_label,
                        list(payload["groups"]),
                        str(payload["empty"]),
                    )
                self.send_bytes(
                    pdf_bytes,
                    "application/pdf",
                    download_name,
                    inline=inline_pdf,
                )
                return
            elif parsed.path == "/contribuinte":
                page = render_contributor(self.server.db, query)
            elif parsed.path == "/contribuicoes":
                page = render_contributions(self.server.db, query)
            elif parsed.path == "/extrato/contribuicoes":
                page = render_contribution_statement(self.server.db, query)
            elif parsed.path == "/extrato/contribuicoes.pdf":
                person_id = moneyless_int(query.get("person_id", ["0"])[0])
                year = normalize_query(query.get("year", [""])[0])
                date_start = normalize_query(query.get("date_start", [""])[0])
                date_end = normalize_query(query.get("date_end", [""])[0])
                competencia = normalize_query(query.get("competencia", [""])[0])
                type_ids = [moneyless_int(item) for item in query.get("tipo_id", []) if moneyless_int(item) > 0]
                statement = build_contribution_statement_data(
                    self.server.db,
                    person_id,
                    year=year,
                    date_start=date_start,
                    date_end=date_end,
                    competencia=competencia,
                    type_ids=type_ids,
                )
                person = statement["person"]
                if person is None:
                    self.send_error(HTTPStatus.NOT_FOUND, "Membro nao encontrado")
                    return
                payload = build_contribution_statement_pdf(
                    person,
                    statement["entries"],
                    float(statement["total_general"]),
                    moneyless_int(statement["competence_count"]),
                    str(statement["period_label"]),
                    str(statement["competencia"] or "Todas"),
                    str(statement["type_label"]),
                )
                self.send_bytes(
                    payload,
                    "application/pdf",
                    contribution_statement_download_name(
                        person_id,
                        year=year,
                        date_start=date_start,
                        date_end=date_end,
                        competencia=competencia,
                    ),
                )
                return
            elif parsed.path == "/contribuicao/nova":
                page = render_contribution_new(self.server.db, query)
            elif parsed.path == "/recibos":
                page = render_receipts(self.server.db, query)
            elif parsed.path == "/recibo/novo":
                page = render_receipt_new(self.server.db, query)
            elif parsed.path == "/recibo":
                page = render_receipt(self.server.db, query)
            elif parsed.path == "/foto/pessoa":
                person_id = moneyless_int(query.get("id", ["0"])[0])
                person = self.server.db.get_person(person_id)
                if person is None:
                    self.send_error(HTTPStatus.NOT_FOUND, "Pessoa nao encontrada")
                    return
                photo_path = find_member_photo(person_id, person["cpf"], person["nome"])
                if photo_path is None:
                    self.send_error(HTTPStatus.NOT_FOUND, "Foto nao encontrada")
                    return
                self.send_file(photo_path)
                return
            elif parsed.path == BRAND_LOGO_URL:
                if not brand_logo_available():
                    self.send_error(HTTPStatus.NOT_FOUND, "Logo nao encontrada")
                    return
                self.send_file(BRAND_LOGO_PATH)
                return
            elif parsed.path == "/pessoas":
                page = render_people(self.server.db, query)
            elif parsed.path == "/pessoas/importar":
                page = render_people_import(self.server.db, query)
            elif parsed.path == "/pessoas/importar/lote":
                page = render_people_import_lot(self.server.db, query)
            elif parsed.path == "/pessoa":
                page = render_person(self.server.db, query)
            elif parsed.path == "/pessoa/nova":
                page = render_person_new(self.server.db, query)
            elif parsed.path == "/pessoa/editar":
                page = render_person_edit(self.server.db, query)
            elif parsed.path == "/auditoria":
                page = render_audit(self.server.db, query)
            else:
                self.send_error(HTTPStatus.NOT_FOUND, "Pagina nao encontrada")
                return
            payload = page.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except Exception as exc:  # noqa: BLE001 - demo local should show a useful error
            payload = f"<pre>{h(type(exc).__name__)}: {h(exc)}</pre>".encode("utf-8")
            self.send_response(HTTPStatus.INTERNAL_SERVER_ERROR)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    def log_message(self, fmt: str, *args: object) -> None:
        return


class DemoServer(HTTPServer):
    db: PowerChurchDB


def server_port_from_args(argv: list[str]) -> int:
    for index, item in enumerate(argv):
        if item == "--port" and index + 1 < len(argv):
            return moneyless_int(argv[index + 1])
        if item.startswith("--port="):
            return moneyless_int(item.split("=", 1)[1])
    return moneyless_int(os.environ.get("POWER_CHURCH_PORT", "0"))


def server_host_from_args(argv: list[str]) -> str:
    for index, item in enumerate(argv):
        if item == "--host" and index + 1 < len(argv):
            return argv[index + 1].strip() or "127.0.0.1"
        if item.startswith("--host="):
            return item.split("=", 1)[1].strip() or "127.0.0.1"
    return os.environ.get("POWER_CHURCH_HOST", "127.0.0.1").strip() or "127.0.0.1"


def start_server(no_browser: bool = False, port: int = 0, host: str = "") -> DemoServer:
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Banco nao encontrado: {DB_PATH}")
    PHOTO_DIR.mkdir(parents=True, exist_ok=True)
    BRAND_DIR.mkdir(parents=True, exist_ok=True)
    PIX_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    STATEMENT_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    db = PowerChurchDB(DB_PATH)
    server_host = host.strip() or server_host_from_args(sys.argv)
    server = DemoServer((server_host, max(0, moneyless_int(port))), Handler)
    server.db = db
    bound_host, bound_port = server.server_address
    display_host = "127.0.0.1" if bound_host in {"0.0.0.0", "::"} else bound_host
    url = f"http://{display_host}:{bound_port}/"
    print(f"{APP_TITLE} iniciado em {url}", flush=True)
    if not no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    return server


def main() -> None:
    no_browser = "--no-browser" in sys.argv
    server = start_server(
        no_browser=no_browser,
        port=server_port_from_args(sys.argv),
        host=server_host_from_args(sys.argv),
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.db.close()
        server.server_close()


if __name__ == "__main__":
    main()

from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Mapping

from .banking import statement_layout_is_santander
from .bank_parsers import (
    bradesco_extract_source_name,
    bradesco_source_name_is_noise,
    statement_display_label,
)
from .normalization import cleaned_document_token, moneyless_int, normalize_match_name, normalize_query, pix_code_from_amount, santander_document_type
from .signatures import statement_global_signature


@dataclass(frozen=True)
class StatementEntryPlan:
    page_number: int
    order_in_file: int
    received_on: str
    competencia: str
    competencia_ordem: int
    amount: float
    cent_code: str
    movement_kind: str
    receiving_code: str
    bank_document: str
    document_type: str
    prefix: str
    source_name: str
    source_name_normalized: str
    origin_label: str
    detail_text: str
    raw_text: str
    signature_global: str
    fingerprint: str


def slugify_filename_text(value: object, fallback: str = "sem_nome", limit: int = 48) -> str:
    raw = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    pieces: list[str] = []
    previous_separator = False
    for char in raw.lower():
        if char.isalnum():
            pieces.append(char)
            previous_separator = False
        elif not previous_separator:
            pieces.append("_")
            previous_separator = True
    slug = "".join(pieces).strip("_")
    if not slug:
        slug = fallback
    return slug[:limit].strip("_") or fallback


def uploaded_file_hash(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def statement_duplicate_scope(layout_code: object) -> str:
    return "santander_family" if statement_layout_is_santander(layout_code) else "exact_layout"


def statement_upload_target_name(filename: str, file_hash: str, today_iso: str | None = None) -> str:
    stamp = today_iso or date.today().isoformat()
    stem = slugify_filename_text(Path(filename).stem, fallback="extrato")
    return f"{stamp}_{stem}_{file_hash[:10]}.pdf"


def statement_force_person_review(layout_code: object, document_value: object, suggested_person_id: int, has_rule: bool) -> bool:
    return bool(statement_layout_is_santander(layout_code) and normalize_query(document_value) and not suggested_person_id and not has_rule)


def statement_operational_bank_family(layout_code: object, bank_name: object = "") -> str:
    layout = normalize_query(layout_code).upper()
    bank = normalize_match_name(bank_name)
    if layout in {"SICOOB_RECEBIMENTOS", "SICOOB_CONTA_CORRENTE"} or "SICOOB" in bank:
        return "sicoob"
    if statement_layout_is_santander(layout) or "SANTANDER" in bank:
        return "santander"
    if layout == "BRADESCO_EXTRATO" or "BRADESCO" in bank:
        return "bradesco"
    return layout.lower() or bank.lower() or "extrato"


def statement_operational_identity(layout_code: object, source_name: object, document_value: object) -> tuple[str, str]:
    layout = normalize_query(layout_code).upper()
    document = cleaned_document_token(document_value)
    name = normalize_match_name(source_name)
    if statement_layout_is_santander(layout):
        if document:
            return "documento", document
        if name:
            return "nome", name
        return "sem_identidade", ""
    if name:
        return "nome", name
    if document:
        return "documento", document
    return "sem_identidade", ""


def statement_operational_duplicate_key(
    layout_code: object,
    bank_name: object,
    received_on: object,
    amount: object,
    source_name: object,
    document_value: object,
) -> tuple[str, str, int, str, str]:
    identity_kind, identity_value = statement_operational_identity(layout_code, source_name, document_value)
    amount_cents = int(round(float(amount or 0) * 100))
    return (
        statement_operational_bank_family(layout_code, bank_name),
        normalize_query(received_on),
        amount_cents,
        identity_kind,
        identity_value,
    )


def statement_entry_fingerprint(
    received_on: object,
    amount: object,
    source_name: object,
    document_value: object,
    page_number: object,
    order_in_file: object,
    movement_kind: object,
) -> str:
    fingerprint_source = "|".join(
        [
            str(received_on),
            f"{float(amount):.2f}",
            normalize_match_name(source_name),
            normalize_query(document_value),
            str(page_number),
            str(order_in_file),
            str(movement_kind),
        ]
    )
    return hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest()


def _get(row: Mapping[str, object], key: str, default: object = "") -> object:
    try:
        return row[key]
    except (KeyError, IndexError):
        return default


def statement_entry_plan(layout_code: object, entry: Mapping[str, object]) -> StatementEntryPlan:
    layout = normalize_query(layout_code).upper()
    source_name = normalize_query(entry.get("source_name"))
    document_value = normalize_query(entry.get("bank_document"))
    document_type = normalize_query(entry.get("document_type")).lower()
    if statement_layout_is_santander(layout) and document_value and not document_type:
        document_type = santander_document_type(document_value)
    received_on = str(entry["received_on"])
    amount = float(entry["amount"])
    movement_kind = str(entry["movement_kind"])
    raw_text = str(entry["raw_text"])
    source_name_normalized = normalize_query(entry.get("source_name_normalized")) or normalize_match_name(source_name)
    signature_global = statement_global_signature(
        layout,
        received_on,
        amount,
        source_name_normalized,
        movement_kind,
        document_value,
        raw_text,
    )
    return StatementEntryPlan(
        page_number=moneyless_int(entry.get("page_number")) or 1,
        order_in_file=moneyless_int(entry.get("order_in_file")),
        received_on=received_on,
        competencia=str(entry["competencia"]),
        competencia_ordem=moneyless_int(entry["competencia_ordem"]),
        amount=amount,
        cent_code=pix_code_from_amount(amount),
        movement_kind=movement_kind,
        receiving_code=str(entry["receiving_code"]),
        bank_document=document_value,
        document_type=document_type,
        prefix=str(entry.get("prefix") or ""),
        source_name=source_name,
        source_name_normalized=source_name_normalized,
        origin_label=str(entry.get("origin_label") or ""),
        detail_text=normalize_query(entry.get("detail_text")),
        raw_text=raw_text,
        signature_global=signature_global,
        fingerprint=statement_entry_fingerprint(
            received_on,
            amount,
            source_name,
            document_value,
            moneyless_int(entry.get("page_number")) or 1,
            moneyless_int(entry.get("order_in_file")),
            movement_kind,
        ),
    )


def statement_reprocess_plan(
    layout_code: object,
    row: Mapping[str, object],
    entry: Mapping[str, object] | None = None,
) -> StatementEntryPlan:
    layout = normalize_query(layout_code).upper() or "BRADESCO_EXTRATO"
    if entry is not None:
        return statement_entry_plan(layout, entry)

    prefix = normalize_query(_get(row, "prefixo_historico"))
    raw_text = normalize_query(_get(row, "raw_text"))
    detail_text = raw_text
    if prefix and raw_text.startswith(prefix):
        detail_text = raw_text[len(prefix) :].lstrip(" |")
    source_name = normalize_query(_get(row, "nome_origem"))
    if not source_name and layout == "BRADESCO_EXTRATO":
        source_name, _explicit_date, detail_text = bradesco_extract_source_name(prefix, [detail_text] if detail_text else [])
    elif layout == "BRADESCO_EXTRATO" and bradesco_source_name_is_noise(source_name):
        source_name = ""

    document_value = normalize_query(_get(row, "bank_document"))
    document_type = santander_document_type(document_value) if statement_layout_is_santander(layout) and document_value else ""
    received_on = str(_get(row, "data_movimento"))
    amount = float(_get(row, "valor") or 0)
    movement_kind = str(_get(row, "movement_kind") or "")
    order_in_file = moneyless_int(_get(row, "ordem_no_lote"))
    page_number = moneyless_int(_get(row, "pagina")) or 1
    source_name_normalized = normalize_match_name(source_name)
    origin_label = statement_display_label(layout, prefix, source_name, detail_text)
    signature_global = statement_global_signature(
        layout,
        received_on,
        amount,
        source_name_normalized,
        movement_kind,
        document_value,
        raw_text,
    )
    return StatementEntryPlan(
        page_number=page_number,
        order_in_file=order_in_file,
        received_on=received_on,
        competencia=str(_get(row, "competencia")),
        competencia_ordem=moneyless_int(_get(row, "competencia_ordem")),
        amount=amount,
        cent_code=pix_code_from_amount(amount),
        movement_kind=movement_kind,
        receiving_code=str(_get(row, "receiving_code") or ""),
        bank_document=document_value,
        document_type=document_type,
        prefix=prefix,
        source_name=source_name,
        source_name_normalized=source_name_normalized,
        origin_label=origin_label,
        detail_text=detail_text,
        raw_text=raw_text,
        signature_global=signature_global,
        fingerprint=statement_entry_fingerprint(
            received_on,
            amount,
            source_name,
            document_value,
            page_number,
            order_in_file,
            movement_kind,
        ),
    )

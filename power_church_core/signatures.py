from __future__ import annotations

import hashlib
import re

from .normalization import cleaned_document_token, normalize_query


def signature_component(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).upper()


def pix_global_signature(
    received_on: object,
    amount: object,
    donor_name_normalized: object,
    document_mask: object,
    document_type: object,
    raw_text: object,
) -> str:
    payload = "|".join(
        [
            str(received_on or ""),
            f"{float(amount or 0):.2f}",
            signature_component(donor_name_normalized),
            cleaned_document_token(document_mask),
            normalize_query(document_type),
            signature_component(raw_text),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def statement_global_signature(
    bank_key: object,
    received_on: object,
    amount: object,
    donor_name_normalized: object,
    movement_kind: object,
    bank_document: object,
    raw_text: object,
) -> str:
    payload = "|".join(
        [
            normalize_query(bank_key),
            str(received_on or ""),
            f"{float(amount or 0):.2f}",
            signature_component(donor_name_normalized),
            normalize_query(movement_kind),
            normalize_query(bank_document),
            signature_component(raw_text),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

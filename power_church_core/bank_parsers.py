from __future__ import annotations

import calendar
import hashlib
import re
from datetime import date
from pathlib import Path
from typing import Mapping

from .banking import (
    santander_document_display,
    santander_identity_source_label,
    statement_layout_is_santander,
    statement_layout_label,
)
from .formatting import competencia_from_date, parse_money
from .normalization import cleaned_document_token, moneyless_int, normalize_match_name, normalize_query, santander_document_type
from .pdf_text import extract_pdf_line_selections, extract_pdf_pages


BRADESCO_CREDIT_PREFIXES = {
    "Transferencia Pix": {
        "movement_kind": "pix",
        "receiving_code": "PIX",
        "expected_details": 1,
        "allow_without_name": False,
    },
    "Ted-transf Elet Dispon": {
        "movement_kind": "ted",
        "receiving_code": "TRANSFERENCIA",
        "expected_details": 1,
        "allow_without_name": False,
    },
    "Deposit Transfer BDN": {
        "movement_kind": "deposit_transfer_bdn",
        "receiving_code": "TRANSFERENCIA",
        "expected_details": 1,
        "allow_without_name": False,
    },
    "Transf C/c BDN": {
        "movement_kind": "transferencia_bdn",
        "receiving_code": "TRANSFERENCIA",
        "expected_details": 1,
        "allow_without_name": False,
    },
    "Transf.autoriz.entre C/c": {
        "movement_kind": "transferencia_conta",
        "receiving_code": "TRANSFERENCIA",
        "expected_details": 1,
        "allow_without_name": False,
    },
    "Transf Autoriz Entre Ags": {
        "movement_kind": "transferencia_agencias",
        "receiving_code": "TRANSFERENCIA",
        "expected_details": 1,
        "allow_without_name": False,
    },
    "Transf.poup Para C/c": {
        "movement_kind": "transferencia_poupanca",
        "receiving_code": "TRANSFERENCIA",
        "expected_details": 1,
        "allow_without_name": False,
    },
    "Dep Dinheiro Atm": {
        "movement_kind": "deposito_dinheiro",
        "receiving_code": "DINHEIRO",
        "expected_details": 1,
        "allow_without_name": True,
    },
    "Dep Cheque Atm": {
        "movement_kind": "deposito_cheque",
        "receiving_code": "CHEQUE",
        "expected_details": 1,
        "allow_without_name": True,
    },
    "Deposito C/corrente-bdn": {
        "movement_kind": "deposito_dinheiro",
        "receiving_code": "DINHEIRO",
        "expected_details": 1,
        "allow_without_name": True,
    },
}
SICOOB_RECEIVING_PREFIXES = {
    "PIX RECEB.OUTRA IF": {
        "movement_kind": "pix",
        "receiving_code": "PIX",
        "allow_without_name": False,
    },
    "PIX RECEBIDO - OUTRA IF": {
        "movement_kind": "pix",
        "receiving_code": "PIX",
        "allow_without_name": False,
    },
    "CRED.TR.CT.INTERCRE": {
        "movement_kind": "transferencia_intercre",
        "receiving_code": "TRANSFERENCIA",
        "allow_without_name": False,
    },
    "CRÉD.TED-STR": {
        "movement_kind": "ted",
        "receiving_code": "TRANSFERENCIA",
        "allow_without_name": False,
    },
    "TRANSF.RECEB-PIX SI": {
        "movement_kind": "transferencia_pix_sicoob",
        "receiving_code": "TRANSFERENCIA",
        "allow_without_name": False,
    },
    "TRANSF.RECEBIDA - PIX SICOOB": {
        "movement_kind": "transferencia_pix_sicoob",
        "receiving_code": "TRANSFERENCIA",
        "allow_without_name": False,
    },
    "DEP.DINHEIRO INTERC": {
        "movement_kind": "deposito_dinheiro",
        "receiving_code": "DINHEIRO",
        "allow_without_name": True,
    },
    "DEP.CHEQUE BLOQ.1D": {
        "movement_kind": "deposito_cheque",
        "receiving_code": "CHEQUE",
        "allow_without_name": True,
    },
    "LIBER.DEPÓSITO BLOQ": {
        "movement_kind": "liberacao_deposito",
        "receiving_code": "DINHEIRO",
        "allow_without_name": True,
    },
    "EST.PIX EMIT.OUT.IF": {
        "movement_kind": "estorno_pix",
        "receiving_code": "AJUSTE",
        "allow_without_name": True,
    },
}
BRADESCO_KNOWN_PREFIXES = tuple(
    sorted(
        {
            *BRADESCO_CREDIT_PREFIXES.keys(),
            "Pagto Eletron Cobranca",
            "Pagto Eletronico Tributo",
            "Debito Automatico",
            "Aplic.invest Facil",
            "Resgate Invest Facil",
            "Rentab.invest Facilcred*",
            "Conta de Luz",
            "Conta de Agua",
            "Conta de Agua e Esgoto",
            "Conta de Telefone",
            "Tarifa Transferen Valor",
            "Tarifa Bancaria Max Empresarial 3",
            "Gastos Cartao de Credito",
            "Estorno de Deposito-bdn",
        },
        key=len,
        reverse=True,
    )
)
MONTH_ABBR_PT = {
    "JAN": 1,
    "FEV": 2,
    "MAR": 3,
    "ABR": 4,
    "MAI": 5,
    "JUN": 6,
    "JUL": 7,
    "AGO": 8,
    "SET": 9,
    "OUT": 10,
    "NOV": 11,
    "DEZ": 12,
}
SANTANDER_MONTHS_PT = {
    "JANEIRO": 1,
    "FEVEREIRO": 2,
    "MARCO": 3,
    "ABRIL": 4,
    "MAIO": 5,
    "JUNHO": 6,
    "JULHO": 7,
    "AGOSTO": 8,
    "SETEMBRO": 9,
    "OUTUBRO": 10,
    "NOVEMBRO": 11,
    "DEZEMBRO": 12,
}


def br_to_iso(value: str) -> str:
    day, month, year = value.split("/")
    return f"{year}-{month}-{day}"


def infer_pdf_statement_date(month_token: str, day: int, period_start_iso: str, period_end_iso: str) -> str:
    month = MONTH_ABBR_PT.get(str(month_token or "").upper())
    if month is None:
        raise ValueError("Mes do extrato PIX nao reconhecido.")
    period_start = date.fromisoformat(period_start_iso)
    period_end = date.fromisoformat(period_end_iso)
    candidate_years = {
        period_start.year - 1,
        period_start.year,
        period_end.year,
        period_end.year + 1,
    }
    candidates: list[date] = []
    for year in sorted(candidate_years):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        if period_start <= candidate <= period_end:
            return candidate.isoformat()
        candidates.append(candidate)
    if not candidates:
        raise ValueError("Nao foi possivel inferir a data real do movimento PIX.")
    candidates.sort(key=lambda item: abs((item - period_end).days))
    return candidates[0].isoformat()


def infer_statement_date_from_br_token(day_month: str, period_start_iso: str, period_end_iso: str) -> str:
    token = normalize_query(day_month)
    if re.fullmatch(r"\d{2}/\d{2}/\d{4}", token):
        day, month, year = [moneyless_int(part) for part in token.split("/")]
        try:
            return date(year, month, day).isoformat()
        except ValueError as exc:
            raise ValueError("Data do extrato bancario em formato inesperado.") from exc
    if not re.fullmatch(r"\d{2}/\d{2}", token):
        raise ValueError("Data do extrato bancario em formato inesperado.")
    day, month = [moneyless_int(part) for part in token.split("/")]
    period_start = date.fromisoformat(period_start_iso)
    period_end = date.fromisoformat(period_end_iso)
    candidate_years = {
        period_start.year - 1,
        period_start.year,
        period_end.year,
        period_end.year + 1,
    }
    candidates: list[date] = []
    for year in sorted(candidate_years):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        if period_start <= candidate <= period_end:
            candidates.append(candidate)
    if not candidates:
        for year in sorted(candidate_years):
            try:
                candidates.append(date(year, month, day))
            except ValueError:
                continue
    if not candidates:
        raise ValueError("Nao foi possivel inferir a data do extrato bancario.")
    return candidates[0].isoformat()


def sicoob_receiving_kind_metadata(history: object) -> dict[str, object]:
    return dict(SICOOB_RECEIVING_PREFIXES.get(normalize_query(history), {}))


def sicoob_receiving_kind_metadata_norm(history: object) -> dict[str, object]:
    history_norm = normalize_match_name(history)
    for key, value in SICOOB_RECEIVING_PREFIXES.items():
        if normalize_match_name(key) == history_norm:
            return dict(value)
    return {}


def bradesco_match_prefix(value: object) -> str:
    text = normalize_query(value)
    text_norm = normalize_match_name(text)
    for prefix in BRADESCO_KNOWN_PREFIXES:
        if text.startswith(prefix) or (text_norm and text_norm.startswith(normalize_match_name(prefix))):
            return prefix
    return ""


def bradesco_source_name_is_noise(value: object) -> bool:
    text = normalize_query(value)
    if not text:
        return True
    if re.fullmatch(r"[.\-_/ ]+", text):
        return True
    norm = normalize_match_name(text)
    if not norm:
        return True
    if norm in {"SALDO", "SALDO ANTERIOR", "ANTERIOR"}:
        return True
    if "VENCIMENTO" in norm or "TAXA" in norm:
        return True
    if re.search(r"\ba\.?m\.?\b", text, flags=re.IGNORECASE) or re.search(r"\ba\.?a\.?\b", text, flags=re.IGNORECASE):
        return True
    if re.fullmatch(r"AG\d{5}MAQ\d+SEQ\d+", norm):
        return True
    return False


def normalize_contributor_source_name(name: object, source: object = "") -> str:
    text = normalize_query(name)
    source_text = normalize_query(source).lower()
    if source_text == "extrato_bradesco":
        text = re.sub(r"^Remet\.\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"^Rem:\s*", "", text, flags=re.IGNORECASE)
    return normalize_query(text)


def contributor_name_is_noise(name: object, source: object = "") -> bool:
    text = normalize_contributor_source_name(name, source)
    source_text = normalize_query(source).lower()
    if not text:
        return True
    if not normalize_match_name(text):
        return True
    if source_text == "extrato_bradesco" and bradesco_source_name_is_noise(text):
        return True
    return False


def sicoob_detail_line_is_noise(value: object) -> bool:
    text = normalize_query(value)
    norm = normalize_match_name(text)
    if norm in {
        "DATA DOCUMENTO HISTORICO VALOR",
    }:
        return True
    if text.lower().startswith(("http://", "https://", "www.")):
        return True
    if "ib.sicoob.com.br" in text.lower():
        return True
    if re.fullmatch(r"\d{1,3}/\d{1,3}", text):
        return True
    return False


def bradesco_extract_source_name(prefix: str, detail_lines: list[str]) -> tuple[str, str, str]:
    details = [normalize_query(item) for item in detail_lines if normalize_query(item)]
    detail_text = " ".join(details).strip()
    explicit_date = ""
    source_name = ""
    if prefix == "Transferencia Pix":
        joined = detail_text
        match = re.search(r"Rem:\s*(.+?)\s+(\d{2}/\d{2})\b", joined, flags=re.IGNORECASE)
        if match:
            source_name = normalize_query(match.group(1))
            explicit_date = normalize_query(match.group(2))
        else:
            source_name = normalize_query(re.sub(r"^Rem:\s*", "", joined, count=1, flags=re.IGNORECASE))
    elif prefix == "Ted-transf Elet Dispon":
        source_name = normalize_query(re.sub(r"^Remet\.\s*", "", detail_text, count=1, flags=re.IGNORECASE))
    elif prefix in {
        "Deposit Transfer BDN",
        "Transf C/c BDN",
        "Transf.autoriz.entre C/c",
        "Transf Autoriz Entre Ags",
        "Transf.poup Para C/c",
    }:
        source_name = detail_text
    elif prefix in {"Dep Dinheiro Atm", "Dep Cheque Atm", "Deposito C/corrente-bdn"}:
        source_name = ""
    else:
        source_name = detail_text
    if bradesco_source_name_is_noise(source_name):
        source_name = ""
    return source_name, explicit_date, detail_text


def bradesco_credit_kind_metadata(prefix: str) -> dict[str, object]:
    return dict(BRADESCO_CREDIT_PREFIXES.get(prefix, {}))


def bradesco_credit_display_label(prefix: str, source_name: str, detail_text: str) -> str:
    prefix_label = normalize_query(prefix) or "Credito bancario"
    if normalize_query(source_name):
        return normalize_query(source_name)
    if prefix in {"Dep Dinheiro Atm", "Dep Cheque Atm", "Deposito C/corrente-bdn"}:
        return prefix_label
    if normalize_query(detail_text) and not bradesco_source_name_is_noise(detail_text):
        return normalize_query(detail_text)
    return prefix_label


def bradesco_period_from_text(full_text: str) -> tuple[str, str]:
    range_match = re.search(r"(\d{2}/\d{2}/\d{4})\s+a\s+(\d{2}/\d{2}/\d{4})", full_text)
    if range_match:
        return range_match.groups()
    range_match = re.search(
        r"Entre\s+(\d{2}/\d{2}/\d{4})\s+e\s+(\d{2}/\d{2}/\d{4})",
        full_text,
        flags=re.IGNORECASE,
    )
    if range_match:
        return range_match.groups()
    range_match = re.search(
        r"Extrato\s+de:.*?(\d{2}/\d{2}/\d{4})\s+(\d{2}/\d{2}/\d{4})",
        full_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if range_match:
        return range_match.groups()
    raise ValueError("Nao foi possivel identificar o periodo do extrato Bradesco.")


def bradesco_detail_amount_parts(detail_line: str) -> tuple[str, str, str]:
    text = normalize_query(detail_line)
    amounts = re.findall(r"(?<![-\d])\d{1,3}(?:\.\d{3})*,\d{2}", text)
    if not amounts:
        return "", "", ""
    credit_text = amounts[0]
    before_amount = text.split(credit_text, 1)[0].strip()
    doc_match = re.search(r"\b(\d{5,8})\s*$", before_amount)
    document = doc_match.group(1) if doc_match else ""
    detail_text = before_amount[: doc_match.start()].strip() if doc_match else before_amount
    return detail_text, document, credit_text


def parse_bradesco_statement_text_entries(
    pages: list[str],
    period_start: str,
    period_end: str,
    period_start_br: str,
) -> list[dict[str, object]]:
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
            line_norm = normalize_match_name(line)
            if any(token in line_norm for token in {"EXTRATO MENSAL", "NOME DO USUARIO", "DATA DA OPERACAO"}):
                continue
            if "PRIMEIRA IGREJA" in line_norm and "CNPJ" in line_norm:
                continue
            date_match = re.match(r"^(\d{2}/\d{2}(?:/\d{4})?)\s+(.+)$", line)
            line_date = ""
            body = line
            if date_match:
                line_date = normalize_query(date_match.group(1))
                body = normalize_query(date_match.group(2))
                carry_date_token = line_date
                if pending is not None:
                    pending["date_token"] = line_date
            prefix = bradesco_match_prefix(body)
            if prefix:
                if pending is not None and pending.get("amount_line"):
                    finalize(str(pending.get("amount_line") or ""))
                pending = {
                    "prefix": prefix,
                    "date_token": line_date or carry_date_token,
                    "page_number": page_number,
                }
                remainder = normalize_query(body[len(prefix) :])
                if remainder and re.search(r"\d{1,3}(?:\.\d{3})*,\d{2}", remainder):
                    finalize(remainder)
                continue
            if pending is not None and re.search(r"\d{1,3}(?:\.\d{3})*,\d{2}", body):
                detail_text, document, credit_text = bradesco_detail_amount_parts(body)
                if credit_text and document and not detail_text:
                    pending["amount_line"] = body
                    continue
                finalize(body)
                continue
            if pending is not None and pending.get("amount_line") and body:
                finalize(f"{body} {pending.get('amount_line')}")
    return entries


def parse_bradesco_statement_pdf(pdf_path: Path) -> dict[str, object]:
    pages = extract_pdf_pages(pdf_path)
    page_lines = extract_pdf_line_selections(pdf_path)
    full_text = "\n".join(pages)
    period_start_br, period_end_br = bradesco_period_from_text(full_text)
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
                if float(row.get("x") or 0) < 70 and re.fullmatch(r"\d{2}/\d{2}", normalize_query(row.get("text")))
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
    text_entries = parse_bradesco_statement_text_entries(pages, period_start, period_end, period_start_br)
    coordinate_valid = [entry for entry in entries if not statement_should_skip_bradesco_entry(entry)]
    text_valid = [entry for entry in text_entries if not statement_should_skip_bradesco_entry(entry)]
    if not entries or len(text_valid) > len(coordinate_valid):
        entries = text_entries
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


def sicoob_receiving_identity_document(detail_lines: list[str]) -> tuple[str, str]:
    details = [normalize_query(item) for item in detail_lines if normalize_query(item)]
    joined_details = normalize_query(" ".join(details))
    if joined_details:
        details.append(joined_details)
    patterns = [
        (re.compile(r"[*0-9]{3}\.[*0-9]{3}\.[*0-9]{3}-[*0-9]{2}"), "cpf"),
        (re.compile(r"\d{3}\.\d{3}\.\d{3}-\d{2}"), "cpf"),
        (re.compile(r"[*0-9]{2}\.[*0-9]{3}\.[*0-9]{3}\s?[*0-9]{4}-[*0-9]{2}"), "cnpj"),
        (re.compile(r"\d{2}\.\d{3}\.\d{3}\s?\d{4}-\d{2}"), "cnpj"),
        (re.compile(r"CPF:\s*\d{3}\.\d{3}\.\d{3}-\d{2}", re.IGNORECASE), "cpf"),
    ]
    for line in details:
        for pattern, doc_type in patterns:
            match = pattern.search(line)
            if match:
                value = normalize_query(match.group(0).replace("CPF:", "").strip())
                return value, doc_type
    return "", ""


def sicoob_receiving_bank_reference(detail_lines: list[str]) -> str:
    details = [normalize_query(item) for item in detail_lines if normalize_query(item)]
    for line in details:
        doc_line = normalize_query(line)
        if not doc_line.startswith("DOC.:"):
            continue
        doc_value = normalize_query(doc_line.replace("DOC.:", "", 1))
        if doc_value and doc_value.upper() != "PIX":
            return doc_value
    return ""


def sicoob_receiving_extract_source_name(
    history: str,
    detail_lines: list[str],
    identity_document: str = "",
) -> tuple[str, str]:
    details = [normalize_query(item) for item in detail_lines if normalize_query(item)]
    detail_text = " ".join(details).strip()
    source_name = ""
    if history in {"PIX RECEB.OUTRA IF", "PIX RECEBIDO - OUTRA IF"}:
        for line in details:
            line_norm = normalize_query(line)
            if sicoob_detail_line_is_noise(line_norm):
                continue
            if line_norm in {"Recebimento Pix", "DOC.: Pix", "Transferencia Pix", "Transferência Pix", identity_document}:
                continue
            if line_norm.startswith("DOC.:") or line_norm.startswith("REM.:") or line_norm.startswith("CPF:") or line_norm.startswith("ENVELOPE:"):
                continue
            if line_norm.lower().startswith("recebimento pix "):
                candidate = normalize_query(re.sub(r"^Recebimento\s+Pix\s+", "", line_norm, flags=re.IGNORECASE))
                if identity_document and identity_document in candidate:
                    candidate = candidate.split(identity_document, 1)[0].strip()
                else:
                    document_match = re.search(
                        r"[*0-9]{3}\.[*0-9]{3}\.[*0-9]{3}-[*0-9]{2}|[*0-9]{2}\.[*0-9]{3}\.[*0-9]{3}\s?[*0-9]{4}-[*0-9]{2}",
                        candidate,
                    )
                    if document_match:
                        candidate = candidate[: document_match.start()].strip()
                source_name = normalize_query(candidate)
                break
            source_name = line_norm
            break
    elif history in {"CRED.TR.CT.INTERCRE", "TRANSF.RECEB-PIX SI", "TRANSF.RECEBIDA - PIX SICOOB"}:
        for line in details:
            if normalize_query(line).startswith("REM.:"):
                source_name = normalize_query(line).replace("REM.:", "", 1).strip()
                break
        if not source_name:
            for line in details:
                line_norm = normalize_query(line)
                if sicoob_detail_line_is_noise(line_norm):
                    continue
                if line_norm in {"Transferencia Pix", "Transferência Pix", identity_document}:
                    continue
                if line_norm.startswith("DOC.:") or line_norm.startswith("CPF:") or line_norm.startswith("ENVELOPE:"):
                    continue
                source_name = line_norm
                break
        if source_name:
            source_name = re.split(r"\s+Transfer[eê]ncia\s+Pix\s+", source_name, maxsplit=1, flags=re.IGNORECASE)[0].strip()
            if identity_document and identity_document in source_name:
                source_name = source_name.split(identity_document, 1)[0].strip()
            source_name = re.sub(
                r"\b\d{2}\.\d{3}\.\d{3}\s?\d{4}-\d{2}\b|\b\d{3}\.\d{3}\.\d{3}-\d{2}\b",
                "",
                source_name,
            ).strip()
    elif history == "CRÉD.TED-STR":
        for line in details:
            line_norm = normalize_query(line)
            if sicoob_detail_line_is_noise(line_norm):
                continue
            if line_norm.startswith("CODIGO TED:") or line_norm.startswith("DOC.:") or line_norm == "00000000000000":
                continue
            if line_norm == identity_document:
                continue
            source_name = line_norm
            break
        if source_name:
            source_name = re.sub(r"\bCODIGO\s+TED:.*$", "", source_name, flags=re.IGNORECASE).strip()
            if identity_document and identity_document in source_name:
                source_name = source_name.split(identity_document, 1)[0].strip()
            source_name = re.sub(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b", "", source_name).strip()
    if source_name and contributor_name_is_noise(source_name, "extrato_sicoob"):
        source_name = ""
    return source_name, detail_text


def sicoob_receiving_display_label(history: str, source_name: str, detail_text: str) -> str:
    if normalize_query(source_name):
        return normalize_query(source_name)
    if normalize_query(detail_text):
        return normalize_query(detail_text)
    return normalize_query(history) or "Credito Sicoob"


def statement_display_label(layout_code: object, prefix: str, source_name: str, detail_text: str) -> str:
    normalized_layout = normalize_query(layout_code).upper()
    if normalized_layout in {"SICOOB_RECEBIMENTOS", "SICOOB_CONTA_CORRENTE"}:
        return sicoob_receiving_display_label(prefix, source_name, detail_text)
    if statement_layout_is_santander(normalized_layout):
        if normalize_query(source_name):
            return normalize_query(source_name)
        detail = normalize_query(detail_text)
        match = re.search(r"\b(CPF|CNPJ|DOCUMENTO)\s+(\d{5,14})\b", detail, flags=re.IGNORECASE)
        if match:
            return santander_identity_source_label(match.group(2), match.group(1).lower())
        return detail or "PIX Santander"
    return bradesco_credit_display_label(prefix, source_name, detail_text)


def parse_sicoob_receipts_pdf(
    pdf_path: Path,
    *,
    include_current_account_lines: bool = False,
    statement_kind: str = "extrato_sicoob_recebimentos",
    layout_code: str = "SICOOB_RECEBIMENTOS",
) -> dict[str, object]:
    pages = extract_pdf_pages(pdf_path)
    full_text = "\n".join(pages)
    range_match = re.search(r"PER[ÍI]ODO:\s*(\d{2}/\d{2}/\d{4})\s*-\s*(\d{2}/\d{2}/\d{4})", full_text, flags=re.IGNORECASE)
    if not range_match:
        raise ValueError("Nao foi possivel identificar o periodo do extrato Sicoob.")
    period_start_br, period_end_br = range_match.groups()
    period_start = br_to_iso(period_start_br)
    period_end = br_to_iso(period_end_br)
    main_re = re.compile(r"^(\d{2}/\d{2})\s+(.+?)\s+([\d.]+,\d{2}[CD\*])$")
    value_only_re = re.compile(r"^R\s*\$\s*([\d.]+,\d{2}[CD\*])$", flags=re.IGNORECASE)
    trailing_value_re = re.compile(r"R\s*\$\s*([\d.]+,\d{2}[CD\*])\s*$", flags=re.IGNORECASE)
    current_account_prefix_re = re.compile(r"^(\d{2}/\d{2})\s+((?:Pix|PIX|TED|DOC|Transf\.?|[\d.]{3,}))\s+(.+)$", flags=re.IGNORECASE)
    excluded = {"SALDO DO DIA", "SALDO ANTERIOR", "SALDO BLOQ.ANTERIOR"}
    current_account_histories = {
        "PIX RECEBIDO - OUTRA IF",
        "CRED.TRANSF.CONTAS INTERCREDIS",
        "CRÉD.TED-STR",
        "TRANSF.RECEBIDA - PIX SICOOB",
        "CRÉDITO RESGATE FUNDOS DE INVESTIMENTO",
    }
    blocks: list[dict[str, object]] = []
    pending_value_texts: list[str] = []
    current: dict[str, object] | None = None

    def start_block(page_number: int, date_token: str, history: str, *, value_text: str = "", initial_detail: str = "") -> dict[str, object]:
        block = {
            "page_number": page_number,
            "date_token": normalize_query(date_token),
            "history": normalize_query(history),
            "value_text": normalize_query(value_text),
            "details": [],
        }
        if normalize_query(initial_detail):
            block["details"].append(normalize_query(initial_detail))
        return block

    def first_unvalued_pix_block(page_blocks: list[dict[str, object]], current_block: dict[str, object] | None) -> dict[str, object] | None:
        candidates = [*page_blocks]
        if current_block is not None:
            candidates.append(current_block)
        for block in candidates:
            if (
                normalize_query(block.get("history")) == "PIX RECEBIDO - OUTRA IF"
                and not normalize_query(block.get("value_text"))
            ):
                return block
        return None

    current_date_token = ""
    carry_block: dict[str, object] | None = None
    for page_number, page in enumerate(pages, start=1):
        page_blocks: list[dict[str, object]] = []
        page_values: list[str] = []
        current = carry_block
        carry_block = None
        for raw_line in page.splitlines():
            line = normalize_query(raw_line)
            if not line or line == "Data Documento Histórico Valor":
                continue
            value_only_match = value_only_re.match(line) if include_current_account_lines else None
            if value_only_match:
                page_values.append(normalize_query(value_only_match.group(1)))
                continue
            match = None if include_current_account_lines else main_re.match(line)
            if not include_current_account_lines:
                if match:
                    if current is not None:
                        page_blocks.append(current)
                    current = start_block(
                        page_number,
                        normalize_query(match.group(1)),
                        normalize_query(match.group(2)),
                        value_text=normalize_query(match.group(3)),
                    )
                    continue
                if current is not None and line:
                    current["details"].append(line)
                continue

            prefix_match = current_account_prefix_re.match(line)
            if prefix_match:
                token_matches = list(re.finditer(r"(\d{2}/\d{2})\s+(?:Pix|PIX|TED|DOC|Transf\.?|[\d.]{3,})", line, flags=re.IGNORECASE))
                current_date_token = normalize_query(prefix_match.group(1))
                document_token = normalize_query(prefix_match.group(2))
                tail = normalize_query(line[token_matches[-1].end() :]) if token_matches else normalize_query(prefix_match.group(3))
                inline_value = ""
                inline_value_match = trailing_value_re.search(tail)
                if inline_value_match:
                    inline_value = normalize_query(inline_value_match.group(1))
                    tail = normalize_query(tail[: inline_value_match.start()]).strip()
                if document_token.upper() == "PIX" and not tail and inline_value:
                    unresolved_block = first_unvalued_pix_block(page_blocks, current)
                    if unresolved_block is not None:
                        unresolved_block["value_text"] = inline_value
                        continue
                if document_token.upper() == "PIX" and normalize_query(tail) == "PIX RECEBIDO - OUTRA IF" and inline_value:
                    unresolved_block = first_unvalued_pix_block(page_blocks, current)
                    if unresolved_block is not None and [item for item in unresolved_block.get("details", []) if normalize_query(item)]:
                        unresolved_block["date_token"] = current_date_token
                        unresolved_block["value_text"] = inline_value
                        continue
                if (
                    current is not None
                    and normalize_query(current.get("history")) == "PIX RECEBIDO - OUTRA IF"
                    and normalize_query(current.get("value_text"))
                    and not [item for item in current.get("details", []) if normalize_query(item)]
                    and tail.startswith("Recebimento Pix")
                ):
                    current["details"].append(tail)
                    continue
                if current is not None and tail and not tail.startswith("Recebimento Pix") and tail not in current_account_histories:
                    absorbed_tail = normalize_query(tail)
                    if absorbed_tail in {"DEP.DINHEIRO - INTERCREDIS", "DEP.CHEQUE BLOQ.1D", "LIBER.DEPÓSITO BLOQ", "EST.PIX EMIT.OUT.IF"}:
                        page_blocks.append(current)
                        current = start_block(page_number, current_date_token, absorbed_tail)
                        continue
                    current["details"].append(tail)
                    if inline_value:
                        current["value_text"] = inline_value
                    continue
                initial_detail = ""
                history = tail
                if tail.startswith("Recebimento Pix"):
                    history = "PIX RECEBIDO - OUTRA IF"
                    initial_detail = tail
                elif not tail and document_token.upper() == "PIX":
                    history = "PIX RECEBIDO - OUTRA IF"
                if current is not None:
                    page_blocks.append(current)
                current = start_block(
                    page_number,
                    current_date_token,
                    history,
                    value_text=inline_value,
                    initial_detail=initial_detail,
                )
                continue

            if line in current_account_histories and current_date_token:
                if (
                    current is not None
                    and normalize_query(line) == "PIX RECEBIDO - OUTRA IF"
                    and normalize_query(current.get("history")) == "PIX RECEBIDO - OUTRA IF"
                    and normalize_query(current.get("value_text"))
                    and not [item for item in current.get("details", []) if normalize_query(item)]
                ):
                    continue
                if current is not None:
                    page_blocks.append(current)
                current = start_block(page_number, current_date_token, line)
                continue

            if line.startswith("Recebimento Pix") and current_date_token:
                if (
                    current is not None
                    and normalize_query(current.get("history")) == "PIX RECEBIDO - OUTRA IF"
                    and normalize_query(current.get("value_text"))
                    and [item for item in current.get("details", []) if normalize_query(item)]
                ):
                    page_blocks.append(current)
                    current = None
            if line.startswith("Recebimento Pix") and current is None and current_date_token:
                current = start_block(
                    page_number,
                    current_date_token,
                    "PIX RECEBIDO - OUTRA IF",
                    initial_detail=line,
                )
                continue

            if current is not None:
                current["details"].append(line)

        if current is not None:
            details = [item for item in current.get("details", []) if normalize_query(item)]
            if normalize_query(current.get("history")) == "PIX RECEBIDO - OUTRA IF" and not normalize_query(current.get("value_text")) and details:
                carry_block = current
            elif normalize_query(current.get("history")) == "PIX RECEBIDO - OUTRA IF" and normalize_query(current.get("value_text")) and not details:
                carry_block = current
            else:
                page_blocks.append(current)
        blocks.extend(page_blocks)
        pending_value_texts.extend(page_values)

    if carry_block is not None:
        blocks.append(carry_block)

    pending_blocks = [block for block in blocks if not normalize_query(block.get("value_text"))]
    if pending_value_texts and len(pending_value_texts) == len(pending_blocks):
        for block, value_text in zip(pending_blocks, pending_value_texts):
            block["value_text"] = normalize_query(value_text)
    entries: list[dict[str, object]] = []
    order = 0
    for block in blocks:
        history = normalize_query(block["history"])
        if history in excluded:
            continue
        metadata = sicoob_receiving_kind_metadata(history) or sicoob_receiving_kind_metadata_norm(history)
        if not metadata:
            continue
        movement_kind = str(metadata.get("movement_kind") or "")
        if movement_kind in {"deposito_dinheiro", "deposito_cheque", "liberacao_deposito", "estorno_pix"}:
            continue
        value_text = normalize_query(block["value_text"])
        if not value_text:
            continue
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
                "movement_kind": movement_kind,
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
        "statement_kind": statement_kind,
        "layout_code": layout_code,
        "file_hash": hashlib.sha256(pdf_path.read_bytes()).hexdigest(),
        "period_start": period_start,
        "period_end": period_end,
        "entries": entries,
    }


def parse_sicoob_current_account_pdf(pdf_path: Path) -> dict[str, object]:
    return parse_sicoob_receipts_pdf(
        pdf_path,
        include_current_account_lines=True,
        statement_kind="extrato_sicoob_conta_corrente",
        layout_code="SICOOB_CONTA_CORRENTE",
    )


def santander_detect_layout(full_text: object) -> str:
    normalized = normalize_match_name(full_text)
    if "EXTRATO CONSOLIDADO INTELIGENTE" in normalized:
        return "SANTANDER_CONSOLIDADO"
    if re.search(r"Per[íi]odos:\s*\d{2}/\d{2}/\d{4}\s+a\s+\d{2}/\d{2}/\d{4}", str(full_text or ""), flags=re.IGNORECASE):
        return "SANTANDER_NAO_CONSOLIDADO"
    return "SANTANDER_AUTO"


def santander_period_from_text(full_text: object, layout_code: object) -> tuple[str, str]:
    text = str(full_text or "")
    normalized_layout = normalize_query(layout_code).upper()
    range_match = re.search(
        r"Per[íi]odos:\s*(\d{2}/\d{2}/\d{4})\s+a\s+(\d{2}/\d{2}/\d{4})",
        text,
        flags=re.IGNORECASE,
    )
    if range_match:
        start_br, end_br = range_match.groups()
        start_day, start_month, start_year = [moneyless_int(part) for part in start_br.split("/")]
        end_day, end_month, end_year = [moneyless_int(part) for part in end_br.split("/")]
        return date(start_year, start_month, start_day).isoformat(), date(end_year, end_month, end_day).isoformat()

    month_match = re.search(r"([A-Za-zÀ-ÿçÇ]+)\s*/\s*(\d{4})", text)
    if month_match and normalized_layout == "SANTANDER_CONSOLIDADO":
        month_norm = normalize_match_name(month_match.group(1))
        month = SANTANDER_MONTHS_PT.get(month_norm)
        year = moneyless_int(month_match.group(2))
        if month and year:
            last_day = calendar.monthrange(year, month)[1]
            return date(year, month, 1).isoformat(), date(year, month, last_day).isoformat()

    dated_rows = []
    for day, month, year in re.findall(r"\b(\d{2})/(\d{2})/(\d{4})\b", text):
        try:
            dated_rows.append(date(moneyless_int(year), moneyless_int(month), moneyless_int(day)))
        except ValueError:
            continue
    if dated_rows:
        return min(dated_rows).isoformat(), max(dated_rows).isoformat()

    if month_match:
        month_norm = normalize_match_name(month_match.group(1))
        month = SANTANDER_MONTHS_PT.get(month_norm)
        year = moneyless_int(month_match.group(2))
        if month and year:
            last_day = calendar.monthrange(year, month)[1]
            return date(year, month, 1).isoformat(), date(year, month, last_day).isoformat()

    detected = statement_layout_label(layout_code)
    raise ValueError(f"Nao foi possivel identificar o periodo do extrato {detected}.")


def santander_period_from_entries(
    entries: list[dict[str, object]],
    fallback_start: str,
    fallback_end: str,
) -> tuple[str, str]:
    movement_dates: list[date] = []
    for entry in entries:
        received_on = normalize_query(entry.get("received_on"))
        if not received_on:
            continue
        try:
            movement_dates.append(date.fromisoformat(received_on))
        except ValueError:
            continue
    if movement_dates:
        return min(movement_dates).isoformat(), max(movement_dates).isoformat()
    return fallback_start, fallback_end


def parse_santander_statement_pdf(pdf_path: Path, requested_layout_code: str = "SANTANDER_AUTO") -> dict[str, object]:
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
        r"^(?:(\d{2}/\d{2}(?:/\d{4})?)\s+)?Pi\s*x\s+Recebido\s+(\d{11}|\d{14})(?:\s+\d{4,})?\s+-?\s*R?\s*\$?\s*([0-9.]+,\d{2})\b",
        flags=re.IGNORECASE,
    )
    entries: list[dict[str, object]] = []
    current_date_token = ""
    order = 0
    for page_number, page in enumerate(pages, start=1):
        raw_lines = page.splitlines()
        index = 0
        while index < len(raw_lines):
            line = normalize_query(raw_lines[index])
            next_line = normalize_query(raw_lines[index + 1]) if index + 1 < len(raw_lines) else ""
            if next_line:
                line_has_pix = bool(re.search(r"\bPi\s*x\s+Recebido\b", line, flags=re.IGNORECASE))
                next_has_pix = bool(re.search(r"\bPi\s*x\s+Recebido\b", next_line, flags=re.IGNORECASE))
                split_pix_prefix = bool(re.search(r"\bPi\s*$", line, flags=re.IGNORECASE)) and bool(
                    re.search(r"^x\s+Recebido\b", next_line, flags=re.IGNORECASE)
                )
                combined_line = normalize_query(f"{line} {next_line}")
                if (split_pix_prefix or line_has_pix) and not line_re.search(line) and line_re.search(combined_line):
                    line = combined_line
                    index += 1
            if not line:
                index += 1
                continue
            date_match = re.match(r"^(\d{2}/\d{2}(?:/\d{4})?)\b", line)
            if date_match:
                current_date_token = normalize_query(date_match.group(1))
            if not re.search(r"\bPi\s*x\s+Recebido\b", line, flags=re.IGNORECASE):
                index += 1
                continue
            match = line_re.search(line)
            if not match:
                index += 1
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
            index += 1
    if not entries:
        raise ValueError("Nao foi possivel localizar PIX recebidos validos no extrato Santander.")
    period_start, period_end = santander_period_from_entries(entries, period_start, period_end)
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
    normalized_layout = normalize_query(layout_code).upper()
    if normalized_layout == "SICOOB_RECEBIMENTOS":
        return parse_sicoob_receipts_pdf(pdf_path)
    if normalized_layout == "SICOOB_CONTA_CORRENTE":
        return parse_sicoob_current_account_pdf(pdf_path)
    if statement_layout_is_santander(normalized_layout):
        return parse_santander_statement_pdf(pdf_path, normalized_layout)
    return parse_bradesco_statement_pdf(pdf_path)


def statement_should_skip_bradesco_entry(entry: Mapping[str, object]) -> bool:
    movement_kind = normalize_query(entry.get("movement_kind"))
    prefix = normalize_query(entry.get("prefix"))
    source_name = normalize_query(entry.get("source_name"))
    if movement_kind in {"deposito_dinheiro", "deposito_cheque"} and not source_name:
        if prefix in {"Dep Dinheiro Atm", "Dep Cheque Atm", "Deposito C/corrente-bdn"}:
            return True
    return False


def statement_should_skip_sicoob_entry(entry: Mapping[str, object]) -> bool:
    movement_kind = normalize_query(entry.get("movement_kind"))
    prefix = normalize_query(entry.get("prefix"))
    if movement_kind in {"deposito_dinheiro", "deposito_cheque", "liberacao_deposito", "estorno_pix"}:
        if prefix in {"DEP.DINHEIRO INTERC", "DEP.CHEQUE BLOQ.1D", "LIBER.DEPÓSITO BLOQ", "EST.PIX EMIT.OUT.IF"}:
            return True
    return False


def statement_should_skip_entry(layout_code: object, entry: Mapping[str, object]) -> bool:
    normalized_layout = normalize_query(layout_code).upper()
    if normalized_layout in {"SICOOB_RECEBIMENTOS", "SICOOB_CONTA_CORRENTE"}:
        return statement_should_skip_sicoob_entry(entry)
    if statement_layout_is_santander(normalized_layout):
        return False
    return statement_should_skip_bradesco_entry(entry)


def statement_row_should_be_excluded(row: Mapping[str, object]) -> bool:
    movement_kind = normalize_query(row["movement_kind"])
    prefix = normalize_query(row["prefixo_historico"])
    source_name = normalize_query(row["nome_origem"])
    if movement_kind in {"deposito_dinheiro", "deposito_cheque"} and not source_name:
        if prefix in {"Dep Dinheiro Atm", "Dep Cheque Atm", "Deposito C/corrente-bdn"}:
            return True
    if movement_kind in {"deposito_dinheiro", "deposito_cheque", "liberacao_deposito", "estorno_pix"}:
        if prefix in {"DEP.DINHEIRO INTERC", "DEP.CHEQUE BLOQ.1D", "LIBER.DEPÓSITO BLOQ", "EST.PIX EMIT.OUT.IF"}:
            return True
    return False


def merge_statement_review_notes(current_note: object, extra_note: object) -> str:
    current_text = normalize_query(current_note)
    extra_text = normalize_query(extra_note)
    if not current_text:
        return extra_text
    if not extra_text:
        return current_text
    if extra_text in current_text:
        return current_text
    return f"{current_text}\n{extra_text}"


def statement_association_name_key(value: object) -> str:
    text = normalize_query(value)
    if not text:
        return ""
    text = re.sub(r"^(Remet\.|Rem:)\s*", "", text, flags=re.IGNORECASE)
    return normalize_match_name(text)


def statement_association_identity_key(source_name: object, bank_document: object = "") -> str:
    name_key = statement_association_name_key(source_name)
    if name_key:
        return f"nome:{name_key}"
    document_key = cleaned_document_token(bank_document)
    if document_key:
        return f"doc:{document_key}"
    return ""

from __future__ import annotations

import re
from typing import Mapping

from .normalization import normalize_match_name, normalize_query


def _value(row: Mapping[str, object], key: str) -> object:
    getter = getattr(row, "get", None)
    if getter:
        return getter(key)
    try:
        return row[key]
    except (KeyError, IndexError):
        return ""


def normalize_address_complement(value: object) -> str:
    text = normalize_match_name(value)
    if not text:
        return ""
    text = re.sub(r"\b(APARTAMENTO|APTO|APT|AP)\b", "AP", text)
    text = re.sub(r"\b(BLOCO|BLCO|BL)\b", "BL", text)
    text = re.sub(r"\b(CASA|CS)\b", "CASA", text)
    text = re.sub(r"\b(COBERTURA|COB)\b", "COB", text)
    text = re.sub(r"\b(SALA|SL)\b", "SALA", text)
    text = re.sub(r"\b(LOTE|LT)\b", "LT", text)
    text = re.sub(r"\b(QUADRA|QD)\b", "QD", text)
    text = re.sub(r"([A-Z]+)(\d+)", r"\1 \2", text)
    text = re.sub(r"(\d+)([A-Z]+)", r"\1 \2", text)
    text = re.sub(r"\b(\d+) 0\b", r"\1", text)
    tokens = text.split()
    pairs: list[tuple[str, str]] = []
    bare: list[str] = []
    index = 0
    labels = {"AP", "BL", "CASA", "COB", "SALA", "LT", "QD"}
    while index < len(tokens):
        token = tokens[index]
        if token in labels and index + 1 < len(tokens):
            value_token = tokens[index + 1].lstrip("0") or "0"
            pairs.append((token, value_token))
            index += 2
            continue
        if token.isdigit():
            bare.append(token.lstrip("0") or "0")
        index += 1
    if not pairs and len(bare) == 1:
        pairs.append(("AP", bare[0]))
    elif bare:
        pairs.extend(("NUM", item) for item in bare)
    if not pairs:
        return " ".join(tokens)
    ordered = sorted(pairs, key=lambda item: (item[0], item[1]))
    return " ".join(f"{label} {number}" for label, number in ordered)


def family_address_key(row: Mapping[str, object], *, include_complement: bool = True) -> tuple[str, ...]:
    cep = normalize_match_name(_value(row, "cep"))
    street = normalize_match_name(_value(row, "logradouro"))
    number = normalize_match_name(_value(row, "numero"))
    neighborhood = normalize_match_name(_value(row, "bairro"))
    city = normalize_match_name(_value(row, "cidade"))
    state = normalize_match_name(_value(row, "uf"))
    if not (cep or (street and number)):
        return ()
    base = (cep, street, number, neighborhood, city, state)
    if not include_complement:
        return base
    return (*base, normalize_address_complement(_value(row, "complemento")))


def family_base_address_key(row: Mapping[str, object]) -> tuple[str, ...]:
    return family_address_key(row, include_complement=False)


def family_group_label(row: Mapping[str, object], complement: object | None = None) -> str:
    street = normalize_query(_value(row, "logradouro"))
    number = normalize_query(_value(row, "numero"))
    neighborhood = normalize_query(_value(row, "bairro"))
    cep = normalize_query(_value(row, "cep"))
    unit = normalize_address_complement(_value(row, "complemento") if complement is None else complement)
    address = " ".join(part for part in [street, number] if part)
    suffix = " · ".join(part for part in [unit, neighborhood, cep] if part)
    return f"Familia domiciliar {address}" + (f" · {suffix}" if suffix else "")

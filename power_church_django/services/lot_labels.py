from __future__ import annotations

from datetime import date, datetime

from power_church_core.normalization import normalize_query

_MONTHS_FULL = {
    1: "janeiro",
    2: "fevereiro",
    3: "marco",
    4: "abril",
    5: "maio",
    6: "junho",
    7: "julho",
    8: "agosto",
    9: "setembro",
    10: "outubro",
    11: "novembro",
    12: "dezembro",
}

_MONTHS_SHORT = {
    "jan": 1,
    "fev": 2,
    "mar": 3,
    "abr": 4,
    "mai": 5,
    "jun": 6,
    "jul": 7,
    "ago": 8,
    "set": 9,
    "out": 10,
    "nov": 11,
    "dez": 12,
}


def lot_public_label(lot_id: int | None, *, month_year: str = "") -> str:
    clean_id = int(lot_id or 0)
    suffix = f"#{clean_id:03d}" if clean_id else "#000"
    clean_month_year = normalize_query(month_year)
    return f"{clean_month_year} {suffix}".strip() if clean_month_year else suffix


def month_year_from_any(*values: object) -> str:
    for value in values:
        label = _month_year_label(value)
        if label:
            return label
    return ""


def _month_year_label(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return _month_year(value.year, value.month)
    if isinstance(value, date):
        return _month_year(value.year, value.month)
    raw = normalize_query(value)
    if not raw:
        return ""
    if len(raw) >= 7 and raw[4] == "-" and raw[:4].isdigit() and raw[5:7].isdigit():
        return _month_year(int(raw[:4]), int(raw[5:7]))
    if len(raw) >= 10 and raw[2] == "/" and raw[5] == "/":
        try:
            parsed = datetime.strptime(raw[:10], "%d/%m/%Y")
        except ValueError:
            parsed = None
        if parsed is not None:
            return _month_year(parsed.year, parsed.month)
    lowered = raw.casefold()
    if "/" in lowered:
        left, right = lowered.split("/", 1)
        left = left.strip()
        right = right.strip()
        if left in _MONTHS_SHORT and right.isdigit() and len(right) == 4:
            return _month_year(int(right), _MONTHS_SHORT[left])
        if left.isdigit() and len(left) == 2 and right.isdigit() and len(right) == 4:
            return _month_year(int(right), int(left))
    if len(raw) == 6 and raw.isdigit():
        return _month_year(int(raw[:4]), int(raw[4:6]))
    return ""


def _month_year(year: int, month: int) -> str:
    month_name = _MONTHS_FULL.get(int(month or 0), "")
    year_value = int(year or 0)
    if not month_name or year_value <= 0:
        return ""
    return f"{month_name}/{year_value}"

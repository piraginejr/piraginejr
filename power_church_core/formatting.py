from __future__ import annotations


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


def br_date(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parts = text.split("-")
    if len(parts) == 3 and all(part.isdigit() for part in parts):
        return f"{parts[2]}/{parts[1]}/{parts[0]}"
    return text


def br_datetime(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    date_part, _, time_part = text.replace("T", " ").partition(" ")
    formatted_date = br_date(date_part)
    if not time_part:
        return formatted_date
    return f"{formatted_date} {time_part[:5]}"


def br_money(value: object) -> str:
    try:
        amount = float(value or 0)
    except (TypeError, ValueError):
        amount = 0.0
    formatted = f"{amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {formatted}"


def parse_money(value: object) -> float:
    text = str(value or "").strip()
    if not text:
        raise ValueError("Informe o valor da contribuicao.")
    cleaned = text.replace("R$", "").replace(" ", "").replace(".", "").replace(",", ".")
    try:
        amount = float(cleaned)
    except ValueError as exc:
        raise ValueError("Valor da contribuicao invalido.") from exc
    if amount <= 0:
        raise ValueError("O valor da contribuicao deve ser maior que zero.")
    return round(amount, 2)


def competencia_from_date(value: object) -> tuple[str, int]:
    text = str(value or "").strip()
    parts = text.split("-")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        raise ValueError("Informe uma data de recebimento valida.")
    year, month, _day = (int(parts[0]), int(parts[1]), int(parts[2]))
    if month < 1 or month > 12:
        raise ValueError("Mes de recebimento invalido.")
    return f"{MONTHS_PT[month]} {str(year)[-2:]}", year * 100 + month

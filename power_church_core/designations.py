from __future__ import annotations


CENT_RULE_PLAN_ACCOUNT_PREFIX = "CENT."
CENT_RULE_TYPE_PREFIX = "CENT_"


def cent_rule_digits(code: object) -> str:
    return "".join(ch for ch in str(code or "") if ch.isdigit())[-2:].zfill(2)


def cent_rule_plan_account_code(code: object, prefix: str = CENT_RULE_PLAN_ACCOUNT_PREFIX) -> str:
    return f"{prefix}{cent_rule_digits(code)}"


def cent_rule_type_code(code: object, prefix: str = CENT_RULE_TYPE_PREFIX) -> str:
    return f"{prefix}{cent_rule_digits(code)}"


def cent_rule_type_is_system_managed(
    selected_type_code: object,
    cent_code: object,
    default_type_codes: dict[str, str] | None = None,
    prefix: str = CENT_RULE_TYPE_PREFIX,
) -> bool:
    selected = str(selected_type_code or "").strip().upper()
    code = cent_rule_digits(cent_code)
    if selected.startswith(prefix):
        return True
    return selected == str((default_type_codes or {}).get(code, "")).strip().upper()


def suggested_type_for_cent_rule(rule_row: object | None) -> str:
    return "destinacao_especial" if rule_row else "dizimo"

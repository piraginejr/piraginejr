from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from power_church_core.bank_lots import StatementEntryPlan, statement_entry_plan
from power_church_core.bank_parsers import parse_statement_pdf_by_layout, statement_should_skip_entry
from power_church_core.normalization import normalize_query


def plan_statement_import(layout_code: str, pdf_path: Path) -> dict[str, object]:
    parsed = parse_statement_pdf_by_layout(layout_code, pdf_path)
    stored_layout = normalize_query(parsed.get("layout_code") or layout_code).upper() or layout_code
    plans: list[StatementEntryPlan] = [
        statement_entry_plan(stored_layout, entry)
        for entry in parsed["entries"]
        if not statement_should_skip_entry(stored_layout, entry)
    ]
    return {
        "bank_name": parsed["bank_name"],
        "statement_kind": parsed["statement_kind"],
        "layout_code": stored_layout,
        "file_hash": parsed["file_hash"],
        "period_start": parsed["period_start"],
        "period_end": parsed["period_end"],
        "entries": [asdict(plan) for plan in plans],
    }


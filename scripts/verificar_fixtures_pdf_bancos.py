from __future__ import annotations

import argparse
import os
import importlib.util
import json
import sqlite3
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "power_church_demo.py"
DB_PATH = ROOT / "data" / "power_church_membros_importado.db"
REPORT_DIR = ROOT / "data" / "homologacao"
FIXTURE_DIR = REPORT_DIR / "pdf_fixtures"


def load_app_module():
    root_text = str(ROOT)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    spec = importlib.util.spec_from_file_location("power_church_demo", APP_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@dataclass
class LotCheck:
    origin: str
    lot_id: int
    layout: str
    filename: str
    ok: bool
    detail: str
    parsed_count: int = 0
    db_count: int = 0
    parsed_total: float = 0.0
    db_total: float = 0.0


def round_money(value: object) -> float:
    return round(float(value or 0.0), 2)


@contextmanager
def temporary_pdf_provider(provider: str):
    provider = str(provider or "").strip()
    if not provider:
        yield
        return
    previous = os.environ.get("POWER_CHURCH_PDF_PROVIDER")
    os.environ["POWER_CHURCH_PDF_PROVIDER"] = provider
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("POWER_CHURCH_PDF_PROVIDER", None)
        else:
            os.environ["POWER_CHURCH_PDF_PROVIDER"] = previous


def lot_pdf_provider(row: sqlite3.Row) -> str:
    forced = str(os.environ.get("POWER_CHURCH_PDF_PROVIDER") or "").strip()
    if forced:
        return forced
    notes = str(row["observacoes"] or "").lower() if "observacoes" in row.keys() else ""
    if "motor pdf usado na importacao django: pymupdf" in notes:
        return "pymupdf"
    if "motor pdf usado na importacao django: swift_pdfkit" in notes:
        return "swift_pdfkit"
    return ""


def entry_digest(entry: dict[str, object]) -> dict[str, object]:
    return {
        "order": int(entry.get("order_in_file") or 0),
        "page": int(entry.get("page_number") or 0),
        "date": str(entry.get("received_on") or ""),
        "amount": round_money(entry.get("amount")),
        "kind": str(entry.get("movement_kind") or ""),
        "source_name": str(entry.get("source_name") or entry.get("donor_name") or ""),
        "document": str(entry.get("bank_document") or entry.get("document_mask") or ""),
        "prefix": str(entry.get("prefix") or ""),
    }


def compare_counts_and_totals(parsed_count: int, parsed_total: float, db_count: int, db_total: float) -> tuple[bool, str]:
    count_ok = parsed_count == db_count
    total_ok = abs(parsed_total - db_total) <= 0.01
    detail = f"parser {parsed_count} / {parsed_total:.2f}; banco {db_count} / {db_total:.2f}"
    return count_ok and total_ok, detail


def build_pix_lot_fixture(app, row: sqlite3.Row) -> tuple[LotCheck, dict[str, object]]:
    lot_id = int(row["id"] or 0)
    path = Path(str(row["caminho_arquivo"] or ""))
    provider = lot_pdf_provider(row)
    base = {
        "origin": "pix",
        "lot_id": lot_id,
        "bank": str(row["banco"] or ""),
        "layout": "SICOOB_PIX",
        "filename": str(row["nome_arquivo"] or ""),
        "path": str(path),
        "pdf_provider": provider or "default",
        "db_count": int(row["total_movimentos"] or 0),
        "db_total": round_money(row["total_valor"]),
    }
    if not path.exists():
        check = LotCheck("PIX", lot_id, "SICOOB_PIX", base["filename"], False, f"arquivo nao encontrado: {path}")
        return check, {**base, "ok": False, "error": check.detail}
    try:
        with temporary_pdf_provider(provider):
            parsed = app.parse_sicoob_pix_pdf(path)
        entries = list(parsed["entries"])
        parsed_count = len(entries)
        parsed_total = round_money(sum(float(entry.get("amount") or 0.0) for entry in entries))
        db_count = int(row["total_movimentos"] or 0)
        db_total = round_money(row["total_valor"])
        ok, detail = compare_counts_and_totals(parsed_count, parsed_total, db_count, db_total)
        period_ok = str(parsed.get("period_start") or "") == str(row["periodo_inicio"] or "") and str(parsed.get("period_end") or "") == str(row["periodo_fim"] or "")
        if not period_ok:
            ok = False
            detail += f"; periodo parser {parsed.get('period_start')} a {parsed.get('period_end')}"
        check = LotCheck("PIX", lot_id, "SICOOB_PIX", base["filename"], ok, detail, parsed_count, db_count, parsed_total, db_total)
        return check, {
            **base,
            "ok": ok,
            "detail": detail,
            "parsed_count": parsed_count,
            "parsed_total": parsed_total,
            "parsed_period_start": str(parsed.get("period_start") or ""),
            "parsed_period_end": str(parsed.get("period_end") or ""),
            "file_hash": str(parsed.get("file_hash") or ""),
            "entries": [entry_digest(entry) for entry in entries],
        }
    except Exception as exc:
        check = LotCheck("PIX", lot_id, "SICOOB_PIX", base["filename"], False, f"{type(exc).__name__}: {exc}")
        return check, {**base, "ok": False, "error": check.detail}


def statement_financial_totals(conn: sqlite3.Connection, lot_id: int) -> tuple[int, float]:
    row = conn.execute(
        """
        SELECT COUNT(*) AS quantidade, COALESCE(SUM(c.valor), 0) AS total
        FROM extrato_movimentos m
        JOIN contribuicoes c ON c.id = m.imported_contribution_id
        WHERE m.lote_id = ? AND m.ativo = 1 AND c.ativo = 1
        """,
        (lot_id,),
    ).fetchone()
    return int(row["quantidade"] or 0), round_money(row["total"])


def build_statement_lot_fixture(app, conn: sqlite3.Connection, row: sqlite3.Row, organization_name: str) -> tuple[LotCheck, dict[str, object]]:
    lot_id = int(row["id"] or 0)
    layout = str(row["layout_codigo"] or "")
    path = Path(str(row["caminho_arquivo"] or ""))
    provider = lot_pdf_provider(row)
    base = {
        "origin": "extrato",
        "lot_id": lot_id,
        "bank": str(row["banco"] or ""),
        "layout": layout,
        "filename": str(row["nome_arquivo"] or ""),
        "path": str(path),
        "pdf_provider": provider or "default",
        "db_count": int(row["total_movimentos"] or 0),
        "db_total": round_money(row["total_valor"]),
    }
    if not path.exists():
        check = LotCheck("Extrato", lot_id, layout, base["filename"], False, f"arquivo nao encontrado: {path}")
        return check, {**base, "ok": False, "error": check.detail}
    try:
        with temporary_pdf_provider(provider):
            parsed = app.parse_statement_pdf_by_layout(layout, path)
        raw_entries = list(parsed["entries"])
        included_entries = [entry for entry in raw_entries if not app.statement_should_skip_entry(layout, entry)]
        same_org_entries = [
            entry
            for entry in included_entries
            if app.statement_is_same_organization_origin(entry.get("source_name"), organization_name)
        ]
        contribution_entries = [
            entry
            for entry in included_entries
            if not app.statement_is_same_organization_origin(entry.get("source_name"), organization_name)
        ]
        parsed_count = len(included_entries)
        parsed_total = round_money(sum(float(entry.get("amount") or 0.0) for entry in included_entries))
        db_count = int(row["total_movimentos"] or 0)
        db_total = round_money(row["total_valor"])
        ok, detail = compare_counts_and_totals(parsed_count, parsed_total, db_count, db_total)
        db_financial_count, db_financial_total = statement_financial_totals(conn, lot_id)
        parsed_contribution_count = len(contribution_entries)
        parsed_contribution_total = round_money(sum(float(entry.get("amount") or 0.0) for entry in contribution_entries))
        if not ok and same_org_entries:
            financial_ok = (
                parsed_contribution_count == db_financial_count
                and abs(parsed_contribution_total - db_financial_total) <= 0.01
            )
            if financial_ok:
                ok = True
                detail += (
                    f"; OK financeiro {parsed_contribution_count} / {parsed_contribution_total:.2f}; "
                    f"alerta operacional: parser atual encontra {len(same_org_entries)} remessa(s) interna(s)"
                )
        parsed_layout = str(parsed.get("layout_code") or layout)
        period_ok = str(parsed.get("period_start") or "") == str(row["periodo_inicio"] or "") and str(parsed.get("period_end") or "") == str(row["periodo_fim"] or "")
        if not period_ok:
            ok = False
            detail += f"; periodo parser {parsed.get('period_start')} a {parsed.get('period_end')}"
        check = LotCheck("Extrato", lot_id, parsed_layout, base["filename"], ok, detail, parsed_count, db_count, parsed_total, db_total)
        return check, {
            **base,
            "ok": ok,
            "detail": detail,
            "parsed_layout": parsed_layout,
            "parsed_count": parsed_count,
            "parsed_total": parsed_total,
            "parsed_contribution_count": parsed_contribution_count,
            "parsed_contribution_total": parsed_contribution_total,
            "db_financial_count": db_financial_count,
            "db_financial_total": db_financial_total,
            "same_org_count": len(same_org_entries),
            "same_org_total": round_money(sum(float(entry.get("amount") or 0.0) for entry in same_org_entries)),
            "raw_count": len(raw_entries),
            "raw_total": round_money(sum(float(entry.get("amount") or 0.0) for entry in raw_entries)),
            "skipped_count": len(raw_entries) - len(included_entries),
            "parsed_period_start": str(parsed.get("period_start") or ""),
            "parsed_period_end": str(parsed.get("period_end") or ""),
            "file_hash": str(parsed.get("file_hash") or ""),
            "entries": [entry_digest(entry) for entry in included_entries],
        }
    except Exception as exc:
        check = LotCheck("Extrato", lot_id, layout, base["filename"], False, f"{type(exc).__name__}: {exc}")
        return check, {**base, "ok": False, "error": check.detail}


def build_fixtures(db_path: Path) -> tuple[list[LotCheck], dict[str, object]]:
    app = load_app_module()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    checks: list[LotCheck] = []
    fixtures: list[dict[str, object]] = []
    try:
        pix_rows = conn.execute("SELECT * FROM pix_lotes ORDER BY id").fetchall()
        statement_rows = conn.execute("SELECT * FROM extrato_lotes ORDER BY id").fetchall()
        organization_row = conn.execute("SELECT nome FROM organizacoes ORDER BY id LIMIT 1").fetchone()
        organization_name = str(organization_row["nome"] if organization_row else "")
        for row in pix_rows:
            check, fixture = build_pix_lot_fixture(app, row)
            checks.append(check)
            fixtures.append(fixture)
        for row in statement_rows:
            check, fixture = build_statement_lot_fixture(app, conn, row, organization_name)
            checks.append(check)
            fixtures.append(fixture)
    finally:
        conn.close()
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "database": str(db_path),
        "fixtures": fixtures,
    }
    return checks, payload


def write_outputs(checks: list[LotCheck], payload: dict[str, object]) -> tuple[Path, Path]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_target = FIXTURE_DIR / f"pdf_fixtures_bancos_{stamp}.json"
    report_target = REPORT_DIR / f"fixtures_pdf_bancos_{stamp}.md"
    json_target.write_text(json.dumps(payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    failed = [check for check in checks if not check.ok]
    lines = [
        "# Fixtures PDF Bancos",
        "",
        f"Gerado em: {datetime.now().isoformat(timespec='seconds')}",
        f"Resultado: {'OK' if not failed else 'FALHAS'}",
        f"Fixture JSON: `{json_target}`",
        "",
        "| Origem | Lote | Layout | Status | Parser | Banco | Detalhe |",
        "| --- | ---: | --- | --- | ---: | ---: | --- |",
    ]
    for check in checks:
        lines.append(
            "| "
            + " | ".join(
                [
                    check.origin,
                    str(check.lot_id),
                    check.layout,
                    "OK" if check.ok else "FALHA",
                    f"{check.parsed_count} / {check.parsed_total:.2f}",
                    f"{check.db_count} / {check.db_total:.2f}",
                    check.detail.replace("|", "/"),
                ]
            )
            + " |"
        )
    report_target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_target, json_target


def main() -> int:
    parser = argparse.ArgumentParser(description="Gera fixtures dos PDFs bancarios ja importados e compara com o banco.")
    parser.add_argument("--db", default=str(DB_PATH), help="Caminho do banco SQLite.")
    parser.add_argument("--report", action="store_true", help="Grava relatorio markdown e fixture JSON.")
    args = parser.parse_args()
    checks, payload = build_fixtures(Path(args.db))
    for check in checks:
        print(
            f"- {'OK' if check.ok else 'FALHA'}: {check.origin} lote {check.lot_id} "
            f"({check.layout}; {check.detail})"
        )
    if args.report:
        report, fixture = write_outputs(checks, payload)
        print(f"\nRelatorio: {report}")
        print(f"Fixture: {fixture}")
    return 1 if any(not check.ok for check in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())

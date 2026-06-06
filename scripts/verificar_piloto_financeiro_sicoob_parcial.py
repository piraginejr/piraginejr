from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "power_church_membros_importado.db"
REPORT_DIR = ROOT / "data" / "homologacao"
SICOOB_PARTIAL_FILE = ROOT / "data" / "statement_uploads" / "2026-05-13_sicoob_01a11_d2f3bd7390.pdf"
SICOOB_PARTIAL_LOT_ID = 13

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from power_church_core.bank_parsers import parse_statement_pdf_by_layout  # noqa: E402
from power_church_core.normalization import normalize_match_name  # noqa: E402


@dataclass
class Check:
    name: str
    status: str
    detail: str

    @property
    def failed(self) -> bool:
        return self.status == "FALHA"


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def money(value: object) -> str:
    total = float(value or 0)
    return f"R$ {total:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def approx_equal(left: float, right: float, tolerance: float = 0.009) -> bool:
    return abs(float(left) - float(right)) <= tolerance


def expect(name: str, condition: bool, detail_ok: str, detail_fail: str) -> Check:
    return Check(name, "OK" if condition else "FALHA", detail_ok if condition else detail_fail)


def fetch_reference_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT date(data_movimento) AS data_movimento,
               round(valor, 2) AS valor,
               upper(trim(nome_origem)) AS nome_origem,
               coalesce(bank_document, '') AS bank_document
          FROM extrato_movimentos
         WHERE lote_id = ? AND ativo = 1
         ORDER BY id
        """,
        (SICOOB_PARTIAL_LOT_ID,),
    ).fetchall()


def normalized_signature_counter(rows: list[sqlite3.Row]) -> Counter[tuple[object, ...]]:
    counter: Counter[tuple[object, ...]] = Counter()
    for row in rows:
        counter[
            (
                str(row["data_movimento"] or ""),
                round(float(row["valor"] or 0), 2),
                normalize_match_name(row["nome_origem"]),
            )
        ] += 1
    return counter


def parsed_signature_counter(entries: list[dict[str, object]]) -> Counter[tuple[object, ...]]:
    counter: Counter[tuple[object, ...]] = Counter()
    for entry in entries:
        counter[
            (
                str(entry.get("received_on") or ""),
                round(float(entry.get("amount") or 0), 2),
                normalize_match_name(entry.get("source_name") or ""),
            )
        ] += 1
    return counter


def has_entry(entries: list[dict[str, object]], *, date: str, amount: float, name: str) -> bool:
    wanted_name = normalize_match_name(name)
    for entry in entries:
        if (
            str(entry.get("received_on") or "") == date
            and approx_equal(float(entry.get("amount") or 0), amount)
            and normalize_match_name(entry.get("source_name") or "") == wanted_name
        ):
            return True
    return False


def write_report(source_db: Path, checks: list[Check]) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    target = REPORT_DIR / f"piloto_financeiro_sicoob_parcial_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    lines = [
        "# Verificacao Piloto Financeiro Sicoob Parcial",
        "",
        f"Gerado em: {datetime.now().isoformat(timespec='seconds')}",
        f"Banco operacional origem: `{source_db}`",
        f"Arquivo-base: `{SICOOB_PARTIAL_FILE}`",
        f"Lote de referencia: `#{SICOOB_PARTIAL_LOT_ID}`",
        "",
        "| Checagem | Status | Detalhe |",
        "| --- | --- | --- |",
    ]
    for check in checks:
        lines.append(f"| {check.name} | {check.status} | {check.detail} |")
    failed = [check for check in checks if check.failed]
    lines.extend(
        [
            "",
            "## Resultado",
            "",
            "FALHAS detectadas." if failed else "Todas as checagens do piloto Sicoob parcial passaram.",
        ]
    )
    target.write_text("\n".join(lines), encoding="utf-8")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="Verifica o parser do Sicoob parcial contra o lote historico validado.")
    parser.add_argument("--db", default=str(DB_PATH), help="Banco operacional SQLite de origem.")
    parser.add_argument("--report", action="store_true", help="Gera relatorio Markdown em data/homologacao.")
    args = parser.parse_args()

    source_db = Path(args.db).expanduser().resolve()
    checks: list[Check] = []
    if not source_db.exists():
        print(f"FALHA: banco nao encontrado: {source_db}")
        return 2
    if not SICOOB_PARTIAL_FILE.exists():
        print(f"FALHA: arquivo nao encontrado: {SICOOB_PARTIAL_FILE}")
        return 2

    parsed = parse_statement_pdf_by_layout("SICOOB_CONTA_CORRENTE", SICOOB_PARTIAL_FILE)
    entries = list(parsed.get("entries") or [])
    parsed_total = round(sum(float(entry.get("amount") or 0) for entry in entries), 2)

    with connect(source_db) as conn:
        rows = fetch_reference_rows(conn)
        db_total = round(sum(float(row["valor"] or 0) for row in rows), 2)

    checks.extend(
        [
            expect("Quantidade do parcial", len(entries) == 352, "Parser retornou 352 movimentos.", f"Parser retornou {len(entries)} movimentos."),
            expect("Total do parcial", approx_equal(parsed_total, 232892.91), "Parser retornou R$ 232.892,91.", f"Parser retornou {money(parsed_total)}."),
            expect("Lote historico de referencia", len(rows) == 352 and approx_equal(db_total, 232892.91), "Lote #13 segue com 352 movimentos e R$ 232.892,91.", f"Lote #13 retornou {len(rows)} movimentos e {money(db_total)}."),
        ]
    )

    parsed_counter = parsed_signature_counter(entries)
    reference_counter = normalized_signature_counter(rows)
    extra = parsed_counter - reference_counter
    missing = reference_counter - parsed_counter
    checks.append(
        expect(
            "Assinatura normalizada do parcial",
            not extra and not missing,
            "Assinatura data + valor + nome normalizado bateu 100% contra o lote validado.",
            f"Extras={sum(extra.values())} Missing={sum(missing.values())}",
        )
    )

    sentinel_cases = [
        ("Sentinela DOXA 08/05", "2026-05-08", 4000.00, "DOXA TREINAMENTO LTDA"),
        ("Sentinela Paschoal 08/05", "2026-05-08", 1000.00, "PASCHOAL PIRAGINE JUNIOR"),
        ("Sentinela Filipe 04/05", "2026-05-04", 25.00, "FILIPE LIMA POLI"),
        ("Sentinela Luciene 04/05", "2026-05-04", 101.00, "LUCIENE CARDOSO DOS SANTOS DA SILVA"),
    ]
    for label, date, amount, name in sentinel_cases:
        checks.append(
            expect(
                label,
                has_entry(entries, date=date, amount=amount, name=name),
                f"{name} preservado em {date} por {money(amount)}.",
                f"{name} nao encontrado em {date} por {money(amount)}.",
            )
        )

    report = write_report(source_db, checks) if args.report else None
    for check in checks:
        print(f"{check.status}: {check.name} - {check.detail}")
    if report:
        print(f"Relatorio: {report}")
    return 1 if any(check.failed for check in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())

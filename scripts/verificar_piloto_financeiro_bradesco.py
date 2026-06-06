from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "power_church_membros_importado.db"
REPORT_DIR = ROOT / "data" / "homologacao"
SANDBOX_DIR = ROOT / "data" / "sandboxes"
BRADESCO_FILE = Path("/Users/piraginejr/Library/Mobile Documents/com~apple~CloudDocs/Downloads/Downloads/BRADESCO_MAIO26.pdf")
LAYOUT_CODE = "BRADESCO_EXTRATO"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from power_church_demo import PowerChurchDB  # noqa: E402
from power_church_django.services.legacy_bank_write import compare_pdf_upload_providers  # noqa: E402


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


def scalar(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> Any:
    row = conn.execute(sql, params).fetchone()
    return row[0] if row else None


def money(value: object) -> str:
    total = float(value or 0)
    return f"R$ {total:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def approx_equal(left: float, right: float, tolerance: float = 0.009) -> bool:
    return abs(float(left) - float(right)) <= tolerance


def expect(name: str, condition: bool, detail_ok: str, detail_fail: str) -> Check:
    return Check(name, "OK" if condition else "FALHA", detail_ok if condition else detail_fail)


def write_report(source_db: Path, sandbox_db: Path, checks: list[Check]) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    target = REPORT_DIR / f"piloto_financeiro_bradesco_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    lines = [
        "# Verificacao Piloto Financeiro Bradesco",
        "",
        f"Gerado em: {datetime.now().isoformat(timespec='seconds')}",
        f"Banco operacional origem: `{source_db}`",
        f"Banco clone da verificacao: `{sandbox_db}`",
        f"Arquivo-base: `{BRADESCO_FILE}`",
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
            "FALHAS detectadas." if failed else "Todas as checagens do piloto Bradesco passaram.",
        ]
    )
    target.write_text("\n".join(lines), encoding="utf-8")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="Verifica o piloto financeiro controlado do Bradesco em banco clone.")
    parser.add_argument("--db", default=str(DB_PATH), help="Banco operacional SQLite de origem.")
    parser.add_argument("--report", action="store_true", help="Gera relatorio Markdown em data/homologacao.")
    args = parser.parse_args()

    source_db = Path(args.db).expanduser().resolve()
    checks: list[Check] = []
    if not source_db.exists():
        print(f"FALHA: banco nao encontrado: {source_db}")
        return 2
    if not BRADESCO_FILE.exists():
        print(f"FALHA: arquivo nao encontrado: {BRADESCO_FILE}")
        return 2

    comparison = compare_pdf_upload_providers(
        BRADESCO_FILE.name,
        BRADESCO_FILE.read_bytes(),
        import_kind="statement",
        layout_code=LAYOUT_CODE,
    )
    checks.append(
        expect(
            "Portabilidade Bradesco",
            bool(comparison.get("ok")),
            "Comparacao leitor homologado x portavel aprovada.",
            str(comparison.get("difference") or comparison.get("error") or "comparacao nao aprovada."),
        )
    )
    if not comparison.get("ok"):
        report = write_report(source_db, Path("-"), checks) if args.report else None
        if report:
            print(f"Relatorio: {report}")
        for check in checks:
            print(f"{check.status}: {check.name} - {check.detail}")
        return 1

    SANDBOX_DIR.mkdir(parents=True, exist_ok=True)
    sandbox_db = SANDBOX_DIR / f"{source_db.stem}_verify_bradesco_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    shutil.copy2(source_db, sandbox_db)

    with connect(sandbox_db) as conn:
        existing_lot_id = scalar(
            conn,
            """
            SELECT id
              FROM extrato_lotes
             WHERE banco = 'Bradesco'
               AND nome_arquivo = ?
             ORDER BY id DESC
             LIMIT 1
            """,
            (BRADESCO_FILE.name,),
        )
    if existing_lot_id:
        lot_id = int(existing_lot_id)
    else:
        previous_provider = os.environ.get("POWER_CHURCH_PDF_PROVIDER")
        os.environ["POWER_CHURCH_PDF_PROVIDER"] = "pymupdf"
        db = PowerChurchDB(sandbox_db)
        try:
            lot_id = db.create_statement_lot_from_upload(BRADESCO_FILE.name, BRADESCO_FILE.read_bytes(), layout_code=LAYOUT_CODE)
        finally:
            db.close()
            if previous_provider is None:
                os.environ.pop("POWER_CHURCH_PDF_PROVIDER", None)
            else:
                os.environ["POWER_CHURCH_PDF_PROVIDER"] = previous_provider

    with connect(sandbox_db) as conn:
        lot = conn.execute(
            """
            SELECT banco, nome_arquivo, periodo_inicio, periodo_fim, total_movimentos, total_valor, status
              FROM extrato_lotes
             WHERE id = ?
            """,
            (lot_id,),
        ).fetchone()
        checks.append(expect("Lote criado", lot is not None, f"Lote #{lot_id} criado no clone.", "Lote nao encontrado apos a importacao."))
        if lot is None:
            report = write_report(source_db, sandbox_db, checks) if args.report else None
            if report:
                print(f"Relatorio: {report}")
            for check in checks:
                print(f"{check.status}: {check.name} - {check.detail}")
            return 1

        checks.extend(
            [
                expect("Banco do lote", str(lot["banco"] or "") == "Bradesco", "Banco identificado como Bradesco.", f"Banco retornado: {lot['banco']!r}"),
                expect("Arquivo do lote", str(lot["nome_arquivo"] or "") == BRADESCO_FILE.name, "Nome do arquivo preservado.", f"Arquivo retornado: {lot['nome_arquivo']!r}"),
                expect("Periodo inicial", str(lot["periodo_inicio"] or "") == "2026-05-01", "Periodo inicial 2026-05-01.", f"Periodo inicial retornado: {lot['periodo_inicio']!r}"),
                expect("Periodo final", str(lot["periodo_fim"] or "") == "2026-05-31", "Periodo final 2026-05-31.", f"Periodo final retornado: {lot['periodo_fim']!r}"),
                expect("Quantidade de movimentos", int(lot["total_movimentos"] or 0) == 33, "Lote com 33 movimentos uteis.", f"Quantidade retornada: {lot['total_movimentos']!r}"),
                expect("Total do lote", approx_equal(float(lot["total_valor"] or 0), 56015.61), "Total do lote R$ 56.015,61.", f"Total retornado: {money(lot['total_valor'])}"),
                expect(
                    "Status do lote",
                    str(lot["status"] or "") in {"parcial", "encerrado"},
                    "Status do lote dentro do fluxo homologado (parcial ou encerrado).",
                    f"Status retornado: {lot['status']!r}",
                ),
            ]
        )

        review_counts = {
            str(row["review_status"] or "sem_status"): int(row["total"] or 0)
            for row in conn.execute(
                """
                SELECT COALESCE(review_status, '') AS review_status, COUNT(*) AS total
                  FROM extrato_movimentos
                 WHERE lote_id = ? AND ativo = 1
                 GROUP BY COALESCE(review_status, '')
                """,
                (lot_id,),
            ).fetchall()
        }
        expected_review_counts = {
            "revisar_duplicidade": 17,
            "pronto": 11,
            "revisar_pessoa": 4,
            "revisar_destinacao": 1,
        }
        checks.append(
            expect(
                "Distribuicao de review_status",
                review_counts == expected_review_counts,
                f"Distribuicao correta: {expected_review_counts}.",
                f"Distribuicao retornada: {review_counts}",
            )
        )

        imported_count = int(
            scalar(
                conn,
                "SELECT COUNT(*) FROM extrato_movimentos WHERE lote_id = ? AND ativo = 1 AND imported_contribution_id IS NOT NULL",
                (lot_id,),
            )
            or 0
        )
        checks.append(
            expect(
                "Movimentos importados no clone",
                imported_count == 33,
                "Todos os 33 movimentos receberam imported_contribution_id no clone.",
                f"Movimentos importados retornados: {imported_count}",
            )
        )

        duplicate_probe = conn.execute(
            """
            SELECT ordem_no_lote, data_movimento, valor, nome_origem, review_status, duplicate_movement_id, duplicate_contribution_id
              FROM extrato_movimentos
             WHERE lote_id = ? AND ordem_no_lote = 3
            """,
            (lot_id,),
        ).fetchone()
        checks.append(
            expect(
                "Sentinela duplicidade Ronaldo",
                duplicate_probe is not None
                and str(duplicate_probe["nome_origem"] or "") == "RONALDO SANTOS MENDO"
                and str(duplicate_probe["review_status"] or "") == "revisar_duplicidade"
                and int(round(float(duplicate_probe["valor"] or 0))) == 5734
                and int(duplicate_probe["duplicate_movement_id"] or 0) == 3157
                and int(duplicate_probe["duplicate_contribution_id"] or 0) == 7069,
                "Duplicidade sentinela de Ronaldo preservada.",
                f"Probe retornado: {dict(duplicate_probe) if duplicate_probe else None}",
            )
        )

        cent_probe = conn.execute(
            """
            SELECT ordem_no_lote, data_movimento, valor, nome_origem, review_status, codigo_centavos, imported_contribution_id
              FROM extrato_movimentos
             WHERE lote_id = ? AND ordem_no_lote = 41
            """,
            (lot_id,),
        ).fetchone()
        checks.append(
            expect(
                "Sentinela centavos especiais Alessandro",
                cent_probe is not None
                and str(cent_probe["nome_origem"] or "") == "ALESSANDRO DA SILVA BATISTA"
                and str(cent_probe["review_status"] or "") == "revisar_destinacao"
                and str(cent_probe["codigo_centavos"] or "") == "03"
                and approx_equal(float(cent_probe["valor"] or 0), 1000.03)
                and int(cent_probe["imported_contribution_id"] or 0) > 0,
                "Caso de centavos especiais preservado para destinacao.",
                f"Probe retornado: {dict(cent_probe) if cent_probe else None}",
            )
        )

    report = write_report(source_db, sandbox_db, checks) if args.report else None
    for check in checks:
        print(f"{check.status}: {check.name} - {check.detail}")
    if report:
        print(f"Relatorio: {report}")
    return 1 if any(check.failed for check in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())

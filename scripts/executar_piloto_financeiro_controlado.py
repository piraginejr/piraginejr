from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "power_church_membros_importado.db"
SANDBOX_DIR = ROOT / "data" / "sandboxes"
REPORT_DIR = ROOT / "data" / "homologacao"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from power_church_demo import PowerChurchDB  # noqa: E402
from power_church_django.services.legacy_bank_write import compare_pdf_upload_providers  # noqa: E402


DEFAULT_FILE = Path("/Users/piraginejr/Library/Mobile Documents/com~apple~CloudDocs/Downloads/Downloads/BRADESCO_MAIO26.pdf")


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


def write_report(
    *,
    source_db: Path,
    sandbox_db: Path,
    file_path: Path,
    layout_code: str,
    comparison: dict[str, object],
    lot_id: int,
    summary: dict[str, object],
) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    target = REPORT_DIR / f"piloto_financeiro_controlado_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    lines = [
        "# Piloto Financeiro Controlado",
        "",
        f"Gerado em: {datetime.now().isoformat(timespec='seconds')}",
        "",
        f"- Banco operacional origem: `{source_db}`",
        f"- Banco clone usado no piloto: `{sandbox_db}`",
        f"- Arquivo importado: `{file_path}`",
        f"- Layout: `{layout_code}`",
        f"- Comparacao leitor homologado x portavel: {'OK' if comparison.get('ok') else 'ATENCAO'}",
        "",
        "## Resultado do lote no clone",
        "",
        f"- Lote criado: `#{lot_id}`",
        f"- Banco: {summary['bank']}",
        f"- Arquivo: {summary['file_name']}",
        f"- Periodo: {summary['period_start']} a {summary['period_end']}",
        f"- Movimentos: {summary['movement_count']}",
        f"- Total do lote: {summary['total_value_fmt']}",
        f"- Status do lote: {summary['lot_status']}",
        f"- Movimentos pendentes: {summary['pending_count']}",
        f"- Movimentos duplicados: {summary['duplicate_count']}",
        f"- Movimentos conciliados/importados: {summary['imported_count']}",
        "",
        "## Status por review",
        "",
        "| Review status | Quantidade |",
        "| --- | ---: |",
    ]
    for key, value in summary["review_counts"].items():
        lines.append(f"| {key} | {value} |")
    lines.extend(
        [
            "",
            "## Observacao",
            "",
            "Este piloto gravou apenas no banco clone, preservando integralmente a base operacional.",
        ]
    )
    target.write_text("\n".join(lines), encoding="utf-8")
    return target


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Executa um piloto financeiro controlado em banco clone.")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Banco operacional SQLite de origem.")
    parser.add_argument("--file", default=str(DEFAULT_FILE), help="PDF do extrato bancario a importar no clone.")
    parser.add_argument("--layout", default="BRADESCO_EXTRATO", help="Layout do extrato.")
    parser.add_argument("--provider", default="pymupdf", choices=["swift_pdfkit", "pymupdf"], help="Leitor PDF a usar no clone.")
    parser.add_argument("--report", action="store_true", help="Gera relatorio Markdown em data/homologacao.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source_db = Path(args.db).expanduser().resolve()
    file_path = Path(args.file).expanduser().resolve()
    layout_code = str(args.layout or "").strip().upper() or "BRADESCO_EXTRATO"
    if not source_db.exists():
        print(f"FALHA: banco nao encontrado: {source_db}")
        return 2
    if not file_path.exists():
        print(f"FALHA: arquivo nao encontrado: {file_path}")
        return 2

    comparison = compare_pdf_upload_providers(
        file_path.name,
        file_path.read_bytes(),
        import_kind="statement",
        layout_code=layout_code,
    )
    if not comparison.get("ok"):
        print("FALHA: a portabilidade do arquivo nao foi aprovada para piloto controlado.")
        print(comparison.get("difference") or comparison.get("error") or "sem detalhe")
        return 1

    SANDBOX_DIR.mkdir(parents=True, exist_ok=True)
    sandbox_db = SANDBOX_DIR / f"{source_db.stem}_piloto_{layout_code.lower()}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    shutil.copy2(source_db, sandbox_db)

    previous_provider = os.environ.get("POWER_CHURCH_PDF_PROVIDER")
    os.environ["POWER_CHURCH_PDF_PROVIDER"] = args.provider
    db = PowerChurchDB(sandbox_db)
    try:
        lot_id = db.create_statement_lot_from_upload(file_path.name, file_path.read_bytes(), layout_code=layout_code)
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
        rows = conn.execute(
            """
            SELECT COALESCE(review_status, '') AS review_status, COUNT(*) AS total
              FROM extrato_movimentos
             WHERE lote_id = ? AND ativo = 1
             GROUP BY COALESCE(review_status, '')
             ORDER BY total DESC, review_status
            """,
            (lot_id,),
        ).fetchall()
        pending_count = int(
            scalar(
                conn,
                """
                SELECT COUNT(*)
                  FROM extrato_movimentos
                 WHERE lote_id = ?
                   AND ativo = 1
                   AND COALESCE(review_status, '') IN ('pendente', 'revisar_pessoa', 'revisar_destinacao', 'revisar_duplicidade', 'classificacao_pendente')
                """,
                (lot_id,),
            )
            or 0
        )
        duplicate_count = int(
            scalar(
                conn,
                """
                SELECT COUNT(*)
                  FROM extrato_movimentos
                 WHERE lote_id = ? AND ativo = 1 AND COALESCE(review_status, '') = 'revisar_duplicidade'
                """,
                (lot_id,),
            )
            or 0
        )
        imported_count = int(
            scalar(
                conn,
                """
                SELECT COUNT(*)
                  FROM extrato_movimentos
                 WHERE lote_id = ? AND ativo = 1 AND imported_contribution_id IS NOT NULL
                """,
                (lot_id,),
            )
            or 0
        )
    review_counts = {str(row["review_status"] or "sem_status"): int(row["total"] or 0) for row in rows}
    summary = {
        "bank": lot["banco"] if lot else "",
        "file_name": lot["nome_arquivo"] if lot else "",
        "period_start": lot["periodo_inicio"] if lot else "",
        "period_end": lot["periodo_fim"] if lot else "",
        "movement_count": int(lot["total_movimentos"] or 0) if lot else 0,
        "total_value_fmt": money(lot["total_valor"] if lot else 0),
        "lot_status": lot["status"] if lot else "",
        "pending_count": pending_count,
        "duplicate_count": duplicate_count,
        "imported_count": imported_count,
        "review_counts": review_counts,
    }

    print(f"Clone criado: {sandbox_db}")
    print(f"Lote piloto: #{lot_id} | banco={summary['bank']} | movimentos={summary['movement_count']} | total={summary['total_value_fmt']}")
    print(f"Status do lote: {summary['lot_status']} | pendentes={pending_count} | duplicidades={duplicate_count} | importados={imported_count}")
    for key, value in review_counts.items():
        print(f"- {key}: {value}")
    if args.report:
        report = write_report(
            source_db=source_db,
            sandbox_db=sandbox_db,
            file_path=file_path,
            layout_code=layout_code,
            comparison=comparison,
            lot_id=lot_id,
            summary=summary,
        )
        print(f"Relatorio: {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

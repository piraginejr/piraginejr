from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DJANGO_ROOT = ROOT / "power_church_django"
REPORT_DIR = ROOT / "data" / "homologacao"
DEFAULT_FILE = Path("/Users/piraginejr/Library/Mobile Documents/com~apple~CloudDocs/Downloads/Downloads/BRADESCO_MAIO26.pdf")

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(DJANGO_ROOT) not in sys.path:
    sys.path.insert(0, str(DJANGO_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "power_church_site.settings")

import django  # noqa: E402

django.setup()

from power_church_django.apps.contributions.models import ReceiptDispatch  # noqa: E402
from power_church_django.services.legacy import legacy_db_path  # noqa: E402
from power_church_django.services.legacy_bank_write import (  # noqa: E402
    LegacyBankWriteError,
    create_statement_lot_from_upload,
    prepare_statement_lot_for_audit,
)


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def money(value: object) -> str:
    total = float(value or 0)
    return f"R$ {total:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def write_report(
    *,
    file_path: Path,
    layout_code: str,
    lot_id: int,
    lot_summary: dict[str, Any],
    prepare_result: dict[str, Any],
    dispatch_rows: list[ReceiptDispatch],
) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    target = REPORT_DIR / f"lancamento_extrato_operacional_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    lines = [
        "# Lancamento Operacional de Extrato",
        "",
        f"Gerado em: {datetime.now().isoformat(timespec='seconds')}",
        "",
        f"- Arquivo: `{file_path}`",
        f"- Layout: `{layout_code}`",
        f"- Banco legado operacional: `{legacy_db_path()}`",
        "",
        "## Lote",
        "",
        f"- Lote criado: `#{lot_id}`",
        f"- Banco: {lot_summary['bank']}",
        f"- Arquivo gravado: {lot_summary['file_name']}",
        f"- Periodo: {lot_summary['period_start']} a {lot_summary['period_end']}",
        f"- Movimentos: {lot_summary['movement_count']}",
        f"- Total financeiro: {lot_summary['total_value_fmt']}",
        f"- Status do lote: {lot_summary['status']}",
        "",
        "## Review status",
        "",
        "| Status | Quantidade |",
        "| --- | ---: |",
    ]
    for key, value in lot_summary["review_counts"].items():
        lines.append(f"| {key} | {value} |")
    lines.extend(
        [
            "",
        "## Preparacao para auditoria e recibos automaticos",
        "",
        f"- Contribuicoes importadas/atualizadas no preparo: {int(prepare_result.get('importados', 0) or 0)}",
        f"- Status anterior do lote: {prepare_result.get('status_antes') or '-'}",
        f"- Status final do lote: {lot_summary['status']}",
        f"- Contribuicoes aptas a recibo: {int(prepare_result.get('auto_receipt_candidates', 0) or 0)}",
        f"- Recibos criados automaticamente: {int(prepare_result.get('auto_receipt_created', 0) or 0)}",
        f"- Recibos enviados: {int(prepare_result.get('auto_receipt_sent', 0) or 0)}",
        f"- Recibos enfileirados: {int(prepare_result.get('auto_receipt_queued', 0) or 0)}",
        f"- Recibos sem e-mail: {int(prepare_result.get('auto_receipt_without_email', 0) or 0)}",
        f"- Falhas de envio: {int(prepare_result.get('auto_receipt_failed', 0) or 0)}",
        ]
    )
    if prepare_result.get("auto_receipt_error"):
        lines.extend(
            [
                "",
                "## Erro de automacao",
                "",
                f"- {prepare_result['auto_receipt_error']}",
            ]
        )
    if dispatch_rows:
        lines.extend(
            [
                "",
                "## Filas/Envios gerados",
                "",
                "| Dispatch | Recibo | Destino | Status | Atualizado em |",
                "| ---: | --- | --- | --- | --- |",
            ]
        )
        for row in dispatch_rows:
            lines.append(
                f"| {int(row.pk or 0)} | {row.legacy_receipt_number or row.legacy_receipt_id} | {row.email_to or row.person_email or '-'} | {row.status} | {row.updated_at.strftime('%d/%m/%Y %H:%M:%S')} |"
            )
    target.write_text("\n".join(lines), encoding="utf-8")
    return target


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Importa um extrato na base operacional, lanca o financeiro e prepara o lote para auditoria com recibos automaticos do que ficou pronto.")
    parser.add_argument("--file", default=str(DEFAULT_FILE), help="PDF do extrato bancario.")
    parser.add_argument("--layout", default="BRADESCO_EXTRATO", help="Layout do extrato.")
    parser.add_argument(
        "--pdf-provider-mode",
        default="compare_pymupdf",
        choices=["swift_pdfkit", "compare_pymupdf", "pymupdf"],
        help="Modo do leitor PDF no fluxo Django.",
    )
    parser.add_argument("--report", action="store_true", help="Gera relatorio Markdown em data/homologacao.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    file_path = Path(args.file).expanduser().resolve()
    layout_code = str(args.layout or "").strip().upper() or "BRADESCO_EXTRATO"
    if not file_path.exists():
        print(f"FALHA: arquivo nao encontrado: {file_path}")
        return 2
    try:
        lot_id = create_statement_lot_from_upload(
            file_path.name,
            file_path.read_bytes(),
            layout_code=layout_code,
            pdf_provider_mode=args.pdf_provider_mode,
        )
        prepare_result = prepare_statement_lot_for_audit(lot_id, actor="lancamento_extrato_operacional")
    except LegacyBankWriteError as exc:
        print(f"FALHA: {exc}")
        return 1

    with connect(Path(legacy_db_path())) as conn:
        lot_row = conn.execute(
            """
            SELECT id, banco, nome_arquivo, periodo_inicio, periodo_fim, total_movimentos, total_valor, status
              FROM extrato_lotes
             WHERE id = ?
            """,
            (lot_id,),
        ).fetchone()
        review_rows = conn.execute(
            """
            SELECT COALESCE(review_status, '') AS review_status, COUNT(*) AS total
              FROM extrato_movimentos
             WHERE lote_id = ? AND ativo = 1
             GROUP BY COALESCE(review_status, '')
             ORDER BY total DESC, review_status
            """,
            (lot_id,),
        ).fetchall()
    dispatch_rows = list(
        ReceiptDispatch.objects.filter(pk__in=[int(value or 0) for value in prepare_result.get("auto_receipt_dispatch_ids", []) if int(value or 0)])
        .order_by("id")
    )
    lot_summary = {
        "bank": lot_row["banco"] if lot_row else "",
        "file_name": lot_row["nome_arquivo"] if lot_row else "",
        "period_start": lot_row["periodo_inicio"] if lot_row else "",
        "period_end": lot_row["periodo_fim"] if lot_row else "",
        "movement_count": int(lot_row["total_movimentos"] or 0) if lot_row else 0,
        "total_value_fmt": money(lot_row["total_valor"] if lot_row else 0),
        "status": lot_row["status"] if lot_row else "",
        "review_counts": {str(row["review_status"] or "sem_status"): int(row["total"] or 0) for row in review_rows},
    }
    print(
        "Lote operacional fechado: "
        f"#{lot_id} | banco={lot_summary['bank']} | movimentos={lot_summary['movement_count']} | "
        f"recibos={int(prepare_result.get('auto_receipt_created', 0) or 0)} | "
        f"enfileirados={int(prepare_result.get('auto_receipt_queued', 0) or 0)} | "
        f"sem_email={int(prepare_result.get('auto_receipt_without_email', 0) or 0)} | "
        f"falhas={int(prepare_result.get('auto_receipt_failed', 0) or 0)} | "
        f"status={lot_summary['status']}"
    )
    if args.report:
        report_path = write_report(
            file_path=file_path,
            layout_code=layout_code,
            lot_id=lot_id,
            lot_summary=lot_summary,
            prepare_result=prepare_result,
            dispatch_rows=dispatch_rows,
        )
        print(f"Relatorio: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

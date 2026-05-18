from __future__ import annotations

import argparse
import importlib.util
import shutil
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "power_church_demo.py"
DEFAULT_DB_PATH = ROOT / "data" / "power_church_membros_importado.db"
DEFAULT_REPORT_DIR = ROOT / "data" / "homologacao"


def load_app_module():
    root_text = str(ROOT)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    spec = importlib.util.spec_from_file_location("power_church_demo", APP_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def counter_samples(counter: Counter[tuple[object, ...]], limit: int = 12) -> list[tuple[int, tuple[object, ...]]]:
    return [(count, item) for item, count in counter.most_common(limit)]


def compare_against_pix_lot(app, db, pix_lot_id: int, statement_lot_id: int) -> dict[str, object]:
    pix_rows = db.conn.execute(
        """
        SELECT data_recebimento, valor, nome_origem, documento_mascarado
        FROM pix_movimentos
        WHERE lote_id = ? AND ativo = 1
        ORDER BY ordem_no_lote, id
        """,
        (pix_lot_id,),
    ).fetchall()
    statement_rows = db.conn.execute(
        """
        SELECT data_movimento, valor, nome_origem, bank_document, movement_kind, review_status
        FROM extrato_movimentos
        WHERE lote_id = ? AND ativo = 1
        ORDER BY ordem_no_lote, id
        """,
        (statement_lot_id,),
    ).fetchall()

    def pix_signature(row) -> tuple[object, ...]:
        return (
            str(row["data_recebimento"] or ""),
            round(float(row["valor"] or 0), 2),
            app.normalize_match_name(row["nome_origem"]),
        )

    def statement_signature(row) -> tuple[object, ...]:
        return (
            str(row["data_movimento"] or ""),
            round(float(row["valor"] or 0), 2),
            app.normalize_match_name(row["nome_origem"]),
        )

    pix_counter = Counter(pix_signature(row) for row in pix_rows)
    statement_counter = Counter(statement_signature(row) for row in statement_rows)
    matched_counter = pix_counter & statement_counter
    pix_only = pix_counter - statement_counter
    statement_only = statement_counter - pix_counter

    unmatched_statement_rows: list[dict[str, object]] = []
    temp_counter = Counter(statement_counter)
    for row in statement_rows:
        sig = statement_signature(row)
        if pix_counter[sig] > 0:
            pix_counter[sig] -= 1
            temp_counter[sig] -= 1
            continue
        unmatched_statement_rows.append(
            {
                "data": str(row["data_movimento"]),
                "valor": round(float(row["valor"] or 0), 2),
                "nome": str(row["nome_origem"] or ""),
                "documento": str(row["bank_document"] or ""),
                "canal": str(row["movement_kind"] or ""),
                "status": str(row["review_status"] or ""),
            }
        )

    unmatched_by_kind = Counter(str(row["canal"]) for row in unmatched_statement_rows)
    unmatched_by_status = Counter(str(row["status"]) for row in unmatched_statement_rows)

    return {
        "pix_count": len(pix_rows),
        "statement_count": len(statement_rows),
        "matched_exact": int(sum(matched_counter.values())),
        "pix_only_count": int(sum(pix_only.values())),
        "statement_only_count": int(sum(statement_only.values())),
        "pix_only_samples": counter_samples(pix_only),
        "statement_only_samples": counter_samples(statement_only),
        "statement_unmatched_rows": unmatched_statement_rows,
        "statement_unmatched_by_kind": unmatched_by_kind,
        "statement_unmatched_by_status": unmatched_by_status,
    }


def render_report(
    *,
    pdf_path: Path,
    db_copy_path: Path,
    pix_lot_id: int,
    statement_lot: dict[str, object],
    parsed_summary: dict[str, object],
    lot_counts: dict[str, int],
    financial_counts: dict[str, int],
    comparison: dict[str, object],
) -> str:
    unmatched_rows = comparison["statement_unmatched_rows"][:20]
    unmatched_lines = "\n".join(
        f"- {row['data']} | {row['canal']} | R$ {row['valor']:.2f} | {row['nome'] or '(sem nome)'} | {row['documento'] or '(sem documento)'} | {row['status']}"
        for row in unmatched_rows
    ) or "- Nenhum exemplo"
    by_kind_lines = "\n".join(
        f"- {kind}: {qty}"
        for kind, qty in comparison["statement_unmatched_by_kind"].most_common()
    ) or "- Nenhum"
    by_status_lines = "\n".join(
        f"- {status}: {qty}"
        for status, qty in comparison["statement_unmatched_by_status"].most_common()
    ) or "- Nenhum"
    parsed_kind_lines = "\n".join(
        f"- {kind}: {qty} lancamentos | R$ {parsed_summary['totals_by_kind'][kind]:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        for kind, qty in parsed_summary["counts_by_kind"].most_common()
    )
    recommendation = (
        "O extrato Sicoob se mostrou promissor como fonte canonica do mes, mas antes de substituir o lote PIX historico "
        "vale revisar os casos exclusivos do extrato e confirmar o tratamento de mesma titularidade."
    )
    return f"""# Homologacao: Extrato Sicoob x PIX historico

Gerado em: {datetime.now().strftime("%d/%m/%Y %H:%M:%S")}

Arquivo testado:

- {pdf_path}

Base de homologacao criada em:

- {db_copy_path}

Lote PIX de comparacao:

- PIX lote #{pix_lot_id}

Lote de extrato criado na copia:

- Extrato lote #{statement_lot['id']} ({statement_lot['layout_codigo']})

## Resumo do parser

- Banco: {statement_lot['banco']}
- Periodo: {statement_lot['periodo_inicio']} a {statement_lot['periodo_fim']}
- Entradas parseadas e ativas no lote: {statement_lot['total_movimentos']}
- Total do lote: R$ {float(statement_lot['total_valor'] or 0):,.2f}

Resumo por tipo:

{parsed_kind_lines}

## Saneamento do lote de extrato

- Revisar pessoa: {lot_counts.get('revisar_pessoa', 0)}
- Revisar destinacao: {lot_counts.get('revisar_destinacao', 0)}
- Revisar duplicidade: {lot_counts.get('revisar_duplicidade', 0)}
- Ignorados: {lot_counts.get('ignorado', 0)}
- Lancados financeiramente: {financial_counts.get('lancados', 0)}
- Sem associacao: {financial_counts.get('sem_associacao', 0)}

## Comparacao com o PIX historico

- Movimentos ativos no PIX lote #{pix_lot_id}: {comparison['pix_count']}
- Movimentos ativos no extrato homologado: {comparison['statement_count']}
- Correspondencias exatas por data + valor + nome: {comparison['matched_exact']}
- Exclusivos do PIX historico: {comparison['pix_only_count']}
- Exclusivos do extrato: {comparison['statement_only_count']}

Exclusivos do extrato por canal:

{by_kind_lines}

Exclusivos do extrato por status:

{by_status_lines}

Exemplos de movimentos exclusivos do extrato:

{unmatched_lines}

## Parecer

{recommendation}
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Homologa o Extrato Sicoob de recebimentos contra um lote PIX historico.")
    parser.add_argument("pdf_path", help="PDF do extrato de recebimentos do Sicoob")
    parser.add_argument("--pix-lot-id", type=int, default=2, help="ID do lote PIX historico para comparacao")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="Banco real a ser copiado para homologacao")
    parser.add_argument("--report", default="", help="Arquivo markdown de saida")
    parser.add_argument("--keep-copy", action="store_true", help="Mantem a copia da base apos a homologacao")
    args = parser.parse_args()

    app = load_app_module()
    pdf_path = Path(args.pdf_path).expanduser().resolve()
    db_path = Path(args.db).expanduser().resolve()
    report_path = Path(args.report).expanduser().resolve() if args.report else None

    if report_path is None:
        DEFAULT_REPORT_DIR.mkdir(parents=True, exist_ok=True)
        report_name = f"homologacao_extrato_sicoob_{pdf_path.stem.replace(' ', '_')}.md"
        report_path = DEFAULT_REPORT_DIR / report_name

    temp_dir = Path(tempfile.mkdtemp(prefix="power_church_homolog_sicoob_", dir="/private/tmp"))
    db_copy_path = temp_dir / db_path.name
    shutil.copy2(db_path, db_copy_path)

    original_ensure_financial = app.PowerChurchDB.ensure_statement_financial_entries
    original_refresh_status = app.PowerChurchDB.refresh_statement_lot_status

    # Homologacao foca primeiro na consistencia da leitura e do saneamento; o financeiro pesado fica fora desta etapa.
    app.PowerChurchDB.ensure_statement_financial_entries = lambda self, lot_id=0: 0
    app.PowerChurchDB.refresh_statement_lot_status = lambda self, lot_id: "auditando"

    db = app.PowerChurchDB(db_copy_path)
    try:
        lot_id = db.create_statement_lot_from_upload(pdf_path.name, pdf_path.read_bytes(), layout_code="SICOOB_RECEBIMENTOS")
        statement_lot_row = db.get_statement_lot(lot_id)
        if statement_lot_row is None:
            raise RuntimeError("Lote de homologacao do extrato Sicoob nao foi criado.")
        statement_lot = dict(statement_lot_row)
        counts = db.statement_lot_review_counts(lot_id)
        financial = db.statement_lot_financial_counts(lot_id)

        parsed_entries = db.conn.execute(
            """
            SELECT movement_kind, COUNT(*) AS qty, COALESCE(SUM(valor), 0) AS total
            FROM extrato_movimentos
            WHERE lote_id = ? AND ativo = 1
            GROUP BY movement_kind
            ORDER BY qty DESC
            """,
            (lot_id,),
        ).fetchall()
        parsed_summary = {
            "counts_by_kind": Counter({str(row["movement_kind"]): int(row["qty"]) for row in parsed_entries}),
            "totals_by_kind": defaultdict(float, {str(row["movement_kind"]): round(float(row["total"] or 0), 2) for row in parsed_entries}),
        }

        comparison = compare_against_pix_lot(app, db, args.pix_lot_id, lot_id)
        report = render_report(
            pdf_path=pdf_path,
            db_copy_path=db_copy_path,
            pix_lot_id=args.pix_lot_id,
            statement_lot=statement_lot,
            parsed_summary=parsed_summary,
            lot_counts=counts,
            financial_counts=financial,
            comparison=comparison,
        )
        report_path.write_text(report, encoding="utf-8")
    finally:
        db.close()
        app.PowerChurchDB.ensure_statement_financial_entries = original_ensure_financial
        app.PowerChurchDB.refresh_statement_lot_status = original_refresh_status
        if not args.keep_copy:
            # Mantemos a pasta do relatorio, mas a copia do banco so fica quando explicitamente pedida.
            try:
                db_copy_path.unlink(missing_ok=True)
                temp_dir.rmdir()
            except OSError:
                pass

    print(f"Relatorio gerado em: {report_path}")
    if args.keep_copy:
        print(f"Copia da base preservada em: {db_copy_path}")


if __name__ == "__main__":
    main()

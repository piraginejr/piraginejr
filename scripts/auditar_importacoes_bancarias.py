from __future__ import annotations

import argparse
import importlib.util
import sqlite3
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "power_church_membros_importado.db"
APP_PATH = ROOT / "power_church_demo.py"


def load_app_module():
    spec = importlib.util.spec_from_file_location("power_church_demo", APP_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def compare_text(left: object, right: object) -> bool:
    return str(left or "").strip() == str(right or "").strip()


def compare_float(left: object, right: object) -> bool:
    try:
        return round(float(left or 0), 2) == round(float(right or 0), 2)
    except (TypeError, ValueError):
        return False


def normalized_name(app, value: object) -> str:
    return app.normalize_match_name(value)


def pix_signature(app, payload: dict[str, object]) -> tuple[object, ...]:
    return (
        str(payload.get("data") or ""),
        round(float(payload.get("valor") or 0), 2),
        normalized_name(app, payload.get("nome")),
    )


def statement_signature(app, payload: dict[str, object]) -> tuple[object, ...]:
    return (
        str(payload.get("data") or ""),
        round(float(payload.get("valor") or 0), 2),
        normalized_name(app, payload.get("nome")),
        str(payload.get("canal") or ""),
    )


def counter_samples(counter: Counter[tuple[object, ...]], limit: int = 8) -> list[dict[str, object]]:
    samples: list[dict[str, object]] = []
    for item, count in list(counter.items())[:limit]:
        samples.append({"count": count, "signature": item})
    return samples


def audit_pix_lots(conn: sqlite3.Connection, app) -> list[dict[str, object]]:
    lots = conn.execute(
        "SELECT id, nome_arquivo, caminho_arquivo, periodo_inicio, periodo_fim, total_movimentos, total_valor FROM pix_lotes ORDER BY id"
    ).fetchall()
    reports: list[dict[str, object]] = []
    for lot in lots:
        parsed = app.parse_sicoob_pix_pdf(Path(str(lot["caminho_arquivo"])))
        rows = conn.execute(
            """
            SELECT id, ordem_no_lote, data_recebimento, valor, nome_origem, documento_mascarado, codigo_centavos
            FROM pix_movimentos
            WHERE lote_id = ? AND ativo = 1
            ORDER BY ordem_no_lote, id
            """,
            (lot["id"],),
        ).fetchall()
        mismatches: list[dict[str, object]] = []
        parsed_by_order = {int(entry["order_in_file"]): entry for entry in parsed["entries"]}
        for row in rows:
            entry = parsed_by_order.get(int(row["ordem_no_lote"]))
            if entry is None:
                continue
            fields: list[str] = []
            if not compare_text(row["data_recebimento"], entry["received_on"]):
                fields.append("data")
            if not compare_float(row["valor"], entry["amount"]):
                fields.append("valor")
            if not compare_text(row["nome_origem"], entry["donor_name"]):
                fields.append("nome")
            if not compare_text(row["documento_mascarado"], entry["document_mask"]):
                fields.append("documento")
            if not compare_text(row["codigo_centavos"], entry["cent_code"]):
                fields.append("centavos")
            if fields:
                mismatches.append(
                    {
                        "movimento_id": row["id"],
                        "ordem": row["ordem_no_lote"],
                        "fields": fields,
                        "current": {
                            "data": row["data_recebimento"],
                            "valor": row["valor"],
                            "nome": row["nome_origem"],
                            "documento": row["documento_mascarado"],
                            "centavos": row["codigo_centavos"],
                        },
                        "parsed": {
                            "data": entry["received_on"],
                            "valor": entry["amount"],
                            "nome": entry["donor_name"],
                            "documento": entry["document_mask"],
                            "centavos": entry["cent_code"],
                        },
                    }
                )
        stored_signatures = Counter(
            pix_signature(
                app,
                {
                    "data": row["data_recebimento"],
                    "valor": row["valor"],
                    "nome": row["nome_origem"],
                },
            )
            for row in rows
        )
        parsed_signatures = Counter(
            pix_signature(
                app,
                {
                    "data": entry["received_on"],
                    "valor": entry["amount"],
                    "nome": entry["donor_name"],
                },
            )
            for entry in parsed["entries"]
        )
        only_stored = stored_signatures - parsed_signatures
        only_parsed = parsed_signatures - stored_signatures
        reports.append(
            {
                "kind": "pix",
                "lot_id": lot["id"],
                "file": lot["nome_arquivo"],
                "stored_total_movements": lot["total_movimentos"],
                "parsed_total_movements": len(parsed["entries"]),
                "stored_total_value": round(float(lot["total_valor"] or 0), 2),
                "parsed_total_value": round(sum(float(entry["amount"]) for entry in parsed["entries"]), 2),
                "mismatches": mismatches,
                "exact_signature_matches": int(sum((stored_signatures & parsed_signatures).values())),
                "stored_only_count": int(sum(only_stored.values())),
                "parsed_only_count": int(sum(only_parsed.values())),
                "stored_only_samples": counter_samples(only_stored),
                "parsed_only_samples": counter_samples(only_parsed),
            }
        )
    return reports


def audit_statement_lots(conn: sqlite3.Connection, app) -> list[dict[str, object]]:
    lots = conn.execute(
        "SELECT id, banco, nome_arquivo, caminho_arquivo, periodo_inicio, periodo_fim, total_movimentos, total_valor FROM extrato_lotes ORDER BY id"
    ).fetchall()
    reports: list[dict[str, object]] = []
    for lot in lots:
        parsed_raw = app.parse_bradesco_statement_pdf(Path(str(lot["caminho_arquivo"])))
        parsed_entries = [
            entry
            for entry in parsed_raw["entries"]
            if not app.statement_should_skip_bradesco_entry(entry)
        ]
        rows = conn.execute(
            """
            SELECT id, ordem_no_lote, data_movimento, valor, nome_origem, bank_document, movement_kind, codigo_centavos
            FROM extrato_movimentos
            WHERE lote_id = ? AND ativo = 1
            ORDER BY ordem_no_lote, id
            """,
            (lot["id"],),
        ).fetchall()
        mismatches: list[dict[str, object]] = []
        parsed_by_order = {int(entry["order_in_file"]): entry for entry in parsed_entries}
        for row in rows:
            entry = parsed_by_order.get(int(row["ordem_no_lote"]))
            if entry is None:
                continue
            fields: list[str] = []
            if not compare_text(row["data_movimento"], entry["received_on"]):
                fields.append("data")
            if not compare_float(row["valor"], entry["amount"]):
                fields.append("valor")
            if not compare_text(row["nome_origem"], entry["source_name"]):
                fields.append("nome")
            if not compare_text(row["bank_document"], entry["bank_document"]):
                fields.append("docto")
            if not compare_text(row["movement_kind"], entry["movement_kind"]):
                fields.append("canal")
            if not compare_text(row["codigo_centavos"], app.pix_code_from_amount(float(entry["amount"]))):
                fields.append("centavos")
            if fields:
                mismatches.append(
                    {
                        "movimento_id": row["id"],
                        "ordem": row["ordem_no_lote"],
                        "fields": fields,
                        "current": {
                            "data": row["data_movimento"],
                            "valor": row["valor"],
                            "nome": row["nome_origem"],
                            "docto": row["bank_document"],
                            "canal": row["movement_kind"],
                            "centavos": row["codigo_centavos"],
                        },
                        "parsed": {
                            "data": entry["received_on"],
                            "valor": entry["amount"],
                            "nome": entry["source_name"],
                            "docto": entry["bank_document"],
                            "canal": entry["movement_kind"],
                            "centavos": app.pix_code_from_amount(float(entry["amount"])),
                        },
                    }
                )
        stored_signatures = Counter(
            statement_signature(
                app,
                {
                    "data": row["data_movimento"],
                    "valor": row["valor"],
                    "nome": row["nome_origem"],
                    "canal": row["movement_kind"],
                },
            )
            for row in rows
        )
        parsed_signatures = Counter(
            statement_signature(
                app,
                {
                    "data": entry["received_on"],
                    "valor": entry["amount"],
                    "nome": entry["source_name"],
                    "canal": entry["movement_kind"],
                },
            )
            for entry in parsed_entries
        )
        only_stored = stored_signatures - parsed_signatures
        only_parsed = parsed_signatures - stored_signatures
        reports.append(
            {
                "kind": "extrato",
                "lot_id": lot["id"],
                "file": lot["nome_arquivo"],
                "stored_total_movements": lot["total_movimentos"],
                "parsed_total_movements": len(parsed_entries),
                "stored_total_value": round(float(lot["total_valor"] or 0), 2),
                "parsed_total_value": round(sum(float(entry["amount"]) for entry in parsed_entries), 2),
                "mismatches": mismatches,
                "exact_signature_matches": int(sum((stored_signatures & parsed_signatures).values())),
                "stored_only_count": int(sum(only_stored.values())),
                "parsed_only_count": int(sum(only_parsed.values())),
                "stored_only_samples": counter_samples(only_stored),
                "parsed_only_samples": counter_samples(only_parsed),
            }
        )
    return reports


def render_report(reports: list[dict[str, object]]) -> str:
    lines = ["# Auditoria De Importacoes Bancarias", ""]
    total_mismatch_lots = 0
    total_mismatches = 0
    for report in reports:
        has_total_diff = (
            int(report["stored_total_movements"]) != int(report["parsed_total_movements"])
            or round(float(report["stored_total_value"]), 2) != round(float(report["parsed_total_value"]), 2)
        )
        mismatch_count = len(report["mismatches"])
        if has_total_diff or mismatch_count:
            total_mismatch_lots += 1
        total_mismatches += mismatch_count
    lines.extend(
        [
            f"- Lotes auditados: `{len(reports)}`",
            f"- Lotes com divergencia: `{total_mismatch_lots}`",
            f"- Divergencias de movimento: `{total_mismatches}`",
            "",
        ]
    )
    for report in reports:
        lines.append(f"## {report['kind'].upper()} Lote #{report['lot_id']} - {report['file']}")
        lines.append("")
        lines.append(f"- Movimentos armazenados: `{report['stored_total_movements']}`")
        lines.append(f"- Movimentos reparseados: `{report['parsed_total_movements']}`")
        lines.append(f"- Valor armazenado: `R$ {report['stored_total_value']:.2f}`")
        lines.append(f"- Valor reparseado: `R$ {report['parsed_total_value']:.2f}`")
        lines.append(f"- Assinaturas exatas coincidentes: `{report['exact_signature_matches']}`")
        lines.append(f"- Somente no banco atual: `{report['stored_only_count']}`")
        lines.append(f"- Somente no reparse: `{report['parsed_only_count']}`")
        lines.append(f"- Divergencias de movimento: `{len(report['mismatches'])}`")
        lines.append("")
        for item in report["mismatches"][:25]:
            lines.append(
                f"- Ordem `{item['ordem']}` / movimento `{item['movimento_id']}` / campos {', '.join(item['fields'])}: "
                f"atual=({item['current']}) | reparse=({item['parsed']})"
            )
        if len(report["mismatches"]) > 25:
            lines.append(f"- ... mais `{len(report['mismatches']) - 25}` divergencia(s)")
        if report["stored_only_samples"]:
            lines.append("- Amostras somente no banco atual:")
            for sample in report["stored_only_samples"]:
                lines.append(f"  - `{sample['count']}x` {sample['signature']}")
        if report["parsed_only_samples"]:
            lines.append("- Amostras somente no reparse:")
            for sample in report["parsed_only_samples"]:
                lines.append(f"  - `{sample['count']}x` {sample['signature']}")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DB_PATH), help="Arquivo de banco SQLite a auditar")
    parser.add_argument("--output", default="", help="Arquivo markdown de saida")
    args = parser.parse_args()

    app = load_app_module()
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        reports = []
        reports.extend(audit_pix_lots(conn, app))
        reports.extend(audit_statement_lots(conn, app))
        markdown = render_report(reports)
        if args.output:
            Path(args.output).write_text(markdown, encoding="utf-8")
        print(markdown)
    finally:
        conn.close()


if __name__ == "__main__":
    main()

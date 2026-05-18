from __future__ import annotations

import argparse
import importlib.util
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "power_church_demo.py"
DB_PATH = ROOT / "data" / "power_church_membros_importado.db"


def load_app_module():
    spec = importlib.util.spec_from_file_location("power_church_demo", APP_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def parse_money_br(value: str) -> float:
    return float(
        value.replace(".", "")
        .replace(",", ".")
        .replace("*", "")
        .replace("C", "")
        .replace("D", "")
    )


def build_sicoob_receipts_entries(module, pdf_path: Path) -> list[dict[str, object]]:
    pages = module.extract_pdf_pages_with_swift(pdf_path)
    main_re = re.compile(r"^(\d{2}/\d{2})\s+(.+?)\s+([\d.]+,\d{2}[CD\*])$")
    excluded = {"SALDO DO DIA", "SALDO ANTERIOR", "SALDO BLOQ.ANTERIOR"}
    blocks: list[dict[str, object]] = []
    for page_number, page in enumerate(pages, start=1):
        current: dict[str, object] | None = None
        for raw in page.splitlines():
            line = raw.strip()
            match = main_re.match(line)
            if match:
                if current:
                    blocks.append(current)
                current = {
                    "page": page_number,
                    "date_br": match.group(1),
                    "history": match.group(2).strip(),
                    "value_text": match.group(3).strip(),
                    "details": [],
                }
                continue
            if current and line:
                current["details"].append(line)
        if current:
            blocks.append(current)
    entries: list[dict[str, object]] = []
    for block in blocks:
        history = str(block["history"])
        if history in excluded:
            continue
        details = list(block["details"])
        amount = parse_money_br(str(block["value_text"]))
        document = ""
        for line in details:
            if (
                re.search(r"[*0-9]{3}\.[*0-9]{3}\.[*0-9]{3}-\**[*0-9]{2}", line)
                or re.search(r"\d{3}\.\d{3}\.\d{3}-\d{2}", line)
                or re.search(r"\d{2}\.\d{3}\.\d{3}\s?\d{4}-\d{2}", line)
            ):
                document = line.strip()
                break
        source_name = ""
        if history == "PIX RECEB.OUTRA IF":
            for line in details:
                if line in {"Recebimento Pix", "DOC.: Pix"}:
                    continue
                if line.startswith("DOC.:") or line == document:
                    continue
                if line.lower().startswith(("dizimo", "oferta", "missoes", "miss", "aluguel", "gratidao")):
                    continue
                source_name = line.strip()
                break
            if not source_name:
                for line in details:
                    if line and line != document and not line.startswith("DOC.:") and line not in {"Recebimento Pix"}:
                        source_name = line.strip()
                        break
        elif history in {"CRED.TR.CT.INTERCRE", "TRANSF.RECEB-PIX SI"}:
            for line in details:
                if line.startswith("REM.:"):
                    source_name = line.replace("REM.:", "", 1).strip()
                    break
            if not source_name:
                for line in details:
                    if line not in {"Transferencia Pix", "Transferência Pix"} and line != document and not line.startswith("DOC.:"):
                        source_name = line.strip()
                        break
        elif history == "CRÉD.TED-STR":
            for line in details:
                if line.startswith("CODIGO TED:") or line.startswith("DOC.:") or line == "00000000000000":
                    continue
                if line == document:
                    continue
                source_name = line.strip()
                break
        entries.append(
            {
                "page": int(block["page"]),
                "date_br": str(block["date_br"]),
                "history": history,
                "amount": round(amount, 2),
                "document": document,
                "source_name_raw": source_name,
                "source_name_norm": module.normalize_match_name(source_name),
                "details": details,
            }
        )
    return entries


def compare_with_pix_lot(module, db_path: Path, lot_id: int, entries: list[dict[str, object]]) -> dict[str, object]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT valor, nome_origem, documento_mascarado
        FROM pix_movimentos
        WHERE lote_id = ? AND ativo = 1
        """,
        (lot_id,),
    ).fetchall()
    conn.close()
    exact_counter = Counter(
        (
            round(float(row["valor"]), 2),
            module.normalize_match_name(row["nome_origem"]),
        )
        for row in rows
    )
    value_counter = Counter(round(float(row["valor"]), 2) for row in rows)
    matched = 0
    unmatched: list[dict[str, object]] = []
    unmatched_by_history = Counter()
    for entry in entries:
        key = (round(float(entry["amount"]), 2), str(entry["source_name_norm"]))
        if entry["source_name_norm"] and exact_counter[key] > 0:
            exact_counter[key] -= 1
            matched += 1
            continue
        if not entry["source_name_norm"] and value_counter[round(float(entry["amount"]), 2)] > 0:
            value_counter[round(float(entry["amount"]), 2)] -= 1
            matched += 1
            continue
        unmatched.append(entry)
        unmatched_by_history[str(entry["history"])] += 1
    return {
        "lot_rows": len(rows),
        "matched": matched,
        "unmatched": unmatched,
        "unmatched_by_history": unmatched_by_history,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Analisa extrato de recebimentos do Sicoob e compara com um lote PIX.")
    parser.add_argument("pdf_path", help="Caminho do PDF do extrato de recebimentos")
    parser.add_argument("--lot-id", type=int, default=0, help="Lote PIX para comparar")
    args = parser.parse_args()

    module = load_app_module()
    pdf_path = Path(args.pdf_path)
    entries = build_sicoob_receipts_entries(module, pdf_path)
    total = round(sum(float(entry["amount"]) for entry in entries), 2)
    by_history = Counter(str(entry["history"]) for entry in entries)
    by_history_total: dict[str, float] = defaultdict(float)
    for entry in entries:
        by_history_total[str(entry["history"])] += float(entry["amount"])

    print(f"PDF: {pdf_path}")
    print(f"Entradas parseadas: {len(entries)}")
    print(f"Total parseado: {total:.2f}")
    print("\nResumo por historico:")
    for history, qty in sorted(by_history.items(), key=lambda item: (-item[1], item[0])):
        print(f"- {history}: {qty} lancamentos | R$ {by_history_total[history]:.2f}")

    if args.lot_id:
        comparison = compare_with_pix_lot(module, DB_PATH, args.lot_id, entries)
        print(f"\nComparacao com lote PIX #{args.lot_id}:")
        print(f"- Movimentos ativos no lote: {comparison['lot_rows']}")
        print(f"- Casos com correspondente aproximado no lote: {comparison['matched']}")
        print(f"- Casos sem correspondente aproximado: {len(comparison['unmatched'])}")
        print("\nNao casados por historico:")
        for history, qty in comparison["unmatched_by_history"].most_common():
            print(f"- {history}: {qty}")
        print("\nExemplos sem correspondente:")
        for entry in comparison["unmatched"][:20]:
            print(
                f"- {entry['date_br']} | {entry['history']} | R$ {float(entry['amount']):.2f} | "
                f"{entry['source_name_raw'] or '(sem nome)'} | {entry['document'] or '(sem documento)'}"
            )


if __name__ == "__main__":
    main()

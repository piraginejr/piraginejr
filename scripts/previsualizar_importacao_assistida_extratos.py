from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "data" / "homologacao"
DEFAULT_DB = ROOT / "data" / "power_church_membros_importado.db"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from power_church_core.bank_lots import StatementEntryPlan, statement_entry_plan, statement_operational_duplicate_key, statement_operational_identity
from power_church_core.bank_parsers import parse_statement_pdf_by_layout, statement_should_skip_entry
from power_church_core.banking import statement_layout_is_santander
from power_church_core.normalization import moneyless_int, normalize_match_name, normalize_query
from power_church_django.services.legacy_bank_write import LegacyBankWriteError, compare_pdf_upload_providers


@dataclass(frozen=True)
class PreviewCase:
    bank: str
    layout: str
    path: Path


@contextmanager
def pdf_provider(provider: str):
    previous = os.environ.get("POWER_CHURCH_PDF_PROVIDER")
    os.environ["POWER_CHURCH_PDF_PROVIDER"] = provider
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("POWER_CHURCH_PDF_PROVIDER", None)
        else:
            os.environ["POWER_CHURCH_PDF_PROVIDER"] = previous


def money(value: float) -> str:
    return f"R$ {float(value or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def amount_cents(value: object) -> int:
    try:
        return int(round(float(value or 0) * 100))
    except (TypeError, ValueError):
        return 0


def entry_identity(layout_code: object, source_name: object, document_value: object) -> tuple[str, str]:
    return statement_operational_identity(layout_code, source_name, document_value)


def operational_key(
    layout_code: object,
    bank_name: object,
    received_on: object,
    amount: object,
    source_name: object,
    document_value: object,
) -> tuple[str, str, int, str, str]:
    return statement_operational_duplicate_key(layout_code, bank_name, received_on, amount, source_name, document_value)


def connect_readonly(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
        (table_name,),
    ).fetchone()
    return row is not None


def load_existing_occurrences(conn: sqlite3.Connection) -> tuple[dict[tuple[str, str, int, str, str], list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    by_key: dict[tuple[str, str, int, str, str], list[dict[str, Any]]] = defaultdict(list)
    by_signature: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if table_exists(conn, "extrato_movimentos"):
        rows = conn.execute(
            """
            SELECT m.id, m.lote_id, m.data_movimento, m.valor, m.nome_origem, m.nome_normalizado,
                   m.bank_document, m.signature_global, m.review_status, m.imported_contribution_id,
                   l.banco, l.layout_codigo, l.nome_arquivo
              FROM extrato_movimentos m
              JOIN extrato_lotes l ON l.id = m.lote_id
             WHERE m.ativo = 1
               AND COALESCE(m.review_status, '') <> 'ignorado'
            """
        ).fetchall()
        for row in rows:
            item = {
                "origem": "extrato",
                "id": moneyless_int(row["id"]),
                "lote_id": moneyless_int(row["lote_id"]),
                "arquivo": row["nome_arquivo"] or "",
                "status": row["review_status"] or "",
                "contribuicao_id": moneyless_int(row["imported_contribution_id"]),
            }
            key = operational_key(
                row["layout_codigo"],
                row["banco"],
                row["data_movimento"],
                row["valor"],
                row["nome_origem"] or row["nome_normalizado"],
                row["bank_document"],
            )
            if key[-1]:
                by_key[key].append(item)
            signature = normalize_query(row["signature_global"])
            if signature:
                by_signature[signature].append(item)
    if table_exists(conn, "pix_movimentos"):
        rows = conn.execute(
            """
            SELECT pm.id, pm.lote_id, pm.data_recebimento, pm.valor, pm.nome_origem, pm.nome_normalizado,
                   pm.documento_mascarado, pm.signature_global, pm.review_status, pm.imported_contribution_id,
                   pl.nome_arquivo
              FROM pix_movimentos pm
              JOIN pix_lotes pl ON pl.id = pm.lote_id
             WHERE pm.ativo = 1
               AND COALESCE(pm.review_status, '') <> 'ignorado'
            """
        ).fetchall()
        for row in rows:
            item = {
                "origem": "pix",
                "id": moneyless_int(row["id"]),
                "lote_id": moneyless_int(row["lote_id"]),
                "arquivo": row["nome_arquivo"] or "",
                "status": row["review_status"] or "",
                "contribuicao_id": moneyless_int(row["imported_contribution_id"]),
            }
            key = operational_key(
                "SICOOB_RECEBIMENTOS",
                "Sicoob",
                row["data_recebimento"],
                row["valor"],
                row["nome_origem"] or row["nome_normalizado"],
                row["documento_mascarado"],
            )
            if key[-1]:
                by_key[key].append(item)
            signature = normalize_query(row["signature_global"])
            if signature:
                by_signature[signature].append(item)
    return by_key, by_signature


def load_cent_rules(conn: sqlite3.Connection) -> set[str]:
    if not table_exists(conn, "pix_centavo_regras"):
        return set()
    rows = conn.execute(
        "SELECT codigo_centavos FROM pix_centavo_regras WHERE COALESCE(ativo, 1) = 1"
    ).fetchall()
    return {normalize_query(row["codigo_centavos"]) for row in rows if normalize_query(row["codigo_centavos"])}


def load_org_name(conn: sqlite3.Connection) -> str:
    if not table_exists(conn, "organizacoes"):
        return ""
    row = conn.execute("SELECT nome FROM organizacoes ORDER BY id LIMIT 1").fetchone()
    return normalize_query(row["nome"] if row else "")


def same_org_source(source_name: object, org_name: object) -> bool:
    source_norm = normalize_match_name(source_name)
    org_norm = normalize_match_name(org_name)
    if not source_norm or not org_norm:
        return False
    if source_norm == org_norm:
        return True
    if source_norm.startswith(org_norm) or org_norm.startswith(source_norm):
        return True
    return source_norm.split()[:3] == org_norm.split()[:3] and len(source_norm.split()) >= 3


def parse_with_portable(case: PreviewCase) -> dict[str, Any]:
    with pdf_provider("pymupdf"):
        return parse_statement_pdf_by_layout(case.layout, case.path)


def build_plans(parsed: Mapping[str, Any], requested_layout: str) -> tuple[str, list[StatementEntryPlan], int]:
    stored_layout = normalize_query(parsed.get("layout_code") or requested_layout).upper() or requested_layout
    skipped = 0
    plans: list[StatementEntryPlan] = []
    for entry in parsed.get("entries") or []:
        if statement_should_skip_entry(stored_layout, entry):
            skipped += 1
            continue
        plans.append(statement_entry_plan(stored_layout, entry))
    return stored_layout, plans, skipped


def compare_providers(case: PreviewCase) -> dict[str, Any]:
    try:
        return compare_pdf_upload_providers(
            case.path.name,
            case.path.read_bytes(),
            import_kind="statement",
            layout_code=case.layout,
        )
    except LegacyBankWriteError as exc:
        return {"ok": False, "error": str(exc)}


def existing_file_lot(conn: sqlite3.Connection, layout_code: str, file_hash: str) -> dict[str, Any] | None:
    if not table_exists(conn, "extrato_lotes"):
        return None
    if statement_layout_is_santander(layout_code):
        row = conn.execute(
            """
            SELECT id, nome_arquivo, layout_codigo
              FROM extrato_lotes
             WHERE hash_arquivo = ? AND layout_codigo LIKE 'SANTANDER%'
             ORDER BY id DESC
             LIMIT 1
            """,
            (file_hash,),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT id, nome_arquivo, layout_codigo
              FROM extrato_lotes
             WHERE hash_arquivo = ? AND layout_codigo = ?
             ORDER BY id DESC
             LIMIT 1
            """,
            (file_hash, layout_code),
        ).fetchone()
    return dict(row) if row else None


def preview_case(
    conn: sqlite3.Connection,
    case: PreviewCase,
    existing_by_key: dict[tuple[str, str, int, str, str], list[dict[str, Any]]],
    existing_by_signature: dict[str, list[dict[str, Any]]],
    cent_rules: set[str],
    org_name: str,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "bank": case.bank,
        "layout_requested": case.layout,
        "path": str(case.path),
        "file": case.path.name,
        "ok": False,
        "errors": [],
        "warnings": [],
    }
    if not case.path.exists():
        result["errors"].append("Arquivo nao encontrado.")
        return result
    comparison = compare_providers(case)
    result["provider_comparison"] = comparison
    if not comparison.get("ok"):
        result["warnings"].append(f"Comparacao Swift/PyMuPDF nao aprovada: {comparison.get('difference') or comparison.get('error')}")
    try:
        parsed = parse_with_portable(case)
    except Exception as exc:
        result["errors"].append(f"Falha no parser PyMuPDF: {exc}")
        return result
    stored_layout, plans, skipped = build_plans(parsed, case.layout)
    result.update(
        {
            "ok": True,
            "bank_name": normalize_query(parsed.get("bank_name")) or case.bank,
            "layout_code": stored_layout,
            "statement_kind": normalize_query(parsed.get("statement_kind")),
            "file_hash": normalize_query(parsed.get("file_hash")),
            "period_start": normalize_query(parsed.get("period_start")),
            "period_end": normalize_query(parsed.get("period_end")),
            "entries_count": len(plans),
            "skipped_count": skipped,
            "total": round(sum(float(plan.amount or 0) for plan in plans), 2),
        }
    )
    existing_lot = existing_file_lot(conn, stored_layout, result["file_hash"])
    if existing_lot:
        result["warnings"].append(
            f"Arquivo identico ja carregado no lote #{existing_lot['id']} ({existing_lot['nome_arquivo']})."
        )
    new_occurrences: Counter[tuple[str, str, int, str, str]] = Counter()
    status_counts: Counter[str] = Counter()
    identity_counts: Counter[str] = Counter()
    movement_counts: Counter[str] = Counter()
    rows: list[dict[str, Any]] = []
    for plan in plans:
        key = operational_key(stored_layout, parsed.get("bank_name") or case.bank, plan.received_on, plan.amount, plan.source_name, plan.bank_document)
        new_occurrences[key] += 1
        occurrence = new_occurrences[key]
        exact_matches = existing_by_signature.get(plan.signature_global, [])
        operational_matches = existing_by_key.get(key, [])
        duplicated = bool(exact_matches) or (key[-1] and occurrence <= len(operational_matches))
        identity_kind, identity_value = entry_identity(stored_layout, plan.source_name, plan.bank_document)
        identity_counts[identity_kind] += 1
        movement_counts[plan.movement_kind or "-"] += 1
        risks: list[str] = []
        if amount_cents(plan.amount) <= 0:
            risks.append("valor <= 0")
        if identity_kind == "sem_identidade":
            risks.append("sem nome/documento")
        if same_org_source(plan.source_name, org_name):
            risks.append("possivel mesma titularidade")
        if plan.cent_code in cent_rules:
            risks.append(f"centavos especiais {plan.cent_code}")
        if duplicated:
            risks.append("duplicidade provavel")
        status = "duplicidade" if duplicated else "revisao" if risks else "novo"
        status_counts[status] += 1
        rows.append(
            {
                "status": status,
                "risks": risks,
                "date": plan.received_on,
                "amount": plan.amount,
                "amount_fmt": money(plan.amount),
                "identity_kind": identity_kind,
                "identity_value": identity_value,
                "name": plan.source_name,
                "document": plan.bank_document,
                "movement_kind": plan.movement_kind,
                "page": plan.page_number,
                "order": plan.order_in_file,
                "exact_matches": exact_matches,
                "operational_matches": operational_matches[:3] if duplicated else [],
                "raw_text": plan.raw_text,
            }
        )
    result["status_counts"] = dict(status_counts)
    result["identity_counts"] = dict(identity_counts)
    result["movement_counts"] = dict(movement_counts)
    result["rows"] = rows
    result["risk_rows"] = [row for row in rows if row["risks"]]
    result["duplicate_rows"] = [row for row in rows if row["status"] == "duplicidade"]
    result["zero_rows"] = [row for row in rows if amount_cents(row["amount"]) <= 0]
    result["blank_identity_rows"] = [row for row in rows if row["identity_kind"] == "sem_identidade"]
    return result


def render_matches(matches: Iterable[Mapping[str, Any]]) -> str:
    parts = []
    for match in matches:
        target = f"{match.get('origem')} #{match.get('id')} lote #{match.get('lote_id')}"
        arquivo = normalize_query(match.get("arquivo"))
        if arquivo:
            target += f" ({arquivo})"
        if match.get("contribuicao_id"):
            target += f" -> contribuicao #{match.get('contribuicao_id')}"
        parts.append(target)
    return "; ".join(parts) or "-"


def write_report(results: list[dict[str, Any]]) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    target = REPORT_DIR / f"pre_importacao_assistida_extratos_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    lines = [
        "# Previa Assistida De Importacao De Extratos",
        "",
        f"Gerado em: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "Esta rotina nao grava lotes nem movimentos. Ela compara leitores PDF e cruza os lancamentos contra movimentos ja existentes para reduzir risco de duplicidade entre extrato parcial e consolidado.",
        "",
        "## Ordem Sugerida",
        "",
        "1. Sicoob: primeiro, porque valida duplicidade contra PIX/extrato ja importado.",
        "2. Bradesco: segundo, porque exige conferencia de nomes em linhas adjacentes e quebras de pagina.",
        "3. Santander: terceiro, porque depende principalmente de CPF/CNPJ sem nome no extrato.",
        "",
    ]
    for result in results:
        lines.extend(
            [
                f"## {result['bank']}",
                "",
                f"- Arquivo: `{result['file']}`",
                f"- Layout solicitado: `{result['layout_requested']}`",
            ]
        )
        if not result.get("ok"):
            lines.append(f"- Resultado: FALHA ({'; '.join(result.get('errors') or [])})")
            lines.append("")
            continue
        comparison = result.get("provider_comparison") or {}
        lines.extend(
            [
                f"- Layout detectado: `{result['layout_code']}`",
                f"- Periodo: {result['period_start']} a {result['period_end']}",
                f"- Lancamentos uteis: {result['entries_count']} (ignorados pelo parser: {result['skipped_count']})",
                f"- Total lido: {money(result['total'])}",
                f"- Comparacao Swift x PyMuPDF: {'OK' if comparison.get('ok') else 'ATENCAO'}",
            ]
        )
        if comparison.get("difference"):
            lines.append(f"- Primeiro desvio: {comparison['difference']}")
        for warning in result.get("warnings") or []:
            lines.append(f"- Aviso: {warning}")
        status_counts = result.get("status_counts") or {}
        identity_counts = result.get("identity_counts") or {}
        lines.extend(
            [
                "",
                "| Indicador | Quantidade |",
                "| --- | ---: |",
                f"| Novos aparentes | {status_counts.get('novo', 0)} |",
                f"| Revisao por risco | {status_counts.get('revisao', 0)} |",
                f"| Duplicidade provavel | {status_counts.get('duplicidade', 0)} |",
                f"| Valor <= 0 | {len(result.get('zero_rows') or [])} |",
                f"| Sem nome/documento | {len(result.get('blank_identity_rows') or [])} |",
                f"| Identidade por nome | {identity_counts.get('nome', 0)} |",
                f"| Identidade por documento | {identity_counts.get('documento', 0)} |",
                "",
            ]
        )
        risk_rows = result.get("risk_rows") or []
        if risk_rows:
            lines.extend(["### Lancamentos Para Conferencia", "", "| Status | Data | Valor | Identidade | Riscos | Duplicidade encontrada |", "| --- | --- | ---: | --- | --- | --- |"])
            for row in risk_rows[:80]:
                identity = normalize_query(row["name"]) or normalize_query(row["document"]) or "-"
                matches = row.get("exact_matches") or row.get("operational_matches") or []
                lines.append(
                    f"| {row['status']} | {row['date']} | {row['amount_fmt']} | {identity} | {', '.join(row['risks']) or '-'} | {render_matches(matches)} |"
                )
            if len(risk_rows) > 80:
                lines.append(f"| ... | ... | ... | ... | mais {len(risk_rows) - 80} lancamento(s) | ... |")
            lines.append("")
        rows = result.get("rows") or []
        lines.extend(["### Amostra Da Leitura", "", "| Ordem | Data | Valor | Nome/documento | Tipo | Pagina |", "| ---: | --- | ---: | --- | --- | ---: |"])
        for row in rows[:20]:
            identity = normalize_query(row["name"]) or normalize_query(row["document"]) or "-"
            lines.append(
                f"| {row['order']} | {row['date']} | {row['amount_fmt']} | {identity} | {row['movement_kind'] or '-'} | {row['page']} |"
            )
        if len(rows) > 20:
            lines.append(f"| ... | ... | ... | mais {len(rows) - 20} lancamento(s) | ... | ... |")
        lines.append("")
    target.write_text("\n".join(lines), encoding="utf-8")
    return target


def default_cases() -> list[PreviewCase]:
    base = Path("/Users/piraginejr/Library/Mobile Documents/com~apple~CloudDocs/Downloads/Downloads")
    return [
        PreviewCase("Sicoob", "SICOOB_CONTA_CORRENTE", base / "sicoob_01a11.pdf"),
        PreviewCase("Bradesco", "BRADESCO_EXTRATO", base / "bradesco_01a11.pdf"),
        PreviewCase("Santander", "SANTANDER_AUTO", base / "santander_01a11.pdf"),
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Previsualiza importacao assistida de extratos sem gravar no banco.")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Caminho do SQLite operacional.")
    parser.add_argument("--file", action="append", default=[], help="Caso no formato Banco:LAYOUT:/caminho/arquivo.pdf")
    parser.add_argument("--report", action="store_true", help="Gera relatorio Markdown em data/homologacao.")
    return parser.parse_args()


def cases_from_args(values: list[str]) -> list[PreviewCase]:
    if not values:
        return default_cases()
    cases: list[PreviewCase] = []
    for value in values:
        try:
            bank, layout, path = value.split(":", 2)
        except ValueError as exc:
            raise SystemExit(f"--file invalido: {value}. Use Banco:LAYOUT:/caminho/arquivo.pdf") from exc
        cases.append(PreviewCase(bank=bank, layout=layout.upper(), path=Path(path)))
    return cases


def main() -> int:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"FALHA: banco nao encontrado: {db_path}")
        return 2
    cases = cases_from_args(args.file)
    with connect_readonly(db_path) as conn:
        existing_by_key, existing_by_signature = load_existing_occurrences(conn)
        cent_rules = load_cent_rules(conn)
        org_name = load_org_name(conn)
        results = [
            preview_case(conn, case, existing_by_key, existing_by_signature, cent_rules, org_name)
            for case in cases
        ]
    for result in results:
        if not result.get("ok"):
            print(f"- FALHA: {result['bank']} ({'; '.join(result.get('errors') or [])})")
            continue
        status_counts = result.get("status_counts") or {}
        comparison = result.get("provider_comparison") or {}
        print(
            "- {bank}: {count} lanc., {total}, periodo {start} a {end}, "
            "novos {new}, revisao {review}, duplicidade {dup}, Swift/PyMuPDF {cmp}".format(
                bank=result["bank"],
                count=result["entries_count"],
                total=money(result["total"]),
                start=result["period_start"],
                end=result["period_end"],
                new=status_counts.get("novo", 0),
                review=status_counts.get("revisao", 0),
                dup=status_counts.get("duplicidade", 0),
                cmp="OK" if comparison.get("ok") else "ATENCAO",
            )
        )
    if args.report:
        report_path = write_report(results)
        print(f"Relatorio: {report_path}")
    failed = [result for result in results if not result.get("ok")]
    hard_risks = [
        result
        for result in results
        if result.get("ok") and (result.get("zero_rows") or not (result.get("provider_comparison") or {}).get("ok"))
    ]
    return 1 if failed or hard_risks else 0


if __name__ == "__main__":
    raise SystemExit(main())

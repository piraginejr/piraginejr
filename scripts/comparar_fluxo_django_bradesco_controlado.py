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
DJANGO_DIR = ROOT / "power_church_django"
BRADESCO_FILE = Path("/Users/piraginejr/Library/Mobile Documents/com~apple~CloudDocs/Downloads/Downloads/BRADESCO_MAIO26.pdf")
LAYOUT_CODE = "BRADESCO_EXTRATO"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(DJANGO_DIR) not in sys.path:
    sys.path.insert(0, str(DJANGO_DIR))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "power_church_site.settings")

import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402
from django.contrib.auth import get_user_model  # noqa: E402
from django.core.files.uploadedfile import SimpleUploadedFile  # noqa: E402
from django.test import Client  # noqa: E402

from power_church_demo import PowerChurchDB  # noqa: E402


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


def summarize_lot(db_path: Path, lot_id: int) -> dict[str, Any]:
    with connect(db_path) as conn:
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
        review_counts = {str(row["review_status"] or "sem_status"): int(row["total"] or 0) for row in rows}
        imported_count = int(
            scalar(
                conn,
                "SELECT COUNT(*) FROM extrato_movimentos WHERE lote_id = ? AND ativo = 1 AND imported_contribution_id IS NOT NULL",
                (lot_id,),
            )
            or 0
        )
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
                "SELECT COUNT(*) FROM extrato_movimentos WHERE lote_id = ? AND ativo = 1 AND COALESCE(review_status, '') = 'revisar_duplicidade'",
                (lot_id,),
            )
            or 0
        )
        duplicate_probe = conn.execute(
            """
            SELECT ordem_no_lote, data_movimento, valor, nome_origem, review_status, duplicate_movement_id, duplicate_contribution_id
              FROM extrato_movimentos
             WHERE lote_id = ? AND ordem_no_lote = 3
            """,
            (lot_id,),
        ).fetchone()
        cent_probe = conn.execute(
            """
            SELECT ordem_no_lote, data_movimento, valor, nome_origem, review_status, codigo_centavos, imported_contribution_id
              FROM extrato_movimentos
             WHERE lote_id = ? AND ordem_no_lote = 41
            """,
            (lot_id,),
        ).fetchone()
    return {
        "bank": lot["banco"] if lot else "",
        "file_name": lot["nome_arquivo"] if lot else "",
        "period_start": lot["periodo_inicio"] if lot else "",
        "period_end": lot["periodo_fim"] if lot else "",
        "movement_count": int(lot["total_movimentos"] or 0) if lot else 0,
        "total_value": float(lot["total_valor"] or 0) if lot else 0.0,
        "total_value_fmt": money(lot["total_valor"] if lot else 0),
        "lot_status": lot["status"] if lot else "",
        "review_counts": review_counts,
        "imported_count": imported_count,
        "pending_count": pending_count,
        "duplicate_count": duplicate_count,
        "duplicate_probe": dict(duplicate_probe) if duplicate_probe else None,
        "cent_probe": dict(cent_probe) if cent_probe else None,
    }


def compare_summaries(controlled: dict[str, Any], django_summary: dict[str, Any]) -> list[Check]:
    checks = [
        Check("Banco do lote", "OK" if controlled["bank"] == django_summary["bank"] else "FALHA", f"controlado={controlled['bank']!r} django={django_summary['bank']!r}"),
        Check("Arquivo do lote", "OK" if controlled["file_name"] == django_summary["file_name"] else "FALHA", f"controlado={controlled['file_name']!r} django={django_summary['file_name']!r}"),
        Check("Periodo inicial", "OK" if controlled["period_start"] == django_summary["period_start"] else "FALHA", f"controlado={controlled['period_start']!r} django={django_summary['period_start']!r}"),
        Check("Periodo final", "OK" if controlled["period_end"] == django_summary["period_end"] else "FALHA", f"controlado={controlled['period_end']!r} django={django_summary['period_end']!r}"),
        Check("Quantidade de movimentos", "OK" if controlled["movement_count"] == django_summary["movement_count"] else "FALHA", f"controlado={controlled['movement_count']} django={django_summary['movement_count']}"),
        Check("Total do lote", "OK" if approx_equal(controlled["total_value"], django_summary["total_value"]) else "FALHA", f"controlado={controlled['total_value_fmt']} django={django_summary['total_value_fmt']}"),
        Check("Status do lote", "OK" if controlled["lot_status"] == django_summary["lot_status"] else "FALHA", f"controlado={controlled['lot_status']!r} django={django_summary['lot_status']!r}"),
        Check("Distribuicao review_status", "OK" if controlled["review_counts"] == django_summary["review_counts"] else "FALHA", f"controlado={controlled['review_counts']} django={django_summary['review_counts']}"),
        Check("Quantidade importada", "OK" if controlled["imported_count"] == django_summary["imported_count"] else "FALHA", f"controlado={controlled['imported_count']} django={django_summary['imported_count']}"),
        Check("Pendencias", "OK" if controlled["pending_count"] == django_summary["pending_count"] else "FALHA", f"controlado={controlled['pending_count']} django={django_summary['pending_count']}"),
        Check("Duplicidades", "OK" if controlled["duplicate_count"] == django_summary["duplicate_count"] else "FALHA", f"controlado={controlled['duplicate_count']} django={django_summary['duplicate_count']}"),
        Check("Sentinela duplicidade Ronaldo", "OK" if controlled["duplicate_probe"] == django_summary["duplicate_probe"] else "FALHA", f"controlado={controlled['duplicate_probe']} django={django_summary['duplicate_probe']}"),
        Check("Sentinela centavos Alessandro", "OK" if controlled["cent_probe"] == django_summary["cent_probe"] else "FALHA", f"controlado={controlled['cent_probe']} django={django_summary['cent_probe']}"),
    ]
    return checks


def write_report(source_db: Path, controlled_db: Path, django_db: Path, checks: list[Check], controlled: dict[str, Any], django_summary: dict[str, Any]) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    target = REPORT_DIR / f"comparacao_fluxo_django_bradesco_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    lines = [
        "# Comparacao Fluxo Django Atual X Piloto Controlado Bradesco",
        "",
        f"Gerado em: {datetime.now().isoformat(timespec='seconds')}",
        f"Banco operacional origem: `{source_db}`",
        f"Clone piloto controlado: `{controlled_db}`",
        f"Clone fluxo Django: `{django_db}`",
        f"Arquivo: `{BRADESCO_FILE}`",
        "",
        "| Checagem | Status | Detalhe |",
        "| --- | --- | --- |",
    ]
    for check in checks:
        lines.append(f"| {check.name} | {check.status} | {check.detail} |")
    lines.extend(
        [
            "",
            "## Resumo dos dois lotes",
            "",
            f"- Controlado: movimentos={controlled['movement_count']} total={controlled['total_value_fmt']} status={controlled['lot_status']}",
            f"- Django: movimentos={django_summary['movement_count']} total={django_summary['total_value_fmt']} status={django_summary['lot_status']}",
        ]
    )
    target.write_text("\n".join(lines), encoding="utf-8")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="Compara o fluxo Django atual com o piloto controlado Bradesco em bancos clone.")
    parser.add_argument("--db", default=str(DB_PATH), help="Banco operacional SQLite de origem.")
    parser.add_argument("--report", action="store_true", help="Gera relatorio Markdown em data/homologacao.")
    args = parser.parse_args()

    source_db = Path(args.db).expanduser().resolve()
    if not source_db.exists():
        print(f"FALHA: banco nao encontrado: {source_db}")
        return 2
    if not BRADESCO_FILE.exists():
        print(f"FALHA: arquivo nao encontrado: {BRADESCO_FILE}")
        return 2

    SANDBOX_DIR.mkdir(parents=True, exist_ok=True)
    controlled_db = SANDBOX_DIR / f"{source_db.stem}_compare_controlled_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    django_db = SANDBOX_DIR / f"{source_db.stem}_compare_django_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    shutil.copy2(source_db, controlled_db)
    shutil.copy2(source_db, django_db)

    previous_provider = os.environ.get("POWER_CHURCH_PDF_PROVIDER")

    os.environ["POWER_CHURCH_PDF_PROVIDER"] = "pymupdf"
    controlled = None
    try:
        db = PowerChurchDB(controlled_db)
        try:
            controlled_lot_id = db.create_statement_lot_from_upload(BRADESCO_FILE.name, BRADESCO_FILE.read_bytes(), layout_code=LAYOUT_CODE)
        finally:
            db.close()
        controlled = summarize_lot(controlled_db, controlled_lot_id)

        settings.POWER_CHURCH_LEGACY_DB_PATH = str(django_db)
        if "testserver" not in settings.ALLOWED_HOSTS:
            settings.ALLOWED_HOSTS.append("testserver")
        client = Client()
        user_model = get_user_model()
        user = (
            user_model.objects.filter(is_active=True, is_superuser=True).order_by("id").first()
            or user_model.objects.filter(is_active=True, is_staff=True).order_by("id").first()
            or user_model.objects.filter(is_active=True).order_by("id").first()
        )
        if user is None:
            user = user_model.objects.create_user(username=f"codex_compare_{datetime.now().strftime('%H%M%S')}", password="temporary-pass")
        client.force_login(user)
        upload = SimpleUploadedFile(BRADESCO_FILE.name, BRADESCO_FILE.read_bytes(), content_type="application/pdf")
        response = client.post(
            "/imports/",
            {
                "import_kind": "statement",
                "layout_code": LAYOUT_CODE,
                "pdf_provider_mode": "pymupdf",
                "extrato_pdf": upload,
            },
            follow=False,
        )
        if response.status_code not in {301, 302}:
            print(f"FALHA: POST do fluxo Django retornou status inesperado {response.status_code}")
            return 1
        with connect(django_db) as conn:
            django_lot_id = int(
                scalar(
                    conn,
                    """
                    SELECT id
                      FROM extrato_lotes
                     WHERE nome_arquivo = ?
                     ORDER BY id DESC
                     LIMIT 1
                    """,
                    (BRADESCO_FILE.name,),
                )
                or 0
            )
        if not django_lot_id:
            print("FALHA: fluxo Django nao criou lote no clone.")
            return 1
        django_summary = summarize_lot(django_db, django_lot_id)
    finally:
        if previous_provider is None:
            os.environ.pop("POWER_CHURCH_PDF_PROVIDER", None)
        else:
            os.environ["POWER_CHURCH_PDF_PROVIDER"] = previous_provider

    checks = compare_summaries(controlled, django_summary)
    for check in checks:
        print(f"{check.status}: {check.name} - {check.detail}")
    if args.report:
        report = write_report(source_db, controlled_db, django_db, checks, controlled, django_summary)
        print(f"Relatorio: {report}")
    return 1 if any(check.failed for check in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())

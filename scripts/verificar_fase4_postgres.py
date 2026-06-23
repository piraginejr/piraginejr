from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "data" / "homologacao"
DEFAULT_DB = ROOT / "data" / "power_church_membros_importado.db"
DEFAULT_ENV_FILE = ROOT / ".env.power_church_django.postgres.local"
DEFAULT_BASE_URL = "http://127.0.0.1:63621"
DJANGO_DIR = ROOT / "power_church_django"
DJANGO_VENV_PYTHON = DJANGO_DIR / ".venv" / "bin" / "python"

EXPECTED_LOTS = {
    "BRADESCO_MAIO26.pdf": {
        "bank": "Bradesco",
        "movements": 33,
        "total": Decimal("56015.61"),
        "status": "parcial",
        "pending_human": 5,
    },
    "SANTANDER_Maio2026.pdf": {
        "bank": "Santander",
        "movements": 55,
        "total": Decimal("16372.80"),
        "status": "parcial",
        "pending_human": 14,
    },
    "SICOOB_MAIO26.pdf": {
        "bank": "Sicoob",
        "movements": 612,
        "total": Decimal("433542.54"),
        "status": "parcial",
        "pending_human": 85,
    },
}


@dataclass
class Check:
    name: str
    status: str
    detail: str

    @property
    def failed(self) -> bool:
        return self.status == "FALHA"


def _load_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if value.startswith(("'", '"')) and value.endswith(("'", '"')) and len(value) >= 2:
            value = value[1:-1]
        env[key] = value
    return env


def _prefer_local_postgres_socket(env: dict[str, str]) -> dict[str, str]:
    host = str(env.get("POWER_CHURCH_POSTGRES_HOST") or "").strip()
    port = str(env.get("POWER_CHURCH_POSTGRES_PORT") or "5432").strip() or "5432"
    socket_path = Path(f"/tmp/.s.PGSQL.{port}")
    if host in {"127.0.0.1", "localhost"} and socket_path.exists():
        env["POWER_CHURCH_POSTGRES_HOST"] = "/tmp"
    return env


def _run_inside_venv() -> int | None:
    if not DJANGO_VENV_PYTHON.exists():
        return None
    if Path(sys.executable).resolve() == DJANGO_VENV_PYTHON.resolve():
        return None
    completed = os.spawnve(
        os.P_WAIT,
        str(DJANGO_VENV_PYTHON),
        [str(DJANGO_VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]],
        os.environ.copy(),
    )
    return int(completed)


def _sqlite_scalar(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> Any:
    row = conn.execute(sql, params).fetchone()
    return row[0] if row else None


def _human_pending_sqlite(conn: sqlite3.Connection, lot_id: int) -> int:
    return int(
        _sqlite_scalar(
            conn,
            """
            SELECT COUNT(*)
              FROM extrato_movimentos m
             WHERE m.lote_id = ?
               AND m.ativo = 1
               AND (
                    m.review_status IN ('pendente', 'revisar_pessoa', 'revisar_destinacao', 'classificacao_pendente')
                    OR (m.review_status = 'revisar_duplicidade' AND COALESCE(m.imported_contribution_id, 0) = 0)
               )
            """,
            (lot_id,),
        )
        or 0
    )


def _http_probe(url: str) -> tuple[bool, str]:
    request = Request(url, headers={"User-Agent": "PowerChurch-Fase4-Check/1.0"})
    try:
        with urlopen(request, timeout=8) as response:
            final_url = getattr(response, "geturl", lambda: url)()
            return True, f"HTTP {getattr(response, 'status', 200)} -> {final_url}"
    except HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except URLError as exc:
        return False, f"indisponivel: {exc}"


def _write_report(checks: list[Check]) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    target = REPORT_DIR / f"verificar_fase4_postgres_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    lines = [
        "# Verificacao Da Fase 4 Em PostgreSQL",
        "",
        f"Gerado em: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "| Checagem | Status | Detalhe |",
        "| --- | --- | --- |",
    ]
    for check in checks:
        lines.append(f"| {check.name} | {check.status} | {check.detail} |")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def _append_settings_checks(checks: list[Check]) -> None:
    settings_path = DJANGO_DIR / "power_church_site" / "settings.py"
    text = settings_path.read_text(encoding="utf-8", errors="replace") if settings_path.exists() else ""
    checks.append(
        Check(
            "Boot do Django sem AnyMail",
            "OK" if '"anymail",' not in text and '"anymail"' not in text else "FALHA",
            "Anymail removido do INSTALLED_APPS" if '"anymail",' not in text and '"anymail"' not in text else "Anymail ainda presente no settings.py",
        )
    )
    migration_candidates = sorted(
        (DJANGO_DIR / "apps" / "contributions" / "migrations").glob("0003_receipt*.py")
    )
    checks.append(
        Check(
            "Migration dos snapshots de recibo",
            "OK" if migration_candidates else "FALHA",
            str(migration_candidates[0].relative_to(ROOT)) if migration_candidates else "arquivo nao encontrado",
        )
    )


def _append_sqlite_checks(checks: list[Check], db_path: Path) -> dict[str, int]:
    lot_ids_by_file: dict[str, int] = {}
    if not db_path.exists():
        checks.append(Check("Banco legado SQLite", "FALHA", f"nao encontrado: {db_path}"))
        return lot_ids_by_file
    checks.append(Check("Banco legado SQLite", "OK", str(db_path)))
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        for filename, expected in EXPECTED_LOTS.items():
            row = conn.execute(
                """
                SELECT id, banco, nome_arquivo, total_movimentos, total_valor, status
                  FROM extrato_lotes
                 WHERE nome_arquivo = ?
                 ORDER BY id DESC
                 LIMIT 1
                """,
                (filename,),
            ).fetchone()
            if row is None:
                checks.append(Check(f"Lote legado {filename}", "FALHA", "lote nao encontrado"))
                continue
            lot_id = int(row["id"] or 0)
            lot_ids_by_file[filename] = lot_id
            status_ok = str(row["status"] or "") == str(expected["status"])
            movement_ok = int(row["total_movimentos"] or 0) == int(expected["movements"])
            total_ok = abs(Decimal(str(row["total_valor"] or 0)) - Decimal(str(expected["total"]))) <= Decimal("0.009")
            pending_human = _human_pending_sqlite(conn, lot_id)
            pending_ok = int(pending_human) == int(expected["pending_human"])
            checks.append(
                Check(
                    f"Lote legado {filename}",
                    "OK" if status_ok and movement_ok and total_ok and pending_ok else "FALHA",
                    (
                        f"id={lot_id} status={row['status']} movimentos={row['total_movimentos']} "
                        f"total={row['total_valor']} pendencias_humanas={pending_human}"
                    ),
                )
            )
        dispatch_without_email = int(
            _sqlite_scalar(
                conn,
                """
                SELECT COUNT(*)
                  FROM contribuicoes
                 WHERE ativo = 1
                   AND extrato_movimento_id IS NOT NULL
                   AND status_operacional = 'regular'
                """,
            )
            or 0
        )
        checks.append(
            Check(
                "Financeiro de extrato ativo no legado",
                "OK" if dispatch_without_email > 0 else "FALHA",
                f"{dispatch_without_email} contribuicao(oes) regulares originadas de extrato",
            )
        )
    finally:
        conn.close()
    return lot_ids_by_file


def _append_postgres_checks(checks: list[Check], env_file: Path, lot_ids_by_file: dict[str, int]) -> None:
    env = dict(os.environ)
    env.update(_prefer_local_postgres_socket(_load_env_file(env_file)))
    try:
        import psycopg
    except Exception as exc:  # pragma: no cover - best effort
        checks.append(Check("Driver psycopg", "FALHA", str(exc)))
        return
    db_name = str(env.get("POWER_CHURCH_POSTGRES_DB") or "").strip()
    if not db_name:
        checks.append(Check("Configuracao PostgreSQL", "FALHA", f"env sem POWER_CHURCH_POSTGRES_DB: {env_file}"))
        return
    try:
        conn = psycopg.connect(
            dbname=db_name,
            user=str(env.get("POWER_CHURCH_POSTGRES_USER") or "power_church"),
            password=str(env.get("POWER_CHURCH_POSTGRES_PASSWORD") or ""),
            host=str(env.get("POWER_CHURCH_POSTGRES_HOST") or "127.0.0.1"),
            port=str(env.get("POWER_CHURCH_POSTGRES_PORT") or "5432"),
        )
    except Exception as exc:
        checks.append(Check("Conexao PostgreSQL", "FALHA", str(exc)))
        return
    checks.append(Check("Conexao PostgreSQL", "OK", f"banco {db_name} acessivel"))
    try:
        with conn.cursor() as cur:
            required_tables = [
                "contributions_contributiontypesnapshot",
                "people_personsnapshot",
                "people_personcontributionsnapshot",
                "people_nativepeopleimportlot",
                "people_nativepeopleimportpending",
                "people_nativepeopleimportline",
                "contributions_receiptsnapshot",
                "contributions_receiptitemsnapshot",
                "contributions_receiptdispatch",
                "imports_statementimportpilotlot",
                "imports_statementimportpilotmovement",
            ]
            cur.execute(
                """
                SELECT tablename
                  FROM pg_tables
                 WHERE schemaname = 'public'
                   AND tablename = ANY(%s)
                """,
                (required_tables,),
            )
            existing = {row[0] for row in cur.fetchall()}
            missing = [name for name in required_tables if name not in existing]
            checks.append(
                Check(
                    "Tabelas Postgres da fase 4",
                    "OK" if not missing else "FALHA",
                    "todas presentes" if not missing else "faltando: " + ", ".join(missing),
                )
            )
            if "contributions_receiptsnapshot" in existing and "contributions_receiptitemsnapshot" in existing:
                cur.execute("SELECT COUNT(*) FROM contributions_receiptsnapshot")
                receipt_snapshots = int(cur.fetchone()[0] or 0)
                checks.append(
                    Check(
                        "Espelho de recibos em Postgres",
                        "OK" if receipt_snapshots > 0 else "FALHA",
                        f"{receipt_snapshots} recibo(s) espelhado(s)",
                    )
                )
                cur.execute(
                    """
                    SELECT COUNT(*)
                      FROM contributions_receiptdispatch d
                      LEFT JOIN contributions_receiptsnapshot r ON r.legacy_id = d.legacy_receipt_id
                     WHERE d.legacy_receipt_id IS NOT NULL
                       AND COALESCE(d.status, '') <> 'cancelado'
                       AND r.id IS NULL
                    """
                )
                dispatch_missing_snapshot = int(cur.fetchone()[0] or 0)
                checks.append(
                    Check(
                        "Fila de recibos coberta por snapshot",
                        "OK" if dispatch_missing_snapshot == 0 else "FALHA",
                        f"{dispatch_missing_snapshot} dispatch(es) sem ReceiptSnapshot correspondente",
                    )
                )
            else:
                checks.append(
                    Check(
                        "Espelho de recibos em Postgres",
                        "FALHA",
                        "tabelas de snapshot de recibo ainda nao aplicadas no Postgres local",
                    )
                )
                checks.append(
                    Check(
                        "Fila de recibos coberta por snapshot",
                        "FALHA",
                        "nao verificado porque contributions_receiptsnapshot ainda nao existe",
                    )
                )
            if {
                "people_nativepeopleimportlot",
                "people_nativepeopleimportpending",
                "people_nativepeopleimportline",
            }.issubset(existing):
                cur.execute("SELECT COUNT(*) FROM people_nativepeopleimportlot")
                people_import_lots = int(cur.fetchone()[0] or 0)
                cur.execute("SELECT COUNT(*) FROM people_nativepeopleimportpending")
                people_import_pendings = int(cur.fetchone()[0] or 0)
                cur.execute("SELECT COUNT(*) FROM people_nativepeopleimportline")
                people_import_lines = int(cur.fetchone()[0] or 0)
                checks.append(
                    Check(
                        "Importacao de pessoas espelhada no Postgres",
                        "OK" if people_import_lots > 0 else "FALHA",
                        f"lotes={people_import_lots} pendencias={people_import_pendings} linhas={people_import_lines}",
                    )
                )
            if "contributions_contributiontypesnapshot" in existing:
                cur.execute("SELECT COUNT(*), COUNT(*) FILTER (WHERE is_active) FROM contributions_contributiontypesnapshot")
                type_total, type_active = cur.fetchone()
                checks.append(
                    Check(
                        "Catalogo de tipos em Postgres",
                        "OK" if int(type_total or 0) > 0 else "FALHA",
                        f"{int(type_total or 0)} tipo(s), {int(type_active or 0)} ativo(s)",
                    )
                )
            else:
                checks.append(
                    Check(
                        "Catalogo de tipos em Postgres",
                        "FALHA",
                        "tabela contributions_contributiontypesnapshot ausente",
                    )
                )
            for filename, lot_id in lot_ids_by_file.items():
                cur.execute(
                    """
                    SELECT movement_count, total_value, lot_status, metadata
                      FROM imports_statementimportpilotlot
                     WHERE source_backend = 'django_web'
                       AND source_lot_id = %s
                     ORDER BY updated_at DESC, id DESC
                     LIMIT 1
                    """,
                    (lot_id,),
                )
                row = cur.fetchone()
                if row is None:
                    checks.append(Check(f"Snapshot Postgres {filename}", "FALHA", f"source_lot_id={lot_id} nao encontrado"))
                    continue
                movement_count = int(row[0] or 0)
                total_value = Decimal(str(row[1] or 0))
                lot_status = str(row[2] or "")
                metadata = row[3] or {}
                pending_human = int((metadata or {}).get("pending_human_count") or 0)
                expected = EXPECTED_LOTS[filename]
                ok = (
                    movement_count == int(expected["movements"])
                    and abs(total_value - Decimal(str(expected["total"]))) <= Decimal("0.009")
                    and pending_human == int(expected["pending_human"])
                )
                checks.append(
                    Check(
                        f"Snapshot Postgres {filename}",
                        "OK" if ok else "FALHA",
                        (
                            f"movimentos={movement_count} total={total_value} "
                            f"pendencias_humanas={pending_human} status_snapshot={lot_status}"
                        ),
                    )
                )
    finally:
        conn.close()


def _append_http_checks(checks: list[Check], base_url: str, lot_ids_by_file: dict[str, int]) -> None:
    probes = [
        ("Login publico", f"{base_url}/accounts/login/"),
        ("Central de importacoes", f"{base_url}/imports/"),
        ("Monitor da fila de recibos", f"{base_url}/receipts/queue/"),
    ]
    for filename, lot_id in lot_ids_by_file.items():
        probes.append((f"Lote {filename}", f"{base_url}/imports/statement/{lot_id}/?status=pendencias"))
    for name, url in probes:
        ok, detail = _http_probe(url)
        checks.append(Check(name, "OK" if ok else "FALHA", detail))


def main() -> int:
    rerun = _run_inside_venv()
    if rerun is not None:
        return rerun

    parser = argparse.ArgumentParser(description="Verifica o termino operacional da Fase 4 em PostgreSQL sem depender do manage.py check.")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Banco legado SQLite.")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE), help="Arquivo de ambiente do Postgres local.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="URL base do Django/Postgres local.")
    parser.add_argument("--skip-http", action="store_true", help="Nao valida rotas HTTP locais.")
    parser.add_argument("--report", action="store_true", help="Gera relatorio em Markdown.")
    args = parser.parse_args()

    os.environ.setdefault("PYTHONPYCACHEPREFIX", "/private/tmp/pycache_powerchurch")

    checks: list[Check] = []
    db_path = Path(args.db).expanduser().resolve()
    env_file = Path(args.env_file).expanduser().resolve()
    _append_settings_checks(checks)
    lot_ids_by_file = _append_sqlite_checks(checks, db_path)
    _append_postgres_checks(checks, env_file, lot_ids_by_file)
    if not args.skip_http:
        _append_http_checks(checks, str(args.base_url).rstrip("/"), lot_ids_by_file)

    if args.report:
        report = _write_report(checks)
        print(f"Relatorio: {report}")
    for check in checks:
        print(f"{check.name}={check.status}:{check.detail}")
    return 1 if any(check.failed for check in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())

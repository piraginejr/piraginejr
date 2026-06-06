from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DJANGO_DIR = ROOT / "power_church_django"
DJANGO_VENV_PYTHON = DJANGO_DIR / ".venv" / "bin" / "python"
REPORT_DIR = ROOT / "data" / "homologacao"
DEFAULT_DB = ROOT / "data" / "power_church_membros_importado.db"
DEFAULT_ENV_FILE = ROOT / ".env.power_church_django.postgres.local"
TARGET_FILES = (
    "BRADESCO_MAIO26.pdf",
    "SANTANDER_Maio2026.pdf",
    "SICOOB_MAIO26.pdf",
)


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
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if value.startswith(("\"", "'")) and value.endswith(("\"", "'")) and len(value) >= 2:
            value = value[1:-1]
        env[key.strip()] = value
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


def _legacy_scalar(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> Any:
    row = conn.execute(sql, params).fetchone()
    return row[0] if row else None


def _find_target_lots(db_path: Path) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            f"""
            SELECT id, banco, nome_arquivo, total_movimentos, total_valor, status
              FROM extrato_lotes
             WHERE nome_arquivo IN ({",".join("?" for _ in TARGET_FILES)})
             ORDER BY id ASC
            """,
            TARGET_FILES,
        ).fetchall()
    finally:
        conn.close()


def _human_pending_count(conn: sqlite3.Connection, lot_id: int) -> int:
    return int(
        _legacy_scalar(
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


def _write_report(checks: list[Check]) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    target = REPORT_DIR / f"verificar_snapshots_financeiros_postgres_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    lines = [
        "# Verificacao Dos Snapshots Financeiros Postgres",
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


def main() -> int:
    rerun = _run_inside_venv()
    if rerun is not None:
        return rerun

    parser = argparse.ArgumentParser(description="Verifica os snapshots financeiros oficiais da Etapa 3 no Postgres.")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Banco legado SQLite.")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE), help="Arquivo de ambiente do Postgres local.")
    parser.add_argument("--report", action="store_true", help="Gera relatorio em data/homologacao.")
    args = parser.parse_args()

    db_path = Path(args.db).expanduser().resolve()
    env_file = Path(args.env_file).expanduser().resolve()
    if env_file.exists():
        os.environ.update(_prefer_local_postgres_socket(_load_env_file(env_file)))
    os.environ.setdefault("PYTHONPYCACHEPREFIX", "/private/tmp/pycache_powerchurch")
    os.environ["POWER_CHURCH_LEGACY_DB_PATH"] = str(db_path)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "power_church_site.settings")
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(DJANGO_DIR))

    import django

    django.setup()

    from power_church_django.apps.imports.models import StatementImportPilotLot, StatementImportPilotMovement

    legacy_conn = sqlite3.connect(db_path)
    legacy_conn.row_factory = sqlite3.Row
    checks: list[Check] = []
    try:
        for lot in _find_target_lots(db_path):
            source_lot_id = int(lot["id"])
            snapshot = (
                StatementImportPilotLot.objects.filter(
                    source_backend=StatementImportPilotLot.SourceBackend.DJANGO_WEB,
                    source_lot_id=source_lot_id,
                )
                .order_by("-updated_at", "-id")
                .first()
            )
            if snapshot is None:
                checks.append(Check(f"Snapshot lote {source_lot_id}", "FALHA", "snapshot nao encontrado"))
                continue
            movement_count = StatementImportPilotMovement.objects.filter(lot=snapshot).count()
            pending_human = int((snapshot.metadata or {}).get("pending_human_count") or 0)
            expected_pending = _human_pending_count(legacy_conn, source_lot_id)
            expected_movements = int(lot["total_movimentos"] or 0)
            expected_total = float(lot["total_valor"] or 0)
            if movement_count != expected_movements:
                checks.append(
                    Check(
                        f"Snapshot lote {source_lot_id} movimentos",
                        "FALHA",
                        f"snapshot={movement_count} legado={expected_movements}",
                    )
                )
            else:
                checks.append(
                    Check(
                        f"Snapshot lote {source_lot_id} movimentos",
                        "OK",
                        f"{movement_count} movimento(s)",
                    )
                )
            if abs(float(snapshot.total_value) - expected_total) > 0.009:
                checks.append(
                    Check(
                        f"Snapshot lote {source_lot_id} total",
                        "FALHA",
                        f"snapshot={snapshot.total_value} legado={expected_total}",
                    )
                )
            else:
                checks.append(
                    Check(
                        f"Snapshot lote {source_lot_id} total",
                        "OK",
                        f"{snapshot.total_value}",
                    )
                )
            if pending_human != expected_pending:
                checks.append(
                    Check(
                        f"Snapshot lote {source_lot_id} pendencias humanas",
                        "FALHA",
                        f"snapshot={pending_human} legado={expected_pending}",
                    )
                )
            else:
                checks.append(
                    Check(
                        f"Snapshot lote {source_lot_id} pendencias humanas",
                        "OK",
                        f"{pending_human}",
                    )
                )
    finally:
        legacy_conn.close()

    if args.report:
        report = _write_report(checks)
        print(f"Relatorio: {report}")
    for check in checks:
        print(f"{check.name}={check.status}:{check.detail}")
    return 1 if any(check.failed for check in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())

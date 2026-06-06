from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from itertools import islice


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


def _find_target_lots(db_path: Path) -> list[tuple[int, str, str]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            f"""
            SELECT id, banco, nome_arquivo
              FROM extrato_lotes
             WHERE nome_arquivo IN ({",".join("?" for _ in TARGET_FILES)})
             ORDER BY id ASC
            """,
            TARGET_FILES,
        ).fetchall()
        return [(int(row["id"]), str(row["banco"] or ""), str(row["nome_arquivo"] or "")) for row in rows]
    finally:
        conn.close()


def _find_target_receipts(db_path: Path) -> tuple[list[int], list[int]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        receipt_rows = conn.execute(
            """
            SELECT id, pessoa_id
              FROM recibos
             ORDER BY id ASC
            """
        ).fetchall()
    finally:
        conn.close()
    receipt_ids = [int(row["id"] or 0) for row in receipt_rows if int(row["id"] or 0)]
    person_ids = sorted({int(row["pessoa_id"] or 0) for row in receipt_rows if int(row["pessoa_id"] or 0)})
    return receipt_ids, person_ids


def _chunked(values: list[int], size: int = 200):
    iterator = iter(values)
    while True:
        chunk = list(islice(iterator, size))
        if not chunk:
            break
        yield chunk


def _write_report(db_path: Path, synced: list[tuple[int, str, str]]) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    target = REPORT_DIR / f"snapshots_financeiros_postgres_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    lines = [
        "# Sincronizacao Dos Snapshots Financeiros Postgres",
        "",
        f"Gerado em: {datetime.now().isoformat(timespec='seconds')}",
        f"Banco legado: `{db_path}`",
        "",
        "| Lote | Banco | Arquivo |",
        "| --- | --- | --- |",
    ]
    for lot_id, bank_name, file_name in synced:
        lines.append(f"| {lot_id} | {bank_name} | {file_name} |")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def main() -> int:
    rerun = _run_inside_venv()
    if rerun is not None:
        return rerun

    parser = argparse.ArgumentParser(description="Sincroniza os snapshots financeiros oficiais da Etapa 3 no Postgres.")
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

    from power_church_django.apps.imports.services import sync_statement_lot_snapshot_from_legacy
    from power_church_django.services.postgres_people_sync import sync_contribution_type_snapshots
    from power_church_django.services.receipt_delivery import sync_receipt_snapshots

    target_lots = _find_target_lots(db_path)
    if not target_lots:
        print("Nenhum lote oficial encontrado para sincronizar.")
        return 1

    synced: list[tuple[int, str, str]] = []
    type_stats = sync_contribution_type_snapshots(db_path)
    print(
        "contribution_type_snapshot_sync=OK: "
        f"tipos={type_stats['postgres_contribution_types_total']} "
        f"ativos={type_stats['postgres_contribution_types_active']}"
    )
    for lot_id, bank_name, file_name in target_lots:
        sync_statement_lot_snapshot_from_legacy(lot_id)
        synced.append((lot_id, bank_name, file_name))
        print(f"snapshot_sync=OK: lote={lot_id} banco={bank_name} arquivo={file_name}")

    receipt_ids, person_ids = _find_target_receipts(db_path)
    synced_receipts = 0
    for chunk in _chunked(receipt_ids, size=150):
        synced_receipts += len(sync_receipt_snapshots(receipt_ids=chunk))
    for chunk in _chunked(person_ids, size=150):
        sync_receipt_snapshots(person_ids=chunk)
    print(f"receipt_snapshot_sync=OK: recibos={len(receipt_ids)} pessoas={len(person_ids)} sincronizados={synced_receipts}")

    if args.report:
        report = _write_report(db_path, synced)
        print(f"Relatorio: {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

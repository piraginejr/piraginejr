from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DJANGO_DIR = ROOT / "power_church_django"
DJANGO_VENV_PYTHON = DJANGO_DIR / ".venv" / "bin" / "python"
DEFAULT_DB = ROOT / "data" / "power_church_membros_importado.db"
DEFAULT_ENV_FILE = ROOT / ".env.power_church_django.postgres.local"


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
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
        if value.startswith(("'", '"')) and value.endswith(("'", '"')) and len(value) >= 2:
            value = value[1:-1]
        os.environ[key.strip()] = value


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


def _statement_lot_ids(db_path: Path) -> list[int]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT id FROM extrato_lotes ORDER BY id ASC").fetchall()
        return [int(row[0]) for row in rows if int(row[0] or 0)]
    finally:
        conn.close()


def main() -> int:
    rerun = _run_inside_venv()
    if rerun is not None:
        return rerun

    parser = argparse.ArgumentParser(
        description="Preenche o runtime Postgres com regras de centavos e lotes de extrato do legado."
    )
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Banco legado SQLite.")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE), help="Arquivo de ambiente alvo.")
    parser.add_argument("--skip-cent-rules", action="store_true", help="Nao recarrega as regras de centavos.")
    parser.add_argument("--skip-statement-lots", action="store_true", help="Nao recarrega os lotes de extrato.")
    args = parser.parse_args()

    db_path = Path(args.db).expanduser().resolve()
    env_file = Path(args.env_file).expanduser().resolve()
    _load_env_file(env_file)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "power_church_site.settings")
    os.environ.setdefault("PYTHONPYCACHEPREFIX", "/private/tmp/pycache_powerchurch")
    os.environ["POWER_CHURCH_LEGACY_DB_PATH"] = str(db_path)
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(DJANGO_DIR))

    import django

    django.setup()

    from power_church_django.apps.contributions.models import ContributionTypeSnapshot
    from power_church_django.apps.imports.models import CentRuleSnapshot
    from power_church_django.apps.imports.services import sync_statement_lot_snapshot_from_legacy
    from power_church_django.services.legacy import cent_rules_data

    if not args.skip_cent_rules:
        organization_id = (
            ContributionTypeSnapshot.objects.order_by("organization_id", "legacy_id")
            .values_list("organization_id", flat=True)
            .first()
            or 1
        )
        synced = 0
        active = 0
        for row in cent_rules_data().get("rules", []):
            CentRuleSnapshot.objects.update_or_create(
                legacy_id=int(row.get("id") or 0),
                defaults={
                    "organization_id": int(organization_id),
                    "cent_code": str(row.get("codigo") or "").zfill(2),
                    "destination_name": str(row.get("nome") or ""),
                    "contribution_type_legacy_id": int(row.get("tipo_id") or 0) or None,
                    "contribution_type_name": str(row.get("tipo_nome") or ""),
                    "campaign_name": str(row.get("campanha_nome") or ""),
                    "account_code": str(row.get("conta_codigo") or ""),
                    "account_name": str(row.get("conta_nome") or ""),
                    "is_active": bool(row.get("ativo")),
                },
            )
            synced += 1
            if bool(row.get("ativo")):
                active += 1
        print(f"cent_rules_sync=OK total={synced} active={active}")

    if not args.skip_statement_lots:
        lot_ids = _statement_lot_ids(db_path)
        synced_lots = 0
        for lot_id in lot_ids:
            sync_statement_lot_snapshot_from_legacy(lot_id)
            synced_lots += 1
        print(f"statement_lots_sync=OK total={synced_lots}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

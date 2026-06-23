from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DJANGO_DIR = ROOT / "power_church_django"
DJANGO_VENV_PYTHON = DJANGO_DIR / ".venv" / "bin" / "python"
REPORT_DIR = ROOT / "data" / "homologacao"
DEFAULT_ENV_FILE = ROOT / ".env.power_church_django.postgres.local"


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
        value = value.strip()
        if value.startswith(("'", '"')) and value.endswith(("'", '"')) and len(value) >= 2:
            value = value[1:-1]
        env[key.strip()] = value
    return env


def _merge_env_defaults(values: dict[str, str]) -> None:
    for key, value in values.items():
        current = os.environ.get(key)
        if current is None or not str(current).strip():
            os.environ[key] = value


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


def _write_report(limit: int, synced: int) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    target = REPORT_DIR / f"importacoes_pessoas_postgres_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    lines = [
        "# Sincronizacao Das Importacoes De Pessoas Para PostgreSQL",
        "",
        f"Gerado em: {datetime.now().isoformat(timespec='seconds')}",
        "",
        f"- Limite solicitado: `{limit}`",
        f"- Lotes sincronizados: `{synced}`",
        "",
        "Este relatorio confirma que os lotes recentes de importacao de pessoas foram materializados na camada nativa de leitura do Postgres.",
    ]
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def main() -> int:
    rerun = _run_inside_venv()
    if rerun is not None:
        return rerun

    parser = argparse.ArgumentParser(description="Sincroniza lotes de importacao de pessoas do legado para o Postgres.")
    parser.add_argument("--limit", type=int, default=20, help="Quantidade de lotes recentes para sincronizar.")
    parser.add_argument("--report", action="store_true", help="Gera relatorio em data/homologacao.")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE), help="Arquivo de ambiente do Postgres local.")
    args = parser.parse_args()

    env_file = Path(args.env_file).expanduser().resolve()
    if env_file.exists():
        _merge_env_defaults(_prefer_local_postgres_socket(_load_env_file(env_file)))
    os.environ.setdefault("PYTHONPYCACHEPREFIX", "/private/tmp/pycache_powerchurch")
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "power_church_site.settings")
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(DJANGO_DIR))

    import django

    django.setup()

    from power_church_django.services.people_import_backfill import backfill_people_import_lots_postgres

    synced = backfill_people_import_lots_postgres(limit=max(int(args.limit or 0), 1), line_limit=2000)
    print(f"people_import_sync=OK: lotes={synced}")
    if args.report:
        report = _write_report(limit=int(args.limit or 0), synced=synced)
        print(f"Relatorio: {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

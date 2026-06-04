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
DEFAULT_DB = ROOT / "data" / "power_church_membros_importado.db"
DEFAULT_ENV_FILE = ROOT / ".env.power_church_django.postgres.local"


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


def _write_report(db_path: Path, stats: dict[str, object]) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    target = REPORT_DIR / f"cadastro_postgres_sync_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    lines = [
        "# Sincronizacao Do Espelho Cadastral Postgres",
        "",
        f"Gerado em: {datetime.now().isoformat(timespec='seconds')}",
        f"Banco legado: `{db_path}`",
        "",
        "| Medida | Valor |",
        "| --- | --- |",
    ]
    for key, value in stats.items():
        lines.append(f"| {key} | {value} |")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def main() -> int:
    rerun = _run_inside_venv()
    if rerun is not None:
        return rerun

    parser = argparse.ArgumentParser(description="Sincroniza o espelho cadastral da Etapa 2 no PostgreSQL.")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Banco legado SQLite.")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE), help="Arquivo de ambiente do Postgres local.")
    parser.add_argument("--actor", default="django:etapa2_sync", help="Identificador de auditoria.")
    parser.add_argument("--report", action="store_true", help="Gera relatorio em data/homologacao.")
    args = parser.parse_args()

    env_file = Path(args.env_file).expanduser().resolve()
    if env_file.exists():
        os.environ.update(_load_env_file(env_file))
    os.environ.setdefault("PYTHONPYCACHEPREFIX", "/private/tmp/pycache_powerchurch")
    os.environ["POWER_CHURCH_LEGACY_DB_PATH"] = str(Path(args.db).expanduser().resolve())
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "power_church_site.settings")
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(DJANGO_DIR))

    import django

    django.setup()

    from power_church_django.services.postgres_people_sync import sync_people_snapshots

    stats = sync_people_snapshots(Path(args.db).expanduser().resolve(), actor=args.actor)
    for key, value in stats.items():
        print(f"{key}={value}")
    if args.report:
        report = _write_report(Path(args.db).expanduser().resolve(), stats)
        print(f"Relatorio: {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

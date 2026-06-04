from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DJANGO_DIR = ROOT / "power_church_django"
DJANGO_VENV_PYTHON = DJANGO_DIR / ".venv" / "bin" / "python"
REPORT_DIR = ROOT / "data" / "homologacao"
DEFAULT_DB = ROOT / "data" / "power_church_membros_importado.db"
DEFAULT_ENV_FILE = ROOT / ".env.power_church_django.postgres.local"


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


def _sample_persons():
    from power_church_django.apps.people.models import PersonAddressSnapshot, PersonContactSnapshot, PersonRelationshipSnapshot, PersonSnapshot

    samples = []
    for person in PersonSnapshot.objects.order_by("normalized_name", "legacy_id")[:3]:
        samples.append(
            f"{person.legacy_id}:{person.name} contatos={PersonContactSnapshot.objects.filter(person=person).count()} "
            f"enderecos={PersonAddressSnapshot.objects.filter(person=person).count()} "
            f"relacoes={PersonRelationshipSnapshot.objects.filter(person=person, is_active=True).count()}"
        )
    return " ; ".join(samples) if samples else "sem amostras"


def build_checks(db_path: Path) -> list[Check]:
    import django

    django.setup()

    from power_church_django.services.postgres_people_sync import compare_people_snapshots

    stats = compare_people_snapshots(db_path)
    checks = [
        Check(
            "Contagem total de pessoas",
            "OK" if stats["legacy_people_total"] == stats["postgres_people_total"] else "FALHA",
            f"legado={stats['legacy_people_total']} postgres={stats['postgres_people_total']}",
        ),
        Check(
            "Contagem ativa de pessoas",
            "OK" if stats["legacy_people_active"] == stats["postgres_people_active"] else "FALHA",
            f"legado={stats['legacy_people_active']} postgres={stats['postgres_people_active']}",
        ),
        Check(
            "Contagem de contatos",
            "OK" if stats["legacy_contacts_total"] == stats["postgres_contacts_total"] else "FALHA",
            f"legado={stats['legacy_contacts_total']} postgres={stats['postgres_contacts_total']}",
        ),
        Check(
            "Contagem de enderecos",
            "OK" if stats["legacy_addresses_total"] == stats["postgres_addresses_total"] else "FALHA",
            f"legado={stats['legacy_addresses_total']} postgres={stats['postgres_addresses_total']}",
        ),
        Check(
            "Contagem total de relacionamentos",
            "OK" if stats["legacy_relationships_total"] == stats["postgres_relationships_total"] else "FALHA",
            f"legado={stats['legacy_relationships_total']} postgres={stats['postgres_relationships_total']}",
        ),
        Check(
            "Contagem ativa de relacionamentos",
            "OK" if stats["legacy_relationships_active"] == stats["postgres_relationships_active"] else "FALHA",
            f"legado={stats['legacy_relationships_active']} postgres={stats['postgres_relationships_active']}",
        ),
        Check(
            "Perfis domiciliares Django",
            "OK",
            f"{stats['household_profiles_total']} perfil(is) domiciliar(es) no Postgres",
        ),
        Check(
            "Amostras do espelho cadastral",
            "OK",
            _sample_persons(),
        ),
    ]
    return checks


def write_report(checks: list[Check], db_path: Path) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    target = REPORT_DIR / f"cadastro_postgres_verificacao_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    failed = [item for item in checks if item.failed]
    lines = [
        "# Verificacao Do Espelho Cadastral Postgres",
        "",
        f"Gerado em: {datetime.now().isoformat(timespec='seconds')}",
        f"Banco legado: `{db_path}`",
        f"Resultado: {'OK' if not failed else 'FALHAS'}",
        "",
        "| Check | Status | Detalhe |",
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

    parser = argparse.ArgumentParser(description="Verifica o espelho cadastral da Etapa 2 no PostgreSQL.")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Banco legado SQLite.")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE), help="Arquivo de ambiente do Postgres local.")
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

    checks = build_checks(Path(args.db).expanduser().resolve())
    for check in checks:
        print(f"- {check.status}: {check.name} ({check.detail})")
    if args.report:
        report = write_report(checks, Path(args.db).expanduser().resolve())
        print(f"Relatorio: {report}")
    return 1 if any(check.failed for check in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())

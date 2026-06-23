from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DJANGO_DIR = ROOT / "power_church_django"
DEFAULT_ENV_FILE = ROOT / ".env.power_church_django.postgres.local"
DJANGO_VENV_PYTHON = DJANGO_DIR / ".venv" / "bin" / "python"


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ[key] = value


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


def main() -> int:
    rerun = _run_inside_venv()
    if rerun is not None:
        return rerun
    _load_env_file(DEFAULT_ENV_FILE)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "power_church_site.settings")
    os.environ.setdefault("PYTHONPYCACHEPREFIX", "/private/tmp/pycache_powerchurch")
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(DJANGO_DIR))

    import django

    django.setup()

    from power_church_django.apps.contributions.models import ContributionTypeSnapshot
    from power_church_django.apps.imports.models import CentRuleSnapshot
    from power_church_django.services.legacy import cent_rules_data

    organization_id = (
        ContributionTypeSnapshot.objects.order_by("organization_id", "legacy_id")
        .values_list("organization_id", flat=True)
        .first()
        or 1
    )
    count = 0
    active = 0
    for row in cent_rules_data().get("rules", []):
        _, _created = CentRuleSnapshot.objects.update_or_create(
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
        count += 1
        if bool(row.get("ativo")):
            active += 1
    print(f"regras={count}")
    print(f"ativas={active}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


ROOT = Path("/Users/piraginejr/Documents/New project/Teste/Power Church")
DJANGO_ROOT = ROOT / "power_church_django"
VENV_SITE = next((DJANGO_ROOT / ".venv" / "lib").glob("python*/site-packages"), None)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(DJANGO_ROOT) not in sys.path:
    sys.path.insert(0, str(DJANGO_ROOT))
if VENV_SITE is not None and str(VENV_SITE) not in sys.path:
    sys.path.insert(0, str(VENV_SITE))


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


os.environ.update(_load_env_file(ROOT / ".env.power_church_django.postgres.local"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "power_church_site.settings")
os.environ.setdefault("POWER_CHURCH_ENV_FILE", str(ROOT / ".env.power_church_django.postgres.local"))

import django  # noqa: E402

django.setup()

from power_church_django.apps.imports.models import StatementImportPilotMovement  # noqa: E402
from power_church_django.apps.imports.services import (  # noqa: E402
    close_statement_lot_postgres_native,
    get_statement_lot_detail_from_snapshot,
    get_statement_movement_detail_from_snapshot,
    prepare_statement_lot_postgres_native,
    reprocess_statement_lot_postgres_native,
    update_statement_movement_postgres_native,
)


class _FakeForm(dict):
    def lists(self):
        for key, value in self.items():
            if isinstance(value, list):
                yield key, value
            else:
                yield key, [value]


def main() -> int:
    lot_id = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    result: dict[str, object] = {"lot_id": lot_id}
    prepared = prepare_statement_lot_postgres_native(lot_id, actor="codex")
    result["prepared"] = prepared
    detail = get_statement_lot_detail_from_snapshot(lot_id, backend="postgres_nativo")
    if not detail:
        raise SystemExit("Lote nativo nao encontrado apos prepare.")
    result["status_after_prepare"] = detail["lot"]["status"]
    result["pending_after_prepare"] = next(
        (item["count"] for item in detail["status_options"] if item["value"] == "pendencias"),
        None,
    )
    first_pending = next(
        (item for item in detail["movements"] if item["review_status"] in {"revisar_pessoa", "revisar_destinacao", "pendente"}),
        None,
    )
    result["first_pending"] = first_pending["id"] if first_pending else None
    if first_pending:
        movement_detail = get_statement_movement_detail_from_snapshot(
            int(first_pending["id"]),
            backend="postgres_nativo",
        )
        if not movement_detail:
            raise SystemExit("Movimento nativo nao encontrado.")
        person_options = movement_detail["person_options"]
        selected_person = next((item["id"] for item in person_options if item["id"]), 0)
        type_options = movement_detail["type_options"]
        selected_type = next((item["id"] for item in type_options if item["id"]), 0)
        update_statement_movement_postgres_native(
            int(first_pending["id"]),
            _FakeForm(
                action="approve",
                resolved_person_id=str(selected_person),
                resolved_tipo_contribuicao_id=str(selected_type),
                review_notes="Prova automatica do fluxo nativo.",
            ),
            actor="codex",
        )
        refreshed = StatementImportPilotMovement.objects.get(id=int(first_pending["id"]))
        result["updated_status"] = refreshed.review_status
        result["updated_person"] = refreshed.resolved_person_legacy_id
        result["updated_type"] = (refreshed.metadata or {}).get("resolved_tipo_contribuicao_id")
    result["reprocessed"] = reprocess_statement_lot_postgres_native(lot_id)
    try:
        close_result = close_statement_lot_postgres_native(lot_id, actor="codex")
    except Exception as exc:  # noqa: BLE001
        close_result = {"error": str(exc)}
    result["close_attempt"] = close_result
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

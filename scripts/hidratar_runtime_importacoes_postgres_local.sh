#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DEFAULT_RUNTIME_DIR="$HOME/power_church_postgres_runtime"
RUNTIME_DIR="${POWER_CHURCH_RUNTIME_DIR:-$DEFAULT_RUNTIME_DIR}"
COMPOSE_FILE="$PROJECT_DIR/docker-compose.runtime.yml"
ENV_FILE="$RUNTIME_DIR/env/runtime.env"
NATIVE_MARKER_FILE="$RUNTIME_DIR/.runtime_native_seeded"

resolve_docker_bin() {
  if command -v docker >/dev/null 2>&1; then
    command -v docker
    return 0
  fi
  for candidate in "$HOME/.orbstack/bin/docker" /usr/local/bin/docker /opt/homebrew/bin/docker /Applications/Docker.app/Contents/Resources/bin/docker; do
    if [[ -x "$candidate" ]]; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

DOCKER_BIN="$(resolve_docker_bin || true)"
if [[ -z "$DOCKER_BIN" ]]; then
  echo "Docker nao encontrado para hidratar o runtime."
  exit 1
fi

compose_exec() {
  "$DOCKER_BIN" compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T power-church-django-runtime "$@"
}

echo "Aguardando o container Django ficar pronto para hidratar importacoes..."
attempt=0
until compose_exec python manage.py shell -c "print('ready')" >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  if [[ "$attempt" -ge 30 ]]; then
    echo "Container Django nao ficou pronto a tempo para hidratar importacoes."
    exit 1
  fi
  sleep 2
done

echo "Conferindo se o Postgres local ja esta completo para operar sem dependencia do legado..."
native_status_json="$(
  compose_exec sh -lc 'cd /app/power_church_django && python - <<'"'"'PY'"'"'
import json
import os
import sqlite3

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "power_church_site.settings")

import django

django.setup()

from power_church_django.apps.contributions.models import (
    NativeAuxContributor,
    NativeContribution,
    NativeEnvelope,
    ReceiptSnapshot,
)
from power_church_django.apps.people.models import PersonContributorSnapshot, PersonSnapshot

conn = sqlite3.connect("/app/data/power_church_membros_importado.db")
legacy = {
    "people": int(conn.execute("SELECT COUNT(*) FROM pessoas WHERE ativo = 1").fetchone()[0] or 0),
    "contributions": int(conn.execute("SELECT COUNT(*) FROM contribuicoes WHERE ativo = 1").fetchone()[0] or 0),
    "aux_contributors": int(
        conn.execute(
            "SELECT COUNT(*) FROM contribuintes WHERE ativo = 1 AND COALESCE(pessoa_id, 0) = 0"
        ).fetchone()[0]
        or 0
    ),
    "envelopes": int(conn.execute("SELECT COUNT(*) FROM envelopes WHERE ativo = 1").fetchone()[0] or 0),
    "receipts": int(conn.execute("SELECT COUNT(*) FROM recibos").fetchone()[0] or 0),
}
conn.close()

native = {
    "people": PersonSnapshot.objects.filter(is_active=True).count(),
    "member_contributors": PersonContributorSnapshot.objects.filter(is_active=True).count(),
    "aux_contributors": NativeAuxContributor.objects.filter(is_active=True).count(),
    "contributions": NativeContribution.objects.filter(is_active=True).count(),
    "envelopes": NativeEnvelope.objects.filter(is_active=True).count(),
    "receipts": ReceiptSnapshot.objects.count(),
}

needs_seed = any(
    [
        native["people"] < legacy["people"],
        native["contributions"] < legacy["contributions"],
        native["aux_contributors"] < legacy["aux_contributors"],
        native["envelopes"] < legacy["envelopes"],
        native["receipts"] < legacy["receipts"],
    ]
)

print(json.dumps({"needs_seed": needs_seed, "legacy": legacy, "native": native}, ensure_ascii=False))
PY'
)"

needs_native_seed="$(
  python3 -c 'import json,sys; data=json.load(sys.stdin); print("1" if data.get("needs_seed") else "0")' \
    <<<"$native_status_json"
)"

python3 - "$native_status_json" <<'PY'
import json
import sys

data = json.loads(sys.argv[1])
legacy = data["legacy"]
native = data["native"]
print(
    "Comparativo nativo atual: "
    f"pessoas {native['people']}/{legacy['people']}, "
    f"contribuicoes {native['contributions']}/{legacy['contributions']}, "
    f"auxiliares {native['aux_contributors']}/{legacy['aux_contributors']}, "
    f"envelopes {native['envelopes']}/{legacy['envelopes']}, "
    f"recibos {native['receipts']}/{legacy['receipts']}"
)
PY

if [[ "$needs_native_seed" == "1" ]]; then
  echo "Runtime Postgres incompleto. Fazendo bootstrap nativo completo a partir do banco antigo local..."
  compose_exec python /app/scripts/sincronizar_espelho_cadastro_postgres.py \
    --db /app/data/power_church_membros_importado.db \
    --actor runtime:native_bootstrap
  compose_exec python /app/scripts/backfill_financeiro_nativo_postgres.py
  printf '%s\n' "$(date '+%Y-%m-%dT%H:%M:%S')" > "$NATIVE_MARKER_FILE"
elif [[ ! -f "$NATIVE_MARKER_FILE" ]]; then
  printf '%s\n' "$(date '+%Y-%m-%dT%H:%M:%S')" > "$NATIVE_MARKER_FILE"
fi

echo "Hidratando regras de centavos e lotes de extrato no runtime..."
compose_exec python manage.py shell -c "
import sqlite3
from pathlib import Path
from power_church_django.apps.contributions.models import ContributionTypeSnapshot
from power_church_django.apps.imports.models import CentRuleSnapshot
from power_church_django.apps.imports.services import sync_statement_lot_snapshot_from_legacy
from power_church_django.services.legacy import cent_rules_data
from power_church_django.services.postgres_people_sync import sync_people_runtime_auxiliary_loads

organization_id = (
    ContributionTypeSnapshot.objects.order_by('organization_id', 'legacy_id')
    .values_list('organization_id', flat=True)
    .first()
    or 1
)
rule_count = 0
active_count = 0
for row in cent_rules_data().get('rules', []):
    CentRuleSnapshot.objects.update_or_create(
        legacy_id=int(row.get('id') or 0),
        defaults={
            'organization_id': int(organization_id),
            'cent_code': str(row.get('codigo') or '').zfill(2),
            'destination_name': str(row.get('nome') or ''),
            'contribution_type_legacy_id': int(row.get('tipo_id') or 0) or None,
            'contribution_type_name': str(row.get('tipo_nome') or ''),
            'campaign_name': str(row.get('campanha_nome') or ''),
            'account_code': str(row.get('conta_codigo') or ''),
            'account_name': str(row.get('conta_nome') or ''),
            'is_active': bool(row.get('ativo')),
        },
    )
    rule_count += 1
    if bool(row.get('ativo')):
        active_count += 1

conn = sqlite3.connect('/app/data/power_church_membros_importado.db')
lot_ids = [int(row[0]) for row in conn.execute('SELECT id FROM extrato_lotes ORDER BY id ASC').fetchall()]
conn.close()
synced_lots = 0
for lot_id in lot_ids:
    sync_statement_lot_snapshot_from_legacy(lot_id)
    synced_lots += 1

aux_stats = sync_people_runtime_auxiliary_loads(Path('/app/data/power_church_membros_importado.db'))

print(f'cent_rules_sync=OK total={rule_count} active={active_count}')
print(f'statement_lots_sync=OK total={synced_lots}')
print(
    'people_aux_sync=OK '
    f\"trash={aux_stats['secure_trash_total']} \"
    f\"purge={aux_stats['secure_purge_total']} \"
    f\"import_lots={aux_stats['people_import_native_lots_total']} \"
    f\"import_lines={aux_stats['people_import_lines_total']} \"
    f\"import_pendings={aux_stats['people_import_pendings_total']}\"
)
"

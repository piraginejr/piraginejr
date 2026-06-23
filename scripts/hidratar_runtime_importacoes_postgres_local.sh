#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DEFAULT_RUNTIME_DIR="$HOME/power_church_postgres_runtime"
RUNTIME_DIR="${POWER_CHURCH_RUNTIME_DIR:-$DEFAULT_RUNTIME_DIR}"
COMPOSE_FILE="$PROJECT_DIR/docker-compose.runtime.yml"
ENV_FILE="$RUNTIME_DIR/env/runtime.env"

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

echo "Aguardando o container Django ficar pronto para hidratar importacoes..."
attempt=0
until "$DOCKER_BIN" compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T power-church-django-runtime python manage.py shell -c "print('ready')" >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  if [[ "$attempt" -ge 30 ]]; then
    echo "Container Django nao ficou pronto a tempo para hidratar importacoes."
    exit 1
  fi
  sleep 2
done

echo "Hidratando regras de centavos e lotes de extrato no runtime..."
"$DOCKER_BIN" compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T power-church-django-runtime python manage.py shell -c "
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

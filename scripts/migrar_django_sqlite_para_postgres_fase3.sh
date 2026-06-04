#!/bin/zsh
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DJANGO_DIR="$BASE_DIR/power_church_django"
PYTHON_BIN="$DJANGO_DIR/.venv/bin/python"
BASE_ENV_FILE="$BASE_DIR/.env.power_church_django.local"
PG_ENV_FILE="$BASE_DIR/.env.power_church_django.postgres.local"
POSTGRES_APP_BIN="/Applications/Postgres.app/Contents/Versions/latest/bin"
PG_DUMP_BIN="$POSTGRES_APP_BIN/pg_dump"
SQLITE_DB="$BASE_DIR/data/power_church_django.sqlite3"
STAMP="$(date +%Y%m%d_%H%M%S)"
FIXTURE_FILE="$BASE_DIR/data/homologacao/django_sqlite_to_postgres_fase3_${STAMP}.json"
PG_BACKUP_FILE="$BASE_DIR/data/backups/postgres_django_pre_fase3_${STAMP}.dump"
REPORT_FILE="$BASE_DIR/data/homologacao/fase3_migracao_django_postgres_${STAMP}.md"

mkdir -p "$BASE_DIR/data/homologacao" "$BASE_DIR/data/backups"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Python do Django nao encontrado: $PYTHON_BIN"
  exit 1
fi

if [[ ! -f "$SQLITE_DB" ]]; then
  echo "Banco SQLite do Django nao encontrado: $SQLITE_DB"
  exit 1
fi

if [[ ! -x "$PG_DUMP_BIN" ]]; then
  echo "pg_dump do Postgres.app nao encontrado: $PG_DUMP_BIN"
  exit 1
fi

if [[ -f "$BASE_ENV_FILE" ]]; then
  set -a
  source "$BASE_ENV_FILE"
  set +a
fi

if [[ -f "$PG_ENV_FILE" ]]; then
  set -a
  source "$PG_ENV_FILE"
  set +a
fi

PGHOST="${POWER_CHURCH_POSTGRES_HOST:-127.0.0.1}"
PGPORT="${POWER_CHURCH_POSTGRES_PORT:-5432}"
PGDATABASE="${POWER_CHURCH_POSTGRES_DB:-power_church}"
PGUSER="${POWER_CHURCH_POSTGRES_USER:-power_church}"
PGPASSWORD="${POWER_CHURCH_POSTGRES_PASSWORD:-power_church_dev}"
export PGPASSWORD

MODELS=(
  auth.user
  contributions.receiptemailtemplate
  contributions.receiptdispatch
  audit.auditevent
  auditlog.logentry
  people.householdprofile
  waffle.flag
  waffle.switch
)

echo "== Fase 3 - Migracao Django SQLite -> PostgreSQL =="
echo "SQLite origem: $SQLITE_DB"
echo "PostgreSQL destino: $PGUSER@$PGHOST:$PGPORT/$PGDATABASE"
echo

echo "Gerando fixture do SQLite atual..."
(
  cd "$DJANGO_DIR"
  set -a
  source "$BASE_ENV_FILE"
  set +a
  unset POWER_CHURCH_POSTGRES_DB POWER_CHURCH_POSTGRES_USER POWER_CHURCH_POSTGRES_PASSWORD POWER_CHURCH_POSTGRES_HOST POWER_CHURCH_POSTGRES_PORT
  "$PYTHON_BIN" manage.py dumpdata \
    --indent 2 \
    --output "$FIXTURE_FILE" \
    "${MODELS[@]}"
)

echo "Gerando backup do PostgreSQL antes da carga..."
"$PG_DUMP_BIN" -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" -Fc -f "$PG_BACKUP_FILE"

echo "Aplicando flush e carga no PostgreSQL..."
(
  cd "$DJANGO_DIR"
  set -a
  source "$BASE_ENV_FILE"
  source "$PG_ENV_FILE"
  set +a
  "$PYTHON_BIN" manage.py flush --noinput
  "$PYTHON_BIN" manage.py setup_access_profiles
  "$PYTHON_BIN" manage.py loaddata "$FIXTURE_FILE"
  "$PYTHON_BIN" manage.py setup_access_profiles
  "$PYTHON_BIN" manage.py check
)

echo "Gerando relatorio comparativo..."
SOURCE_COUNTS="$(sqlite3 "$SQLITE_DB" "SELECT 'auth_user', count(*) FROM auth_user UNION ALL SELECT 'auth_group', count(*) FROM auth_group UNION ALL SELECT 'auth_user_groups', count(*) FROM auth_user_groups UNION ALL SELECT 'contributions_receiptdispatch', count(*) FROM contributions_receiptdispatch UNION ALL SELECT 'contributions_receiptemailtemplate', count(*) FROM contributions_receiptemailtemplate UNION ALL SELECT 'audit_auditevent', count(*) FROM audit_auditevent UNION ALL SELECT 'auditlog_logentry', count(*) FROM auditlog_logentry UNION ALL SELECT 'people_householdprofile', count(*) FROM people_householdprofile UNION ALL SELECT 'waffle_flag', count(*) FROM waffle_flag UNION ALL SELECT 'waffle_switch', count(*) FROM waffle_switch;")"
TARGET_COUNTS="$("$POSTGRES_APP_BIN/psql" -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" -Atc "SELECT 'auth_user', COUNT(*) FROM auth_user UNION ALL SELECT 'auth_group', COUNT(*) FROM auth_group UNION ALL SELECT 'auth_user_groups', COUNT(*) FROM auth_user_groups UNION ALL SELECT 'contributions_receiptdispatch', COUNT(*) FROM contributions_receiptdispatch UNION ALL SELECT 'contributions_receiptemailtemplate', COUNT(*) FROM contributions_receiptemailtemplate UNION ALL SELECT 'audit_auditevent', COUNT(*) FROM audit_auditevent UNION ALL SELECT 'auditlog_logentry', COUNT(*) FROM auditlog_logentry UNION ALL SELECT 'people_householdprofile', COUNT(*) FROM people_householdprofile UNION ALL SELECT 'waffle_flag', COUNT(*) FROM waffle_flag UNION ALL SELECT 'waffle_switch', COUNT(*) FROM waffle_switch;")"

cat > "$REPORT_FILE" <<EOF
# Fase 3 Migracao Django SQLite Para PostgreSQL

Gerado em: $(date -Iseconds)

## Artefatos

- Fixture: $FIXTURE_FILE
- Backup PostgreSQL antes da carga: $PG_BACKUP_FILE

## Origem SQLite

\`\`\`text
$SOURCE_COUNTS
\`\`\`

## Destino PostgreSQL

\`\`\`text
$TARGET_COUNTS
\`\`\`
EOF

echo
echo "Migracao concluida."
echo "Fixture: $FIXTURE_FILE"
echo "Backup PostgreSQL: $PG_BACKUP_FILE"
echo "Relatorio: $REPORT_FILE"

#!/bin/zsh
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_BIN="/Applications/Postgres.app/Contents/Versions/latest/bin"
DATA_DIR="$BASE_DIR/data/postgres_cluster"
LOG_FILE="$BASE_DIR/data/postgres.log"
PG_CTL="$APP_BIN/pg_ctl"
INITDB="$APP_BIN/initdb"
PSQL="$APP_BIN/psql"
PG_ISREADY="$APP_BIN/pg_isready"
PORT="${POWER_CHURCH_POSTGRES_PORT:-5432}"
HOST="${POWER_CHURCH_POSTGRES_HOST:-127.0.0.1}"
DB_NAME="${POWER_CHURCH_POSTGRES_DB:-power_church}"
DB_USER="${POWER_CHURCH_POSTGRES_USER:-power_church}"
DB_PASSWORD="${POWER_CHURCH_POSTGRES_PASSWORD:-power_church_dev}"

if [[ ! -x "$PG_CTL" || ! -x "$INITDB" || ! -x "$PSQL" ]]; then
  echo "Nao encontrei os binarios do Postgres.app em:"
  echo "$APP_BIN"
  echo "Instale o Postgres.app antes de usar este script."
  exit 1
fi

mkdir -p "$BASE_DIR/data"

if [[ ! -f "$DATA_DIR/PG_VERSION" ]]; then
  echo "Inicializando cluster PostgreSQL local da Fase 2..."
  rm -rf "$DATA_DIR"
  "$INITDB" -D "$DATA_DIR" --username=postgres --auth-local=trust --auth-host=scram-sha-256 >/dev/null
fi

if ! grep -q "^listen_addresses = '$HOST'" "$DATA_DIR/postgresql.conf" 2>/dev/null; then
  {
    echo
    echo "listen_addresses = '$HOST'"
    echo "port = $PORT"
  } >> "$DATA_DIR/postgresql.conf"
fi

if ! "$PG_CTL" -D "$DATA_DIR" status >/dev/null 2>&1; then
  echo "Subindo PostgreSQL local da Fase 2..."
  "$PG_CTL" -D "$DATA_DIR" -l "$LOG_FILE" -o "-h $HOST -p $PORT" start >/dev/null
fi

for _ in {1..20}; do
  if "$PG_ISREADY" -h "$HOST" -p "$PORT" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! "$PG_ISREADY" -h "$HOST" -p "$PORT" >/dev/null 2>&1; then
  echo "O PostgreSQL nao respondeu em $HOST:$PORT."
  exit 2
fi

echo "Garantindo role e banco da aplicacao..."
"$PSQL" -v ON_ERROR_STOP=1 -d postgres -U postgres <<SQL >/dev/null
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '${DB_USER}') THEN
    EXECUTE format('CREATE ROLE %I LOGIN PASSWORD %L', '${DB_USER}', '${DB_PASSWORD}');
  ELSE
    EXECUTE format('ALTER ROLE %I WITH LOGIN PASSWORD %L', '${DB_USER}', '${DB_PASSWORD}');
  END IF;
END
\$\$;
SQL

if ! "$PSQL" -tAc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" -d postgres -U postgres | grep -q 1; then
  "$APP_BIN/createdb" -U postgres -O "$DB_USER" "$DB_NAME"
fi

echo
echo "PostgreSQL local pronto."
echo "Host: $HOST"
echo "Porta: $PORT"
echo "Banco: $DB_NAME"
echo "Usuario: $DB_USER"
echo "Data dir: $DATA_DIR"

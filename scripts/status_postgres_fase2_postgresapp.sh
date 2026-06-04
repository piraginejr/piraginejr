#!/bin/zsh
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_BIN="/Applications/Postgres.app/Contents/Versions/latest/bin"
DATA_DIR="$BASE_DIR/data/postgres_cluster"
PG_CTL="$APP_BIN/pg_ctl"
PG_ISREADY="$APP_BIN/pg_isready"
HOST="${POWER_CHURCH_POSTGRES_HOST:-127.0.0.1}"
PORT="${POWER_CHURCH_POSTGRES_PORT:-5432}"

if [[ ! -x "$PG_CTL" || ! -x "$PG_ISREADY" ]]; then
  echo "Postgres.app nao esta disponivel."
  exit 1
fi

if [[ ! -f "$DATA_DIR/PG_VERSION" ]]; then
  echo "Cluster da Fase 2 ainda nao foi inicializado."
  exit 0
fi

if "$PG_CTL" -D "$DATA_DIR" status >/dev/null 2>&1; then
  echo "Status interno: rodando"
else
  echo "Status interno: parado"
fi

if "$PG_ISREADY" -h "$HOST" -p "$PORT" >/dev/null 2>&1; then
  echo "Rede: respondendo em $HOST:$PORT"
else
  echo "Rede: sem resposta em $HOST:$PORT"
fi

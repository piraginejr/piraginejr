#!/bin/zsh
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_BIN="/Applications/Postgres.app/Contents/Versions/latest/bin"
DATA_DIR="$BASE_DIR/data/postgres_cluster"
PG_CTL="$APP_BIN/pg_ctl"

if [[ ! -x "$PG_CTL" ]]; then
  echo "Nao encontrei o pg_ctl do Postgres.app."
  exit 1
fi

if [[ ! -f "$DATA_DIR/PG_VERSION" ]]; then
  echo "Nenhum cluster da Fase 2 foi inicializado ainda."
  exit 0
fi

if "$PG_CTL" -D "$DATA_DIR" status >/dev/null 2>&1; then
  "$PG_CTL" -D "$DATA_DIR" stop -m fast >/dev/null
  echo "PostgreSQL da Fase 2 parado."
else
  echo "PostgreSQL da Fase 2 ja estava parado."
fi

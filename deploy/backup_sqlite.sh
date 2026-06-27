#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB_PATH="${POWER_CHURCH_DB_PATH:-$ROOT_DIR/data/power_church_membros_importado.db}"
BACKUP_DIR="${POWER_CHURCH_BACKUP_DIR:-$ROOT_DIR/data/backups}"
STAMP="$(date +%Y%m%d_%H%M%S)"
TARGET="$BACKUP_DIR/power_church_backup_$STAMP.db"
RUNTIME_DIR="${POWER_CHURCH_RUNTIME_DIR:-$HOME/power_church_postgres_runtime}"
RUNTIME_ENV_FILE="$RUNTIME_DIR/env/runtime.env"
RUNTIME_BACKUP_SCRIPT="$ROOT_DIR/scripts/powerbackup_runtime.sh"
BACKUP_MODE="${POWER_CHURCH_BACKUP_MODE:-auto}"

runtime_ready() {
  [[ -f "$RUNTIME_ENV_FILE" ]] && [[ -x "$RUNTIME_BACKUP_SCRIPT" ]]
}

backup_legacy_sqlite() {
  mkdir -p "$BACKUP_DIR"
  sqlite3 "$DB_PATH" ".backup '$TARGET'"
  echo "$TARGET"
}

case "$BACKUP_MODE" in
  runtime)
    exec "$RUNTIME_BACKUP_SCRIPT"
    ;;
  sqlite)
    backup_legacy_sqlite
    ;;
  auto)
    if runtime_ready; then
      exec "$RUNTIME_BACKUP_SCRIPT"
    fi
    backup_legacy_sqlite
    ;;
  *)
    echo "Modo de backup invalido: $BACKUP_MODE" >&2
    echo "Use POWER_CHURCH_BACKUP_MODE=auto|runtime|sqlite" >&2
    exit 1
    ;;
esac

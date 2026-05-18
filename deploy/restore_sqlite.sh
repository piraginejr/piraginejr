#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Uso: deploy/restore_sqlite.sh caminho/do/backup.db" >&2
  exit 2
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB_PATH="${POWER_CHURCH_DB_PATH:-$ROOT_DIR/data/power_church_membros_importado.db}"
BACKUP_SOURCE="$1"
PRE_RESTORE_DIR="${POWER_CHURCH_BACKUP_DIR:-$ROOT_DIR/data/backups}"
STAMP="$(date +%Y%m%d_%H%M%S)"

if [[ ! -f "$BACKUP_SOURCE" ]]; then
  echo "Backup nao encontrado: $BACKUP_SOURCE" >&2
  exit 1
fi

mkdir -p "$PRE_RESTORE_DIR"
if [[ -f "$DB_PATH" ]]; then
  sqlite3 "$DB_PATH" ".backup '$PRE_RESTORE_DIR/power_church_pre_restore_$STAMP.db'"
fi

cp "$BACKUP_SOURCE" "$DB_PATH"
echo "Restaurado: $DB_PATH"


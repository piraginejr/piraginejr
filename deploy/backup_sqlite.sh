#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB_PATH="${POWER_CHURCH_DB_PATH:-$ROOT_DIR/data/power_church_membros_importado.db}"
BACKUP_DIR="${POWER_CHURCH_BACKUP_DIR:-$ROOT_DIR/data/backups}"
STAMP="$(date +%Y%m%d_%H%M%S)"
TARGET="$BACKUP_DIR/power_church_backup_$STAMP.db"

mkdir -p "$BACKUP_DIR"
sqlite3 "$DB_PATH" ".backup '$TARGET'"
echo "$TARGET"


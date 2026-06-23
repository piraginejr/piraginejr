#!/bin/zsh
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$BASE_DIR"

exec "$BASE_DIR/Abrir Power Church Django PostgreSQL.command" "$@"

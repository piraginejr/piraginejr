#!/bin/zsh
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
export POWER_CHURCH_DJANGO_EXTRA_ENV_FILE="$BASE_DIR/.env.power_church_django.postgres.local"

exec "$BASE_DIR/Abrir Power Church Django.command"

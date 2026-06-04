#!/bin/zsh
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker nao esta disponivel no PATH."
  echo "Instale ou abra o Docker Desktop antes de usar este atalho."
  exit 1
fi

cd "$BASE_DIR"
docker compose -f docker-compose.django.yml up -d postgres
echo
echo "PostgreSQL da Fase 2 iniciado."
echo "Agora voce pode abrir:"
echo "  $BASE_DIR/Abrir Power Church Django PostgreSQL.command"

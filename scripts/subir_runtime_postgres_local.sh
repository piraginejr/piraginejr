#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DEFAULT_RUNTIME_DIR="$HOME/power_church_postgres_runtime"
RUNTIME_DIR="${POWER_CHURCH_RUNTIME_DIR:-$DEFAULT_RUNTIME_DIR}"
COMPOSE_FILE="$PROJECT_DIR/docker-compose.runtime.yml"
ENV_FILE="$RUNTIME_DIR/env/runtime.env"
RUNTIME_URL="${POWER_CHURCH_RUNTIME_URL:-http://127.0.0.1:8001/accounts/login/}"
FORCE_BUILD="${POWER_CHURCH_RUNTIME_FORCE_BUILD:-0}"

export POWER_CHURCH_RUNTIME_DIR="$RUNTIME_DIR"

resolve_docker_bin() {
  if command -v docker >/dev/null 2>&1; then
    command -v docker
    return 0
  fi
  for candidate in /usr/local/bin/docker /opt/homebrew/bin/docker /Applications/Docker.app/Contents/Resources/bin/docker; do
    if [[ -x "$candidate" ]]; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

DOCKER_BIN="$(resolve_docker_bin || true)"
if [[ -z "$DOCKER_BIN" ]]; then
  echo "Docker nao encontrado. Abra o Docker Desktop/OrbStack ou ajuste o PATH antes de continuar."
  exit 1
fi

if /usr/bin/curl -fsS --max-time 2 "$RUNTIME_URL" >/dev/null 2>&1; then
  echo "Runtime PostgreSQL ja esta respondendo em $RUNTIME_URL"
  exit 0
fi

echo "Subindo runtime Docker do Power Church..."
echo "Runtime alvo: $RUNTIME_DIR"

if [[ ! -f "$RUNTIME_DIR/.runtime_seeded" ]]; then
  echo "Primeira carga do runtime: sincronizando dados operacionais..."
  "$SCRIPT_DIR/preparar_runtime_postgres_local.sh" --sync-existing-data --verbose-sync
  touch "$RUNTIME_DIR/.runtime_seeded"
else
  echo "Runtime ja semeado: preparando apenas estrutura leve..."
  "$SCRIPT_DIR/preparar_runtime_postgres_local.sh"
fi

echo "Executando docker compose..."
if [[ "$FORCE_BUILD" == "1" ]]; then
  echo "Modo rebuild forçado: reconstruindo a imagem do Django."
  "$DOCKER_BIN" compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d --build
else
  echo "Modo rapido: sem rebuild de imagem."
  "$DOCKER_BIN" compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d
fi
zsh "$SCRIPT_DIR/hidratar_runtime_importacoes_postgres_local.sh"
"$DOCKER_BIN" compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps

echo
echo "Runtime Docker do Power Church PostgreSQL no ar."
echo "Login publico: http://127.0.0.1:8001/accounts/login/"
echo "Runtime persistente: $RUNTIME_DIR"

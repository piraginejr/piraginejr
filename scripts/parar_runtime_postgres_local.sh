#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DEFAULT_RUNTIME_DIR="$HOME/power_church_postgres_runtime"
RUNTIME_DIR="${POWER_CHURCH_RUNTIME_DIR:-$DEFAULT_RUNTIME_DIR}"
COMPOSE_FILE="$PROJECT_DIR/docker-compose.runtime.yml"
ENV_FILE="$RUNTIME_DIR/env/runtime.env"

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

"$DOCKER_BIN" compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" down

echo "Runtime Docker do Power Church PostgreSQL parado."

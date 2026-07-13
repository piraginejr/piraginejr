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

docker_server_ready() {
  "$DOCKER_BIN" version >/dev/null 2>&1
}

docker_context_exists() {
  local context_name="${1:-}"
  [[ -n "$context_name" ]] || return 1
  "$DOCKER_BIN" context inspect "$context_name" >/dev/null 2>&1
}

try_working_context() {
  local context_name="${1:-}"
  [[ -n "$context_name" ]] || return 1
  docker_context_exists "$context_name" || return 1
  "$DOCKER_BIN" --context "$context_name" version >/dev/null 2>&1 || return 1
  "$DOCKER_BIN" context use "$context_name" >/dev/null 2>&1 || true
  return 0
}

resolve_app_path() {
  local app_name="${1:-}"
  [[ -n "$app_name" ]] || return 1
  for base_dir in /Applications "$HOME/Applications"; do
    if [[ -d "$base_dir/$app_name.app" ]]; then
      echo "$base_dir/$app_name.app"
      return 0
    fi
  done
  return 1
}

resolve_preferred_docker_app() {
  local current_context
  current_context="$("$DOCKER_BIN" context show 2>/dev/null || echo "")"
  if [[ "$current_context" == "orbstack" ]] && resolve_app_path "OrbStack" >/dev/null 2>&1; then
    echo "OrbStack"
    return 0
  fi
  if resolve_app_path "OrbStack" >/dev/null 2>&1; then
    echo "OrbStack"
    return 0
  fi
  if resolve_app_path "Docker" >/dev/null 2>&1; then
    echo "Docker"
    return 0
  fi
  return 1
}

launch_docker_runtime_app() {
  local app_name="${1:-}"
  [[ -n "$app_name" ]] || return 1
  local app_path
  app_path="$(resolve_app_path "$app_name" || true)"
  [[ -n "$app_path" ]] || return 1
  echo "Docker indisponivel. Abrindo $app_name automaticamente..."
  /usr/bin/open -a "$app_path" >/dev/null 2>&1 || return 1
  return 0
}

ensure_docker_ready() {
  if docker_server_ready; then
    return 0
  fi

  local preferred_app=""
  preferred_app="$(resolve_preferred_docker_app || true)"
  if [[ -n "$preferred_app" ]]; then
    launch_docker_runtime_app "$preferred_app" || true
  fi

  local wait_seconds=90
  local interval_seconds=3
  local elapsed=0
  local current_context=""
  current_context="$("$DOCKER_BIN" context show 2>/dev/null || echo "")"

  while (( elapsed < wait_seconds )); do
    if docker_server_ready; then
      return 0
    fi
    if [[ "$current_context" != "orbstack" ]] && try_working_context "orbstack"; then
      return 0
    fi
    if [[ "$current_context" != "default" ]] && try_working_context "default"; then
      return 0
    fi
    if (( elapsed == 0 )); then
      echo "Aguardando o Docker iniciar..."
    fi
    sleep "$interval_seconds"
    elapsed=$((elapsed + interval_seconds))
  done

  echo "Docker nao respondeu apos ${wait_seconds}s."
  echo "Abra o OrbStack/Docker Desktop e confirme que o daemon terminou de iniciar."
  return 1
}

DOCKER_BIN="$(resolve_docker_bin || true)"
if [[ -z "$DOCKER_BIN" ]]; then
  echo "Docker nao encontrado. Abra o Docker Desktop/OrbStack ou ajuste o PATH antes de continuar."
  exit 1
fi

if ! ensure_docker_ready; then
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

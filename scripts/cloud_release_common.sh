#!/usr/bin/env bash

SCRIPT_DIR="${SCRIPT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
PROJECT_DIR="${PROJECT_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
DEFAULT_RUNTIME_DIR="${DEFAULT_RUNTIME_DIR:-$HOME/power_church_postgres_runtime}"
RUNTIME_DIR="${POWER_CHURCH_RUNTIME_DIR:-$DEFAULT_RUNTIME_DIR}"
COMPOSE_FILE="${COMPOSE_FILE:-$PROJECT_DIR/docker-compose.runtime.yml}"
ENV_FILE="${ENV_FILE:-$RUNTIME_DIR/env/runtime.env}"
LOGIN_URL="${POWER_CHURCH_RUNTIME_LOGIN_URL:-http://127.0.0.1:8001/accounts/login/}"
HEALTH_URL="${POWER_CHURCH_RUNTIME_HEALTH_URL:-http://127.0.0.1:8001/api/v1/health/}"
CLOUD_RELEASE_REMOTE="${POWER_CHURCH_CLOUD_RELEASE_REMOTE:-origin}"
CLOUD_RELEASE_BRANCH="${POWER_CHURCH_CLOUD_RELEASE_BRANCH:-cloud-release}"
STATE_DIR="${POWER_CHURCH_CLOUD_RELEASE_STATE_DIR:-$RUNTIME_DIR/logs/cloud_release}"
HISTORY_DIR="$STATE_DIR/history"

resolve_docker_bin() {
  if command -v docker >/dev/null 2>&1; then
    command -v docker
    return 0
  fi
  for candidate in \
    "$HOME/.orbstack/bin/docker" \
    /usr/local/bin/docker \
    /opt/homebrew/bin/docker \
    /Applications/Docker.app/Contents/Resources/bin/docker
  do
    if [[ -x "$candidate" ]]; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

ensure_runtime_prerequisites() {
  if [[ ! -f "$COMPOSE_FILE" ]]; then
    echo "Arquivo de compose do runtime nao encontrado: $COMPOSE_FILE" >&2
    exit 1
  fi
  if [[ ! -f "$ENV_FILE" ]]; then
    echo "Arquivo de ambiente do runtime nao encontrado: $ENV_FILE" >&2
    exit 1
  fi
  mkdir -p "$HISTORY_DIR"
}

ensure_clean_worktree() {
  if [[ -n "$(git -C "$PROJECT_DIR" status --porcelain)" ]]; then
    echo "Worktree com alteracoes locais. Limpe antes de rodar o deploy cloud-release." >&2
    git -C "$PROJECT_DIR" status --short >&2
    exit 1
  fi
}

ensure_branch_checked_out() {
  local branch="$1"
  local remote="$2"
  if git -C "$PROJECT_DIR" show-ref --verify --quiet "refs/heads/$branch"; then
    git -C "$PROJECT_DIR" checkout "$branch" >/dev/null
  else
    git -C "$PROJECT_DIR" checkout -b "$branch" "$remote/$branch" >/dev/null
  fi
}

wait_for_http_ok() {
  local url="$1"
  local label="$2"
  local attempts="${3:-60}"
  local sleep_seconds="${4:-2}"
  local attempt
  for ((attempt = 1; attempt <= attempts; attempt += 1)); do
    if curl -fsS --max-time 5 "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep "$sleep_seconds"
  done
  echo "Falha aguardando $label em $url" >&2
  return 1
}

capture_compose_status() {
  local docker_bin="$1"
  "$docker_bin" compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps
}

run_runtime_backup() {
  local backup_log="$1"
  if [[ ! -x "$SCRIPT_DIR/powerbackup_runtime.sh" ]]; then
    echo "Script de backup do runtime nao encontrado: $SCRIPT_DIR/powerbackup_runtime.sh" >&2
    exit 1
  fi
  "$SCRIPT_DIR/powerbackup_runtime.sh" 2>&1 | tee "$backup_log"
}

extract_backup_field() {
  local label="$1"
  local backup_log="$2"
  grep "^$label" "$backup_log" | tail -n 1 | sed "s/^$label//"
}

write_release_state() {
  local state_file="$1"
  local kind="$2"
  local timestamp="$3"
  local remote="$4"
  local branch="$5"
  local previous_sha="$6"
  local current_sha="$7"
  local target_ref="$8"
  local backup_log="$9"
  local backup_dump="${10}"
  local backup_files="${11}"
  local backup_manifest="${12}"
  cat > "$state_file" <<EOF
RELEASE_KIND=$kind
TIMESTAMP=$timestamp
REMOTE=$remote
BRANCH=$branch
PREVIOUS_SHA=$previous_sha
CURRENT_SHA=$current_sha
TARGET_REF=$target_ref
BACKUP_LOG=$backup_log
BACKUP_DUMP=$backup_dump
BACKUP_FILES=$backup_files
BACKUP_MANIFEST=$backup_manifest
EOF
}

write_release_report() {
  local report_file="$1"
  local kind="$2"
  local timestamp="$3"
  local remote="$4"
  local branch="$5"
  local previous_sha="$6"
  local current_sha="$7"
  local target_ref="$8"
  local backup_log="$9"
  local backup_dump="${10}"
  local backup_files="${11}"
  local backup_manifest="${12}"
  local compose_status="${13}"
  cat > "$report_file" <<EOF
# Cloud Release Report

- tipo: $kind
- gerado em: $timestamp
- remoto: $remote
- branch: $branch
- SHA anterior: $previous_sha
- SHA atual: $current_sha
- ref alvo: $target_ref
- log do backup: $backup_log
- dump do backup: $backup_dump
- arquivos do backup: $backup_files
- manifesto do backup: $backup_manifest

## Containers

\`\`\`
$compose_status
\`\`\`
EOF
}

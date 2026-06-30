#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/cloud_release_common.sh"

TARGET_SHA=""
SKIP_BACKUP=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --sha)
      shift
      if [[ $# -eq 0 ]]; then
        echo "Informe o SHA depois de --sha" >&2
        exit 1
      fi
      TARGET_SHA="$1"
      ;;
    --skip-backup)
      SKIP_BACKUP=1
      ;;
    *)
      echo "Opcao desconhecida: $1" >&2
      echo "Uso: scripts/rollback_cloud_release.sh [--sha SHA] [--skip-backup]" >&2
      exit 1
      ;;
  esac
  shift
done

LATEST_STATE_FILE="$STATE_DIR/latest_success.env"
if [[ ! -f "$LATEST_STATE_FILE" ]]; then
  echo "Nao existe estado salvo em $LATEST_STATE_FILE para orientar o rollback." >&2
  exit 1
fi

set -a
. "$LATEST_STATE_FILE"
set +a

TARGET_SHA="${TARGET_SHA:-${PREVIOUS_SHA:-}}"
if [[ -z "$TARGET_SHA" ]]; then
  echo "Nenhum SHA de rollback disponivel. Use --sha manualmente." >&2
  exit 1
fi

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_LOG="$HISTORY_DIR/rollback_backup_${TIMESTAMP}.log"
REPORT_FILE="$HISTORY_DIR/rollback_${TIMESTAMP}.md"
STATE_FILE="$HISTORY_DIR/rollback_${TIMESTAMP}.env"

DOCKER_BIN="$(resolve_docker_bin || true)"
if [[ -z "$DOCKER_BIN" ]]; then
  echo "Docker nao encontrado para o rollback cloud-release." >&2
  exit 1
fi

ensure_runtime_prerequisites
ensure_clean_worktree

echo "======================================"
echo " Rollback Cloud Release"
echo "======================================"
echo "Projeto: $PROJECT_DIR"
echo "Runtime: $RUNTIME_DIR"
echo "Branch: $CLOUD_RELEASE_BRANCH"
echo "SHA alvo do rollback: $TARGET_SHA"
echo

PREVIOUS_DEPLOYED_SHA="$(git -C "$PROJECT_DIR" rev-parse HEAD)"

BACKUP_DUMP=""
BACKUP_FILES=""
BACKUP_MANIFEST=""

if [[ "$SKIP_BACKUP" == "1" ]]; then
  echo "Backup pulado por parametro explicito."
else
  echo "Executando backup do runtime antes do rollback..."
  run_runtime_backup "$BACKUP_LOG"
  BACKUP_DUMP="$(extract_backup_field 'Dump Postgres: ' "$BACKUP_LOG")"
  BACKUP_FILES="$(extract_backup_field 'Arquivos persistentes: ' "$BACKUP_LOG")"
  BACKUP_MANIFEST="$(extract_backup_field 'Manifesto: ' "$BACKUP_LOG")"
fi

git -C "$PROJECT_DIR" fetch --all --tags >/dev/null 2>&1 || true
ensure_branch_checked_out "$CLOUD_RELEASE_BRANCH" "$CLOUD_RELEASE_REMOTE"

echo "Reposicionando codigo para $TARGET_SHA..."
git -C "$PROJECT_DIR" reset --hard "$TARGET_SHA" >/dev/null

echo "Reconstruindo o container Django..."
"$DOCKER_BIN" compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" build power-church-django-runtime

echo "Subindo o runtime..."
"$DOCKER_BIN" compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d

echo "Aguardando login responder..."
wait_for_http_ok "$LOGIN_URL" "login publico"

echo "Aguardando healthcheck da API..."
wait_for_http_ok "$HEALTH_URL" "API health"

COMPOSE_STATUS="$(capture_compose_status "$DOCKER_BIN")"

write_release_state \
  "$STATE_FILE" \
  "rollback" \
  "$TIMESTAMP" \
  "$CLOUD_RELEASE_REMOTE" \
  "$CLOUD_RELEASE_BRANCH" \
  "$PREVIOUS_DEPLOYED_SHA" \
  "$TARGET_SHA" \
  "$TARGET_SHA" \
  "$BACKUP_LOG" \
  "$BACKUP_DUMP" \
  "$BACKUP_FILES" \
  "$BACKUP_MANIFEST"

cp "$STATE_FILE" "$LATEST_STATE_FILE"

write_release_report \
  "$REPORT_FILE" \
  "rollback" \
  "$TIMESTAMP" \
  "$CLOUD_RELEASE_REMOTE" \
  "$CLOUD_RELEASE_BRANCH" \
  "$PREVIOUS_DEPLOYED_SHA" \
  "$TARGET_SHA" \
  "$TARGET_SHA" \
  "$BACKUP_LOG" \
  "$BACKUP_DUMP" \
  "$BACKUP_FILES" \
  "$BACKUP_MANIFEST" \
  "$COMPOSE_STATUS"

echo
echo "Rollback concluido com sucesso."
echo "SHA anterior: $PREVIOUS_DEPLOYED_SHA"
echo "SHA restaurado: $TARGET_SHA"
echo "Estado salvo em: $LATEST_STATE_FILE"
echo "Relatorio: $REPORT_FILE"

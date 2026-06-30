#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/cloud_release_common.sh"

TARGET_REF=""
SKIP_BACKUP=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --ref)
      shift
      if [[ $# -eq 0 ]]; then
        echo "Informe a ref depois de --ref" >&2
        exit 1
      fi
      TARGET_REF="$1"
      ;;
    --skip-backup)
      SKIP_BACKUP=1
      ;;
    *)
      echo "Opcao desconhecida: $1" >&2
      echo "Uso: scripts/deploy_cloud_release.sh [--ref REF] [--skip-backup]" >&2
      exit 1
      ;;
  esac
  shift
done

TARGET_REF="${TARGET_REF:-$CLOUD_RELEASE_REMOTE/$CLOUD_RELEASE_BRANCH}"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_LOG="$HISTORY_DIR/deploy_backup_${TIMESTAMP}.log"
REPORT_FILE="$HISTORY_DIR/deploy_${TIMESTAMP}.md"
STATE_FILE="$HISTORY_DIR/deploy_${TIMESTAMP}.env"
LATEST_STATE_FILE="$STATE_DIR/latest_success.env"

DOCKER_BIN="$(resolve_docker_bin || true)"
if [[ -z "$DOCKER_BIN" ]]; then
  echo "Docker nao encontrado para o deploy cloud-release." >&2
  exit 1
fi

ensure_runtime_prerequisites
ensure_clean_worktree

echo "======================================"
echo " Deploy Cloud Release"
echo "======================================"
echo "Projeto: $PROJECT_DIR"
echo "Runtime: $RUNTIME_DIR"
echo "Remoto: $CLOUD_RELEASE_REMOTE"
echo "Branch: $CLOUD_RELEASE_BRANCH"
echo "Ref alvo: $TARGET_REF"
echo

git -C "$PROJECT_DIR" fetch "$CLOUD_RELEASE_REMOTE" "$CLOUD_RELEASE_BRANCH"
PREVIOUS_SHA="$(git -C "$PROJECT_DIR" rev-parse HEAD)"
ensure_branch_checked_out "$CLOUD_RELEASE_BRANCH" "$CLOUD_RELEASE_REMOTE"
CURRENT_SHA="$(git -C "$PROJECT_DIR" rev-parse HEAD)"
RESOLVED_TARGET_SHA="$(git -C "$PROJECT_DIR" rev-parse "$TARGET_REF")"

if [[ "$CURRENT_SHA" == "$RESOLVED_TARGET_SHA" ]]; then
  echo "A branch cloud-release ja esta no SHA alvo: $RESOLVED_TARGET_SHA"
  exit 0
fi

BACKUP_DUMP=""
BACKUP_FILES=""
BACKUP_MANIFEST=""

if [[ "$SKIP_BACKUP" == "1" ]]; then
  echo "Backup pulado por parametro explicito."
else
  echo "Executando backup do runtime antes do deploy..."
  run_runtime_backup "$BACKUP_LOG"
  BACKUP_DUMP="$(extract_backup_field 'Dump Postgres: ' "$BACKUP_LOG")"
  BACKUP_FILES="$(extract_backup_field 'Arquivos persistentes: ' "$BACKUP_LOG")"
  BACKUP_MANIFEST="$(extract_backup_field 'Manifesto: ' "$BACKUP_LOG")"
fi

echo "Atualizando codigo para $RESOLVED_TARGET_SHA..."
git -C "$PROJECT_DIR" reset --hard "$RESOLVED_TARGET_SHA" >/dev/null

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
  "deploy" \
  "$TIMESTAMP" \
  "$CLOUD_RELEASE_REMOTE" \
  "$CLOUD_RELEASE_BRANCH" \
  "$PREVIOUS_SHA" \
  "$RESOLVED_TARGET_SHA" \
  "$TARGET_REF" \
  "$BACKUP_LOG" \
  "$BACKUP_DUMP" \
  "$BACKUP_FILES" \
  "$BACKUP_MANIFEST"

cp "$STATE_FILE" "$LATEST_STATE_FILE"

write_release_report \
  "$REPORT_FILE" \
  "deploy" \
  "$TIMESTAMP" \
  "$CLOUD_RELEASE_REMOTE" \
  "$CLOUD_RELEASE_BRANCH" \
  "$PREVIOUS_SHA" \
  "$RESOLVED_TARGET_SHA" \
  "$TARGET_REF" \
  "$BACKUP_LOG" \
  "$BACKUP_DUMP" \
  "$BACKUP_FILES" \
  "$BACKUP_MANIFEST" \
  "$COMPOSE_STATUS"

echo
echo "Deploy concluido com sucesso."
echo "SHA anterior: $PREVIOUS_SHA"
echo "SHA atual: $RESOLVED_TARGET_SHA"
echo "Estado salvo em: $LATEST_STATE_FILE"
echo "Relatorio: $REPORT_FILE"

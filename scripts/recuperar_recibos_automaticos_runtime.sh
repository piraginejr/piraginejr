#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Reusa a resolucao do runtime Docker ja adotada no deploy local/cloud.
# shellcheck source=/dev/null
. "$SCRIPT_DIR/cloud_release_common.sh"

SEND_PENDING=0
DRAIN=1
LIMIT=40
SLEEP_SECONDS=3
PAUSE_EVERY=40
PAUSE_SECONDS=60

usage() {
  cat <<EOF
Uso: scripts/recuperar_recibos_automaticos_runtime.sh [opcoes]

Verifica envelopes e extratos nativos que deveriam ter recibo automatico,
reenfileira o que estiver faltando e, opcionalmente, processa a fila.

Opcoes:
  --send-pending          Depois da verificacao, envia a fila pendente.
  --no-drain              Processa apenas uma rodada da fila quando usado com --send-pending.
  --limit N               Quantidade maxima por rodada da fila (padrao: 40).
  --sleep-seconds N       Espera entre envios (padrao: 3).
  --pause-every N         Pausa maior a cada N envios (padrao: 40).
  --pause-seconds N       Duracao da pausa maior (padrao: 60).
  --help                  Mostra esta ajuda.

Exemplos:
  scripts/recuperar_recibos_automaticos_runtime.sh
  scripts/recuperar_recibos_automaticos_runtime.sh --send-pending
  scripts/recuperar_recibos_automaticos_runtime.sh --send-pending --limit 20 --pause-every 20
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --send-pending)
      SEND_PENDING=1
      shift
      ;;
    --no-drain)
      DRAIN=0
      shift
      ;;
    --limit)
      LIMIT="${2:?Informe um valor para --limit}"
      shift 2
      ;;
    --sleep-seconds)
      SLEEP_SECONDS="${2:?Informe um valor para --sleep-seconds}"
      shift 2
      ;;
    --pause-every)
      PAUSE_EVERY="${2:?Informe um valor para --pause-every}"
      shift 2
      ;;
    --pause-seconds)
      PAUSE_SECONDS="${2:?Informe um valor para --pause-seconds}"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Opcao invalida: $1" >&2
      echo >&2
      usage >&2
      exit 1
      ;;
  esac
done

ensure_runtime_prerequisites

DOCKER_BIN="$(resolve_docker_bin || true)"
if [[ -z "$DOCKER_BIN" ]]; then
  echo "Docker nao encontrado para recuperar recibos automaticos." >&2
  exit 1
fi

BACKFILL_CMD=(
  "$DOCKER_BIN" compose
  --env-file "$ENV_FILE"
  -f "$COMPOSE_FILE"
  exec -T power-church-django-runtime
  python manage.py backfill_automatic_event_receipts
)

QUEUE_CMD=(
  "$DOCKER_BIN" compose
  --env-file "$ENV_FILE"
  -f "$COMPOSE_FILE"
  exec -T power-church-django-runtime
  python manage.py process_receipt_dispatch_queue
  --pending-only
  --limit "$LIMIT"
  --sleep-seconds "$SLEEP_SECONDS"
  --pause-every "$PAUSE_EVERY"
  --pause-seconds "$PAUSE_SECONDS"
)

if [[ "$DRAIN" == "1" ]]; then
  QUEUE_CMD+=(--drain)
fi

echo "Runtime: $RUNTIME_DIR"
echo "Env file: $ENV_FILE"
echo
echo "1. Verificando e reenfileirando recibos automaticos faltantes..."
"${BACKFILL_CMD[@]}"

if [[ "$SEND_PENDING" != "1" ]]; then
  echo
  echo "Nenhum envio realizado."
  echo "Para disparar a fila depois da verificacao, rode novamente com --send-pending."
  exit 0
fi

echo
echo "2. Processando fila pendente de recibos..."
"${QUEUE_CMD[@]}"

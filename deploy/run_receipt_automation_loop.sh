#!/bin/sh
set -eu

enabled="${POWER_CHURCH_RECEIPT_AUTOMATION_ENABLED:-true}"
if [ "$enabled" != "true" ]; then
  exit 0
fi

auto_email="${POWER_CHURCH_RECEIPT_AUTO_EMAIL_ENABLED:-false}"
auto_send="${POWER_CHURCH_RECEIPT_AUTO_SEND_ENABLED:-false}"
force_run="${POWER_CHURCH_RECEIPT_AUTOMATION_FORCE:-false}"

if [ "$force_run" != "true" ] && [ "$auto_email" != "true" ] && [ "$auto_send" != "true" ]; then
  echo "Rotina automatica de recibos ignorada: auto_email e auto_send desabilitados." >&2
  exit 0
fi

interval_seconds="${POWER_CHURCH_RECEIPT_AUTOMATION_INTERVAL_SECONDS:-300}"
drain_queue="${POWER_CHURCH_RECEIPT_AUTOMATION_DRAIN_QUEUE:-true}"
limit="${POWER_CHURCH_RECEIPT_AUTOMATION_LIMIT:-40}"
sleep_seconds="${POWER_CHURCH_RECEIPT_AUTOMATION_SLEEP_SECONDS:-3}"
pause_every="${POWER_CHURCH_RECEIPT_AUTOMATION_PAUSE_EVERY:-40}"
pause_seconds="${POWER_CHURCH_RECEIPT_AUTOMATION_PAUSE_SECONDS:-60}"
state_dir="${POWER_CHURCH_RECEIPT_AUTOMATION_STATE_DIR:-/app/data/runtime_flags}"
log_file="${POWER_CHURCH_RECEIPT_AUTOMATION_LOG_FILE:-/app/logs/receipt_automation.log}"
pid_file="$state_dir/receipt_automation.pid"
lock_dir="$state_dir/receipt_automation.lock"

mkdir -p "$state_dir" /app/logs
cd /app/power_church_django

log() {
  message="$1"
  timestamp="$(date -Iseconds)"
  echo "$timestamp $message" >> "$log_file"
  echo "$message" >&2
}

cleanup() {
  rm -f "$pid_file"
  if [ -d "$lock_dir" ]; then
    rmdir "$lock_dir" 2>/dev/null || true
  fi
}

trap cleanup EXIT INT TERM
echo "$$" > "$pid_file"

run_cycle() {
  if ! mkdir "$lock_dir" 2>/dev/null; then
    log "Rotina automatica de recibos pulada: outro ciclo ainda esta em execucao."
    return 0
  fi

  log "Rotina automatica de recibos iniciada."
  if ! python manage.py backfill_automatic_event_receipts >> "$log_file" 2>&1; then
    log "Falha ao reenfileirar recibos automaticos pendentes."
  fi

  if [ "$drain_queue" = "true" ] && [ "$auto_send" = "true" ]; then
    if ! python manage.py process_receipt_dispatch_queue \
      --pending-only \
      --drain \
      --limit "$limit" \
      --sleep-seconds "$sleep_seconds" \
      --pause-every "$pause_every" \
      --pause-seconds "$pause_seconds" \
      >> "$log_file" 2>&1; then
      log "Falha ao drenar a fila automatica de recibos."
    fi
  else
    log "Drenagem automatica nao executada nesta rodada."
  fi

  rmdir "$lock_dir" 2>/dev/null || true
  log "Rotina automatica de recibos concluida."
}

run_cycle
while :; do
  sleep "$interval_seconds"
  run_cycle
done

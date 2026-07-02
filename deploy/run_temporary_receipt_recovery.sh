#!/bin/sh
set -eu

enabled="${POWER_CHURCH_TEMP_RECEIPT_RECOVERY_ENABLED:-false}"
if [ "$enabled" != "true" ]; then
  exit 0
fi

stamp_name="${POWER_CHURCH_TEMP_RECEIPT_RECOVERY_STAMP:-}"
if [ -z "$stamp_name" ]; then
  echo "POWER_CHURCH_TEMP_RECEIPT_RECOVERY_STAMP nao informado; rotina temporaria ignorada." >&2
  exit 0
fi

runtime_flag_dir="${POWER_CHURCH_TEMP_RECEIPT_RECOVERY_FLAG_DIR:-/app/data/runtime_flags}"
mkdir -p "$runtime_flag_dir" /app/logs

stamp_file="$runtime_flag_dir/${stamp_name}.done"
log_file="/app/logs/${stamp_name}.log"

if [ -f "$stamp_file" ]; then
  echo "Rotina temporaria de recibos ja executada: $stamp_name"
  exit 0
fi

cd /app/power_church_django

echo "==== $(date -Iseconds) :: iniciando recuperacao temporaria de recibos ====" >> "$log_file"
echo "Stamp: $stamp_name" >> "$log_file"

python manage.py backfill_automatic_event_receipts >> "$log_file" 2>&1

drain_queue="${POWER_CHURCH_TEMP_RECEIPT_RECOVERY_DRAIN_QUEUE:-true}"
if [ "$drain_queue" = "true" ]; then
  python manage.py process_receipt_dispatch_queue \
    --drain \
    --limit "${POWER_CHURCH_TEMP_RECEIPT_RECOVERY_LIMIT:-40}" \
    --sleep-seconds "${POWER_CHURCH_TEMP_RECEIPT_RECOVERY_SLEEP_SECONDS:-3}" \
    --pause-every "${POWER_CHURCH_TEMP_RECEIPT_RECOVERY_PAUSE_EVERY:-40}" \
    --pause-seconds "${POWER_CHURCH_TEMP_RECEIPT_RECOVERY_PAUSE_SECONDS:-60}" \
    >> "$log_file" 2>&1
fi

date -Iseconds > "$stamp_file"
echo "==== $(date -Iseconds) :: recuperacao temporaria concluida ====" >> "$log_file"
echo "Rotina temporaria de recibos concluida: $stamp_name"

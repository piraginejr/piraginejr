#!/bin/sh
set -eu

group_name="${1:-}"
if [ -z "$group_name" ]; then
  echo "Uso: run_runtime_hook_group.sh <startup|background>" >&2
  exit 1
fi

hooks_dir="/app/deploy/runtime_${group_name}.d"
log_file="/app/logs/runtime_hooks_${group_name}.log"

mkdir -p /app/logs

log() {
  timestamp="$(date -Iseconds)"
  echo "$timestamp $1" >> "$log_file"
  echo "$1" >&2
}

if [ ! -d "$hooks_dir" ]; then
  log "Grupo de hooks inexistente: $hooks_dir"
  exit 0
fi

found_hook=0
for hook in "$hooks_dir"/*.sh; do
  if [ ! -e "$hook" ]; then
    continue
  fi
  found_hook=1
  if [ ! -x "$hook" ]; then
    log "Hook ignorado (nao executavel): $hook"
    continue
  fi
  log "Executando hook ${group_name}: $(basename "$hook")"
  if ! "$hook" >> "$log_file" 2>&1; then
    log "Hook ${group_name} falhou: $(basename "$hook")"
    continue
  fi
  log "Hook ${group_name} concluido: $(basename "$hook")"
done

if [ "$found_hook" = "0" ]; then
  log "Nenhum hook registrado para o grupo ${group_name}."
fi

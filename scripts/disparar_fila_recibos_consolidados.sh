#!/bin/bash
set -euo pipefail

ROOT="/Users/piraginejr/Documents/New project/Teste/Power Church/power_church_django"
cd "$ROOT"

./.venv/bin/python manage.py process_receipt_dispatch_queue \
  --campaign-key retroativo_consolidado:2026-05-31 \
  --drain \
  --limit 40 \
  --sleep-seconds 3 \
  --pause-every 40 \
  --pause-seconds 60

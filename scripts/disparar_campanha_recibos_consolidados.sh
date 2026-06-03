#!/bin/bash
set -euo pipefail

ROOT="/Users/piraginejr/Documents/New project/Teste/Power Church/power_church_django"
cd "$ROOT"

./.venv/bin/python manage.py run_consolidated_receipt_campaign \
  --cutoff-date 2026-05-31 \
  --emission-date "$(date +%F)" \
  --batch-size 40 \
  --sleep-seconds 3 \
  --pause-every 40 \
  --pause-seconds 60

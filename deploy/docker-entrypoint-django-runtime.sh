#!/bin/sh
set -eu

mkdir -p \
  /app/data/backups \
  /app/data/branding \
  /app/data/envelope_uploads \
  /app/data/fotos_membros \
  /app/data/homologacao \
  /app/data/legacy \
  /app/data/people_uploads \
  /app/data/pix_uploads \
  /app/data/statement_uploads \
  /app/logs \
  /app/reports

python -c '
import os
import socket
import sys
import time

host = os.environ.get("POWER_CHURCH_POSTGRES_HOST", "postgres-runtime")
port = int(os.environ.get("POWER_CHURCH_POSTGRES_PORT", "5432"))
deadline = time.time() + 90
last_error = None

while time.time() < deadline:
    try:
        with socket.create_connection((host, port), timeout=3):
            sys.exit(0)
    except OSError as exc:
        last_error = exc
        time.sleep(1)

raise SystemExit(f"PostgreSQL indisponivel em {host}:{port}: {last_error}")
'

cd /app/power_church_django
python manage.py migrate --noinput
python manage.py collectstatic --noinput

/app/deploy/run_runtime_hook_group.sh startup || true
/app/deploy/run_runtime_hook_group.sh background &

exec gunicorn power_church_site.wsgi:application --bind 0.0.0.0:8000

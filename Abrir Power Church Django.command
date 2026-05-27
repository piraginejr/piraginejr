#!/bin/zsh
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
DJANGO_DIR="$BASE_DIR/power_church_django"
PYTHON_BIN="$DJANGO_DIR/.venv/bin/python"
ENV_FILE="$BASE_DIR/.env.power_church_django.local"
HOST="${POWER_CHURCH_DJANGO_HOST:-127.0.0.1}"
PORT="${POWER_CHURCH_DJANGO_PORT:-63620}"
URL="http://$HOST:$PORT/"
HEALTH_URL="$URL"

cd "$BASE_DIR"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  source "$ENV_FILE"
  set +a
fi

HOST="${POWER_CHURCH_DJANGO_HOST:-127.0.0.1}"
PORT="${POWER_CHURCH_DJANGO_PORT:-63620}"
URL="http://$HOST:$PORT/"
HEALTH_URL="$URL"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Nao encontrei o Python do Django em:"
  echo "$PYTHON_BIN"
  echo
  echo "Execute a instalacao local do Django antes de abrir por este atalho."
  echo "Pressione ENTER para fechar."
  read -r
  exit 1
fi

if /usr/sbin/lsof -ti "tcp:$PORT" >/dev/null 2>&1; then
  if /usr/bin/curl -fsS --max-time 2 "$HEALTH_URL" >/dev/null 2>&1; then
    echo "Power Church Django ja esta rodando em $URL"
    if [[ "${POWER_CHURCH_DJANGO_NO_BROWSER:-0}" != "1" ]]; then
      /usr/bin/open "$URL"
    fi
    echo
    echo "Pressione ENTER para fechar esta janela."
    read -r
    exit 0
  fi

  echo "Encontrei um processo na porta $PORT, mas ele nao respondeu ao teste HTTP."
  echo "Reiniciando o servidor Django para evitar tela vazia ou travada..."
  /usr/sbin/lsof -ti "tcp:$PORT" | while read -r existing_pid; do
    if [[ -n "$existing_pid" ]]; then
      /bin/kill "$existing_pid" >/dev/null 2>&1 || true
    fi
  done
  sleep 1
fi

export POWER_CHURCH_DJANGO_ALLOWED_HOSTS="${POWER_CHURCH_DJANGO_ALLOWED_HOSTS:-127.0.0.1,localhost}"
export POWER_CHURCH_DJANGO_DB_PATH="${POWER_CHURCH_DJANGO_DB_PATH:-$BASE_DIR/data/power_church_django.sqlite3}"
export POWER_CHURCH_LEGACY_DB_PATH="${POWER_CHURCH_LEGACY_DB_PATH:-$BASE_DIR/data/power_church_membros_importado.db}"
export PYTHONPYCACHEPREFIX="${PYTHONPYCACHEPREFIX:-/private/tmp/power_church_pycache}"

echo "Abrindo Power Church Django..."
echo "Endereco: $URL"
echo
echo "Para fechar o sistema, volte nesta janela e pressione CONTROL+C."

echo "Aplicando migracoes Django pendentes, se houver..."
"$PYTHON_BIN" "$DJANGO_DIR/manage.py" migrate --noinput
"$PYTHON_BIN" "$DJANGO_DIR/manage.py" setup_access_profiles
echo

if [[ "${POWER_CHURCH_DJANGO_NO_BROWSER:-0}" != "1" ]]; then
  (
    sleep 2
    /usr/bin/open "$URL"
  ) &
fi

exec "$PYTHON_BIN" "$DJANGO_DIR/manage.py" runserver "$HOST:$PORT" --noreload

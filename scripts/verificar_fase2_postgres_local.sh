#!/bin/zsh
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BASE_ENV_FILE="$BASE_DIR/.env.power_church_django.local"
OVERRIDE_ENV_FILE="$BASE_DIR/.env.power_church_django.postgres.local"
PYTHON_BIN="$BASE_DIR/power_church_django/.venv/bin/python"
POSTGRES_APP_BIN="/Applications/Postgres.app/Contents/Versions/latest/bin"

if [[ -f "$BASE_ENV_FILE" ]]; then
  set -a
  source "$BASE_ENV_FILE"
  set +a
fi

if [[ -f "$OVERRIDE_ENV_FILE" ]]; then
  set -a
  source "$OVERRIDE_ENV_FILE"
  set +a
fi

HOST="${POWER_CHURCH_POSTGRES_HOST:-127.0.0.1}"
PORT="${POWER_CHURCH_POSTGRES_PORT:-5432}"
DB="${POWER_CHURCH_POSTGRES_DB:-power_church}"
USER_NAME="${POWER_CHURCH_POSTGRES_USER:-power_church}"

echo "== Fase 2 - Verificacao PostgreSQL local =="
echo "Host: $HOST"
echo "Porta: $PORT"
echo "Banco: $DB"
echo "Usuario: $USER_NAME"
echo

if command -v psql >/dev/null 2>&1; then
  echo "[OK] psql encontrado em: $(command -v psql)"
elif [[ -x "$POSTGRES_APP_BIN/psql" ]]; then
  echo "[OK] psql encontrado no Postgres.app: $POSTGRES_APP_BIN/psql"
else
  echo "[AVISO] psql nao encontrado no PATH."
fi

if command -v postgres >/dev/null 2>&1; then
  echo "[OK] postgres encontrado em: $(command -v postgres)"
elif [[ -x "$POSTGRES_APP_BIN/postgres" ]]; then
  echo "[OK] postgres encontrado no Postgres.app: $POSTGRES_APP_BIN/postgres"
else
  echo "[AVISO] postgres nao encontrado no PATH."
fi

if command -v docker >/dev/null 2>&1; then
  echo "[OK] docker encontrado em: $(command -v docker)"
else
  echo "[AVISO] docker nao encontrado no PATH."
fi

echo
echo "Testando conexao TCP em $HOST:$PORT ..."
if /usr/bin/nc -z "$HOST" "$PORT" >/dev/null 2>&1; then
  echo "[OK] Porta PostgreSQL respondeu."
else
  echo "[BLOQUEIO] Nenhum servidor PostgreSQL respondeu em $HOST:$PORT."
fi

echo
echo "Testando driver psycopg no ambiente virtual ..."
"$PYTHON_BIN" - <<'PY'
import os
import sys

try:
    import psycopg
except Exception as exc:  # pragma: no cover
    print(f"[ERRO] psycopg indisponivel: {exc}")
    sys.exit(1)

host = os.environ.get("POWER_CHURCH_POSTGRES_HOST", "127.0.0.1")
port = os.environ.get("POWER_CHURCH_POSTGRES_PORT", "5432")
dbname = os.environ.get("POWER_CHURCH_POSTGRES_DB", "power_church")
user = os.environ.get("POWER_CHURCH_POSTGRES_USER", "power_church")
password = os.environ.get("POWER_CHURCH_POSTGRES_PASSWORD", "")

print(f"[OK] psycopg {psycopg.__version__} carregado.")

try:
    psycopg.connect(
        host=host,
        port=port,
        dbname=dbname,
        user=user,
        password=password,
        connect_timeout=3,
    ).close()
except Exception as exc:
    print(f"[BLOQUEIO] Conexao PostgreSQL ainda indisponivel: {exc}")
    sys.exit(2)

print("[OK] Conexao PostgreSQL validada.")
PY

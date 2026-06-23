#!/bin/zsh
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
RUNTIME_URL="${POWER_CHURCH_RUNTIME_URL:-http://127.0.0.1:8001/accounts/login/}"

cd "$BASE_DIR"

echo "Abrindo Power Church Django + PostgreSQL (runtime Docker)..."
echo "Endereco: $RUNTIME_URL"
if [[ "${POWER_CHURCH_RUNTIME_FORCE_BUILD:-0}" == "1" ]]; then
  echo "Modo: rebuild completo do runtime"
else
  echo "Modo: abertura rapida, sem rebuild"
fi
echo

if ! zsh "$BASE_DIR/scripts/subir_runtime_postgres_local.sh"; then
  echo
  echo "Falha ao subir o runtime PostgreSQL."
  echo "Pressione ENTER para fechar."
  read -r
  exit 1
fi

if [[ "${POWER_CHURCH_DJANGO_NO_BROWSER:-0}" != "1" ]]; then
  /usr/bin/open "$RUNTIME_URL" >/dev/null 2>&1 || true
fi

echo
echo "Runtime PostgreSQL pronto."
echo "Pressione ENTER para fechar esta janela."
read -r

#!/bin/zsh
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"

cd "$BASE_DIR"
echo "Executando Powerbackup do runtime Docker..."
echo

if ! "$BASE_DIR/powerbackup"; then
  echo
  echo "Falha ao executar o powerbackup."
  echo "Pressione ENTER para fechar."
  read -r
  exit 1
fi

echo
echo "Powerbackup concluido."
echo "Pressione ENTER para fechar."
read -r

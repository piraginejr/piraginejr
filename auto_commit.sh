#!/bin/bash

set -euo pipefail

PROJECT_DIR="/Users/piraginejr/Documents/New project/Teste/Power Church"
cd "$PROJECT_DIR" || exit 1

DRY_RUN=0
PUSH_AFTER_COMMIT=1
CUSTOM_MSG=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      DRY_RUN=1
      ;;
    --no-push)
      PUSH_AFTER_COMMIT=0
      ;;
    -m|--message)
      shift
      if [[ $# -eq 0 ]]; then
        echo "Informe a mensagem depois de $1."
        exit 1
      fi
      CUSTOM_MSG="$1"
      ;;
    *)
      echo "Opcao desconhecida: $1"
      echo "Uso: ./auto_commit.sh [--dry-run] [--no-push] [-m \"mensagem\"]"
      exit 1
      ;;
  esac
  shift
done

echo "======================================"
echo " Power Church Runtime Git Backup"
echo "======================================"
echo ""
echo "Escopo: runtime Docker/PostgreSQL, codigo Django, scripts, Docker e documentacao."
echo "Ficam fora: data operacional, bancos locais, volumes do runtime e arquivos .env."
echo ""

SCOPE_PATHS=(
  ".dockerignore"
  ".gitignore"
  "auto_commit.sh"
  "Dockerfile.django"
  "docker-compose.runtime.yml"
  "deploy"
  "power_church_core"
  "power_church_django"
  "scripts"
  "Abrir Power Church Django PostgreSQL.command"
  "Abrir Power Church.command"
)

for doc in *.md; do
  if [[ -e "$doc" ]]; then
    SCOPE_PATHS+=("$doc")
  fi
done

CHANGED_FILES="$(git status --short -- "${SCOPE_PATHS[@]}")"
if [[ -z "$CHANGED_FILES" ]]; then
  echo "Nenhuma alteracao dentro do escopo do runtime."
  exit 0
fi

echo "Arquivos do backup Git do runtime:"
echo "$CHANGED_FILES"
echo ""

if [[ "$DRY_RUN" == "1" ]]; then
  echo "Dry-run: nenhum arquivo foi adicionado, commitado ou enviado."
  exit 0
fi

DATA="$(date +"%Y-%m-%d %H:%M")"
MSG="${CUSTOM_MSG:-Backup runtime Power Church Django PostgreSQL - $DATA}"

echo "Mensagem do commit:"
echo "$MSG"
echo ""

git add -A -- "${SCOPE_PATHS[@]}"

STAGED_SCOPE="$(git diff --cached --name-only -- "${SCOPE_PATHS[@]}")"
if [[ -z "$STAGED_SCOPE" ]]; then
  echo "Nenhuma alteracao rastreavel do runtime foi preparada para commit."
  exit 0
fi

echo "Arquivos staged neste backup:"
echo "$STAGED_SCOPE"
echo ""

if ! git commit -m "$MSG" -- "${SCOPE_PATHS[@]}"; then
  echo ""
  echo "======================================"
  echo " Falha ao criar o commit do runtime"
  echo "======================================"
  exit 1
fi

if [[ "$PUSH_AFTER_COMMIT" == "0" ]]; then
  echo ""
  echo "======================================"
  echo " Commit do runtime criado sem push"
  echo "======================================"
  exit 0
fi

if ! git push; then
  echo ""
  echo "======================================"
  echo " Push bloqueado ou com falha"
  echo " O backup local do runtime foi criado, mas nao enviado ao GitHub"
  echo "======================================"
  exit 1
fi

echo ""
echo "======================================"
echo " Backup do runtime enviado com sucesso"
echo "======================================"

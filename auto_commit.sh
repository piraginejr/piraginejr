#!/bin/bash

set -e

# Vai automaticamente para a pasta do projeto
cd "/Users/piraginejr/Documents/New project/Teste/Power Church" || exit

echo "======================================"
echo " Power Church Auto Backup GitHub"
echo "======================================"
echo ""

# Verifica alterações
if [[ -z $(git status --porcelain) ]]; then
    echo "Nenhuma alteração encontrada."
    exit 0
fi

echo "Arquivos alterados:"
git status --short

echo ""

# Mensagem automática
DATA=$(date +"%Y-%m-%d %H:%M")
MSG="Auto backup Power Church - $DATA"

echo "Mensagem automática:"
echo "$MSG"

echo ""

# Commit automático
git add .
if git diff --cached --quiet; then
    echo "Nenhuma alteração rastreável para commit."
    exit 0
fi

if ! git commit -m "$MSG"; then
    echo ""
    echo "======================================"
    echo " Falha ao criar o commit automático"
    echo "======================================"
    exit 1
fi

if ! git push; then
    echo ""
    echo "======================================"
    echo " Push bloqueado ou com falha"
    echo " O backup local foi criado, mas nao foi enviado ao GitHub"
    echo "======================================"
    exit 1
fi

echo ""
echo "======================================"
echo " Backup enviado com sucesso"
echo "======================================"

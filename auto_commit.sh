#!/bin/bash

echo "======================================"
echo " Power Church Auto Backup GitHub"
echo "======================================"
echo ""

# Verifica mudanças
if [[ -z $(git status --porcelain) ]]; then
    echo "Nenhuma alteração encontrada."
    exit 0
fi

echo "Arquivos alterados:"
git status --short

echo ""

# Gera mensagem automática baseada na data
DATA=$(date +"%Y-%m-%d %H:%M")

MSG="Auto backup Power Church - $DATA"

echo "Mensagem automática:"
echo "$MSG"

echo ""

# Adiciona tudo
git add .

# Commit
git commit -m "$MSG"

# Push
git push

echo ""
echo "======================================"
echo " Backup enviado com sucesso"

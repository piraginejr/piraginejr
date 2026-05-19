#!/bin/bash

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
git commit -m "$MSG"
git push

echo ""
echo "======================================"
echo " Backup enviado com sucesso"
echo "======================================"

#!/bin/bash

echo "=============================="
echo " Backup Power Church no GitHub"
echo "=============================="
echo ""

echo "Arquivos modificados:"
git status --short

echo ""
read -p "Digite a mensagem do commit: " msg

if [ -z "$msg" ]; then
  echo "Commit cancelado: mensagem vazia."
  exit 1
fi

git add .
git commit -m "$msg"
git push

echo ""
echo "Backup enviado para o GitHub com sucesso."

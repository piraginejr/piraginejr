#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if command -v apt-get >/dev/null 2>&1; then
  sudo apt-get update
  grep -vE '^\s*(#|$)' deploy/system/ubuntu-24.04.txt | xargs sudo apt-get install -y
fi

python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r deploy/requirements/base.txt

python scripts/verificar_dependencias_servidor.py --profile server --report
POWER_CHURCH_PDF_PROVIDER=pymupdf python scripts/verificar_extratores_pdf.py --compare-provider pymupdf --report

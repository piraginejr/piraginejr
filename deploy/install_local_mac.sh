#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip

if [[ -f deploy/requirements/base.txt ]]; then
  python -m pip install -r deploy/requirements/base.txt
fi

python scripts/verificar_dependencias_servidor.py --profile local --report
python scripts/verificar_funcionalidade_total.py --report

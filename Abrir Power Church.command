#!/bin/zsh
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$BASE_DIR"

exec /usr/bin/env python3 "$BASE_DIR/power_church_demo.py" "$@"

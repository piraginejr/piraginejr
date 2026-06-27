#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_DIR="$HOME/bin"
ZSHRC="$HOME/.zshrc"
BASHRC="$HOME/.bashrc"
BASH_PROFILE="$HOME/.bash_profile"
MANAGED_START="# >>> power_church_runtime >>>"
MANAGED_END="# <<< power_church_runtime <<<"
MANAGED_BLOCK=$'# >>> power_church_runtime >>>\nexport PATH="$HOME/bin:$PATH"\n# <<< power_church_runtime <<<'

mkdir -p "$BIN_DIR"
ln -sfn "$PROJECT_DIR/powerbackup" "$BIN_DIR/powerbackup"
ln -sfn "$PROJECT_DIR/auto_commit.sh" "$BIN_DIR/powergitbackup"

update_managed_block() {
  local file="$1"
  touch "$file"
  local tmp
  tmp="$(mktemp)"
  awk -v start="$MANAGED_START" -v end="$MANAGED_END" '
    $0 == start { skip=1; next }
    $0 == end { skip=0; next }
    !skip { print }
  ' "$file" > "$tmp"
  mv "$tmp" "$file"
  printf '\n%s\n' "$MANAGED_BLOCK" >> "$file"
}

remove_old_powerbackup_alias() {
  local file="$1"
  touch "$file"
  local tmp
  tmp="$(mktemp)"
  awk 'index($0, "alias powerbackup=") != 1 { print }' "$file" > "$tmp"
  mv "$tmp" "$file"
}

remove_old_powerbackup_alias "$ZSHRC"
update_managed_block "$ZSHRC"
update_managed_block "$BASHRC"

touch "$BASH_PROFILE"
if ! grep -Fq 'source ~/.bashrc' "$BASH_PROFILE"; then
  printf '\n[ -f ~/.bashrc ] && source ~/.bashrc\n' >> "$BASH_PROFILE"
fi

echo "Registro do powerbackup concluido."
echo "Comando global: powerbackup"
echo "Backup Git antigo preservado em: powergitbackup"

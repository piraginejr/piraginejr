#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BASE_REF="${1:-origin/main}"
TARGET_REF="${2:-HEAD}"
STAMP="$(date +%Y%m%d_%H%M%S)"
OUTPUT_ROOT="${3:-$PROJECT_DIR/dist/cloud_update_$STAMP}"
PACKAGE_ROOT="$OUTPUT_ROOT/package"
COMMITS_FILE="$OUTPUT_ROOT/COMMITS_PENDENTES.md"
FILES_FILE="$OUTPUT_ROOT/ARQUIVOS_ALTERADOS.txt"
README_FILE="$OUTPUT_ROOT/README_IMPLANTACAO.md"

mkdir -p "$OUTPUT_ROOT"

echo "Preparando entrega de atualizacao para a nuvem..."
echo "Projeto: $PROJECT_DIR"
echo "Base ref: $BASE_REF"
echo "Target ref: $TARGET_REF"
echo "Saida: $OUTPUT_ROOT"

if ! git -C "$PROJECT_DIR" rev-parse --verify "$BASE_REF" >/dev/null 2>&1; then
  echo "Ref base invalida: $BASE_REF" >&2
  exit 1
fi

if ! git -C "$PROJECT_DIR" rev-parse --verify "$TARGET_REF" >/dev/null 2>&1; then
  echo "Ref alvo invalida: $TARGET_REF" >&2
  exit 1
fi

if [[ -z "$(git -C "$PROJECT_DIR" log --oneline "$BASE_REF..$TARGET_REF")" ]]; then
  echo "Nao ha commits entre $BASE_REF e $TARGET_REF." >&2
  exit 1
fi

cat > "$COMMITS_FILE" <<EOF
# Commits Pendentes Para A Nuvem

Gerado em: $(date -Iseconds)
Projeto: $PROJECT_DIR
Intervalo: $BASE_REF..$TARGET_REF

## Lista Curta

EOF

git -C "$PROJECT_DIR" log --reverse --oneline "$BASE_REF..$TARGET_REF" >> "$COMMITS_FILE"

cat >> "$COMMITS_FILE" <<EOF

## Estatistica

EOF

git -C "$PROJECT_DIR" show --stat --oneline --summary $(git -C "$PROJECT_DIR" rev-list --reverse "$BASE_REF..$TARGET_REF") >> "$COMMITS_FILE"

git -C "$PROJECT_DIR" diff --name-only "$BASE_REF..$TARGET_REF" | sort > "$FILES_FILE"

cat > "$README_FILE" <<EOF
# Entrega De Atualizacao Da Nuvem

## Origem

- intervalo de commits: \`$BASE_REF..$TARGET_REF\`
- SHA final esperado: \`$(git -C "$PROJECT_DIR" rev-parse --short "$TARGET_REF")\`

## Conteudo

- \`COMMITS_PENDENTES.md\`: lista e estatistica dos commits
- \`ARQUIVOS_ALTERADOS.txt\`: arquivos alterados no intervalo
- \`package/\`: pacote de codigo para a nuvem

## Roteiro Da Atualizacao

Seguir o documento:

- \`deploy/ROTINA_ATUALIZACAO_NUVEM_RUNTIME.md\`

Resumo operacional:

1. backup antes da troca
2. substituir codigo pelo pacote
3. rebuildar o container Django
4. subir com \`docker compose\`
5. validar login, healthcheck e fluxo afetado
6. registrar o SHA implantado
EOF

"$SCRIPT_DIR/empacotar_entrega_operador_runtime.sh" "$PACKAGE_ROOT"

echo
echo "Entrega pronta."
echo "Resumo:"
echo "- Changelog: $COMMITS_FILE"
echo "- Arquivos: $FILES_FILE"
echo "- Pacote: $PACKAGE_ROOT/power_church_runtime_operator_package"
echo "- Instrucoes: $README_FILE"

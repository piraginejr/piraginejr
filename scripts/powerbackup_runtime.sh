#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEFAULT_RUNTIME_DIR="$HOME/power_church_postgres_runtime"
RUNTIME_DIR="${POWER_CHURCH_RUNTIME_DIR:-$DEFAULT_RUNTIME_DIR}"
COMPOSE_FILE="$ROOT_DIR/docker-compose.runtime.yml"
ENV_FILE="$RUNTIME_DIR/env/runtime.env"
STAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_DIR="${POWER_CHURCH_RUNTIME_BACKUP_DIR:-$RUNTIME_DIR/data/backups}"
PG_DUMP_TARGET="$BACKUP_DIR/power_church_postgres_runtime_${STAMP}.dump"
FILES_TARGET="$BACKUP_DIR/power_church_runtime_files_${STAMP}.tar.gz"
MANIFEST_TARGET="$BACKUP_DIR/power_church_runtime_${STAMP}.md"

resolve_docker_bin() {
  if command -v docker >/dev/null 2>&1; then
    command -v docker
    return 0
  fi
  for candidate in /usr/local/bin/docker /opt/homebrew/bin/docker /Applications/Docker.app/Contents/Resources/bin/docker; do
    if [[ -x "$candidate" ]]; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

usage() {
  cat <<EOF
Uso: scripts/powerbackup_runtime.sh

Gera backup do runtime Docker/PostgreSQL atual:
- dump logico do Postgres do container
- arquivo tar.gz dos dados operacionais persistentes do runtime

Variaveis opcionais:
- POWER_CHURCH_RUNTIME_DIR
- POWER_CHURCH_RUNTIME_BACKUP_DIR
EOF
}

if [[ "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

DOCKER_BIN="$(resolve_docker_bin || true)"
if [[ -z "$DOCKER_BIN" ]]; then
  echo "Docker nao encontrado para gerar backup do runtime." >&2
  exit 1
fi

if [[ ! -f "$COMPOSE_FILE" ]]; then
  echo "Arquivo de compose do runtime nao encontrado: $COMPOSE_FILE" >&2
  exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Arquivo de ambiente do runtime nao encontrado: $ENV_FILE" >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"

set -a
. "$ENV_FILE"
set +a

POSTGRES_USER="${POSTGRES_USER:-${POWER_CHURCH_POSTGRES_USER:-power_church}}"
POSTGRES_DB="${POSTGRES_DB:-${POWER_CHURCH_POSTGRES_DB:-power_church}}"

echo "Preparando backup do runtime Docker..."
echo "Runtime: $RUNTIME_DIR"
echo "Destino: $BACKUP_DIR"

"$DOCKER_BIN" compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d postgres-runtime >/dev/null

echo "Gerando dump logico do PostgreSQL..."
"$DOCKER_BIN" compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T postgres-runtime \
  sh -lc 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$PG_DUMP_TARGET"

echo "Compactando arquivos persistentes do runtime..."
runtime_items=()
for rel_path in \
  "env/runtime.env" \
  "reports" \
  "data/branding" \
  "data/envelope_uploads" \
  "data/fotos_membros" \
  "data/homologacao" \
  "data/legacy" \
  "data/people_uploads" \
  "data/pix_uploads" \
  "data/statement_uploads" \
  "data/power_church_membros_importado.db"
do
  if [[ -e "$RUNTIME_DIR/$rel_path" ]]; then
    runtime_items+=("$rel_path")
  fi
done

if [[ ${#runtime_items[@]} -eq 0 ]]; then
  echo "Nenhum arquivo operacional do runtime foi encontrado para compactar." >&2
  exit 1
fi

tar -czf "$FILES_TARGET" -C "$RUNTIME_DIR" "${runtime_items[@]}"

cat > "$MANIFEST_TARGET" <<EOF
# Powerbackup Runtime

Gerado em: $(date -Iseconds)
Runtime: $RUNTIME_DIR

## Artefatos

- Dump Postgres: $(basename "$PG_DUMP_TARGET")
- Arquivos persistentes: $(basename "$FILES_TARGET")

## Conteudo do tar.gz

$(printf '%s\n' "${runtime_items[@]}" | sed 's/^/- /')
EOF

echo
echo "Backup do runtime concluido."
echo "Dump Postgres: $PG_DUMP_TARGET"
echo "Arquivos persistentes: $FILES_TARGET"
echo "Manifesto: $MANIFEST_TARGET"

#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
OUTPUT_ROOT="${1:-$PROJECT_DIR/dist}"
PACKAGE_DIR="$OUTPUT_ROOT/power_church_runtime_operator_package"

echo "Gerando pacote enxuto do runtime para operador..."
echo "Origem: $PROJECT_DIR"
echo "Destino: $PACKAGE_DIR"

mkdir -p "$OUTPUT_ROOT"
rm -rf "$PACKAGE_DIR"
mkdir -p "$PACKAGE_DIR"

copy_tree() {
  local source="$1"
  local target="$2"
  mkdir -p "$(dirname "$target")"
  if command -v rsync >/dev/null 2>&1; then
    mkdir -p "$target"
    rsync -a \
      --exclude '.DS_Store' \
      --exclude '.venv' \
      --exclude '__pycache__' \
      --exclude '.pytest_cache' \
      --exclude '.mypy_cache' \
      --exclude '.ruff_cache' \
      --exclude 'db.sqlite3' \
      --exclude '*.sqlite3' \
      --exclude '*.pyc' \
      "$source"/ "$target"/
  else
    cp -R "$source" "$target"
  fi
}

copy_file() {
  local source="$1"
  local target="$2"
  mkdir -p "$(dirname "$target")"
  cp "$source" "$target"
}

copy_file "$PROJECT_DIR/Dockerfile.django" "$PACKAGE_DIR/Dockerfile.django"
copy_file "$PROJECT_DIR/docker-compose.runtime.yml" "$PACKAGE_DIR/docker-compose.runtime.yml"
copy_file "$PROJECT_DIR/.dockerignore" "$PACKAGE_DIR/.dockerignore"

copy_tree "$PROJECT_DIR/deploy" "$PACKAGE_DIR/deploy"
copy_tree "$PROJECT_DIR/power_church_core" "$PACKAGE_DIR/power_church_core"
copy_tree "$PROJECT_DIR/power_church_django" "$PACKAGE_DIR/power_church_django"
copy_tree "$PROJECT_DIR/scripts" "$PACKAGE_DIR/scripts"

cat > "$PACKAGE_DIR/README_OPERADOR_RUNTIME.md" <<'EOF'
# Pacote Do Runtime Power Church

Este pacote contem o codigo minimo para buildar e subir o container Django do runtime PostgreSQL.

Ele tambem inclui `scripts/` porque parte do runtime Django ainda importa utilitarios desse diretorio, especialmente no fluxo de importacao de pessoas.

## O Que Ja Deve Existir No Ambiente Do Operador

- pasta persistente do runtime, por exemplo `power_church_postgres_runtime/`
- `power_church_postgres_runtime/env/runtime.env`
- `power_church_postgres_runtime/postgres/`
- `power_church_postgres_runtime/data/`
- opcionalmente `power_church_postgres_runtime/reports/`
- opcionalmente `power_church_postgres_runtime/logs/`

## Estrutura Esperada

O `docker-compose.runtime.yml` usa a variavel `POWER_CHURCH_RUNTIME_DIR`.

Exemplo:

```bash
export POWER_CHURCH_RUNTIME_DIR=/caminho/do/power_church_postgres_runtime
```

## Build Da Imagem

Na raiz deste pacote:

```bash
docker build -f Dockerfile.django -t powerchurch-power-church-django-runtime .
```

## Subida Pelo Docker Compose

```bash
docker compose --env-file "$POWER_CHURCH_RUNTIME_DIR/env/runtime.env" -f docker-compose.runtime.yml build power-church-django-runtime
docker compose --env-file "$POWER_CHURCH_RUNTIME_DIR/env/runtime.env" -f docker-compose.runtime.yml up -d
```

## Observacoes

- o pacote nao inclui `data/`, `postgres/`, `.env` nem volumes operacionais;
- o pacote inclui `scripts/` porque o container Django faz `COPY . /app` e algumas rotinas importam modulos desse diretorio;
- o entrypoint roda `migrate` e `collectstatic` antes de subir o Gunicorn;
- o Postgres precisa estar acessivel conforme o `runtime.env`.
EOF

echo "Pacote gerado com sucesso."
echo "Conteudo principal:"
find "$PACKAGE_DIR" -maxdepth 2 | sort

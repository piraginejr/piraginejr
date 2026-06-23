#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DEFAULT_RUNTIME_DIR="$HOME/power_church_postgres_runtime"
RUNTIME_DIR="${POWER_CHURCH_RUNTIME_DIR:-$DEFAULT_RUNTIME_DIR}"
SOURCE_DATA_DIR="$PROJECT_DIR/data"
SOURCE_REPORTS_DIR="$PROJECT_DIR/reports"
ENV_TEMPLATE="$PROJECT_DIR/deploy/runtime.env.postgres.local.example"
ENV_TARGET="$RUNTIME_DIR/env/runtime.env"
RUNTIME_DATA_DIR="$RUNTIME_DIR/data"
SYNC_EXISTING_DATA=false
VERBOSE_SYNC=false
SYNC_ARCHIVES=false
LINK_EXISTING_DATA=true

echo "Preparando runtime em: $RUNTIME_DIR"

for arg in "$@"; do
  case "$arg" in
    --sync-existing-data)
      SYNC_EXISTING_DATA=true
      ;;
    --verbose-sync)
      VERBOSE_SYNC=true
      ;;
    --sync-archives)
      SYNC_ARCHIVES=true
      ;;
    --copy-existing-data)
      LINK_EXISTING_DATA=false
      ;;
  esac
done

mkdir -p \
  "$RUNTIME_DIR/env" \
  "$RUNTIME_DIR/logs" \
  "$RUNTIME_DIR/postgres" \
  "$RUNTIME_DIR/reports"

if [[ ! -e "$RUNTIME_DATA_DIR" ]]; then
  mkdir -p "$RUNTIME_DATA_DIR"
fi
if [[ ! -L "$RUNTIME_DATA_DIR" ]]; then
  mkdir -p \
    "$RUNTIME_DATA_DIR/backups" \
    "$RUNTIME_DATA_DIR/branding" \
    "$RUNTIME_DATA_DIR/envelope_uploads" \
    "$RUNTIME_DATA_DIR/fotos_membros" \
    "$RUNTIME_DATA_DIR/homologacao" \
    "$RUNTIME_DATA_DIR/legacy" \
    "$RUNTIME_DATA_DIR/people_uploads" \
    "$RUNTIME_DATA_DIR/pix_uploads" \
    "$RUNTIME_DATA_DIR/statement_uploads"
fi

echo "Diretorios do runtime garantidos."

if [[ ! -f "$ENV_TARGET" ]]; then
  echo "Criando runtime.env inicial..."
  cp "$ENV_TEMPLATE" "$ENV_TARGET"
fi

echo "Atualizando variaveis do runtime no runtime.env..."
python3 - "$ENV_TARGET" "$RUNTIME_DIR" "$PROJECT_DIR" <<'PY'
from pathlib import Path
import sys

env_path = Path(sys.argv[1])
runtime_dir = Path(sys.argv[2]).expanduser().resolve()
project_dir = Path(sys.argv[3]).expanduser().resolve()
raw_lines = env_path.read_text(encoding="utf-8").splitlines()
env = {}
ordered_keys = []
local_env = {}


def load_env_file(path: Path) -> dict[str, str]:
    payload: dict[str, str] = {}
    if not path.exists():
        return payload
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        payload[key] = value
    return payload

for line in raw_lines:
    if not line or line.lstrip().startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    key = key.strip()
    if key not in ordered_keys:
        ordered_keys.append(key)
    env[key] = value

for candidate in [
    project_dir / ".env.power_church_django.local",
    project_dir / ".env.power_church_django.postgres.local",
]:
    for key, value in load_env_file(candidate).items():
        if value:
            local_env[key] = value

env["POWER_CHURCH_RUNTIME_DIR"] = str(runtime_dir)
env["POWER_CHURCH_LEGACY_DB_PATH"] = "/app/data/power_church_membros_importado.db"
env["POWER_CHURCH_BRAND_LOGO_PATH"] = "/app/data/branding/power_church_logo.jpg"
env["POWER_CHURCH_ENVELOPE_DIR"] = "/app/data/envelope_uploads"
env["POWER_CHURCH_PHOTO_DIR"] = "/app/data/fotos_membros"
env["POWER_CHURCH_BACKUP_DIR"] = "/app/data/backups"
env["POWER_CHURCH_DJANGO_SECRET_KEY"] = env.get("POWER_CHURCH_DJANGO_SECRET_KEY") or "change-me-before-cloud"
env["POWER_CHURCH_DJANGO_DEBUG"] = env.get("POWER_CHURCH_DJANGO_DEBUG") or "false"
env["POWER_CHURCH_DJANGO_ALLOWED_HOSTS"] = env.get("POWER_CHURCH_DJANGO_ALLOWED_HOSTS") or "127.0.0.1,localhost"
env["POWER_CHURCH_EMAIL_BACKEND"] = local_env.get("POWER_CHURCH_EMAIL_BACKEND") or env.get("POWER_CHURCH_EMAIL_BACKEND") or "django.core.mail.backends.console.EmailBackend"
env["POWER_CHURCH_EMAIL_PROVIDER"] = local_env.get("POWER_CHURCH_EMAIL_PROVIDER") or env.get("POWER_CHURCH_EMAIL_PROVIDER") or "smtp"
env["POWER_CHURCH_DEFAULT_FROM_EMAIL"] = local_env.get("POWER_CHURCH_DEFAULT_FROM_EMAIL") or env.get("POWER_CHURCH_DEFAULT_FROM_EMAIL") or "recebimento@localhost"
env["POWER_CHURCH_RECEIPT_REPLY_TO"] = local_env.get("POWER_CHURCH_RECEIPT_REPLY_TO") or env.get("POWER_CHURCH_RECEIPT_REPLY_TO") or ""
env["POWER_CHURCH_RECEIPT_AUTO_EMAIL_ENABLED"] = local_env.get("POWER_CHURCH_RECEIPT_AUTO_EMAIL_ENABLED") or env.get("POWER_CHURCH_RECEIPT_AUTO_EMAIL_ENABLED") or "false"
env["POWER_CHURCH_RECEIPT_AUTO_SEND_ENABLED"] = local_env.get("POWER_CHURCH_RECEIPT_AUTO_SEND_ENABLED") or env.get("POWER_CHURCH_RECEIPT_AUTO_SEND_ENABLED") or "false"
env["POWER_CHURCH_GRAPH_TENANT_ID"] = local_env.get("POWER_CHURCH_GRAPH_TENANT_ID") or env.get("POWER_CHURCH_GRAPH_TENANT_ID") or ""
env["POWER_CHURCH_GRAPH_CLIENT_ID"] = local_env.get("POWER_CHURCH_GRAPH_CLIENT_ID") or env.get("POWER_CHURCH_GRAPH_CLIENT_ID") or ""
env["POWER_CHURCH_GRAPH_CLIENT_SECRET"] = local_env.get("POWER_CHURCH_GRAPH_CLIENT_SECRET") or env.get("POWER_CHURCH_GRAPH_CLIENT_SECRET") or ""
env["POWER_CHURCH_GRAPH_SENDER_USER"] = local_env.get("POWER_CHURCH_GRAPH_SENDER_USER") or env.get("POWER_CHURCH_GRAPH_SENDER_USER") or ""
env["POWER_CHURCH_GRAPH_SCOPE"] = local_env.get("POWER_CHURCH_GRAPH_SCOPE") or env.get("POWER_CHURCH_GRAPH_SCOPE") or "https://graph.microsoft.com/.default"
env["POWER_CHURCH_GRAPH_BASE_URL"] = local_env.get("POWER_CHURCH_GRAPH_BASE_URL") or env.get("POWER_CHURCH_GRAPH_BASE_URL") or "https://graph.microsoft.com/v1.0"
env["POWER_CHURCH_GRAPH_TIMEOUT_SECONDS"] = local_env.get("POWER_CHURCH_GRAPH_TIMEOUT_SECONDS") or env.get("POWER_CHURCH_GRAPH_TIMEOUT_SECONDS") or "30"
env["POWER_CHURCH_DATA_UPLOAD_MAX_NUMBER_FILES"] = env.get("POWER_CHURCH_DATA_UPLOAD_MAX_NUMBER_FILES") or "5000"
env["POWER_CHURCH_DATA_UPLOAD_MAX_NUMBER_FIELDS"] = env.get("POWER_CHURCH_DATA_UPLOAD_MAX_NUMBER_FIELDS") or "20000"

power_church_postgres_defaults = {
    "POWER_CHURCH_POSTGRES_DB": env.get("POWER_CHURCH_POSTGRES_DB") or env.get("POSTGRES_DB") or "power_church",
    "POWER_CHURCH_POSTGRES_USER": env.get("POWER_CHURCH_POSTGRES_USER") or env.get("POSTGRES_USER") or "power_church",
    "POWER_CHURCH_POSTGRES_PASSWORD": env.get("POWER_CHURCH_POSTGRES_PASSWORD") or env.get("POSTGRES_PASSWORD") or "power_church_dev",
    "POWER_CHURCH_POSTGRES_HOST": env.get("POWER_CHURCH_POSTGRES_HOST") or "postgres-runtime",
    "POWER_CHURCH_POSTGRES_PORT": env.get("POWER_CHURCH_POSTGRES_PORT") or "5432",
}

for key, value in power_church_postgres_defaults.items():
    env[key] = value
    if key not in ordered_keys:
        ordered_keys.append(key)

postgres_defaults = {
    "POSTGRES_DB": env.get("POSTGRES_DB") or env.get("POWER_CHURCH_POSTGRES_DB") or "power_church",
    "POSTGRES_USER": env.get("POSTGRES_USER") or env.get("POWER_CHURCH_POSTGRES_USER") or "power_church",
    "POSTGRES_PASSWORD": env.get("POSTGRES_PASSWORD") or env.get("POWER_CHURCH_POSTGRES_PASSWORD") or "power_church_dev",
}

for key, value in postgres_defaults.items():
    env[key] = value
    if key not in ordered_keys:
        ordered_keys.append(key)

if "POWER_CHURCH_RUNTIME_DIR" not in ordered_keys:
    ordered_keys.append("POWER_CHURCH_RUNTIME_DIR")
for key in [
    "POWER_CHURCH_LEGACY_DB_PATH",
    "POWER_CHURCH_BRAND_LOGO_PATH",
    "POWER_CHURCH_ENVELOPE_DIR",
    "POWER_CHURCH_PHOTO_DIR",
    "POWER_CHURCH_BACKUP_DIR",
    "POWER_CHURCH_DJANGO_SECRET_KEY",
    "POWER_CHURCH_DJANGO_DEBUG",
    "POWER_CHURCH_DJANGO_ALLOWED_HOSTS",
    "POWER_CHURCH_EMAIL_BACKEND",
    "POWER_CHURCH_EMAIL_PROVIDER",
    "POWER_CHURCH_DEFAULT_FROM_EMAIL",
    "POWER_CHURCH_RECEIPT_REPLY_TO",
    "POWER_CHURCH_RECEIPT_AUTO_EMAIL_ENABLED",
    "POWER_CHURCH_RECEIPT_AUTO_SEND_ENABLED",
    "POWER_CHURCH_GRAPH_TENANT_ID",
    "POWER_CHURCH_GRAPH_CLIENT_ID",
    "POWER_CHURCH_GRAPH_CLIENT_SECRET",
    "POWER_CHURCH_GRAPH_SENDER_USER",
    "POWER_CHURCH_GRAPH_SCOPE",
    "POWER_CHURCH_GRAPH_BASE_URL",
    "POWER_CHURCH_GRAPH_TIMEOUT_SECONDS",
    "POWER_CHURCH_DATA_UPLOAD_MAX_NUMBER_FILES",
    "POWER_CHURCH_DATA_UPLOAD_MAX_NUMBER_FIELDS",
]:
    if key not in ordered_keys:
        ordered_keys.append(key)

output_lines = []
handled = set()
for line in raw_lines:
    if not line or line.lstrip().startswith("#") or "=" not in line:
        output_lines.append(line)
        continue
    key, _ = line.split("=", 1)
    key = key.strip()
    if key in env:
        output_lines.append(f"{key}={env[key]}")
        handled.add(key)
    else:
        output_lines.append(line)

for key in ordered_keys:
    if key not in handled:
        output_lines.append(f"{key}={env[key]}")

env_path.write_text("\n".join(output_lines).rstrip() + "\n", encoding="utf-8")
PY
echo "runtime.env pronto."

copy_if_missing() {
  local source="$1"
  local target="$2"
  if [[ -f "$source" && ! -f "$target" ]]; then
    mkdir -p "$(dirname "$target")"
    echo "Copiando arquivo inicial: $source -> $target"
    cp "$source" "$target"
  fi
}

sync_dir() {
  local source="$1"
  local target="$2"
  local label="$3"
  if [[ ! -d "$source" ]]; then
    return
  fi
  mkdir -p "$target"
  echo "Sincronizando $label..."
  if [[ "$(uname -s)" == "Darwin" ]] && command -v ditto >/dev/null 2>&1; then
    if [[ "$VERBOSE_SYNC" == true ]]; then
      echo "Usando ditto no macOS para evitar timeouts do iCloud..."
    fi
    ditto "$source" "$target"
  elif command -v rsync >/dev/null 2>&1; then
    if [[ "$VERBOSE_SYNC" == true ]]; then
      rsync -a --progress "$source"/ "$target"/
    else
      rsync -a "$source"/ "$target"/
    fi
  else
    cp -R "$source"/. "$target"/
  fi
  echo "OK: $label"
}

is_empty_dir() {
  local dir="$1"
  [[ -d "$dir" ]] || return 1
  [[ -z "$(find "$dir" -mindepth 1 -maxdepth 1 2>/dev/null | head -n 1)" ]]
}

link_or_sync_dir() {
  local source="$1"
  local target="$2"
  local label="$3"
  if [[ ! -e "$source" ]]; then
    return
  fi
  if [[ "$LINK_EXISTING_DATA" == true ]]; then
    if [[ -L "$target" ]]; then
      echo "Link existente mantido: $label"
      return
    fi
    if is_empty_dir "$target"; then
      rmdir "$target" 2>/dev/null || true
      ln -s "$source" "$target"
      echo "Linkado $label -> $source"
      return
    fi
  fi
  sync_dir "$source" "$target" "$label"
}

link_or_sync_data_root() {
  local source="$1"
  local target="$2"
  if [[ ! -d "$source" ]]; then
    return
  fi
  if [[ "$LINK_EXISTING_DATA" == true ]]; then
    if [[ -L "$target" ]]; then
      echo "Link de data existente mantido."
      return
    fi
    if is_empty_dir "$target"; then
      rmdir "$target" 2>/dev/null || true
      ln -s "$source" "$target"
      echo "Linkado data -> $source"
      return
    fi
  fi
  sync_dir "$source" "$target" "data"
}

sync_old_runtime_dir_if_needed() {
  local old_path="$1"
  local new_path="$2"
  local label="$3"
  if [[ ! -e "$old_path" || -L "$RUNTIME_DATA_DIR" ]]; then
    return
  fi
  if is_empty_dir "$new_path"; then
    sync_dir "$old_path" "$new_path" "$label"
  fi
}

copy_if_missing \
  "$RUNTIME_DIR/legacy/power_church_membros_importado.db" \
  "$RUNTIME_DATA_DIR/power_church_membros_importado.db"

sync_old_runtime_dir_if_needed "$RUNTIME_DIR/backups" "$RUNTIME_DATA_DIR/backups" "backups do layout antigo"
sync_old_runtime_dir_if_needed "$RUNTIME_DIR/branding" "$RUNTIME_DATA_DIR/branding" "branding do layout antigo"
sync_old_runtime_dir_if_needed "$RUNTIME_DIR/envelope_uploads" "$RUNTIME_DATA_DIR/envelope_uploads" "envelopes do layout antigo"
sync_old_runtime_dir_if_needed "$RUNTIME_DIR/fotos_membros" "$RUNTIME_DATA_DIR/fotos_membros" "fotos do layout antigo"
sync_old_runtime_dir_if_needed "$RUNTIME_DIR/homologacao" "$RUNTIME_DATA_DIR/homologacao" "homologacao do layout antigo"
sync_old_runtime_dir_if_needed "$RUNTIME_DIR/people_uploads" "$RUNTIME_DATA_DIR/people_uploads" "planilhas do layout antigo"
sync_old_runtime_dir_if_needed "$RUNTIME_DIR/pix_uploads" "$RUNTIME_DATA_DIR/pix_uploads" "pix do layout antigo"
sync_old_runtime_dir_if_needed "$RUNTIME_DIR/statement_uploads" "$RUNTIME_DATA_DIR/statement_uploads" "extratos do layout antigo"

copy_if_missing \
  "$SOURCE_DATA_DIR/power_church_membros_importado.db" \
  "$RUNTIME_DATA_DIR/power_church_membros_importado.db"

copy_if_missing \
  "$SOURCE_DATA_DIR/branding/power_church_logo.jpg" \
  "$RUNTIME_DATA_DIR/branding/power_church_logo.jpg"

echo "Arquivos base do runtime conferidos."

if [[ "$SYNC_EXISTING_DATA" == true ]]; then
  link_or_sync_data_root "$SOURCE_DATA_DIR" "$RUNTIME_DATA_DIR"
  link_or_sync_dir "$SOURCE_REPORTS_DIR" "$RUNTIME_DIR/reports" "relatorios"
  if [[ "$SYNC_ARCHIVES" == true ]]; then
    sync_dir "$SOURCE_DATA_DIR/backups" "$RUNTIME_DATA_DIR/backups" "backups"
  fi
fi

echo "Runtime preparado em: $RUNTIME_DIR"
echo "Arquivo de ambiente: $ENV_TARGET"
if [[ "$SYNC_EXISTING_DATA" == true ]]; then
  echo "Dados existentes sincronizados para o runtime novo."
  if [[ "$SYNC_ARCHIVES" != true ]]; then
    echo "Backups antigos ficaram de fora desta primeira carga para acelerar a subida local."
  fi
  if [[ "$LINK_EXISTING_DATA" == true ]]; then
    echo "Quando possivel, o runtime passou a apontar o data inteiro para a base local, sem recopia pasta por pasta."
  fi
else
  echo "Nenhum volume antigo foi copiado. Use --sync-existing-data para levar o data atual inteiro ao runtime novo."
fi

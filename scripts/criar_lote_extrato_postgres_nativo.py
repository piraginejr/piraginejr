from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DJANGO_DIR = ROOT / "power_church_django"
DJANGO_VENV_PYTHON = DJANGO_DIR / ".venv" / "bin" / "python"
DEFAULT_ENV_FILE = ROOT / ".env.power_church_django.postgres.local"
REPORT_DIR = ROOT / "data" / "homologacao"


def _load_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if value.startswith(("'", '"')) and value.endswith(("'", '"')) and len(value) >= 2:
            value = value[1:-1]
        env[key.strip()] = value
    return env


def _run_inside_venv() -> int | None:
    if not DJANGO_VENV_PYTHON.exists():
        return None
    if Path(sys.executable).resolve() == DJANGO_VENV_PYTHON.resolve():
        return None
    completed = os.spawnve(
        os.P_WAIT,
        str(DJANGO_VENV_PYTHON),
        [str(DJANGO_VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]],
        os.environ.copy(),
    )
    return int(completed)


def _write_report(*, pdf_path: Path, layout_code: str, lot_id: int, movement_count: int, total_value: str) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    target = REPORT_DIR / f"lote_extrato_postgres_nativo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    target.write_text(
        "\n".join(
            [
                "# Lote De Extrato Postgres Nativo",
                "",
                f"Gerado em: {datetime.now().isoformat(timespec='seconds')}",
                f"Arquivo: `{pdf_path}`",
                f"Layout: `{layout_code}`",
                f"Lote nativo: `{lot_id}`",
                f"Movimentos: `{movement_count}`",
                f"Total: `{total_value}`",
                f"URL do lote: `/imports/statement/{lot_id}/?backend=postgres_nativo`",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return target


def main() -> int:
    rerun = _run_inside_venv()
    if rerun is not None:
        return rerun

    parser = argparse.ArgumentParser(description="Cria um lote de extrato diretamente no Postgres nativo.")
    parser.add_argument("--pdf", required=True, help="Caminho do PDF bancario.")
    parser.add_argument("--layout", required=True, help="Codigo do layout bancario.")
    parser.add_argument("--provider", default="pymupdf", help="Provider PDF para leitura.")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE), help="Arquivo de ambiente do Postgres.")
    parser.add_argument("--report", action="store_true", help="Gera relatorio em data/homologacao.")
    args = parser.parse_args()

    env_file = Path(args.env_file).expanduser().resolve()
    os.environ.update(_load_env_file(env_file))
    os.environ.setdefault("POWER_CHURCH_POSTGRES_HOST", "127.0.0.1")
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "power_church_site.settings")
    os.environ.setdefault("PYTHONPYCACHEPREFIX", "/private/tmp/pycache_powerchurch")

    pdf_path = Path(args.pdf).expanduser().resolve()
    if not pdf_path.exists():
        print(f"PDF nao encontrado: {pdf_path}", file=sys.stderr)
        return 1

    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(DJANGO_DIR))

    import django

    django.setup()

    from power_church_django.apps.imports.services import create_statement_lot_postgres_native

    payload = pdf_path.read_bytes()
    lot = create_statement_lot_postgres_native(
        filename=pdf_path.name,
        payload=payload,
        layout_code=str(args.layout).strip().upper(),
        pdf_provider=str(args.provider).strip() or "pymupdf",
    )
    print(f"lot_id={lot.id}")
    print(f"backend={lot.source_backend}")
    print(f"movements={lot.movement_count}")
    print(f"total={lot.total_value}")
    print(f"url=/imports/statement/{lot.id}/?backend=postgres_nativo")
    if args.report:
        report = _write_report(
            pdf_path=pdf_path,
            layout_code=str(args.layout).strip().upper(),
            lot_id=int(lot.id),
            movement_count=int(lot.movement_count or 0),
            total_value=str(lot.total_value or 0),
        )
        print(f"report={report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

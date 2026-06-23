from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "power_church_membros_importado.db"
SANDBOX_DIR = ROOT / "data" / "sandboxes"
REPORT_DIR = ROOT / "data" / "homologacao"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "power_church_django.power_church_site.settings")

import django  # noqa: E402

django.setup()

from power_church_core.normalization import moneyless_int  # noqa: E402
from power_church_django.services.legacy_write import LegacyWriteError, merge_people  # noqa: E402


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def scalar(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> Any:
    row = conn.execute(sql, params).fetchone()
    return row[0] if row else None


def person_summary(conn: sqlite3.Connection, person_id: int) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT id, codigo_interno, nome, cpf, status, ativo, email_principal, telefone_principal, whatsapp_principal
          FROM pessoas
         WHERE id = ?
         LIMIT 1
        """,
        (person_id,),
    ).fetchone()
    if row is None:
        return {"id": person_id, "found": False}
    return {
        "id": moneyless_int(row["id"]),
        "found": True,
        "codigo": row["codigo_interno"] or "",
        "nome": row["nome"] or "",
        "cpf": row["cpf"] or "",
        "status": row["status"] or "",
        "ativo": bool(row["ativo"]),
        "email": row["email_principal"] or "",
        "telefone": row["telefone_principal"] or "",
        "whatsapp": row["whatsapp_principal"] or "",
        "contribuicoes": int(scalar(conn, "SELECT COUNT(*) FROM contribuicoes WHERE pessoa_id = ?", (person_id,)) or 0),
        "recibos": int(scalar(conn, "SELECT COUNT(*) FROM recibos WHERE pessoa_id = ?", (person_id,)) or 0),
        "contribuintes": int(scalar(conn, "SELECT COUNT(*) FROM contribuintes WHERE pessoa_id = ?", (person_id,)) or 0),
        "historico": int(scalar(conn, "SELECT COUNT(*) FROM pessoa_historico WHERE pessoa_id = ?", (person_id,)) or 0),
        "relacionamentos": int(
            scalar(
                conn,
                "SELECT COUNT(*) FROM pessoa_relacionamentos WHERE pessoa_id = ? OR pessoa_relacionada_id = ?",
                (person_id, person_id),
            )
            or 0
        ),
    }


def write_report(
    *,
    source_db: Path,
    sandbox_db: Path,
    primary_id: int,
    duplicate_id: int,
    reason: str,
    prefer_duplicate_name: bool,
    before_primary: dict[str, Any],
    before_duplicate: dict[str, Any],
    after_primary: dict[str, Any],
    after_duplicate: dict[str, Any],
    result: dict[str, Any],
) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    target = REPORT_DIR / f"merge_controlado_{primary_id}_{duplicate_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    lines = [
        "# Merge Controlado em Clone",
        "",
        f"Gerado em: {datetime.now().isoformat(timespec='seconds')}",
        "",
        f"- Banco operacional origem: `{source_db}`",
        f"- Banco clone usado no ensaio: `{sandbox_db}`",
        f"- Ficha principal: `#{primary_id}`",
        f"- Ficha duplicada: `#{duplicate_id}`",
        f"- Preferir nome da duplicada: `{'sim' if prefer_duplicate_name else 'nao'}`",
        f"- Justificativa: {reason}",
        "",
        "## Resultado",
        "",
        f"- Merge executado no clone com sucesso",
        f"- Trash da ficha duplicada: `#{result.get('duplicate_trash_id')}`",
        "",
        "## Antes",
        "",
        f"- Principal: `{before_primary.get('nome')}` · ativo={before_primary.get('ativo')} · contribuições={before_primary.get('contribuicoes')} · recibos={before_primary.get('recibos')} · contribuintes={before_primary.get('contribuintes')}",
        f"- Duplicada: `{before_duplicate.get('nome')}` · ativo={before_duplicate.get('ativo')} · contribuições={before_duplicate.get('contribuicoes')} · recibos={before_duplicate.get('recibos')} · contribuintes={before_duplicate.get('contribuintes')}",
        "",
        "## Depois",
        "",
        f"- Principal: `{after_primary.get('nome')}` · ativo={after_primary.get('ativo')} · contribuições={after_primary.get('contribuicoes')} · recibos={after_primary.get('recibos')} · contribuintes={after_primary.get('contribuintes')}",
        f"- Duplicada: `{after_duplicate.get('nome')}` · ativo={after_duplicate.get('ativo')} · contribuições={after_duplicate.get('contribuicoes')} · recibos={after_duplicate.get('recibos')} · contribuintes={after_duplicate.get('contribuintes')}",
        "",
        "## Contadores retornados pelo merge",
        "",
        "| Chave | Valor |",
        "| --- | ---: |",
    ]
    for key, value in sorted((result.get("counts") or {}).items()):
        lines.append(f"| {key} | `{value}` |")
    lines.extend(
        [
            "",
            "## Observacao",
            "",
            "Este ensaio gravou apenas no banco clone. A base principal permaneceu intacta.",
        ]
    )
    target.write_text("\n".join(lines), encoding="utf-8")
    return target


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Executa um merge controlado em banco clone.")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Banco operacional SQLite de origem.")
    parser.add_argument("--primary-id", required=True, type=int, help="ID da ficha principal.")
    parser.add_argument("--duplicate-id", required=True, type=int, help="ID da ficha duplicada.")
    parser.add_argument("--reason", required=True, help="Justificativa do merge.")
    parser.add_argument("--prefer-duplicate-name", action="store_true", help="Prefere o nome da ficha duplicada.")
    parser.add_argument("--report", action="store_true", help="Gera relatorio Markdown em data/homologacao.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source_db = Path(args.db).expanduser().resolve()
    if not source_db.exists():
        print(f"FALHA: banco nao encontrado: {source_db}")
        return 2
    SANDBOX_DIR.mkdir(parents=True, exist_ok=True)
    sandbox_db = SANDBOX_DIR / f"{source_db.stem}_merge_{args.primary_id}_{args.duplicate_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    shutil.copy2(source_db, sandbox_db)

    previous_legacy_db = os.environ.get("POWER_CHURCH_LEGACY_DB_PATH")
    os.environ["POWER_CHURCH_LEGACY_DB_PATH"] = str(sandbox_db)

    try:
        with connect(sandbox_db) as conn:
            before_primary = person_summary(conn, int(args.primary_id))
            before_duplicate = person_summary(conn, int(args.duplicate_id))
        result = merge_people(
            int(args.primary_id),
            int(args.duplicate_id),
            reason=str(args.reason or ""),
            actor="django:merge_controlado_clone",
            prefer_duplicate_name=bool(args.prefer_duplicate_name),
        )
        with connect(sandbox_db) as conn:
            after_primary = person_summary(conn, int(args.primary_id))
            after_duplicate = person_summary(conn, int(args.duplicate_id))
        print("OK: merge executado no clone.")
        print(f"Clone: {sandbox_db}")
        print(f"Principal: {after_primary.get('nome')} (ativo={after_primary.get('ativo')})")
        print(f"Duplicada: {after_duplicate.get('nome')} (ativo={after_duplicate.get('ativo')})")
        if args.report:
            report = write_report(
                source_db=source_db,
                sandbox_db=sandbox_db,
                primary_id=int(args.primary_id),
                duplicate_id=int(args.duplicate_id),
                reason=str(args.reason or ""),
                prefer_duplicate_name=bool(args.prefer_duplicate_name),
                before_primary=before_primary,
                before_duplicate=before_duplicate,
                after_primary=after_primary,
                after_duplicate=after_duplicate,
                result=result,
            )
            print(f"Relatorio: {report}")
        return 0
    except LegacyWriteError as exc:
        print(f"FALHA: {exc}")
        print(f"Clone preservado para auditoria: {sandbox_db}")
        return 1
    finally:
        if previous_legacy_db is None:
            os.environ.pop("POWER_CHURCH_LEGACY_DB_PATH", None)
        else:
            os.environ["POWER_CHURCH_LEGACY_DB_PATH"] = previous_legacy_db


if __name__ == "__main__":
    raise SystemExit(main())

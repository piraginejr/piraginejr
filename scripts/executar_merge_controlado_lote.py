from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DB = ROOT / "data" / "power_church_membros_importado.db"
SANDBOX_DIR = ROOT / "data" / "sandboxes"
REPORT_DIR = ROOT / "data" / "homologacao"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "power_church_django.power_church_site.settings")

import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402
from power_church_django.services.legacy_write import LegacyWriteError, merge_people  # noqa: E402


CASES = [
    (
        "Caio",
        159,
        160,
        "Mesma identidade com dados complementares; ficha principal preserva CPF e e-mail, nome da duplicada e mais completo.",
        True,
    ),
    (
        "Davi",
        271,
        270,
        "Mesmo nome e identidade dobrada; ficha principal preserva contato ativo e consolida historico.",
        False,
    ),
    (
        "Francisca",
        463,
        462,
        "Mesma identidade com dados complementares; ficha principal preserva nome completo e CPF, absorvendo contato da duplicada.",
        False,
    ),
    (
        "Gilza",
        497,
        498,
        "Mesma identidade com dados complementares; ficha principal preserva CPF e e-mail, usando o nome mais completo da duplicada.",
        True,
    ),
    (
        "Luiza",
        823,
        822,
        "Mudanca de nome de solteira para casada; ficha principal de casada deve consolidar os dados da ficha antiga.",
        False,
    ),
    (
        "Luca",
        793,
        792,
        "Mesma identidade com dados complementares; ficha principal preserva nome completo e CPF, absorvendo contato da ficha curta.",
        False,
    ),
    (
        "Maria",
        950,
        916,
        "Mesma identidade com dados complementares; ficha principal preserva CPF e e-mail, adotando o nome completo da outra ficha.",
        True,
    ),
]


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def person_summary(conn: sqlite3.Connection, person_id: int) -> dict[str, object]:
    row = conn.execute(
        """
        SELECT id, codigo_interno, nome, cpf, data_nascimento, status, ativo, email_principal
          FROM pessoas
         WHERE id = ?
        """,
        (person_id,),
    ).fetchone()
    if row is None:
        return {"id": person_id, "found": False}
    return {
        "id": row["id"],
        "found": True,
        "codigo": row["codigo_interno"] or "",
        "nome": row["nome"] or "",
        "cpf": row["cpf"] or "",
        "data_nascimento": row["data_nascimento"] or "",
        "status": row["status"] or "",
        "ativo": bool(row["ativo"]),
        "email": row["email_principal"] or "",
        "contribuicoes": conn.execute("SELECT COUNT(*) FROM contribuicoes WHERE pessoa_id = ?", (person_id,)).fetchone()[0],
        "recibos": conn.execute("SELECT COUNT(*) FROM recibos WHERE pessoa_id = ?", (person_id,)).fetchone()[0],
        "contribuintes": conn.execute("SELECT COUNT(*) FROM contribuintes WHERE pessoa_id = ?", (person_id,)).fetchone()[0],
    }


def write_report(results: list[dict[str, object]]) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    target = REPORT_DIR / f"merge_controlado_lote_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    lines = [
        "# Merge Controlado em Lote",
        "",
        f"Gerado em: {datetime.now().isoformat(timespec='seconds')}",
        "",
    ]
    for item in results:
        lines.append(f"## {item['label']}")
        lines.append("")
        lines.append(f"- Status: `{item['status']}`")
        lines.append(f"- Principal: `#{item['primary_id']}` {item['before_primary'].get('nome', '')}")
        lines.append(f"- Duplicada: `#{item['duplicate_id']}` {item['before_duplicate'].get('nome', '')}")
        lines.append(f"- Clone: `{item['sandbox_db']}`")
        if item["status"] != "ok":
            lines.append(f"- Motivo: {item['reason']}")
        else:
            lines.append(f"- Trash da duplicada: `#{item['merge_result'].get('duplicate_trash_id')}`")
            lines.append(
                f"- Principal depois: `{item['after_primary'].get('nome', '')}` ativo={item['after_primary'].get('ativo')} "
                f"contribuições={item['after_primary'].get('contribuicoes')}"
            )
            lines.append(
                f"- Duplicada depois: `{item['after_duplicate'].get('nome', '')}` ativo={item['after_duplicate'].get('ativo')} "
                f"contribuições={item['after_duplicate'].get('contribuicoes')}"
            )
        lines.append("")
    target.write_text("\n".join(lines), encoding="utf-8")
    return target


def main() -> int:
    SANDBOX_DIR.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, object]] = []
    old_db = settings.POWER_CHURCH_LEGACY_DB_PATH
    try:
        for label, primary_id, duplicate_id, reason, prefer in CASES:
            print(f"=== {label} ===", flush=True)
            sandbox_db = SANDBOX_DIR / f"{SOURCE_DB.stem}_merge_{label.lower()}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.db"
            shutil.copy2(SOURCE_DB, sandbox_db)
            settings.POWER_CHURCH_LEGACY_DB_PATH = str(sandbox_db)
            with connect(sandbox_db) as conn:
                before_primary = person_summary(conn, primary_id)
                before_duplicate = person_summary(conn, duplicate_id)
            try:
                merge_result = merge_people(
                    primary_id,
                    duplicate_id,
                    reason=reason,
                    actor="django:merge_controlado_clone",
                    prefer_duplicate_name=prefer,
                )
                with connect(sandbox_db) as conn:
                    after_primary = person_summary(conn, primary_id)
                    after_duplicate = person_summary(conn, duplicate_id)
                item = {
                    "label": label,
                    "status": "ok",
                    "primary_id": primary_id,
                    "duplicate_id": duplicate_id,
                    "before_primary": before_primary,
                    "before_duplicate": before_duplicate,
                    "after_primary": after_primary,
                    "after_duplicate": after_duplicate,
                    "merge_result": merge_result,
                    "sandbox_db": str(sandbox_db),
                }
                print(
                    f"OK: principal #{primary_id} absorveu #{duplicate_id}; trash #{merge_result.get('duplicate_trash_id')}",
                    flush=True,
                )
            except LegacyWriteError as exc:
                item = {
                    "label": label,
                    "status": "blocked",
                    "reason": str(exc),
                    "primary_id": primary_id,
                    "duplicate_id": duplicate_id,
                    "before_primary": before_primary,
                    "before_duplicate": before_duplicate,
                    "sandbox_db": str(sandbox_db),
                }
                print(f"BLOQUEADO: {exc}", flush=True)
            results.append(item)
            print("", flush=True)
    finally:
        settings.POWER_CHURCH_LEGACY_DB_PATH = old_db
    report = write_report(results)
    print(f"RELATORIO: {report}", flush=True)
    print(json.dumps({"report": str(report), "results": results}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

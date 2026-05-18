#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DJANGO_DIR = ROOT / "power_church_django"
DJANGO_VENV_PYTHON = DJANGO_DIR / ".venv" / "bin" / "python"


sys.path.insert(0, str(ROOT))

from power_church_core.family import family_address_key


def address_key(row: sqlite3.Row) -> tuple[str, ...]:
    return family_address_key(row)


def relation_exists(conn: sqlite3.Connection, left_id: int, right_id: int) -> bool:
    row = conn.execute(
        """
        SELECT 1
         FROM pessoa_relacionamentos
         WHERE ativo = 1
           AND (
                (pessoa_id = ? AND pessoa_relacionada_id = ?)
                OR
                (pessoa_id = ? AND pessoa_relacionada_id = ?)
           )
         LIMIT 1
        """,
        (left_id, right_id, right_id, left_id),
    ).fetchone()
    return row is not None


def analyze(db_path: Path) -> dict[str, int]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        groups: dict[tuple[str, ...], list[int]] = {}
        for row in conn.execute(
            """
            SELECT p.id, e.cep, e.logradouro, e.numero, e.complemento, e.bairro, e.cidade, e.uf
              FROM pessoas p
              JOIN pessoa_enderecos e ON e.pessoa_id = p.id
             WHERE p.ativo = 1
            """
        ):
            key = address_key(row)
            if key:
                groups.setdefault(key, []).append(int(row["id"]))
        grouped_people = 0
        possible_pairs = 0
        pending_pairs = 0
        for people in groups.values():
            unique_people = sorted(set(people))
            if len(unique_people) < 2:
                continue
            grouped_people += len(unique_people)
            for index, left_id in enumerate(unique_people):
                for right_id in unique_people[index + 1 :]:
                    possible_pairs += 1
                    if not relation_exists(conn, left_id, right_id):
                        pending_pairs += 1
        return {
            "grupos_endereco_exato": sum(1 for people in groups.values() if len(set(people)) > 1),
            "pessoas_em_grupos": grouped_people,
            "pares_possiveis": possible_pairs,
            "pares_pendentes": pending_pairs,
        }
    finally:
        conn.close()


def apply_sync(db_path: Path) -> dict[str, int]:
    os.environ["POWER_CHURCH_LEGACY_DB_PATH"] = str(db_path)
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(DJANGO_DIR))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "power_church_site.settings")

    import django

    django.setup()

    from power_church_django.services.legacy_write import (
        connect_legacy_write,
        sync_household_relationships_by_address,
    )

    totals = {"created": 0, "deactivated": 0}
    with connect_legacy_write() as conn:
        people = conn.execute(
            "SELECT id, organizacao_id FROM pessoas WHERE ativo = 1 ORDER BY id"
        ).fetchall()
        with conn:
            for person in people:
                summary = sync_household_relationships_by_address(
                    conn,
                    int(person["organizacao_id"] or 0),
                    int(person["id"] or 0),
                    actor="script:nucleos_familiares_endereco",
                )
                totals["created"] += summary["created"]
                totals["deactivated"] += summary["deactivated"]
    return totals


def main() -> int:
    parser = argparse.ArgumentParser(description="Sincroniza familias domiciliares por endereco completo exato.")
    parser.add_argument("--db", default=str(ROOT / "data" / "power_church_membros_importado.db"))
    parser.add_argument("--apply", action="store_true", help="Grava os vinculos pendentes. Sem esta flag, faz apenas simulacao.")
    parser.add_argument("--report", action="store_true", help="Aceito para compatibilidade com a bateria total.")
    args = parser.parse_args()
    if Path(sys.executable).resolve() != DJANGO_VENV_PYTHON.resolve() and DJANGO_VENV_PYTHON.exists():
        import subprocess

        completed = subprocess.run(
            [str(DJANGO_VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]],
            cwd=str(ROOT),
            text=True,
            check=False,
        )
        return completed.returncode
    db_path = Path(args.db)
    summary = analyze(db_path)
    print(
        "Simulacao: "
        f"{summary['grupos_endereco_exato']} grupo(s), "
        f"{summary['pessoas_em_grupos']} pessoa(s), "
        f"{summary['pares_pendentes']} vinculo(s) pendente(s)."
    )
    if not args.apply:
        print("Nenhuma alteracao gravada. Use --apply para criar as familias domiciliares.")
        return 0
    totals = apply_sync(db_path)
    print(
        "Aplicado: "
        f"{totals['created']} relacao(oes) de familia domiciliar criada(s), "
        f"{totals['deactivated']} relacao(oes) automatica(s) removida(s)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

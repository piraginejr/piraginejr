#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DJANGO_DIR = ROOT / "power_church_django"
DJANGO_VENV_PYTHON = DJANGO_DIR / ".venv" / "bin" / "python"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Padroniza fotos de membros para pasta por faixa de ID e nome com sufixo de ID."
    )
    parser.add_argument("--apply", action="store_true", help="Executa as mudancas. Sem isso, roda apenas em simulacao.")
    parser.add_argument("--db", default="", help="Caminho opcional para o banco legado.")
    parser.add_argument("--photo-dir", default="", help="Pasta opcional das fotos.")
    args = parser.parse_args()

    if Path(sys.executable).resolve() != DJANGO_VENV_PYTHON.resolve() and DJANGO_VENV_PYTHON.exists():
        import subprocess

        completed = subprocess.run(
            [str(DJANGO_VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]],
            cwd=str(ROOT),
            text=True,
            capture_output=False,
            check=False,
        )
        return completed.returncode

    if args.db:
        os.environ["POWER_CHURCH_LEGACY_DB_PATH"] = str(Path(args.db).expanduser())
    if args.photo_dir:
        os.environ["POWER_CHURCH_PHOTO_DIR"] = str(Path(args.photo_dir).expanduser())

    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(DJANGO_DIR))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "power_church_site.settings")

    import django

    django.setup()

    from power_church_django.services.legacy_write import connect_legacy_write
    from power_church_django.services.photos import (
        find_member_photo,
        member_photo_example_filename,
        member_photo_folder,
    )

    planned: list[tuple[Path, Path, int, str]] = []
    conflicts: list[tuple[Path, Path, int, str]] = []
    already_ok = 0
    without_photo = 0

    with connect_legacy_write() as conn:
        rows = conn.execute(
            """
            SELECT id, nome, cpf
              FROM pessoas
             WHERE ativo = 1
             ORDER BY id
            """
        ).fetchall()

    for row in rows:
        person_id = int(row["id"])
        source = find_member_photo(person_id, row["cpf"], row["nome"])
        if source is None:
            without_photo += 1
            continue
        target = member_photo_folder(person_id) / member_photo_example_filename(
            person_id,
            row["cpf"],
            row["nome"],
            source.suffix.lower(),
        )
        if source.resolve() == target.resolve():
            already_ok += 1
            continue
        if target.exists():
            conflicts.append((source, target, person_id, str(row["nome"] or "")))
            continue
        planned.append((source, target, person_id, str(row["nome"] or "")))

    if args.apply:
        for source, target, _person_id, _name in planned:
            target.parent.mkdir(parents=True, exist_ok=True)
            source.rename(target)

    mode = "APLICADO" if args.apply else "SIMULACAO"
    print(f"{mode}: fotos ja padronizadas={already_ok}, sem foto={without_photo}, a padronizar={len(planned)}, conflitos={len(conflicts)}")
    for source, target, person_id, name in planned[:25]:
        print(f"- ID {person_id} {name}: {source.relative_to(ROOT)} -> {target.relative_to(ROOT)}")
    if len(planned) > 25:
        print(f"- ... mais {len(planned) - 25} foto(s)")
    if conflicts:
        print("Conflitos que exigem revisao manual:")
        for source, target, person_id, name in conflicts[:25]:
            print(f"- ID {person_id} {name}: destino ja existe {target.relative_to(ROOT)}; origem {source.relative_to(ROOT)}")
    return 1 if conflicts else 0


if __name__ == "__main__":
    raise SystemExit(main())

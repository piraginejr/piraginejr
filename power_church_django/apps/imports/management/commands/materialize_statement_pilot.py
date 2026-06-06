from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from power_church_django.apps.imports.services import persist_statement_pilot_from_clone


class Command(BaseCommand):
    help = "Materializa um lote de extrato vindo de um banco clone para os modelos piloto em Postgres."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--clone-db", required=True, help="Caminho do banco clone SQLite.")
        parser.add_argument("--lot-id", required=True, type=int, help="ID do lote de extrato dentro do clone.")
        parser.add_argument("--source-backend", default="legado_clone", help="Origem do piloto: legado_clone, django_web ou postgres_nativo.")
        parser.add_argument("--pdf-provider", default="", help="Leitor PDF usado para gerar o clone.")
        parser.add_argument("--comparison-ok", action="store_true", help="Marca que a comparacao entre leitores foi aprovada.")
        parser.add_argument("--comparison-note", default="", help="Nota de comparacao entre leitores.")
        parser.add_argument("--report-path", default="", help="Relatorio associado ao piloto.")

    def handle(self, *args, **options) -> None:
        clone_db = Path(str(options["clone_db"])).expanduser().resolve()
        lot_id = int(options["lot_id"])
        if not clone_db.exists():
            raise CommandError(f"Banco clone nao encontrado: {clone_db}")
        pilot_lot = persist_statement_pilot_from_clone(
            clone_db,
            lot_id,
            source_backend=str(options["source_backend"] or "legado_clone"),
            pdf_provider=str(options["pdf_provider"] or ""),
            comparison_ok=bool(options["comparison_ok"]),
            comparison_note=str(options["comparison_note"] or ""),
            report_path=str(options["report_path"] or ""),
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Lote piloto materializado no Postgres: #{pilot_lot.id} ({pilot_lot.bank_name} {pilot_lot.file_name})"
            )
        )

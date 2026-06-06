from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from power_church_django.apps.imports.services import sync_statement_lot_snapshot_from_legacy


class Command(BaseCommand):
    help = "Sincroniza um lote de extrato operacional do legado para o snapshot de leitura em Postgres."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--lot-id", required=True, type=int, help="ID do lote de extrato no banco legado operacional.")

    def handle(self, *args, **options) -> None:
        lot_id = int(options["lot_id"])
        try:
            pilot_lot = sync_statement_lot_snapshot_from_legacy(lot_id)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            self.style.SUCCESS(
                f"Snapshot sincronizado no Postgres para o lote legado #{lot_id}: piloto #{pilot_lot.id} ({pilot_lot.bank_name} {pilot_lot.file_name})"
            )
        )

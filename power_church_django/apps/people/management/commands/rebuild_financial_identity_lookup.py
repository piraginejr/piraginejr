from __future__ import annotations

from django.core.management.base import BaseCommand

from power_church_django.services.financial_identity_lookup import rebuild_financial_identity_lookup


class Command(BaseCommand):
    help = "Reconstroi a tabela de consulta de identidades financeiras."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--person-id",
            dest="person_ids",
            action="append",
            type=int,
            help="Legacy id da pessoa a sincronizar. Pode ser repetido.",
        )

    def handle(self, *args, **options):
        person_ids = options.get("person_ids") or None
        total = rebuild_financial_identity_lookup(person_ids=person_ids)
        scope = "todas as pessoas" if not person_ids else f"{len(person_ids)} pessoa(s)"
        self.stdout.write(self.style.SUCCESS(f"Tabela de identidades financeiras atualizada para {scope}: {total} linha(s)."))

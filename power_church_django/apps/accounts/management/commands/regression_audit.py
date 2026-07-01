from __future__ import annotations

from django.core.management.base import BaseCommand

from power_church_django.services.regression_audit import run_regression_audit


class Command(BaseCommand):
    help = "Executa uma varredura de regressao operacional e grava relatorio em reports/."

    def handle(self, *args, **options):
        run_regression_audit(stdout=self.stdout)

from __future__ import annotations

from django.core.management.base import BaseCommand

from power_church_django.services.bank_parser_regression import (
    run_bank_parser_regression_checks,
    write_bank_parser_regression_report,
)


class Command(BaseCommand):
    help = "Executa sentinelas de regressao dos parsers bancarios."

    def add_arguments(self, parser):
        parser.add_argument("--report", action="store_true", help="Grava relatorio Markdown em reports/.")

    def handle(self, *args, **options):
        checks = run_bank_parser_regression_checks()
        failed = [check for check in checks if not check.ok and check.severity == "FAIL"]
        for check in checks:
            status = "OK" if check.ok else check.severity
            self.stdout.write(f"- {status}: {check.name} ({check.detail})")
        if options.get("report"):
            report = write_bank_parser_regression_report(checks)
            self.stdout.write(f"Relatorio salvo em {report}")
        if failed:
            raise SystemExit(1)

from __future__ import annotations

import json

from django.core.management.base import BaseCommand

from power_church_django.services.receipt_delivery import backfill_native_event_receipts


class Command(BaseCommand):
    help = "Reenfileira recibos automaticos nativos que ficaram faltando em envelopes e extratos."

    def handle(self, *args, **options) -> None:
        result = backfill_native_event_receipts(
            actor="manage.py:backfill_automatic_event_receipts",
        )
        self.stdout.write(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))

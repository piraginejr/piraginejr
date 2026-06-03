from __future__ import annotations

import json
from datetime import date

from django.core.management.base import BaseCommand, CommandParser

from power_church_django.apps.contributions.models import ReceiptDispatch
from power_church_django.services.receipt_delivery import (
    dedupe_campaign_receipt_dispatches,
    prepare_consolidated_receipt_campaign,
    process_campaign_receipt_dispatches,
)


class Command(BaseCommand):
    help = "Prepara a campanha retroativa de recibos consolidados e processa a fila inteira em lotes controlados."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--cutoff-date", default="2026-05-31", help="Data final das contribuicoes a consolidar.")
        parser.add_argument("--emission-date", default=date.today().isoformat(), help="Data de emissao dos recibos.")
        parser.add_argument("--prepare-limit", type=int, default=0, help="Limite opcional de pessoas na preparacao.")
        parser.add_argument("--batch-size", type=int, default=40, help="Quantidade maxima por rodada de envio.")
        parser.add_argument("--sleep-seconds", type=float, default=3.0, help="Espera entre e-mails.")
        parser.add_argument("--pause-every", type=int, default=40, help="Pausa maior a cada N envios.")
        parser.add_argument("--pause-seconds", type=float, default=60.0, help="Duracao da pausa maior.")
        parser.add_argument("--pending-only", action="store_true", help="Processa so pendentes, sem reprocessar falhas.")
        parser.add_argument("--max-rounds", type=int, default=0, help="Limite de rodadas. 0 = ate esvaziar a campanha.")

    def handle(self, *args, **options):
        actor = "manage.py:run_consolidated_receipt_campaign"
        prepared = prepare_consolidated_receipt_campaign(
            cutoff_date=options["cutoff_date"],
            emission_date=options["emission_date"],
            actor=actor,
            limit=int(options["prepare_limit"] or 0),
        )
        campaign_key = str(prepared["campaign_key"])
        dedupe = dedupe_campaign_receipt_dispatches(campaign_key=campaign_key, actor=actor)
        rounds: list[dict[str, object]] = []
        sent = 0
        failed = 0
        round_index = 0
        while True:
            pending_qs = ReceiptDispatch.objects.filter(
                metadata__campaign_key=campaign_key,
                status__in=[ReceiptDispatch.Status.PENDING, ReceiptDispatch.Status.FAILED]
                if not options["pending_only"]
                else [ReceiptDispatch.Status.PENDING],
            )
            remaining = pending_qs.count()
            if remaining <= 0:
                break
            round_index += 1
            if int(options["max_rounds"] or 0) > 0 and round_index > int(options["max_rounds"]):
                break
            processed = process_campaign_receipt_dispatches(
                campaign_key=campaign_key,
                limit=int(options["batch_size"] or 40),
                actor=actor,
                pending_only=bool(options["pending_only"]),
                sleep_seconds=float(options["sleep_seconds"] or 0),
                pause_every=int(options["pause_every"] or 0),
                pause_seconds=float(options["pause_seconds"] or 0),
            )
            round_sent = sum(1 for item in processed if item.status == ReceiptDispatch.Status.SENT)
            round_failed = sum(1 for item in processed if item.status != ReceiptDispatch.Status.SENT)
            sent += round_sent
            failed += round_failed
            rounds.append(
                {
                    "round": round_index,
                    "selected": len(processed),
                    "sent": round_sent,
                    "failed": round_failed,
                    "remaining_after_round": max(0, remaining - len(processed)),
                }
            )
            if not processed:
                break
        still_pending = ReceiptDispatch.objects.filter(
            metadata__campaign_key=campaign_key,
            status__in=[ReceiptDispatch.Status.PENDING, ReceiptDispatch.Status.FAILED],
        ).count()
        self.stdout.write(
            json.dumps(
                {
                    "campaign_key": campaign_key,
                    "prepared": prepared["prepared"],
                    "created": prepared["created"],
                    "reused": prepared["reused"],
                    "retried": prepared["retried"],
                    "dedupe": dedupe,
                    "rounds": rounds,
                    "sent": sent,
                    "failed": failed,
                    "remaining_pending_or_failed": still_pending,
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )

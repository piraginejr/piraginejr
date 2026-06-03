from __future__ import annotations

import json
import time

from django.core.management.base import BaseCommand, CommandParser

from power_church_django.apps.contributions.models import ReceiptDispatch
from power_church_django.services.receipt_delivery import dedupe_campaign_receipt_dispatches, process_campaign_receipt_dispatches, send_receipt_dispatch


class Command(BaseCommand):
    help = "Processa a fila de envio de recibos com cadencia controlada para evitar disparos em rajada."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--limit", type=int, default=10, help="Quantidade maxima de envios nesta rodada.")
        parser.add_argument(
            "--campaign-key",
            default="",
            help="Filtra apenas itens da campanha informada em metadata.campaign_key.",
        )
        parser.add_argument(
            "--sleep-seconds",
            type=float,
            default=3.0,
            help="Espera em segundos entre um envio e outro.",
        )
        parser.add_argument(
            "--pause-every",
            type=int,
            default=40,
            help="A cada N envios, faz uma pausa maior. 0 desliga.",
        )
        parser.add_argument(
            "--pause-seconds",
            type=float,
            default=60.0,
            help="Pausa maior em segundos.",
        )
        parser.add_argument(
            "--pending-only",
            action="store_true",
            help="Processa somente pendentes e ignora filas com falha anterior.",
        )
        parser.add_argument(
            "--drain",
            action="store_true",
            help="Repete as rodadas ate esvaziar a fila filtrada.",
        )

    def handle(self, *args, **options):
        statuses = [ReceiptDispatch.Status.PENDING]
        if not options["pending_only"]:
            statuses.append(ReceiptDispatch.Status.FAILED)
        campaign_key = str(options["campaign_key"] or "").strip()
        actor = "manage.py:process_receipt_dispatch_queue"
        if campaign_key:
            dedupe_campaign_receipt_dispatches(campaign_key=campaign_key, actor=actor)
        sent = 0
        failed = 0
        processed: list[dict[str, object]] = []
        rounds = 0
        while True:
            queryset = ReceiptDispatch.objects.filter(status__in=statuses).exclude(email_to="", person_email="")
            if campaign_key:
                queryset = queryset.filter(metadata__campaign_key=campaign_key)
            items = list(queryset.order_by("created_at", "id")[: max(1, int(options["limit"] or 10))])
            if not items:
                break
            rounds += 1
            if campaign_key:
                batch = process_campaign_receipt_dispatches(
                    campaign_key=campaign_key,
                    limit=int(options["limit"] or 10),
                    actor=actor,
                    pending_only=bool(options["pending_only"]),
                    sleep_seconds=float(options["sleep_seconds"] or 0),
                    pause_every=int(options["pause_every"] or 0),
                    pause_seconds=float(options["pause_seconds"] or 0),
                )
            else:
                batch = []
                for index, item in enumerate(items, start=1):
                    result = send_receipt_dispatch(item, actor=actor)
                    batch.append(result)
                    if index < len(items):
                        if int(options["pause_every"] or 0) > 0 and index % int(options["pause_every"] or 0) == 0:
                            time.sleep(float(options["pause_seconds"] or 0))
                        elif float(options["sleep_seconds"] or 0) > 0:
                            time.sleep(float(options["sleep_seconds"] or 0))
            for result in batch:
                processed.append(
                    {
                        "dispatch_id": int(result.pk or 0),
                        "receipt_id": int(result.legacy_receipt_id or 0),
                        "receipt_number": result.legacy_receipt_number,
                        "email_to": result.email_to or result.person_email,
                        "status": result.status,
                        "error": result.last_error,
                    }
                )
                if result.status == ReceiptDispatch.Status.SENT:
                    sent += 1
                else:
                    failed += 1
            if not options["drain"]:
                break
        self.stdout.write(
            json.dumps(
                {
                    "campaign_key": campaign_key,
                    "rounds": rounds,
                    "selected": len(processed),
                    "sent": sent,
                    "failed": failed,
                    "sleep_seconds": float(options["sleep_seconds"] or 0),
                    "pause_every": int(options["pause_every"] or 0),
                    "pause_seconds": float(options["pause_seconds"] or 0),
                    "processed": processed,
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )

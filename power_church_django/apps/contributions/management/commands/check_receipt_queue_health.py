from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandParser

from power_church_django.services.receipt_delivery import backfill_native_event_receipts, drain_receipt_dispatch_queue
from power_church_django.services.receipt_queue_health import receipt_queue_health_snapshot


class Command(BaseCommand):
    help = "Verifica se a fila de recibos esta saudável ou parada sem atividade recente."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--max-pending-age-minutes",
            type=int,
            default=20,
            help="Janela maxima sem atividade antes de considerar a fila pendente como travada.",
        )
        parser.add_argument(
            "--max-failed-age-minutes",
            type=int,
            default=120,
            help="Janela maxima para falhas antigas antes de elevar a severidade.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Retorna o resumo em JSON.",
        )
        parser.add_argument(
            "--recover",
            action="store_true",
            help="Tenta recuperar automaticamente a fila quando houver alerta.",
        )
        parser.add_argument("--limit", type=int, default=40, help="Limite por rodada ao tentar recuperar a fila.")
        parser.add_argument("--sleep-seconds", type=float, default=3.0, help="Espera entre envios na recuperacao.")
        parser.add_argument("--pause-every", type=int, default=40, help="Pausa maior a cada N envios na recuperacao.")
        parser.add_argument("--pause-seconds", type=float, default=60.0, help="Duracao da pausa maior na recuperacao.")

    def handle(self, *args, **options):
        snapshot = receipt_queue_health_snapshot(
            max_pending_age_minutes=int(options["max_pending_age_minutes"] or 20),
            max_failed_age_minutes=int(options["max_failed_age_minutes"] or 120),
        )
        recovery: dict[str, object] | None = None
        if options["recover"] and snapshot["severity"] in {"warn", "critical"}:
            actor = "manage.py:check_receipt_queue_health"
            recovery = {
                "backfill": backfill_native_event_receipts(actor=actor),
                "drain": drain_receipt_dispatch_queue(
                    actor=actor,
                    campaign_key="",
                    limit=int(options["limit"] or 40),
                    pending_only=False,
                    sleep_seconds=float(options["sleep_seconds"] or 3),
                    pause_every=int(options["pause_every"] or 40),
                    pause_seconds=float(options["pause_seconds"] or 60),
                    drain=True,
                ),
            }
            snapshot = receipt_queue_health_snapshot(
                max_pending_age_minutes=int(options["max_pending_age_minutes"] or 20),
                max_failed_age_minutes=int(options["max_failed_age_minutes"] or 120),
            )
            snapshot["recovery"] = recovery
        if options["json"]:
            self.stdout.write(json.dumps(snapshot, ensure_ascii=False, indent=2, default=str))
        else:
            self.stdout.write(f"Severidade: {snapshot['severity']}")
            self.stdout.write(f"Resumo: {snapshot['summary']}")
            self.stdout.write(
                "Fila: "
                f"{snapshot['pending_count']} pendente(s), "
                f"{snapshot['failed_count']} falha(s), "
                f"{snapshot['sent_count']} enviado(s)"
            )
            self.stdout.write(
                "Atividade: "
                f"ultima tentativa={snapshot['latest_attempt_at'] or '-'} · "
                f"ultimo envio={snapshot['latest_sent_at'] or '-'}"
            )
            if snapshot["oldest_pending_at"]:
                self.stdout.write(f"Pendencia mais antiga: {snapshot['oldest_pending_at']}")
            if snapshot["issues"]:
                self.stdout.write("Alertas:")
                for issue in snapshot["issues"]:
                    self.stdout.write(f"- {issue}")
            if recovery:
                self.stdout.write(
                    "Recuperacao automatica: "
                    f"backfill={int((recovery.get('backfill') or {}).get('queued', 0) or 0)} reenfileirado(s) · "
                    f"drain={int((recovery.get('drain') or {}).get('sent', 0) or 0)} enviado(s)"
                )
            self.stdout.write(snapshot["delivery_note"])
        if snapshot["severity"] == "critical":
            raise SystemExit(2)
        if snapshot["severity"] == "warn":
            raise SystemExit(1)

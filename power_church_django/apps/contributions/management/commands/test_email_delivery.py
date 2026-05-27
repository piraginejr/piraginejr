from __future__ import annotations

import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser

from power_church_core.normalization import normalize_query
from power_church_django.services.mail_dispatch import MailAttachment, graph_config_snapshot, send_email_message
from power_church_django.services.receipt_delivery import email_runtime_snapshot


class Command(BaseCommand):
    help = "Testa a infraestrutura de envio de e-mail do Power Church, inclusive modo seco do Microsoft Graph."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--to", default="", help="Destinatario do teste.")
        parser.add_argument("--subject", default="Teste de e-mail Power Church", help="Assunto do e-mail.")
        parser.add_argument(
            "--body",
            default="Este e-mail foi gerado pelo comando de teste do Power Church.",
            help="Corpo do e-mail.",
        )
        parser.add_argument("--reply-to", default="", help="Responder para opcional.")
        parser.add_argument("--dry-run", action="store_true", help="Monta tudo, mas nao envia externamente.")
        parser.add_argument(
            "--with-attachment",
            action="store_true",
            help="Inclui um pequeno anexo txt para validar montagem de anexos.",
        )

    def handle(self, *args, **options):
        to_email = normalize_query(options["to"])
        dry_run = bool(options["dry_run"])
        provider = email_runtime_snapshot()["provider"]
        if not to_email and not dry_run:
            raise SystemExit("Informe --to para envio real. Para simulacao, use --dry-run.")
        attachments: list[MailAttachment] = []
        if options["with_attachment"]:
            attachments.append(
                MailAttachment(
                    filename="teste-power-church.txt",
                    content=b"Teste de anexo do Power Church via provedor configurado.",
                    content_type="text/plain",
                )
            )
        from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "") or "recebimento@localhost"
        reply_to = [normalize_query(options["reply_to"])] if normalize_query(options["reply_to"]) else []
        result = send_email_message(
            subject=options["subject"],
            body=options["body"],
            from_email=from_email,
            to_emails=[to_email] if to_email else ["destinatario@simulacao.local"],
            reply_to=reply_to,
            attachments=attachments,
            dry_run=dry_run,
        )
        payload = {
            "provider": provider,
            "accepted": result.accepted,
            "from_email": from_email,
            "dry_run": dry_run,
            "runtime": email_runtime_snapshot(),
            "graph": graph_config_snapshot(),
            "result": result.metadata or {},
        }
        self.stdout.write(json.dumps(payload, ensure_ascii=True, indent=2, default=str))

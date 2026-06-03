from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser

from power_church_django.services.receipt_delivery import (
    consolidated_receipt_campaign_summary,
    prepare_consolidated_receipt_campaign,
)


def _write_report(payload: dict[str, object]) -> str:
    report_dir = Path(settings.REPO_ROOT) / "data" / "homologacao"
    report_dir.mkdir(parents=True, exist_ok=True)
    target = report_dir / f"campanha_recibos_consolidados_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    summary = payload.get("summary", {}) or {}
    items = payload.get("items", []) or []
    lines = [
        "# Campanha de Recibos Consolidados",
        "",
        f"Gerado em: {datetime.now().isoformat(timespec='seconds')}",
        f"Corte: {payload.get('cutoff_date') or '-'}",
        f"Emissao: {payload.get('emission_date') or '-'}",
        f"Campaign key: {payload.get('campaign_key') or '-'}",
        "",
        "## Resumo",
        "",
        f"- Pessoas avaliadas: {summary.get('total_people', 0)}",
        f"- Prontas para enfileirar: {summary.get('ready_to_queue', 0)}",
        f"- Gerar e enfileirar: {summary.get('generate_and_queue', 0)}",
        f"- Reusar recibo existente: {summary.get('queue_existing', 0)}",
        f"- Reenfileirar recibo com falha anterior: {summary.get('retry_existing', 0)}",
        f"- Ja enfileiradas: {summary.get('already_queued', 0)}",
        f"- Ja enviadas: {summary.get('already_sent', 0)}",
    ]
    if "prepared" in payload:
        lines.extend(
            [
                "",
                "## Execucao",
                "",
                f"- Preparadas agora: {payload.get('prepared', 0)}",
                f"- Recibos criados agora: {payload.get('created', 0)}",
                f"- Recibos existentes reaproveitados: {payload.get('reused', 0)}",
                f"- Recibos reenfileirados por falha previa: {payload.get('retried', 0)}",
                f"- Ignoradas nesta rodada: {payload.get('skipped', 0)}",
            ]
        )
    lines.extend(
        [
            "",
            "## Itens",
            "",
            "| Pessoa | E-mail | Acao | Qtd contrib. | Total | Periodo | Recibo | Fila |",
            "| --- | --- | --- | ---: | ---: | --- | --- | --- |",
        ]
    )
    for item in items:
        lines.append(
            "| {person_name} | {email} | {action} | {contribution_count} | {total_value:.2f} | {period_start} a {period_end} | {matching_receipt_number} | {latest_dispatch_status} |".format(
                **{
                    "person_name": str(item.get("person_name") or "").replace("|", "/"),
                    "email": str(item.get("email") or "").replace("|", "/"),
                    "action": str(item.get("action") or "").replace("|", "/"),
                    "contribution_count": int(item.get("contribution_count") or 0),
                    "total_value": float(item.get("total_value") or 0),
                    "period_start": str(item.get("period_start") or "-"),
                    "period_end": str(item.get("period_end") or "-"),
                    "matching_receipt_number": str(item.get("matching_receipt_number") or "-").replace("|", "/"),
                    "latest_dispatch_status": str(item.get("latest_dispatch_status") or "-").replace("|", "/"),
                }
            )
        )
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(target)


class Command(BaseCommand):
    help = "Prepara a campanha retroativa de recibos consolidados por pessoa, somente para fichas com e-mail."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--cutoff-date",
            default=date.today().isoformat(),
            help="Data final das contribuicoes a consolidar (YYYY-MM-DD).",
        )
        parser.add_argument(
            "--emission-date",
            default=date.today().isoformat(),
            help="Data de emissao a gravar nos recibos novos (YYYY-MM-DD).",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Limita a quantidade de pessoas preparadas nesta rodada. 0 = todas.",
        )
        parser.add_argument(
            "--queue",
            action="store_true",
            help="Cria/reemite os recibos consolidados e enfileira os envios. Sem esta flag, roda so em modo preview.",
        )
        parser.add_argument(
            "--report",
            action="store_true",
            help="Grava um relatorio markdown em data/homologacao.",
        )

    def handle(self, *args, **options):
        actor = "manage.py:prepare_consolidated_receipt_campaign"
        cutoff_date = options["cutoff_date"]
        emission_date = options["emission_date"]
        limit = int(options["limit"] or 0)
        if options["queue"]:
            payload = prepare_consolidated_receipt_campaign(
                cutoff_date=cutoff_date,
                emission_date=emission_date,
                actor=actor,
                limit=limit,
            )
        else:
            payload = consolidated_receipt_campaign_summary(cutoff_date=cutoff_date)
            payload["campaign_key"] = f"retroativo_consolidado:{cutoff_date}"
            payload["emission_date"] = emission_date
            if limit > 0:
                payload["items"] = list(payload.get("items") or [])[:limit]
        result = {
            "queue_mode": bool(options["queue"]),
            "cutoff_date": cutoff_date,
            "emission_date": emission_date,
            **payload,
        }
        if options["report"]:
            result["report_path"] = _write_report(result)
        self.stdout.write(json.dumps(result, ensure_ascii=False, indent=2, default=str))

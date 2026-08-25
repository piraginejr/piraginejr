from __future__ import annotations

import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from power_church_django.services.statement_lot_saneamento import (
    DEFAULT_CORRECTION_BODY,
    DEFAULT_CORRECTION_SUBJECT,
    analyze_statement_lot_against_pdf,
    apply_statement_lot_saneamento,
    recover_partial_statement_lot_saneamento,
    rebuild_missing_statement_lot_from_native_contributions,
    write_statement_saneamento_report,
)


class Command(BaseCommand):
    help = "Audita e saneia um lote de extrato relendo o PDF original com o parser atual."

    def add_arguments(self, parser):
        parser.add_argument("--lot-id", required=True, type=int, help="ID do lote de extrato a auditar/sanear.")
        parser.add_argument("--pdf", required=True, help="Caminho do PDF original do extrato.")
        parser.add_argument("--layout", default="", help="Layout do extrato. Se vazio, usa o layout do lote.")
        parser.add_argument("--pdf-provider", default="pymupdf", help="Leitor PDF a usar no parser.")
        parser.add_argument("--apply", action="store_true", help="Aplica o saneamento no banco.")
        parser.add_argument(
            "--recover-partial",
            action="store_true",
            help="Recupera uma rodada parcial: cancela recibos/contribuicoes duplicados e reemite a partir das contribuicoes originais.",
        )
        parser.add_argument("--send-now", action="store_true", help="Envia imediatamente os novos recibos enfileirados.")
        parser.add_argument("--confirm", default="", help="Para aplicar, informe exatamente APPLICAR_SANEAMENTO.")
        parser.add_argument("--subject", default=DEFAULT_CORRECTION_SUBJECT, help="Assunto do e-mail de correção.")
        parser.add_argument("--body", default=DEFAULT_CORRECTION_BODY, help="Corpo do e-mail de correção.")
        parser.add_argument(
            "--rebuild-missing-from-notes",
            action="store_true",
            help="Se o lote sumiu da tabela de lotes, reconstrói movimentos a partir das contribuições nativas com a nota do lote.",
        )
        parser.add_argument("--json", action="store_true", help="Mostra resumo em JSON.")

    def handle(self, *args, **options):
        lot_id = int(options["lot_id"] or 0)
        pdf_path = Path(str(options["pdf"] or "")).expanduser()
        if not pdf_path.exists() or not pdf_path.is_file():
            raise CommandError(f"PDF nao encontrado: {pdf_path}")
        report_dir = Path(getattr(settings, "REPO_ROOT", Path(settings.BASE_DIR).parent)) / "reports"
        apply = bool(options["apply"])
        if apply and str(options.get("confirm") or "") != "APLICAR_SANEAMENTO":
            raise CommandError("Para aplicar, rode tambem: --confirm APLICAR_SANEAMENTO")
        recover_partial = bool(options["recover_partial"])
        if recover_partial and str(options.get("confirm") or "") != "APLICAR_SANEAMENTO":
            raise CommandError("Para recuperar uma rodada parcial, rode tambem: --confirm APLICAR_SANEAMENTO")
        if bool(options["send_now"]) and not apply and not recover_partial:
            raise CommandError("--send-now so pode ser usado junto com --apply ou --recover-partial.")
        if bool(options["rebuild_missing_from_notes"]):
            rebuild_missing_statement_lot_from_native_contributions(
                lot_id=lot_id,
                pdf_path=pdf_path,
                layout_code=str(options.get("layout") or ""),
                pdf_provider=str(options.get("pdf_provider") or "pymupdf"),
            )

        if recover_partial:
            result = recover_partial_statement_lot_saneamento(
                lot_id=lot_id,
                actor="manage.py:saneamento_lote_extrato",
                send_now=bool(options["send_now"]),
                subject=str(options.get("subject") or DEFAULT_CORRECTION_SUBJECT),
                body=str(options.get("body") or DEFAULT_CORRECTION_BODY),
            )
            summary = {
                "mode": "recovered",
                "lot_id": lot_id,
                "partial_receipt_ids_cancelled": result["partial_receipt_ids_cancelled"],
                "duplicate_contribution_ids_deactivated": result["duplicate_contribution_ids_deactivated"],
                "updated_contribution_ids": result["updated_contribution_ids"],
                "reissued_contribution_ids": result["reissued_contribution_ids"],
                "new_receipt_ids": result["new_receipt_ids"],
                "queued_dispatch_ids": result["queued_dispatch_ids"],
                "without_email": result["without_email"],
                "report": "",
            }
        elif apply:
            result = apply_statement_lot_saneamento(
                lot_id=lot_id,
                pdf_path=pdf_path,
                layout_code=str(options.get("layout") or ""),
                pdf_provider=str(options.get("pdf_provider") or "pymupdf"),
                actor="manage.py:saneamento_lote_extrato",
                send_now=bool(options["send_now"]),
                subject=str(options.get("subject") or DEFAULT_CORRECTION_SUBJECT),
                body=str(options.get("body") or DEFAULT_CORRECTION_BODY),
            )
            report = write_statement_saneamento_report(result, report_dir=report_dir, applied=True)
            summary = {
                "mode": "applied",
                "lot_id": lot_id,
                "differences": len(result["analysis"].differences),
                "cancelled_receipt_ids": result["cancelled_receipt_ids"],
                "updated_contribution_ids": result["updated_contribution_ids"],
                "new_receipt_ids": result["new_receipt_ids"],
                "queued_dispatch_ids": result["queued_dispatch_ids"],
                "without_email": result["without_email"],
                "report": str(report),
            }
        else:
            analysis = analyze_statement_lot_against_pdf(
                lot_id=lot_id,
                pdf_path=pdf_path,
                layout_code=str(options.get("layout") or ""),
                pdf_provider=str(options.get("pdf_provider") or "pymupdf"),
            )
            report = write_statement_saneamento_report(analysis, report_dir=report_dir, applied=False)
            summary = {
                "mode": "report",
                "lot_id": lot_id,
                "differences": len(analysis.differences),
                "affected_contribution_ids": analysis.affected_contribution_ids,
                "missing_orders": analysis.missing_orders,
                "extra_orders": analysis.extra_orders,
                "report": str(report),
            }
        if options["json"]:
            self.stdout.write(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            self.stdout.write(f"Modo: {summary['mode']}")
            self.stdout.write(f"Lote: {lot_id}")
            if "differences" in summary:
                self.stdout.write(f"Divergencias: {summary['differences']}")
            if summary.get("report"):
                self.stdout.write(f"Relatorio: {summary['report']}")
            if recover_partial:
                self.stdout.write(f"Recibos parciais cancelados: {summary['partial_receipt_ids_cancelled']}")
                self.stdout.write(f"Contribuicoes duplicadas desativadas: {summary['duplicate_contribution_ids_deactivated']}")
                self.stdout.write(f"Novos recibos: {summary['new_receipt_ids']}")
                self.stdout.write(f"Envios enfileirados: {summary['queued_dispatch_ids']}")
            elif apply:
                self.stdout.write(f"Recibos cancelados: {summary['cancelled_receipt_ids']}")
                self.stdout.write(f"Novos recibos: {summary['new_receipt_ids']}")
                self.stdout.write(f"Envios enfileirados: {summary['queued_dispatch_ids']}")

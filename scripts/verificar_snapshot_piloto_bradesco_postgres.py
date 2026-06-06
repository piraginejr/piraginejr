from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "data" / "homologacao"
DJANGO_DIR = ROOT / "power_church_django"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(DJANGO_DIR) not in sys.path:
    sys.path.insert(0, str(DJANGO_DIR))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "power_church_site.settings")

import django  # noqa: E402

django.setup()

from power_church_django.apps.imports.models import StatementImportPilotLot, StatementImportPilotMovement  # noqa: E402


@dataclass
class Check:
    name: str
    status: str
    detail: str

    @property
    def failed(self) -> bool:
        return self.status == "FALHA"


def expect(name: str, condition: bool, detail_ok: str, detail_fail: str) -> Check:
    return Check(name, "OK" if condition else "FALHA", detail_ok if condition else detail_fail)


def write_report(checks: list[Check], lot: StatementImportPilotLot | None) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    target = REPORT_DIR / f"snapshot_piloto_bradesco_postgres_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    lines = [
        "# Verificacao Snapshot Piloto Bradesco Postgres",
        "",
        f"Gerado em: {datetime.now().isoformat(timespec='seconds')}",
        f"Lote encontrado: `{lot.id if lot else '-'} {lot.file_name if lot else ''}`",
        "",
        "| Checagem | Status | Detalhe |",
        "| --- | --- | --- |",
    ]
    for check in checks:
        lines.append(f"| {check.name} | {check.status} | {check.detail} |")
    target.write_text("\n".join(lines), encoding="utf-8")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="Verifica o snapshot Postgres do piloto Bradesco.")
    parser.add_argument("--report", action="store_true", help="Gera relatorio Markdown em data/homologacao.")
    args = parser.parse_args()

    checks: list[Check] = []
    lot = StatementImportPilotLot.objects.filter(bank_name="Bradesco", file_name="BRADESCO_MAIO26.pdf").order_by("-id").first()
    checks.append(expect("Lote piloto Bradesco presente", lot is not None, "Snapshot do lote encontrado.", "Nenhum snapshot Bradesco encontrado no Postgres."))
    if lot is not None:
        checks.extend(
            [
                expect("Origem do lote", lot.source_backend == "legado_clone", "Origem marcada como legado_clone.", f"Origem retornada: {lot.source_backend!r}"),
                expect("Leitor PDF", lot.pdf_provider == "pymupdf", "Leitor PDF registrado como pymupdf.", f"Leitor retornado: {lot.pdf_provider!r}"),
                expect("Comparacao aprovada", bool(lot.comparison_ok), "Comparacao entre leitores aprovada.", "comparison_ok veio falso."),
                expect("Quantidade de movimentos", int(lot.movement_count or 0) == 33, "Snapshot com 33 movimentos.", f"Quantidade retornada: {lot.movement_count}"),
                expect("Status do lote", str(lot.lot_status or "") == "parcial", "Status do lote = parcial.", f"Status retornado: {lot.lot_status!r}"),
                expect(
                    "Distribuicao review_counts",
                    lot.metadata.get("review_counts") == {
                        "revisar_duplicidade": 17,
                        "pronto": 11,
                        "revisar_pessoa": 4,
                        "revisar_destinacao": 1,
                    },
                    "Distribuicao de review_counts preservada.",
                    f"Distribuicao retornada: {lot.metadata.get('review_counts')!r}",
                ),
                expect(
                    "Linhas materializadas",
                    StatementImportPilotMovement.objects.filter(lot=lot).count() == 33,
                    "As 33 linhas do lote foram materializadas.",
                    f"Quantidade retornada: {StatementImportPilotMovement.objects.filter(lot=lot).count()}",
                ),
            ]
        )
        ronald = StatementImportPilotMovement.objects.filter(lot=lot, order_in_lot=3).first()
        checks.append(
            expect(
                "Sentinela duplicidade Ronaldo no Postgres",
                ronald is not None
                and ronald.source_name == "RONALDO SANTOS MENDO"
                and ronald.review_status == "revisar_duplicidade"
                and int(ronald.duplicate_movement_legacy_id or 0) == 3157
                and int(ronald.duplicate_contribution_legacy_id or 0) == 7069,
                "Duplicidade sentinela do Ronaldo preservada no snapshot.",
                f"Snapshot retornado: {ronald}",
            )
        )
        alessandro = StatementImportPilotMovement.objects.filter(lot=lot, order_in_lot=41).first()
        checks.append(
            expect(
                "Sentinela centavos Alessandro no Postgres",
                alessandro is not None
                and alessandro.source_name == "ALESSANDRO DA SILVA BATISTA"
                and alessandro.review_status == "revisar_destinacao"
                and alessandro.cent_code == "03"
                and float(alessandro.amount) == 1000.03,
                "Caso de centavos especiais preservado no snapshot.",
                f"Snapshot retornado: {alessandro}",
            )
        )
    report = write_report(checks, lot) if args.report else None
    for check in checks:
        print(f"{check.status}: {check.name} - {check.detail}")
    if report:
        print(f"Relatorio: {report}")
    return 1 if any(check.failed for check in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())

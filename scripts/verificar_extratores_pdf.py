from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "power_church_membros_importado.db"
REPORT_DIR = ROOT / "data" / "homologacao"


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


def ensure_root_path() -> None:
    root_text = str(ROOT)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)


def run_fixture_with_provider(db_path: Path, provider_code: str):
    os.environ["POWER_CHURCH_PDF_PROVIDER"] = provider_code
    from scripts import verificar_fixtures_pdf_bancos

    return verificar_fixtures_pdf_bancos.build_fixtures(db_path)


def build_checks(db_path: Path, compare_provider: str = "") -> list[Check]:
    ensure_root_path()
    from power_church_core import pdf_text

    checks: list[Check] = []
    statuses = pdf_text.provider_statuses()
    active_provider = pdf_text.active_provider_code()
    available = {status.code for status in statuses if status.available}
    for status in statuses:
        checks.append(Check(f"Provedor {status.code}", True, f"{'disponivel' if status.available else 'indisponivel'}: {status.detail}"))
    checks.append(Check("Provedor ativo", active_provider in available, active_provider))

    provider_to_compare = compare_provider.strip().lower()
    if provider_to_compare:
        if provider_to_compare not in available:
            checks.append(Check("Comparacao de provedor portatil", False, f"{provider_to_compare} indisponivel"))
            return checks
        fixture_checks, _payload = run_fixture_with_provider(db_path, provider_to_compare)
        failed = [check for check in fixture_checks if not check.ok]
        checks.append(
            Check(
                f"Fixtures com {provider_to_compare}",
                not failed,
                f"{len(fixture_checks) - len(failed)}/{len(fixture_checks)} lote(s) OK",
            )
        )
    else:
        portable_available = "pymupdf" in available
        checks.append(
            Check(
                "Comparacao portatil opcional",
                True,
                "PyMuPDF disponivel para comparacao" if portable_available else "PyMuPDF ainda nao instalado; comparacao portatil adiada",
            )
        )
    return checks


def write_report(checks: list[Check]) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    target = REPORT_DIR / f"extratores_pdf_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    failed = [check for check in checks if not check.ok]
    lines = [
        "# Extratores PDF",
        "",
        f"Gerado em: {datetime.now().isoformat(timespec='seconds')}",
        f"Resultado: {'OK' if not failed else 'FALHAS'}",
        "",
        "| Check | Status | Detalhe |",
        "| --- | --- | --- |",
    ]
    for check in checks:
        lines.append(f"| {check.name} | {'OK' if check.ok else 'FALHA'} | {check.detail} |")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="Verifica arquitetura de provedores de extracao PDF.")
    parser.add_argument("--db", default=str(DB_PATH), help="Caminho do banco SQLite.")
    parser.add_argument("--compare-provider", default="", help="Provedor a comparar nas fixtures, por exemplo: pymupdf.")
    parser.add_argument("--report", action="store_true", help="Grava relatorio markdown.")
    args = parser.parse_args()
    checks = build_checks(Path(args.db), compare_provider=args.compare_provider)
    for check in checks:
        print(f"- {'OK' if check.ok else 'FALHA'}: {check.name} ({check.detail})")
    if args.report:
        report = write_report(checks)
        print(f"\nRelatorio: {report}")
    return 1 if any(not check.ok for check in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())

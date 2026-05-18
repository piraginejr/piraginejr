from __future__ import annotations

import argparse
import importlib.util
import platform
import shutil
import sqlite3
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
    status: str
    detail: str

    @property
    def failed(self) -> bool:
        return self.status == "FALHA"


def module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def command_status(command: str) -> str:
    path = shutil.which(command)
    return path or "nao encontrado"


def build_checks(profile: str, db_path: Path) -> list[Check]:
    root_text = str(ROOT)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)

    from power_church_core import ocr, pdf_text

    profile = (profile or "local").lower()
    server_mode = profile == "server"
    checks: list[Check] = []

    python_ok = sys.version_info >= (3, 9)
    checks.append(
        Check(
            "Python",
            "OK" if python_ok else "FALHA",
            f"{platform.python_version()} em {sys.executable}",
        )
    )
    checks.append(Check("Sistema operacional", "OK", f"{platform.system()} {platform.release()}"))

    sqlite_ok = sqlite3.sqlite_version_info >= (3, 30, 0)
    checks.append(Check("SQLite Python", "OK" if sqlite_ok else "FALHA", sqlite3.sqlite_version))
    checks.append(
        Check(
            "Comando sqlite3",
            "OK" if shutil.which("sqlite3") else "ALERTA",
            command_status("sqlite3"),
        )
    )

    checks.append(
        Check(
            "Banco de dados",
            "OK" if db_path.exists() else "ALERTA",
            str(db_path) if db_path.exists() else "banco ainda nao copiado para este ambiente",
        )
    )
    for relative in (
        "data",
        "data/homologacao",
        "data/statement_uploads",
        "data/pix_uploads",
        "data/envelope_uploads",
        "power_church_core",
        "scripts",
    ):
        path = ROOT / relative
        checks.append(Check(f"Diretorio {relative}", "OK" if path.exists() else "FALHA", str(path)))

    statuses = pdf_text.provider_statuses()
    status_by_code = {status.code: status for status in statuses}
    active_provider = ""
    try:
        active_provider = pdf_text.active_provider_code()
        checks.append(Check("PDF provedor ativo", "OK", active_provider))
    except Exception as exc:
        checks.append(Check("PDF provedor ativo", "FALHA", f"{type(exc).__name__}: {exc}"))
    for status in statuses:
        if status.code == "pymupdf" and server_mode:
            expected_status = "OK" if status.available else "FALHA"
        elif status.code == "swift_pdfkit" and not server_mode:
            expected_status = "OK" if status.available else "FALHA"
        else:
            expected_status = "OK" if status.available else "ALERTA"
        checks.append(
            Check(
                f"PDF provedor {status.code}",
                expected_status,
                f"{'disponivel' if status.available else 'indisponivel'}: {status.detail}",
            )
        )
    if server_mode and active_provider and active_provider != "pymupdf":
        checks.append(Check("PDF server usa provedor portatil", "FALHA", f"ativo: {active_provider}"))
    elif server_mode and status_by_code.get("pymupdf") and status_by_code["pymupdf"].available:
        checks.append(Check("PDF server usa provedor portatil", "OK", "pymupdf disponivel"))

    checks.append(
        Check(
            "Modulo PyMuPDF",
            "OK" if module_available("fitz") else ("FALHA" if server_mode else "ALERTA"),
            "fitz encontrado" if module_available("fitz") else "fitz/PyMuPDF nao instalado",
        )
    )

    tesseract = ocr.tesseract_status()
    checks.append(
        Check(
            "OCR Tesseract",
            "OK" if tesseract.available else ("FALHA" if server_mode else "ALERTA"),
            f"{tesseract.command or 'nao encontrado'}; {tesseract.detail}",
        )
    )
    return checks


def write_report(checks: list[Check], profile: str) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    target = REPORT_DIR / f"dependencias_servidor_{profile}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    failed = [check for check in checks if check.failed]
    alerts = [check for check in checks if check.status == "ALERTA"]
    lines = [
        "# Dependencias De Ambiente",
        "",
        f"Gerado em: {datetime.now().isoformat(timespec='seconds')}",
        f"Perfil: {profile}",
        f"Resultado: {'FALHAS' if failed else 'OK COM ALERTAS' if alerts else 'OK'}",
        "",
        "| Check | Status | Detalhe |",
        "| --- | --- | --- |",
    ]
    for check in checks:
        lines.append(f"| {check.name} | {check.status} | {check.detail} |")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="Verifica dependencias do ambiente local ou servidor.")
    parser.add_argument("--profile", choices=["local", "server"], default="local", help="Perfil de validacao.")
    parser.add_argument("--db", default=str(DB_PATH), help="Caminho do banco SQLite.")
    parser.add_argument("--report", action="store_true", help="Grava relatorio markdown.")
    args = parser.parse_args()
    checks = build_checks(args.profile, Path(args.db))
    for check in checks:
        print(f"- {check.status}: {check.name} ({check.detail})")
    if args.report:
        report = write_report(checks, args.profile)
        print(f"\nRelatorio: {report}")
    return 1 if any(check.failed for check in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())

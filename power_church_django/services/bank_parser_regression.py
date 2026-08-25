from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from unittest.mock import patch

from django.conf import settings
from django.utils import timezone

from power_church_core.bank_parsers import parse_statement_pdf_by_layout


@dataclass(slots=True)
class BankParserRegressionCheck:
    name: str
    ok: bool
    detail: str
    bank: str
    layout: str
    severity: str = "FAIL"


def run_bank_parser_regression_checks() -> list[BankParserRegressionCheck]:
    checks = [_check_sicoob_current_account_visual_alignment()]
    checks.extend(_check_optional_real_sicoob_june_pdf())
    return checks


def write_bank_parser_regression_report(checks: list[BankParserRegressionCheck]) -> Path:
    repo_root = Path(getattr(settings, "REPO_ROOT", Path(settings.BASE_DIR).parent))
    report_dir = repo_root / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    target = report_dir / f"bank_parser_regression_{timezone.localtime().strftime('%Y%m%d_%H%M%S')}.md"
    failed = [check for check in checks if not check.ok and check.severity == "FAIL"]
    lines = [
        "# Bank Parser Regression Audit",
        "",
        f"Gerado em: {timezone.localtime().isoformat(timespec='seconds')}",
        f"Resultado: {'OK' if not failed else 'FALHAS'}",
        "",
        "Esta auditoria protege contra regressao em que o total do extrato bate, mas o valor e atribuido a pessoa errada.",
        "",
        "| Banco | Layout | Check | Status | Detalhe |",
        "| --- | --- | --- | --- | --- |",
    ]
    for check in checks:
        status = "OK" if check.ok else check.severity
        lines.append(
            "| "
            + " | ".join(
                [
                    check.bank,
                    check.layout,
                    check.name,
                    status,
                    check.detail.replace("|", "/"),
                ]
            )
            + " |"
        )
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def _check_sicoob_current_account_visual_alignment() -> BankParserRegressionCheck:
    page_rows = [
        {"text": "Data", "x": 37.5, "y": 800.3},
        {"text": "Documento", "x": 69.1, "y": 800.3},
        {"text": "Histórico", "x": 132.4, "y": 800.3},
        {"text": "Valor", "x": 536.7, "y": 800.3},
        {"text": "11/06", "x": 37.5, "y": 690.0},
        {"text": "Pix", "x": 69.1, "y": 690.0},
        {"text": "PIX RECEBIDO - OUTRA IF", "x": 145.9, "y": 690.0},
        {"text": "Recebimento Pix Patricia Gomes de Andrade Brandao ***.574.697-** dizimo restante", "x": 132.4, "y": 681.3},
        {"text": "R$ 60,00C", "x": 508.5, "y": 688.2},
        {"text": "11/06", "x": 37.5, "y": 665.3},
        {"text": "Pix", "x": 69.1, "y": 665.3},
        {"text": "PIX RECEBIDO - OUTRA IF", "x": 145.9, "y": 665.3},
        {"text": "Recebimento Pix ODILON GUIMARAES JUNIOR ***.481.357-**", "x": 132.4, "y": 656.6},
        {"text": "R$ 1.020,00C", "x": 493.9, "y": 663.4},
        {"text": "11/06", "x": 37.5, "y": 640.5},
        {"text": "Pix", "x": 69.1, "y": 640.5},
        {"text": "PIX RECEBIDO - OUTRA IF", "x": 145.9, "y": 640.5},
        {"text": "Recebimento Pix MARIA JOSE DE SOUZA PONCE RIBEIRO ***.969.047-**", "x": 132.4, "y": 631.8},
        {"text": "R$ 200,00C", "x": 502.7, "y": 638.7},
    ]
    try:
        with NamedTemporaryFile(suffix=".pdf") as tmp:
            tmp.write(b"fake-sicoob-pdf")
            tmp.flush()
            with patch("power_church_core.bank_parsers.extract_pdf_pages", return_value=["PERÍODO: 01/06/2026 - 30/06/2026"]):
                with patch("power_church_core.bank_parsers.extract_pdf_line_selections", return_value=[page_rows]):
                    parsed = parse_statement_pdf_by_layout("SICOOB_CONTA_CORRENTE", Path(tmp.name))
        entries = parsed.get("entries") or []
        by_name = {str(entry.get("source_name") or ""): entry for entry in entries}
        expected = {
            "Patricia Gomes de Andrade Brandao": 60.0,
            "ODILON GUIMARAES JUNIOR": 1020.0,
            "MARIA JOSE DE SOUZA PONCE RIBEIRO": 200.0,
        }
        observed = {name: round(float((by_name.get(name) or {}).get("amount") or 0), 2) for name in expected}
        ok = observed == expected and len(entries) == 3 and round(sum(observed.values()), 2) == 1280.0
        return BankParserRegressionCheck(
            name="Sicoob mantem valor com a pessoa visual correta",
            ok=ok,
            detail=f"esperado={expected}; observado={observed}; linhas={len(entries)}",
            bank="Sicoob",
            layout="SICOOB_CONTA_CORRENTE",
        )
    except Exception as exc:
        return BankParserRegressionCheck(
            name="Sicoob mantem valor com a pessoa visual correta",
            ok=False,
            detail=f"{type(exc).__name__}: {exc}",
            bank="Sicoob",
            layout="SICOOB_CONTA_CORRENTE",
        )


def _check_optional_real_sicoob_june_pdf() -> list[BankParserRegressionCheck]:
    pdf_path = _optional_real_sicoob_pdf_path()
    if pdf_path is None:
        return [
            BankParserRegressionCheck(
                name="PDF real Sicoob junho disponivel para sentinela",
                ok=False,
                detail="PDF real nao encontrado no runtime; sentinela sintetica continua ativa.",
                bank="Sicoob",
                layout="SICOOB_CONTA_CORRENTE",
                severity="WARN",
            )
        ]
    try:
        parsed = parse_statement_pdf_by_layout("SICOOB_CONTA_CORRENTE", pdf_path)
        entries = parsed.get("entries") or []
        targets: dict[str, list[float]] = {
            "ODILON GUIMARAES JUNIOR": [],
            "Patricia Gomes de Andrade Brandao": [],
        }
        for entry in entries:
            if str(entry.get("received_on") or "") != "2026-06-11":
                continue
            source_name = str(entry.get("source_name") or "")
            if source_name in targets:
                targets[source_name].append(round(float(entry.get("amount") or 0), 2))
        ok = targets["ODILON GUIMARAES JUNIOR"] == [1020.0] and 60.0 in targets["Patricia Gomes de Andrade Brandao"]
        return [
            BankParserRegressionCheck(
                name="PDF real Sicoob junho - Odilon/Patricia",
                ok=ok,
                detail=f"arquivo={pdf_path}; observado={targets}",
                bank="Sicoob",
                layout="SICOOB_CONTA_CORRENTE",
            )
        ]
    except Exception as exc:
        return [
            BankParserRegressionCheck(
                name="PDF real Sicoob junho - Odilon/Patricia",
                ok=False,
                detail=f"arquivo={pdf_path}; {type(exc).__name__}: {exc}",
                bank="Sicoob",
                layout="SICOOB_CONTA_CORRENTE",
            )
        ]


def _optional_real_sicoob_pdf_path() -> Path | None:
    repo_root = Path(getattr(settings, "REPO_ROOT", Path(settings.BASE_DIR).parent))
    candidates: list[Path] = []
    configured = str(getattr(settings, "POWER_CHURCH_BANK_PARSER_AUDIT_SICOOB_JUNE_PDF", "") or "").strip()
    if configured:
        candidates.append(Path(configured))
    candidates.extend(
        [
            repo_root / "data" / "statement_uploads" / "2026-06 Sicoob - Créditos.pdf",
            Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "Downloads" / "Downloads" / "2026-06 Sicoob - Créditos.pdf",
        ]
    )
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    return None

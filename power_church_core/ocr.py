from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class OcrEngineStatus:
    available: bool
    command: str
    version: str
    languages: tuple[str, ...]
    detail: str

    @property
    def portuguese_available(self) -> bool:
        return "por" in self.languages


def tesseract_command() -> str:
    configured = os.environ.get("POWER_CHURCH_TESSERACT_CMD", "").strip()
    candidates = [
        Path(configured) if configured else None,
        ROOT / ".tools" / "ocr-env" / "bin" / "tesseract",
        ROOT / ".tools" / "tesseract" / "bin" / "tesseract",
    ]
    for candidate in candidates:
        if candidate and candidate.exists() and candidate.is_file():
            return str(candidate)
    return shutil.which("tesseract") or ""


def _run_tesseract(command: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [command, *args],
        text=True,
        capture_output=True,
        check=False,
        timeout=12,
    )


def tesseract_status() -> OcrEngineStatus:
    command = tesseract_command()
    if not command:
        return OcrEngineStatus(False, "", "", (), "Tesseract nao encontrado.")
    try:
        version_result = _run_tesseract(command, "--version")
        language_result = _run_tesseract(command, "--list-langs")
    except Exception as exc:
        return OcrEngineStatus(False, command, "", (), f"Erro ao executar Tesseract: {type(exc).__name__}: {exc}")
    version_text = (version_result.stdout or version_result.stderr or "").splitlines()
    version = version_text[0].strip() if version_text else ""
    language_lines = [
        line.strip()
        for line in (language_result.stdout or language_result.stderr or "").splitlines()
        if line.strip() and not line.lower().startswith("list of available")
    ]
    available = version_result.returncode == 0 and language_result.returncode == 0 and "por" in language_lines
    detail = f"{version or 'versao nao informada'}; {len(language_lines)} idioma(s); por={'OK' if 'por' in language_lines else 'ausente'}"
    return OcrEngineStatus(available, command, version, tuple(language_lines), detail)


def configure_pytesseract() -> str:
    command = tesseract_command()
    if not command:
        return ""
    try:
        import pytesseract
    except Exception:
        return command
    pytesseract.pytesseract.tesseract_cmd = command
    return command

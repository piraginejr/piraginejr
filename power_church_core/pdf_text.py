from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


DEFAULT_PROVIDER = "auto"
SWIFT_PROVIDER = "swift_pdfkit"
PYMUPDF_PROVIDER = "pymupdf"


@dataclass(frozen=True)
class PdfProviderStatus:
    code: str
    label: str
    available: bool
    detail: str


def _swift_env() -> dict[str, str]:
    env = dict(os.environ)
    module_cache = Path(tempfile.gettempdir()) / "swift-module-cache"
    fake_home = Path(tempfile.gettempdir()) / "swift-home"
    module_cache.mkdir(parents=True, exist_ok=True)
    fake_home.mkdir(parents=True, exist_ok=True)
    env["CLANG_MODULE_CACHE_PATH"] = str(module_cache)
    env["HOME"] = str(fake_home)
    return env


def _parse_json_pages(stdout: str, error_message: str) -> list[object]:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(error_message) from exc
    if not isinstance(payload, list):
        raise ValueError(error_message)
    return payload


def _normalize_line_pages(payload: list[object]) -> list[list[dict[str, object]]]:
    pages: list[list[dict[str, object]]] = []
    for page in payload:
        page_rows: list[dict[str, object]] = []
        for item in page or []:
            page_rows.append(
                {
                    "text": str(item.get("text") or ""),
                    "x": float(item.get("x") or 0.0),
                    "y": float(item.get("y") or 0.0),
                    "w": float(item.get("w") or 0.0),
                    "h": float(item.get("h") or 0.0),
                }
            )
        pages.append(page_rows)
    return pages


def _page_text_from_positioned_rows(rows: list[dict[str, object]], y_tolerance: float = 2.0) -> str:
    grouped: list[dict[str, object]] = []
    for row in sorted(rows, key=lambda item: (-float(item.get("y") or 0.0), float(item.get("x") or 0.0))):
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        y = float(row.get("y") or 0.0)
        for group in grouped:
            if abs(float(group["y"]) - y) <= y_tolerance:
                group["items"].append(row)
                break
        else:
            grouped.append({"y": y, "items": [row]})
    lines: list[str] = []
    for group in grouped:
        items = sorted(group["items"], key=lambda item: float(item.get("x") or 0.0))
        line = " ".join(str(item.get("text") or "").strip() for item in items if str(item.get("text") or "").strip())
        if line:
            lines.append(line)
    return "\n".join(lines)


class SwiftPdfKitExtractor:
    code = SWIFT_PROVIDER
    label = "Swift/PDFKit"

    @classmethod
    def status(cls) -> PdfProviderStatus:
        if shutil.which("swift"):
            return PdfProviderStatus(cls.code, cls.label, True, "comando swift encontrado")
        return PdfProviderStatus(cls.code, cls.label, False, "comando swift nao encontrado")

    @classmethod
    def extract_pages(cls, pdf_path: Path) -> list[str]:
        swift_script = """
import Foundation
import PDFKit

let path = CommandLine.arguments[1]
let url = URL(fileURLWithPath: path)
guard let doc = PDFDocument(url: url) else {
    fputs("OPEN_FAIL\\n", stderr)
    exit(1)
}
var pages: [String] = []
for index in 0..<doc.pageCount {
    pages.append(doc.page(at: index)?.string ?? "")
}
let data = try JSONSerialization.data(withJSONObject: pages, options: [])
FileHandle.standardOutput.write(data)
"""
        completed = subprocess.run(
            ["swift", "-", str(pdf_path)],
            input=swift_script,
            text=True,
            capture_output=True,
            env=_swift_env(),
            check=False,
        )
        if completed.returncode != 0:
            raise ValueError("Nao foi possivel ler o PDF com Swift/PDFKit.")
        payload = _parse_json_pages(completed.stdout, "O PDF nao retornou texto estruturado via Swift/PDFKit.")
        return [str(item or "") for item in payload]

    @classmethod
    def extract_line_selections(cls, pdf_path: Path) -> list[list[dict[str, object]]]:
        swift_script = """
import Foundation
import PDFKit

let path = CommandLine.arguments[1]
let url = URL(fileURLWithPath: path)
guard let doc = PDFDocument(url: url) else {
    fputs("OPEN_FAIL\\n", stderr)
    exit(1)
}
var pages: [[[String: Any]]] = []
for index in 0..<doc.pageCount {
    guard let page = doc.page(at: index) else {
        pages.append([])
        continue
    }
    guard let selection = page.selection(for: page.bounds(for: .mediaBox)) else {
        pages.append([])
        continue
    }
    let lines = selection.selectionsByLine()
    var pageLines: [[String: Any]] = []
    for line in lines {
        let text = (line.string ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        if text.isEmpty { continue }
        let bounds = line.bounds(for: page)
        pageLines.append(
            [
                "text": text,
                "x": bounds.origin.x,
                "y": bounds.origin.y,
                "w": bounds.size.width,
                "h": bounds.size.height,
            ]
        )
    }
    pages.append(pageLines)
}
let data = try JSONSerialization.data(withJSONObject: pages, options: [])
FileHandle.standardOutput.write(data)
"""
        completed = subprocess.run(
            ["swift", "-", str(pdf_path)],
            input=swift_script,
            text=True,
            capture_output=True,
            env=_swift_env(),
            check=False,
        )
        if completed.returncode != 0:
            raise ValueError("Nao foi possivel ler as linhas do PDF com Swift/PDFKit.")
        payload = _parse_json_pages(completed.stdout, "O PDF nao retornou linhas estruturadas via Swift/PDFKit.")
        return _normalize_line_pages(payload)


class PyMuPdfExtractor:
    code = PYMUPDF_PROVIDER
    label = "PyMuPDF"

    @classmethod
    def status(cls) -> PdfProviderStatus:
        if importlib.util.find_spec("fitz") is not None:
            return PdfProviderStatus(cls.code, cls.label, True, "modulo fitz encontrado")
        return PdfProviderStatus(cls.code, cls.label, False, "modulo fitz/PyMuPDF nao instalado")

    @classmethod
    def extract_pages(cls, pdf_path: Path) -> list[str]:
        line_pages = cls.extract_line_selections(pdf_path)
        return [_page_text_from_positioned_rows(rows) for rows in line_pages]

    @classmethod
    def extract_line_selections(cls, pdf_path: Path) -> list[list[dict[str, object]]]:
        import fitz  # type: ignore[import-not-found]

        pages: list[list[dict[str, object]]] = []
        with fitz.open(str(pdf_path)) as doc:
            for page in doc:
                page_height = float(page.rect.height)
                rows: list[dict[str, object]] = []
                text_dict = page.get_text("dict")
                for block in text_dict.get("blocks", []):
                    if block.get("type") != 0:
                        continue
                    for line in block.get("lines", []):
                        spans = line.get("spans", [])
                        text = " ".join(str(span.get("text") or "").strip() for span in spans).strip()
                        if not text:
                            continue
                        x0, y0, x1, y1 = [float(value or 0.0) for value in line.get("bbox", [0, 0, 0, 0])]
                        # PyMuPDF usa origem no topo; os parsers atuais esperam coordenada tipo PDFKit.
                        rows.append({"text": text, "x": x0, "y": page_height - y1, "w": x1 - x0, "h": y1 - y0})
                pages.append(rows)
        return pages


PROVIDERS = {
    SWIFT_PROVIDER: SwiftPdfKitExtractor,
    PYMUPDF_PROVIDER: PyMuPdfExtractor,
}


def provider_statuses() -> list[PdfProviderStatus]:
    return [provider.status() for provider in PROVIDERS.values()]


def available_provider_codes() -> list[str]:
    return [status.code for status in provider_statuses() if status.available]


def resolve_provider(provider: str = DEFAULT_PROVIDER):
    requested = str(provider or DEFAULT_PROVIDER).strip().lower()
    if requested == DEFAULT_PROVIDER:
        env_provider = str(os.environ.get("POWER_CHURCH_PDF_PROVIDER") or "").strip().lower()
        if env_provider:
            requested = env_provider
    if requested == DEFAULT_PROVIDER:
        # Mantem o provedor homologado como padrao; provedores portateis entram por opt-in.
        for candidate in (SWIFT_PROVIDER, PYMUPDF_PROVIDER):
            provider_class = PROVIDERS[candidate]
            if provider_class.status().available:
                return provider_class
        raise ValueError("Nenhum provedor de PDF disponivel.")
    provider_class = PROVIDERS.get(requested)
    if provider_class is None:
        raise ValueError(f"Provedor de PDF desconhecido: {provider}.")
    status = provider_class.status()
    if not status.available:
        raise ValueError(f"Provedor de PDF indisponivel: {status.label} ({status.detail}).")
    return provider_class


def active_provider_code(provider: str = DEFAULT_PROVIDER) -> str:
    return str(resolve_provider(provider).code)


def extract_pdf_pages(pdf_path: Path, provider: str = DEFAULT_PROVIDER) -> list[str]:
    return resolve_provider(provider).extract_pages(pdf_path)


def extract_pdf_line_selections(pdf_path: Path, provider: str = DEFAULT_PROVIDER) -> list[list[dict[str, object]]]:
    return resolve_provider(provider).extract_line_selections(pdf_path)

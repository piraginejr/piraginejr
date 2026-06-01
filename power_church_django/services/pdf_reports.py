from __future__ import annotations

from datetime import date
from functools import lru_cache
from typing import Any

from power_church_core.formatting import br_date
from power_church_core.normalization import normalize_query
from power_church_django.services.branding import brand_logo_available, brand_logo_path


def _pdf_escape(value: object) -> str:
    text = str(value or "")
    text = text.encode("latin-1", errors="replace").decode("latin-1")
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _wrap(value: object, width: int) -> list[str]:
    words = normalize_query(value).split()
    if not words:
        return [""]
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= width:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = word[:width]
    if current:
        lines.append(current)
    return lines


def _compact_remittances(remittances: list[dict[str, Any]], limit: int = 4) -> str:
    chunks = [
        f"{item.get('data')} {item.get('valor_fmt')}"
        for item in remittances[:limit]
    ]
    if len(remittances) > limit:
        chunks.append(f"+{len(remittances) - limit}")
    return " | ".join(chunks)


@lru_cache(maxsize=1)
def _brand_logo_jpeg() -> bytes | None:
    if not brand_logo_available():
        return None
    try:
        return brand_logo_path().read_bytes()
    except OSError:
        return None


@lru_cache(maxsize=1)
def _brand_logo_dimensions() -> tuple[int, int] | None:
    payload = _brand_logo_jpeg()
    if not payload or len(payload) < 4 or payload[:2] != b"\xff\xd8":
        return None
    index = 2
    markers = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
    while index + 8 <= len(payload):
        while index < len(payload) and payload[index] != 0xFF:
            index += 1
        while index < len(payload) and payload[index] == 0xFF:
            index += 1
        if index >= len(payload):
            break
        marker = payload[index]
        index += 1
        if marker in {0xD8, 0xD9}:
            continue
        if index + 1 >= len(payload):
            break
        length = (payload[index] << 8) | payload[index + 1]
        index += 2
        if length < 2 or index + length - 2 > len(payload):
            break
        if marker in markers:
            height = (payload[index + 1] << 8) | payload[index + 2]
            width = (payload[index + 3] << 8) | payload[index + 4]
            if width > 0 and height > 0:
                return width, height
            break
        index += length - 2
    return None


def _append_brand_logo(page_ops: list[str], x: int, y_top: int, max_width: int = 130, max_height: int = 50) -> None:
    dimensions = _brand_logo_dimensions()
    if not dimensions:
        return
    width, height = dimensions
    scale = min(max_width / float(width), max_height / float(height))
    draw_width = width * scale
    draw_height = height * scale
    y_bottom = y_top - draw_height
    page_ops.append(f"q {draw_width:.2f} 0 0 {draw_height:.2f} {x:.2f} {y_bottom:.2f} cm /ImBrand Do Q")


def _build_pdf(pages: list[list[str]]) -> bytes:
    page_payloads = ["\n".join(page_ops).encode("latin-1", errors="replace") for page_ops in pages]
    kids: list[str] = []
    objects: list[bytes] = [b""]
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(b"")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")
    brand_logo = _brand_logo_jpeg()
    brand_dimensions = _brand_logo_dimensions()
    brand_object_id: int | None = None
    if brand_logo and brand_dimensions:
        width, height = brand_dimensions
        objects.append(
            (
                f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} "
                f"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length {len(brand_logo)} >>\nstream\n"
            ).encode("ascii")
            + brand_logo
            + b"\nendstream"
        )
        brand_object_id = len(objects) - 1
    for index, content in enumerate(page_payloads):
        content_id = len(objects)
        objects.append(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
        page_id = content_id + 1
        objects.append(b"")
        kids.append(f"{page_id} 0 R")
        resources = "/Resources << /Font << /F1 3 0 R /F2 4 0 R >>"
        if brand_object_id is not None:
            resources += f" /XObject << /ImBrand {brand_object_id} 0 R >>"
        resources += " >>"
        objects[page_id] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            f"{resources} /Contents {content_id} 0 R >>".encode("ascii")
        )
    objects[2] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>".encode("ascii")
    pdf = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
    offsets = [0]
    for object_id, payload in enumerate(objects[1:], start=1):
        offsets.append(len(pdf))
        pdf += f"{object_id} 0 obj\n".encode("ascii") + payload + b"\nendobj\n"
    xref_offset = len(pdf)
    pdf += f"xref\n0 {len(objects)}\n".encode("ascii")
    pdf += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        pdf += f"{offset:010d} 00000 n \n".encode("ascii")
    pdf += (
        f"trailer << /Size {len(objects)} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n"
    ).encode("ascii")
    return pdf


def contribution_period_pdf(report: dict[str, Any]) -> bytes:
    pages: list[list[str]] = []
    current: list[str] = []
    y = 800

    def new_page() -> None:
        nonlocal current, y
        if current:
            pages.append(current)
        current = ["0.12 0.16 0.22 rg"]
        y = 800

    def text_at(x: int, y_pos: int, value: object, size: int = 9, bold: bool = False) -> None:
        font = "F2" if bold else "F1"
        current.append(f"0.12 0.16 0.22 rg BT /{font} {size} Tf 1 0 0 1 {x} {y_pos} Tm ({_pdf_escape(value)}) Tj ET")

    def text_right(x_right: int, y_pos: int, value: object, size: int = 9, bold: bool = False) -> None:
        raw = str(value or "")
        factor = 0.56 if bold else 0.51
        width = max(len(raw) * size * factor, 0)
        x = max(42, x_right - int(round(width)))
        text_at(x, y_pos, raw, size=size, bold=bold)

    def text(x: int, value: object, size: int = 9, bold: bool = False) -> None:
        text_at(x, y, value, size=size, bold=bold)

    def line(value: object, size: int = 9, bold: bool = False, x: int = 42, advance: int = 14) -> None:
        nonlocal y
        if y < 54:
            new_page()
        text(x, value, size=size, bold=bold)
        y -= advance

    def rule() -> None:
        nonlocal y
        current.append(f"0.86 0.83 0.78 RG 0.8 w 42 {y} m 553 {y} l S")
        y -= 12

    def fill_rect(x: int, y_pos: int, width: int, height: int, color: str = "0.98 0.95 0.90") -> None:
        current.append(f"{color} rg {x} {y_pos} {width} {height} re f")

    def stroke_rect(x: int, y_pos: int, width: int, height: int, color: str = "0.86 0.80 0.70", line_width: float = 0.6) -> None:
        current.append(f"{color} RG {line_width:.2f} w {x} {y_pos} {width} {height} re S")

    def summary_box(
        top: int,
        label: str,
        value: object,
        x: int,
        *,
        width: int = 98,
        height: int = 34,
        value_size: int = 10,
        max_lines: int = 2,
    ) -> None:
        fill_rect(x, top - height, width, height, "0.995 0.985 0.965")
        fill_rect(x, top - 13, width, 13, "0.95 0.91 0.84")
        stroke_rect(x, top - height, width, height, "0.84 0.78 0.70", 0.7)
        text_at(x + 7, top - 9, label, size=6, bold=True)
        lines = _wrap(value, max(10, int((width - 16) / 5.6)))[:max_lines] or ["-"]
        for index, chunk in enumerate(lines):
            text_at(x + 7, top - 25 - (index * 10), chunk, size=value_size, bold=index == 0)

    def logo_panel(top: int, x: int = 366, width: int = 187, height: int = 84) -> None:
        fill_rect(x, top - height, width, height, "0.998 0.992 0.980")
        stroke_rect(x, top - height, width, height, "0.84 0.78 0.70", 0.8)
        if _brand_logo_dimensions():
            _append_brand_logo(current, x + 10, top - 8, width - 20, 66)
        else:
            text_at(x + 10, top - 36, "Espaco reservado", size=7, bold=False)
            text_at(x + 10, top - 52, "Logo do cliente", size=11, bold=True)

    def summary_boxes(summary: dict[str, Any]) -> None:
        nonlocal y
        boxes = [
            ("Total geral", summary.get("total_fmt")),
            ("Contribuintes", summary.get("contribuintes")),
            ("Remessas", summary.get("remessas")),
            ("Fora do rol", summary.get("nr")),
            ("Somente doc.", summary.get("somente_documento")),
        ]
        width = 98
        gap = 5
        top = y
        for index, (label, value) in enumerate(boxes):
            x = 42 + (index * (width + gap))
            summary_box(top, label, value, x, width=width, height=34, value_size=10, max_lines=2)
        y -= 46

    def table_header() -> None:
        nonlocal y
        if y < 90:
            new_page()
        fill_rect(42, y - 15, 511, 18, "0.95 0.91 0.84")
        text_at(48, y - 10, "Contribuinte", size=8, bold=True)
        text_at(270, y - 10, "Remessas", size=8, bold=True)
        text_at(456, y - 10, "Total", size=8, bold=True)
        text_at(520, y - 10, "Rol", size=8, bold=True)
        y -= 24

    def row_separator() -> None:
        current.append(f"0.90 0.86 0.80 RG 0.4 w 42 {y + 3} m 553 {y + 3} l S")

    new_page()
    summary = report.get("summary", {})
    filters = []
    if report.get("competencia"):
        filters.append(f"Competencia: {report['competencia']}")
    if report.get("date_start") or report.get("date_end"):
        filters.append(f"Periodo: {br_date(report.get('date_start')) or '-'} a {br_date(report.get('date_end')) or '-'}")
    if report.get("q"):
        filters.append(f"Busca: {report['q']}")
    filter_label = " | ".join(filters) if filters else "Todas as contribuicoes cadastradas"
    fill_rect(42, 716, 511, 86, "0.998 0.994 0.987")
    fill_rect(42, 792, 511, 10, "0.77 0.61 0.40")
    stroke_rect(42, 716, 511, 86, "0.86 0.80 0.70", 0.8)
    text_at(54, 772, "Contribuicoes por periodo", size=21, bold=True)
    text_at(54, 753, "Relatorio analitico de contribuicoes", size=9, bold=True)
    text_at(54, 738, f"Emitido em {br_date(date.today().isoformat())}", size=8, bold=False)
    text_at(54, 724, filter_label, size=7, bold=False)
    logo_panel(792)
    y = 702
    summary_boxes(summary)
    line(
        f"Legenda: SA membro ativo | SI inativo | NF frequentador | NV visitante | NM arquivo morto | NR sem vinculo",
        size=8,
        advance=13,
    )
    line(
        f"Resumo do rol: SA {summary.get('sa')} | SI {summary.get('si')} | NF {summary.get('nf')} | NV {summary.get('nv')} | NM {summary.get('nm')} | NR {summary.get('nr')}",
        size=8,
        advance=14,
    )
    rule()
    table_header()
    last_group = ""
    for item in report.get("items", []):
        group_label = str(item.get("group_label") or "")
        if group_label and group_label != last_group:
            last_group = group_label
            if y < 84:
                new_page()
                table_header()
            line(group_label.upper(), size=9, bold=True, advance=14)
        name_lines = _wrap(item.get("nome"), 34)[:2]
        if item.get("documento"):
            name_lines.append(f"Doc.: {item.get('documento')}")
        remessa_lines = _wrap(_compact_remittances(item.get("remessas") or []), 34)[:2]
        row_lines = max(len(name_lines), len(remessa_lines), 1)
        row_height = 10 + (row_lines * 11)
        if y - row_height < 48:
            new_page()
            table_header()
        start_y = y
        for index, value in enumerate(name_lines):
            text_at(48, start_y - (index * 11), value, size=8, bold=index == 0)
        for index, value in enumerate(remessa_lines):
            text_at(270, start_y - (index * 11), value, size=8)
        text_right(521, start_y, item.get("total_fmt"), size=8, bold=True)
        text_at(523, start_y, item.get("sigla"), size=8, bold=True)
        y -= row_height
        row_separator()
    if not report.get("items"):
        line("Nenhuma contribuicao encontrada para o filtro informado.", size=10)
    if current:
        pages.append(current)
    for index, page in enumerate(pages, start=1):
        page.append(f"0.40 0.44 0.50 rg BT /F1 8 Tf 1 0 0 1 500 24 Tm (Pagina {index}/{len(pages)}) Tj ET")
    return _build_pdf(pages)


def contribution_period_pdf_filename(report: dict[str, Any]) -> str:
    if report.get("competencia"):
        slug = normalize_query(report["competencia"]).lower().replace(" ", "_")
        return f"contribuicoes_por_periodo_{slug}.pdf"
    if report.get("date_start") or report.get("date_end"):
        start = str(report.get("date_start") or "inicio").replace("-", "")
        end = str(report.get("date_end") or "hoje").replace("-", "")
        return f"contribuicoes_por_periodo_{start}_{end}.pdf"
    if report.get("q"):
        slug = "".join(ch if ch.isalnum() else "_" for ch in normalize_query(report["q"]).lower()).strip("_")
        return f"contribuicoes_por_periodo_{slug or 'busca'}.pdf"
    return "contribuicoes_por_periodo.pdf"


def contribution_destination_pdf(report: dict[str, Any]) -> bytes:
    pages: list[list[str]] = []
    current: list[str] = []
    y = 800

    def new_page() -> None:
        nonlocal current, y
        if current:
            pages.append(current)
        current = ["0.12 0.16 0.22 rg"]
        y = 800

    def text_at(x: int, y_pos: int, value: object, size: int = 9, bold: bool = False) -> None:
        font = "F2" if bold else "F1"
        current.append(f"0.12 0.16 0.22 rg BT /{font} {size} Tf 1 0 0 1 {x} {y_pos} Tm ({_pdf_escape(value)}) Tj ET")

    def text_right(x_right: int, y_pos: int, value: object, size: int = 9, bold: bool = False) -> None:
        raw = str(value or "")
        factor = 0.56 if bold else 0.51
        width = max(len(raw) * size * factor, 0)
        x = max(42, x_right - int(round(width)))
        text_at(x, y_pos, raw, size=size, bold=bold)

    def text(x: int, value: object, size: int = 9, bold: bool = False) -> None:
        text_at(x, y, value, size=size, bold=bold)

    def line(value: object, size: int = 9, bold: bool = False, x: int = 42, advance: int = 14) -> None:
        nonlocal y
        if y < 54:
            new_page()
        text(x, value, size=size, bold=bold)
        y -= advance

    def rule() -> None:
        nonlocal y
        current.append(f"0.86 0.83 0.78 RG 0.8 w 42 {y} m 553 {y} l S")
        y -= 12

    def fill_rect(x: int, y_pos: int, width: int, height: int, color: str = "0.98 0.95 0.90") -> None:
        current.append(f"{color} rg {x} {y_pos} {width} {height} re f")

    def stroke_rect(x: int, y_pos: int, width: int, height: int, color: str = "0.86 0.80 0.70", line_width: float = 0.6) -> None:
        current.append(f"{color} RG {line_width:.2f} w {x} {y_pos} {width} {height} re S")

    def summary_box(
        top: int,
        label: str,
        value: object,
        x: int,
        *,
        width: int = 98,
        height: int = 34,
        value_size: int = 10,
        max_lines: int = 2,
    ) -> None:
        fill_rect(x, top - height, width, height, "0.995 0.985 0.965")
        fill_rect(x, top - 13, width, 13, "0.95 0.91 0.84")
        stroke_rect(x, top - height, width, height, "0.84 0.78 0.70", 0.7)
        text_at(x + 7, top - 9, label, size=6, bold=True)
        lines = _wrap(value, max(10, int((width - 16) / 5.6)))[:max_lines] or ["-"]
        for index, chunk in enumerate(lines):
            text_at(x + 7, top - 25 - (index * 10), chunk, size=value_size, bold=index == 0)

    def logo_panel(top: int, x: int = 366, width: int = 187, height: int = 84) -> None:
        fill_rect(x, top - height, width, height, "0.998 0.992 0.980")
        stroke_rect(x, top - height, width, height, "0.84 0.78 0.70", 0.8)
        if _brand_logo_dimensions():
            _append_brand_logo(current, x + 10, top - 8, width - 20, 66)
        else:
            text_at(x + 10, top - 36, "Espaco reservado", size=7, bold=False)
            text_at(x + 10, top - 52, "Logo do cliente", size=11, bold=True)

    def summary_boxes(summary: dict[str, Any]) -> None:
        nonlocal y
        boxes = [
            ("Total geral", summary.get("total_fmt")),
            ("Destinos", summary.get("destinos")),
            ("Contribuintes", summary.get("contribuintes")),
            ("Remessas", summary.get("remessas")),
            ("Fora do rol", summary.get("nr")),
        ]
        width = 98
        gap = 5
        top = y
        for index, (label, value) in enumerate(boxes):
            x = 42 + (index * (width + gap))
            summary_box(top, label, value, x, width=width, height=34, value_size=10, max_lines=2)
        y -= 46

    def table_header() -> None:
        nonlocal y
        if y < 90:
            new_page()
        fill_rect(42, y - 15, 511, 18, "0.95 0.91 0.84")
        text_at(48, y - 10, "Contribuinte", size=8, bold=True)
        text_at(282, y - 10, "Remessas", size=8, bold=True)
        text_at(462, y - 10, "Total", size=8, bold=True)
        text_at(522, y - 10, "Rol", size=8, bold=True)
        y -= 24

    def row_separator() -> None:
        current.append(f"0.90 0.86 0.80 RG 0.4 w 42 {y + 3} m 553 {y + 3} l S")

    new_page()
    summary = report.get("summary", {})
    filters = []
    if report.get("competencia"):
        filters.append(f"Competencia: {report['competencia']}")
    if report.get("date_start") or report.get("date_end"):
        filters.append(f"Periodo: {br_date(report.get('date_start')) or '-'} a {br_date(report.get('date_end')) or '-'}")
    if report.get("q"):
        filters.append(f"Busca: {report['q']}")
    if report.get("selected_destination_label"):
        filters.append(str(report["selected_destination_label"]))
    filter_label = " | ".join(filters) if filters else "Todas as destinacoes cadastradas"
    fill_rect(42, 716, 511, 86, "0.998 0.994 0.987")
    fill_rect(42, 792, 511, 10, "0.77 0.61 0.40")
    stroke_rect(42, 716, 511, 86, "0.86 0.80 0.70", 0.8)
    text_at(54, 772, "Relatorio por destino", size=21, bold=True)
    text_at(54, 753, "Distribuicao analitica das contribuicoes", size=9, bold=True)
    text_at(54, 738, f"Emitido em {br_date(date.today().isoformat())}", size=8, bold=False)
    text_at(54, 724, filter_label, size=7, bold=False)
    logo_panel(792)
    y = 702
    summary_boxes(summary)
    line(
        "Legenda: SA membro ativo | SI inativo | NF frequentador | NV visitante | NM arquivo morto | NR sem vinculo",
        size=8,
        advance=13,
    )
    line(
        f"Resumo do rol: SA {summary.get('sa')} | SI {summary.get('si')} | NF {summary.get('nf')} | NV {summary.get('nv')} | NM {summary.get('nm')} | NR {summary.get('nr')}",
        size=8,
        advance=14,
    )
    rule()
    for destination in report.get("destinations", []):
        if y < 118:
            new_page()
        fill_rect(42, y - 20, 511, 24, "0.98 0.95 0.90")
        stroke_rect(42, y - 20, 511, 24, "0.86 0.80 0.70", 0.6)
        text_at(48, y - 12, f"{destination.get('kind_label')}: {destination.get('label')}", size=10, bold=True)
        text_right(545, y - 12, f"{destination.get('total_fmt')} | {destination.get('remessas')} remessa(s)", size=8, bold=True)
        y -= 34
        table_header()
        for item in destination.get("items", []):
            name_lines = _wrap(item.get("nome"), 35)[:2]
            if item.get("documento"):
                name_lines.append(f"Doc.: {item.get('documento')}")
            remessa_lines = _wrap(_compact_remittances(item.get("remessas") or [], limit=3), 33)[:2]
            row_lines = max(len(name_lines), len(remessa_lines), 1)
            row_height = 10 + (row_lines * 11)
            if y - row_height < 48:
                new_page()
                table_header()
            start_y = y
            for index, value in enumerate(name_lines):
                text_at(48, start_y - (index * 11), value, size=8, bold=index == 0)
            for index, value in enumerate(remessa_lines):
                text_at(282, start_y - (index * 11), value, size=8)
            text_right(525, start_y, item.get("total_fmt"), size=8, bold=True)
            text_at(525, start_y, item.get("sigla"), size=8, bold=True)
            y -= row_height
            row_separator()
        rule()
    if not report.get("destinations"):
        line("Nenhuma contribuicao encontrada para o filtro informado.", size=10)
    if current:
        pages.append(current)
    for index, page in enumerate(pages, start=1):
        page.append(f"0.40 0.44 0.50 rg BT /F1 8 Tf 1 0 0 1 500 24 Tm (Pagina {index}/{len(pages)}) Tj ET")
    return _build_pdf(pages)


def contribution_destination_pdf_filename(report: dict[str, Any]) -> str:
    if report.get("selected_destination_label"):
        slug = "".join(
            ch if ch.isalnum() else "_"
            for ch in normalize_query(report["selected_destination_label"]).lower()
        ).strip("_")
        suffix = slug or "destino"
    elif report.get("competencia"):
        suffix = normalize_query(report["competencia"]).lower().replace(" ", "_")
    elif report.get("date_start") or report.get("date_end"):
        start = str(report.get("date_start") or "inicio").replace("-", "")
        end = str(report.get("date_end") or "hoje").replace("-", "")
        suffix = f"{start}_{end}"
    else:
        suffix = "todos"
    return f"contribuicoes_por_destino_{suffix}.pdf"


def person_statement_pdf(statement: dict[str, Any]) -> bytes:
    pages: list[list[str]] = []
    current: list[str] = []
    y = 800

    def new_page() -> None:
        nonlocal current, y
        if current:
            pages.append(current)
        current = ["0.12 0.16 0.22 rg"]
        y = 800

    def text_at(x: int, y_pos: int, value: object, size: int = 9, bold: bool = False) -> None:
        font = "F2" if bold else "F1"
        current.append(f"0.12 0.16 0.22 rg BT /{font} {size} Tf 1 0 0 1 {x} {y_pos} Tm ({_pdf_escape(value)}) Tj ET")

    def text_right(x_right: int, y_pos: int, value: object, size: int = 9, bold: bool = False) -> None:
        raw = str(value or "")
        factor = 0.56 if bold else 0.51
        width = max(len(raw) * size * factor, 0)
        x = max(42, x_right - int(round(width)))
        text_at(x, y_pos, raw, size=size, bold=bold)

    def text(x: int, value: object, size: int = 9, bold: bool = False) -> None:
        text_at(x, y, value, size=size, bold=bold)

    def line(value: object, size: int = 9, bold: bool = False, x: int = 42, advance: int = 14) -> None:
        nonlocal y
        if y < 54:
            new_page()
        text(x, value, size=size, bold=bold)
        y -= advance

    def rule() -> None:
        nonlocal y
        current.append(f"0.86 0.83 0.78 RG 0.8 w 42 {y} m 553 {y} l S")
        y -= 12

    def fill_rect(x: int, y_pos: int, width: int, height: int, color: str = "0.98 0.95 0.90") -> None:
        current.append(f"{color} rg {x} {y_pos} {width} {height} re f")

    def stroke_rect(x: int, y_pos: int, width: int, height: int, color: str = "0.86 0.80 0.70", line_width: float = 0.6) -> None:
        current.append(f"{color} RG {line_width:.2f} w {x} {y_pos} {width} {height} re S")

    def summary_box(
        top: int,
        label: str,
        value: object,
        x: int,
        *,
        width: int = 120,
        height: int = 36,
        value_size: int = 10,
        max_lines: int = 2,
    ) -> None:
        fill_rect(x, top - height, width, height, "0.995 0.985 0.965")
        fill_rect(x, top - 13, width, 13, "0.95 0.91 0.84")
        stroke_rect(x, top - height, width, height, "0.84 0.78 0.70", 0.7)
        text_at(x + 8, top - 9, label, size=6, bold=True)
        lines = _wrap(value, max(12, int((width - 18) / 5.8)))[:max_lines] or ["-"]
        for index, chunk in enumerate(lines):
            text_at(x + 8, top - 25 - (index * 10), chunk, size=value_size, bold=index == 0)

    def logo_panel(top: int, x: int = 366, width: int = 187, height: int = 84) -> None:
        fill_rect(x, top - height, width, height, "0.998 0.992 0.980")
        stroke_rect(x, top - height, width, height, "0.84 0.78 0.70", 0.8)
        if _brand_logo_dimensions():
            _append_brand_logo(current, x + 10, top - 8, width - 20, 66)
        else:
            text_at(x + 10, top - 36, "Espaco reservado", size=7, bold=False)
            text_at(x + 10, top - 52, "Logo do cliente", size=11, bold=True)

    def summary_boxes(summary: dict[str, Any]) -> None:
        nonlocal y
        boxes = [
            ("Total geral", summary.get("total_fmt")),
            ("Lancamentos", summary.get("lancamentos")),
            ("Competencias", summary.get("competencias")),
        ]
        width = 120
        gap = 10
        top = y
        for index, (label, value) in enumerate(boxes):
            x = 42 + (index * (width + gap))
            summary_box(top, label, value, x, width=width, height=36, value_size=10, max_lines=2)
        y -= 46

    def table_header() -> None:
        nonlocal y
        if y < 90:
            new_page()
        fill_rect(42, y - 15, 511, 18, "0.95 0.91 0.84")
        text_at(48, y - 10, "Data", size=8, bold=True)
        text_at(102, y - 10, "Competencia", size=8, bold=True)
        text_at(182, y - 10, "Tipo", size=8, bold=True)
        text_at(258, y - 10, "Forma", size=8, bold=True)
        text_at(324, y - 10, "Historico / observacoes", size=8, bold=True)
        text_at(506, y - 10, "Valor", size=8, bold=True)
        y -= 24

    def row_separator() -> None:
        current.append(f"0.90 0.86 0.80 RG 0.4 w 42 {y + 3} m 553 {y + 3} l S")

    def wrapped_entry_lines(entry: dict[str, Any]) -> list[str]:
        observation = normalize_query(entry.get("observacoes") or "-")
        return _wrap(observation, 34) or ["-"]

    person = statement.get("person") or {}
    summary = statement.get("summary") or {}
    filters = statement.get("filters") or {}
    filter_chunks: list[str] = []
    if filters.get("year"):
        filter_chunks.append(f"Ano: {filters['year']}")
    if filters.get("competencia"):
        filter_chunks.append(f"Competencia: {filters['competencia']}")
    if filters.get("date_start") or filters.get("date_end"):
        filter_chunks.append(f"Periodo: {br_date(filters.get('date_start')) or '-'} a {br_date(filters.get('date_end')) or '-'}")
    filter_label = " | ".join(filter_chunks) if filter_chunks else "Todos os lancamentos da pessoa"

    new_page()
    fill_rect(42, 716, 511, 86, "0.998 0.994 0.987")
    fill_rect(42, 792, 511, 10, "0.77 0.61 0.40")
    stroke_rect(42, 716, 511, 86, "0.86 0.80 0.70", 0.8)
    text_at(54, 772, "Extrato de contribuicoes", size=21, bold=True)
    text_at(54, 753, person.get("nome") or "Pessoa", size=9, bold=True)
    text_at(54, 738, f"Emitido em {br_date(date.today().isoformat())}", size=8, bold=False)
    text_at(54, 724, filter_label, size=7, bold=False)
    logo_panel(792)
    y = 702
    line(f"Pessoa: {person.get('nome') or ''}", size=11, bold=True, advance=16)
    line(
        f"Ficha {person.get('codigo') or 'sem codigo'} | CPF {person.get('cpf') or 'nao informado'} | {person.get('status') or ''}",
        size=9,
        advance=14,
    )
    if person.get("email"):
        line(f"E-mail: {person.get('email')}", size=9, advance=14)
    rule()
    summary_boxes(summary)
    table_header()
    for entry in statement.get("entries", []):
        if entry.get("kind") == "subtotal":
            if y < 70:
                new_page()
                table_header()
            fill_rect(42, y - 12, 511, 16, "0.97 0.96 0.93")
            text_at(48, y - 8, f"Subtotal {entry.get('competencia') or 'Sem competencia'}", size=8, bold=True)
            text_at(470, y - 8, entry.get("subtotal_fmt"), size=8, bold=True)
            y -= 20
            row_separator()
            continue
        notes = wrapped_entry_lines(entry)
        type_lines = _wrap(entry.get("tipo"), 12) or ["-"]
        form_lines = _wrap(entry.get("forma"), 10) or ["-"]
        row_lines = max(1, len(notes), len(type_lines), len(form_lines))
        row_height = 10 + (row_lines * 11)
        if y - row_height < 48:
            new_page()
            table_header()
        start_y = y
        text_at(48, start_y, entry.get("data"), size=8, bold=True)
        text_at(102, start_y, entry.get("competencia") or "-", size=8)
        for index, value in enumerate(type_lines):
            text_at(182, start_y - (index * 11), value, size=8, bold=index == 0)
        for index, value in enumerate(form_lines):
            text_at(258, start_y - (index * 11), value or "-", size=8)
        for index, value in enumerate(notes):
            text_at(324, start_y - (index * 11), value, size=8)
        text_right(541, start_y, entry.get("valor_fmt"), size=8, bold=True)
        y -= row_height
        row_separator()
    if not statement.get("entries"):
        line("Nenhum lancamento encontrado para os filtros informados.", size=10)
    if current:
        pages.append(current)
    for index, page in enumerate(pages, start=1):
        page.append(f"0.40 0.44 0.50 rg BT /F1 8 Tf 1 0 0 1 500 24 Tm (Pagina {index}/{len(pages)}) Tj ET")
    return _build_pdf(pages)


def person_statement_pdf_filename(statement: dict[str, Any]) -> str:
    person = statement.get("person") or {}
    filters = statement.get("filters") or {}
    person_slug = "".join(ch if ch.isalnum() else "_" for ch in normalize_query(person.get("nome")).lower()).strip("_") or "pessoa"
    if filters.get("competencia"):
        competence = "".join(ch if ch.isalnum() else "_" for ch in normalize_query(filters.get("competencia")).lower()).strip("_")
        return f"extrato_{person_slug}_{competence or 'competencia'}.pdf"
    if filters.get("date_start") or filters.get("date_end"):
        start = str(filters.get("date_start") or "inicio").replace("-", "")
        end = str(filters.get("date_end") or "hoje").replace("-", "")
        return f"extrato_{person_slug}_{start}_{end}.pdf"
    return f"extrato_{person_slug}.pdf"


def receipt_pdf(detail: dict[str, Any]) -> bytes:
    receipt = detail.get("receipt") or {}
    person = detail.get("person") or {}
    items = list(detail.get("items") or [])
    pages: list[list[str]] = []
    current: list[str] = []
    y = 800

    def new_page() -> None:
        nonlocal current, y
        if current:
            pages.append(current)
        current = ["0.12 0.16 0.22 rg"]
        y = 800

    def text_at(x: int, y_pos: int, value: object, size: int = 9, bold: bool = False) -> None:
        font = "F2" if bold else "F1"
        current.append(f"0.12 0.16 0.22 rg BT /{font} {size} Tf 1 0 0 1 {x} {y_pos} Tm ({_pdf_escape(value)}) Tj ET")

    def text_right(x_right: int, y_pos: int, value: object, size: int = 9, bold: bool = False) -> None:
        raw = str(value or "")
        factor = 0.56 if bold else 0.51
        width = max(len(raw) * size * factor, 0)
        x = max(42, x_right - int(round(width)))
        text_at(x, y_pos, raw, size=size, bold=bold)

    def line(value: object, size: int = 9, bold: bool = False, x: int = 42, advance: int = 14) -> None:
        nonlocal y
        if y < 54:
            new_page()
        text_at(x, y, value, size=size, bold=bold)
        y -= advance

    def rule() -> None:
        nonlocal y
        current.append(f"0.86 0.83 0.78 RG 0.8 w 42 {y} m 553 {y} l S")
        y -= 12

    def fill_rect(x: int, y_pos: int, width: int, height: int, color: str = "0.98 0.95 0.90") -> None:
        current.append(f"{color} rg {x} {y_pos} {width} {height} re f")

    def stroke_rect(x: int, y_pos: int, width: int, height: int, color: str = "0.86 0.80 0.70", line_width: float = 0.6) -> None:
        current.append(f"{color} RG {line_width:.2f} w {x} {y_pos} {width} {height} re S")

    def summary_box(
        top: int,
        label: str,
        value: object,
        x: int,
        *,
        width: int = 160,
        height: int = 34,
        value_size: int = 10,
        max_lines: int = 2,
    ) -> None:
        fill_rect(x, top - height, width, height, "0.995 0.985 0.965")
        fill_rect(x, top - 13, width, 13, "0.95 0.91 0.84")
        stroke_rect(x, top - height, width, height, "0.84 0.78 0.70", 0.7)
        text_at(x + 8, top - 9, label, size=6, bold=True)
        wrap_width = max(12, int((width - 18) / 5.8))
        lines = _wrap(value, wrap_width)[:max_lines] or ["-"]
        line_y = top - 25
        for index, chunk in enumerate(lines):
            text_at(x + 8, line_y - (index * 10), chunk, size=value_size, bold=index == 0)

    def logo_panel(top: int, x: int = 366, width: int = 187, height: int = 84) -> None:
        fill_rect(x, top - height, width, height, "0.998 0.992 0.980")
        stroke_rect(x, top - height, width, height, "0.84 0.78 0.70", 0.8)
        dimensions = _brand_logo_dimensions()
        if dimensions:
            raw_width, raw_height = dimensions
            scale = min((width - 18) / float(raw_width), 66 / float(raw_height))
            draw_width = raw_width * scale
            draw_height = raw_height * scale
            logo_x = x + int(round((width - draw_width) / 2))
            logo_top = top - 8
            _append_brand_logo(current, logo_x, logo_top, width - 18, 66)
        else:
            text_at(x + 10, top - 36, "Espaco reservado", size=7, bold=False)
            text_at(x + 10, top - 52, "Logo do cliente", size=11, bold=True)

    new_page()
    fill_rect(42, 716, 511, 86, "0.998 0.994 0.987")
    fill_rect(42, 792, 511, 10, "0.77 0.61 0.40")
    stroke_rect(42, 716, 511, 86, "0.86 0.80 0.70", 0.8)
    text_at(54, 772, "Recibo de contribuicoes", size=21, bold=True)
    text_at(54, 753, f"Documento {receipt.get('numero') or '-'}", size=9, bold=True)
    text_at(
        54,
        738,
        f"Emitido em {receipt.get('data') or br_date(date.today().isoformat())}",
        size=8,
        bold=False,
    )
    text_at(
        54,
        724,
        "Comprovante formal das contribuicoes registradas neste periodo.",
        size=7,
        bold=False,
    )
    logo_panel(792)
    summary_box(706, "Contribuinte", person.get("nome") or receipt.get("person_name") or "-", 42, width=232, height=42, value_size=10, max_lines=2)
    summary_box(706, "Periodo", f"{receipt.get('periodo_inicio') or '-'} a {receipt.get('periodo_fim') or '-'}", 286, width=138, height=42, value_size=8, max_lines=2)
    summary_box(706, "Valor total", receipt.get("valor_fmt") or "-", 436, width=117, height=42, value_size=13, max_lines=1)
    y = 646
    line(
        f"Codigo: {person.get('codigo') or receipt.get('person_code') or 'sem codigo'} | CPF: {person.get('cpf') or receipt.get('person_cpf') or 'nao informado'}",
        size=9,
        advance=14,
    )
    if person.get("email") or receipt.get("person_email"):
        line(f"E-mail: {person.get('email') or receipt.get('person_email')}", size=9, advance=14)
    if receipt.get("observacoes"):
        line("Observacoes do recibo:", size=9, bold=True, advance=14)
        for chunk in _wrap(receipt.get("observacoes"), 90):
            line(chunk, size=8, advance=12)
    rule()
    line(
        f"Recebemos de {person.get('nome') or receipt.get('person_name') or 'Contribuinte'} as contribuicoes abaixo relacionadas.",
        size=9,
        advance=16,
    )

    def table_header() -> None:
        nonlocal y
        if y < 90:
            new_page()
        fill_rect(42, y - 15, 511, 18, "0.95 0.91 0.84")
        text_at(48, y - 10, "Data", size=8, bold=True)
        text_at(106, y - 10, "Competencia", size=8, bold=True)
        text_at(196, y - 10, "Tipo", size=8, bold=True)
        text_at(356, y - 10, "Forma", size=8, bold=True)
        text_right(541, y - 10, "Valor", size=8, bold=True)
        y -= 24

    def row_separator() -> None:
        current.append(f"0.90 0.86 0.80 RG 0.4 w 42 {y + 3} m 553 {y + 3} l S")

    table_header()
    for item in items:
        type_lines = _wrap(item.get("tipo"), 28)[:2]
        form_lines = _wrap(item.get("forma") or "-", 16)[:2]
        row_lines = max(len(type_lines), len(form_lines), 1)
        row_height = 10 + (row_lines * 11)
        if y - row_height < 48:
            new_page()
            table_header()
        start_y = y
        text_at(48, start_y, item.get("data") or "-", size=8, bold=True)
        text_at(106, start_y, item.get("competencia") or "-", size=8)
        for index, value in enumerate(type_lines):
            text_at(196, start_y - (index * 11), value, size=8, bold=index == 0)
        for index, value in enumerate(form_lines):
            text_at(356, start_y - (index * 11), value, size=8)
        text_right(541, start_y, item.get("valor_fmt") or "-", size=8, bold=True)
        y -= row_height
        if item.get("observacoes"):
            for chunk in _wrap(item.get("observacoes"), 78)[:2]:
                text_at(196, y + 2, f"Obs: {chunk}", size=7)
                y -= 10
        row_separator()
    if not items:
        line("Recibo sem itens vinculados.", size=10)
    if current:
        pages.append(current)
    for index, page in enumerate(pages, start=1):
        page.append(f"0.40 0.44 0.50 rg BT /F1 8 Tf 1 0 0 1 500 24 Tm (Pagina {index}/{len(pages)}) Tj ET")
    return _build_pdf(pages)


def receipt_pdf_filename(detail: dict[str, Any]) -> str:
    receipt = detail.get("receipt") or {}
    number = normalize_query(receipt.get("numero")).lower().replace(" ", "_").replace("/", "_")
    person = normalize_query(receipt.get("person_name")).lower()
    person_slug = "".join(ch if ch.isalnum() else "_" for ch in person).strip("_") or "contribuinte"
    if number:
        return f"recibo_{number}.pdf"
    return f"recibo_{person_slug}.pdf"

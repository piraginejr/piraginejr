from __future__ import annotations

from datetime import date
from typing import Any

from power_church_core.formatting import br_date
from power_church_core.normalization import normalize_query


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


def _build_pdf(pages: list[list[str]]) -> bytes:
    page_payloads = ["\n".join(page_ops).encode("latin-1", errors="replace") for page_ops in pages]
    kids: list[str] = []
    objects: list[bytes] = [b"" for _ in range(5 + (len(page_payloads) * 2))]
    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[3] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    objects[4] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>"
    for index, content in enumerate(page_payloads):
        content_id = 5 + (index * 2)
        page_id = content_id + 1
        kids.append(f"{page_id} 0 R")
        objects[content_id] = b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream"
        objects[page_id] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents {content_id} 0 R >>".encode("ascii")
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
            fill_rect(x, top - 34, width, 34)
            current.append(f"0.86 0.80 0.70 RG 0.6 w {x} {top - 34} {width} 34 re S")
            text_at(x + 7, top - 14, label, size=7, bold=True)
            text_at(x + 7, top - 28, value, size=10, bold=True)
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
    line("Contribuicoes por periodo", size=18, bold=True, advance=22)
    line(f"Emitido em {br_date(date.today().isoformat())} | {filter_label}", size=9, advance=18)
    rule()
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
        text_at(456, start_y, item.get("total_fmt"), size=8, bold=True)
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
            fill_rect(x, top - 34, width, 34)
            current.append(f"0.86 0.80 0.70 RG 0.6 w {x} {top - 34} {width} 34 re S")
            text_at(x + 7, top - 14, label, size=7, bold=True)
            text_at(x + 7, top - 28, value, size=10, bold=True)
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
    line("Relatorio por destino", size=18, bold=True, advance=22)
    line(f"Emitido em {br_date(date.today().isoformat())} | {filter_label}", size=9, advance=18)
    rule()
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
        current.append(f"0.86 0.80 0.70 RG 0.6 w 42 {y - 20} 511 24 re S")
        text_at(48, y - 12, f"{destination.get('kind_label')}: {destination.get('label')}", size=10, bold=True)
        text_at(390, y - 12, f"{destination.get('total_fmt')} | {destination.get('remessas')} remessa(s)", size=8, bold=True)
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
            text_at(462, start_y, item.get("total_fmt"), size=8, bold=True)
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

    def summary_box(top: int, label: str, value: object, x: int, width: int = 160) -> None:
        fill_rect(x, top - 34, width, 34)
        current.append(f"0.86 0.80 0.70 RG 0.6 w {x} {top - 34} {width} 34 re S")
        text_at(x + 7, top - 14, label, size=7, bold=True)
        text_at(x + 7, top - 28, value, size=10, bold=True)

    new_page()
    line(f"Recibo de contribuicoes {receipt.get('numero') or ''}".strip(), size=18, bold=True, advance=22)
    line(
        f"Emitido em {receipt.get('data') or br_date(date.today().isoformat())} | Organizacao: {receipt.get('organizacao') or 'Power Church'}",
        size=9,
        advance=18,
    )
    rule()
    fill_rect(408, y - 58, 145, 58, "0.99 0.97 0.93")
    current.append(f"0.82 0.77 0.70 RG 0.8 w 408 {y - 58} 145 58 re S")
    text_at(418, y - 18, "Espaco reservado", size=7, bold=False)
    text_at(418, y - 34, "Logo do cliente", size=11, bold=True)
    text_at(418, y - 48, "Pode receber marca no futuro", size=7, bold=False)
    top = y
    summary_box(top, "Contribuinte", person.get("nome") or receipt.get("person_name") or "-", 42, width=220)
    summary_box(top, "Periodo", f"{receipt.get('periodo_inicio') or '-'} a {receipt.get('periodo_fim') or '-'}", 270, width=180)
    summary_box(top - 64, "Valor total", receipt.get("valor_fmt") or "-", 408, width=145)
    y -= 108
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
        text_at(462, y - 10, "Valor", size=8, bold=True)
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
        text_at(48, start_y, item.get("data") or "-", size=8)
        text_at(106, start_y, item.get("competencia") or "-", size=8)
        for index, value in enumerate(type_lines):
            text_at(196, start_y - (index * 11), value, size=8, bold=index == 0)
        for index, value in enumerate(form_lines):
            text_at(356, start_y - (index * 11), value, size=8)
        text_at(462, start_y, item.get("valor_fmt") or "-", size=8, bold=True)
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

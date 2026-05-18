from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

from power_church_core.normalization import normalize_match_name, normalize_query
from power_church_demo import (
    PowerChurchDB,
    parse_sicoob_pix_pdf,
    parse_statement_pdf_by_layout,
    statement_should_skip_entry,
)

from power_church_django.services.legacy import legacy_db_path


class LegacyBankWriteError(RuntimeError):
    """Raised when Django delegates a bank import write to the legacy engine."""


PDF_PROVIDER_MODES = [
    {
        "value": "swift_pdfkit",
        "label": "Homologado atual (Swift/PDFKit)",
        "description": "Mantem o leitor atual do Mac enquanto validamos a portabilidade.",
    },
    {
        "value": "compare_pymupdf",
        "label": "Comparar Swift x PyMuPDF antes de gravar",
        "description": "Chave segura: se houver divergencia, o lote nao e gravado.",
    },
    {
        "value": "pymupdf",
        "label": "PyMuPDF direto",
        "description": "Leitor portavel para Linux/servidor, apos homologacao do banco.",
    },
]


def _normalize_pdf_provider_mode(value: object) -> str:
    mode = normalize_query(value).lower() or "swift_pdfkit"
    allowed = {item["value"] for item in PDF_PROVIDER_MODES}
    return mode if mode in allowed else "swift_pdfkit"


def _provider_for_mode(mode: str) -> str:
    return "pymupdf" if mode in {"compare_pymupdf", "pymupdf"} else "swift_pdfkit"


@contextmanager
def _temporary_pdf_provider(provider: str) -> Iterator[None]:
    previous = os.environ.get("POWER_CHURCH_PDF_PROVIDER")
    os.environ["POWER_CHURCH_PDF_PROVIDER"] = provider
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("POWER_CHURCH_PDF_PROVIDER", None)
        else:
            os.environ["POWER_CHURCH_PDF_PROVIDER"] = previous


def _amount_cents(value: object) -> int:
    try:
        return int(round(float(value or 0) * 100))
    except (TypeError, ValueError):
        return 0


def _money_from_cents(cents: int) -> str:
    return f"R$ {cents / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _entry_date(entry: Mapping[str, object]) -> str:
    return normalize_query(entry.get("received_on") or entry.get("date") or entry.get("data"))


def _pix_entry_digest(entry: Mapping[str, object]) -> dict[str, object]:
    donor_name = normalize_query(entry.get("donor_name") or entry.get("source_name"))
    document = normalize_query(entry.get("document_mask") or entry.get("bank_document"))
    return {
        "order": int(entry.get("order_in_file") or 0),
        "page": int(entry.get("page_number") or 0),
        "date": _entry_date(entry),
        "amount_cents": _amount_cents(entry.get("amount")),
        "name": donor_name,
        "name_norm": normalize_match_name(donor_name),
        "document": document,
        "document_type": normalize_query(entry.get("document_type")).lower(),
        "cent_code": normalize_query(entry.get("cent_code")),
    }


def _statement_entry_digest(entry: Mapping[str, object]) -> dict[str, object]:
    source_name = normalize_query(entry.get("source_name"))
    return {
        "order": int(entry.get("order_in_file") or 0),
        "page": int(entry.get("page_number") or 0),
        "date": _entry_date(entry),
        "amount_cents": _amount_cents(entry.get("amount")),
        "name": source_name,
        "name_norm": normalize_match_name(source_name),
        "document": normalize_query(entry.get("bank_document")),
        "document_type": normalize_query(entry.get("document_type")).lower(),
        "movement_kind": normalize_query(entry.get("movement_kind")),
        "prefix": normalize_query(entry.get("prefix")),
        "receiving_code": normalize_query(entry.get("receiving_code")),
        "origin_label": normalize_query(entry.get("origin_label")),
    }


def _parsed_summary(
    parsed: Mapping[str, object],
    *,
    import_kind: str,
    requested_layout_code: str = "",
) -> dict[str, object]:
    layout_code = normalize_query(parsed.get("layout_code") or requested_layout_code).upper()
    raw_entries = [entry for entry in parsed.get("entries", []) if isinstance(entry, Mapping)]
    if import_kind == "statement":
        entries = [entry for entry in raw_entries if not statement_should_skip_entry(layout_code, entry)]
        digests = [_statement_entry_digest(entry) for entry in entries]
    else:
        entries = raw_entries
        digests = [_pix_entry_digest(entry) for entry in entries]
    return {
        "bank_name": normalize_query(parsed.get("bank_name")),
        "layout_code": layout_code,
        "period_start": normalize_query(parsed.get("period_start")),
        "period_end": normalize_query(parsed.get("period_end")),
        "count": len(digests),
        "total_cents": sum(int(item["amount_cents"]) for item in digests),
        "entries": digests,
    }


def _summary_label(summary: Mapping[str, object]) -> str:
    return (
        f"{summary.get('count', 0)} movimento(s), "
        f"{_money_from_cents(int(summary.get('total_cents') or 0))}, "
        f"periodo {summary.get('period_start') or '-'} a {summary.get('period_end') or '-'}"
    )


def _first_summary_difference(left: Mapping[str, object], right: Mapping[str, object]) -> str:
    for key, label in [
        ("period_start", "periodo inicial"),
        ("period_end", "periodo final"),
        ("count", "quantidade"),
        ("total_cents", "total"),
    ]:
        if left.get(key) != right.get(key):
            return f"{label}: atual={left.get(key)!r}, pymupdf={right.get(key)!r}"
    left_entries = list(left.get("entries") or [])
    right_entries = list(right.get("entries") or [])
    for index, (left_entry, right_entry) in enumerate(zip(left_entries, right_entries), start=1):
        if left_entry != right_entry:
            return f"movimento {index}: atual={left_entry!r}, pymupdf={right_entry!r}"
    if len(left_entries) != len(right_entries):
        return f"listas divergentes: atual={len(left_entries)}, pymupdf={len(right_entries)}"
    return "sem diferenca detalhada identificada"


def _parse_upload_with_provider(
    provider: str,
    *,
    filename: str,
    payload: bytes,
    import_kind: str,
    layout_code: str = "",
) -> dict[str, object]:
    suffix = Path(filename).suffix or ".pdf"
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix="power_church_pdf_", suffix=suffix, delete=False) as handle:
            handle.write(payload)
            tmp_path = Path(handle.name)
        with _temporary_pdf_provider(provider):
            if import_kind == "pix_sicoob":
                return parse_sicoob_pix_pdf(tmp_path)
            return parse_statement_pdf_by_layout(layout_code, tmp_path)
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass


def compare_pdf_upload_providers(
    filename: str,
    payload: bytes,
    *,
    import_kind: str,
    layout_code: str = "",
) -> dict[str, object]:
    """Compare the current Mac reader with PyMuPDF without writing to the database."""
    if not payload:
        raise LegacyBankWriteError("Selecione um PDF antes de comparar os leitores.")
    try:
        current = _parse_upload_with_provider(
            "swift_pdfkit",
            filename=filename,
            payload=payload,
            import_kind=import_kind,
            layout_code=layout_code,
        )
        portable = _parse_upload_with_provider(
            "pymupdf",
            filename=filename,
            payload=payload,
            import_kind=import_kind,
            layout_code=layout_code,
        )
    except Exception as exc:
        raise LegacyBankWriteError(f"Falha ao comparar leitores PDF: {exc}") from exc
    current_summary = _parsed_summary(current, import_kind=import_kind, requested_layout_code=layout_code)
    portable_summary = _parsed_summary(portable, import_kind=import_kind, requested_layout_code=layout_code)
    ok = current_summary == portable_summary
    return {
        "ok": ok,
        "current": current_summary,
        "portable": portable_summary,
        "difference": "" if ok else _first_summary_difference(current_summary, portable_summary),
    }


def _assert_pdf_provider_comparison(
    filename: str,
    payload: bytes,
    *,
    import_kind: str,
    layout_code: str = "",
) -> str:
    comparison = compare_pdf_upload_providers(
        filename,
        payload,
        import_kind=import_kind,
        layout_code=layout_code,
    )
    if not comparison["ok"]:
        raise LegacyBankWriteError(
            "Comparacao Swift/PyMuPDF divergente. "
            f"Atual: {_summary_label(comparison['current'])}. "
            f"PyMuPDF: {_summary_label(comparison['portable'])}. "
            f"Primeiro desvio: {comparison['difference']}. "
            "O lote nao foi gravado."
        )
    return (
        "Comparacao Swift/PyMuPDF aprovada: "
        f"{_summary_label(comparison['portable'])}."
    )


def _append_lot_note(db: PowerChurchDB, table_name: str, lot_id: int, note: str) -> None:
    if table_name not in {"pix_lotes", "extrato_lotes"}:
        raise ValueError("Tabela de lote invalida.")
    row = db.conn.execute(
        f"SELECT observacoes FROM {table_name} WHERE id = ? LIMIT 1",
        (lot_id,),
    ).fetchone()
    if row is None:
        return
    current = normalize_query(row["observacoes"])
    merged = "\n".join(part for part in [current, note] if part)
    db.conn.execute(
        f"UPDATE {table_name} SET observacoes = ?, atualizado_em = CURRENT_TIMESTAMP WHERE id = ?",
        (merged, lot_id),
    )
    db.conn.commit()


def _provider_note(provider: str, mode: str, comparison_note: str = "") -> str:
    lines = [f"Motor PDF usado na importacao Django: {provider}."]
    if mode == "compare_pymupdf":
        lines.append(comparison_note or "Comparacao Swift/PyMuPDF aprovada antes da gravacao.")
    if provider == "pymupdf":
        lines.append("Leitura portavel preparada para Linux/servidor.")
    return " ".join(lines)


def create_statement_lot_from_upload(
    filename: str,
    payload: bytes,
    layout_code: str,
    *,
    pdf_provider_mode: str = "swift_pdfkit",
) -> int:
    mode = _normalize_pdf_provider_mode(pdf_provider_mode)
    provider = _provider_for_mode(mode)
    comparison_note = ""
    if mode == "compare_pymupdf":
        comparison_note = _assert_pdf_provider_comparison(
            filename,
            payload,
            import_kind="statement",
            layout_code=layout_code,
        )
    db = PowerChurchDB(legacy_db_path())
    try:
        with _temporary_pdf_provider(provider):
            lot_id = db.create_statement_lot_from_upload(filename, payload, layout_code=layout_code)
        _append_lot_note(db, "extrato_lotes", lot_id, _provider_note(provider, mode, comparison_note))
        return lot_id
    except Exception as exc:
        raise LegacyBankWriteError(str(exc)) from exc
    finally:
        db.close()


def create_pix_lot_from_upload(
    filename: str,
    payload: bytes,
    *,
    pdf_provider_mode: str = "swift_pdfkit",
) -> int:
    mode = _normalize_pdf_provider_mode(pdf_provider_mode)
    provider = _provider_for_mode(mode)
    comparison_note = ""
    if mode == "compare_pymupdf":
        comparison_note = _assert_pdf_provider_comparison(
            filename,
            payload,
            import_kind="pix_sicoob",
        )
    db = PowerChurchDB(legacy_db_path())
    try:
        with _temporary_pdf_provider(provider):
            lot_id = db.create_pix_lot_from_upload(filename, payload)
        _append_lot_note(db, "pix_lotes", lot_id, _provider_note(provider, mode, comparison_note))
        return lot_id
    except Exception as exc:
        raise LegacyBankWriteError(str(exc)) from exc
    finally:
        db.close()


def save_cent_rule_from_form(form: Any) -> int:
    db = PowerChurchDB(legacy_db_path())
    try:
        return db.save_pix_rule_from_form(_form_lists(form))
    except Exception as exc:
        raise LegacyBankWriteError(str(exc)) from exc
    finally:
        db.close()


def _form_lists(data: Any) -> dict[str, list[str]]:
    """Convert Django QueryDict/plain mappings into the legacy form contract."""
    if hasattr(data, "lists"):
        return {str(key): [str(item) for item in values] for key, values in data.lists()}
    normalized: dict[str, list[str]] = {}
    for key, value in dict(data or {}).items():
        if isinstance(value, (list, tuple)):
            normalized[str(key)] = [str(item) for item in value]
        else:
            normalized[str(key)] = [str(value)]
    return normalized


def update_bank_movement_from_form(kind: str, movement_id: int, form: Any) -> int:
    db = PowerChurchDB(legacy_db_path())
    try:
        payload = _form_lists(form)
        if kind == "pix":
            return db.update_pix_movement_from_form(movement_id, payload)
        return db.update_statement_movement_from_form(movement_id, payload)
    except Exception as exc:
        raise LegacyBankWriteError(str(exc)) from exc
    finally:
        db.close()


def reprocess_bank_lot(kind: str, lot_id: int) -> int:
    db = PowerChurchDB(legacy_db_path())
    try:
        if kind == "pix":
            return db.reprocess_pix_lot(lot_id)
        return db.reprocess_statement_lot(lot_id)
    except Exception as exc:
        raise LegacyBankWriteError(str(exc)) from exc
    finally:
        db.close()


def import_ready_pix_lot(lot_id: int) -> int:
    db = PowerChurchDB(legacy_db_path())
    try:
        return db.import_ready_pix_lot(lot_id)
    except Exception as exc:
        raise LegacyBankWriteError(str(exc)) from exc
    finally:
        db.close()


def close_bank_lot(kind: str, lot_id: int) -> dict[str, int]:
    db = PowerChurchDB(legacy_db_path())
    try:
        if kind == "pix":
            return db.close_pix_lot(lot_id)
        return db.close_statement_lot(lot_id)
    except Exception as exc:
        raise LegacyBankWriteError(str(exc)) from exc
    finally:
        db.close()

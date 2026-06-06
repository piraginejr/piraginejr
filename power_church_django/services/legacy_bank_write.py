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

STATEMENT_LOT_CLOSE_NOTE = (
    "Lote encerrado. O que restou sem pessoa vinculada foi preservado para associacao futura na central de contribuintes."
)
STATEMENT_MOVEMENT_CLOSE_NOTE = (
    "Lote encerrado pelo operador. O credito foi preservado no contribuinte auxiliar e segue para associacao futura na central de contribuintes."
)


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


def _statement_receipt_eligible_contribution_ids(
    db: PowerChurchDB,
    *,
    lot_id: int = 0,
    contribution_ids: list[int] | None = None,
) -> list[int]:
    clauses = [
        "m.ativo = 1",
        "c.ativo = 1",
        "COALESCE(c.status_operacional, '') = 'regular'",
        "c.pessoa_id IS NOT NULL",
    ]
    params: list[object] = []
    if int(lot_id or 0):
        clauses.append("m.lote_id = ?")
        params.append(int(lot_id))
    clean_ids = [int(value or 0) for value in (contribution_ids or []) if int(value or 0)]
    if clean_ids:
        clauses.append(f"c.id IN ({','.join('?' for _ in clean_ids)})")
        params.extend(clean_ids)
    rows = db.conn.execute(
        f"""
        SELECT DISTINCT c.id
          FROM extrato_movimentos m
          JOIN contribuicoes c ON c.id = m.imported_contribution_id
         WHERE {' AND '.join(clauses)}
         ORDER BY c.id
        """,
        tuple(params),
    ).fetchall()
    return [int(row["id"] or 0) for row in rows if int(row["id"] or 0)]


def _auto_issue_statement_receipts(
    contribution_ids: list[int],
    *,
    actor: str = "",
    send_now: bool = True,
) -> dict[str, object]:
    clean_ids = [int(value or 0) for value in contribution_ids if int(value or 0)]
    if not clean_ids:
        return {
            "eligible_contributions": 0,
            "receipts_created": 0,
            "sent": 0,
            "queued": 0,
            "failed": 0,
            "without_email": 0,
            "receipt_ids": [],
            "dispatch_ids": [],
        }
    try:
        from power_church_django.apps.contributions.models import ReceiptDispatch
        from power_church_django.services.receipt_delivery import (
            get_receipt_detail_cached,
            issue_event_receipts_and_optionally_send,
        )

        result = issue_event_receipts_and_optionally_send(
            contribution_ids=clean_ids,
            email_overrides=None,
            subject="",
            body="",
            actor=actor,
            trigger=ReceiptDispatch.Trigger.AUTOMATIC,
            auto_created=True,
            send_now=bool(send_now),
        )
        dispatch_by_receipt: dict[int, list[object]] = {}
        for dispatch in result.get("dispatches", []):
            dispatch_by_receipt.setdefault(int(dispatch.legacy_receipt_id or 0), []).append(dispatch)
        sent = 0
        queued = 0
        failed = 0
        without_email = 0
        for receipt_id in [int(value or 0) for value in result.get("receipt_ids", []) if int(value or 0)]:
            dispatches = dispatch_by_receipt.get(receipt_id, [])
            if not dispatches:
                without_email += 1
                continue
            statuses = {str(getattr(dispatch, "status", "") or "") for dispatch in dispatches}
            if ReceiptDispatch.Status.SENT in statuses:
                sent += 1
            elif ReceiptDispatch.Status.PENDING in statuses:
                queued += 1
            elif ReceiptDispatch.Status.FAILED in statuses:
                failed += 1
            else:
                detail = get_receipt_detail_cached(receipt_id)
                if (detail.get("person") or {}).get("email") or (detail.get("receipt") or {}).get("person_email"):
                    failed += 1
                else:
                    without_email += 1
        return {
            "eligible_contributions": len(clean_ids),
            "receipts_created": len([int(value or 0) for value in result.get("receipt_ids", []) if int(value or 0)]),
            "sent": sent,
            "queued": queued,
            "failed": failed,
            "without_email": without_email,
            "receipt_ids": [int(value or 0) for value in result.get("receipt_ids", []) if int(value or 0)],
            "dispatch_ids": [int(getattr(dispatch, "pk", 0) or 0) for dispatch in result.get("dispatches", []) if int(getattr(dispatch, "pk", 0) or 0)],
        }
    except Exception as exc:
        return {
            "eligible_contributions": len(clean_ids),
            "receipts_created": 0,
            "sent": 0,
            "queued": 0,
            "failed": 0,
            "without_email": 0,
            "receipt_ids": [],
            "dispatch_ids": [],
            "error": str(exc),
        }


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


def update_bank_movement_from_form(kind: str, movement_id: int, form: Any, actor: str = "") -> int:
    db = PowerChurchDB(legacy_db_path())
    try:
        payload = _form_lists(form)
        before_eligible_ids: list[int] = []
        if kind == "statement":
            before_row = db.conn.execute(
                "SELECT imported_contribution_id FROM extrato_movimentos WHERE id = ? LIMIT 1",
                (movement_id,),
            ).fetchone()
            if before_row is not None:
                before_eligible_ids = _statement_receipt_eligible_contribution_ids(
                    db,
                    contribution_ids=[int(before_row["imported_contribution_id"] or 0)],
                )
        if kind == "pix":
            return db.update_pix_movement_from_form(movement_id, payload)
        contribution_id = db.update_statement_movement_from_form(movement_id, payload)
        after_eligible_ids = _statement_receipt_eligible_contribution_ids(
            db,
            contribution_ids=[int(contribution_id or 0)],
        )
        new_eligible_ids = [item for item in after_eligible_ids if item not in before_eligible_ids]
    except Exception as exc:
        raise LegacyBankWriteError(str(exc)) from exc
    finally:
        db.close()
    if kind == "statement" and new_eligible_ids:
        _auto_issue_statement_receipts(new_eligible_ids, actor=actor, send_now=False)
    return int(contribution_id or 0)


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


def close_bank_lot(kind: str, lot_id: int, actor: str = "") -> dict[str, int]:
    db = PowerChurchDB(legacy_db_path())
    try:
        auto_receipt_candidates: list[int] = []
        statement_was_closed = False
        if kind == "pix":
            return db.close_pix_lot(lot_id)
        lot_row = db.conn.execute(
            "SELECT status FROM extrato_lotes WHERE id = ? LIMIT 1",
            (lot_id,),
        ).fetchone()
        statement_was_closed = normalize_query(lot_row["status"] if lot_row else "") == "encerrado"
        result = db.close_statement_lot(lot_id)
        if not statement_was_closed:
            auto_receipt_candidates = _statement_receipt_eligible_contribution_ids(db, lot_id=lot_id)
    except Exception as exc:
        raise LegacyBankWriteError(str(exc)) from exc
    finally:
        db.close()
    if kind == "statement" and auto_receipt_candidates:
        receipt_result = _auto_issue_statement_receipts(auto_receipt_candidates, actor=actor, send_now=False)
        result = {
            **result,
            "auto_receipt_candidates": int(receipt_result.get("eligible_contributions", 0) or 0),
            "auto_receipt_created": int(receipt_result.get("receipts_created", 0) or 0),
            "auto_receipt_sent": int(receipt_result.get("sent", 0) or 0),
            "auto_receipt_queued": int(receipt_result.get("queued", 0) or 0),
            "auto_receipt_failed": int(receipt_result.get("failed", 0) or 0),
            "auto_receipt_without_email": int(receipt_result.get("without_email", 0) or 0),
            "auto_receipt_receipt_ids": list(receipt_result.get("receipt_ids", []) or []),
            "auto_receipt_dispatch_ids": list(receipt_result.get("dispatch_ids", []) or []),
        }
        if receipt_result.get("error"):
            result["auto_receipt_error"] = str(receipt_result["error"])
    return result


def prepare_statement_lot_for_audit(lot_id: int, actor: str = "") -> dict[str, int]:
    db = PowerChurchDB(legacy_db_path())
    try:
        imported_now = int(db.ensure_statement_financial_entries(lot_id) or 0)
        auto_receipt_candidates = _statement_receipt_eligible_contribution_ids(db, lot_id=lot_id)
        lot_row = db.conn.execute(
            "SELECT status FROM extrato_lotes WHERE id = ? LIMIT 1",
            (lot_id,),
        ).fetchone()
        db.refresh_statement_lot_status(lot_id)
        result: dict[str, int | str | list[int]] = {
            "importados": imported_now,
            "movidos_contribuintes": 0,
            "status_antes": str(lot_row["status"] or "") if lot_row else "",
        }
    except Exception as exc:
        raise LegacyBankWriteError(str(exc)) from exc
    finally:
        db.close()
    if auto_receipt_candidates:
        receipt_result = _auto_issue_statement_receipts(auto_receipt_candidates, actor=actor, send_now=False)
        result = {
            **result,
            "auto_receipt_candidates": int(receipt_result.get("eligible_contributions", 0) or 0),
            "auto_receipt_created": int(receipt_result.get("receipts_created", 0) or 0),
            "auto_receipt_sent": int(receipt_result.get("sent", 0) or 0),
            "auto_receipt_queued": int(receipt_result.get("queued", 0) or 0),
            "auto_receipt_failed": int(receipt_result.get("failed", 0) or 0),
            "auto_receipt_without_email": int(receipt_result.get("without_email", 0) or 0),
            "auto_receipt_receipt_ids": list(receipt_result.get("receipt_ids", []) or []),
            "auto_receipt_dispatch_ids": list(receipt_result.get("dispatch_ids", []) or []),
        }
        if receipt_result.get("error"):
            result["auto_receipt_error"] = str(receipt_result["error"])
    else:
        result = {
            **result,
            "auto_receipt_candidates": 0,
            "auto_receipt_created": 0,
            "auto_receipt_sent": 0,
            "auto_receipt_queued": 0,
            "auto_receipt_failed": 0,
            "auto_receipt_without_email": 0,
            "auto_receipt_receipt_ids": [],
            "auto_receipt_dispatch_ids": [],
        }
    return result  # type: ignore[return-value]


def reopen_statement_lot_for_audit(lot_id: int) -> dict[str, int | str]:
    db = PowerChurchDB(legacy_db_path())
    try:
        lot = db.get_statement_lot(lot_id)
        if lot is None:
            raise ValueError("Lote de extrato nao encontrado.")
        db.conn.execute(
            """
            UPDATE extrato_lotes
            SET status = 'auditando',
                observacoes = TRIM(REPLACE(COALESCE(observacoes, ''), ?, '')),
                atualizado_em = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (STATEMENT_LOT_CLOSE_NOTE, lot_id),
        )
        db.conn.execute(
            """
            UPDATE extrato_movimentos
            SET review_notes = TRIM(REPLACE(COALESCE(review_notes, ''), ?, '')),
                atualizado_em = CURRENT_TIMESTAMP
            WHERE lote_id = ?
              AND ativo = 1
              AND review_status IN ('revisar_pessoa', 'revisar_destinacao')
            """,
            (STATEMENT_MOVEMENT_CLOSE_NOTE, lot_id),
        )
        status = db.refresh_statement_lot_status(lot_id)
        db.conn.commit()
        return {
            "lot_id": int(lot_id),
            "status": status,
            "movement_reviews": int(
                db.scalar(
                    """
                    SELECT COUNT(*)
                    FROM extrato_movimentos
                    WHERE lote_id = ? AND ativo = 1 AND review_status IN ('revisar_pessoa', 'revisar_destinacao')
                    """,
                    (lot_id,),
                )
            ),
        }
    except Exception as exc:
        raise LegacyBankWriteError(str(exc)) from exc
    finally:
        db.close()

from __future__ import annotations

import argparse
import importlib.util
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "power_church_membros_importado.db"
BACKUP_DIR = ROOT / "data" / "backups"
APP_PATH = ROOT / "power_church_demo.py"


def load_app_module():
    root_text = str(ROOT)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    spec = importlib.util.spec_from_file_location("power_church_demo", APP_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@dataclass
class PreservedPixDecision:
    movement_id: int
    old_order: int
    date: str
    amount: float
    name_raw: str
    name_norm: str
    doc_token: str
    mode: str
    person_id: int
    contributor_id: int
    type_id: int
    review_notes: str


def backup_database(db_path: Path) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = BACKUP_DIR / f"{db_path.stem}_before_migrar_pix_para_extrato_{stamp}.db"
    shutil.copy2(db_path, target)
    return target


def infer_document_type(app, value: object) -> str:
    text = app.normalize_query(value)
    digits = "".join(ch for ch in text if ch.isdigit())
    if "/" in text or len(digits) == 14:
        return "cnpj"
    if len(digits) == 11:
        return "cpf"
    if "*" in text and len(app.cleaned_document_token(text)) == 11:
        return "cpf"
    return ""


def preserved_decisions_for_pix_lot(db, app, lot_id: int) -> tuple[dict[str, object], list[PreservedPixDecision]]:
    lot = db.get_pix_lot(lot_id)
    if lot is None:
        raise ValueError(f"Lote PIX #{lot_id} nao encontrado.")
    is_closed = str(lot["status"]) == "encerrado"
    rows = db.conn.execute(
        """
        SELECT *
        FROM pix_movimentos
        WHERE lote_id = ? AND ativo = 1
          AND (
            resolved_person_id IS NOT NULL
            OR COALESCE(association_reviewed, 0) = 1
            OR review_status = 'aprovado'
            OR review_status = 'ignorado'
            OR resolved_tipo_contribuicao_id IS NOT NULL
            OR (? = 1 AND imported_contribution_id IS NOT NULL AND COALESCE(resolved_person_id, 0) = 0)
          )
        ORDER BY ordem_no_lote, id
        """,
        (lot_id, 1 if is_closed else 0),
    ).fetchall()
    decisions: list[PreservedPixDecision] = []
    for row in rows:
        review_status = str(row["review_status"] or "")
        person_id = app.moneyless_int(row["resolved_person_id"])
        contributor_id = app.moneyless_int(row["resolved_contribuinte_id"])
        type_id = app.moneyless_int(row["resolved_tipo_contribuicao_id"])
        association_reviewed = app.moneyless_int(row["association_reviewed"])
        imported_contribution_id = app.moneyless_int(row["imported_contribution_id"])
        if review_status == "ignorado":
            mode = "ignore"
        elif person_id:
            mode = "person"
        elif association_reviewed or review_status == "aprovado":
            mode = "no_person"
        elif is_closed and imported_contribution_id:
            mode = "no_person"
        else:
            continue
        decisions.append(
            PreservedPixDecision(
                movement_id=app.moneyless_int(row["id"]),
                old_order=app.moneyless_int(row["ordem_no_lote"]),
                date=str(row["data_recebimento"] or ""),
                amount=round(float(row["valor"] or 0), 2),
                name_raw=str(row["nome_origem"] or ""),
                name_norm=app.normalize_match_name(row["nome_origem"]),
                doc_token=app.cleaned_document_token(row["documento_mascarado"]),
                mode=mode,
                person_id=person_id,
                contributor_id=contributor_id,
                type_id=type_id,
                review_notes=str(row["review_notes"] or ""),
            )
        )
    return dict(lot), decisions


def deactivate_current_pix_lot(db, app, lot_id: int, reason: str) -> tuple[int, int]:
    rows = db.conn.execute(
        """
        SELECT *
        FROM pix_movimentos
        WHERE lote_id = ? AND ativo = 1
        ORDER BY ordem_no_lote, id
        """,
        (lot_id,),
    ).fetchall()
    deactivated_movements = 0
    deactivated_contributions = 0
    for row in rows:
        organization_id = app.moneyless_int(row["organizacao_id"])
        contribution_id = app.moneyless_int(row["imported_contribution_id"])
        if contribution_id:
            contribution = db.get_contribution(contribution_id)
            if contribution is not None and app.moneyless_int(contribution["ativo"]):
                before_contribution = dict(contribution)
                notes = app.normalize_query(contribution["observacoes"])
                merged_notes = reason if not notes else f"{notes}\n{reason}"
                db.conn.execute(
                    """
                    UPDATE contribuicoes
                    SET ativo = 0,
                        status_operacional = 'substituido_extrato',
                        observacoes = ?,
                        atualizado_em = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (merged_notes, contribution_id),
                )
                after_contribution = db.get_contribution(contribution_id)
                db.write_audit_log(
                    organization_id,
                    "desativar_contribuicao_pix_migracao_extrato",
                    "contribuicoes",
                    contribution_id,
                    before_contribution,
                    dict(after_contribution) if after_contribution else None,
                )
                deactivated_contributions += 1
        before_row = dict(row)
        merged_row_notes = reason if not app.normalize_query(row["review_notes"]) else f"{row['review_notes']}\n{reason}"
        archived_fingerprint = f"{row['fingerprint']}::statement_migration::{row['id']}"
        db.conn.execute(
            """
            UPDATE pix_movimentos
            SET ativo = 0,
                fingerprint = ?,
                review_notes = ?,
                atualizado_em = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (archived_fingerprint, merged_row_notes, row["id"]),
        )
        after_row = db.conn.execute("SELECT * FROM pix_movimentos WHERE id = ?", (row["id"],)).fetchone()
        db.write_audit_log(
            organization_id,
            "desativar_movimento_pix_migracao_extrato",
            "pix_movimentos",
            app.moneyless_int(row["id"]),
            before_row,
            dict(after_row) if after_row else None,
        )
        deactivated_movements += 1
    db.conn.execute(
        """
        UPDATE pix_lotes
        SET status = 'encerrado',
            observacoes = ?,
            atualizado_em = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (reason, lot_id),
    )
    return deactivated_movements, deactivated_contributions


def decision_score(app, decision: PreservedPixDecision, row) -> tuple[int, float]:
    row_date = str(row["data_movimento"] or "")
    row_amount = round(float(row["valor"] or 0), 2)
    row_name_norm = app.normalize_match_name(row["nome_origem"])
    row_doc = app.cleaned_document_token(row["bank_document"])
    ratio = SequenceMatcher(None, decision.name_norm, row_name_norm).ratio() if decision.name_norm and row_name_norm else 0.0
    prefix_match = bool(decision.name_norm and row_name_norm and (decision.name_norm in row_name_norm or row_name_norm in decision.name_norm))
    same_doc = bool(decision.doc_token and row_doc and decision.doc_token == row_doc)
    same_date = decision.date == row_date
    same_amount = abs(decision.amount - row_amount) < 0.005

    score = -1
    if same_doc and same_date and same_amount:
        score = 300
    elif same_doc and same_date and ratio >= 0.55:
        score = 260
    elif same_doc and same_amount and ratio >= 0.55:
        score = 250
    elif same_doc and ratio >= 0.82:
        score = 220
    elif same_date and same_amount and decision.name_norm == row_name_norm and row_name_norm:
        score = 210
    elif same_date and same_amount and prefix_match:
        score = 190
    elif same_date and same_amount and ratio >= 0.88:
        score = 180
    elif same_date and same_amount and ratio >= 0.78:
        score = 150
    elif same_date and prefix_match and ratio >= 0.70:
        score = 120
    return score, ratio


def match_preserved_decision(app, decisions: list[PreservedPixDecision], used_ids: set[int], row) -> PreservedPixDecision | None:
    best: tuple[int, float, int, PreservedPixDecision] | None = None
    for decision in decisions:
        if decision.movement_id in used_ids:
            continue
        score, ratio = decision_score(app, decision, row)
        if score < 0:
            continue
        distance = abs(decision.old_order - app.moneyless_int(row["ordem_no_lote"]))
        candidate = (score, ratio, -distance, decision)
        if best is None or candidate > best:
            best = candidate
    return best[3] if best else None


def apply_preserved_decisions_to_statement_lot(db, app, statement_lot_id: int, decisions: list[PreservedPixDecision]) -> int:
    lot = db.get_statement_lot(statement_lot_id)
    if lot is None:
        raise ValueError(f"Lote de extrato #{statement_lot_id} nao encontrado.")
    layout_code = app.normalize_query(lot["layout_codigo"]).upper()
    contributor_source = app.statement_layout_contributor_source(layout_code)
    rows = db.conn.execute(
        """
        SELECT *
        FROM extrato_movimentos
        WHERE lote_id = ? AND ativo = 1
        ORDER BY ordem_no_lote, id
        """,
        (statement_lot_id,),
    ).fetchall()
    used_ids: set[int] = set()
    applied = 0
    for row in rows:
        decision = match_preserved_decision(app, decisions, used_ids, row)
        if decision is None:
            continue
        used_ids.add(decision.movement_id)
        organization_id = app.moneyless_int(row["organizacao_id"])
        source_name = str(row["nome_origem"] or "")
        bank_document = app.normalize_query(row["bank_document"])
        document_type = infer_document_type(app, bank_document)
        contributor_kind = app.contributor_kind_for_identity(
            source_name,
            document_type=document_type,
            document_value=bank_document,
        )
        contributor_id = decision.contributor_id
        if not contributor_id and app.normalize_query(source_name):
            contributor_id = db.upsert_contributor(
                organization_id,
                source_name,
                contributor_kind,
                document_value=bank_document,
                document_type=document_type if bank_document else "",
                person_id=decision.person_id or None,
                source=contributor_source,
                quality="doador",
            )
        elif contributor_id and decision.person_id:
            db.link_contributor_to_person(
                contributor_id,
                decision.person_id,
                note="Vinculo reaplicado automaticamente durante a migracao PIX -> Extrato Sicoob.",
                commit=False,
            )
        before = db.statement_movement_snapshot(app.moneyless_int(row["id"]))
        notes = app.normalize_query(decision.review_notes)
        migration_note = f"Decisao reaplicada automaticamente a partir do movimento PIX antigo #{decision.movement_id}."
        merged_notes = migration_note if not notes else f"{notes}\n{migration_note}"
        if decision.mode == "ignore":
            db.conn.execute(
                """
                UPDATE extrato_movimentos
                SET resolved_person_id = NULL,
                    resolved_contribuinte_id = ?,
                    resolved_tipo_contribuicao_id = ?,
                    association_reviewed = 0,
                    review_status = 'ignorado',
                    review_notes = ?,
                    atualizado_em = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    contributor_id or None,
                    decision.type_id or None,
                    merged_notes,
                    row["id"],
                ),
            )
            contribution_id = app.moneyless_int(row["imported_contribution_id"])
            if contribution_id:
                contribution = db.get_contribution(contribution_id)
                if contribution is not None and app.moneyless_int(contribution["ativo"]):
                    before_contribution = dict(contribution)
                    notes_now = app.normalize_query(contribution["observacoes"])
                    contribution_notes = merged_notes if not notes_now else f"{notes_now}\n{merged_notes}"
                    db.conn.execute(
                        """
                        UPDATE contribuicoes
                        SET ativo = 0,
                            status_operacional = 'ignorado',
                            observacoes = ?,
                            atualizado_em = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (contribution_notes, contribution_id),
                    )
                    after_contribution = db.get_contribution(contribution_id)
                    db.write_audit_log(
                        organization_id,
                        "reaplicar_ignoracao_migracao_extrato_sicoob",
                        "contribuicoes",
                        contribution_id,
                        before_contribution,
                        dict(after_contribution) if after_contribution else None,
                    )
        else:
            association_reviewed = 0 if decision.person_id else 1
            db.conn.execute(
                """
                UPDATE extrato_movimentos
                SET resolved_person_id = ?,
                    resolved_contribuinte_id = ?,
                    resolved_tipo_contribuicao_id = ?,
                    association_reviewed = ?,
                    review_status = 'aprovado',
                    review_notes = ?,
                    atualizado_em = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    decision.person_id or None,
                    contributor_id or None,
                    decision.type_id or None,
                    association_reviewed,
                    merged_notes,
                    row["id"],
                ),
            )
            if app.moneyless_int(row["imported_contribution_id"]):
                db.sync_imported_contribution_with_statement_movement(app.moneyless_int(row["id"]), refresh_lot=False)
            else:
                db.import_single_statement_movement(app.moneyless_int(row["id"]), refresh_lot=False)
        after = db.statement_movement_snapshot(app.moneyless_int(row["id"]))
        db.write_audit_log(
            organization_id,
            "reaplicar_decisao_migracao_extrato_sicoob",
            "extrato_movimentos",
            app.moneyless_int(row["id"]),
            before,
            after,
        )
        applied += 1
    db.refresh_statement_lot_status(statement_lot_id)
    return applied


def migrate_one_lot(db, app, pix_lot_id: int, pdf_path: Path) -> dict[str, object]:
    lot_row, decisions = preserved_decisions_for_pix_lot(db, app, pix_lot_id)
    reason = (
        f"Lote PIX substituido pelo Extrato Sicoob de recebimentos ({pdf_path.name}) "
        "para consolidacao canonica do mes."
    )
    deactivated_movements, deactivated_contributions = deactivate_current_pix_lot(db, app, pix_lot_id, reason)
    statement_lot_id = db.create_statement_lot_from_upload(pdf_path.name, pdf_path.read_bytes(), layout_code="SICOOB_RECEBIMENTOS")
    reapplied = apply_preserved_decisions_to_statement_lot(db, app, statement_lot_id, decisions)
    db.conn.commit()
    statement_lot = db.get_statement_lot(statement_lot_id)
    review_counts = db.statement_lot_review_counts(statement_lot_id)
    financial_counts = db.statement_lot_financial_counts(statement_lot_id)
    return {
        "pix_lot_id": pix_lot_id,
        "statement_lot_id": statement_lot_id,
        "old_status": lot_row["status"],
        "new_status": str(statement_lot["status"]) if statement_lot else "",
        "deactivated_movements": deactivated_movements,
        "deactivated_contributions": deactivated_contributions,
        "reapplied_decisions": reapplied,
        "statement_total_movements": app.moneyless_int(statement_lot["total_movimentos"] if statement_lot else 0),
        "statement_total_value": round(float(statement_lot["total_valor"] if statement_lot else 0), 2),
        "review_counts": dict(review_counts),
        "financial_counts": dict(financial_counts),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Migra lotes PIX historicos para o Extrato Sicoob de recebimentos.")
    parser.add_argument("--db", default=str(DB_PATH), help="Banco que sera atualizado")
    parser.add_argument(
        "--mapping",
        action="append",
        required=True,
        help="Mapeamento no formato pix_lot_id=/caminho/do/pdf",
    )
    parser.add_argument("--skip-backup", action="store_true", help="Nao criar backup automatico antes da migracao")
    args = parser.parse_args()

    app = load_app_module()
    db_path = Path(args.db).expanduser().resolve()
    mappings: list[tuple[int, Path]] = []
    for raw in args.mapping:
        if "=" not in raw:
            raise ValueError(f"Mapeamento invalido: {raw}")
        lot_text, file_text = raw.split("=", 1)
        mappings.append((int(lot_text.strip()), Path(file_text).expanduser().resolve()))

    backup_path = None
    if not args.skip_backup:
        backup_path = backup_database(db_path)

    db = app.PowerChurchDB(db_path)
    try:
        for pix_lot_id, pdf_path in mappings:
            result = migrate_one_lot(db, app, pix_lot_id, pdf_path)
            print(
                f"PIX lote #{result['pix_lot_id']} -> Extrato lote #{result['statement_lot_id']} | "
                f"{result['statement_total_movements']} movimentos | "
                f"R$ {result['statement_total_value']:.2f} | "
                f"decisoes reaplicadas: {result['reapplied_decisions']}"
            )
    finally:
        db.close()

    if backup_path is not None:
        print(f"Backup criado em: {backup_path}")


if __name__ == "__main__":
    main()

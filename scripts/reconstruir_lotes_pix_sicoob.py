from __future__ import annotations

import argparse
import importlib.util
import shutil
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "power_church_membros_importado.db"
BACKUP_DIR = ROOT / "data" / "backups"
APP_PATH = ROOT / "power_church_demo.py"


def load_app_module():
    spec = importlib.util.spec_from_file_location("power_church_demo", APP_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@dataclass
class PreservedDecision:
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
    target = BACKUP_DIR / f"{db_path.stem}_before_rebuild_pix_{stamp}.db"
    shutil.copy2(db_path, target)
    return target


def preserved_decisions_for_lot(db, app, lot_id: int) -> tuple[dict[str, object], list[PreservedDecision]]:
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
    decisions: list[PreservedDecision] = []
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
            PreservedDecision(
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


def decision_score(app, decision: PreservedDecision, entry: dict[str, object]) -> tuple[int, float]:
    entry_date = str(entry["data_recebimento"])
    entry_amount = round(float(entry["valor"]), 2)
    entry_name_norm = app.normalize_match_name(entry["nome_origem"])
    entry_doc = app.cleaned_document_token(entry["documento_mascarado"])
    ratio = SequenceMatcher(None, decision.name_norm, entry_name_norm).ratio() if decision.name_norm and entry_name_norm else 0.0
    prefix_match = bool(decision.name_norm and entry_name_norm and (decision.name_norm in entry_name_norm or entry_name_norm in decision.name_norm))
    same_doc = bool(decision.doc_token and entry_doc and decision.doc_token == entry_doc)
    same_date = decision.date == entry_date
    same_amount = abs(decision.amount - entry_amount) < 0.005

    score = -1
    if same_doc and same_date and same_amount:
        score = 300
    elif same_doc and same_date and ratio >= 0.55:
        score = 260
    elif same_doc and same_amount and ratio >= 0.55:
        score = 250
    elif same_doc and ratio >= 0.82:
        score = 220
    elif same_date and same_amount and decision.name_norm == entry_name_norm and entry_name_norm:
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


def match_preserved_decision(app, decisions: list[PreservedDecision], used_ids: set[int], entry: dict[str, object]) -> PreservedDecision | None:
    best: tuple[int, float, int, PreservedDecision] | None = None
    for decision in decisions:
        if decision.movement_id in used_ids:
            continue
        score, ratio = decision_score(app, decision, entry)
        if score < 0:
            continue
        distance = abs(decision.old_order - app.moneyless_int(entry["ordem_no_lote"]))
        candidate = (score, ratio, -distance, decision)
        if best is None or candidate > best:
            best = candidate
    if best is None:
        return None
    return best[3]


def deactivate_current_pix_lot(db, app, lot_id: int) -> tuple[int, int]:
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
    reason = "Reconstrucao do lote PIX a partir do PDF original para saneamento de parser."
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
                        status_operacional = 'substituido_auditoria',
                        observacoes = ?,
                        atualizado_em = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (merged_notes, contribution_id),
                )
                after_contribution = db.get_contribution(contribution_id)
                db.write_audit_log(
                    organization_id,
                    "desativar_contribuicao_reconstrucao_pix",
                    "contribuicoes",
                    contribution_id,
                    before_contribution,
                    dict(after_contribution) if after_contribution else None,
                )
                deactivated_contributions += 1
        before_row = dict(row)
        merged_row_notes = reason if not app.normalize_query(row["review_notes"]) else f"{row['review_notes']}\n{reason}"
        archived_fingerprint = f"{row['fingerprint']}::rebuild_inactive::{row['id']}"
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
            "desativar_movimento_reconstrucao_pix",
            "pix_movimentos",
            app.moneyless_int(row["id"]),
            before_row,
            dict(after_row) if after_row else None,
        )
        deactivated_movements += 1
    return deactivated_movements, deactivated_contributions


def insert_reparsed_pix_entries(db, app, lot_id: int, lot_row: dict[str, object], parsed: dict[str, object]) -> int:
    organization_id = app.moneyless_int(lot_row["organizacao_id"])
    people_cache = db.people_for_pix_matching(organization_id)
    rules_by_code = {str(row["codigo_centavos"]): row for row in db.pix_rules(organization_id) if app.moneyless_int(row["ativo"])}
    lot_signature_occurrences: dict[str, int] = {}
    total_value = 0.0
    inserted = 0
    for entry in parsed["entries"]:
        donor_name = str(entry["donor_name"])
        donor_doc = str(entry["document_mask"] or "")
        document_type = str(entry["document_type"] or "")
        match = db.match_pix_entry(
            organization_id,
            donor_name,
            donor_doc,
            document_type,
            people_cache=people_cache,
        )
        contributor_kind = app.contributor_kind_for_identity(
            donor_name,
            document_type=document_type,
            document_value=donor_doc,
        )
        suggested_person_id = app.moneyless_int(match["person_id"])
        suggested_contributor_id = db.upsert_contributor(
            organization_id,
            donor_name,
            contributor_kind,
            document_value=donor_doc,
            document_type=document_type or ("cnpj_mascarado" if contributor_kind == "pj" else "cpf_mascarado"),
            person_id=suggested_person_id,
            source="pix",
            quality="doador",
        )
        cent_code = str(entry["cent_code"])
        rule_row = rules_by_code.get(cent_code)
        type_suggested = "destinacao_especial" if rule_row else "dizimo"
        review_status = db.classify_pix_review_status(str(match["confidence"]), special_rule=rule_row is not None)
        signature_global = app.pix_global_signature(
            entry["received_on"],
            entry["amount"],
            entry["donor_name_normalized"],
            donor_doc,
            document_type,
            entry["raw_text"],
        )
        occurrence_index = lot_signature_occurrences.get(signature_global, 0) + 1
        lot_signature_occurrences[signature_global] = occurrence_index
        duplicate_state = db.find_pix_duplicate_targets(
            organization_id,
            signature_global,
            str(entry["received_on"]),
            float(entry["amount"]),
            person_id=suggested_person_id,
            contributor_id=suggested_contributor_id,
            occurrence_index=occurrence_index,
            ignore_lot_id=lot_id,
        )
        if duplicate_state["review_status"]:
            review_status = str(duplicate_state["review_status"])
        fingerprint_source = "|".join(
            [
                str(entry["received_on"]),
                f"{float(entry['amount']):.2f}",
                app.normalize_match_name(donor_name),
                donor_doc,
                str(entry["page_number"]),
                str(entry["order_in_file"]),
            ]
        )
        fingerprint = app.hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest()
        db.conn.execute(
            """
            INSERT INTO pix_movimentos (
                lote_id, organizacao_id, pagina, ordem_no_lote, data_recebimento, competencia, competencia_ordem,
                valor, codigo_centavos, nome_origem, nome_normalizado, documento_mascarado, documento_tipo,
                tipo_sugerido, regra_id, confidence, match_score, suggested_person_id, suggested_contribuinte_id,
                resolved_person_id, resolved_contribuinte_id, resolved_tipo_contribuicao_id, review_status,
                review_notes, raw_text, fingerprint, signature_global, duplicate_movement_id, duplicate_contribution_id,
                duplicate_reason, imported_contribution_id, ativo, atualizado_em
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 1, CURRENT_TIMESTAMP)
            """,
            (
                lot_id,
                organization_id,
                app.moneyless_int(entry["page_number"]),
                app.moneyless_int(entry["order_in_file"]),
                str(entry["received_on"]),
                str(entry["competencia"]),
                app.moneyless_int(entry["competencia_ordem"]),
                float(entry["amount"]),
                cent_code,
                donor_name,
                str(entry["donor_name_normalized"]),
                donor_doc or None,
                document_type or None,
                type_suggested,
                app.moneyless_int(rule_row["id"]) if rule_row else None,
                str(match["confidence"]),
                float(match["score"]),
                suggested_person_id or None,
                suggested_contributor_id or None,
                review_status,
                str(duplicate_state["duplicate_reason"] or match["notes"]),
                str(entry["raw_text"]),
                fingerprint,
                signature_global,
                app.moneyless_int(duplicate_state["duplicate_movement_id"]) or None,
                app.moneyless_int(duplicate_state["duplicate_contribution_id"]) or None,
                str(duplicate_state["duplicate_reason"] or "") or None,
            ),
        )
        total_value += float(entry["amount"])
        inserted += 1
    db.conn.execute(
        """
        UPDATE pix_lotes
        SET total_movimentos = ?, total_valor = ?, status = 'auditando', atualizado_em = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (inserted, round(total_value, 2), lot_id),
    )
    return inserted


def apply_preserved_decisions(db, app, lot_id: int, decisions: list[PreservedDecision]) -> int:
    rows = db.conn.execute(
        """
        SELECT *
        FROM pix_movimentos
        WHERE lote_id = ? AND ativo = 1
        ORDER BY ordem_no_lote, id
        """,
        (lot_id,),
    ).fetchall()
    used_ids: set[int] = set()
    applied = 0
    for row in rows:
        decision = match_preserved_decision(app, decisions, used_ids, dict(row))
        if decision is None:
            continue
        used_ids.add(decision.movement_id)
        organization_id = app.moneyless_int(row["organizacao_id"])
        contributor_kind = app.contributor_kind_for_identity(
            row["nome_origem"],
            document_type=row["documento_tipo"],
            document_value=row["documento_mascarado"],
        )
        contributor_id = decision.contributor_id
        if not contributor_id:
            contributor_id = db.upsert_contributor(
                organization_id,
                str(row["nome_origem"]),
                contributor_kind,
                document_value=str(row["documento_mascarado"] or ""),
                document_type=str(row["documento_tipo"] or "") or ("cnpj_mascarado" if contributor_kind == "pj" else "cpf_mascarado"),
                person_id=decision.person_id or None,
                source="pix",
                quality="doador",
            )
        elif decision.person_id:
            db.link_contributor_to_person(
                contributor_id,
                decision.person_id,
                note="Vinculo reaplicado automaticamente durante a reconstrucao do lote PIX.",
                commit=False,
            )
        before = db.movement_snapshot(app.moneyless_int(row["id"]))
        notes = app.normalize_query(decision.review_notes)
        rebuild_note = f"Decisao reaplicada automaticamente a partir do movimento antigo #{decision.movement_id}."
        merged_notes = rebuild_note if not notes else f"{notes}\n{rebuild_note}"
        if decision.mode == "ignore":
            db.conn.execute(
                """
                UPDATE pix_movimentos
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
                        "reaplicar_ignoracao_reconstrucao_pix",
                        "contribuicoes",
                        contribution_id,
                        before_contribution,
                        dict(after_contribution) if after_contribution else None,
                    )
        else:
            association_reviewed = 0 if decision.person_id else 1
            db.conn.execute(
                """
                UPDATE pix_movimentos
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
                db.sync_imported_contribution_with_pix_movement(app.moneyless_int(row["id"]), refresh_lot=False)
            else:
                db.import_single_pix_movement(app.moneyless_int(row["id"]), refresh_lot=False)
        after = db.movement_snapshot(app.moneyless_int(row["id"]))
        db.write_audit_log(
            organization_id,
            "reaplicar_decisao_reconstrucao_pix",
            "pix_movimentos",
            app.moneyless_int(row["id"]),
            before,
            after,
        )
        applied += 1
    return applied


def rebuild_pix_lot(db, app, lot_id: int) -> dict[str, object]:
    lot_row, decisions = preserved_decisions_for_lot(db, app, lot_id)
    parsed = app.parse_sicoob_pix_pdf(Path(str(lot_row["caminho_arquivo"])))
    deactivated_movements, deactivated_contributions = deactivate_current_pix_lot(db, app, lot_id)
    inserted = insert_reparsed_pix_entries(db, app, lot_id, lot_row, parsed)
    imported = db.ensure_pix_financial_entries(lot_id)
    applied = apply_preserved_decisions(db, app, lot_id, decisions)
    promoted = db.promote_linked_pix_sem_associacao(lot_id)
    strong_promoted = db.promote_strong_pix_sem_associacao(lot_id)
    db.refresh_pix_lot_status(lot_id)
    if str(lot_row["status"]) == "encerrado":
        db.conn.execute(
            """
            UPDATE pix_lotes
            SET status = 'encerrado',
                observacoes = ?,
                atualizado_em = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (lot_row["observacoes"], lot_id),
        )
    db.conn.commit()
    refreshed = db.get_pix_lot(lot_id)
    return {
        "lot_id": lot_id,
        "old_status": lot_row["status"],
        "new_status": str(refreshed["status"]) if refreshed else "",
        "deactivated_movements": deactivated_movements,
        "deactivated_contributions": deactivated_contributions,
        "inserted": inserted,
        "imported": imported,
        "reapplied": applied,
        "promoted": promoted,
        "strong_promoted": strong_promoted,
        "total_movements": app.moneyless_int(refreshed["total_movimentos"] if refreshed else 0),
        "total_value": round(float(refreshed["total_valor"] if refreshed else 0), 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--lots", nargs="+", type=int, default=[1, 2, 3, 4])
    parser.add_argument("--backup", action="store_true")
    args = parser.parse_args()

    app = load_app_module()
    db_path = Path(args.db)
    if args.backup and db_path.resolve() == DB_PATH.resolve():
        backup = backup_database(db_path)
        print(f"backup={backup}")
    db = app.PowerChurchDB(db_path)
    try:
        for lot_id in args.lots:
            result = rebuild_pix_lot(db, app, lot_id)
            print(result)
    finally:
        db.close()


if __name__ == "__main__":
    main()

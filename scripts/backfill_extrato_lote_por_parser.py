#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "power_church_membros_importado.db"
sys.path.insert(0, str(ROOT))

from power_church_core.normalization import moneyless_int, normalize_match_name, normalize_query
from power_church_demo import (
    PowerChurchDB,
    contributor_kind_for_identity,
    core_bank_lots,
    core_designations,
    parse_statement_pdf_by_layout,
    santander_document_type,
    statement_contributor_name_for_identity,
    statement_is_same_organization_origin,
    statement_layout_contributor_source,
    statement_layout_is_santander,
    statement_same_organization_review_note,
    statement_should_skip_entry,
)


def _key_from_entry(entry: Mapping[str, object]) -> tuple[object, ...]:
    return (
        normalize_query(entry.get("received_on")),
        int(round(float(entry.get("amount") or 0) * 100)),
        normalize_match_name(entry.get("prefix")),
        normalize_match_name(entry.get("source_name") or entry.get("origin_label")),
        normalize_query(entry.get("bank_document")),
    )


def _key_from_row(row: Mapping[str, object]) -> tuple[object, ...]:
    return (
        normalize_query(row["data_movimento"]),
        int(round(float(row["valor"] or 0) * 100)),
        normalize_match_name(row["prefixo_historico"]),
        normalize_match_name(row["nome_origem"] or row["origin_label"]),
        normalize_query(row["bank_document"]),
    )


def _missing_entries(db: PowerChurchDB, lot_id: int, layout_code: str, pdf_path: Path) -> list[dict[str, object]]:
    reparsed = parse_statement_pdf_by_layout(layout_code, pdf_path)
    entries = [
        entry
        for entry in reparsed["entries"]
        if isinstance(entry, Mapping) and not statement_should_skip_entry(layout_code, entry)
    ]
    existing_rows = db.conn.execute(
        "SELECT * FROM extrato_movimentos WHERE lote_id = ? AND ativo = 1",
        (lot_id,),
    ).fetchall()
    existing_counts = Counter(_key_from_row(row) for row in existing_rows)
    signature_seen: dict[str, int] = defaultdict(int)
    missing: list[dict[str, object]] = []
    for entry in entries:
        plan = core_bank_lots.statement_entry_plan(layout_code, entry)
        signature_seen[plan.signature_global] += 1
        key = _key_from_entry(entry)
        if existing_counts[key]:
            existing_counts[key] -= 1
            continue
        missing.append({"entry": dict(entry), "plan": plan, "occurrence_index": signature_seen[plan.signature_global]})
    return missing


def _insert_missing_statement_entry(
    db: PowerChurchDB,
    lot: Mapping[str, object],
    item: Mapping[str, object],
    people_cache: list[Mapping[str, object]],
    rules_by_code: dict[str, Mapping[str, object]],
    organization_name: str,
) -> int:
    entry = item["entry"]
    entry_plan = item["plan"]
    occurrence_index = moneyless_int(item["occurrence_index"]) or 1
    organization_id = moneyless_int(lot["organizacao_id"])
    layout_code = normalize_query(lot["layout_codigo"]).upper()
    contributor_source = statement_layout_contributor_source(layout_code)
    source_name = normalize_query(entry_plan.source_name)
    document_value = normalize_query(entry_plan.bank_document)
    document_type = normalize_query(entry_plan.document_type)
    match: dict[str, Any] = {"confidence": "sem_match", "score": 0.0, "person_id": 0, "notes": ""}
    suggested_person_id = 0
    suggested_contributor_id = 0
    same_org_origin = bool(source_name) and statement_is_same_organization_origin(source_name, organization_name)
    if source_name and not same_org_origin:
        match = db.match_pix_entry(
            organization_id,
            source_name,
            document_value,
            document_type,
            people_cache=people_cache,
        )
        suggested_person_id = moneyless_int(match["person_id"])
        contributor_kind = contributor_kind_for_identity(
            source_name,
            document_type=document_type,
            document_value=document_value,
        )
        suggested_contributor_id = db.upsert_contributor(
            organization_id,
            source_name,
            contributor_kind,
            document_value=document_value,
            document_type=document_type if document_value else "",
            person_id=suggested_person_id,
            source=contributor_source,
            quality="doador",
        )
    elif statement_layout_is_santander(layout_code) and document_value:
        document_type = document_type or santander_document_type(document_value)
        match = db.match_pix_entry(
            organization_id,
            "",
            document_value,
            document_type,
            people_cache=people_cache,
        )
        suggested_person_id = moneyless_int(match["person_id"])
        contributor_name = statement_contributor_name_for_identity(
            layout_code,
            "",
            document_value,
            document_type,
            match.get("person_name"),
        )
        contributor_kind = contributor_kind_for_identity(
            contributor_name,
            document_type=document_type,
            document_value=document_value,
        )
        suggested_contributor_id = db.upsert_contributor(
            organization_id,
            contributor_name,
            contributor_kind,
            document_value=document_value,
            document_type=document_type if document_value else "",
            person_id=suggested_person_id,
            source=contributor_source,
            quality="doador",
        )
    elif same_org_origin:
        match = {
            "confidence": "mesma_organizacao",
            "score": 0.0,
            "person_id": 0,
            "notes": statement_same_organization_review_note(source_name, organization_name),
        }
    cent_code = entry_plan.cent_code
    rule_row = rules_by_code.get(cent_code)
    type_suggested = core_designations.suggested_type_for_cent_rule(rule_row)
    review_status = db.classify_statement_review_status(str(match["confidence"]), source_name, special_rule=rule_row is not None)
    if core_bank_lots.statement_force_person_review(layout_code, document_value, suggested_person_id, rule_row is not None):
        review_status = "revisar_pessoa"
    duplicate_state = db.find_statement_duplicate_targets(
        organization_id,
        entry_plan.signature_global,
        occurrence_index=occurrence_index,
        ignore_lot_id=moneyless_int(lot["id"]),
    )
    if not duplicate_state["review_status"]:
        duplicate_state = db.find_statement_operational_duplicate_targets(
            organization_id,
            layout_code,
            entry_plan.received_on,
            entry_plan.amount,
            source_name,
            document_value,
            occurrence_index=occurrence_index,
            ignore_lot_id=moneyless_int(lot["id"]),
        )
    if duplicate_state["review_status"]:
        review_status = str(duplicate_state["review_status"])
    cursor = db.conn.execute(
        """
        INSERT INTO extrato_movimentos (
            lote_id, organizacao_id, pagina, ordem_no_lote, data_movimento, competencia, competencia_ordem,
            valor, codigo_centavos, movement_kind, receiving_code, bank_document, prefixo_historico, nome_origem, nome_normalizado,
            origin_label, tipo_sugerido, regra_id, confidence, match_score, suggested_person_id, suggested_contribuinte_id,
            review_status, review_notes, raw_text, fingerprint, signature_global, duplicate_movement_id,
            duplicate_contribution_id, duplicate_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            moneyless_int(lot["id"]),
            organization_id,
            entry_plan.page_number,
            entry_plan.order_in_file,
            entry_plan.received_on,
            entry_plan.competencia,
            entry_plan.competencia_ordem,
            entry_plan.amount,
            cent_code,
            entry_plan.movement_kind,
            entry_plan.receiving_code,
            document_value or None,
            entry_plan.prefix or None,
            source_name or None,
            entry_plan.source_name_normalized or None,
            entry_plan.origin_label,
            type_suggested,
            moneyless_int(rule_row["id"]) if rule_row else None,
            str(match["confidence"]),
            float(match["score"]),
            suggested_person_id or None,
            suggested_contributor_id or None,
            review_status,
            str(duplicate_state["duplicate_reason"] or match["notes"]),
            entry_plan.raw_text,
            entry_plan.fingerprint,
            entry_plan.signature_global,
            moneyless_int(duplicate_state["duplicate_movement_id"]) or None,
            moneyless_int(duplicate_state["duplicate_contribution_id"]) or None,
            str(duplicate_state["duplicate_reason"] or "") or None,
        ),
    )
    movement_id = moneyless_int(cursor.lastrowid)
    saved = db.conn.execute("SELECT * FROM extrato_movimentos WHERE id = ?", (movement_id,)).fetchone()
    db.write_audit_log(
        organization_id,
        "backfill_movimento_extrato_parser_corrigido",
        "extrato_movimentos",
        movement_id,
        None,
        dict(saved) if saved else {"id": movement_id},
    )
    return movement_id


def main() -> int:
    parser = argparse.ArgumentParser(description="Insere movimentos ausentes em lote de extrato apos correcao do parser.")
    parser.add_argument("lot_id", type=int)
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--provider", default="pymupdf")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    previous_provider = os.environ.get("POWER_CHURCH_PDF_PROVIDER")
    os.environ["POWER_CHURCH_PDF_PROVIDER"] = args.provider
    db = PowerChurchDB(Path(args.db))
    try:
        lot = db.get_statement_lot(args.lot_id)
        if lot is None:
            raise SystemExit(f"Lote {args.lot_id} nao encontrado.")
        layout_code = normalize_query(lot["layout_codigo"]).upper()
        missing = _missing_entries(db, args.lot_id, layout_code, Path(str(lot["caminho_arquivo"])))
        print(f"lote={args.lot_id} layout={layout_code} ausentes={len(missing)}")
        for item in missing:
            plan = item["plan"]
            print(
                f"- ordem={plan.order_in_file} pagina={plan.page_number} data={plan.received_on} "
                f"valor={plan.amount:.2f} prefixo={plan.prefix} origem={plan.source_name} docto={plan.bank_document}"
            )
        if not args.apply or not missing:
            return 0
        organization_name_row = db.conn.execute(
            "SELECT nome FROM organizacoes WHERE id = ? LIMIT 1",
            (moneyless_int(lot["organizacao_id"]),),
        ).fetchone()
        organization_name = str(organization_name_row["nome"] if organization_name_row else "")
        people_cache = db.people_for_pix_matching(moneyless_int(lot["organizacao_id"]))
        rules_by_code = {str(row["codigo_centavos"]): row for row in db.pix_rules(moneyless_int(lot["organizacao_id"])) if moneyless_int(row["ativo"])}
        before_lot = db.get_statement_lot(args.lot_id)
        movement_ids = [
            _insert_missing_statement_entry(db, lot, item, people_cache, rules_by_code, organization_name)
            for item in missing
        ]
        created_financial = db.ensure_statement_financial_entries(args.lot_id)
        db.refresh_statement_lot_status(args.lot_id)
        after_lot = db.get_statement_lot(args.lot_id)
        db.write_audit_log(
            moneyless_int(lot["organizacao_id"]),
            "backfill_lote_extrato_parser_corrigido",
            "extrato_lotes",
            args.lot_id,
            dict(before_lot) if before_lot else None,
            {
                **(dict(after_lot) if after_lot else {"id": args.lot_id}),
                "movimentos_inseridos": movement_ids,
                "contribuicoes_criadas_ou_sincronizadas": created_financial,
            },
        )
        db.conn.commit()
        print(f"aplicado movimentos={movement_ids} financeiro={created_financial}")
        return 0
    finally:
        db.close()
        if previous_provider is None:
            os.environ.pop("POWER_CHURCH_PDF_PROVIDER", None)
        else:
            os.environ["POWER_CHURCH_PDF_PROVIDER"] = previous_provider


if __name__ == "__main__":
    raise SystemExit(main())

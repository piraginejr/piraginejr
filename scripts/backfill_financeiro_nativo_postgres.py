from __future__ import annotations

import os
import sqlite3
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DJANGO_ROOT = ROOT / "power_church_django"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(DJANGO_ROOT) not in sys.path:
    sys.path.insert(0, str(DJANGO_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "power_church_site.settings")

import django

django.setup()

from django.db import transaction

from power_church_django.apps.contributions.models import (
    NativeAuxContributor,
    NativeContribution,
    NativeEnvelope,
    NativeEnvelopeItem,
    NativeEnvelopeLot,
    ReceiptItemSnapshot,
    ReceiptSnapshot,
)
from power_church_django.apps.people.models import PersonSnapshot
from power_church_django.services.contributions_native import (
    _contributor_cache_from_aux,
    _contributor_cache_from_person,
    _sync_person_contribution_snapshot,
)
from power_church_django.services.legacy import legacy_db_path, table_exists
from power_church_core.normalization import normalize_match_name, normalize_query


def _connect_legacy() -> sqlite3.Connection:
    conn = sqlite3.connect(legacy_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def _money(value: object) -> Decimal:
    return Decimal(str(value or 0))


def _safe_int(value: object) -> int | None:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return None
    return parsed or None


def _contributor_lookup(conn: sqlite3.Connection) -> dict[int, dict[str, object]]:
    if not table_exists(conn, "contribuintes"):
        return {}
    return {
        int(row["id"]): dict(row)
        for row in conn.execute(
            """
            SELECT id, organizacao_id, pessoa_id, nome, documento_principal, tipo, origem, qualidade, status, observacoes
              FROM contribuintes
             WHERE ativo = 1
            """
        ).fetchall()
    }


def _legacy_cache(
    *,
    organization_id: int,
    person_id: int | None,
    contributor_id: int | None,
    contributor_rows: dict[int, dict[str, object]],
) -> dict[str, object]:
    if person_id:
        person = PersonSnapshot.objects.filter(legacy_id=int(person_id), is_active=True).first()
        if person is not None:
            cache = _contributor_cache_from_person(person)
            if contributor_id:
                row = contributor_rows.get(int(contributor_id))
                if row:
                    cache["contributor_legacy_id"] = int(contributor_id)
                    cache["contributor_name"] = str(row.get("nome") or cache["contributor_name"] or "")
                    cache["contributor_document"] = str(row.get("documento_principal") or cache["contributor_document"] or "")
                    cache["contributor_type"] = str(row.get("tipo") or cache["contributor_type"] or "")
            return cache
    if contributor_id:
        row = contributor_rows.get(int(contributor_id))
        if row is None:
            return {
                "person_legacy_id": int(person_id or 0) or None,
                "contributor_legacy_id": int(contributor_id or 0) or None,
                "native_aux_contributor_id": None,
                "contributor_source": "legacy_aux_missing",
                "contributor_name": "",
                "contributor_document": "",
                "contributor_type": "",
            }
        aux, _ = NativeAuxContributor.objects.update_or_create(
            organization_id=int(organization_id or 0),
            legacy_reference_id=int(contributor_id or 0),
            defaults={
                "person_legacy_id": int(person_id or 0) or _safe_int(row.get("pessoa_id")) or None,
                "name": normalize_query(row.get("nome")) or f"Contribuinte #{contributor_id}",
                "normalized_name": normalize_match_name(row.get("nome") or f"Contribuinte #{contributor_id}"),
                "primary_document": normalize_query(row.get("documento_principal")) or "",
                "document_type": "",
                "contributor_type": normalize_query(row.get("tipo")) or "",
                "origin": normalize_query(row.get("origem")) or "backfill_legacy_sqlite",
                "quality": normalize_query(row.get("qualidade")) or "doador",
                "status": normalize_query(row.get("status")) or "ativo",
                "notes": normalize_query(row.get("observacoes")) or "",
                "is_active": True,
            },
        )
        cache = _contributor_cache_from_aux(aux)
        if int(cache.get("person_legacy_id") or 0):
            valid_person = PersonSnapshot.objects.filter(legacy_id=int(cache.get("person_legacy_id") or 0), is_active=True).exists()
            if not valid_person:
                cache["person_legacy_id"] = None
        return cache
    return {
        "person_legacy_id": None,
        "contributor_legacy_id": None,
        "native_aux_contributor_id": None,
        "contributor_source": "",
        "contributor_name": "",
        "contributor_document": "",
        "contributor_type": "",
    }


def backfill_contributions(conn: sqlite3.Connection) -> dict[str, int]:
    contributor_rows = _contributor_lookup(conn)
    rows = conn.execute(
        """
        SELECT co.id, co.organizacao_id, co.pessoa_id, co.contribuinte_id, co.tipo_contribuicao_id,
               co.campanha_id, co.data_recebimento, co.competencia, co.competencia_ordem, co.valor,
               co.forma_recebimento_id, co.observacoes, co.status_operacional, co.ativo,
               tc.nome AS tipo_nome, ca.nome AS campanha_nome, fr.nome AS forma_nome
          FROM contribuicoes co
          LEFT JOIN tipos_contribuicao tc ON tc.id = co.tipo_contribuicao_id
          LEFT JOIN campanhas ca ON ca.id = co.campanha_id
          LEFT JOIN formas_recebimento fr ON fr.id = co.forma_recebimento_id
         WHERE co.ativo = 1
         ORDER BY co.id
        """
    ).fetchall()
    created = 0
    updated = 0
    for index, row in enumerate(rows, start=1):
        cache = _legacy_cache(
            organization_id=int(row["organizacao_id"] or 0),
            person_id=_safe_int(row["pessoa_id"]),
            contributor_id=_safe_int(row["contribuinte_id"]),
            contributor_rows=contributor_rows,
        )
        obj, was_created = NativeContribution.objects.update_or_create(
            legacy_id=int(row["id"] or 0),
            defaults={
                "organization_id": int(row["organizacao_id"] or 0),
                "person_legacy_id": int(cache.get("person_legacy_id") or 0) or None,
                "contributor_legacy_id": int(cache.get("contributor_legacy_id") or 0) or None,
                "native_aux_contributor_id": int(cache.get("native_aux_contributor_id") or 0) or None,
                "contributor_source": str(cache.get("contributor_source") or ""),
                "contributor_name": str(cache.get("contributor_name") or ""),
                "contributor_document": str(cache.get("contributor_document") or ""),
                "contributor_type": str(cache.get("contributor_type") or ""),
                "received_at_raw": str(row["data_recebimento"] or ""),
                "received_at": row["data_recebimento"] or None,
                "competence": str(row["competencia"] or ""),
                "competence_order": int(row["competencia_ordem"] or 0),
                "amount": _money(row["valor"]),
                "contribution_type_legacy_id": int(row["tipo_contribuicao_id"] or 0),
                "contribution_type_name": str(row["tipo_nome"] or ""),
                "campaign_legacy_id": int(row["campanha_id"] or 0) or None,
                "campaign_name": str(row["campanha_nome"] or ""),
                "receipt_method_legacy_id": int(row["forma_recebimento_id"] or 0) or None,
                "receipt_method_name": str(row["forma_nome"] or ""),
                "operational_status": str(row["status_operacional"] or "regular"),
                "notes": str(row["observacoes"] or ""),
                "source": "backfill_legacy_sqlite",
                "is_active": True,
                "updated_by": "backfill",
                "created_by": "backfill",
            },
        )
        _sync_person_contribution_snapshot(obj)
        if was_created:
            created += 1
        else:
            updated += 1
        if index % 500 == 0:
            print(f"Contribuicoes: {index}/{len(rows)}", flush=True)
    return {"rows": len(rows), "created": created, "updated": updated}


def backfill_envelopes(conn: sqlite3.Connection) -> dict[str, int]:
    contributor_rows = _contributor_lookup(conn)
    lot_rows = conn.execute(
        """
        SELECT id, organizacao_id, nome, competencia, competencia_ordem, data_padrao_recebimento,
               origem_operacional_padrao, tipo_contribuicao_id_padrao, campanha_id_padrao,
               forma_recebimento_id_padrao, caminho_pasta, observacoes, status
          FROM envelope_lotes
         ORDER BY id
        """
    ).fetchall()
    lot_created = 0
    lot_updated = 0
    for index, row in enumerate(lot_rows, start=1):
        _, was_created = NativeEnvelopeLot.objects.update_or_create(
            legacy_id=int(row["id"] or 0),
            defaults={
                "organization_id": int(row["organizacao_id"] or 0),
                "name": str(row["nome"] or ""),
                "competence": str(row["competencia"] or ""),
                "competence_order": int(row["competencia_ordem"] or 0),
                "default_received_at": row["data_padrao_recebimento"] or None,
                "default_received_at_raw": str(row["data_padrao_recebimento"] or ""),
                "default_source": str(row["origem_operacional_padrao"] or ""),
                "default_contribution_type_legacy_id": int(row["tipo_contribuicao_id_padrao"] or 0) or None,
                "default_campaign_legacy_id": int(row["campanha_id_padrao"] or 0) or None,
                "default_receipt_method_legacy_id": int(row["forma_recebimento_id_padrao"] or 0) or None,
                "folder_path": str(row["caminho_pasta"] or ""),
                "notes": str(row["observacoes"] or ""),
                "status": str(row["status"] or ""),
                "is_active": True,
                "updated_by": "backfill",
                "created_by": "backfill",
            },
        )
        if was_created:
            lot_created += 1
        else:
            lot_updated += 1
        print(f"Lotes de envelope: {index}/{len(lot_rows)}", flush=True)
    envelope_rows = conn.execute(
        """
        SELECT e.id, e.organizacao_id, e.lote_id, e.competencia, e.competencia_ordem, e.data_recebimento,
               e.total_informado, e.total_linhas, e.nome_informado, e.telefone_informado, e.endereco_informado,
               e.pessoa_id, e.contribuinte_id, e.forma_recebimento_id, e.origem_operacional, e.status,
               e.observacoes, e.justificativa, e.nome_arquivo_original, e.imagem_hash, e.imagem_content_type,
               e.imagem_tamanho, e.caminho_imagem, e.rastreio_forma_identificada, e.rastreio_banco_operadora,
               e.rastreio_numero_cheque, e.rastreio_numero_operacao, e.rastreio_nsu_tid, e.rastreio_ultimos_digitos_cartao,
               e.rastreio_data_operacao, e.rastreio_valor_operacao, e.rastreio_status_conciliacao, e.rastreio_observacoes,
               e.ativo, l.nome AS lote_nome, fr.nome AS forma_nome
          FROM envelopes e
          LEFT JOIN envelope_lotes l ON l.id = e.lote_id
          LEFT JOIN formas_recebimento fr ON fr.id = e.forma_recebimento_id
         WHERE e.ativo = 1
         ORDER BY e.id
        """
    ).fetchall()
    env_created = 0
    env_updated = 0
    for index, row in enumerate(envelope_rows, start=1):
        cache = _legacy_cache(
            organization_id=int(row["organizacao_id"] or 0),
            person_id=_safe_int(row["pessoa_id"]),
            contributor_id=_safe_int(row["contribuinte_id"]),
            contributor_rows=contributor_rows,
        )
        _, was_created = NativeEnvelope.objects.update_or_create(
            legacy_id=int(row["id"] or 0),
            defaults={
                "organization_id": int(row["organizacao_id"] or 0),
                "native_lot_legacy_id": int(row["lote_id"] or 0) or None,
                "lot_name": str(row["lote_nome"] or ""),
                "competence": str(row["competencia"] or ""),
                "competence_order": int(row["competencia_ordem"] or 0),
                "received_at": row["data_recebimento"] or None,
                "received_at_raw": str(row["data_recebimento"] or ""),
                "total_informed": _money(row["total_informado"]),
                "total_lines": _money(row["total_linhas"]),
                "informed_name": str(row["nome_informado"] or ""),
                "informed_phone": str(row["telefone_informado"] or ""),
                "informed_address": str(row["endereco_informado"] or ""),
                "person_legacy_id": int(cache.get("person_legacy_id") or 0) or None,
                "contributor_legacy_id": int(cache.get("contributor_legacy_id") or 0) or None,
                "native_aux_contributor_id": int(cache.get("native_aux_contributor_id") or 0) or None,
                "receipt_method_legacy_id": int(row["forma_recebimento_id"] or 0) or None,
                "receipt_method_name": str(row["forma_nome"] or ""),
                "operational_status": "regular",
                "source": str(row["origem_operacional"] or ""),
                "status": str(row["status"] or ""),
                "notes": str(row["observacoes"] or ""),
                "justification": str(row["justificativa"] or ""),
                "image_original_name": str(row["nome_arquivo_original"] or ""),
                "image_hash": str(row["imagem_hash"] or ""),
                "image_content_type": str(row["imagem_content_type"] or ""),
                "image_size": int(row["imagem_tamanho"] or 0),
                "image_path": str(row["caminho_imagem"] or ""),
                "traceability_form": str(row["rastreio_forma_identificada"] or ""),
                "traceability_provider": str(row["rastreio_banco_operadora"] or ""),
                "traceability_check_number": str(row["rastreio_numero_cheque"] or ""),
                "traceability_operation_number": str(row["rastreio_numero_operacao"] or ""),
                "traceability_nsu_tid": str(row["rastreio_nsu_tid"] or ""),
                "traceability_card_suffix": str(row["rastreio_ultimos_digitos_cartao"] or ""),
                "traceability_operation_date": row["rastreio_data_operacao"] or None,
                "traceability_operation_date_raw": str(row["rastreio_data_operacao"] or ""),
                "traceability_operation_amount": _money(row["rastreio_valor_operacao"]) if row["rastreio_valor_operacao"] not in (None, "") else None,
                "traceability_status": str(row["rastreio_status_conciliacao"] or ""),
                "traceability_notes": str(row["rastreio_observacoes"] or ""),
                "is_active": True,
                "updated_by": "backfill",
                "created_by": "backfill",
            },
        )
        if was_created:
            env_created += 1
        else:
            env_updated += 1
        if index % 50 == 0:
            print(f"Envelopes: {index}/{len(envelope_rows)}", flush=True)
    item_rows = conn.execute(
        """
        SELECT ei.id, ei.envelope_id, ei.pessoa_id, ei.contribuinte_id, ei.tipo_contribuicao_id, ei.campanha_id,
               ei.valor, ei.observacoes, ei.contribuicao_id, ei.ativo,
               tc.nome AS tipo_nome, ca.nome AS campanha_nome, ct.nome AS contribuinte_nome, ct.documento_principal
          FROM envelope_itens ei
          LEFT JOIN tipos_contribuicao tc ON tc.id = ei.tipo_contribuicao_id
          LEFT JOIN campanhas ca ON ca.id = ei.campanha_id
          LEFT JOIN contribuintes ct ON ct.id = ei.contribuinte_id
         WHERE ei.ativo = 1
         ORDER BY ei.id
        """
    ).fetchall()
    item_created = 0
    item_updated = 0
    for index, row in enumerate(item_rows, start=1):
        cache = _legacy_cache(
            organization_id=int(
                NativeEnvelope.objects.filter(legacy_id=int(row["envelope_id"] or 0)).values_list("organization_id", flat=True).first() or 0
            ),
            person_id=_safe_int(row["pessoa_id"]),
            contributor_id=_safe_int(row["contribuinte_id"]),
            contributor_rows=contributor_rows,
        )
        envelope = NativeEnvelope.objects.filter(legacy_id=int(row["envelope_id"] or 0)).first()
        if envelope is None:
            continue
        _, was_created = NativeEnvelopeItem.objects.update_or_create(
            legacy_id=int(row["id"] or 0),
            defaults={
                "envelope": envelope,
                "person_legacy_id": int(cache.get("person_legacy_id") or 0) or None,
                "contributor_legacy_id": int(cache.get("contributor_legacy_id") or 0) or None,
                "native_aux_contributor_id": int(cache.get("native_aux_contributor_id") or 0) or None,
                "contributor_name": str(row["contribuinte_nome"] or cache.get("contributor_name") or ""),
                "contributor_document": str(row["documento_principal"] or cache.get("contributor_document") or ""),
                "contribution_legacy_id": int(row["contribuicao_id"] or 0) or None,
                "contribution_type_legacy_id": int(row["tipo_contribuicao_id"] or 0),
                "contribution_type_name": str(row["tipo_nome"] or ""),
                "campaign_legacy_id": int(row["campanha_id"] or 0) or None,
                "campaign_name": str(row["campanha_nome"] or ""),
                "amount": _money(row["valor"]),
                "notes": str(row["observacoes"] or ""),
                "is_active": True,
            },
        )
        if was_created:
            item_created += 1
        else:
            item_updated += 1
        if index % 100 == 0:
            print(f"Itens de envelope: {index}/{len(item_rows)}", flush=True)
    return {
        "lots": len(lot_rows),
        "lot_created": lot_created,
        "lot_updated": lot_updated,
        "envelopes": len(envelope_rows),
        "envelope_created": env_created,
        "envelope_updated": env_updated,
        "items": len(item_rows),
        "item_created": item_created,
        "item_updated": item_updated,
    }


def backfill_receipts(conn: sqlite3.Connection) -> dict[str, int]:
    receipt_rows = conn.execute(
        """
        SELECT r.id, r.organizacao_id, r.pessoa_id, r.numero, r.data_emissao, r.periodo_inicio, r.periodo_fim,
               r.valor_total, r.status, r.observacoes, r.cancelado_em,
               o.nome AS organizacao_nome, o.nome_fantasia AS organizacao_fantasia,
               p.nome AS pessoa_nome, p.codigo_interno, p.cpf, p.email_principal, p.telefone_principal
          FROM recibos r
          JOIN pessoas p ON p.id = r.pessoa_id
          JOIN organizacoes o ON o.id = r.organizacao_id
         ORDER BY r.id
        """
    ).fetchall()
    receipt_created = 0
    receipt_updated = 0
    receipt_index: dict[int, ReceiptSnapshot] = {}
    for index, row in enumerate(receipt_rows, start=1):
        snapshot, was_created = ReceiptSnapshot.objects.update_or_create(
            legacy_id=int(row["id"] or 0),
            defaults={
                "organization_id": int(row["organizacao_id"] or 0),
                "person_legacy_id": int(row["pessoa_id"] or 0),
                "receipt_number": str(row["numero"] or ""),
                "status": str(row["status"] or ""),
                "organization_name": str(row["organizacao_fantasia"] or row["organizacao_nome"] or ""),
                "person_name": str(row["pessoa_nome"] or ""),
                "person_code": str(row["codigo_interno"] or ""),
                "person_cpf": str(row["cpf"] or ""),
                "person_email": str(row["email_principal"] or ""),
                "person_phone": str(row["telefone_principal"] or ""),
                "emission_date": row["data_emissao"] or None,
                "emission_date_raw": str(row["data_emissao"] or ""),
                "period_start": row["periodo_inicio"] or None,
                "period_start_raw": str(row["periodo_inicio"] or ""),
                "period_end": row["periodo_fim"] or None,
                "period_end_raw": str(row["periodo_fim"] or ""),
                "total_value": _money(row["valor_total"]),
                "notes": str(row["observacoes"] or ""),
                "is_cancelled": bool(row["cancelado_em"]),
            },
        )
        receipt_index[int(snapshot.legacy_id or 0)] = snapshot
        if was_created:
            receipt_created += 1
        else:
            receipt_updated += 1
        if index % 250 == 0:
            print(f"Recibos: {index}/{len(receipt_rows)}", flush=True)

    item_rows = conn.execute(
        """
        SELECT ri.id, ri.recibo_id, ri.contribuicao_id, ri.valor,
               co.contribuinte_id, co.data_recebimento, co.competencia, co.observacoes,
               tc.nome AS tipo_nome, fr.nome AS forma_nome
          FROM recibo_itens ri
          JOIN contribuicoes co ON co.id = ri.contribuicao_id
          LEFT JOIN tipos_contribuicao tc ON tc.id = co.tipo_contribuicao_id
          LEFT JOIN formas_recebimento fr ON fr.id = co.forma_recebimento_id
         ORDER BY ri.id
        """
    ).fetchall()
    item_created = 0
    item_updated = 0
    for index, row in enumerate(item_rows, start=1):
        receipt = receipt_index.get(int(row["recibo_id"] or 0))
        if receipt is None:
            continue
        native = NativeContribution.objects.filter(legacy_id=int(row["contribuicao_id"] or 0)).only("contributor_legacy_id").first()
        _, was_created = ReceiptItemSnapshot.objects.update_or_create(
            legacy_id=int(row["id"] or 0),
            defaults={
                "receipt": receipt,
                "contribution_legacy_id": int(row["contribuicao_id"] or 0),
                "contributor_legacy_id": int(
                    row["contribuinte_id"]
                    or (native.contributor_legacy_id if native is not None else 0)
                    or 0
                )
                or None,
                "received_at": row["data_recebimento"] or None,
                "received_at_raw": str(row["data_recebimento"] or ""),
                "competence": str(row["competencia"] or ""),
                "contribution_type_name": str(row["tipo_nome"] or ""),
                "receipt_method_name": str(row["forma_nome"] or ""),
                "notes": str(row["observacoes"] or ""),
                "amount": _money(row["valor"]),
            },
        )
        if was_created:
            item_created += 1
        else:
            item_updated += 1
        if index % 500 == 0:
            print(f"Itens de recibo: {index}/{len(item_rows)}", flush=True)
    return {
        "receipts": len(receipt_rows),
        "receipt_created": receipt_created,
        "receipt_updated": receipt_updated,
        "items": len(item_rows),
        "item_created": item_created,
        "item_updated": item_updated,
    }


def main() -> int:
    with _connect_legacy() as conn:
        print("Iniciando backfill de contribuicoes...", flush=True)
        contribution_stats = backfill_contributions(conn)
        print("Iniciando backfill de envelopes...", flush=True)
        envelope_stats = backfill_envelopes(conn)
        print("Iniciando backfill de recibos...", flush=True)
        receipt_stats = backfill_receipts(conn)
    print("Backfill financeiro nativo concluido.")
    print(contribution_stats)
    print(envelope_stats)
    print(receipt_stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

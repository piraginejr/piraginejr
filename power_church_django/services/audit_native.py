from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from django.core.paginator import Paginator
from django.db.models import Q
from django.utils import timezone

from power_church_core.normalization import normalize_query
from power_church_django.apps.audit.models import AuditEvent
from power_church_django.apps.imports.models import StatementImportPilotMovement
from power_church_django.apps.people.models import PersonSnapshot
from power_church_core.normalization import valid_cpf
from power_church_django.services.runtime_formatting import format_status, status_sigla
from power_church_django.services.smart_audit import classify_import_pendency, summarize_smart_audit


def search_receipt_people_postgres(q: str = "", limit: int = 20) -> list[dict[str, Any]]:
    query = normalize_query(q)
    digits = "".join(ch for ch in query if ch.isdigit())
    if len(query) < 2 and len(digits) < 2:
        return []
    queryset = PersonSnapshot.objects.filter(is_active=True)
    if query:
        queryset = queryset.filter(
            Q(normalized_name__icontains=query.lower())
            | Q(internal_code__icontains=query)
            | Q(cpf__icontains=digits or query)
            | Q(normalized_email__icontains=query.lower())
        )
    rows = list(queryset.order_by("normalized_name", "legacy_id")[: max(1, min(int(limit or 20), 80))])
    return [
        {
            "id": int(row.legacy_id or 0),
            "nome": normalize_query(row.name),
            "codigo": normalize_query(row.internal_code),
            "codigo_interno": normalize_query(row.internal_code),
            "cpf": normalize_query(row.cpf),
            "status": format_status(row.status),
            "status_label": format_status(row.status),
            "sigla": status_sigla(row.status, True),
            "email": normalize_query(row.primary_email),
            "telefone": normalize_query(row.primary_phone),
        }
        for row in rows
    ]


def operational_audit_postgres(tipo: str = "", severidade: str = "", page: int = 1, page_size: int = 200) -> dict[str, Any]:
    tipo = normalize_query(tipo)
    severidade = normalize_query(severidade)
    page = max(1, int(page or 1))
    page_size = min(max(50, int(page_size or 200)), 1000)
    items: list[dict[str, Any]] = []

    people = list(PersonSnapshot.objects.filter(is_active=True).only("legacy_id", "name", "internal_code", "cpf", "primary_email", "status"))
    cpf_groups = Counter("".join(ch for ch in (row.cpf or "") if ch.isdigit()) for row in people if "".join(ch for ch in (row.cpf or "") if ch.isdigit()))
    email_groups = Counter(normalize_query(row.primary_email).lower() for row in people if normalize_query(row.primary_email))
    person_by_id = {int(row.legacy_id or 0): row for row in people}

    for row in people:
        cpf_digits = "".join(ch for ch in (row.cpf or "") if ch.isdigit())
        if cpf_digits and not valid_cpf(cpf_digits):
            items.append(
                {
                    "id": f"cpf:{row.legacy_id}",
                    "tipo": "cpf_invalido",
                    "severidade": "aviso",
                    "descricao": "CPF com digitos invalidos no cadastro ativo.",
                    "acao_sugerida": "Conferir o documento correto na ficha da pessoa.",
                    "numero_linha": "",
                    "pessoa_id": int(row.legacy_id or 0),
                    "nome": normalize_query(row.name),
                    "codigo_interno": normalize_query(row.internal_code),
                    "status": normalize_query(row.status),
                    "resolvivel": True,
                    "origem": "postgres",
                }
            )
        if cpf_digits and cpf_groups.get(cpf_digits, 0) > 1:
            items.append(
                {
                    "id": f"cpf_dup:{row.legacy_id}",
                    "tipo": "cpf_duplicado",
                    "severidade": "aviso",
                    "descricao": "CPF repetido em mais de uma ficha ativa.",
                    "acao_sugerida": "Revisar duplicidade e considerar mesclagem assistida.",
                    "numero_linha": "",
                    "pessoa_id": int(row.legacy_id or 0),
                    "nome": normalize_query(row.name),
                    "codigo_interno": normalize_query(row.internal_code),
                    "status": normalize_query(row.status),
                    "resolvivel": True,
                    "origem": "postgres",
                }
            )
        email_key = normalize_query(row.primary_email).lower()
        if email_key and email_groups.get(email_key, 0) > 1:
            items.append(
                {
                    "id": f"email_dup:{row.legacy_id}",
                    "tipo": "email_duplicado",
                    "severidade": "info",
                    "descricao": "E-mail compartilhado por mais de uma ficha ativa.",
                    "acao_sugerida": "Conferir se ha familia, duplicidade ou e-mail corporativo compartilhado.",
                    "numero_linha": "",
                    "pessoa_id": int(row.legacy_id or 0),
                    "nome": normalize_query(row.name),
                    "codigo_interno": normalize_query(row.internal_code),
                    "status": normalize_query(row.status),
                    "resolvivel": True,
                    "origem": "postgres",
                }
            )

    pending_movements = list(
        StatementImportPilotMovement.objects.filter(
            review_status__in=["revisar_pessoa", "revisar_destinacao", "revisar_duplicidade"],
        ).order_by("lot_id", "movement_date", "id")[:2000]
    )
    for row in pending_movements:
        if str(row.review_status or "") == "revisar_duplicidade" and int(row.imported_contribution_legacy_id or 0):
            continue
        person = person_by_id.get(int(row.resolved_person_legacy_id or row.suggested_person_legacy_id or 0))
        item = {
            "id": f"mov:{int(row.pk or 0)}",
            "tipo": str(row.review_status or ""),
            "severidade": "aviso",
            "descricao": f"Movimento bancario {row.amount} exige revisao operacional.",
            "acao_sugerida": "Revisar pessoa, destinacao ou duplicidade antes do encerramento do lote.",
            "numero_linha": "",
            "pessoa_id": int(person.legacy_id or 0) if person else 0,
            "nome": normalize_query(person.name) if person else (normalize_query(row.source_name) or normalize_query(row.origin_label) or normalize_query(row.bank_document) or ""),
            "codigo_interno": normalize_query(person.internal_code) if person else "",
            "status": normalize_query(person.status) if person else "",
            "resolvivel": True,
            "origem": "extrato_postgres",
        }
        item["smart_audit"] = classify_import_pendency(item)
        items.append(item)

    if tipo:
        items = [item for item in items if normalize_query(item.get("tipo")) == tipo]
    if severidade:
        items = [item for item in items if normalize_query(item.get("severidade")) == severidade]

    grouped_summary: dict[tuple[str, str], int] = {}
    for item in items:
        key = (str(item.get("tipo") or ""), str(item.get("severidade") or ""))
        grouped_summary[key] = grouped_summary.get(key, 0) + 1
    severity_rank = {"aviso": 0, "info": 1}
    summary_rows = [{"tipo": key[0], "severidade": key[1], "quantidade": value} for key, value in grouped_summary.items()]
    summary_rows.sort(key=lambda row: (severity_rank.get(str(row["severidade"]), 9), -int(row["quantidade"] or 0), str(row["tipo"])))

    people_by_id: dict[int, dict[str, Any]] = {}
    for item in items:
        person_id = int(item.get("pessoa_id") or 0)
        if not person_id:
            continue
        bucket = people_by_id.setdefault(
            person_id,
            {
                "pessoa_id": person_id,
                "nome": item.get("nome") or "",
                "codigo_interno": item.get("codigo_interno") or "",
                "status": item.get("status") or "",
                "avisos": 0,
                "infos": 0,
                "total": 0,
                "tipos_set": set(),
            },
        )
        if item.get("severidade") == "aviso":
            bucket["avisos"] += 1
        else:
            bucket["infos"] += 1
        bucket["total"] += 1
        bucket["tipos_set"].add(str(item.get("tipo") or ""))
    people_rows = [
        {
            "pessoa_id": bucket["pessoa_id"],
            "nome": bucket["nome"],
            "codigo_interno": bucket["codigo_interno"],
            "status": format_status(bucket["status"]),
            "avisos": bucket["avisos"],
            "infos": bucket["infos"],
            "total": bucket["total"],
            "tipos": ", ".join(sorted(bucket["tipos_set"])),
        }
        for bucket in people_by_id.values()
    ]
    people_rows.sort(key=lambda row: (-int(row["avisos"]), -int(row["total"]), str(row["nome"]).casefold()))

    total_items = len(items)
    offset = (page - 1) * page_size
    paged_items = items[offset : offset + page_size]
    total_pages = max(1, (total_items + page_size - 1) // page_size)
    return {
        "tipo": tipo,
        "severidade": severidade,
        "summary": summary_rows,
        "smart_summary": summarize_smart_audit(items),
        "people": people_rows,
        "items": [
            {
                "id": item.get("id"),
                "tipo": item.get("tipo") or "",
                "severidade": item.get("severidade") or "",
                "descricao": item.get("descricao") or "",
                "acao_sugerida": item.get("acao_sugerida") or "",
                "smart_category": (item.get("smart_audit") or {}).get("category_label") or "",
                "smart_risk": (item.get("smart_audit") or {}).get("risk_label") or "",
                "smart_confidence": (item.get("smart_audit") or {}).get("confidence_label") or "",
                "smart_operator_hint": (item.get("smart_audit") or {}).get("operator_hint") or "",
                "numero_linha": item.get("numero_linha") or "",
                "pessoa_id": int(item.get("pessoa_id") or 0),
                "nome": item.get("nome") or "",
                "codigo": item.get("codigo_interno") or "",
                "system_id": f"ID-{int(item.get('pessoa_id') or 0):06d}" if int(item.get("pessoa_id") or 0) else "",
                "status": format_status(item.get("status")),
                "resolvivel": bool(item.get("resolvivel")),
                "origem": item.get("origem") or "",
            }
            for item in paged_items
        ],
        "total": total_items,
        "shown": len(paged_items),
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "has_previous": page > 1,
        "has_next": page < total_pages,
        "previous_page": max(1, page - 1),
        "next_page": min(total_pages, page + 1),
        "technical_count": int(AuditEvent.objects.count()),
    }


def technical_audit_postgres(action: str = "", table: str = "", page: int = 1, page_size: int = 120) -> dict[str, Any]:
    action = normalize_query(action)
    table = normalize_query(table)
    page = max(1, int(page or 1))
    page_size = min(max(50, int(page_size or 120)), 1000)
    queryset = AuditEvent.objects.all()
    if action:
        queryset = queryset.filter(action=action)
    if table:
        queryset = queryset.filter(table_name=table)
    paginator = Paginator(queryset, page_size)
    page_obj = paginator.get_page(page)
    actions = list(AuditEvent.objects.order_by("action").values_list("action", flat=True).distinct()[:60])
    tables = list(AuditEvent.objects.order_by("table_name").values_list("table_name", flat=True).distinct()[:60])
    return {
        "action": action,
        "table": table,
        "items": [
            {
                "id": int(event.pk or 0),
                "usuario_id": event.actor or "",
                "acao": event.action,
                "tabela": event.table_name,
                "registro_id": event.record_id or "",
                "criado_em": timezone.localtime(event.created_at).strftime("%d/%m/%Y %H:%M"),
            }
            for event in page_obj.object_list
        ],
        "actions": actions,
        "tables": tables,
        "total": paginator.count,
        "shown": len(page_obj.object_list),
        "page": page_obj.number,
        "page_size": page_size,
        "total_pages": paginator.num_pages or 1,
        "has_previous": page_obj.has_previous(),
        "has_next": page_obj.has_next(),
        "previous_page": page_obj.previous_page_number() if page_obj.has_previous() else 1,
        "next_page": page_obj.next_page_number() if page_obj.has_next() else paginator.num_pages or 1,
    }

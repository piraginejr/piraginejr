from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
import os
import re
import sqlite3
import time
import traceback
from typing import Any, Iterable

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.db import connection, transaction
from django.db.models import CharField, Q, Sum, Value
from django.db.models.functions import Cast, Coalesce, Lower
from django.test import Client, override_settings
from django.urls import URLPattern, URLResolver, get_resolver
from django.utils import timezone

from power_church_core.normalization import normalize_match_name
from power_church_django.apps.audit.models import AuditEvent
from power_church_django.apps.contributions.models import (
    NativeAuxContributor,
    NativeContribution,
    NativeEnvelope,
    NativeEnvelopeItem,
    NativeEnvelopeLot,
    NativeEnvelopeProfileUpdate,
    ReceiptDispatch,
    ReceiptEmailTemplate,
    ReceiptItemSnapshot,
    ReceiptSnapshot,
)
from power_church_django.apps.imports.models import StatementImportPilotLot, StatementImportPilotMovement
from power_church_django.apps.people.models import (
    NativePeopleImportLot,
    NativePeopleImportPending,
    PersonAddressSnapshot,
    PersonContributionSnapshot,
    PersonProfileSnapshot,
    PersonSecurePurgeSnapshot,
    PersonSecureTrashSnapshot,
    PersonSnapshot,
)
from power_church_django.services.access_control import access_control_snapshot, user_has_module_permission
from power_church_django.services.bank_parser_regression import run_bank_parser_regression_checks
from power_church_django.services.mail_dispatch import MailAttachment, graph_config_snapshot, send_email_message
from power_church_django.services.pdf_reports import contribution_period_pdf, receipt_pdf
from power_church_django.services.photos import photo_dir


ACCENTED_CHARS = "áàâãéêíóôõúçÁÀÂÃÉÊÍÓÔÕÚÇ"
SUSPICIOUS_MOJIBAKE_TOKENS = ("Ã", "Â", "├", "┬", "�")
WEB_PREFIXES = ("/", "/people/", "/contributors/", "/contributions/", "/receipts/", "/imports/", "/audit/", "/reports/")
SAFE_ROUTE_PREFIXES = ("", "people/", "contributors/", "contributions/", "receipts/", "imports/", "audit/", "reports/")
PLACEHOLDER_RE = re.compile(r"<(?:(?P<converter>[^:>]+):)?(?P<name>[^>]+)>")


@dataclass(slots=True)
class EntitySamples:
    user_label: str = "sem_usuario"
    user_id: int = 0
    person_ids: list[int] = field(default_factory=list)
    person_names: dict[int, str] = field(default_factory=dict)
    person_queries: list[str] = field(default_factory=list)
    person_statuses: list[str] = field(default_factory=list)
    person_cities: list[str] = field(default_factory=list)
    accented_people: list[tuple[int, str]] = field(default_factory=list)
    contribution_ids: list[int] = field(default_factory=list)
    contribution_person_ids: list[int] = field(default_factory=list)
    contribution_competences: list[str] = field(default_factory=list)
    receipt_ids: list[int] = field(default_factory=list)
    receipt_person_ids: list[int] = field(default_factory=list)
    receipt_queries: list[str] = field(default_factory=list)
    envelope_ids: list[int] = field(default_factory=list)
    envelope_image_ids: list[int] = field(default_factory=list)
    envelope_lot_ids: list[int] = field(default_factory=list)
    envelope_launch_ids: list[int] = field(default_factory=list)
    envelope_edit_ids: list[int] = field(default_factory=list)
    envelope_profile_update_ids: list[int] = field(default_factory=list)
    contributor_ids: list[int] = field(default_factory=list)
    people_import_lot_ids: list[int] = field(default_factory=list)
    statement_lot_ids: list[int] = field(default_factory=list)
    statement_movement_ids: list[int] = field(default_factory=list)
    report_destination_keys: list[str] = field(default_factory=list)
    date_ranges: list[tuple[str, str]] = field(default_factory=list)
    years: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RouteInfo:
    path_template: str
    route_name: str
    namespace: str
    app_name: str
    lookup_str: str


@dataclass(slots=True)
class AuditResult:
    item: str
    result: str
    probable_area: str
    url: str = ""
    user_label: str = ""
    status_code: int = 0
    response_time_ms: int = 0
    method: str = "GET"
    route_name: str = ""
    namespace: str = ""
    app_name: str = ""
    view_name: str = ""
    template_names: str = ""
    details: str = ""
    error: str = ""
    traceback_summary: str = ""
    probable_postgres: bool = False
    probable_encoding: bool = False
    probable_missing_attr: bool = False
    target: str = ""


def run_regression_audit(*, stdout: Any | None = None) -> tuple[Path, list[AuditResult]]:
    started_at = timezone.localtime()
    results: list[AuditResult] = []
    samples = _collect_samples()
    routes = _discover_web_routes()
    route_inventory = _route_inventory(routes)
    report_dir = Path(getattr(settings, "REPO_ROOT", Path(settings.BASE_DIR).parent)) / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)

    allowed_hosts = list(dict.fromkeys([*getattr(settings, "ALLOWED_HOSTS", []), "testserver", "localhost", "127.0.0.1"]))
    with override_settings(ALLOWED_HOSTS=allowed_hosts):
        anonymous_client = Client(raise_request_exception=False)
        authenticated_client = Client(raise_request_exception=False)
        user = get_user_model().objects.filter(is_active=True).order_by("-is_superuser", "-is_staff", "id").first()
        if user is not None:
            authenticated_client.force_login(user)

        _record_runtime_checks(results, samples=samples)
        _record_bank_parser_regression_checks(results, samples=samples)
        _record_totals_and_consistency_checks(results, samples=samples)
        _record_file_and_attachment_checks(results, samples=samples)
        _record_email_checks(results, samples=samples)
        _record_operator_scenarios(
            results,
            anonymous_client=anonymous_client,
            authenticated_client=authenticated_client,
            user_available=user is not None,
            samples=samples,
        )
        _record_safe_post_checks(results, user_available=user is not None, samples=samples)
        _record_profile_checks(results, samples=samples)
        _record_discovered_route_checks(
            results,
            user_available=user is not None,
            samples=samples,
            routes=routes,
        )
        _record_query_checks(results, samples=samples)
        _record_performance_findings(results)

    finished_at = timezone.localtime()
    report_path = report_dir / f"regression_audit_{started_at.strftime('%Y%m%d_%H%M%S')}.md"
    report_path.write_text(
        _render_markdown_report(
            results=results,
            samples=samples,
            route_inventory=route_inventory,
            started_at=started_at,
            finished_at=finished_at,
        ),
        encoding="utf-8",
    )
    if stdout is not None:
        summary = _result_summary(results)
        stdout.write(f"Relatorio salvo em {report_path}")
        stdout.write(
            f"Resumo: {summary['total']} verificacoes, "
            f"{summary['OK']} OK, {summary['FAIL']} FAIL, {summary['WARN']} WARN, {summary['SKIP']} SKIP"
        )
    return report_path, results


def _collect_samples() -> EntitySamples:
    sample = EntitySamples()
    user = get_user_model().objects.filter(is_active=True).order_by("-is_superuser", "-is_staff", "id").first()
    if user is not None:
        sample.user_label = str(user.username or f"user-{user.pk}")
        sample.user_id = int(user.pk or 0)

    people = list(PersonSnapshot.objects.filter(is_active=True).order_by("normalized_name", "legacy_id")[:12])
    sample.person_ids = [int(person.legacy_id or 0) for person in people[:5] if int(person.legacy_id or 0)]
    sample.person_names = {int(person.legacy_id or 0): str(person.name or "") for person in people if int(person.legacy_id or 0)}
    sample.person_queries = _unique_preserve_order(
        [_query_fragment(person.name or "") for person in people if _query_fragment(person.name or "")]
    )[:5]
    sample.person_statuses = _unique_preserve_order(
        [str(person.status or "") for person in people if str(person.status or "").strip()]
    )[:3]
    cities = list(
        PersonAddressSnapshot.objects.exclude(city="")
        .order_by("city", "person_id", "legacy_id")
        .values_list("city", flat=True)[:20]
    )
    sample.person_cities = _unique_preserve_order([str(city or "") for city in cities if str(city or "").strip()])[:3]
    sample.accented_people = [
        (int(person.legacy_id or 0), str(person.name or ""))
        for person in people
        if int(person.legacy_id or 0) and any(char in ACCENTED_CHARS for char in str(person.name or ""))
    ][:3]

    contributions = list(
        NativeContribution.objects.filter(is_active=True)
        .order_by("-received_at", "-legacy_id")[:12]
    )
    sample.contribution_ids = [int(row.legacy_id or 0) for row in contributions[:5] if int(row.legacy_id or 0)]
    sample.contribution_person_ids = _unique_preserve_order(
        [int(row.person_legacy_id or 0) for row in contributions if int(row.person_legacy_id or 0)]
    )[:5]
    sample.contribution_competences = _unique_preserve_order(
        [str(row.competence or "") for row in contributions if str(row.competence or "").strip()]
    )[:3]
    sample.date_ranges = _build_date_ranges(
        [str(row.received_at_raw or "") for row in contributions if str(row.received_at_raw or "").strip()]
    )
    sample.years = _unique_preserve_order(
        [date_value[:4] for date_value, _ in sample.date_ranges if len(date_value) >= 4]
    )[:2]

    receipts = list(ReceiptSnapshot.objects.order_by("-emission_date", "-legacy_id")[:12])
    sample.receipt_ids = [int(row.legacy_id or 0) for row in receipts[:5] if int(row.legacy_id or 0)]
    sample.receipt_person_ids = _unique_preserve_order(
        [int(row.person_legacy_id or 0) for row in receipts if int(row.person_legacy_id or 0)]
    )[:5]
    sample.receipt_queries = _unique_preserve_order(
        [_query_fragment(row.person_name or "") for row in receipts if _query_fragment(row.person_name or "")]
    )[:4]

    envelopes = list(NativeEnvelope.objects.order_by("-competence_order", "-legacy_id")[:12])
    sample.envelope_ids = [int(row.legacy_id or 0) for row in envelopes[:5] if int(row.legacy_id or 0)]
    sample.envelope_image_ids = [
        int(row.legacy_id or 0)
        for row in envelopes
        if int(row.legacy_id or 0) and str(row.image_path or "").strip()
    ][:5]
    sample.envelope_launch_ids = [
        int(row.legacy_id or 0)
        for row in envelopes
        if int(row.legacy_id or 0) and str(row.status or "") in {"aguardando_digitacao", "em_digitacao"}
    ][:5]
    sample.envelope_edit_ids = [
        int(row.legacy_id or 0)
        for row in envelopes
        if int(row.legacy_id or 0) and str(row.status or "") == "lancado"
    ][:5]
    envelope_lots = list(NativeEnvelopeLot.objects.order_by("-competence_order", "-legacy_id")[:8])
    sample.envelope_lot_ids = [int(row.legacy_id or 0) for row in envelope_lots[:5] if int(row.legacy_id or 0)]
    sample.envelope_profile_update_ids = [
        int(value or 0)
        for value in NativeEnvelopeProfileUpdate.objects.order_by("-id").values_list("id", flat=True)[:5]
        if int(value or 0)
    ]

    sample.contributor_ids = [
        int(value or 0)
        for value in NativeAuxContributor.objects.order_by("name", "id").values_list("id", flat=True)[:5]
        if int(value or 0)
    ]
    sample.people_import_lot_ids = [
        int(value or 0)
        for value in NativePeopleImportLot.objects.order_by("-legacy_id").values_list("legacy_id", flat=True)[:5]
        if int(value or 0)
    ]
    sample.statement_lot_ids = [
        int(value or 0)
        for value in StatementImportPilotLot.objects.order_by("-created_at", "-id").values_list("id", flat=True)[:5]
        if int(value or 0)
    ]
    sample.statement_movement_ids = [
        int(value or 0)
        for value in StatementImportPilotMovement.objects.order_by("-updated_at", "-id").values_list("id", flat=True)[:5]
        if int(value or 0)
    ]
    sample.report_destination_keys = _collect_report_destinations()
    if not sample.person_queries:
        sample.person_queries = ["joao"]
    if not sample.receipt_queries:
        sample.receipt_queries = list(sample.person_queries)
    if not sample.date_ranges:
        sample.date_ranges = [("", "")]
    if not sample.years:
        sample.years = [""]
    return sample


def _record_bank_parser_regression_checks(results: list[AuditResult], *, samples: EntitySamples) -> None:
    for check in run_bank_parser_regression_checks():
        if check.ok:
            result = "OK"
        elif check.severity == "WARN":
            result = "WARN"
        else:
            result = "FAIL"
        results.append(
            AuditResult(
                item=f"Parser bancario - {check.name}",
                result=result,
                probable_area="Imports / parsers bancarios",
                details=check.detail,
                probable_postgres=False,
                probable_encoding=False,
                probable_missing_attr=False,
                user_label=samples.user_label,
                app_name="imports",
                view_name="bank_parser",
                target=f"{check.bank} {check.layout}",
            )
        )


def _record_runtime_checks(results: list[AuditResult], *, samples: EntitySamples) -> None:
    db_name = connection.settings_dict.get("NAME") or ""
    results.append(
        AuditResult(
            item="Runtime base Django/PostgreSQL",
            result="OK" if connection.vendor == "postgresql" else "WARN",
            probable_area="Runtime / banco",
            details=f"Banco atual: {connection.vendor} · name={db_name}",
            user_label=samples.user_label,
            target="database",
            app_name="runtime",
            view_name="database",
        )
    )
    for label, queryset, area in (
        ("Pessoas snapshot", PersonSnapshot.objects.all(), "Pessoas"),
        ("Contribuicoes nativas", NativeContribution.objects.all(), "Contribuicoes"),
        ("Recibos snapshot", ReceiptSnapshot.objects.all(), "Recibos"),
        ("Fila de recibos", ReceiptDispatch.objects.all(), "Recibos"),
        ("Lotes de envelopes", NativeEnvelopeLot.objects.all(), "Contribuicoes"),
        ("Lotes de importacao de pessoas", NativePeopleImportLot.objects.all(), "Imports"),
        ("Lotes de extrato piloto", StatementImportPilotLot.objects.all(), "Imports"),
    ):
        count = int(queryset.count())
        results.append(
            AuditResult(
                item=f"Carga essencial: {label}",
                result="OK" if count > 0 else "WARN",
                probable_area=f"{area} / cargas",
                details=f"{count} registro(s) disponiveis.",
                user_label=samples.user_label,
                target=label,
                app_name=area.lower(),
                view_name="cargas",
            )
        )
    results.append(
        AuditResult(
            item="Usuario para auditoria autenticada",
            result="OK" if samples.user_id else "FAIL",
            probable_area="Acesso / autenticacao",
            details=(
                f"Cliente autenticado usando {samples.user_label}."
                if samples.user_id
                else "Nao existe usuario ativo para force_login no comando."
            ),
            user_label=samples.user_label,
            target="/accounts/login/",
            app_name="accounts",
            view_name="login",
        )
    )


def _record_totals_and_consistency_checks(results: list[AuditResult], *, samples: EntitySamples) -> None:
    _record_count_result(
        results,
        item="Total operacional de pessoas",
        area="Pessoas / totais",
        count=PersonSnapshot.objects.filter(is_active=True).count(),
        samples=samples,
    )
    _record_count_result(
        results,
        item="Total operacional de contribuicoes",
        area="Contribuicoes / totais",
        count=NativeContribution.objects.filter(is_active=True).count(),
        samples=samples,
    )
    _record_count_result(
        results,
        item="Total operacional de recibos",
        area="Recibos / totais",
        count=ReceiptSnapshot.objects.count(),
        samples=samples,
    )
    _record_count_result(
        results,
        item="Total operacional de lotes",
        area="Imports / totais",
        count=(
            NativeEnvelopeLot.objects.count()
            + NativePeopleImportLot.objects.count()
            + StatementImportPilotLot.objects.count()
        ),
        samples=samples,
    )
    _record_count_result(
        results,
        item="Total operacional de auditorias",
        area="Auditoria / totais",
        count=(
            AuditEvent.objects.count()
            + NativePeopleImportPending.objects.filter(resolved=False).count()
            + StatementImportPilotMovement.objects.filter(
                Q(review_status__in=["pendente", "revisar_pessoa", "revisar_destinacao", "classificacao_pendente"])
                | Q(review_status="revisar_duplicidade", imported_contribution_legacy_id__isnull=True)
            ).count()
        ),
        samples=samples,
    )

    for date_start, date_end in samples.date_ranges[:2]:
        contribution_total = _decimal_or_zero(
            NativeContribution.objects.filter(
                _date_range_q("received_at", date_start, date_end),
                is_active=True,
                person_legacy_id__isnull=False,
            ).exclude(
                person_legacy_id=0,
            ).aggregate(total=Sum("amount"))["total"]
        )
        person_total = _decimal_or_zero(
            PersonContributionSnapshot.objects.filter(
                _date_range_q("received_at", date_start, date_end),
                is_active=True,
            ).aggregate(total=Sum("amount"))["total"]
        )
        receipts_total = _decimal_or_zero(
            ReceiptSnapshot.objects.filter(
                _date_range_q("emission_date", date_start, date_end)
            ).aggregate(total=Sum("total_value"))["total"]
        )
        receipt_items_total = _decimal_or_zero(
            ReceiptItemSnapshot.objects.filter(
                _date_range_q("receipt__emission_date", date_start, date_end)
            ).aggregate(total=Sum("amount"))["total"]
        )
        period_label = f"{date_start or '-'} a {date_end or '-'}"
        results.append(
            AuditResult(
                item=f"Totais financeiros por periodo ({period_label})",
                result="OK" if _same_money(contribution_total, person_total) else "FAIL",
                probable_area="Contribuicoes / totais financeiros",
                details=(
                    f"contribuicoes_nativas_com_pessoa={contribution_total} · "
                    f"espelho_pessoa={person_total} · "
                    f"recibos={receipts_total} · itens_recibo={receipt_items_total}"
                ),
                probable_postgres=not _same_money(contribution_total, person_total),
                user_label=samples.user_label,
                app_name="contributions",
                view_name="financial_totals",
                target=period_label,
            )
        )
        results.append(
            AuditResult(
                item=f"Paridade financeira de recibos ({period_label})",
                result="OK" if _same_money(receipts_total, receipt_items_total) else "WARN",
                probable_area="Recibos / totais financeiros",
                details=f"recibos={receipts_total} · itens_recibo={receipt_items_total}",
                user_label=samples.user_label,
                app_name="receipts",
                view_name="financial_totals",
                target=period_label,
            )
        )

    _record_legacy_parity_checks(results, samples=samples)

    results.extend(
        [
            AuditResult(
                item="Consistencia - contribuicoes com pessoa inexistente",
                result="OK" if _missing_person_links_count() == 0 else "FAIL",
                probable_area="PostgreSQL compatibility / consistencia",
                details=f"{_missing_person_links_count()} contribuicao(oes) apontando para pessoa inexistente.",
                probable_postgres=_missing_person_links_count() > 0,
                user_label=samples.user_label,
                app_name="contributions",
                view_name="consistency",
                target="NativeContribution.person_legacy_id",
            ),
            AuditResult(
                item="Consistencia - recibos com pessoa inexistente",
                result="OK" if _missing_receipt_people_count() == 0 else "FAIL",
                probable_area="PostgreSQL compatibility / consistencia",
                details=f"{_missing_receipt_people_count()} recibo(s) apontando para pessoa inexistente.",
                probable_postgres=_missing_receipt_people_count() > 0,
                user_label=samples.user_label,
                app_name="receipts",
                view_name="consistency",
                target="ReceiptSnapshot.person_legacy_id",
            ),
            AuditResult(
                item="Consistencia - envelopes sem lote valido",
                result="OK" if _missing_envelope_lot_count() == 0 else "WARN",
                probable_area="Contribuicoes / consistencia",
                details=f"{_missing_envelope_lot_count()} envelope(s) com native_lot_legacy_id sem lote correspondente.",
                user_label=samples.user_label,
                app_name="contributions",
                view_name="consistency",
                target="NativeEnvelope.native_lot_legacy_id",
            ),
            AuditResult(
                item="Consistencia - itens de envelope sem contribuicao valida",
                result="OK" if _missing_envelope_item_contribution_count() == 0 else "WARN",
                probable_area="Contribuicoes / consistencia",
                details=f"{_missing_envelope_item_contribution_count()} item(ns) de envelope apontando para contribuicao inexistente.",
                user_label=samples.user_label,
                app_name="contributions",
                view_name="consistency",
                target="NativeEnvelopeItem.contribution_legacy_id",
            ),
            AuditResult(
                item="Consistencia - movimentos importados com contribuicao inexistente",
                result="OK" if _missing_statement_contribution_links_count() == 0 else "WARN",
                probable_area="Imports / consistencia",
                details=f"{_missing_statement_contribution_links_count()} movimento(s) com imported_contribution_legacy_id inexistente.",
                user_label=samples.user_label,
                app_name="imports",
                view_name="consistency",
                target="StatementImportPilotMovement.imported_contribution_legacy_id",
            ),
            AuditResult(
                item="Consistencia - pessoas sem nome",
                result="OK" if PersonSnapshot.objects.filter(Q(name="") | Q(normalized_name="")).count() == 0 else "FAIL",
                probable_area="Pessoas / consistencia",
                details=f"{PersonSnapshot.objects.filter(Q(name='') | Q(normalized_name='')).count()} pessoa(s) com nome ou nome normalizado vazio.",
                user_label=samples.user_label,
                app_name="people",
                view_name="consistency",
                target="PersonSnapshot.name",
            ),
            AuditResult(
                item="Consistencia - perfis de familia sem cabeca",
                result="OK" if PersonProfileSnapshot.objects.filter(profile="").count() == 0 else "WARN",
                probable_area="Pessoas / consistencia",
                details=f"{PersonProfileSnapshot.objects.filter(profile='').count()} perfil(is) com campo profile vazio.",
                user_label=samples.user_label,
                app_name="people",
                view_name="consistency",
                target="PersonProfileSnapshot.profile",
            ),
            AuditResult(
                item="Consistencia - lixeira e purga seguras",
                result="OK",
                probable_area="Pessoas / consistencia",
                details=(
                    f"lixeira={PersonSecureTrashSnapshot.objects.count()} · "
                    f"purgas={PersonSecurePurgeSnapshot.objects.count()}"
                ),
                user_label=samples.user_label,
                app_name="people",
                view_name="consistency",
                target="secure_trash",
            ),
        ]
    )


def _record_file_and_attachment_checks(results: list[AuditResult], *, samples: EntitySamples) -> None:
    repo_root = Path(getattr(settings, "REPO_ROOT", Path(settings.BASE_DIR).parent))
    paths = [
        ("Logo institucional", Path(getattr(settings, "POWER_CHURCH_BRAND_LOGO_PATH", "")), "Arquivos / branding", False, True),
        ("Diretorio de fotos", photo_dir(), "Arquivos / fotos", True, True),
        ("Uploads de envelopes", repo_root / "data" / "envelope_uploads", "Arquivos / uploads", True, True),
        ("Uploads de pessoas", repo_root / "data" / "people_uploads", "Arquivos / uploads", True, True),
        ("Uploads de extratos", repo_root / "data" / "statement_uploads", "Arquivos / uploads", True, True),
        ("Relatorios gerados", repo_root / "reports", "Arquivos / relatorios", True, True),
        ("Homologacao", repo_root / "data" / "homologacao", "Arquivos / documentos", True, True),
        ("Static root", Path(getattr(settings, "STATIC_ROOT", repo_root / "staticfiles")), "Arquivos / staticfiles", True, False),
    ]
    for label, path, area, should_be_dir, writable in paths:
        exists = path.exists()
        is_expected_kind = path.is_dir() if should_be_dir else path.is_file()
        writable_ok = os.access(path, os.W_OK) if exists and writable else exists
        result = "OK" if exists and is_expected_kind and writable_ok else "WARN"
        details = f"path={path} · existe={exists} · tipo_ok={is_expected_kind} · gravavel={writable_ok}"
        results.append(
            AuditResult(
                item=f"Arquivos - {label}",
                result=result,
                probable_area=area,
                details=details,
                user_label=samples.user_label,
                app_name="runtime",
                view_name="filesystem",
                target=str(path),
            )
        )

    if samples.receipt_ids:
        detail = ReceiptSnapshot.objects.filter(legacy_id=samples.receipt_ids[0]).first()
        if detail is not None:
            payload = receipt_pdf(
                {
                    "receipt": {
                        "id": detail.legacy_id,
                        "numero": detail.receipt_number,
                        "nome_pessoa": detail.person_name,
                        "email_pessoa": detail.person_email,
                        "cpf_pessoa": detail.person_cpf,
                        "codigo_pessoa": detail.person_code,
                        "data_emissao": detail.emission_date_raw,
                        "valor_total": detail.total_value,
                        "observacoes": detail.notes,
                    },
                    "items": [],
                }
            )
            results.append(
                AuditResult(
                    item="Arquivos - geracao interna de recibo PDF",
                    result="OK" if payload.startswith(b"%PDF") and len(payload) > 100 else "FAIL",
                    probable_area="Arquivos / documentos",
                    details=f"payload_pdf={len(payload)} bytes",
                    user_label=samples.user_label,
                    app_name="receipts",
                    view_name="pdf_attachment",
                    target="receipt_pdf",
                )
            )
    report_payload = contribution_period_pdf(
        {
            "period_label": "Homologacao",
            "rows": [],
            "totals": {"count": 0, "amount": Decimal("0.00")},
        }
    )
    results.append(
        AuditResult(
            item="Arquivos - geracao interna de PDF de relatorio",
            result="OK" if report_payload.startswith(b"%PDF") and len(report_payload) > 100 else "FAIL",
            probable_area="Arquivos / documentos",
            details=f"payload_pdf={len(report_payload)} bytes",
            user_label=samples.user_label,
            app_name="reports",
            view_name="pdf_attachment",
            target="contribution_period_pdf",
        )
    )


def _record_email_checks(results: list[AuditResult], *, samples: EntitySamples) -> None:
    snapshot = graph_config_snapshot()
    provider = str(snapshot.get("provider") or getattr(settings, "POWER_CHURCH_EMAIL_PROVIDER", "smtp"))
    results.append(
        AuditResult(
            item="E-mail - backend Django configurado",
            result="OK" if str(getattr(settings, "EMAIL_BACKEND", "")).strip() else "FAIL",
            probable_area="E-mails / configuracao",
            details=f"backend={getattr(settings, 'EMAIL_BACKEND', '')} · provider={provider}",
            user_label=samples.user_label,
            app_name="runtime",
            view_name="email_backend",
            target=str(getattr(settings, "EMAIL_BACKEND", "")),
        )
    )
    results.append(
        AuditResult(
            item="E-mail - templates de recibo",
            result="OK" if ReceiptEmailTemplate.objects.filter(active=True).exists() else "WARN",
            probable_area="E-mails / templates",
            details=f"templates_ativos={ReceiptEmailTemplate.objects.filter(active=True).count()}",
            user_label=samples.user_label,
            app_name="receipts",
            view_name="email_templates",
            target="ReceiptEmailTemplate",
        )
    )
    if provider == "microsoft_graph":
        missing = [
            key
            for key, value in (
                ("POWER_CHURCH_GRAPH_TENANT_ID", snapshot.get("tenant_id")),
                ("POWER_CHURCH_GRAPH_CLIENT_ID", snapshot.get("client_id")),
                ("POWER_CHURCH_GRAPH_CLIENT_SECRET", snapshot.get("has_client_secret")),
                ("POWER_CHURCH_GRAPH_SENDER_USER", snapshot.get("sender_user")),
            )
            if not value
        ]
        results.append(
            AuditResult(
                item="E-mail - Microsoft Graph configurado",
                result="OK" if not missing else "FAIL",
                probable_area="E-mails / configuracao",
                details=f"missing={missing or '-'} · sender={snapshot.get('sender_user') or '-'}",
                user_label=samples.user_label,
                app_name="runtime",
                view_name="email_provider",
                target="microsoft_graph",
            )
        )
    dry_run = send_email_message(
        subject="Homologacao Power Church",
        body="Teste seco do regression_audit.",
        from_email=str(getattr(settings, "DEFAULT_FROM_EMAIL", "") or "recebimento@localhost"),
        to_emails=["homologacao@example.com"],
        attachments=[MailAttachment(filename="teste.pdf", content=b"%PDF-1.4\n", content_type="application/pdf")],
        dry_run=True,
    )
    results.append(
        AuditResult(
            item="E-mail - dry run com anexo",
            result="OK" if bool(getattr(dry_run, "accepted", False)) else "FAIL",
            probable_area="E-mails / homologacao",
            details=f"provider={dry_run.provider} · metadata={dry_run.metadata}",
            user_label=samples.user_label,
            app_name="runtime",
            view_name="email_dry_run",
            target="mail_dispatch",
        )
    )
    results.append(
        AuditResult(
            item="E-mail - outbox em modo auditoria",
            result="OK",
            probable_area="E-mails / homologacao",
            details=f"outbox_local={len(getattr(mail, 'outbox', [])) if hasattr(mail, 'outbox') else 0}",
            user_label=samples.user_label,
            app_name="runtime",
            view_name="email_outbox",
            target="django.core.mail",
        )
    )


def _record_safe_post_checks(results: list[AuditResult], *, user_available: bool, samples: EntitySamples) -> None:
    if not user_available:
        return
    user = get_user_model().objects.filter(pk=samples.user_id).first() if samples.user_id else None
    if user is None:
        return
    post_checks: list[tuple[str, str, str, dict[str, Any], bool]] = [
        ("POST seguro - login invalido", "/accounts/login/", "Acesso / POST seguro", {"username": samples.user_label, "password": "senha_invalida"}, False),
        ("POST seguro - importacao de pessoas sem arquivo", "/people/imports/", "Imports / POST seguro", {}, False),
        ("POST seguro - importacao bancaria sem arquivo", "/imports/", "Imports / POST seguro", {}, False),
    ]
    profile_update = NativeEnvelopeProfileUpdate.objects.order_by("id").first()
    if profile_update is not None:
        post_checks.append(
            (
                "POST seguro - ignorar pendencia cadastral de envelope",
                f"/contributions/envelopes/profile-updates/{profile_update.id}/ignore/",
                "Contribuicoes / POST seguro",
                {"envelope_id": str(profile_update.envelope_id)},
                True,
            )
        )
    for item, url, area, payload, rollback in post_checks:
        request_data = _request_post_url(url, payload, user=user, user_label=samples.user_label, rollback=rollback)
        _record_page_check(results, request_data, item=item, area=area, expected_kind="route")


def _record_profile_checks(results: list[AuditResult], *, samples: EntitySamples) -> None:
    snapshot = access_control_snapshot()
    results.append(
        AuditResult(
            item="Perfis - grupos padrao instalados",
            result="OK" if bool(snapshot.get("installed")) else "WARN",
            probable_area="Perfis / acesso",
            details=(
                f"grupos={snapshot.get('group_count')} · "
                f"permissoes={snapshot.get('permission_count')} · "
                f"missing={snapshot.get('missing_permissions') or '-'}"
            ),
            user_label=samples.user_label,
            app_name="accounts",
            view_name="profiles",
            target="access_control",
        )
    )
    users = list(snapshot.get("users") or [])
    sampled_users = users[:5]
    permission_paths = [
        ("dashboard", "/", "view_dashboard"),
        ("pessoas", "/people/", "view_people"),
        ("contribuicoes", "/contributions/", "view_contributions"),
        ("imports", "/imports/", "view_imports"),
        ("relatorios", "/reports/", "view_reports"),
        ("auditoria", "/audit/", "view_audit"),
        ("usuarios", "/accounts/", "manage_accounts"),
    ]
    for user in sampled_users:
        groups = ",".join(user.groups.order_by("name").values_list("name", flat=True)) or "-"
        client = Client(raise_request_exception=False)
        client.force_login(user)
        for label, url, codename in permission_paths:
            request_data = _request_url(client, url, user_label=str(user.username))
            if user_has_module_permission(user, codename):
                _record_page_check(
                    results,
                    request_data,
                    item=f"Perfil - {user.username} acessa {label}",
                    area="Perfis / acesso",
                    expected_kind="route",
                )
            else:
                status_code = int(request_data["status_code"] or 0)
                ok = status_code == 403
                results.append(
                    AuditResult(
                        item=f"Perfil - {user.username} bloqueado em {label}",
                        result="OK" if ok else "FAIL",
                        probable_area="Perfis / acesso",
                        url=request_data["url"],
                        user_label=request_data["user_label"],
                        status_code=status_code,
                        response_time_ms=int(request_data["elapsed_ms"] or 0),
                        route_name=request_data["resolver_view_name"],
                        namespace=request_data["resolver_namespaces"],
                        app_name=request_data["resolver_app_names"] or "accounts",
                        view_name=request_data["lookup_str"] or request_data["resolver_view_name"],
                        template_names=request_data["templates"],
                        details=f"HTTP {status_code} · esperado bloqueio em {codename}",
                        error="" if ok else request_data["exception_detail"] or str(request_data["body"] or "")[:400],
                        traceback_summary=request_data["exception_summary"],
                        probable_postgres=bool(request_data["probable_postgres"]),
                        probable_encoding=bool(request_data["probable_encoding"]),
                        probable_missing_attr=bool(request_data["probable_missing_attr"]),
                        target=request_data["url"],
                    )
                )
            if results:
                results[-1].details = f"{results[-1].details} · grupos={groups} · staff={user.is_staff} · superuser={user.is_superuser}"

def _record_operator_scenarios(
    results: list[AuditResult],
    *,
    anonymous_client: Client,
    authenticated_client: Client,
    user_available: bool,
    samples: EntitySamples,
) -> None:
    _record_page_check(
        results,
        _request_url(anonymous_client, "/accounts/login/", user_label="anonimo"),
        item="Login publico",
        area="Acesso / autenticacao",
        expected_kind="html",
    )
    for path, area in (("/", "Dashboard / autenticacao"), ("/people/", "Pessoas / autenticacao")):
        _record_guard_check(results, _request_url(anonymous_client, path, user_label="anonimo"), item=f"Protecao de login: {path}", area=area)

    if not user_available:
        results.append(
            AuditResult(
                item="Varredura autenticada",
                result="SKIP",
                probable_area="Acesso / autenticacao",
                details="Sem usuario ativo para force_login; a varredura autenticada foi pulada.",
                user_label=samples.user_label,
            )
        )
        return

    for item, path, area in [
        ("Dashboard operacional", "/", "Dashboard"),
        ("Pessoas - lista", "/people/", "Pessoas"),
        ("Pessoas - familias", "/people/families/", "Pessoas"),
        ("Contribuintes auxiliares", "/contributors/", "Contribuicoes"),
        ("Contribuicoes - lista", "/contributions/", "Contribuicoes"),
        ("Envelopes - lista", "/contributions/envelopes/", "Contribuicoes"),
        ("Recibos - hub", "/receipts/", "Recibos"),
        ("Recibos - fila", "/receipts/queue/", "Recibos"),
        ("Importacoes bancarias", "/imports/", "Imports"),
        ("Regras de centavos", "/imports/rules/", "Imports"),
        ("Auditoria", "/audit/", "Auditoria"),
        ("Relatorios", "/reports/", "Relatorios"),
        ("Relatorios por destino", "/reports/destinations/", "Relatorios"),
    ]:
        _record_page_check(
            results,
            _request_url(authenticated_client, path, user_label=samples.user_label),
            item=item,
            area=area,
            expected_kind="html",
        )

    for query in samples.person_queries[:3]:
        _record_page_check(
            results,
            _request_url(authenticated_client, f"/people/?q={query}", user_label=samples.user_label),
            item=f"Operador - buscar pessoa por nome ({query})",
            area="Pessoas",
            expected_kind="html",
        )
        _record_page_check(
            results,
            _request_url(authenticated_client, f"/contributions/?q={query}", user_label=samples.user_label),
            item=f"Operador - buscar contribuicoes por nome ({query})",
            area="Contribuicoes",
            expected_kind="html",
        )
        _record_page_check(
            results,
            _request_url(authenticated_client, f"/receipts/?q={query}", user_label=samples.user_label),
            item=f"Operador - filtrar recibos ({query})",
            area="Recibos",
            expected_kind="html",
        )
        _record_page_check(
            results,
            _request_url(authenticated_client, f"/reports/?q={query}", user_label=samples.user_label),
            item=f"Operador - relatorio por periodo com busca ({query})",
            area="Relatorios",
            expected_kind="html",
        )

    for status in samples.person_statuses[:2]:
        _record_page_check(
            results,
            _request_url(authenticated_client, f"/people/?status={status}", user_label=samples.user_label),
            item=f"Pessoas - filtro por status ({status})",
            area="Pessoas",
            expected_kind="html",
        )

    for city in samples.person_cities[:2]:
        _record_page_check(
            results,
            _request_url(authenticated_client, f"/people/?city={city}", user_label=samples.user_label),
            item=f"Pessoas - filtro por cidade ({city})",
            area="Pessoas",
            expected_kind="html",
            encoding_probe=True,
        )

    for person_id in samples.person_ids[:3]:
        person_name = samples.person_names.get(person_id, "")
        _record_page_check(
            results,
            _request_url(authenticated_client, f"/people/{person_id}/", user_label=samples.user_label),
            item=f"Operador - abrir ficha da pessoa #{person_id}",
            area="Pessoas",
            expected_kind="html",
            must_contain=person_name or None,
            encoding_probe=bool(person_name),
        )
        _record_page_check(
            results,
            _request_url(authenticated_client, f"/people/{person_id}/edit/", user_label=samples.user_label),
            item=f"Operador - abrir edicao da pessoa #{person_id}",
            area="Pessoas",
            expected_kind="html",
        )
        _record_page_check(
            results,
            _request_url(authenticated_client, f"/people/{person_id}/merge/", user_label=samples.user_label),
            item=f"Operador - abrir merge da pessoa #{person_id}",
            area="Pessoas",
            expected_kind="html",
        )
        _record_page_check(
            results,
            _request_url(authenticated_client, f"/contributions/statements/{person_id}/", user_label=samples.user_label),
            item=f"Operador - abrir extrato da pessoa #{person_id}",
            area="Contribuicoes",
            expected_kind="html",
        )
        _record_page_check(
            results,
            _request_url(authenticated_client, f"/contributions/statements/{person_id}/pdf/", user_label=samples.user_label),
            item=f"Operador - gerar PDF do extrato da pessoa #{person_id}",
            area="Contribuicoes",
            expected_kind="pdf",
        )

    for contribution_id in samples.contribution_ids[:3]:
        _record_page_check(
            results,
            _request_url(authenticated_client, f"/contributions/{contribution_id}/", user_label=samples.user_label),
            item=f"Contribuicao - detalhe #{contribution_id}",
            area="Contribuicoes",
            expected_kind="html",
        )
        _record_page_check(
            results,
            _request_url(authenticated_client, f"/contributions/{contribution_id}/split/", user_label=samples.user_label),
            item=f"Contribuicao - split #{contribution_id}",
            area="Contribuicoes",
            expected_kind="html",
        )

    for receipt_id in samples.receipt_ids[:3]:
        _record_page_check(
            results,
            _request_url(authenticated_client, f"/receipts/{receipt_id}/", user_label=samples.user_label),
            item=f"Recibo - detalhe #{receipt_id}",
            area="Recibos",
            expected_kind="html",
        )
        _record_page_check(
            results,
            _request_url(authenticated_client, f"/receipts/{receipt_id}/pdf/", user_label=samples.user_label),
            item=f"Recibo - PDF #{receipt_id}",
            area="Recibos",
            expected_kind="pdf",
        )

    for lot_id in samples.envelope_lot_ids[:2]:
        _record_page_check(
            results,
            _request_url(authenticated_client, f"/contributions/envelopes/lots/{lot_id}/", user_label=samples.user_label),
            item=f"Envelopes - detalhe do lote #{lot_id}",
            area="Contribuicoes",
            expected_kind="html",
        )
        _record_page_check(
            results,
            _request_url(authenticated_client, f"/contributions/envelopes/lots/{lot_id}/next/", user_label=samples.user_label),
            item=f"Envelopes - proximo pendente do lote #{lot_id}",
            area="Contribuicoes",
            expected_kind="route",
        )

    for envelope_id in samples.envelope_ids[:3]:
        _record_page_check(
            results,
            _request_url(authenticated_client, f"/contributions/envelopes/{envelope_id}/", user_label=samples.user_label),
            item=f"Envelope - detalhe #{envelope_id}",
            area="Contribuicoes",
            expected_kind="html",
        )

    if not samples.envelope_launch_ids:
        results.append(
            AuditResult(
                item="Envelope - lancamento",
                result="SKIP",
                probable_area="Contribuicoes",
                details="Sem amostra real de envelope pendente/em_digitacao para validar a tela de lancamento.",
                user_label=samples.user_label,
            )
        )
    for envelope_id in samples.envelope_launch_ids[:2]:
        _record_page_check(
            results,
            _request_url(authenticated_client, f"/contributions/envelopes/{envelope_id}/launch/", user_label=samples.user_label),
            item=f"Envelope - lancamento #{envelope_id}",
            area="Contribuicoes",
            expected_kind="route",
        )

    if not samples.envelope_edit_ids:
        results.append(
            AuditResult(
                item="Envelope - edicao",
                result="SKIP",
                probable_area="Contribuicoes",
                details="Sem amostra real de envelope lancado para validar a tela de edicao.",
                user_label=samples.user_label,
            )
        )
    for envelope_id in samples.envelope_edit_ids[:2]:
        _record_page_check(
            results,
            _request_url(authenticated_client, f"/contributions/envelopes/{envelope_id}/edit/", user_label=samples.user_label),
            item=f"Envelope - edicao #{envelope_id}",
            area="Contribuicoes",
            expected_kind="route",
        )

    for image_id in samples.envelope_image_ids[:2]:
        _record_page_check(
            results,
            _request_url(authenticated_client, f"/contributions/envelopes/{image_id}/image/", user_label=samples.user_label),
            item=f"Envelope - imagem #{image_id}",
            area="Contribuicoes",
            expected_kind="image",
        )

    for lot_id in samples.people_import_lot_ids[:3]:
        _record_page_check(
            results,
            _request_url(authenticated_client, f"/people/imports/{lot_id}/", user_label=samples.user_label),
            item=f"Importacao de pessoas - lote #{lot_id}",
            area="Imports",
            expected_kind="html",
        )
        _record_page_check(
            results,
            _request_url(
                authenticated_client,
                f"/people/imports/{lot_id}/?tipo=data_invalida&pendencia_status=abertas",
                user_label=samples.user_label,
            ),
            item=f"Importacao de pessoas - lote filtrado #{lot_id}",
            area="Imports",
            expected_kind="html",
        )

    for lot_id in samples.statement_lot_ids[:3]:
        _record_page_check(
            results,
            _request_url(authenticated_client, f"/imports/statement/{lot_id}/?backend=postgres_nativo", user_label=samples.user_label),
            item=f"Extrato - lote #{lot_id}",
            area="Imports",
            expected_kind="html",
        )
        _record_page_check(
            results,
            _request_url(
                authenticated_client,
                f"/imports/statement/{lot_id}/?backend=postgres_nativo&status=pendencias",
                user_label=samples.user_label,
            ),
            item=f"Extrato - lote pendencias #{lot_id}",
            area="Imports",
            expected_kind="html",
        )

    for movement_id in samples.statement_movement_ids[:3]:
        _record_page_check(
            results,
            _request_url(
                authenticated_client,
                f"/imports/statement/movement/{movement_id}/?backend=postgres_nativo",
                user_label=samples.user_label,
            ),
            item=f"Extrato - movimento #{movement_id}",
            area="Imports",
            expected_kind="html",
        )

    for contributor_id in samples.contributor_ids[:3]:
        _record_page_check(
            results,
            _request_url(authenticated_client, f"/contributors/{contributor_id}/", user_label=samples.user_label),
            item=f"Contribuinte auxiliar - detalhe #{contributor_id}",
            area="Contribuicoes",
            expected_kind="html",
        )

    for date_start, date_end in samples.date_ranges[:2]:
        query_parts = []
        if date_start:
            query_parts.append(f"date_start={date_start}")
        if date_end:
            query_parts.append(f"date_end={date_end}")
        query = "&".join(query_parts)
        suffix = f"?{query}" if query else ""
        _record_page_check(
            results,
            _request_url(authenticated_client, f"/contributions/{suffix}", user_label=samples.user_label),
            item=f"Contribuicoes - filtro por periodo {date_start or '-'} a {date_end or '-'}",
            area="Contribuicoes",
            expected_kind="html",
        )
        _record_page_check(
            results,
            _request_url(authenticated_client, f"/receipts/{suffix}", user_label=samples.user_label),
            item=f"Recibos - filtro por periodo {date_start or '-'} a {date_end or '-'}",
            area="Recibos",
            expected_kind="html",
        )
        _record_page_check(
            results,
            _request_url(authenticated_client, f"/reports/{suffix}", user_label=samples.user_label),
            item=f"Relatorio HTML por periodo {date_start or '-'} a {date_end or '-'}",
            area="Relatorios",
            expected_kind="html",
        )
        _record_page_check(
            results,
            _request_url(authenticated_client, f"/reports/destinations/{suffix}", user_label=samples.user_label),
            item=f"Relatorio destino HTML por periodo {date_start or '-'} a {date_end or '-'}",
            area="Relatorios",
            expected_kind="html",
        )
        _record_page_check(
            results,
            _request_url(authenticated_client, f"/reports/contributions-period.pdf?inline=1{'&' + query if query else ''}", user_label=samples.user_label),
            item=f"Relatorio PDF por periodo {date_start or '-'} a {date_end or '-'}",
            area="Relatorios",
            expected_kind="pdf",
        )
        _record_page_check(
            results,
            _request_url(authenticated_client, f"/reports/contributions-destinations.pdf?inline=1{'&' + query if query else ''}", user_label=samples.user_label),
            item=f"Relatorio PDF por destino {date_start or '-'} a {date_end or '-'}",
            area="Relatorios",
            expected_kind="pdf",
        )

    for competence in samples.contribution_competences[:2]:
        if not competence:
            continue
        _record_page_check(
            results,
            _request_url(authenticated_client, f"/reports/?competencia={competence}", user_label=samples.user_label),
            item=f"Relatorio HTML por competencia {competence}",
            area="Relatorios",
            expected_kind="html",
        )
        _record_page_check(
            results,
            _request_url(authenticated_client, f"/reports/destinations/?competencia={competence}", user_label=samples.user_label),
            item=f"Relatorio destino HTML por competencia {competence}",
            area="Relatorios",
            expected_kind="html",
        )

    for destination_key in samples.report_destination_keys[:2]:
        _record_page_check(
            results,
            _request_url(authenticated_client, f"/reports/destinations/?destination={destination_key}", user_label=samples.user_label),
            item=f"Relatorio destino filtrado ({destination_key})",
            area="Relatorios",
            expected_kind="html",
        )
        _record_page_check(
            results,
            _request_url(authenticated_client, f"/reports/contributions-destinations.pdf?inline=1&destination={destination_key}", user_label=samples.user_label),
            item=f"Relatorio destino PDF filtrado ({destination_key})",
            area="Relatorios",
            expected_kind="pdf",
        )

    _record_page_check(
        results,
        _request_url(authenticated_client, "/people/export/?format=csv&preset=cadastro_basico", user_label=samples.user_label),
        item="Exportacao de pessoas CSV",
        area="Relatorios",
        expected_kind="csv",
        expected_headers=("ID", "Codigo", "Nome"),
    )
    _record_page_check(
        results,
        _request_url(authenticated_client, "/people/export/?format=csv&source=dynamic&column=nome&column=cidade", user_label=samples.user_label),
        item="Exportacao dinamica CSV",
        area="Relatorios",
        expected_kind="csv",
        expected_headers=("Nome", "Cidade"),
    )
    _record_page_check(
        results,
        _request_url(authenticated_client, "/people/export/?format=xlsx&preset=contatos", user_label=samples.user_label),
        item="Exportacao de pessoas XLSX",
        area="Relatorios",
        expected_kind="xlsx",
    )
    _record_page_check(
        results,
        _request_url(authenticated_client, "/people/export/?format=xlsx&source=dynamic&column=nome&column=email", user_label=samples.user_label),
        item="Exportacao dinamica XLSX",
        area="Relatorios",
        expected_kind="xlsx",
    )


def _record_discovered_route_checks(
    results: list[AuditResult],
    *,
    user_available: bool,
    samples: EntitySamples,
    routes: list[RouteInfo],
) -> None:
    if not user_available:
        return
    seen_urls: set[str] = set()
    user = get_user_model().objects.filter(pk=samples.user_id).first() if samples.user_id else None
    for route in routes:
        concrete_urls = _expand_route_to_urls(route, samples)
        if not concrete_urls:
            results.append(
                AuditResult(
                    item=f"Descoberta de rota: {route.route_name or route.path_template}",
                    result="SKIP",
                    probable_area=f"{_group_label(route.app_name)} / rotas",
                    details="Sem amostra real para preencher os parametros desta rota.",
                    route_name=route.route_name,
                    namespace=route.namespace,
                    app_name=route.app_name,
                    view_name=route.lookup_str,
                    user_label=samples.user_label,
                    target=route.path_template,
                )
            )
            continue
        for url in concrete_urls[:2]:
            if url in seen_urls:
                continue
            seen_urls.add(url)
            client = Client(raise_request_exception=False)
            if user is not None:
                client.force_login(user)
            _record_page_check(
                results,
                _request_url(client, url, user_label=samples.user_label, route=route),
                item=f"Descoberta de rota GET: {route.route_name or route.path_template}",
                area=f"{_group_label(route.app_name)} / rotas",
                expected_kind="route",
            )


def _record_query_checks(results: list[AuditResult], *, samples: EntitySamples) -> None:
    query_term = samples.person_queries[0] if samples.person_queries else "joao"
    normalized_term = normalize_match_name(query_term)
    legacy_id_suffix = str(samples.person_ids[0])[-2:] if samples.person_ids else ""

    checks: list[tuple[str, str, Any]] = [
        (
            "ORM pessoas - icontains nome",
            "Pessoas / ORM",
            lambda: list(PersonSnapshot.objects.filter(name__icontains=query_term).values_list("legacy_id", flat=True)[:5]),
        ),
        (
            "ORM pessoas - normalized_name icontains",
            "Pessoas / ORM",
            lambda: list(PersonSnapshot.objects.filter(normalized_name__icontains=normalized_term).values_list("legacy_id", flat=True)[:5]),
        ),
        (
            "ORM pessoas - nulos e vazios em e-mail",
            "Pessoas / PostgreSQL compatibility",
            lambda: PersonSnapshot.objects.filter(Q(primary_email__isnull=True) | Q(primary_email="")).count(),
        ),
        (
            "ORM enderecos - cidade icontains",
            "Pessoas / ORM",
            lambda: list(
                PersonAddressSnapshot.objects.filter(city__icontains=(samples.person_cities[0] if samples.person_cities else query_term))
                .values_list("city", flat=True)[:5]
            ),
        ),
        (
            "ORM pessoas - ordenacao com null/date",
            "Pessoas / PostgreSQL compatibility",
            lambda: list(
                PersonSnapshot.objects.annotate(birth_date_safe=Coalesce("birth_date_raw", Value("")))
                .order_by("birth_date_safe", "normalized_name", "legacy_id")
                .values_list("legacy_id", flat=True)[:5]
            ),
        ),
        (
            "ORM pessoas - cast legacy_id texto",
            "Pessoas / PostgreSQL compatibility",
            lambda: list(
                PersonSnapshot.objects.annotate(legacy_id_text=Cast("legacy_id", output_field=CharField()))
                .filter(legacy_id_text__contains=legacy_id_suffix or "1")
                .values_list("legacy_id", flat=True)[:5]
            ),
        ),
        (
            "ORM pessoas - lower/coalesce",
            "Pessoas / PostgreSQL compatibility",
            lambda: list(
                PersonSnapshot.objects.annotate(search_blob=Lower(Coalesce("social_name", "name")))
                .filter(search_blob__icontains=query_term.lower())
                .values_list("legacy_id", flat=True)[:5]
            ),
        ),
        (
            "ORM contribuicoes - filtro por competencia",
            "Contribuicoes / ORM",
            lambda: list(
                NativeContribution.objects.filter(competence__icontains=(samples.contribution_competences[0] if samples.contribution_competences else "202"))
                .values_list("legacy_id", flat=True)[:5]
            ),
        ),
        (
            "ORM contribuicoes - datas e ordenacao",
            "Contribuicoes / PostgreSQL compatibility",
            lambda: list(
                NativeContribution.objects.exclude(received_at__isnull=True)
                .order_by("-received_at", "-legacy_id")
                .values_list("legacy_id", flat=True)[:5]
            ),
        ),
        (
            "ORM recibos - nulos e vazios em e-mail",
            "Recibos / PostgreSQL compatibility",
            lambda: ReceiptSnapshot.objects.filter(Q(person_email__isnull=True) | Q(person_email="")).count(),
        ),
        (
            "ORM recibos - ordenacao por emissao",
            "Recibos / PostgreSQL compatibility",
            lambda: list(ReceiptSnapshot.objects.order_by("-emission_date", "-legacy_id").values_list("legacy_id", flat=True)[:5]),
        ),
    ]

    for item, area, callback in checks:
        started = time.perf_counter()
        try:
            payload = callback()
        except Exception as exc:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            flags = _problem_flags(str(exc), str(exc))
            results.append(
                AuditResult(
                    item=item,
                    result="FAIL",
                    probable_area=area,
                    details="Consulta ORM levantou excecao ao rodar no ambiente atual.",
                    error=str(exc),
                    traceback_summary=_short_exception(type(exc).__name__, str(exc)),
                    response_time_ms=elapsed_ms,
                    user_label=samples.user_label,
                    method="ORM",
                    app_name=_infer_app_from_area(area),
                    view_name="ORM",
                    probable_postgres=flags["postgres"],
                    probable_encoding=flags["encoding"],
                    probable_missing_attr=flags["missing_attr"],
                    target="ORM",
                )
            )
            continue
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        results.append(
            AuditResult(
                item=item,
                result="OK",
                probable_area=area,
                details=f"Resultado amostral: {_preview(payload)}",
                response_time_ms=elapsed_ms,
                user_label=samples.user_label,
                method="ORM",
                app_name=_infer_app_from_area(area),
                view_name="ORM",
                target="ORM",
            )
        )


def _discover_web_routes() -> list[RouteInfo]:
    routes: list[RouteInfo] = []

    def _walk(patterns: Iterable[URLPattern | URLResolver], prefix: str = "", namespaces: tuple[str, ...] = (), app_name: str = "") -> None:
        for pattern in patterns:
            pattern_route = _pattern_route(pattern.pattern)
            if isinstance(pattern, URLPattern):
                raw_route = prefix + pattern_route
                if raw_route.startswith("admin/") or raw_route.startswith("api/"):
                    continue
                if not any(raw_route.startswith(prefix_item) for prefix_item in SAFE_ROUTE_PREFIXES):
                    continue
                route_name = ":".join([*namespaces, pattern.name]) if pattern.name and namespaces else (pattern.name or "")
                namespace_label = ":".join(namespaces)
                routes.append(
                    RouteInfo(
                        path_template="/" + raw_route.lstrip("/"),
                        route_name=route_name,
                        namespace=namespace_label,
                        app_name=app_name or (namespaces[0] if namespaces else ""),
                        lookup_str=getattr(pattern, "lookup_str", ""),
                    )
                )
                continue
            next_prefix = prefix + pattern_route
            next_namespaces = namespaces + ((pattern.namespace,) if pattern.namespace else ())
            next_app_name = app_name or pattern.app_name or (pattern.namespace or "")
            _walk(list(pattern.url_patterns), prefix=next_prefix, namespaces=next_namespaces, app_name=next_app_name)

    _walk(get_resolver().url_patterns)
    routes.sort(key=lambda item: (item.app_name, item.path_template, item.route_name))
    return routes


def _expand_route_to_urls(route: RouteInfo, samples: EntitySamples) -> list[str]:
    raw_path = route.path_template
    placeholders = list(PLACEHOLDER_RE.finditer(raw_path))
    if not placeholders:
        return [raw_path]

    values_per_placeholder: list[list[str]] = []
    for match in placeholders:
        name = str(match.group("name") or "")
        values = _values_for_placeholder(name=name, route=route, samples=samples)
        if not values:
            return []
        values_per_placeholder.append(values[:2])

    variants: list[str] = []

    def _build(index: int, current: str) -> None:
        if index >= len(placeholders):
            variants.append(current)
            return
        match = placeholders[index]
        placeholder = match.group(0)
        for value in values_per_placeholder[index]:
            _build(index + 1, current.replace(placeholder, value, 1))

    _build(0, raw_path)
    return variants[:4]


def _values_for_placeholder(*, name: str, route: RouteInfo, samples: EntitySamples) -> list[str]:
    path = route.path_template
    if name == "kind":
        return ["statement"]
    if name in {"person_id", "pk"}:
        return [str(value) for value in samples.person_ids]
    if name == "contribution_id":
        return [str(value) for value in samples.contribution_ids]
    if name == "receipt_id":
        return [str(value) for value in samples.receipt_ids]
    if name == "contributor_id":
        return [str(value) for value in samples.contributor_ids]
    if name == "envelope_id":
        if "/contributions/envelopes/" in path and "/edit/" in path:
            return [str(value) for value in samples.envelope_edit_ids]
        if "/contributions/envelopes/" in path and ("/launch/" in path or "/ignore/" in path):
            return [str(value) for value in samples.envelope_launch_ids]
        return [str(value) for value in samples.envelope_ids]
    if name == "update_id":
        return [str(value) for value in samples.envelope_profile_update_ids]
    if name == "trash_id":
        return []
    if name == "movement_id":
        return [str(value) for value in samples.statement_movement_ids]
    if name == "lot_id":
        if "/people/imports/" in path:
            return [str(value) for value in samples.people_import_lot_ids]
        if "/contributions/envelopes/lots/" in path:
            return [str(value) for value in samples.envelope_lot_ids]
        if "/imports/" in path:
            return [str(value) for value in samples.statement_lot_ids]
        return []
    return []


def _pattern_route(pattern: Any) -> str:
    route = getattr(pattern, "_route", None)
    if route is not None:
        return str(route)

    raw = str(pattern)
    raw = raw.lstrip("^")
    raw = raw.replace("\\Z", "").rstrip("$")
    raw = re.sub(r"\(\?P<([^>]+)>[^)]+\)", r"<\1>", raw)
    raw = raw.replace("\\/", "/")
    return raw


def _request_url(client: Client, url: str, *, user_label: str, route: RouteInfo | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    response = client.get(url, HTTP_HOST="localhost", follow=False)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    resolver_match = getattr(getattr(response, "wsgi_request", None), "resolver_match", None)
    templates = ", ".join(
        sorted(
            {
                str(getattr(template, "name", "") or "")
                for template in getattr(response, "templates", []) or []
                if str(getattr(template, "name", "") or "").strip()
            }
        )
    )
    payload = _response_bytes(response)
    body = payload.decode("utf-8", errors="replace")
    exc_summary, exc_detail = _response_exception_details(response)
    content_type = response.headers.get("Content-Type", "")
    textual_body = "" if _is_binary_content_type(content_type) else body
    flags = _problem_flags(exc_detail or textual_body, textual_body)
    return {
        "response": response,
        "url": url,
        "user_label": user_label,
        "elapsed_ms": elapsed_ms,
        "status_code": int(response.status_code or 0),
        "content_type": content_type,
        "body": body,
        "resolver_view_name": getattr(resolver_match, "view_name", "") if resolver_match else "",
        "resolver_app_names": ",".join(getattr(resolver_match, "app_names", []) or []) if resolver_match else "",
        "resolver_namespaces": ",".join(getattr(resolver_match, "namespaces", []) or []) if resolver_match else "",
        "lookup_str": getattr(resolver_match, "_func_path", "") if resolver_match else (route.lookup_str if route else ""),
        "templates": templates,
        "payload": payload,
        "exception_summary": exc_summary,
        "exception_detail": exc_detail,
        "probable_postgres": flags["postgres"],
        "probable_encoding": flags["encoding"],
        "probable_missing_attr": flags["missing_attr"],
        "route": route,
    }


def _record_page_check(
    results: list[AuditResult],
    request_data: dict[str, Any],
    *,
    item: str,
    area: str,
    expected_kind: str,
    must_contain: str | None = None,
    expected_headers: tuple[str, ...] = (),
    encoding_probe: bool = False,
) -> None:
    response = request_data["response"]
    body = str(request_data["body"] or "")
    content_type = str(request_data["content_type"] or "")
    payload = bytes(request_data.get("payload") or b"")
    status_code = int(request_data["status_code"] or 0)
    suspicious = _find_mojibake(body) if encoding_probe else ""

    if status_code >= 500:
        result = "FAIL"
        details = f"HTTP {status_code} · {content_type}"
        error = request_data["exception_detail"] or body[:600]
    elif expected_kind == "html":
        ok = status_code == 200 and "text/html" in content_type
        if ok and must_contain and must_contain not in body:
            ok = False
            error = f"Conteudo esperado nao encontrado: {must_contain}"
        else:
            error = ""
        if ok and suspicious:
            ok = False
            error = f"Padrao suspeito de encoding encontrado: {suspicious}"
        result = "OK" if ok else "FAIL"
        details = f"HTTP {status_code} · {content_type}"
    elif expected_kind == "json":
        ok = status_code == 200 and "application/json" in content_type
        error = "" if ok else body[:600]
        result = "OK" if ok else "FAIL"
        details = f"HTTP {status_code} · {content_type}"
    elif expected_kind == "pdf":
        ok = status_code == 200 and content_type.startswith("application/pdf") and payload.startswith(b"%PDF") and len(payload) > 100
        error = "" if ok else request_data["exception_detail"] or body[:600]
        result = "OK" if ok else "FAIL"
        details = f"HTTP {status_code} · {content_type} · {len(payload)} bytes"
    elif expected_kind == "csv":
        header_line = body.lstrip("\ufeff").splitlines()[0] if body else ""
        ok = status_code == 200 and content_type.startswith("text/csv") and payload.startswith(b"\xef\xbb\xbf") and len(payload) > 3
        if ok and expected_headers:
            for column in expected_headers:
                if column not in header_line:
                    ok = False
                    break
        if ok and _find_mojibake(body):
            ok = False
        error = "" if ok else body[:600]
        result = "OK" if ok else "FAIL"
        details = f"HTTP {status_code} · {content_type} · cabecalho={header_line!r}"
    elif expected_kind == "xlsx":
        ok = status_code == 200 and payload.startswith(b"PK") and len(payload) > 50
        error = "" if ok else request_data["exception_detail"] or body[:600]
        result = "OK" if ok else "FAIL"
        details = f"HTTP {status_code} · {content_type} · {len(payload)} bytes"
    elif expected_kind == "image":
        ok = status_code == 200 and content_type.startswith("image/") and len(payload) > 50
        error = "" if ok else request_data["exception_detail"] or body[:600]
        result = "OK" if ok else "WARN"
        details = f"HTTP {status_code} · {content_type} · {len(payload)} bytes"
    else:
        result = _generic_result_for_route(status_code)
        details = f"HTTP {status_code} · {content_type or '-'}"
        location = response.headers.get("Location", "")
        if location:
            details = f"{details} -> {location}"
        error = request_data["exception_detail"] if result == "FAIL" else ""

    route = request_data.get("route")
    results.append(
        AuditResult(
            item=item,
            result=result,
            probable_area=area,
            url=request_data["url"],
            user_label=request_data["user_label"],
            status_code=status_code,
            response_time_ms=int(request_data["elapsed_ms"] or 0),
            route_name=(route.route_name if route else "") or request_data["resolver_view_name"],
            namespace=(route.namespace if route else "") or request_data["resolver_namespaces"],
            app_name=(route.app_name if route else "") or request_data["resolver_app_names"] or _infer_app_from_area(area),
            view_name=request_data["lookup_str"] or request_data["resolver_view_name"],
            template_names=request_data["templates"],
            details=details,
            error=error,
            traceback_summary=request_data["exception_summary"],
            probable_postgres=bool(request_data["probable_postgres"]),
            probable_encoding=bool(request_data["probable_encoding"] or suspicious),
            probable_missing_attr=bool(request_data["probable_missing_attr"]),
            target=request_data["url"],
        )
    )


def _record_guard_check(results: list[AuditResult], request_data: dict[str, Any], *, item: str, area: str) -> None:
    location = request_data["response"].headers.get("Location", "")
    ok = request_data["status_code"] in {301, 302} and "/accounts/login/" in location
    results.append(
        AuditResult(
            item=item,
            result="OK" if ok else "FAIL",
            probable_area=area,
            url=request_data["url"],
            user_label=request_data["user_label"],
            status_code=int(request_data["status_code"] or 0),
            response_time_ms=int(request_data["elapsed_ms"] or 0),
            details=f"HTTP {request_data['status_code']} -> {location or '-'}",
            error="" if ok else "Rota protegida nao redirecionou para o login como esperado.",
            target=request_data["url"],
            app_name="accounts",
            view_name="login_guard",
        )
    )


def _request_post_url(
    url: str,
    payload: dict[str, Any],
    *,
    user: Any | None,
    user_label: str,
    rollback: bool,
) -> dict[str, Any]:
    client = Client(raise_request_exception=False)
    if user is not None:
        client.force_login(user)
    started = time.perf_counter()
    if rollback:
        with transaction.atomic():
            response = client.post(url, payload, HTTP_HOST="localhost", follow=False)
            transaction.set_rollback(True)
    else:
        response = client.post(url, payload, HTTP_HOST="localhost", follow=False)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    resolver_match = getattr(getattr(response, "wsgi_request", None), "resolver_match", None)
    content_type = response.headers.get("Content-Type", "")
    payload_bytes = _response_bytes(response)
    body = payload_bytes.decode("utf-8", errors="replace")
    textual_body = "" if _is_binary_content_type(content_type) else body
    exc_summary, exc_detail = _response_exception_details(response)
    flags = _problem_flags(exc_detail or textual_body, textual_body)
    return {
        "response": response,
        "url": url,
        "user_label": user_label,
        "elapsed_ms": elapsed_ms,
        "status_code": int(response.status_code or 0),
        "content_type": content_type,
        "body": body,
        "resolver_view_name": getattr(resolver_match, "view_name", "") if resolver_match else "",
        "resolver_app_names": ",".join(getattr(resolver_match, "app_names", []) or []) if resolver_match else "",
        "resolver_namespaces": ",".join(getattr(resolver_match, "namespaces", []) or []) if resolver_match else "",
        "lookup_str": getattr(resolver_match, "_func_path", "") if resolver_match else "",
        "templates": "",
        "payload": payload_bytes,
        "exception_summary": exc_summary,
        "exception_detail": exc_detail,
        "probable_postgres": flags["postgres"],
        "probable_encoding": flags["encoding"],
        "probable_missing_attr": flags["missing_attr"],
        "route": None,
    }


def _response_exception_details(response: Any) -> tuple[str, str]:
    exc_info = getattr(response, "exc_info", None)
    if not exc_info:
        return "", ""
    exc_type, exc_value, exc_tb = exc_info
    summary = _short_exception(getattr(exc_type, "__name__", "Exception"), str(exc_value or ""))
    if exc_tb is None:
        return summary, summary
    frames = traceback.extract_tb(exc_tb)
    if not frames:
        return summary, summary
    last_frame = frames[-1]
    detail = f"{summary} @ {last_frame.filename}:{last_frame.lineno} in {last_frame.name}"
    return summary, detail


def _problem_flags(error_text: str, body: str) -> dict[str, bool]:
    haystack = f"{error_text}\n{body}".lower()
    return {
        "postgres": any(
            token in haystack
            for token in (
                "programmingerror",
                "operator does not exist",
                "cannot resolve keyword",
                "fielderror",
                "postgres",
                "distinct on",
                "does not exist",
                "psycopg",
                "cast",
            )
        ),
        "encoding": any(token in (error_text or body) for token in SUSPICIOUS_MOJIBAKE_TOKENS)
        or "unicode" in haystack
        or "encoding" in haystack,
        "missing_attr": "has no attribute" in haystack or "cannot resolve keyword" in haystack or "attributeerror" in haystack,
    }


def _response_text(response: Any) -> str:
    payload = _response_bytes(response)
    return payload.decode("utf-8", errors="replace")


def _response_bytes(response: Any) -> bytes:
    try:
        if getattr(response, "streaming", False):
            return b"".join(response.streaming_content)
        return response.content
    except Exception:
        return b""


def _record_count_result(
    results: list[AuditResult],
    *,
    item: str,
    area: str,
    count: int,
    samples: EntitySamples,
) -> None:
    results.append(
        AuditResult(
            item=item,
            result="OK" if count > 0 else "WARN",
            probable_area=area,
            details=f"{count} registro(s).",
            user_label=samples.user_label,
            app_name=_infer_app_from_area(area),
            view_name="totals",
            target=item,
        )
    )


def _date_range_q(field_name: str, date_start: str, date_end: str) -> Q:
    query = Q()
    if date_start:
        query &= Q(**{f"{field_name}__gte": date_start})
    if date_end:
        query &= Q(**{f"{field_name}__lte": date_end})
    return query


def _decimal_or_zero(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value or "0"))
    except Exception:
        return Decimal("0.00")


def _same_money(left: Decimal, right: Decimal) -> bool:
    return abs(_decimal_or_zero(left) - _decimal_or_zero(right)) <= Decimal("0.01")


def _missing_person_links_count() -> int:
    known_people = PersonSnapshot.objects.values_list("legacy_id", flat=True)
    return NativeContribution.objects.exclude(person_legacy_id__isnull=True).exclude(person_legacy_id=0).exclude(person_legacy_id__in=known_people).count()


def _missing_receipt_people_count() -> int:
    known_people = PersonSnapshot.objects.values_list("legacy_id", flat=True)
    return ReceiptSnapshot.objects.exclude(person_legacy_id__isnull=True).exclude(person_legacy_id=0).exclude(person_legacy_id__in=known_people).count()


def _missing_envelope_lot_count() -> int:
    known_lots = NativeEnvelopeLot.objects.values_list("legacy_id", flat=True)
    return NativeEnvelope.objects.exclude(native_lot_legacy_id__isnull=True).exclude(native_lot_legacy_id=0).exclude(native_lot_legacy_id__in=known_lots).count()


def _missing_envelope_item_contribution_count() -> int:
    known_contributions = NativeContribution.objects.values_list("legacy_id", flat=True)
    return NativeEnvelopeItem.objects.exclude(contribution_legacy_id__isnull=True).exclude(contribution_legacy_id=0).exclude(contribution_legacy_id__in=known_contributions).count()


def _missing_statement_contribution_links_count() -> int:
    known_contributions = NativeContribution.objects.values_list("legacy_id", flat=True)
    return StatementImportPilotMovement.objects.exclude(imported_contribution_legacy_id__isnull=True).exclude(imported_contribution_legacy_id=0).exclude(imported_contribution_legacy_id__in=known_contributions).count()


def _record_legacy_parity_checks(results: list[AuditResult], *, samples: EntitySamples) -> None:
    legacy_path = Path(getattr(settings, "POWER_CHURCH_LEGACY_DB_PATH", ""))
    if not legacy_path.exists():
        results.append(
            AuditResult(
                item="Paridade com banco legado",
                result="WARN",
                probable_area="PostgreSQL compatibility / legado",
                details=f"Banco legado nao encontrado em {legacy_path}.",
                user_label=samples.user_label,
                app_name="runtime",
                view_name="legacy_parity",
                target=str(legacy_path),
            )
        )
        return
    try:
        with sqlite3.connect(str(legacy_path)) as conn:
            conn.row_factory = sqlite3.Row
            legacy_people = int(conn.execute("SELECT COUNT(*) FROM pessoas WHERE ativo = 1").fetchone()[0] or 0)
            legacy_contributions = int(conn.execute("SELECT COUNT(*) FROM contribuicoes WHERE ativo = 1").fetchone()[0] or 0)
            legacy_receipts = int(conn.execute("SELECT COUNT(*) FROM recibos").fetchone()[0] or 0)
            legacy_sum = _decimal_or_zero(conn.execute("SELECT COALESCE(SUM(valor), 0) FROM contribuicoes WHERE ativo = 1").fetchone()[0])
    except Exception as exc:
        results.append(
            AuditResult(
                item="Paridade com banco legado",
                result="WARN",
                probable_area="PostgreSQL compatibility / legado",
                details=f"Leitura do legado falhou: {exc}",
                user_label=samples.user_label,
                app_name="runtime",
                view_name="legacy_parity",
                target=str(legacy_path),
            )
        )
        return

    comparisons = [
        (
            "Paridade legado x Postgres - pessoas",
            legacy_people,
            PersonSnapshot.objects.filter(is_active=True).count(),
            "Pessoas / paridade legado",
        ),
        (
            "Paridade legado x Postgres - contribuicoes",
            legacy_contributions,
            NativeContribution.objects.filter(is_active=True).count(),
            "Contribuicoes / paridade legado",
        ),
        (
            "Paridade legado x Postgres - recibos",
            legacy_receipts,
            ReceiptSnapshot.objects.count(),
            "Recibos / paridade legado",
        ),
    ]
    for item, legacy_total, current_total, area in comparisons:
        results.append(
            AuditResult(
                item=item,
                result="OK" if legacy_total == current_total else "WARN",
                probable_area=area,
                details=f"legado={legacy_total} · postgres={current_total}",
                user_label=samples.user_label,
                app_name=_infer_app_from_area(area),
                view_name="legacy_parity",
                target=item,
            )
        )
    current_sum = _decimal_or_zero(NativeContribution.objects.filter(is_active=True).aggregate(total=Sum("amount"))["total"])
    results.append(
        AuditResult(
            item="Paridade legado x Postgres - soma financeira de contribuicoes",
            result="OK" if _same_money(legacy_sum, current_sum) else "WARN",
            probable_area="Contribuicoes / paridade legado",
            details=f"legado={legacy_sum} · postgres={current_sum}",
            user_label=samples.user_label,
            app_name="contributions",
            view_name="legacy_parity",
            target="contribution_sum",
        )
    )


def _record_performance_findings(results: list[AuditResult]) -> None:
    slow_candidates = [
        item
        for item in results
        if item.result == "OK"
        and item.method == "GET"
        and item.url
        and (
            item.response_time_ms >= 2000
            or ("pdf" in item.url and item.response_time_ms >= 700)
            or ("export" in item.url and item.response_time_ms >= 700)
        )
    ]
    for item in sorted(slow_candidates, key=lambda row: row.response_time_ms, reverse=True)[:8]:
        results.append(
            AuditResult(
                item=f"Performance - endpoint lento: {item.item}",
                result="WARN",
                probable_area="Performance / possivel query pesada",
                url=item.url,
                user_label=item.user_label,
                status_code=item.status_code,
                response_time_ms=item.response_time_ms,
                method=item.method,
                route_name=item.route_name,
                namespace=item.namespace,
                app_name=item.app_name,
                view_name=item.view_name,
                template_names=item.template_names,
                details=f"Resposta em {item.response_time_ms} ms. Revisar query pesada ou N+1.",
                target=item.target or item.url,
            )
        )


def _is_binary_content_type(content_type: str) -> bool:
    lowered = str(content_type or "").lower()
    return (
        lowered.startswith("application/pdf")
        or lowered.startswith("image/")
        or "spreadsheetml" in lowered
        or lowered.startswith("application/octet-stream")
    )


def _route_inventory(routes: list[RouteInfo]) -> dict[str, Any]:
    by_app: dict[str, int] = {}
    by_namespace: dict[str, int] = {}
    for route in routes:
        by_app[route.app_name or "root"] = by_app.get(route.app_name or "root", 0) + 1
        by_namespace[route.namespace or "root"] = by_namespace.get(route.namespace or "root", 0) + 1
    return {
        "named_routes": len(routes),
        "functional_routes": len(routes),
        "by_app": dict(sorted(by_app.items())),
        "by_namespace": dict(sorted(by_namespace.items())),
    }


def _render_markdown_report(
    *,
    results: list[AuditResult],
    samples: EntitySamples,
    route_inventory: dict[str, Any],
    started_at: Any,
    finished_at: Any,
) -> str:
    summary = _result_summary(results)
    failures = [item for item in results if item.result == "FAIL"]
    grouped_failures = _group_failures(results)
    top_failures = _top_failures(failures)
    slow_items = sorted(
        [item for item in results if "Performance / possivel query pesada" in item.probable_area],
        key=lambda row: row.response_time_ms,
        reverse=True,
    )

    lines = [
        "# Regression Audit",
        "",
        f"- Inicio: {started_at.strftime('%d/%m/%Y %H:%M:%S')}",
        f"- Fim: {finished_at.strftime('%d/%m/%Y %H:%M:%S')}",
        f"- Banco: `{connection.vendor}`",
        f"- Usuario usado no force_login: `{samples.user_label}`",
        f"- Rotas nomeadas inventariadas: `{route_inventory['named_routes']}`",
        f"- Rotas web funcionais inventariadas: `{route_inventory['functional_routes']}`",
        "",
        "## Resumo",
        "",
        f"- Total de verificacoes: `{summary['total']}`",
        f"- OK: `{summary['OK']}`",
        f"- FAIL: `{summary['FAIL']}`",
        f"- WARN: `{summary['WARN']}`",
        f"- SKIP: `{summary['SKIP']}`",
        f"- Areas com falha: `{', '.join(summary['failed_areas']) or '-'}`",
        "",
        "## Amostras usadas",
        "",
        f"- Pessoas: `{samples.person_ids[:5]}`",
        f"- Pessoas com acento: `{samples.accented_people[:3]}`",
        f"- Contribuicoes: `{samples.contribution_ids[:5]}`",
        f"- Pessoas com contribuicoes: `{samples.contribution_person_ids[:5]}`",
        f"- Recibos: `{samples.receipt_ids[:5]}`",
        f"- Pessoas com recibos: `{samples.receipt_person_ids[:5]}`",
        f"- Envelopes: `{samples.envelope_ids[:5]}`",
        f"- Lotes de envelopes: `{samples.envelope_lot_ids[:5]}`",
        f"- Lotes de importacao de pessoas: `{samples.people_import_lot_ids[:5]}`",
        f"- Lotes de extrato: `{samples.statement_lot_ids[:5]}`",
        f"- Movimentos de extrato: `{samples.statement_movement_ids[:5]}`",
        "",
        "## Inventario De Rotas",
        "",
        "| App | Quantidade |",
        "| --- | --- |",
    ]
    for app_name, count in route_inventory["by_app"].items():
        lines.append(f"| {_md(app_name)} | `{count}` |")
    lines.extend(
        [
            "",
            "## Itens Testados",
            "",
            "| Item | Resultado | URL | Usuario | HTTP | Tempo ms | Area | App | View | Template | Flags | Erro |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for item in results:
        flags = []
        if item.probable_postgres:
            flags.append("postgres")
        if item.probable_encoding:
            flags.append("encoding")
        if item.probable_missing_attr:
            flags.append("missing_attr")
        lines.append(
            "| {item} | {result} | {url} | {user} | {http} | {time} | {area} | {app} | {view} | {template} | {flags} | {error} |".format(
                item=_md(item.item),
                result=_md(item.result),
                url=_md(item.url or item.target or "-"),
                user=_md(item.user_label or "-"),
                http=_md(str(item.status_code or "-")),
                time=_md(str(item.response_time_ms or 0)),
                area=_md(item.probable_area),
                app=_md(item.app_name or "-"),
                view=_md(item.view_name or item.route_name or "-"),
                template=_md(item.template_names or "-"),
                flags=_md(", ".join(flags) or "-"),
                error=_md(item.traceback_summary or item.error or item.details or "-"),
            )
        )

    lines.extend(["", "## Top falhas para corrigir primeiro", ""])
    if top_failures:
        for index, item in enumerate(top_failures, start=1):
            lines.append(
                f"{index}. `{item.probable_area}` {item.item} · HTTP {item.status_code or '-'} · "
                f"{item.traceback_summary or item.error or item.details or 'sem detalhe'}"
            )
    else:
        lines.append("1. Nenhuma falha bloqueadora foi detectada nesta rodada.")

    lines.extend(["", "## Falhas agrupadas", ""])
    for group_name in [
        "Pessoas",
        "Contribuições",
        "Recibos",
        "Relatórios",
        "Imports",
        "Encoding",
        "PostgreSQL compatibility",
        "Templates/Views",
    ]:
        group_items = grouped_failures.get(group_name, [])
        lines.append(f"### {group_name}")
        lines.append("")
        if not group_items:
            lines.append("- Nenhuma falha agrupada nesta area.")
            lines.append("")
            continue
        for item in group_items:
            lines.append(
                f"- `{item.item}` · `{item.url or item.target or '-'}` · HTTP `{item.status_code or '-'}` · "
                f"{item.traceback_summary or item.error or item.details or 'sem detalhe'}"
            )
        lines.append("")

    lines.extend(["## Performance", ""])
    if slow_items:
        for item in slow_items[:10]:
            lines.append(
                f"- `{item.item}` · `{item.url or item.target or '-'}` · `{item.response_time_ms} ms` · "
                f"{item.details or 'revisar query pesada'}"
            )
    else:
        lines.append("- Nenhum endpoint excedeu o limiar de lentidao configurado nesta rodada.")
    lines.append("")

    return "\n".join(lines) + "\n"


def _group_failures(results: list[AuditResult]) -> dict[str, list[AuditResult]]:
    groups = {
        "Pessoas": [],
        "Contribuições": [],
        "Recibos": [],
        "Relatórios": [],
        "Imports": [],
        "Encoding": [],
        "PostgreSQL compatibility": [],
        "Templates/Views": [],
    }
    for item in results:
        if item.result != "FAIL":
            continue
        area = item.probable_area.lower()
        if "pessoas" in area:
            groups["Pessoas"].append(item)
        if "contribui" in area or "envelop" in area or item.app_name in {"contributions", "contributors"}:
            groups["Contribuições"].append(item)
        if "recibo" in area or item.app_name == "receipts":
            groups["Recibos"].append(item)
        if "relat" in area or item.app_name == "reports":
            groups["Relatórios"].append(item)
        if "import" in area or "extrato" in area or item.app_name == "imports":
            groups["Imports"].append(item)
        if item.probable_encoding:
            groups["Encoding"].append(item)
        if item.probable_postgres:
            groups["PostgreSQL compatibility"].append(item)
        if item.probable_missing_attr or "template" in (item.traceback_summary or "").lower() or "view" in (item.view_name or "").lower():
            groups["Templates/Views"].append(item)
    return groups


def _top_failures(failures: list[AuditResult]) -> list[AuditResult]:
    def _priority(item: AuditResult) -> tuple[int, int, int]:
        return (
            0 if item.status_code >= 500 else 1,
            0 if item.probable_missing_attr else 1,
            -int(item.response_time_ms or 0),
        )

    return sorted(failures, key=_priority)[:10]


def _result_summary(results: list[AuditResult]) -> dict[str, Any]:
    counts = {"OK": 0, "FAIL": 0, "WARN": 0, "SKIP": 0}
    failed_areas: list[str] = []
    for item in results:
        counts[item.result] = counts.get(item.result, 0) + 1
        if item.result == "FAIL" and item.probable_area not in failed_areas:
            failed_areas.append(item.probable_area)
    counts["total"] = sum(counts.values())
    counts["failed_areas"] = failed_areas
    return counts


def _generic_result_for_route(status_code: int) -> str:
    if status_code >= 500:
        return "FAIL"
    if status_code in {200, 301, 302}:
        return "OK"
    if status_code in {403, 404, 405}:
        return "WARN"
    return "WARN"


def _build_date_ranges(raw_dates: list[str]) -> list[tuple[str, str]]:
    clean = [value for value in raw_dates if len(value) == 10]
    unique = _unique_preserve_order(clean)
    ranges: list[tuple[str, str]] = []
    if unique:
        ranges.append((unique[0], unique[0]))
        if len(unique) > 1:
            start = min(unique[0], unique[1])
            end = max(unique[0], unique[1])
            ranges.append((start, end))
    return ranges[:2]


def _collect_report_destinations() -> list[str]:
    options: list[str] = []
    rows = (
        NativeContribution.objects.filter(is_active=True)
        .exclude(campaign_legacy_id__isnull=True)
        .exclude(campaign_name="")
        .values("campaign_legacy_id")
        .distinct()[:2]
    )
    for row in rows:
        campaign_id = int(row.get("campaign_legacy_id") or 0)
        if campaign_id:
            options.append(f"campanha:{campaign_id}")
    rows = (
        NativeContribution.objects.filter(is_active=True)
        .exclude(contribution_type_legacy_id__isnull=True)
        .values("contribution_type_legacy_id")
        .distinct()[:2]
    )
    for row in rows:
        type_id = int(row.get("contribution_type_legacy_id") or 0)
        if type_id:
            options.append(f"tipo:{type_id}")
    return _unique_preserve_order(options)


def _group_label(app_name: str) -> str:
    mapping = {
        "people": "Pessoas",
        "contributors": "Contribuicoes",
        "contributions": "Contribuicoes",
        "receipts": "Recibos",
        "imports": "Imports",
        "reports": "Relatorios",
        "audit": "Auditoria",
        "accounts": "Acesso",
        "root": "Dashboard",
    }
    return mapping.get(app_name or "root", (app_name or "Dashboard").title())


def _infer_app_from_area(area: str) -> str:
    lowered = area.lower()
    if "pessoas" in lowered:
        return "people"
    if "contribui" in lowered or "envelop" in lowered:
        return "contributions"
    if "recibo" in lowered:
        return "receipts"
    if "import" in lowered or "extrato" in lowered:
        return "imports"
    if "relat" in lowered:
        return "reports"
    if "audit" in lowered:
        return "audit"
    if "acesso" in lowered:
        return "accounts"
    return "runtime"


def _preview(payload: Any) -> str:
    if isinstance(payload, (list, tuple)):
        return ", ".join(str(item) for item in list(payload)[:5]) or "vazio"
    return str(payload)


def _find_mojibake(text: str) -> str:
    for token in SUSPICIOUS_MOJIBAKE_TOKENS:
        if token in text:
            return token
    return ""


def _query_fragment(text: str) -> str:
    for raw_part in str(text or "").replace("/", " ").replace("-", " ").split():
        part = "".join(ch for ch in raw_part if ch.isalnum())
        if len(part) >= 3:
            return part[:12]
    return ""


def _unique_preserve_order(values: Iterable[Any]) -> list[Any]:
    result: list[Any] = []
    seen: set[Any] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _short_exception(exc_type: str, message: str) -> str:
    clean_message = " ".join(str(message or "").split())
    return f"{exc_type}: {clean_message}".strip(": ")


def _md(value: str) -> str:
    return str(value or "").replace("\n", "<br>").replace("|", "\\|")

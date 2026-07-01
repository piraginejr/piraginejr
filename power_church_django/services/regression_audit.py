from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
import time
import traceback
from typing import Any, Iterable

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection
from django.db.models import CharField, Q, Value
from django.db.models.functions import Cast, Coalesce, Lower
from django.test import Client, override_settings
from django.urls import URLPattern, URLResolver, get_resolver
from django.utils import timezone

from power_church_core.normalization import normalize_match_name
from power_church_django.apps.contributions.models import (
    NativeAuxContributor,
    NativeContribution,
    NativeEnvelope,
    NativeEnvelopeLot,
    NativeEnvelopeProfileUpdate,
    ReceiptDispatch,
    ReceiptSnapshot,
)
from power_church_django.apps.imports.models import StatementImportPilotLot, StatementImportPilotMovement
from power_church_django.apps.people.models import NativePeopleImportLot, PersonAddressSnapshot, PersonSnapshot


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
        _record_operator_scenarios(
            results,
            anonymous_client=anonymous_client,
            authenticated_client=authenticated_client,
            user_available=user is not None,
            samples=samples,
        )
        _record_discovered_route_checks(
            results,
            user_available=user is not None,
            samples=samples,
            routes=routes,
        )
        _record_query_checks(results, samples=samples)

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
        _record_page_check(
            results,
            _request_url(authenticated_client, f"/contributions/envelopes/{envelope_id}/launch/", user_label=samples.user_label),
            item=f"Envelope - lancamento #{envelope_id}",
            area="Contribuicoes",
            expected_kind="route",
        )
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

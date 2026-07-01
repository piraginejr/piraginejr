from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

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
    NativeContribution,
    NativeEnvelope,
    NativeEnvelopeLot,
    ReceiptSnapshot,
)
from power_church_django.apps.imports.models import StatementImportPilotLot, StatementImportPilotMovement
from power_church_django.apps.people.models import NativePeopleImportLot, PersonAddressSnapshot, PersonSnapshot


ACCENTED_CHARS = "áàâãéêíóôõúçÁÀÂÃÉÊÍÓÔÕÚÇ"
SUSPICIOUS_MOJIBAKE_TOKENS = ("Ã", "Â", "├", "┬", "�")


@dataclass(slots=True)
class AuditSample:
    user_label: str
    person_id: int = 0
    person_name: str = ""
    person_status: str = ""
    person_city: str = ""
    person_query: str = ""
    accented_person_id: int = 0
    accented_person_name: str = ""
    accented_city: str = ""
    contribution_id: int = 0
    contribution_person_id: int = 0
    contribution_competence: str = ""
    receipt_id: int = 0
    receipt_person_id: int = 0
    envelope_id: int = 0
    envelope_image_id: int = 0
    envelope_lot_id: int = 0
    people_import_lot_id: int = 0
    statement_lot_id: int = 0
    statement_movement_id: int = 0


@dataclass(slots=True)
class AuditResult:
    item: str
    result: str
    probable_area: str
    details: str = ""
    error: str = ""
    target: str = ""


def run_regression_audit(*, stdout: Any | None = None) -> tuple[Path, list[AuditResult]]:
    started_at = timezone.localtime()
    results: list[AuditResult] = []
    sample = _collect_samples()
    route_inventory = _route_inventory()
    report_dir = Path(getattr(settings, "REPO_ROOT", Path(settings.BASE_DIR).parent)) / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)

    allowed_hosts = list(dict.fromkeys([*getattr(settings, "ALLOWED_HOSTS", []), "testserver", "localhost", "127.0.0.1"]))
    with override_settings(ALLOWED_HOSTS=allowed_hosts):
        anonymous_client = Client(raise_request_exception=False)
        authenticated_client = Client(raise_request_exception=False)
        user = get_user_model().objects.filter(is_active=True).order_by("-is_superuser", "-is_staff", "id").first()
        if user is not None:
            authenticated_client.force_login(user)

        _record_runtime_checks(results, sample=sample)
        _record_route_checks(
            results,
            anonymous_client=anonymous_client,
            authenticated_client=authenticated_client,
            user_available=user is not None,
            sample=sample,
        )
        _record_query_checks(results, sample=sample)

    finished_at = timezone.localtime()
    report_path = report_dir / f"regression_audit_{started_at.strftime('%Y%m%d_%H%M%S')}.md"
    report_path.write_text(
        _render_markdown_report(
            results=results,
            sample=sample,
            route_inventory=route_inventory,
            started_at=started_at,
            finished_at=finished_at,
        ),
        encoding="utf-8",
    )
    if stdout is not None:
        ok_count = sum(1 for item in results if item.result == "OK")
        fail_count = sum(1 for item in results if item.result == "FAIL")
        warn_count = sum(1 for item in results if item.result == "WARN")
        skip_count = sum(1 for item in results if item.result == "SKIP")
        stdout.write(f"Relatorio salvo em {report_path}")
        stdout.write(f"Resumo: {ok_count} OK, {fail_count} FAIL, {warn_count} WARN, {skip_count} SKIP")
    return report_path, results


def _collect_samples() -> AuditSample:
    user = get_user_model().objects.filter(is_active=True).order_by("-is_superuser", "-is_staff", "id").first()
    person = PersonSnapshot.objects.order_by("normalized_name", "legacy_id").first()
    accented_person = _first_accented_person()
    address = PersonAddressSnapshot.objects.exclude(city="").order_by("person_id", "legacy_id").first()
    contribution = NativeContribution.objects.order_by("-received_at", "-legacy_id").first()
    contribution_person = (
        NativeContribution.objects.filter(person_legacy_id__isnull=False)
        .exclude(person_legacy_id=0)
        .order_by("-received_at", "-legacy_id")
        .first()
    )
    receipt = ReceiptSnapshot.objects.order_by("-emission_date", "-legacy_id").first()
    envelope = NativeEnvelope.objects.order_by("-competence_order", "-legacy_id").first()
    envelope_image = NativeEnvelope.objects.exclude(image_path="").order_by("-competence_order", "-legacy_id").first()
    envelope_lot = NativeEnvelopeLot.objects.order_by("-competence_order", "-legacy_id").first()
    people_import_lot = NativePeopleImportLot.objects.order_by("-legacy_id").first()
    statement_lot = StatementImportPilotLot.objects.order_by("-created_at", "-id").first()
    statement_movement = StatementImportPilotMovement.objects.order_by("-updated_at", "-id").first()

    return AuditSample(
        user_label=str(getattr(user, "username", "") or "sem_usuario"),
        person_id=int(getattr(person, "legacy_id", 0) or 0),
        person_name=str(getattr(person, "name", "") or ""),
        person_status=str(getattr(person, "status", "") or ""),
        person_city=str(getattr(address, "city", "") or ""),
        person_query=_query_fragment(str(getattr(accented_person, "name", "") or getattr(person, "name", "") or "joao")),
        accented_person_id=int(getattr(accented_person, "legacy_id", 0) or 0),
        accented_person_name=str(getattr(accented_person, "name", "") or ""),
        accented_city=str(getattr(_first_accented_city(), "city", "") or ""),
        contribution_id=int(getattr(contribution, "legacy_id", 0) or 0),
        contribution_person_id=int(getattr(contribution_person, "person_legacy_id", 0) or 0),
        contribution_competence=str(getattr(contribution, "competence", "") or ""),
        receipt_id=int(getattr(receipt, "legacy_id", 0) or 0),
        receipt_person_id=int(getattr(receipt, "person_legacy_id", 0) or 0),
        envelope_id=int(getattr(envelope, "legacy_id", 0) or 0),
        envelope_image_id=int(getattr(envelope_image, "legacy_id", 0) or 0),
        envelope_lot_id=int(getattr(envelope_lot, "legacy_id", 0) or 0),
        people_import_lot_id=int(getattr(people_import_lot, "legacy_id", 0) or 0),
        statement_lot_id=int(getattr(statement_lot, "id", 0) or 0),
        statement_movement_id=int(getattr(statement_movement, "id", 0) or 0),
    )


def _record_runtime_checks(results: list[AuditResult], *, sample: AuditSample) -> None:
    db_name = connection.settings_dict.get("NAME") or ""
    results.append(
        AuditResult(
            item="Runtime base Django/PostgreSQL",
            result="OK" if connection.vendor == "postgresql" else "WARN",
            probable_area="Runtime / banco",
            details=f"Banco atual: {connection.vendor} · name={db_name}",
            target="database",
        )
    )
    for label, queryset in (
        ("Pessoas snapshot", PersonSnapshot.objects.all()),
        ("Contribuicoes nativas", NativeContribution.objects.all()),
        ("Recibos snapshot", ReceiptSnapshot.objects.all()),
        ("Lotes de envelopes", NativeEnvelopeLot.objects.all()),
        ("Lotes de importacao de pessoas", NativePeopleImportLot.objects.all()),
        ("Lotes de extrato piloto", StatementImportPilotLot.objects.all()),
    ):
        count = int(queryset.count())
        results.append(
            AuditResult(
                item=f"Carga essencial: {label}",
                result="OK" if count > 0 else "WARN",
                probable_area="Carga / sincronizacao",
                details=f"{count} registro(s) disponiveis.",
                target=label,
            )
        )
    if not sample.user_label or sample.user_label == "sem_usuario":
        results.append(
            AuditResult(
                item="Usuario para auditoria autenticada",
                result="FAIL",
                probable_area="Acesso / autenticacao",
                details="Nao existe usuario ativo para force_login no comando.",
                target="/accounts/login/",
            )
        )
    else:
        results.append(
            AuditResult(
                item="Usuario para auditoria autenticada",
                result="OK",
                probable_area="Acesso / autenticacao",
                details=f"Cliente autenticado usando {sample.user_label}.",
                target="/accounts/login/",
            )
        )


def _record_route_checks(
    results: list[AuditResult],
    *,
    anonymous_client: Client,
    authenticated_client: Client,
    user_available: bool,
    sample: AuditSample,
) -> None:
    _check_login_page(results, anonymous_client)
    _check_login_guard(results, anonymous_client, path="/", area="Dashboard / autenticacao")
    _check_login_guard(results, anonymous_client, path="/people/", area="Pessoas / autenticacao")

    if not user_available:
        results.append(
            AuditResult(
                item="Rotas autenticadas principais",
                result="SKIP",
                probable_area="Acesso / autenticacao",
                details="Sem usuario ativo para force_login; a varredura autenticada foi pulada.",
            )
        )
        return

    for item, path, area in [
        ("Dashboard operacional", "/", "Dashboard"),
        ("Pessoas - lista", "/people/", "Pessoas"),
        ("Pessoas - familias", "/people/families/", "Familias"),
        ("Contribuintes auxiliares", "/contributors/", "Contribuintes auxiliares"),
        ("Contribuicoes - lista", "/contributions/", "Contribuicoes"),
        ("Envelopes - lista", "/contributions/envelopes/", "Envelopes"),
        ("Recibos - hub", "/receipts/", "Recibos"),
        ("Recibos - fila", "/receipts/queue/", "Fila de recibos"),
        ("Importacoes bancarias", "/imports/", "Extratos / importacoes"),
        ("Regras de centavos", "/imports/rules/", "Extratos / importacoes"),
        ("Auditoria", "/audit/", "Auditoria"),
        ("Relatorios", "/reports/", "Relatorios"),
        ("Relatorios por destino", "/reports/destinations/", "Relatorios"),
    ]:
        _check_html(results, authenticated_client, item=item, path=path, area=area)

    if sample.person_query:
        _check_html(
            results,
            authenticated_client,
            item="Pessoas - busca por texto",
            path=f"/people/?q={sample.person_query}",
            area="Pessoas / filtros",
            expect_text="text/html",
        )
        _check_json(
            results,
            authenticated_client,
            item="Pessoas - busca relacional JSON",
            path=f"/people/search/?q={sample.person_query}&person_id={sample.person_id or 0}",
            area="Pessoas / busca",
        )
        _check_html(
            results,
            authenticated_client,
            item="Contribuicoes - busca por texto",
            path=f"/contributions/?q={sample.person_query}",
            area="Contribuicoes / filtros",
        )
        _check_html(
            results,
            authenticated_client,
            item="Recibos - busca por texto",
            path=f"/receipts/?q={sample.person_query}",
            area="Recibos / filtros",
        )
        _check_html(
            results,
            authenticated_client,
            item="Relatorios - busca por texto",
            path=f"/reports/?q={sample.person_query}",
            area="Relatorios / filtros",
        )
        _check_html(
            results,
            authenticated_client,
            item="Relatorios destino - busca por texto",
            path=f"/reports/destinations/?q={sample.person_query}",
            area="Relatorios / filtros",
        )

    if sample.person_status:
        _check_html(
            results,
            authenticated_client,
            item="Pessoas - filtro por status",
            path=f"/people/?status={sample.person_status}",
            area="Pessoas / filtros",
        )

    if sample.person_city:
        _check_html(
            results,
            authenticated_client,
            item="Pessoas - filtro por cidade",
            path=f"/people/?city={sample.person_city}",
            area="Pessoas / filtros",
        )

    _check_export_csv(
        results,
        authenticated_client,
        item="Exportacao de pessoas CSV",
        path="/people/export/?format=csv&preset=cadastro_basico",
        area="Pessoas / exportacao",
        expected_headers=("ID", "Codigo", "Nome"),
    )
    _check_export_csv(
        results,
        authenticated_client,
        item="Exportacao dinamica CSV",
        path="/people/export/?format=csv&source=dynamic&column=nome&column=cidade",
        area="Pessoas / exportacao",
        expected_headers=("Nome", "Cidade"),
    )
    _check_export_xlsx(
        results,
        authenticated_client,
        item="Exportacao de pessoas XLSX",
        path="/people/export/?format=xlsx&preset=contatos",
        area="Pessoas / exportacao",
    )

    _check_pdf(
        results,
        authenticated_client,
        item="Relatorio PDF por periodo",
        path="/reports/contributions-period.pdf?inline=1",
        area="Relatorios / PDF",
    )
    _check_pdf(
        results,
        authenticated_client,
        item="Relatorio PDF por destino",
        path="/reports/contributions-destinations.pdf?inline=1",
        area="Relatorios / PDF",
    )

    if sample.person_id:
        _check_html(
            results,
            authenticated_client,
            item="Pessoa - detalhe",
            path=f"/people/{sample.person_id}/",
            area="Pessoas / ficha",
            ensure_no_mojibake=sample.person_id == sample.accented_person_id,
            must_contain=sample.person_name or None,
        )
    else:
        _skip(results, "Pessoa - detalhe", "Pessoas / ficha", "Nenhuma pessoa disponivel para abrir ficha.")

    if sample.accented_person_id and sample.accented_person_name:
        _check_html(
            results,
            authenticated_client,
            item="Pessoa - acento no HTML",
            path=f"/people/{sample.accented_person_id}/",
            area="Encoding / pessoas",
            must_contain=sample.accented_person_name,
            ensure_no_mojibake=True,
        )
    else:
        _skip(results, "Pessoa - acento no HTML", "Encoding / pessoas", "Nenhuma ficha com acento foi encontrada para validar encoding.")

    if sample.contribution_id:
        _check_html(
            results,
            authenticated_client,
            item="Contribuicao - detalhe",
            path=f"/contributions/{sample.contribution_id}/",
            area="Contribuicoes / detalhe",
        )
        _check_html(
            results,
            authenticated_client,
            item="Contribuicao - split",
            path=f"/contributions/{sample.contribution_id}/split/",
            area="Contribuicoes / split",
        )
    else:
        _skip(results, "Contribuicao - detalhe", "Contribuicoes / detalhe", "Nenhuma contribuicao disponivel.")
        _skip(results, "Contribuicao - split", "Contribuicoes / split", "Nenhuma contribuicao disponivel.")

    if sample.contribution_person_id:
        _check_html(
            results,
            authenticated_client,
            item="Extrato por pessoa - HTML",
            path=f"/contributions/statements/{sample.contribution_person_id}/",
            area="Contribuicoes / extrato",
        )
        _check_pdf(
            results,
            authenticated_client,
            item="Extrato por pessoa - PDF",
            path=f"/contributions/statements/{sample.contribution_person_id}/pdf/",
            area="Contribuicoes / extrato",
        )
    else:
        _skip(results, "Extrato por pessoa - HTML", "Contribuicoes / extrato", "Nenhuma pessoa com contribuicoes foi encontrada.")
        _skip(results, "Extrato por pessoa - PDF", "Contribuicoes / extrato", "Nenhuma pessoa com contribuicoes foi encontrada.")

    if sample.receipt_id:
        _check_html(
            results,
            authenticated_client,
            item="Recibo - detalhe",
            path=f"/receipts/{sample.receipt_id}/",
            area="Recibos / detalhe",
        )
        _check_pdf(
            results,
            authenticated_client,
            item="Recibo - PDF",
            path=f"/receipts/{sample.receipt_id}/pdf/",
            area="Recibos / PDF",
        )
    else:
        _skip(results, "Recibo - detalhe", "Recibos / detalhe", "Nenhum recibo disponivel.")
        _skip(results, "Recibo - PDF", "Recibos / PDF", "Nenhum recibo disponivel.")

    if sample.envelope_lot_id:
        _check_html(
            results,
            authenticated_client,
            item="Envelopes - detalhe do lote",
            path=f"/contributions/envelopes/lots/{sample.envelope_lot_id}/",
            area="Envelopes / lotes",
        )
        _check_redirect_or_html(
            results,
            authenticated_client,
            item="Envelopes - proximo pendente",
            path=f"/contributions/envelopes/lots/{sample.envelope_lot_id}/next/",
            area="Envelopes / lotes",
        )
    else:
        _skip(results, "Envelopes - detalhe do lote", "Envelopes / lotes", "Nenhum lote de envelopes disponivel.")
        _skip(results, "Envelopes - proximo pendente", "Envelopes / lotes", "Nenhum lote de envelopes disponivel.")

    if sample.envelope_id:
        _check_html(
            results,
            authenticated_client,
            item="Envelope - detalhe",
            path=f"/contributions/envelopes/{sample.envelope_id}/",
            area="Envelopes / detalhe",
        )
    else:
        _skip(results, "Envelope - detalhe", "Envelopes / detalhe", "Nenhum envelope disponivel.")

    if sample.envelope_image_id:
        _check_binary(
            results,
            authenticated_client,
            item="Envelope - imagem",
            path=f"/contributions/envelopes/{sample.envelope_image_id}/image/",
            area="Envelopes / anexos",
            expected_content_prefix="image/",
        )
    else:
        _skip(results, "Envelope - imagem", "Envelopes / anexos", "Nenhum envelope com imagem disponivel.")

    if sample.people_import_lot_id:
        _check_html(
            results,
            authenticated_client,
            item="Importacao de pessoas - lote",
            path=f"/people/imports/{sample.people_import_lot_id}/",
            area="Importacao de pessoas",
        )
        _check_html(
            results,
            authenticated_client,
            item="Importacao de pessoas - lote filtrado",
            path=f"/people/imports/{sample.people_import_lot_id}/?tipo=data_invalida&pendencia_status=abertas",
            area="Importacao de pessoas / filtros",
        )
    else:
        _skip(results, "Importacao de pessoas - lote", "Importacao de pessoas", "Nenhum lote de importacao de pessoas disponivel.")
        _skip(results, "Importacao de pessoas - lote filtrado", "Importacao de pessoas / filtros", "Nenhum lote de importacao de pessoas disponivel.")

    if sample.statement_lot_id:
        _check_html(
            results,
            authenticated_client,
            item="Extrato - lote",
            path=f"/imports/statement/{sample.statement_lot_id}/?backend=postgres_nativo",
            area="Extratos / lotes",
        )
        _check_html(
            results,
            authenticated_client,
            item="Extrato - lote pendencias",
            path=f"/imports/statement/{sample.statement_lot_id}/?backend=postgres_nativo&status=pendencias",
            area="Extratos / filtros",
        )
    else:
        _skip(results, "Extrato - lote", "Extratos / lotes", "Nenhum lote de extrato disponivel.")
        _skip(results, "Extrato - lote pendencias", "Extratos / filtros", "Nenhum lote de extrato disponivel.")

    if sample.statement_movement_id:
        _check_html(
            results,
            authenticated_client,
            item="Extrato - movimento",
            path=f"/imports/statement/movement/{sample.statement_movement_id}/?backend=postgres_nativo",
            area="Extratos / movimentos",
        )
    else:
        _skip(results, "Extrato - movimento", "Extratos / movimentos", "Nenhum movimento de extrato disponivel.")


def _record_query_checks(results: list[AuditResult], *, sample: AuditSample) -> None:
    query_term = sample.person_query or "joao"
    normalized_term = normalize_match_name(query_term)
    legacy_id_suffix = str(sample.person_id or 0)[-2:] if sample.person_id else ""

    checks: list[tuple[str, str, Callable[[], Any]]] = [
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
            "Pessoas / NULL",
            lambda: PersonSnapshot.objects.filter(Q(primary_email__isnull=True) | Q(primary_email="")).count(),
        ),
        (
            "ORM enderecos - cidade icontains",
            "Pessoas / filtros",
            lambda: list(PersonAddressSnapshot.objects.filter(city__icontains=sample.person_city or query_term).values_list("city", flat=True)[:5]),
        ),
        (
            "ORM pessoas - ordenacao com null/date",
            "Pessoas / ordenacao",
            lambda: list(
                PersonSnapshot.objects.annotate(birth_date_safe=Coalesce("birth_date_raw", Value("")))
                .order_by("birth_date_safe", "normalized_name", "legacy_id")
                .values_list("legacy_id", flat=True)[:5]
            ),
        ),
        (
            "ORM pessoas - cast legacy_id texto",
            "Pessoas / cast",
            lambda: list(
                PersonSnapshot.objects.annotate(legacy_id_text=Cast("legacy_id", output_field=CharField()))
                .filter(legacy_id_text__contains=legacy_id_suffix or "1")
                .values_list("legacy_id", flat=True)[:5]
            ),
        ),
        (
            "ORM pessoas - lower/coalesce",
            "Pessoas / texto",
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
                NativeContribution.objects.filter(competence__icontains=sample.contribution_competence or "202")
                .values_list("legacy_id", flat=True)[:5]
            ),
        ),
        (
            "ORM contribuicoes - datas e ordenacao",
            "Contribuicoes / datas",
            lambda: list(
                NativeContribution.objects.exclude(received_at__isnull=True)
                .order_by("-received_at", "-legacy_id")
                .values_list("legacy_id", flat=True)[:5]
            ),
        ),
        (
            "ORM recibos - nulos e vazios em e-mail",
            "Recibos / NULL",
            lambda: ReceiptSnapshot.objects.filter(Q(person_email__isnull=True) | Q(person_email="")).count(),
        ),
        (
            "ORM recibos - ordenacao por emissao",
            "Recibos / datas",
            lambda: list(ReceiptSnapshot.objects.order_by("-emission_date", "-legacy_id").values_list("legacy_id", flat=True)[:5]),
        ),
    ]

    for item, area, callback in checks:
        try:
            payload = callback()
        except Exception as exc:
            results.append(
                AuditResult(
                    item=item,
                    result="FAIL",
                    probable_area=area,
                    details="Consulta levantou excecao ao rodar no ORM atual.",
                    error=str(exc),
                    target="ORM",
                )
            )
            continue
        details = f"Resultado amostral: {_preview(payload)}"
        results.append(AuditResult(item=item, result="OK", probable_area=area, details=details, target="ORM"))


def _check_login_page(results: list[AuditResult], client: Client) -> None:
    response = client.get("/accounts/login/", HTTP_HOST="localhost")
    if response.status_code >= 500:
        results.append(
            AuditResult(
                item="Login publico",
                result="FAIL",
                probable_area="Acesso / autenticacao",
                details=f"HTTP {response.status_code}",
                error=_response_text(response),
                target="/accounts/login/",
            )
        )
        return
    ok = response.status_code == 200 and "text/html" in response.headers.get("Content-Type", "")
    results.append(
        AuditResult(
            item="Login publico",
            result="OK" if ok else "FAIL",
            probable_area="Acesso / autenticacao",
            details=f"HTTP {response.status_code} · {response.headers.get('Content-Type', '')}",
            error="" if ok else "Tela de login nao respondeu como HTML 200.",
            target="/accounts/login/",
        )
    )


def _check_login_guard(results: list[AuditResult], client: Client, *, path: str, area: str) -> None:
    response = client.get(path, HTTP_HOST="localhost", follow=False)
    location = response.headers.get("Location", "")
    ok = response.status_code in {301, 302} and "/accounts/login/" in location
    results.append(
        AuditResult(
            item=f"Protecao de login: {path}",
            result="OK" if ok else "FAIL",
            probable_area=area,
            details=f"HTTP {response.status_code} -> {location or '-'}",
            error="" if ok else "Rota protegida nao redirecionou para o login como esperado.",
            target=path,
        )
    )


def _check_html(
    results: list[AuditResult],
    client: Client,
    *,
    item: str,
    path: str,
    area: str,
    expect_text: str = "text/html",
    must_contain: str | None = None,
    ensure_no_mojibake: bool = False,
) -> None:
    response = client.get(path, HTTP_HOST="localhost", follow=False)
    content_type = response.headers.get("Content-Type", "")
    body = _response_text(response)
    suspicious = _find_mojibake(body) if ensure_no_mojibake else ""
    ok = response.status_code == 200 and expect_text in content_type and not suspicious
    error = ""
    if response.status_code >= 500:
        error = body
    elif response.status_code != 200:
        error = f"Status inesperado: {response.status_code}"
    elif expect_text not in content_type:
        error = f"Content-Type inesperado: {content_type}"
    elif must_contain and must_contain not in body:
        ok = False
        error = f"Conteudo esperado nao encontrado: {must_contain}"
    elif suspicious:
        ok = False
        error = f"Padrao suspeito de mojibake encontrado: {suspicious}"
    results.append(
        AuditResult(
            item=item,
            result="OK" if ok else "FAIL",
            probable_area=area,
            details=f"HTTP {response.status_code} · {content_type}",
            error=error,
            target=path,
        )
    )


def _check_json(results: list[AuditResult], client: Client, *, item: str, path: str, area: str) -> None:
    response = client.get(path, HTTP_HOST="localhost", follow=False)
    content_type = response.headers.get("Content-Type", "")
    body = _response_text(response)
    ok = response.status_code == 200 and "application/json" in content_type
    if ok:
        try:
            payload = response.json()
        except Exception as exc:
            ok = False
            body = str(exc)
        else:
            if not isinstance(payload, dict):
                ok = False
                body = "Resposta JSON nao retornou objeto."
    results.append(
        AuditResult(
            item=item,
            result="OK" if ok else "FAIL",
            probable_area=area,
            details=f"HTTP {response.status_code} · {content_type}",
            error="" if ok else body,
            target=path,
        )
    )


def _check_pdf(results: list[AuditResult], client: Client, *, item: str, path: str, area: str) -> None:
    response = client.get(path, HTTP_HOST="localhost", follow=False)
    content_type = response.headers.get("Content-Type", "")
    payload = response.content if not response.streaming else b"".join(response.streaming_content)
    ok = response.status_code == 200 and content_type.startswith("application/pdf") and payload.startswith(b"%PDF")
    results.append(
        AuditResult(
            item=item,
            result="OK" if ok else "FAIL",
            probable_area=area,
            details=f"HTTP {response.status_code} · {content_type} · {len(payload)} bytes",
            error="" if ok else _response_text(response),
            target=path,
        )
    )


def _check_export_csv(
    results: list[AuditResult],
    client: Client,
    *,
    item: str,
    path: str,
    area: str,
    expected_headers: tuple[str, ...],
) -> None:
    response = client.get(path, HTTP_HOST="localhost", follow=False)
    content_type = response.headers.get("Content-Type", "")
    payload = response.content
    text = payload.decode("utf-8", errors="replace")
    header_line = text.lstrip("\ufeff").splitlines()[0] if text else ""
    ok = response.status_code == 200 and content_type.startswith("text/csv") and payload.startswith(b"\xef\xbb\xbf")
    if ok:
        for column in expected_headers:
            if column not in header_line:
                ok = False
                break
    results.append(
        AuditResult(
            item=item,
            result="OK" if ok else "FAIL",
            probable_area=area,
            details=f"HTTP {response.status_code} · {content_type} · cabecalho={header_line!r}",
            error="" if ok else text[:400],
            target=path,
        )
    )


def _check_export_xlsx(results: list[AuditResult], client: Client, *, item: str, path: str, area: str) -> None:
    response = client.get(path, HTTP_HOST="localhost", follow=False)
    content_type = response.headers.get("Content-Type", "")
    payload = response.content
    ok = response.status_code == 200 and payload.startswith(b"PK")
    results.append(
        AuditResult(
            item=item,
            result="OK" if ok else "FAIL",
            probable_area=area,
            details=f"HTTP {response.status_code} · {content_type} · {len(payload)} bytes",
            error="" if ok else _response_text(response),
            target=path,
        )
    )


def _check_redirect_or_html(results: list[AuditResult], client: Client, *, item: str, path: str, area: str) -> None:
    response = client.get(path, HTTP_HOST="localhost", follow=False)
    content_type = response.headers.get("Content-Type", "")
    location = response.headers.get("Location", "")
    ok = response.status_code in {200, 301, 302} and response.status_code < 500
    details = f"HTTP {response.status_code} · {content_type or '-'}"
    if location:
        details = f"{details} -> {location}"
    results.append(
        AuditResult(
            item=item,
            result="OK" if ok else "FAIL",
            probable_area=area,
            details=details,
            error="" if ok else _response_text(response),
            target=path,
        )
    )


def _check_binary(
    results: list[AuditResult],
    client: Client,
    *,
    item: str,
    path: str,
    area: str,
    expected_content_prefix: str,
) -> None:
    response = client.get(path, HTTP_HOST="localhost", follow=False)
    content_type = response.headers.get("Content-Type", "")
    ok = response.status_code == 200 and content_type.startswith(expected_content_prefix)
    results.append(
        AuditResult(
            item=item,
            result="OK" if ok else "FAIL",
            probable_area=area,
            details=f"HTTP {response.status_code} · {content_type}",
            error="" if ok else _response_text(response),
            target=path,
        )
    )


def _skip(results: list[AuditResult], item: str, area: str, details: str) -> None:
    results.append(AuditResult(item=item, result="SKIP", probable_area=area, details=details))


def _first_accented_person() -> PersonSnapshot | None:
    for person in PersonSnapshot.objects.only("legacy_id", "name").order_by("legacy_id")[:5000]:
        if any(char in ACCENTED_CHARS for char in str(person.name or "")):
            return person
    return None


def _first_accented_city() -> PersonAddressSnapshot | None:
    for address in PersonAddressSnapshot.objects.exclude(city="").only("city").order_by("legacy_id")[:5000]:
        if any(char in ACCENTED_CHARS for char in str(address.city or "")):
            return address
    return None


def _query_fragment(text: str) -> str:
    for raw_part in str(text or "").replace("/", " ").replace("-", " ").split():
        part = "".join(ch for ch in raw_part if ch.isalnum())
        if len(part) >= 3:
            return part[:12]
    return "joao"


def _find_mojibake(text: str) -> str:
    for token in SUSPICIOUS_MOJIBAKE_TOKENS:
        if token in text:
            return token
    return ""


def _response_text(response: Any) -> str:
    try:
        if response.streaming:
            payload = b"".join(response.streaming_content)
        else:
            payload = response.content
    except Exception:
        return ""
    return payload.decode("utf-8", errors="replace")


def _preview(payload: Any) -> str:
    if isinstance(payload, (list, tuple)):
        return ", ".join(str(item) for item in list(payload)[:5]) or "vazio"
    return str(payload)


def _route_inventory() -> dict[str, int]:
    total_named = 0
    covered_prefixes = 0
    for route in _flatten_patterns(get_resolver().url_patterns):
        if route.name and not route.pattern.describe().startswith("'admin/"):
            total_named += 1
            if any(route.pattern.describe().startswith(prefix) for prefix in ("''", "'people/", "'contributors/", "'contributions/", "'receipts/", "'imports/", "'audit/", "'reports/")):
                covered_prefixes += 1
    return {"named_routes": total_named, "functional_routes": covered_prefixes}


def _flatten_patterns(patterns: list[URLPattern | URLResolver]) -> list[URLPattern]:
    items: list[URLPattern] = []
    for pattern in patterns:
        if isinstance(pattern, URLPattern):
            items.append(pattern)
            continue
        items.extend(_flatten_patterns(list(pattern.url_patterns)))
    return items


def _render_markdown_report(
    *,
    results: list[AuditResult],
    sample: AuditSample,
    route_inventory: dict[str, int],
    started_at: Any,
    finished_at: Any,
) -> str:
    ok_count = sum(1 for item in results if item.result == "OK")
    fail_count = sum(1 for item in results if item.result == "FAIL")
    warn_count = sum(1 for item in results if item.result == "WARN")
    skip_count = sum(1 for item in results if item.result == "SKIP")
    lines = [
        "# Regression Audit",
        "",
        f"- Inicio: {started_at.strftime('%d/%m/%Y %H:%M:%S')}",
        f"- Fim: {finished_at.strftime('%d/%m/%Y %H:%M:%S')}",
        f"- Banco: `{connection.vendor}`",
        f"- Usuario usado no force_login: `{sample.user_label}`",
        f"- Rotas nomeadas inventariadas: `{route_inventory['named_routes']}`",
        f"- Rotas funcionais sob prefixos operacionais: `{route_inventory['functional_routes']}`",
        "",
        "## Resumo",
        "",
        f"- OK: `{ok_count}`",
        f"- FAIL: `{fail_count}`",
        f"- WARN: `{warn_count}`",
        f"- SKIP: `{skip_count}`",
        "",
        "## Amostras usadas",
        "",
        f"- Pessoa base: `{sample.person_id}` {sample.person_name or '-'}",
        f"- Pessoa com acento: `{sample.accented_person_id}` {sample.accented_person_name or '-'}",
        f"- Contribuicao base: `{sample.contribution_id}`",
        f"- Recibo base: `{sample.receipt_id}`",
        f"- Envelope base: `{sample.envelope_id}`",
        f"- Lote de envelopes: `{sample.envelope_lot_id}`",
        f"- Lote de importacao de pessoas: `{sample.people_import_lot_id}`",
        f"- Lote de extrato: `{sample.statement_lot_id}`",
        f"- Movimento de extrato: `{sample.statement_movement_id}`",
        "",
        "## Itens testados",
        "",
        "| Item | Resultado | Area provavel | Alvo | Detalhes | Erro |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for item in results:
        lines.append(
            "| {item} | {result} | {area} | {target} | {details} | {error} |".format(
                item=_md(item.item),
                result=_md(item.result),
                area=_md(item.probable_area),
                target=_md(item.target or "-"),
                details=_md(item.details or "-"),
                error=_md(item.error or "-"),
            )
        )
    failed = [item for item in results if item.result == "FAIL"]
    if failed:
        lines.extend(["", "## Falhas encontradas", ""])
        for item in failed:
            lines.append(f"- **{item.item}**: {item.error or item.details or 'falha sem detalhe'}")
    else:
        lines.extend(["", "## Falhas encontradas", "", "- Nenhuma falha bloqueadora foi detectada nesta rodada automática."])
    return "\n".join(lines) + "\n"


def _md(value: str) -> str:
    return str(value or "").replace("\n", "<br>").replace("|", "\\|")

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import verificar_roteiro_operador_zero_hibrido as roteiro_checks
import verificar_runtime_postgres_operacional as runtime_checks


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "data" / "homologacao"


@dataclass
class MasterCheck:
    block: str
    area: str
    name: str
    status: str
    detail: str
    source: str

    @property
    def failed(self) -> bool:
        return self.status == "FALHA"


def _runtime_block(
    db_path: Path,
    runtime_dir: Path,
    runtime_url: str,
) -> list[MasterCheck]:
    checks: list[runtime_checks.Check] = []
    docker_bin = runtime_checks._resolve_docker_bin()
    if docker_bin is None:
        runtime_checks._record(checks, "runtime", "Docker disponivel", False, "docker nao encontrado")
        return [_convert("runtime_e_cargas", item, "verificar_runtime_postgres_operacional.py") for item in checks]

    try:
        ps_output = runtime_checks._compose_output(docker_bin, runtime_dir, ["ps"])
        runtime_checks._record(checks, "runtime", "docker compose ps", True, ps_output.replace("\n", " | "))
    except Exception as exc:
        runtime_checks._record(checks, "runtime", "docker compose ps", False, str(exc))
        return [_convert("runtime_e_cargas", item, "verificar_runtime_postgres_operacional.py") for item in checks]

    login_status, login_detail = runtime_checks._http_status(runtime_url.rstrip("/") + "/accounts/login/")
    runtime_checks._record(checks, "http", "login responde", login_status == 200, f"status={login_status} {login_detail}".strip())
    imports_status, imports_detail = runtime_checks._http_status(runtime_url.rstrip("/") + "/imports/rules/")
    runtime_checks._record(
        checks,
        "http",
        "regras de centavos respondem",
        imports_status in {200, 302},
        f"status={imports_status} {imports_detail}".strip(),
    )

    legacy = runtime_checks._load_legacy_counts(db_path)
    runtime = runtime_checks._runtime_counts(docker_bin, runtime_dir)
    export_probe = runtime_checks._runtime_export_probe(docker_bin, runtime_dir)
    export_guard_probe = runtime_checks._runtime_export_guard_probe(docker_bin, runtime_dir)

    mapped = [
        ("people_total", "pessoas totais"),
        ("people_active", "pessoas ativas"),
        ("contacts_total", "contatos"),
        ("addresses_total", "enderecos"),
        ("relationships_total", "relacionamentos"),
        ("relationships_active", "relacionamentos ativos"),
        ("profiles_active", "perfis ativos"),
        ("history_total", "historico"),
        ("contributors_linked_active", "contribuintes vinculados"),
        ("identifiers_linked_active", "identificadores vinculados"),
        ("contribution_types_total", "tipos de contribuicao"),
        ("statement_lots_total", "lotes de extrato"),
        ("statement_movements_active", "movimentos de extrato ativos"),
        ("cent_rules_total", "regras de centavos"),
        ("secure_trash_total", "lixeira segura"),
        ("secure_purge_total", "purga segura"),
        ("receipts_total", "recibos"),
        ("receipt_items_total", "itens de recibo"),
        ("people_import_lots_total", "lotes de importacao de pessoas"),
        ("people_import_rows_total", "linhas de importacao de pessoas"),
        ("people_import_pendings_total", "pendencias de importacao de pessoas"),
    ]
    for key, label in mapped:
        runtime_checks._record(
            checks,
            "cargas",
            label,
            int(legacy.get(key) or 0) == int(runtime.get(key) or 0),
            f"legado={legacy.get(key)} runtime={runtime.get(key)}",
        )

    runtime_checks._record(
        checks,
        "cargas",
        "contribuicoes da ficha por identidade financeira",
        int(legacy.get("person_contributions_identity_active") or 0)
        == int(runtime.get("person_contributions_identity_active") or 0),
        (
            "legado_com_identidade="
            f"{legacy.get('person_contributions_identity_active')} "
            f"runtime={runtime.get('person_contributions_identity_active')} "
            f"legado_direto={legacy.get('person_contributions_direct_active')}"
        ),
    )

    requested = list(export_probe.get("requested") or [])
    resolved_columns = list(export_probe.get("resolved_columns") or [])
    headers = list(export_probe.get("headers") or [])
    runtime_checks._record(
        checks,
        "exportacao",
        "exportacao dinamica preserva colunas selecionadas",
        requested == resolved_columns,
        f"solicitadas={requested} resolvidas={resolved_columns}",
    )
    runtime_checks._record(
        checks,
        "exportacao",
        "exportacao dinamica gera cabecalhos esperados",
        len(headers) == len(requested),
        f"headers={headers} total={len(headers)}",
    )
    runtime_checks._record(
        checks,
        "exportacao",
        "exportacao dinamica inclui campos nao basicos",
        all(
            label in headers
            for label in [
                "Familia domiciliar",
                "Contribuicoes total",
                "Ultima competencia",
                "Familia resumo financeiro",
            ]
        ),
        f"headers={headers}",
    )
    runtime_checks._record(
        checks,
        "exportacao",
        "exportacao dinamica alinha largura das linhas",
        int(export_probe.get("height") or 0) == 0 or int(export_probe.get("first_row_len") or 0) == len(requested),
        f"linhas={export_probe.get('height')} largura_primeira={export_probe.get('first_row_len')}",
    )
    runtime_checks._record(
        checks,
        "exportacao",
        "exportacao dinamica vazia nao cai no preset basico",
        int(export_guard_probe.get("status_code") or 0) == 302
        and "#exportacao-dinamica" in str(export_guard_probe.get("location") or ""),
        f"status={export_guard_probe.get('status_code')} location={export_guard_probe.get('location')}",
    )
    return [_convert("runtime_e_cargas", item, "verificar_runtime_postgres_operacional.py") for item in checks]


def _operational_block() -> list[MasterCheck]:
    ok, output = roteiro_checks._run_probe(["-c", roteiro_checks.django_probe_code()])
    if not ok:
        return [
            MasterCheck(
                block="fluxos_operacionais",
                area="boot",
                name="probe do roteiro operador executa",
                status="FALHA",
                detail=output.strip() or "Falha ao executar probe Django.",
                source="verificar_roteiro_operador_zero_hibrido.py",
            )
        ]
    payload = _extract_probe_json(output)
    return [
        MasterCheck(
            block="fluxos_operacionais",
            area=str(item["area"]),
            name=str(item["name"]),
            status=str(item["status"]),
            detail=str(item["detail"]),
            source="verificar_roteiro_operador_zero_hibrido.py",
        )
        for item in payload
    ]


def _navigation_and_documents_probe_code() -> str:
    return r"""
import json
import os
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

root = Path.cwd()
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "power_church_django"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "power_church_site.settings")

import django
django.setup()

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client

from power_church_django.apps.contributions.models import NativeAuxContributor, NativeContribution, NativeEnvelope, NativeEnvelopeLot, NativeEnvelopeProfileUpdate, ReceiptSnapshot
from power_church_django.apps.imports.models import StatementImportPilotLot, StatementImportPilotMovement
from power_church_django.apps.people.models import NativePeopleImportLot, PersonSnapshot
from power_church_django.services.data_exchange import people_export_dataset, dataset_download_response
from power_church_django.services.pdf_reports import (
    contribution_destination_pdf,
    contribution_destination_pdf_filename,
    contribution_period_pdf,
    contribution_period_pdf_filename,
    person_statement_pdf,
    person_statement_pdf_filename,
    receipt_pdf,
    receipt_pdf_filename,
)
from power_church_django.services.reports_native import contribution_destination_report_postgres, contribution_report_postgres
from power_church_django.apps.contributions.views import get_receipt_detail_cached
from power_church_django.services.people_read_native import get_person_detail
from power_church_django.services.contributions_native import person_statement_data_postgres


results = []
inventory = []


class ControlParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.controls = []
        self.form_stack = []
        self.button_stack = []

    def handle_starttag(self, tag, attrs):
        attr_map = {key: value or "" for key, value in attrs}
        if tag == "form":
            form = {
                "tag": "form",
                "method": (attr_map.get("method") or "get").lower(),
                "action": attr_map.get("action") or "",
                "text": "",
            }
            self.form_stack.append(form)
            self.controls.append(form.copy())
            return
        if tag == "a":
            self.controls.append(
                {
                    "tag": "a",
                    "href": attr_map.get("href") or "",
                    "text": "",
                    "onclick": attr_map.get("onclick") or "",
                }
            )
            return
        if tag == "button":
            button = {
                "tag": "button",
                "type": (attr_map.get("type") or "submit").lower(),
                "onclick": attr_map.get("onclick") or "",
                "text": "",
                "form_action": self.form_stack[-1]["action"] if self.form_stack else "",
                "form_method": self.form_stack[-1]["method"] if self.form_stack else "",
            }
            self.button_stack.append(button)
            self.controls.append(button.copy())
            return
        if tag == "input":
            input_type = (attr_map.get("type") or "text").lower()
            if input_type in {"submit", "button"}:
                self.controls.append(
                    {
                        "tag": "input",
                        "type": input_type,
                        "text": attr_map.get("value") or "",
                        "onclick": attr_map.get("onclick") or "",
                        "form_action": self.form_stack[-1]["action"] if self.form_stack else "",
                        "form_method": self.form_stack[-1]["method"] if self.form_stack else "",
                    }
                )

    def handle_endtag(self, tag):
        if tag == "form" and self.form_stack:
            self.form_stack.pop()
        if tag == "button" and self.button_stack:
            self.button_stack.pop()

    def handle_data(self, data):
        text = " ".join(data.split())
        if not text:
            return
        if self.button_stack:
            self.button_stack[-1]["text"] = f"{self.button_stack[-1]['text']} {text}".strip()
            self.controls[-1]["text"] = self.button_stack[-1]["text"]
        if self.controls and self.controls[-1]["tag"] == "a":
            self.controls[-1]["text"] = f"{self.controls[-1]['text']} {text}".strip()


def record(area, name, ok, detail, block, source):
    results.append(
        {
            "block": block,
            "area": area,
            "name": name,
            "status": "OK" if ok else "FALHA",
            "detail": str(detail).replace("\n", " ").strip(),
            "source": source,
        }
    )


def normalize_path(url):
    parsed = urlsplit(url)
    path = parsed.path or ""
    path = re.sub(r"/\d+/", "/<id>/", path)
    path = re.sub(r"/\d+$", "/<id>", path)
    return path or "/"


def classify_control(control):
    url = ""
    if control["tag"] == "a":
        url = control.get("href") or ""
    elif control["tag"] == "form":
        url = control.get("action") or ""
    else:
        url = control.get("form_action") or ""
    onclick = control.get("onclick") or ""
    method = (control.get("method") or control.get("form_method") or "").lower()
    if "window.print(" in onclick:
        return "impressao"
    if ".pdf" in url:
        return "pdf"
    if "/people/export/" in url or url.endswith(".csv") or url.endswith(".xlsx"):
        return "exportacao"
    if control["tag"] == "form" and method == "post":
        return "acao_post"
    if control["tag"] in {"button", "input"} and (control.get("form_method") or "").lower() == "post":
        return "acao_post"
    if control["tag"] == "form":
        return "acao_get"
    if control["tag"] in {"button", "input"} and control.get("form_action"):
        return "acao_get" if (control.get("form_method") or "get").lower() == "get" else "acao_post"
    return "navegacao"


def internal_target(url):
    if not url:
        return ""
    if url.startswith("#") or url.startswith("javascript:") or url.startswith("mailto:") or url.startswith("tel:"):
        return ""
    parsed = urlsplit(url)
    return parsed.path + (f"?{parsed.query}" if parsed.query else "")


User = get_user_model()
user = User.objects.filter(is_active=True).order_by("is_superuser", "id").last()
if not user:
    print("__RESULT_JSON_START__")
    print(json.dumps({"checks": [{"block": "botoes_e_navegacao", "area": "boot", "name": "usuario ativo", "status": "FALHA", "detail": "nenhum usuario ativo encontrado", "source": "verificar_botoes_servicos_impressao_total.py"}], "inventory": []}, ensure_ascii=True))
    print("__RESULT_JSON_END__")
    raise SystemExit(1)

if "testserver" not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append("testserver")
for host in ("127.0.0.1", "localhost"):
    if host not in settings.ALLOWED_HOSTS:
        settings.ALLOWED_HOSTS.append(host)
settings.MIDDLEWARE = [item for item in settings.MIDDLEWARE if "whitenoise" not in item.lower()]

client = Client(HTTP_HOST="127.0.0.1", raise_request_exception=False)
client.force_login(user)

person = PersonSnapshot.objects.filter(is_active=True).order_by("legacy_id").first()
contribution = NativeContribution.objects.filter(is_active=True).order_by("-legacy_id").first()
envelope_lot = NativeEnvelopeLot.objects.filter(is_active=True).exclude(status="").order_by("-legacy_id").first()
open_lot = NativeEnvelopeLot.objects.filter(is_active=True, status__in=["aberto", "parcial", "aguardando_digitacao", "digitado", "duplicado", "ignorado"]).order_by("-legacy_id").first()
pending_envelope = NativeEnvelope.objects.filter(is_active=True, status__in=["pendente", "aberto", "parcial", "aguardando_digitacao"]).order_by("-legacy_id").first()
launched_envelope = NativeEnvelope.objects.filter(is_active=True, status__in=["lancado", "regular"]).order_by("-legacy_id").first()
receipt = ReceiptSnapshot.objects.order_by("-legacy_id").first()
statement_lot = StatementImportPilotLot.objects.order_by("-id").first()
statement_movement = StatementImportPilotMovement.objects.order_by("-id").first()
people_import_lot = NativePeopleImportLot.objects.order_by("-legacy_id").first()
aux_contributor = NativeAuxContributor.objects.filter(is_active=True).order_by("id").first()

visited_pages = [
    ("Navegacao", "Dashboard", "/"),
    ("Pessoas", "Lista de pessoas", "/people/"),
    ("Pessoas", "Nova pessoa", "/people/new/"),
    ("Familias", "Familias domiciliares", "/people/families/"),
    ("Importacoes", "Importacao de pessoas", "/people/imports/"),
    ("Contribuicoes", "Lista de contribuicoes", "/contributions/"),
    ("Contribuicoes", "Nova contribuicao", "/contributions/new/"),
    ("Contribuicoes", "Lote manual", "/contributions/manual/"),
    ("Envelopes", "Central de envelopes", "/contributions/envelopes/"),
    ("Envelopes", "Novo envelope", "/contributions/envelopes/new/"),
    ("Envelopes", "Novo lote", "/contributions/envelopes/lots/new/"),
    ("Recibos", "Central de recibos", "/receipts/"),
    ("Fila e monitor", "Monitor da fila", "/receipts/queue/"),
    ("Importacoes", "Importacoes bancarias", "/imports/"),
    ("Importacoes", "Regras de centavos", "/imports/rules/"),
    ("Relatorios", "Relatorio por periodo", "/reports/"),
    ("Relatorios", "Relatorio por destino", "/reports/destinations/"),
    ("Auditoria", "Auditoria operacional", "/audit/"),
    ("Usuarios", "Usuarios", "/accounts/"),
]
if person:
    visited_pages.extend(
        [
            ("Pessoas", "Detalhe da pessoa", f"/people/{int(person.legacy_id or 0)}/"),
            ("Merge", "Tela de merge", f"/people/{int(person.legacy_id or 0)}/merge/"),
            ("Contribuicoes", "Extrato por pessoa", f"/contributions/statements/{int(person.legacy_id or 0)}/"),
        ]
    )
if contribution:
    visited_pages.extend(
        [
            ("Contribuicoes", "Detalhe da contribuicao", f"/contributions/{int(contribution.legacy_id or 0)}/"),
            ("Contribuicoes", "Rateio da contribuicao", f"/contributions/{int(contribution.legacy_id or 0)}/split/"),
        ]
    )
if open_lot:
    visited_pages.extend(
        [
            ("Envelopes", "Lote de envelopes inacabado", f"/contributions/envelopes/lots/{int(open_lot.legacy_id or 0)}/"),
            ("Envelopes", "Proximo pendente", f"/contributions/envelopes/lots/{int(open_lot.legacy_id or 0)}/next/"),
        ]
    )
elif envelope_lot:
    visited_pages.append(("Envelopes", "Lote de envelopes", f"/contributions/envelopes/lots/{int(envelope_lot.legacy_id or 0)}/"))
if pending_envelope:
    visited_pages.append(("Envelopes", "Lancar pendente", f"/contributions/envelopes/{int(pending_envelope.legacy_id or 0)}/launch/"))
if launched_envelope:
    visited_pages.extend(
        [
            ("Envelopes", "Detalhe do envelope", f"/contributions/envelopes/{int(launched_envelope.legacy_id or 0)}/"),
            ("Envelopes", "Editar envelope", f"/contributions/envelopes/{int(launched_envelope.legacy_id or 0)}/edit/"),
        ]
    )
if receipt:
    visited_pages.append(("Recibos", "Detalhe do recibo", f"/receipts/{int(receipt.legacy_id or 0)}/"))
if statement_lot:
    backend = "postgres_nativo" if str(statement_lot.source_backend or "") == "postgres_nativo" else "django_web"
    visited_pages.append(("Extratos/Importacoes", "Detalhe do lote de extrato", f"/imports/statement/{int(statement_lot.id or 0)}/?backend={backend}"))
if statement_movement:
    movement_backend = "postgres_nativo" if statement_lot and str(statement_lot.source_backend or "") == "postgres_nativo" else "django_web"
    visited_pages.append(("Extratos/Importacoes", "Detalhe do movimento", f"/imports/statement/movement/{int(statement_movement.id or 0)}/?backend={movement_backend}"))
if people_import_lot:
    visited_pages.append(("Importacoes", "Detalhe do lote de importacao de pessoas", f"/people/imports/{int(people_import_lot.legacy_id or 0)}/"))
if aux_contributor:
    visited_pages.append(("Contribuintes auxiliares", "Detalhe do contribuinte", f"/contributors/{int(aux_contributor.id or 0)}/"))

seen_pages = set()
page_bodies = {}
for area, label, url in visited_pages:
    if url in seen_pages:
        continue
    seen_pages.add(url)
    response = client.get(url, follow=False)
    ok = response.status_code in {200, 302}
    detail = f"url={url} status={response.status_code}"
    if response.status_code == 200:
        detail += f" bytes={len(response.content or b'')}"
        page_bodies[url] = response.content.decode("utf-8", errors="replace")
    elif response.status_code in {301, 302}:
        detail += f" location={response.headers.get('Location', '')}"
    record(area, f"Pagina viva: {label}", ok, detail, "botoes_e_navegacao", "verificar_botoes_servicos_impressao_total.py")

validated_targets = set()
for area, label, url in visited_pages:
    body = page_bodies.get(url)
    if not body:
        continue
    parser = ControlParser()
    parser.feed(body)
    controls = parser.controls
    record(area, f"Inventario de controles: {label}", bool(controls), f"url={url} controles={len(controls)}", "botoes_e_navegacao", "verificar_botoes_servicos_impressao_total.py")
    for control in controls:
        control_type = classify_control(control)
        target = ""
        if control["tag"] == "a":
            target = internal_target(control.get("href") or "")
        elif control["tag"] == "form":
            target = internal_target(control.get("action") or "")
        else:
            target = internal_target(control.get("form_action") or "")
        if target.startswith("/admin/"):
            continue
        inventory.append(
            {
                "screen_url": url,
                "screen_area": area,
                "screen_label": label,
                "control_type": control_type,
                "tag": control["tag"],
                "target": target,
                "normalized_target": normalize_path(target) if target else "",
                "text": (control.get("text") or "").strip(),
            }
        )
        if not target:
            continue
        normalized = normalize_path(target)
        validation_key = target if control_type in {"pdf", "exportacao"} else normalized
        if validation_key in validated_targets:
            continue
        validated_targets.add(validation_key)
        response = client.get(target, follow=False)
        detail = f"target={target} normalized={normalized} status={response.status_code}"
        if response.status_code in {301, 302}:
            detail += f" location={response.headers.get('Location', '')}"
        ok = response.status_code in {200, 301, 302, 405}
        if control_type == "pdf":
            ok = response.status_code == 200 and "application/pdf" in str(response.headers.get("Content-Type") or "")
            detail += f" content_type={response.headers.get('Content-Type', '')} disposition={response.headers.get('Content-Disposition', '')}"
        elif control_type == "exportacao":
            ok = response.status_code == 200 and bool(response.headers.get("Content-Disposition"))
            detail += f" content_type={response.headers.get('Content-Type', '')} disposition={response.headers.get('Content-Disposition', '')}"
        record("Navegacao", f"Alvo de controle responde: {normalized}", ok, detail, "botoes_e_navegacao", "verificar_botoes_servicos_impressao_total.py")

print_screens = []
for url, body in page_bodies.items():
    if "window.print()" in body:
        print_screens.append(url)
        record(
            "Impressao e documentos",
            f"Botao de impressao visivel em {normalize_path(url)}",
            True,
            f"url={url}",
            "impressao_e_documentos",
            "verificar_botoes_servicos_impressao_total.py",
        )
record(
    "Impressao e documentos",
    "Telas com window.print inventariadas",
    bool(print_screens),
    f"telas={len(print_screens)}",
    "impressao_e_documentos",
    "verificar_botoes_servicos_impressao_total.py",
)

response = client.get("/reports/contributions-period.pdf")
record(
    "Impressao e documentos",
    "PDF HTTP de relatorio por periodo responde",
    response.status_code == 200 and "application/pdf" in str(response.headers.get("Content-Type") or ""),
    f"status={response.status_code} content_type={response.headers.get('Content-Type', '')} disposition={response.headers.get('Content-Disposition', '')}",
    "impressao_e_documentos",
    "verificar_botoes_servicos_impressao_total.py",
)
response = client.get("/reports/contributions-destinations.pdf")
record(
    "Impressao e documentos",
    "PDF HTTP de relatorio por destino responde",
    response.status_code == 200 and "application/pdf" in str(response.headers.get("Content-Type") or ""),
    f"status={response.status_code} content_type={response.headers.get('Content-Type', '')} disposition={response.headers.get('Content-Disposition', '')}",
    "impressao_e_documentos",
    "verificar_botoes_servicos_impressao_total.py",
)
if person:
    response = client.get(f"/contributions/statements/{int(person.legacy_id or 0)}/pdf/")
    record(
        "Impressao e documentos",
        "PDF HTTP do extrato por pessoa responde",
        response.status_code == 200 and "application/pdf" in str(response.headers.get("Content-Type") or ""),
        f"status={response.status_code} content_type={response.headers.get('Content-Type', '')} disposition={response.headers.get('Content-Disposition', '')}",
        "impressao_e_documentos",
        "verificar_botoes_servicos_impressao_total.py",
    )
if receipt:
    response = client.get(f"/receipts/{int(receipt.legacy_id or 0)}/pdf/")
    record(
        "Impressao e documentos",
        "PDF HTTP do recibo responde",
        response.status_code == 200 and "application/pdf" in str(response.headers.get("Content-Type") or ""),
        f"status={response.status_code} content_type={response.headers.get('Content-Type', '')} disposition={response.headers.get('Content-Disposition', '')}",
        "impressao_e_documentos",
        "verificar_botoes_servicos_impressao_total.py",
    )

export_data = people_export_dataset(
    columns=["nome", "familia_domiciliar", "contribuicoes_total", "ultima_competencia", "familia_resumo_financeiro"],
    preset="cadastro_basico",
)
for export_format in ("csv", "xlsx"):
    response = dataset_download_response(export_data["dataset"], export_format, "pessoas")
    record(
        "Impressao e documentos",
        f"Exportacao {export_format.upper()} gera download",
        response.status_code == 200 and bool(response.get("Content-Disposition")),
        f"content_type={response.get('Content-Type', '')} disposition={response.get('Content-Disposition', '')}",
        "impressao_e_documentos",
        "verificar_botoes_servicos_impressao_total.py",
    )

period_report = contribution_report_postgres()
period_pdf = contribution_period_pdf(period_report)
record(
    "Impressao e documentos",
    "Geracao interna contribution_period_pdf",
    isinstance(period_pdf, (bytes, bytearray)) and period_pdf.startswith(b"%PDF"),
    f"bytes={len(period_pdf)} filename={contribution_period_pdf_filename(period_report)}",
    "impressao_e_documentos",
    "verificar_botoes_servicos_impressao_total.py",
)
destination_report = contribution_destination_report_postgres()
destination_pdf = contribution_destination_pdf(destination_report)
record(
    "Impressao e documentos",
    "Geracao interna contribution_destination_pdf",
    isinstance(destination_pdf, (bytes, bytearray)) and destination_pdf.startswith(b"%PDF"),
    f"bytes={len(destination_pdf)} filename={contribution_destination_pdf_filename(destination_report)}",
    "impressao_e_documentos",
    "verificar_botoes_servicos_impressao_total.py",
)
if person:
    statement = person_statement_data_postgres(int(person.legacy_id or 0))
    statement_pdf = person_statement_pdf(statement)
    record(
        "Impressao e documentos",
        "Geracao interna person_statement_pdf",
        isinstance(statement_pdf, (bytes, bytearray)) and statement_pdf.startswith(b"%PDF"),
        f"bytes={len(statement_pdf)} filename={person_statement_pdf_filename(statement)}",
        "impressao_e_documentos",
        "verificar_botoes_servicos_impressao_total.py",
    )
if receipt:
    detail = get_receipt_detail_cached(int(receipt.legacy_id or 0))
    if detail:
        pdf_payload = receipt_pdf(detail)
        record(
            "Impressao e documentos",
            "Geracao interna receipt_pdf",
            isinstance(pdf_payload, (bytes, bytearray)) and pdf_payload.startswith(b"%PDF"),
            f"bytes={len(pdf_payload)} filename={receipt_pdf_filename(detail)}",
            "impressao_e_documentos",
            "verificar_botoes_servicos_impressao_total.py",
        )
        receipt_info = detail.get("receipt") or {}
        record(
            "Impressao e documentos",
            "Nome de arquivo do recibo gerado",
            str(receipt_pdf_filename(detail)).endswith(".pdf") and bool(receipt_info),
            f"filename={receipt_pdf_filename(detail)}",
            "impressao_e_documentos",
            "verificar_botoes_servicos_impressao_total.py",
        )

print("__RESULT_JSON_START__")
print(json.dumps({"checks": results, "inventory": inventory}, ensure_ascii=True))
print("__RESULT_JSON_END__")
"""


def _navigation_and_documents_block() -> tuple[list[MasterCheck], list[dict[str, Any]]]:
    ok, output = roteiro_checks._run_probe(["-c", _navigation_and_documents_probe_code()])
    if not ok:
        return (
            [
                MasterCheck(
                    block="botoes_e_navegacao",
                    area="boot",
                    name="probe de botoes e documentos executa",
                    status="FALHA",
                    detail=output.strip() or "Falha ao executar probe de botoes e documentos.",
                    source="verificar_botoes_servicos_impressao_total.py",
                )
            ],
            [],
        )
    payload = _extract_probe_json(output)
    checks = [
        MasterCheck(
            block=str(item["block"]),
            area=str(item["area"]),
            name=str(item["name"]),
            status=str(item["status"]),
            detail=str(item["detail"]),
            source=str(item["source"]),
        )
        for item in payload["checks"]
    ]
    return checks, list(payload.get("inventory") or [])


def _extract_probe_json(output: str) -> Any:
    start_marker = "__RESULT_JSON_START__"
    end_marker = "__RESULT_JSON_END__"
    start = output.find(start_marker)
    end = output.find(end_marker, start + len(start_marker)) if start >= 0 else -1
    if start < 0 or end < 0:
        raise RuntimeError(f"Nao foi possivel localizar o bloco JSON do probe.\n{output}")
    json_blob = output[start + len(start_marker):end].strip()
    return json.loads(json_blob)


def _convert(block: str, item: runtime_checks.Check, source: str) -> MasterCheck:
    return MasterCheck(block=block, area=item.area, name=item.name, status=item.status, detail=item.detail, source=source)


def _write_outputs(checks: list[MasterCheck], inventory: list[dict[str, Any]]) -> tuple[Path, Path]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    md_path = REPORT_DIR / f"verificar_botoes_servicos_impressao_total_{stamp}.md"
    json_path = REPORT_DIR / f"verificar_botoes_servicos_impressao_total_{stamp}.json"

    grouped: dict[str, list[MasterCheck]] = {}
    for check in checks:
        grouped.setdefault(check.area, []).append(check)

    lines = [
        "# Verificacao Total De Botoes, Servicos E Impressao",
        "",
        f"Gerado em: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "## Resumo",
        "",
        f"- Total de checks: {len(checks)}",
        f"- Falhas: {sum(1 for item in checks if item.failed)}",
        f"- Inventario de controles: {len(inventory)}",
        "",
        "| Bloco | Area | Checagem | Status | Detalhe | Fonte |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for item in checks:
        lines.append(
            f"| {item.block} | {item.area} | {item.name} | {item.status} | {item.detail} | {item.source} |"
        )

    failures = [item for item in checks if item.failed]
    if failures:
        lines.extend(["", "## Bugs Por Area", ""])
        for area in [
            "Navegacao",
            "Pessoas",
            "Familias",
            "Merge",
            "Contribuicoes",
            "Envelopes",
            "Recibos",
            "Fila e monitor",
            "Extratos/Importacoes",
            "Contribuintes auxiliares",
            "Relatorios",
            "Auditoria",
            "Impressao e documentos",
            "Login e seguranca",
            "Importacoes",
            "Usuarios",
            "runtime",
            "http",
            "cargas",
            "exportacao",
            "boot",
        ]:
            area_failures = [item for item in failures if item.area == area]
            if not area_failures:
                continue
            lines.append(f"### {area}")
            lines.append("")
            for item in area_failures:
                lines.append(f"- `{item.block}` {item.name}: {item.detail}")
            lines.append("")

    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    json_path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "checks": [asdict(item) for item in checks],
                "inventory": inventory,
                "summary": {
                    "total_checks": len(checks),
                    "failed_checks": sum(1 for item in checks if item.failed),
                    "inventory_count": len(inventory),
                    "blocks": sorted({item.block for item in checks}),
                    "areas": sorted({item.area for item in checks}),
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return md_path, json_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Verificador mestre de botoes, servicos, impressao e runtime.")
    parser.add_argument("--db", default=str(runtime_checks.DEFAULT_DB), help="Banco legado SQLite.")
    parser.add_argument("--runtime-dir", default=str(runtime_checks.DEFAULT_RUNTIME_DIR), help="Diretorio persistente do runtime.")
    parser.add_argument("--runtime-url", default=runtime_checks.DEFAULT_RUNTIME_URL, help="URL base do runtime.")
    parser.add_argument("--report", action="store_true", help="Gera relatorios markdown e json.")
    args = parser.parse_args()

    db_path = Path(args.db).expanduser().resolve()
    runtime_dir = Path(args.runtime_dir).expanduser().resolve()

    all_checks: list[MasterCheck] = []
    all_checks.extend(_runtime_block(db_path, runtime_dir, args.runtime_url))
    all_checks.extend(_operational_block())
    nav_checks, inventory = _navigation_and_documents_block()
    all_checks.extend(nav_checks)

    if args.report:
        md_path, json_path = _write_outputs(all_checks, inventory)
        print(f"Relatorio: {md_path}")
        print(f"Inventario JSON: {json_path}")

    failures = [item for item in all_checks if item.failed]
    for item in failures:
        print(f"[FALHA] {item.block} :: {item.area} :: {item.name} :: {item.detail}")
    if not failures:
        for item in all_checks:
            print(f"[OK] {item.block} :: {item.area} :: {item.name} :: {item.detail}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

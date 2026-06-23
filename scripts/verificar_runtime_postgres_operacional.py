from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "data" / "homologacao"
DEFAULT_DB = ROOT / "data" / "power_church_membros_importado.db"
DEFAULT_RUNTIME_DIR = Path.home() / "power_church_postgres_runtime"
DEFAULT_RUNTIME_URL = "http://127.0.0.1:8001"


@dataclass
class Check:
    area: str
    name: str
    status: str
    detail: str

    @property
    def failed(self) -> bool:
        return self.status == "FALHA"


def _record(checks: list[Check], area: str, name: str, ok: bool, detail: str) -> None:
    checks.append(Check(area=area, name=name, status="OK" if ok else "FALHA", detail=detail))


def _resolve_docker_bin() -> str | None:
    for candidate in (
        os.environ.get("DOCKER_BIN", ""),
        shutil_which("docker"),
        str(Path.home() / ".orbstack" / "bin" / "docker"),
        "/usr/local/bin/docker",
        "/opt/homebrew/bin/docker",
        "/Applications/Docker.app/Contents/Resources/bin/docker",
    ):
        if candidate and Path(candidate).exists():
            return candidate
    return None


def shutil_which(binary: str) -> str | None:
    for path_entry in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(path_entry) / binary
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _runtime_env_file(runtime_dir: Path) -> Path:
    return runtime_dir / "env" / "runtime.env"


def _compose_cmd(docker_bin: str, runtime_dir: Path) -> list[str]:
    return [
        docker_bin,
        "compose",
        "--env-file",
        str(_runtime_env_file(runtime_dir)),
        "-f",
        str(ROOT / "docker-compose.runtime.yml"),
    ]


def _compose_output(docker_bin: str, runtime_dir: Path, args: list[str]) -> str:
    completed = subprocess.run(
        [*_compose_cmd(docker_bin, runtime_dir), *args],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "falha no docker compose").strip())
    return (completed.stdout or "").strip()


def _docker_shell_json(docker_bin: str, runtime_dir: Path, python_code: str) -> dict[str, Any]:
    completed = subprocess.run(
        [
            *_compose_cmd(docker_bin, runtime_dir),
            "exec",
            "-T",
            "power-church-django-runtime",
            "python",
            "manage.py",
            "shell",
            "-c",
            python_code,
        ],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "falha no manage.py shell").strip())
    lines = [line.strip() for line in (completed.stdout or "").splitlines() if line.strip()]
    payload = lines[-1] if lines else "{}"
    return json.loads(payload)


def _load_legacy_counts(db_path: Path) -> dict[str, Any]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        counts = {
            "people_total": conn.execute("SELECT COUNT(*) FROM pessoas").fetchone()[0],
            "people_active": conn.execute("SELECT COUNT(*) FROM pessoas WHERE ativo = 1").fetchone()[0],
            "contacts_total": conn.execute("SELECT COUNT(*) FROM pessoa_contatos").fetchone()[0],
            "addresses_total": conn.execute("SELECT COUNT(*) FROM pessoa_enderecos").fetchone()[0],
            "relationships_total": conn.execute("SELECT COUNT(*) FROM pessoa_relacionamentos").fetchone()[0],
            "relationships_active": conn.execute("SELECT COUNT(*) FROM pessoa_relacionamentos WHERE ativo = 1").fetchone()[0],
            "profiles_active": conn.execute("SELECT COUNT(*) FROM pessoa_perfis WHERE ativo = 1").fetchone()[0],
            "history_total": conn.execute("SELECT COUNT(*) FROM pessoa_historico").fetchone()[0],
            "contributors_linked_active": conn.execute("SELECT COUNT(*) FROM contribuintes WHERE ativo = 1 AND pessoa_id IS NOT NULL").fetchone()[0],
            "identifiers_linked_active": conn.execute("SELECT COUNT(*) FROM contribuintes_identificadores WHERE ativo = 1 AND pessoa_id IS NOT NULL").fetchone()[0],
            "person_contributions_direct_active": conn.execute("SELECT COUNT(*) FROM contribuicoes WHERE ativo = 1 AND pessoa_id IS NOT NULL").fetchone()[0],
            "person_contributions_identity_active": conn.execute(
                """
                SELECT COUNT(*)
                  FROM contribuicoes co
                  LEFT JOIN contribuintes c ON c.id = co.contribuinte_id
                 WHERE co.ativo = 1
                   AND (co.pessoa_id IS NOT NULL OR c.pessoa_id IS NOT NULL)
                """
            ).fetchone()[0],
            "contribution_types_total": conn.execute("SELECT COUNT(*) FROM tipos_contribuicao").fetchone()[0],
            "statement_lots_total": conn.execute("SELECT COUNT(*) FROM extrato_lotes").fetchone()[0],
            "statement_movements_active": conn.execute("SELECT COUNT(*) FROM extrato_movimentos WHERE ativo = 1").fetchone()[0],
            "cent_rules_total": conn.execute("SELECT COUNT(*) FROM pix_centavo_regras").fetchone()[0],
            "secure_trash_total": conn.execute("SELECT COUNT(*) FROM pessoas_lixeira_segura").fetchone()[0],
            "secure_purge_total": conn.execute("SELECT COUNT(*) FROM pessoas_purga_segura").fetchone()[0],
            "receipts_total": conn.execute("SELECT COUNT(*) FROM recibos").fetchone()[0],
            "receipt_items_total": conn.execute("SELECT COUNT(*) FROM recibo_itens").fetchone()[0],
            "people_import_lots_total": conn.execute("SELECT COUNT(*) FROM import_lotes").fetchone()[0],
            "people_import_rows_total": conn.execute("SELECT COUNT(*) FROM import_linhas").fetchone()[0],
            "people_import_pendings_total": conn.execute("SELECT COUNT(*) FROM import_pendencias").fetchone()[0],
        }
        return counts
    finally:
        conn.close()


def _runtime_counts(docker_bin: str, runtime_dir: Path) -> dict[str, Any]:
    code = r"""
import json
from power_church_django.apps.people.models import (
    NativePeopleImportLine,
    NativePeopleImportLot,
    NativePeopleImportPending,
    PersonAddressSnapshot,
    PersonContactSnapshot,
    PersonContributionSnapshot,
    PersonContributorSnapshot,
    PersonHistorySnapshot,
    PersonIdentifierSnapshot,
    PersonProfileSnapshot,
    PersonRelationshipSnapshot,
    PersonSecurePurgeSnapshot,
    PersonSecureTrashSnapshot,
    PersonSnapshot,
)
from power_church_django.apps.contributions.models import ContributionTypeSnapshot, ReceiptItemSnapshot, ReceiptSnapshot
from power_church_django.apps.imports.models import CentRuleSnapshot, StatementImportPilotLot, StatementImportPilotMovement

payload = {
    "people_total": PersonSnapshot.objects.count(),
    "people_active": PersonSnapshot.objects.filter(is_active=True).count(),
    "contacts_total": PersonContactSnapshot.objects.count(),
    "addresses_total": PersonAddressSnapshot.objects.count(),
    "relationships_total": PersonRelationshipSnapshot.objects.count(),
    "relationships_active": PersonRelationshipSnapshot.objects.filter(is_active=True).count(),
    "profiles_active": PersonProfileSnapshot.objects.filter(is_active=True).count(),
    "history_total": PersonHistorySnapshot.objects.count(),
    "contributors_linked_active": PersonContributorSnapshot.objects.filter(is_active=True).count(),
    "identifiers_linked_active": PersonIdentifierSnapshot.objects.filter(is_active=True).count(),
    "person_contributions_identity_active": PersonContributionSnapshot.objects.filter(is_active=True).count(),
    "contribution_types_total": ContributionTypeSnapshot.objects.count(),
    "statement_lots_total": StatementImportPilotLot.objects.count(),
    "statement_movements_active": StatementImportPilotMovement.objects.count(),
    "cent_rules_total": CentRuleSnapshot.objects.count(),
    "secure_trash_total": PersonSecureTrashSnapshot.objects.count(),
    "secure_purge_total": PersonSecurePurgeSnapshot.objects.count(),
    "receipts_total": ReceiptSnapshot.objects.count(),
    "receipt_items_total": ReceiptItemSnapshot.objects.count(),
    "people_import_lots_total": NativePeopleImportLot.objects.count(),
    "people_import_rows_total": NativePeopleImportLine.objects.count(),
    "people_import_pendings_total": NativePeopleImportPending.objects.count(),
}
print(json.dumps(payload, ensure_ascii=True, sort_keys=True))
"""
    return _docker_shell_json(docker_bin, runtime_dir, code)


def _runtime_export_probe(docker_bin: str, runtime_dir: Path) -> dict[str, Any]:
    code = r"""
import json
from power_church_django.services.data_exchange import people_export_dataset

requested = [
    "nome",
    "familia_domiciliar",
    "contribuicoes_total",
    "ultima_competencia",
    "familia_resumo_financeiro",
]
payload = people_export_dataset(columns=requested, preset="cadastro_basico")
dataset = payload["dataset"]
first_row = dataset[0] if dataset.height else []
print(json.dumps({
    "requested": requested,
    "resolved_columns": payload["columns"],
    "headers": list(dataset.headers),
    "height": dataset.height,
    "first_row_len": len(first_row),
}, ensure_ascii=True, sort_keys=True))
"""
    return _docker_shell_json(docker_bin, runtime_dir, code)


def _runtime_export_guard_probe(docker_bin: str, runtime_dir: Path) -> dict[str, Any]:
    code = r"""
import json
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory
from power_church_django.apps.people.views import export

request = RequestFactory().get(
    "/people/export/",
    {"source": "dynamic", "preset": "cadastro_basico", "format": "csv"},
)
SessionMiddleware(lambda req: None).process_request(request)
request.session.save()
setattr(request, "_messages", FallbackStorage(request))
request.user = get_user_model().objects.filter(is_superuser=True).first()
response = export(request)
print(json.dumps({
    "status_code": response.status_code,
    "location": response.get("Location", ""),
}, ensure_ascii=True, sort_keys=True))
"""
    return _docker_shell_json(docker_bin, runtime_dir, code)


def _runtime_receipt_queue_probe(docker_bin: str, runtime_dir: Path) -> dict[str, Any]:
    code = r"""
import json
from django.conf import settings
from django.db import transaction

from power_church_django.apps.contributions.models import ReceiptDispatch, ReceiptSnapshot
from power_church_django.apps.people.models import PersonContributionSnapshot
from power_church_django.services import receipt_delivery as receipt_module
from power_church_django.services.mail_dispatch import MailDispatchResult

payload = {
    "provider": str(getattr(settings, "POWER_CHURCH_EMAIL_PROVIDER", "") or ""),
    "graph_sender_configured": bool(getattr(settings, "POWER_CHURCH_GRAPH_SENDER_USER", "") or ""),
    "graph_tenant_configured": bool(getattr(settings, "POWER_CHURCH_GRAPH_TENANT_ID", "") or ""),
    "graph_client_configured": bool(getattr(settings, "POWER_CHURCH_GRAPH_CLIENT_ID", "") or ""),
    "graph_secret_configured": bool(getattr(settings, "POWER_CHURCH_GRAPH_CLIENT_SECRET", "") or ""),
    "auto_email_enabled": bool(getattr(settings, "POWER_CHURCH_RECEIPT_AUTO_EMAIL_ENABLED", False)),
    "auto_send_enabled": bool(getattr(settings, "POWER_CHURCH_RECEIPT_AUTO_SEND_ENABLED", False)),
    "smoke_person_id": 0,
    "smoke_receipt_ids": [],
    "smoke_dispatch_ids": [],
    "smoke_sent_count": 0,
    "smoke_statuses": [],
    "smoke_error": "",
    "queued_receipt_ids": [],
    "queued_dispatch_ids": [],
    "queued_statuses": [],
    "queued_error": "",
    "queue_processor_processed": 0,
    "queue_processor_statuses": [],
    "queue_processor_sleep_calls": [],
    "queue_processor_error": "",
}

person_row = (
    PersonContributionSnapshot.objects.filter(
        is_active=True,
        person__is_active=True,
    )
    .exclude(person__primary_email="")
    .order_by("person__legacy_id", "received_at", "legacy_id")
    .first()
)

if person_row and payload["auto_email_enabled"] and payload["auto_send_enabled"]:
    sample_ids = list(
        PersonContributionSnapshot.objects.filter(
            is_active=True,
            person__legacy_id=int(person_row.person.legacy_id or 0),
        )
        .order_by("received_at", "legacy_id")
        .values_list("legacy_id", flat=True)[:2]
    )
    if sample_ids:
        payload["smoke_person_id"] = int(person_row.person.legacy_id or 0)
        before_ids = set(ReceiptDispatch.objects.values_list("id", flat=True))
        original_send = receipt_module.send_email_message
        try:
            def fake_send_email_message(**kwargs):
                return MailDispatchResult(
                    provider="microsoft_graph",
                    accepted=True,
                    metadata={
                        "fake": True,
                        "to": list(kwargs.get("to_emails") or []),
                    },
                )

            with transaction.atomic():
                receipt_module.send_email_message = fake_send_email_message
                outcomes = receipt_module.schedule_automatic_receipts_for_events(
                    [int(value or 0) for value in sample_ids],
                    actor="verificador_runtime",
                    send_now=None,
                )
                new_dispatches = list(
                    ReceiptDispatch.objects.exclude(id__in=before_ids).order_by("id")
                )
                payload["smoke_receipt_ids"] = [
                    int(item.get("receipt_id") or 0)
                    for item in outcomes
                    if int(item.get("receipt_id") or 0)
                ]
                payload["smoke_dispatch_ids"] = [int(item.id or 0) for item in new_dispatches]
                payload["smoke_statuses"] = [str(item.status or "") for item in new_dispatches]
                payload["smoke_sent_count"] = sum(1 for item in new_dispatches if str(item.status or "") == ReceiptDispatch.Status.SENT)
                transaction.set_rollback(True)
        except Exception as exc:
            payload["smoke_error"] = str(exc)
        finally:
            receipt_module.send_email_message = original_send

if person_row and payload["auto_email_enabled"]:
    queued_ids = list(
        PersonContributionSnapshot.objects.filter(
            is_active=True,
            person__legacy_id=int(person_row.person.legacy_id or 0),
        )
        .order_by("received_at", "legacy_id")
        .values_list("legacy_id", flat=True)[:2]
    )
    if queued_ids:
        before_ids = set(ReceiptDispatch.objects.values_list("id", flat=True))
        original_send = receipt_module.send_email_message
        try:
            def fake_send_email_message(**kwargs):
                return MailDispatchResult(
                    provider="microsoft_graph",
                    accepted=True,
                    metadata={
                        "fake": True,
                        "queued": True,
                        "to": list(kwargs.get("to_emails") or []),
                    },
                )

            with transaction.atomic():
                receipt_module.send_email_message = fake_send_email_message
                outcomes = receipt_module.schedule_automatic_receipts_for_events(
                    [int(value or 0) for value in queued_ids],
                    actor="verificador_runtime",
                    send_now=False,
                )
                new_dispatches = list(
                    ReceiptDispatch.objects.exclude(id__in=before_ids).order_by("id")
                )
                payload["queued_receipt_ids"] = [
                    int(item.get("receipt_id") or 0)
                    for item in outcomes
                    if int(item.get("receipt_id") or 0)
                ]
                payload["queued_dispatch_ids"] = [int(item.id or 0) for item in new_dispatches]
                payload["queued_statuses"] = [str(item.status or "") for item in new_dispatches]
                transaction.set_rollback(True)
        except Exception as exc:
            payload["queued_error"] = str(exc)
        finally:
            receipt_module.send_email_message = original_send

queue_receipts = list(
    ReceiptSnapshot.objects.exclude(person_email="").order_by("legacy_id")[:2]
)
if len(queue_receipts) >= 2:
    original_send = receipt_module.send_email_message
    original_sleep = receipt_module.time.sleep
    sleep_calls = []
    try:
        def fake_send_email_message(**kwargs):
            return MailDispatchResult(
                provider="microsoft_graph",
                accepted=True,
                metadata={
                    "fake": True,
                    "queue_processor": True,
                    "to": list(kwargs.get("to_emails") or []),
                },
            )

        def fake_sleep(seconds):
            sleep_calls.append(float(seconds))

        with transaction.atomic():
            receipt_module.send_email_message = fake_send_email_message
            receipt_module.time.sleep = fake_sleep
            campaign_key = "runtime_queue_smoke"
            for sample in queue_receipts:
                ReceiptDispatch.objects.create(
                    organization_id=int(sample.organization_id or 0) or None,
                    legacy_person_id=int(sample.person_legacy_id or 0),
                    legacy_receipt_id=int(sample.legacy_id or 0),
                    legacy_receipt_number=str(sample.receipt_number or ""),
                    person_name=str(sample.person_name or ""),
                    person_email=str(sample.person_email or ""),
                    competence="",
                    period_label="",
                    mode=ReceiptDispatch.Mode.DATE_RANGE,
                    trigger=ReceiptDispatch.Trigger.AUTOMATIC,
                    status=ReceiptDispatch.Status.PENDING,
                    auto_created=True,
                    email_to=str(sample.person_email or ""),
                    metadata={"campaign_key": campaign_key, "runtime_smoke": True},
                )
            processed = receipt_module.process_campaign_receipt_dispatches(
                campaign_key=campaign_key,
                limit=10,
                actor="verificador_runtime",
                pending_only=True,
                sleep_seconds=0.01,
                pause_every=1,
                pause_seconds=0.02,
            )
            payload["queue_processor_processed"] = len(processed)
            payload["queue_processor_statuses"] = [str(item.status or "") for item in processed]
            payload["queue_processor_sleep_calls"] = sleep_calls
            transaction.set_rollback(True)
    except Exception as exc:
        payload["queue_processor_error"] = str(exc)
    finally:
        receipt_module.send_email_message = original_send
        receipt_module.time.sleep = original_sleep

print(json.dumps(payload, ensure_ascii=True, sort_keys=True))
"""
    return _docker_shell_json(docker_bin, runtime_dir, code)


def _http_status(url: str) -> tuple[int | None, str]:
    request = Request(url, headers={"User-Agent": "power-church-runtime-check"})
    try:
        with urlopen(request, timeout=5) as response:
            return int(response.status), ""
    except HTTPError as exc:
        return int(exc.code), str(exc)
    except URLError as exc:
        return None, str(exc)


def _write_report(checks: list[Check]) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    target = REPORT_DIR / f"verificar_runtime_postgres_operacional_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    lines = [
        "# Verificacao Do Runtime PostgreSQL Operacional",
        "",
        f"Gerado em: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "| Area | Checagem | Status | Detalhe |",
        "| --- | --- | --- | --- |",
    ]
    for item in checks:
        lines.append(f"| {item.area} | {item.name} | {item.status} | {item.detail} |")
    failed = [item for item in checks if item.failed]
    if failed:
        lines.extend(["", "## Divergencias", ""])
        for item in failed:
            lines.append(f"- `{item.area}` {item.name}: {item.detail}")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="Valida o runtime Docker PostgreSQL operacional.")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Banco legado SQLite.")
    parser.add_argument("--runtime-dir", default=str(DEFAULT_RUNTIME_DIR), help="Diretorio persistente do runtime.")
    parser.add_argument("--runtime-url", default=DEFAULT_RUNTIME_URL, help="URL base do runtime.")
    parser.add_argument("--report", action="store_true", help="Gera relatorio markdown.")
    args = parser.parse_args()

    checks: list[Check] = []
    db_path = Path(args.db).expanduser().resolve()
    runtime_dir = Path(args.runtime_dir).expanduser().resolve()
    docker_bin = _resolve_docker_bin()
    if docker_bin is None:
        _record(checks, "runtime", "Docker disponivel", False, "docker nao encontrado")
        if args.report:
            report = _write_report(checks)
            print(f"Relatorio: {report}")
        return 1

    try:
        ps_output = _compose_output(docker_bin, runtime_dir, ["ps"])
        _record(checks, "runtime", "docker compose ps", True, ps_output.replace("\n", " | "))
    except Exception as exc:
        _record(checks, "runtime", "docker compose ps", False, str(exc))
        if args.report:
            report = _write_report(checks)
            print(f"Relatorio: {report}")
        return 1

    login_status, login_detail = _http_status(args.runtime_url.rstrip("/") + "/accounts/login/")
    _record(checks, "http", "login responde", login_status == 200, f"status={login_status} {login_detail}".strip())
    imports_status, imports_detail = _http_status(args.runtime_url.rstrip("/") + "/imports/rules/")
    _record(checks, "http", "regras de centavos respondem", imports_status in {200, 302}, f"status={imports_status} {imports_detail}".strip())

    legacy = _load_legacy_counts(db_path)
    runtime = _runtime_counts(docker_bin, runtime_dir)
    export_probe = _runtime_export_probe(docker_bin, runtime_dir)
    export_guard_probe = _runtime_export_guard_probe(docker_bin, runtime_dir)
    receipt_queue_probe = _runtime_receipt_queue_probe(docker_bin, runtime_dir)

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
        _record(
            checks,
            "cargas",
            label,
            int(legacy.get(key) or 0) == int(runtime.get(key) or 0),
            f"legado={legacy.get(key)} runtime={runtime.get(key)}",
        )

    _record(
        checks,
        "cargas",
        "contribuicoes da ficha por identidade financeira",
        int(legacy.get("person_contributions_identity_active") or 0) == int(runtime.get("person_contributions_identity_active") or 0),
        f"legado_com_identidade={legacy.get('person_contributions_identity_active')} runtime={runtime.get('person_contributions_identity_active')} legado_direto={legacy.get('person_contributions_direct_active')}",
    )
    requested = list(export_probe.get("requested") or [])
    resolved_columns = list(export_probe.get("resolved_columns") or [])
    headers = list(export_probe.get("headers") or [])
    _record(
        checks,
        "exportacao",
        "exportacao dinamica preserva colunas selecionadas",
        requested == resolved_columns,
        f"solicitadas={requested} resolvidas={resolved_columns}",
    )
    _record(
        checks,
        "exportacao",
        "exportacao dinamica gera cabecalhos esperados",
        len(headers) == len(requested),
        f"headers={headers} total={len(headers)}",
    )
    _record(
        checks,
        "exportacao",
        "exportacao dinamica inclui campos nao basicos",
        all(label in headers for label in ["Familia domiciliar", "Contribuicoes total", "Ultima competencia", "Familia resumo financeiro"]),
        f"headers={headers}",
    )
    _record(
        checks,
        "exportacao",
        "exportacao dinamica alinha largura das linhas",
        int(export_probe.get("height") or 0) == 0 or int(export_probe.get("first_row_len") or 0) == len(requested),
        f"linhas={export_probe.get('height')} largura_primeira={export_probe.get('first_row_len')}",
    )
    _record(
        checks,
        "exportacao",
        "exportacao dinamica vazia nao cai no preset basico",
        int(export_guard_probe.get("status_code") or 0) == 302
        and "#exportacao-dinamica" in str(export_guard_probe.get("location") or ""),
        f"status={export_guard_probe.get('status_code')} location={export_guard_probe.get('location')}",
    )
    _record(
        checks,
        "fila_recibos",
        "runtime usa Microsoft Graph",
        str(receipt_queue_probe.get("provider") or "") == "microsoft_graph",
        f"provider={receipt_queue_probe.get('provider')}",
    )
    _record(
        checks,
        "fila_recibos",
        "credenciais do Graph estao carregadas",
        all(
            bool(receipt_queue_probe.get(key))
            for key in [
                "graph_sender_configured",
                "graph_tenant_configured",
                "graph_client_configured",
                "graph_secret_configured",
            ]
        ),
        (
            f"sender={receipt_queue_probe.get('graph_sender_configured')} "
            f"tenant={receipt_queue_probe.get('graph_tenant_configured')} "
            f"client={receipt_queue_probe.get('graph_client_configured')} "
            f"secret={receipt_queue_probe.get('graph_secret_configured')}"
        ),
    )
    _record(
        checks,
        "fila_recibos",
        "fila automatica de recibos esta ligada",
        bool(receipt_queue_probe.get("auto_email_enabled")) and bool(receipt_queue_probe.get("auto_send_enabled")),
        (
            f"auto_email={receipt_queue_probe.get('auto_email_enabled')} "
            f"auto_send={receipt_queue_probe.get('auto_send_enabled')}"
        ),
    )
    _record(
        checks,
        "fila_recibos",
        "smoke da fila automatica de recibos responde",
        not str(receipt_queue_probe.get("smoke_error") or "")
        and int(receipt_queue_probe.get("smoke_sent_count") or 0) > 0,
        (
            f"person={receipt_queue_probe.get('smoke_person_id')} "
            f"dispatches={receipt_queue_probe.get('smoke_dispatch_ids')} "
            f"statuses={receipt_queue_probe.get('smoke_statuses')} "
            f"error={receipt_queue_probe.get('smoke_error')}"
        ),
    )
    _record(
        checks,
        "fila_recibos",
        "fila automatica consegue enfileirar sem disparo imediato",
        not str(receipt_queue_probe.get("queued_error") or "")
        and bool(receipt_queue_probe.get("queued_dispatch_ids"))
        and all(str(status or "") == "pendente" for status in receipt_queue_probe.get("queued_statuses") or []),
        (
            f"dispatches={receipt_queue_probe.get('queued_dispatch_ids')} "
            f"statuses={receipt_queue_probe.get('queued_statuses')} "
            f"error={receipt_queue_probe.get('queued_error')}"
        ),
    )
    _record(
        checks,
        "fila_recibos",
        "processador da fila aplica cadencia controlada",
        not str(receipt_queue_probe.get("queue_processor_error") or "")
        and int(receipt_queue_probe.get("queue_processor_processed") or 0) >= 2
        and all(str(status or "") == "enviado" for status in receipt_queue_probe.get("queue_processor_statuses") or [])
        and any(float(value or 0) > 0 for value in receipt_queue_probe.get("queue_processor_sleep_calls") or []),
        (
            f"processed={receipt_queue_probe.get('queue_processor_processed')} "
            f"statuses={receipt_queue_probe.get('queue_processor_statuses')} "
            f"sleep_calls={receipt_queue_probe.get('queue_processor_sleep_calls')} "
            f"error={receipt_queue_probe.get('queue_processor_error')}"
        ),
    )

    if args.report:
        report = _write_report(checks)
        print(f"Relatorio: {report}")
    failed = [item for item in checks if item.failed]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

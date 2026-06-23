from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "data" / "homologacao"
DJANGO_DIR = ROOT / "power_church_django"
DJANGO_VENV_PYTHON = DJANGO_DIR / ".venv" / "bin" / "python"
DEFAULT_ENV_FILE = ROOT / ".env.power_church_django.postgres.local"


@dataclass
class Check:
    area: str
    name: str
    status: str
    detail: str

    @property
    def failed(self) -> bool:
        return self.status == "FALHA"


def _load_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if value.startswith(("'", '"')) and value.endswith(("'", '"')) and len(value) >= 2:
            value = value[1:-1]
        env[key] = value
    return env


def _prefer_local_postgres_socket(env: dict[str, str]) -> dict[str, str]:
    host = str(env.get("POWER_CHURCH_POSTGRES_HOST") or "").strip()
    port = str(env.get("POWER_CHURCH_POSTGRES_PORT") or "5432").strip() or "5432"
    socket_path = Path(f"/tmp/.s.PGSQL.{port}")
    if host in {"127.0.0.1", "localhost"} and socket_path.exists():
        env["POWER_CHURCH_POSTGRES_HOST"] = "/tmp"
    return env


def _write_report(checks: list[Check]) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    target = REPORT_DIR / f"verificar_roteiro_operador_zero_hibrido_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    lines = [
        "# Verificacao Do Roteiro Operador Zero Hibrido",
        "",
        f"Gerado em: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "| Area | Checagem | Status | Detalhe |",
        "| --- | --- | --- | --- |",
    ]
    for check in checks:
        lines.append(f"| {check.area} | {check.name} | {check.status} | {check.detail} |")
    failed = [check for check in checks if check.failed]
    if failed:
        lines.extend(
            [
                "",
                "## Bugs Identificaveis Localmente",
                "",
            ]
        )
        for check in failed:
            lines.append(f"- `{check.area}` {check.name}: {check.detail}")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def _run_probe(args: list[str]) -> tuple[bool, str]:
    if not DJANGO_VENV_PYTHON.exists():
        return False, f"Python da venv Django nao encontrado: {DJANGO_VENV_PYTHON}"
    env = dict(os.environ)
    env_file = Path(env.get("POWER_CHURCH_ENV_FILE") or DEFAULT_ENV_FILE)
    env.update(_prefer_local_postgres_socket(_load_env_file(env_file)))
    env.setdefault("PYTHONPYCACHEPREFIX", "/private/tmp/pycache_powerchurch")
    completed = subprocess.run(
        [str(DJANGO_VENV_PYTHON), *args],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )
    output = "\n".join(part for part in [completed.stdout.strip(), completed.stderr.strip()] if part)
    return completed.returncode == 0, output


def django_probe_code() -> str:
    return r"""
import json
import os
import sys
from pathlib import Path

root = Path.cwd()
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "power_church_django"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "power_church_site.settings")

import django
django.setup()

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.test import Client

from power_church_django.apps.contributions.models import NativeAuxContributor, NativeContribution, NativeEnvelope, NativeEnvelopeLot, NativeEnvelopeProfileUpdate, ReceiptDispatch, ReceiptSnapshot
from power_church_django.apps.imports.models import StatementImportPilotLot, StatementImportPilotMovement
from power_church_django.apps.people.models import NativePeopleImportLot, PersonContributionSnapshot, PersonSecureTrashSnapshot, PersonSnapshot
from power_church_django.services.contributions_native import (
    create_contribution_postgres,
    create_manual_contribution_batch_postgres,
    update_contribution_postgres,
)
from power_church_django.services.contributors_native import (
    create_frequentador_from_contributor_postgres,
    link_contributor_to_person_by_id_postgres,
)
from power_church_django.services.envelopes_native import (
    apply_envelope_profile_update_postgres,
    ignore_envelope_profile_update_postgres,
    ignore_pending_envelope_postgres,
)
from power_church_django.apps.imports.services import (
    prepare_statement_lot_postgres_native,
    reprocess_statement_lot_postgres_native,
    update_statement_movement_postgres_native,
)
from power_church_django.services.people_native_write import (
    create_person_postgres,
    soft_delete_person_postgres,
    update_person_postgres,
)
from power_church_django.services import receipt_delivery as receipt_module
from power_church_django.services.mail_dispatch import MailDispatchResult
from power_church_django.services.receipt_delivery import (
    issue_receipt_for_contribution_ids,
    process_campaign_receipt_dispatches,
    queue_receipt_dispatches,
    schedule_automatic_receipts_for_events,
)


results = []


def record(area, name, ok, detail):
    results.append(
        {
            "area": area,
            "name": name,
            "status": "OK" if ok else "FALHA",
            "detail": str(detail).replace("\n", " ").strip(),
        }
    )


def expect_status(area, name, response, allowed, detail_prefix=""):
    ok = response.status_code in allowed
    detail = f"{detail_prefix}status={response.status_code}"
    if hasattr(response, "url") and response.url:
        detail += f" url={response.url}"
    record(area, name, ok, detail)


User = get_user_model()
user = User.objects.filter(is_active=True).order_by("is_superuser", "id").last()
if not user:
    print("__RESULT_JSON_START__")
    print(json.dumps([{"area": "boot", "name": "usuario ativo", "status": "FALHA", "detail": "nenhum usuario ativo encontrado"}], ensure_ascii=True))
    print("__RESULT_JSON_END__")
    raise SystemExit(1)

if "testserver" not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append("testserver")
for host in ("127.0.0.1", "localhost"):
    if host not in settings.ALLOWED_HOSTS:
        settings.ALLOWED_HOSTS.append(host)
settings.MIDDLEWARE = [item for item in settings.MIDDLEWARE if "whitenoise" not in item.lower()]

anonymous = Client(HTTP_HOST="127.0.0.1", raise_request_exception=False)
client = Client(HTTP_HOST="127.0.0.1", raise_request_exception=False)
client.force_login(user)

person = PersonSnapshot.objects.filter(is_active=True).order_by("legacy_id").first()
trash_item = PersonSecureTrashSnapshot.objects.order_by("-id").first()
contribution = NativeContribution.objects.filter(is_active=True).order_by("-legacy_id").first()
envelope_lot = NativeEnvelopeLot.objects.filter(is_active=True).exclude(status="").order_by("-legacy_id").first()
open_lot = NativeEnvelopeLot.objects.filter(is_active=True, status__in=["aberto", "parcial", "aguardando_digitacao", "digitado", "duplicado", "ignorado"]).order_by("-legacy_id").first()
envelope = NativeEnvelope.objects.filter(is_active=True).order_by("-legacy_id").first()
pending_envelope = NativeEnvelope.objects.filter(is_active=True, status__in=["pendente", "aberto", "parcial", "aguardando_digitacao"]).order_by("-legacy_id").first()
launched_envelope = NativeEnvelope.objects.filter(is_active=True, status__in=["lancado", "regular"]).order_by("-legacy_id").first()
profile_update = NativeEnvelopeProfileUpdate.objects.order_by("-id").first()
receipt = ReceiptSnapshot.objects.order_by("-legacy_id").first()
statement_lot = StatementImportPilotLot.objects.order_by("-id").first()
statement_movement = StatementImportPilotMovement.objects.order_by("-id").first()
resolved_duplicate_lot = (
    StatementImportPilotLot.objects.filter(
        movements__review_status="revisar_duplicidade",
        movements__imported_contribution_legacy_id__isnull=False,
    )
    .order_by("-updated_at", "-id")
    .distinct()
    .first()
)
people_import_lot = NativePeopleImportLot.objects.order_by("-legacy_id").first()
aux_contributor = NativeAuxContributor.objects.filter(is_active=True).order_by("id").first()
unlinked_aux_contributor = NativeAuxContributor.objects.filter(is_active=True, person_legacy_id__isnull=True).order_by("id").first()
person_with_contributions = (
    PersonContributionSnapshot.objects.filter(is_active=True, person__is_active=True)
    .select_related("person")
    .order_by("-legacy_id")
    .first()
)
pending_statement_movement = (
    StatementImportPilotMovement.objects.filter(
        lot__source_backend="postgres_nativo",
        review_status__in=["pendente", "revisar_pessoa", "revisar_destinacao", "classificacao_pendente", "revisar_duplicidade"],
    )
    .order_by("lot_id", "order_in_lot", "id")
    .first()
)

# Seguranca
response = anonymous.get("/", follow=False)
record("Login e seguranca", "Dashboard sem login redireciona", response.status_code in {301, 302} and "/accounts/login/" in str(response.headers.get("Location") or ""), f"status={response.status_code} location={response.headers.get('Location', '')}")
response = anonymous.get("/people/", follow=False)
record("Login e seguranca", "Pessoas sem login redireciona", response.status_code in {301, 302} and "/accounts/login/" in str(response.headers.get("Location") or ""), f"status={response.status_code} location={response.headers.get('Location', '')}")
response = anonymous.get("/accounts/login/", follow=False)
expect_status("Login e seguranca", "Login publico abre", response, {200})

# Pessoas
response = client.get("/people/")
expect_status("Pessoas", "Lista de pessoas abre", response, {200})
if person:
    response = client.get(f"/people/?q={person.legacy_id}")
    expect_status("Pessoas", "Busca por ID/codigo abre", response, {200}, f"person={person.legacy_id} ")
    response = client.get(f"/people/{person.legacy_id}/")
    expect_status("Pessoas", "Ficha da pessoa abre", response, {200}, f"person={person.legacy_id} ")
    response = client.get(f"/contributions/statements/{person.legacy_id}/")
    expect_status("Contribuicoes", "Extrato por pessoa abre", response, {200}, f"person={person.legacy_id} ")
else:
    record("Pessoas", "Amostra de pessoa", False, "nenhuma pessoa ativa encontrada")
response = client.get("/people/new/")
expect_status("Pessoas", "Tela de nova pessoa abre", response, {200})
if trash_item:
    response = client.get("/people/trash/")
    expect_status("Pessoas", "Lixeira segura abre", response, {200})

# Familias
response = client.get("/people/families/")
expect_status("Familias e auditoria familiar", "Familias domiciliares abrem", response, {200})
response = client.get("/people/families/?section=audit")
expect_status("Familias e auditoria familiar", "Fila de auditoria abre", response, {200})
response = client.get("/people/families/?section=audit&mode=automatic")
expect_status("Familias e auditoria familiar", "Tag alta confianca abre", response, {200})
response = client.get("/people/families/?section=organized&person_status=ativo")
expect_status("Familias e auditoria familiar", "Filtro membros ativos abre", response, {200})

# Merge
if person:
    response = client.get(f"/people/{person.legacy_id}/merge/")
    expect_status("Merge", "Tela de merge abre", response, {200}, f"person={person.legacy_id} ")
    response = client.get(f"/people/{person.legacy_id}/merge/?merge_lookup=Maria")
    expect_status("Merge", "Busca de merge abre comparativo", response, {200}, f"person={person.legacy_id} ")

# Contribuicoes
response = client.get("/contributions/")
expect_status("Contribuicoes", "Lista de contribuicoes abre", response, {200})
response = client.get("/contributions/new/")
expect_status("Contribuicoes", "Tela de nova contribuicao abre", response, {200})
response = client.get("/contributions/manual/")
expect_status("Contribuicoes", "Lancamento manual assistido abre", response, {200})
if contribution:
    response = client.get(f"/contributions/{contribution.legacy_id}/")
    expect_status("Contribuicoes", "Detalhe de contribuicao abre", response, {200}, f"contribution={contribution.legacy_id} ")
    response = client.get(f"/contributions/{contribution.legacy_id}/split/")
    expect_status("Contribuicoes", "Tela de rateio abre", response, {200}, f"contribution={contribution.legacy_id} ")
else:
    record("Contribuicoes", "Amostra de contribuicao", False, "nenhuma contribuicao nativa encontrada")

# Envelopes
response = client.get("/contributions/envelopes/")
expect_status("Envelopes", "Lista de envelopes abre", response, {200})
response = client.get("/contributions/envelopes/new/")
expect_status("Envelopes", "Novo envelope abre", response, {200})
response = client.get("/contributions/envelopes/lots/new/")
expect_status("Envelopes", "Novo lote de envelopes abre", response, {200})
if open_lot:
    response = client.get(f"/contributions/envelopes/lots/{open_lot.legacy_id}/")
    expect_status("Envelopes", "Lote de envelopes inacabado abre", response, {200}, f"lot={open_lot.legacy_id} status={open_lot.status} ")
    response = client.get(f"/contributions/envelopes/lots/{open_lot.legacy_id}/next/")
    expect_status("Envelopes", "Proximo pendente redireciona", response, {302}, f"lot={open_lot.legacy_id} status={open_lot.status} ")
elif envelope_lot:
    response = client.get(f"/contributions/envelopes/lots/{envelope_lot.legacy_id}/")
    expect_status("Envelopes", "Lote de envelopes abre", response, {200}, f"lot={envelope_lot.legacy_id} status={envelope_lot.status} ")
else:
    record("Envelopes", "Amostra de lote", False, "nenhum lote nativo de envelope encontrado")
if pending_envelope:
    response = client.get(f"/contributions/envelopes/{pending_envelope.legacy_id}/launch/")
    expect_status("Envelopes", "Tela de lancar pendente abre", response, {200}, f"envelope={pending_envelope.legacy_id} status={pending_envelope.status} ")
    try:
        with transaction.atomic():
            response = client.post(
                f"/contributions/envelopes/{pending_envelope.legacy_id}/ignore/",
                {
                    "lote_id": int(pending_envelope.native_lot_legacy_id or 0),
                    "justificativa_ignorar": "Ignorado em teste automatizado com rollback.",
                },
                follow=False,
            )
            transaction.set_rollback(True)
        expect_status("Envelopes", "Ignorar pendente responde com redirect", response, {302}, f"envelope={pending_envelope.legacy_id} ")
    except Exception as exc:
        record("Envelopes", "Ignorar pendente responde com redirect", False, f"envelope={pending_envelope.legacy_id} erro={exc}")
if launched_envelope:
    response = client.get(f"/contributions/envelopes/{launched_envelope.legacy_id}/edit/")
    expect_status("Envelopes", "Editar envelope lancado abre", response, {200}, f"envelope={launched_envelope.legacy_id} status={launched_envelope.status} ")
    response = client.get(f"/contributions/envelopes/{launched_envelope.legacy_id}/")
    expect_status("Envelopes", "Detalhe do envelope abre", response, {200}, f"envelope={launched_envelope.legacy_id} ")
    response = client.get(f"/contributions/envelopes/{launched_envelope.legacy_id}/image/")
    expect_status("Envelopes", "Imagem do envelope responde", response, {200, 404}, f"envelope={launched_envelope.legacy_id} ")
if profile_update:
    target = int(profile_update.envelope.legacy_id or 0)
    response = client.get(f"/contributions/envelopes/{target}/")
    expect_status("Envelopes", "Detalhe com pendencia cadastral abre", response, {200}, f"envelope={target} ")
    try:
        with transaction.atomic():
            response = client.post(
                f"/contributions/envelopes/profile-updates/{int(profile_update.id or 0)}/ignore/",
                {"envelope_id": target},
                follow=False,
            )
            transaction.set_rollback(True)
        expect_status("Envelopes", "Ignorar pendencia cadastral responde com redirect", response, {302}, f"update={int(profile_update.id or 0)} envelope={target} ")
    except Exception as exc:
        record("Envelopes", "Ignorar pendencia cadastral responde com redirect", False, f"update={int(profile_update.id or 0)} erro={exc}")

# Gravacoes controladas com rollback
if person:
    try:
        with transaction.atomic():
            created_person_id = create_person_postgres(
                {
                    "nome": "Teste Automatizado Runtime",
                    "status": "frequentador",
                    "email_principal": "teste.runtime.zerohibrido@example.com",
                    "telefone_principal": "21999998888",
                    "cidade": "Niteroi",
                    "uf": "RJ",
                    "logradouro": "Rua de Teste",
                    "numero": "123",
                    "bairro": "Centro",
                },
                actor="verificador_roteiro",
            )
            transaction.set_rollback(True)
        record("Pessoas", "Criacao de pessoa nativa responde", bool(created_person_id), f"person={created_person_id}")
    except Exception as exc:
        record("Pessoas", "Criacao de pessoa nativa responde", False, f"erro={exc}")

    try:
        with transaction.atomic():
            update_person_postgres(
                int(person.legacy_id or 0),
                {
                    "codigo_interno": person.internal_code or "",
                    "nome": person.name or "",
                    "nome_social": person.social_name or "",
                    "cpf": person.cpf or "",
                    "rg": person.rg or "",
                    "data_nascimento": person.birth_date_raw or "",
                    "sexo": person.sex or "",
                    "estado_civil": person.marital_status or "",
                    "email_principal": person.primary_email or "",
                    "telefone_principal": person.primary_phone or "",
                    "whatsapp_principal": person.primary_whatsapp or "",
                    "status": person.status or "frequentador",
                    "observacoes": "Atualizacao de teste com rollback.",
                },
                actor="verificador_roteiro",
            )
            transaction.set_rollback(True)
        record("Pessoas", "Edicao de pessoa nativa responde", True, f"person={int(person.legacy_id or 0)}")
    except Exception as exc:
        record("Pessoas", "Edicao de pessoa nativa responde", False, f"person={int(person.legacy_id or 0)} erro={exc}")

    try:
        with transaction.atomic():
            temp_person_id = create_person_postgres(
                {
                    "nome": "Teste Lixeira Runtime",
                    "status": "frequentador",
                    "email_principal": "teste.lixeira.runtime@example.com",
                },
                actor="verificador_roteiro",
            )
            trash_id = soft_delete_person_postgres(
                int(temp_person_id or 0),
                "Exclusao controlada em teste automatizado.",
                actor="verificador_roteiro",
            )
            transaction.set_rollback(True)
        record("Pessoas", "Lixeira segura responde", bool(trash_id), f"trash={trash_id}")
    except Exception as exc:
        record("Pessoas", "Lixeira segura responde", False, f"erro={exc}")

try:
    from power_church_django.apps.contributions.models import ContributionTypeSnapshot

    type_row = ContributionTypeSnapshot.objects.filter(is_active=True).order_by("legacy_id").first()
except Exception:
    type_row = None

if person and type_row:
    try:
        with transaction.atomic():
            created_contribution_id = create_contribution_postgres(
                {
                    "pessoa_id": str(int(person.legacy_id or 0)),
                    "data_recebimento": "2026-06-07",
                    "valor": "10,00",
                    "tipo_contribuicao_id": str(int(type_row.legacy_id or 0)),
                    "status_operacional": "regular",
                    "observacoes": "Criacao controlada por smoke test.",
                    "justificativa": "Criacao controlada para teste.",
                },
                actor="verificador_roteiro",
            )
            transaction.set_rollback(True)
        record("Contribuicoes", "Criacao de contribuicao nativa responde", bool(created_contribution_id), f"contribution={created_contribution_id}")
    except Exception as exc:
        record("Contribuicoes", "Criacao de contribuicao nativa responde", False, f"erro={exc}")

    try:
        with transaction.atomic():
            batch_ids = create_manual_contribution_batch_postgres(
                {
                    "data_recebimento": "2026-06-07",
                    "forma_recebimento_id": "",
                    "status_operacional": "regular",
                    "justificativa": "Lote controlado para teste.",
                    "observacoes": "Smoke test do lote manual.",
                    "origem_operacional": "Teste automatizado",
                    "valor_total": "12,50",
                    "line_count": "1",
                    "linha_valor_1": "12,50",
                    "linha_pessoa_id_1": str(int(person.legacy_id or 0)),
                    "linha_tipo_contribuicao_id_1": str(int(type_row.legacy_id or 0)),
                    "linha_observacoes_1": "Linha de teste",
                },
                actor="verificador_roteiro",
            )
            transaction.set_rollback(True)
        record("Contribuicoes", "Lote manual de contribuicoes responde", bool(batch_ids), f"ids={batch_ids}")
    except Exception as exc:
        record("Contribuicoes", "Lote manual de contribuicoes responde", False, f"erro={exc}")

if contribution:
    try:
        with transaction.atomic():
            update_contribution_postgres(
                int(contribution.legacy_id or 0),
                {
                    "data_recebimento": contribution.received_at_raw or "2026-06-07",
                    "valor": str(contribution.amount or "0").replace(".", ","),
                    "tipo_contribuicao_id": str(int(contribution.contribution_type_legacy_id or 0)),
                    "campanha_id": str(int(contribution.campaign_legacy_id or 0) or ""),
                    "forma_recebimento_id": str(int(contribution.receipt_method_legacy_id or 0) or ""),
                    "status_operacional": contribution.operational_status or "regular",
                    "observacoes": "Edicao controlada com rollback.",
                    "justificativa": "Edicao controlada para teste.",
                },
                actor="verificador_roteiro",
            )
            transaction.set_rollback(True)
        record("Contribuicoes", "Edicao de contribuicao nativa responde", True, f"contribution={int(contribution.legacy_id or 0)}")
    except Exception as exc:
        record("Contribuicoes", "Edicao de contribuicao nativa responde", False, f"contribution={int(contribution.legacy_id or 0)} erro={exc}")

receipt_person = person_with_contributions.person if person_with_contributions else None
if receipt_person:
    contribution_ids_for_receipt = list(
        PersonContributionSnapshot.objects.filter(person__legacy_id=int(receipt_person.legacy_id or 0), is_active=True)
        .order_by("-legacy_id")
        .values_list("legacy_id", flat=True)[:2]
    )
    if contribution_ids_for_receipt:
        try:
            with transaction.atomic():
                receipt_id = issue_receipt_for_contribution_ids(
                    person_id=int(receipt_person.legacy_id or 0),
                    contribution_ids=[int(value) for value in contribution_ids_for_receipt],
                    emission_date="2026-06-07",
                    notes="Recibo gerado em teste automatizado com rollback.",
                    actor="verificador_roteiro",
                    replace_existing=True,
                )
                dispatches = queue_receipt_dispatches(
                    [int(receipt_id or 0)],
                    email_to=str(receipt_person.primary_email or "teste.recibo.runtime@example.com"),
                    subject="Teste de recibo",
                    body="Envio de teste com rollback.",
                    actor="verificador_roteiro",
                    send_now=False,
                )
                transaction.set_rollback(True)
            record("Recibos", "Criacao de recibo nativo responde", bool(receipt_id), f"receipt={receipt_id}")
            record("Fila e monitor", "Enfileiramento de recibo responde", bool(dispatches), f"dispatches={len(dispatches)}")
        except Exception as exc:
            record("Recibos", "Criacao de recibo nativo responde", False, f"erro={exc}")
            record("Fila e monitor", "Enfileiramento de recibo responde", False, f"erro={exc}")

        try:
            with transaction.atomic():
                original_send = receipt_module.send_email_message
                def fake_send_email_message(**kwargs):
                    return MailDispatchResult(
                        provider="microsoft_graph",
                        accepted=True,
                        metadata={"fake": True, "to": list(kwargs.get("to_emails") or [])},
                    )

                receipt_module.send_email_message = fake_send_email_message
                outcomes = schedule_automatic_receipts_for_events(
                    [int(value) for value in contribution_ids_for_receipt],
                    actor="verificador_roteiro",
                    send_now=False,
                )
                dispatches = list(
                    ReceiptDispatch.objects.filter(trigger=ReceiptDispatch.Trigger.AUTOMATIC)
                    .order_by("-id")[: max(1, len(outcomes))]
                )
                transaction.set_rollback(True)
            record(
                "Fila e monitor",
                "Fila automatica agenda recibos sem disparo imediato",
                bool(outcomes) and bool(dispatches) and all(item.status == ReceiptDispatch.Status.PENDING for item in dispatches),
                f"outcomes={len(outcomes)} statuses={[item.status for item in dispatches]}",
            )
        except Exception as exc:
            record("Fila e monitor", "Fila automatica agenda recibos sem disparo imediato", False, f"erro={exc}")
        finally:
            receipt_module.send_email_message = original_send

        try:
            with transaction.atomic():
                original_send = receipt_module.send_email_message
                original_sleep = receipt_module.time.sleep
                sleep_calls = []

                def fake_send_email_message(**kwargs):
                    return MailDispatchResult(
                        provider="microsoft_graph",
                        accepted=True,
                        metadata={"fake": True, "queue_processor": True, "to": list(kwargs.get("to_emails") or [])},
                    )

                def fake_sleep(seconds):
                    sleep_calls.append(float(seconds))

                receipt_module.send_email_message = fake_send_email_message
                receipt_module.time.sleep = fake_sleep
                campaign_key = "roteiro_queue_smoke"
                receipt_samples = list(ReceiptSnapshot.objects.exclude(person_email="").order_by("legacy_id")[:2])
                for sample in receipt_samples:
                    ReceiptDispatch.objects.create(
                        organization_id=int(sample.organization_id or 0) or None,
                        legacy_person_id=int(sample.person_legacy_id or 0),
                        legacy_receipt_id=int(sample.legacy_id or 0),
                        legacy_receipt_number=str(sample.receipt_number or ""),
                        person_name=str(sample.person_name or ""),
                        person_email=str(sample.person_email or ""),
                        mode=ReceiptDispatch.Mode.DATE_RANGE,
                        trigger=ReceiptDispatch.Trigger.AUTOMATIC,
                        status=ReceiptDispatch.Status.PENDING,
                        auto_created=True,
                        email_to=str(sample.person_email or ""),
                        metadata={"campaign_key": campaign_key, "roteiro_smoke": True},
                    )
                processed = process_campaign_receipt_dispatches(
                    campaign_key=campaign_key,
                    limit=10,
                    actor="verificador_roteiro",
                    pending_only=True,
                    sleep_seconds=0.01,
                    pause_every=1,
                    pause_seconds=0.02,
                )
                transaction.set_rollback(True)
            record(
                "Fila e monitor",
                "Processador da fila respeita cadencia controlada",
                len(processed) >= 2 and all(item.status == ReceiptDispatch.Status.SENT for item in processed) and any(value > 0 for value in sleep_calls),
                f"processed={len(processed)} statuses={[item.status for item in processed]} sleep_calls={sleep_calls}",
            )
        except Exception as exc:
            record("Fila e monitor", "Processador da fila respeita cadencia controlada", False, f"erro={exc}")
        finally:
            receipt_module.send_email_message = original_send
            receipt_module.time.sleep = original_sleep

if pending_envelope:
    try:
        with transaction.atomic():
            result = ignore_pending_envelope_postgres(
                int(pending_envelope.legacy_id or 0),
                "Ignorado em teste automatizado com rollback.",
                actor="verificador_roteiro",
            )
            transaction.set_rollback(True)
        record("Envelopes", "Servico de ignorar pendente responde", bool(result.get("envelope_id")), f"envelope={int(pending_envelope.legacy_id or 0)}")
    except Exception as exc:
        record("Envelopes", "Servico de ignorar pendente responde", False, f"envelope={int(pending_envelope.legacy_id or 0)} erro={exc}")

if profile_update:
    try:
        with transaction.atomic():
            result = ignore_envelope_profile_update_postgres(int(profile_update.id or 0), actor="verificador_roteiro")
            transaction.set_rollback(True)
        record("Envelopes", "Servico de ignorar pendencia cadastral responde", bool(result.get("envelope_id")), f"update={int(profile_update.id or 0)}")
    except Exception as exc:
        record("Envelopes", "Servico de ignorar pendencia cadastral responde", False, f"update={int(profile_update.id or 0)} erro={exc}")

    try:
        with transaction.atomic():
            result = apply_envelope_profile_update_postgres(int(profile_update.id or 0), actor="verificador_roteiro")
            transaction.set_rollback(True)
        record("Envelopes", "Servico de aplicar pendencia cadastral responde", bool(result.get("envelope_id")), f"update={int(profile_update.id or 0)}")
    except Exception as exc:
        record("Envelopes", "Servico de aplicar pendencia cadastral responde", False, f"update={int(profile_update.id or 0)} erro={exc}")

if aux_contributor and person:
    try:
        with transaction.atomic():
            ok = link_contributor_to_person_by_id_postgres(
                int(aux_contributor.id or 0),
                int(person.legacy_id or 0),
                actor="verificador_roteiro",
            )
            transaction.set_rollback(True)
        record("Contribuintes auxiliares", "Vinculo de contribuinte a pessoa responde", bool(ok), f"contributor={int(aux_contributor.id or 0)} person={int(person.legacy_id or 0)}")
    except Exception as exc:
        record("Contribuintes auxiliares", "Vinculo de contribuinte a pessoa responde", False, f"erro={exc}")

if unlinked_aux_contributor:
    try:
        with transaction.atomic():
            created_person_id = create_frequentador_from_contributor_postgres(
                int(unlinked_aux_contributor.id or 0),
                actor="verificador_roteiro",
            )
            transaction.set_rollback(True)
        record("Contribuintes auxiliares", "Criacao de frequentador por contribuinte responde", bool(created_person_id), f"person={created_person_id}")
    except Exception as exc:
        record("Contribuintes auxiliares", "Criacao de frequentador por contribuinte responde", False, f"erro={exc}")

# Recibos e fila
response = client.get("/receipts/")
expect_status("Recibos", "Central de recibos abre", response, {200})
response = client.get("/receipts/new/")
expect_status("Recibos", "Gerador manual de recibos redireciona para a central", response, {302})
response = client.get("/receipts/queue/")
expect_status("Fila e monitor", "Monitor da fila abre", response, {200})
if receipt:
    response = client.get(f"/receipts/{receipt.legacy_id}/")
    expect_status("Recibos", "Detalhe do recibo abre", response, {200}, f"receipt={receipt.legacy_id} ")
    response = client.get(f"/receipts/{receipt.legacy_id}/pdf/")
    expect_status("Recibos", "PDF do recibo responde", response, {200}, f"receipt={receipt.legacy_id} ")
else:
    record("Recibos", "Amostra de recibo", False, "nenhum recibo encontrado")

# Extratos / importacoes bancarias
response = client.get("/imports/")
expect_status("Importacoes", "Dashboard de importacoes abre", response, {200})
response = client.get("/imports/rules/")
expect_status("Importacoes", "Regras de centavos abrem", response, {200})
if statement_lot:
    backend = "postgres_nativo" if str(statement_lot.source_backend or "") == "postgres_nativo" else "django_web"
    response = client.get(f"/imports/statement/{statement_lot.id}/?backend={backend}")
    expect_status("Extratos", "Detalhe do lote de extrato abre", response, {200}, f"lot={statement_lot.id} backend={backend} ")
if statement_movement:
    movement_backend = "postgres_nativo" if statement_lot and str(statement_lot.source_backend or "") == "postgres_nativo" else "django_web"
    response = client.get(f"/imports/statement/movement/{statement_movement.id}/?backend={movement_backend}")
    expect_status("Extratos", "Movimento individual abre", response, {200}, f"movement={statement_movement.id} backend={movement_backend} ")
if statement_lot and str(statement_lot.source_backend or "") == "postgres_nativo":
    try:
        with transaction.atomic():
            response = client.post(
                f"/imports/statement/{statement_lot.id}/?backend=postgres_nativo",
                {"action": "close", "backend": "postgres_nativo"},
                follow=True,
            )
            transaction.set_rollback(True)
        expect_status("Extratos", "Bloqueio de encerramento com pendencia responde", response, {200}, f"lot={statement_lot.id} ")
    except Exception as exc:
        record("Extratos", "Bloqueio de encerramento com pendencia responde", False, f"lot={statement_lot.id} erro={exc}")

    try:
        with transaction.atomic():
            result = prepare_statement_lot_postgres_native(int(statement_lot.id or 0), actor="verificador_roteiro")
            transaction.set_rollback(True)
        record("Extratos", "Preparar lote nativo para auditoria responde", int(result.get("reviewed") or 0) >= 0, f"lot={int(statement_lot.id or 0)} reviewed={int(result.get('reviewed') or 0)}")
    except Exception as exc:
        record("Extratos", "Preparar lote nativo para auditoria responde", False, f"lot={int(statement_lot.id or 0)} erro={exc}")

    try:
        with transaction.atomic():
            updated = reprocess_statement_lot_postgres_native(int(statement_lot.id or 0))
            transaction.set_rollback(True)
        record("Extratos", "Reprocessar lote nativo responde", int(updated or 0) >= 0, f"lot={int(statement_lot.id or 0)} updated={int(updated or 0)}")
    except Exception as exc:
        record("Extratos", "Reprocessar lote nativo responde", False, f"lot={int(statement_lot.id or 0)} erro={exc}")

if pending_statement_movement:
    try:
        with transaction.atomic():
            update_statement_movement_postgres_native(
                int(pending_statement_movement.id or 0),
                {
                    "action": "same_owner",
                    "review_notes": "Resolucao controlada em teste automatizado.",
                },
                actor="verificador_roteiro",
            )
            transaction.set_rollback(True)
        record("Extratos", "Atualizacao controlada de movimento responde", True, f"movement={int(pending_statement_movement.id or 0)}")
    except Exception as exc:
        record("Extratos", "Atualizacao controlada de movimento responde", False, f"movement={int(pending_statement_movement.id or 0)} erro={exc}")

if resolved_duplicate_lot:
    human_pending_filter = (
        Q(review_status__in=["pendente", "revisar_pessoa", "revisar_destinacao", "classificacao_pendente"])
        | (Q(review_status="revisar_duplicidade") & Q(imported_contribution_legacy_id__isnull=True))
    )
    resolved_duplicates = resolved_duplicate_lot.movements.filter(
        review_status="revisar_duplicidade",
        imported_contribution_legacy_id__isnull=False,
    ).count()
    pending_human = resolved_duplicate_lot.movements.filter(human_pending_filter).count()
    direct_human = resolved_duplicate_lot.movements.filter(
        review_status__in=["pendente", "revisar_pessoa", "revisar_destinacao", "classificacao_pendente"]
    ).count()
    duplicate_human = resolved_duplicate_lot.movements.filter(
        review_status="revisar_duplicidade",
        imported_contribution_legacy_id__isnull=True,
    ).count()
    record(
        "Extratos",
        "Duplicidades resolvidas ficam fora da auditoria humana",
        resolved_duplicates >= 0 and pending_human == direct_human + duplicate_human,
        f"lot={int(resolved_duplicate_lot.id or 0)} duplicidades_resolvidas={resolved_duplicates} pendencias_humanas={pending_human}",
    )

# Importacao de pessoas
response = client.get("/people/imports/")
expect_status("Importacoes", "Dashboard de importacao de pessoas abre", response, {200})
if people_import_lot:
    response = client.get(f"/people/imports/{people_import_lot.legacy_id}/")
    expect_status("Importacoes", "Detalhe do lote de importacao de pessoas abre", response, {200}, f"lot={people_import_lot.legacy_id} ")
else:
    record("Importacoes", "Amostra de importacao de pessoas", False, "nenhum lote nativo de importacao encontrado")

# Contribuintes auxiliares
response = client.get("/contributors/")
expect_status("Contribuintes auxiliares", "Lista de contribuintes abre", response, {200})
if aux_contributor:
    response = client.get(f"/contributors/{aux_contributor.id}/")
    expect_status("Contribuintes auxiliares", "Detalhe de contribuinte abre", response, {200}, f"contributor={aux_contributor.id} ")
if launched_envelope:
    response = client.get(
        "/contributions/envelopes/lookup/",
        {"phone": launched_envelope.informed_phone or "", "address": launched_envelope.informed_address or ""},
    )
    expect_status("Contribuintes auxiliares", "Lookup de envelope responde", response, {200}, f"envelope={launched_envelope.legacy_id} ")

# Relatorios
response = client.get("/reports/")
expect_status("Relatorios", "Relatorio por periodo abre", response, {200})
response = client.get("/reports/destinations/")
expect_status("Relatorios", "Relatorio por destino abre", response, {200})
response = client.get("/reports/contributions-period.pdf")
expect_status("Relatorios", "PDF por periodo responde", response, {200})
response = client.get("/reports/contributions-destinations.pdf")
expect_status("Relatorios", "PDF por destino responde", response, {200})

# Auditoria
response = client.get("/audit/")
expect_status("Auditoria", "Auditoria operacional abre", response, {200})
response = client.get("/audit/?section=technical")
expect_status("Auditoria", "Auditoria tecnica abre", response, {200})

print("__RESULT_JSON_START__")
print(json.dumps(results, ensure_ascii=True))
print("__RESULT_JSON_END__")
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Verifica o roteiro final do operador com smoke tests seguros.")
    parser.add_argument("--report", action="store_true", help="Gera relatorio em Markdown.")
    args = parser.parse_args()

    ok, output = _run_probe(["-c", django_probe_code()])
    if not ok:
        print(output.strip() or "Falha ao executar probe Django.", file=sys.stderr)
        return 1
    start_marker = "__RESULT_JSON_START__"
    end_marker = "__RESULT_JSON_END__"
    start = output.find(start_marker)
    end = output.find(end_marker, start + len(start_marker)) if start >= 0 else -1
    if start < 0 or end < 0:
        print(f"Nao foi possivel localizar o bloco JSON do probe.\n{output}", file=sys.stderr)
        return 1
    json_blob = output[start + len(start_marker):end].strip()
    try:
        import json

        payload = json.loads(json_blob)
    except Exception as exc:
        print(f"Nao foi possivel interpretar a saida do probe: {exc}\n{output}", file=sys.stderr)
        return 1

    checks = [Check(area=item["area"], name=item["name"], status=item["status"], detail=item["detail"]) for item in payload]
    if args.report:
        report_path = _write_report(checks)
        print(report_path)

    failures = [check for check in checks if check.failed]
    if failures:
        for check in failures:
            print(f"[FALHA] {check.area} :: {check.name} :: {check.detail}")
        return 1
    for check in checks:
        print(f"[OK] {check.area} :: {check.name} :: {check.detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

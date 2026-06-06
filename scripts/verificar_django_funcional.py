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


def _prefer_local_postgres_socket(env: dict[str, str]) -> dict[str, str]:
    host = str(env.get("POWER_CHURCH_POSTGRES_HOST") or "").strip()
    port = str(env.get("POWER_CHURCH_POSTGRES_PORT") or "5432").strip() or "5432"
    socket_path = Path(f"/tmp/.s.PGSQL.{port}")
    if host in {"127.0.0.1", "localhost"} and socket_path.exists():
        env["POWER_CHURCH_POSTGRES_HOST"] = "/tmp"
    return env


@dataclass
class Check:
    name: str
    status: str
    detail: str

    @property
    def failed(self) -> bool:
        return self.status == "FALHA"


def run_with_venv(args: list[str], db_path: Path) -> tuple[bool, str]:
    if not DJANGO_VENV_PYTHON.exists():
        return False, f"Python da venv Django nao encontrado: {DJANGO_VENV_PYTHON}"
    env = dict(os.environ)
    env_file = Path(env.get("POWER_CHURCH_ENV_FILE") or DEFAULT_ENV_FILE)
    if env_file.exists():
        for raw_line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
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
            if value.startswith(("\"", "'")) and value.endswith(("\"", "'")) and len(value) >= 2:
                value = value[1:-1]
            env[key] = value
    env = _prefer_local_postgres_socket(env)
    env.setdefault("PYTHONPYCACHEPREFIX", "/private/tmp/pycache_powerchurch")
    env["POWER_CHURCH_LEGACY_DB_PATH"] = str(db_path)
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
import os
import re
import sqlite3
import sys
from urllib.parse import quote
from pathlib import Path

root = Path.cwd()
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "power_church_django"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "power_church_site.settings")

import django
django.setup()

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client

from power_church_django.services.access_control import access_control_snapshot
from power_church_django.services.django_audit import list_system_email_events
from power_church_django.services.legacy import connect_legacy, contribution_destination_report, contribution_report, dashboard_summary, family_registry_dashboard, list_contributions, list_contributors, list_envelopes, list_people, list_secure_people_trash, operational_audit
from power_church_django.services.legacy_bank_write import _parse_upload_with_provider, _parsed_summary, compare_pdf_upload_providers
from power_church_django.services.legacy_write import connect_legacy_write, ensure_manual_contributor, _resolve_primary_envelope_identity
from power_church_django.services.receipt_delivery import consolidated_receipt_campaign_summary
from power_church_core.normalization import contribution_report_identity

if "testserver" not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append("testserver")
if int(getattr(settings, "DATA_UPLOAD_MAX_NUMBER_FILES", 0) or 0) < 1000:
    raise AssertionError("Django esta com limite baixo para upload multiplo de envelopes")
if not getattr(settings, "SESSION_EXPIRE_AT_BROWSER_CLOSE", False):
    raise AssertionError("sessao do Django deveria expirar ao fechar o navegador")
if int(getattr(settings, "POWER_CHURCH_SESSION_IDLE_SECONDS", 0) or 0) <= 0:
    raise AssertionError("timeout de inatividade da sessao nao foi configurado")

summary = dashboard_summary()
if summary["people_total"] <= 0:
    raise AssertionError("dashboard nao encontrou pessoas")
if summary["contributions_count"] <= 0:
    raise AssertionError("dashboard nao encontrou contribuicoes")
people_data = list_people()
if int(people_data["shown"] or 0) != int(people_data["total"] or 0):
    raise AssertionError(
        f"lista de pessoas ainda esta limitada: {people_data['shown']} de {people_data['total']}"
    )
contributors_data = list_contributors()
if int(contributors_data["shown"] or 0) != int(contributors_data["total"] or 0):
    raise AssertionError(
        f"lista de contribuintes ainda esta limitada: {contributors_data['shown']} de {contributors_data['total']}"
    )
all_contributions_data = list_contributions()
if int(all_contributions_data["shown"] or 0) != int(all_contributions_data["total"] or 0):
    raise AssertionError(
        f"lista de contribuicoes ainda esta limitada: {all_contributions_data['shown']} de {all_contributions_data['total']}"
    )
all_envelopes_data = list_envelopes()
if int(all_envelopes_data["shown"] or 0) != int(all_envelopes_data["total"] or 0):
    raise AssertionError(
        f"lista de envelopes ainda esta limitada: {all_envelopes_data['shown']} de {all_envelopes_data['total']}"
    )
with connect_legacy_write() as conn:
    conn.execute("BEGIN")
    contributor_id = ensure_manual_contributor(
        conn,
        1,
        "Teste Integracao Frequentador Envelope",
        "",
        source="verificador_django_funcional",
    )
    payload = {
        "participante_principal_ref": f"Contribuinte #{contributor_id} · Teste Integracao Frequentador Envelope",
        "telefone_informado": "(21) 99888-7766",
        "endereco_informado": "Rua de Teste 123, Niteroi",
        "nome_informado": "Teste Integracao Frequentador Envelope",
    }
    identity = _resolve_primary_envelope_identity(conn, 1, payload, actor="verificador_django_funcional")
    person_id = int(identity["person_id"] or 0)
    contributor = conn.execute("SELECT pessoa_id FROM contribuintes WHERE id = ?", (contributor_id,)).fetchone()
    person = conn.execute(
        "SELECT status, telefone_principal, whatsapp_principal FROM pessoas WHERE id = ?",
        (person_id,),
    ).fetchone()
    contact_types = {
        (row["tipo"], row["valor"])
        for row in conn.execute("SELECT tipo, valor FROM pessoa_contatos WHERE pessoa_id = ?", (person_id,)).fetchall()
    }
    identifiers = {
        (row["tipo"], row["valor"], int(row["pessoa_id"] or 0))
        for row in conn.execute(
            "SELECT tipo, valor, pessoa_id FROM contribuintes_identificadores WHERE contribuinte_id = ?",
            (contributor_id,),
        ).fetchall()
    }
    address_row = conn.execute(
        "SELECT logradouro FROM pessoa_enderecos WHERE pessoa_id = ? ORDER BY id DESC LIMIT 1",
        (person_id,),
    ).fetchone()
    if int(identity["contributor_id"] or 0) != int(contributor_id):
        raise AssertionError("identificacao principal do envelope nao preservou o contribuinte auxiliar")
    if not contributor or int(contributor["pessoa_id"] or 0) != person_id:
        raise AssertionError("contribuinte auxiliar com telefone/endereco nao foi vinculado ao frequentador criado")
    if not person or str(person["status"] or "") != "frequentador":
        raise AssertionError("contribuinte auxiliar com telefone/endereco nao virou frequentador automaticamente")
    if str(person["telefone_principal"] or "") != "(21) 99888-7766" or str(person["whatsapp_principal"] or "") != "(21) 99888-7766":
        raise AssertionError("telefone do frequentador criado a partir do envelope nao foi aplicado na ficha")
    if ("telefone", "(21) 99888-7766") not in contact_types or ("whatsapp", "(21) 99888-7766") not in contact_types:
        raise AssertionError("contatos do frequentador criado pelo envelope nao foram persistidos")
    if not address_row or str(address_row["logradouro"] or "") != "Rua de Teste 123, Niteroi":
        raise AssertionError("endereco do frequentador criado pelo envelope nao foi persistido")
    if ("telefone", "21998887766", person_id) not in identifiers or ("endereco", "Rua de Teste 123, Niteroi", person_id) not in identifiers:
        raise AssertionError("identificadores do contribuinte auxiliar nao foram guardados para busca futura")
    conn.rollback()
families_data = family_registry_dashboard(section="organized")
if int(families_data["organized"]["shown"] or 0) != int(families_data["organized"]["total"] or 0):
    raise AssertionError(
        f"lista de familias organizadas ainda esta limitada: {families_data['organized']['shown']} de {families_data['organized']['total']}"
    )
families_audit_data = family_registry_dashboard(section="audit", mode="all")
expected_audit_groups = int(families_audit_data["audit"]["summary"]["filtered_automatic_groups"] or 0) + int(families_audit_data["audit"]["summary"]["filtered_hypothesis_groups"] or 0)
if int(families_audit_data["audit"]["summary"]["shown_groups"] or 0) != expected_audit_groups:
    raise AssertionError(
        f"fila de auditoria das familias ainda esta limitada: {families_audit_data['audit']['summary']['shown_groups']} de {expected_audit_groups}"
    )
if expected_audit_groups > 0 and not families_audit_data["audit"].get("smart_summary"):
    raise AssertionError("auditoria de familias ficou sem resumo inteligente")
broad_families_data = family_registry_dashboard(section="broad")
if int(broad_families_data["broad"]["shown"] or 0) != int(broad_families_data["broad"]["total"] or 0):
    raise AssertionError("criterio amplo de familias ainda esta limitado")
access = access_control_snapshot()
if not access["installed"]:
    raise AssertionError("permissoes Power Church nao instaladas no Django")
if access["group_count"] < 5:
    raise AssertionError("grupos padrao do Django nao instalados")
operational_snapshot = operational_audit(page_size=200)
if not operational_snapshot.get("smart_summary"):
    raise AssertionError("auditoria operacional ficou sem resumo inteligente")
contributor_link_snapshot = list_contributors(mode="recorrentes", tags=["integracao"], section="family_links", limit=10000)
if contributor_link_snapshot["family_links"] and not contributor_link_snapshot.get("family_links_smart_summary"):
    raise AssertionError("integracao de contribuintes ficou sem resumo inteligente")
email_snapshot = list_system_email_events(page_size=120)
if email_snapshot["items"] and not email_snapshot.get("smart_summary"):
    raise AssertionError("auditoria de e-mails ficou sem resumo inteligente")
campaign_snapshot = consolidated_receipt_campaign_summary(cutoff_date="2026-05-31")
campaign_summary = campaign_snapshot.get("summary") or {}
if int(campaign_summary.get("total_people") or 0) <= 0:
    raise AssertionError("campanha de recibos consolidados nao encontrou pessoas com e-mail e contribuicao")
if int(campaign_summary.get("ready_to_queue") or 0) <= 0:
    raise AssertionError("campanha de recibos consolidados nao deixou pessoas prontas para fila")

with connect_legacy() as conn:
    try:
        conn.execute("CREATE TABLE codex_readonly_probe (id INTEGER)")
    except sqlite3.OperationalError as exc:
        if "readonly" not in str(exc).lower() and "read-only" not in str(exc).lower() and "query only" not in str(exc).lower():
            raise
        print("legacy_readonly=OK")
    else:
        raise AssertionError("conexao legada permitiu escrita")

client = Client()
anonymous_client = Client()
anonymous_root = anonymous_client.get("/", follow=False)
if anonymous_root.status_code not in {301, 302}:
    raise AssertionError(f"dashboard raiz sem autenticacao deveria redirecionar para login, mas retornou {anonymous_root.status_code}")
redirect_target = str(anonymous_root.headers.get("Location") or "")
if "/accounts/login/" not in redirect_target:
    raise AssertionError(f"dashboard raiz redirecionou para destino inesperado: {redirect_target}")
for protected_path in (
    "/people/",
    "/contributors/",
    "/contributions/",
    "/receipts/",
    "/imports/",
    "/reports/",
    "/audit/",
):
    anonymous_response = anonymous_client.get(protected_path, follow=False)
    if anonymous_response.status_code not in {301, 302}:
        raise AssertionError(
            f"rota protegida {protected_path} deveria redirecionar para login, mas retornou {anonymous_response.status_code}"
        )
    protected_redirect = str(anonymous_response.headers.get("Location") or "")
    if "/accounts/login/" not in protected_redirect:
        raise AssertionError(
            f"rota protegida {protected_path} redirecionou para destino inesperado: {protected_redirect}"
        )
user_model = get_user_model()
probe_user = (
    user_model.objects.filter(is_active=True, is_superuser=True).order_by("id").first()
    or user_model.objects.filter(is_active=True, is_staff=True).order_by("id").first()
    or user_model.objects.filter(is_active=True).order_by("id").first()
)
if probe_user is None:
    raise AssertionError("nao ha usuario ativo para autenticar a bateria funcional do Django")
client.force_login(probe_user)
authenticated_login = client.get("/accounts/login/", follow=False)
if authenticated_login.status_code != 200:
    raise AssertionError(
        f"usuario autenticado deveria conseguir ver a tela publica de login, mas recebeu status {authenticated_login.status_code}"
    )
authenticated_login_body = authenticated_login.content.decode("utf-8", errors="ignore")
if any(marker in authenticated_login_body for marker in ("Pessoas", "Contribuicoes", "Auditoria")):
    raise AssertionError(
        "tela publica de login nao deveria exibir navegacao funcional do sistema"
    )
cached_probe = client.get("/people/", follow=False)
cache_control = str(cached_probe.headers.get("Cache-Control") or "")
if "no-store" not in cache_control:
    raise AssertionError(f"pagina autenticada deveria sair sem cache, mas retornou Cache-Control={cache_control!r}")
authenticated_relogin = client.get("/accounts/relogin/", follow=False)
if authenticated_relogin.status_code not in {301, 302}:
    raise AssertionError(
        f"rota /accounts/relogin/ deveria redirecionar para login, mas recebeu status {authenticated_relogin.status_code}"
    )
authenticated_relogin_target = str(authenticated_relogin.headers.get("Location") or "")
if "/accounts/login/" not in authenticated_relogin_target:
    raise AssertionError(
        f"rota /accounts/relogin/ redirecionou para destino inesperado: {authenticated_relogin_target}"
    )
post_relogin_response = client.get("/people/", follow=False)
if post_relogin_response.status_code not in {301, 302}:
    raise AssertionError(
        f"rota protegida deveria exigir novo login apos relogin, mas retornou {post_relogin_response.status_code}"
    )
post_relogin_target = str(post_relogin_response.headers.get("Location") or "")
if "/accounts/login/" not in post_relogin_target:
    raise AssertionError(
        f"rota protegida nao exigiu login apos relogin: {post_relogin_target}"
    )
client.force_login(probe_user)
with connect_legacy() as conn:
    person_id = conn.execute("SELECT id FROM pessoas WHERE ativo = 1 ORDER BY id LIMIT 1").fetchone()[0]
    cpf_person = conn.execute(
        "SELECT id, cpf FROM pessoas WHERE ativo = 1 AND COALESCE(cpf, '') <> '' ORDER BY id LIMIT 1"
    ).fetchone()
    inactive_person = conn.execute(
        "SELECT id, nome FROM pessoas WHERE ativo = 0 ORDER BY atualizado_em DESC, id DESC LIMIT 1"
    ).fetchone()
    contributor_id = conn.execute("SELECT id FROM contribuintes WHERE ativo = 1 ORDER BY id LIMIT 1").fetchone()[0]
    contribution_row = conn.execute(
        "SELECT id, pessoa_id FROM contribuicoes WHERE ativo = 1 AND pessoa_id IS NOT NULL ORDER BY id DESC LIMIT 1"
    ).fetchone()
    contribution_id = contribution_row[0]
    contribution_person_id = contribution_row[1]
    latest_competence = conn.execute(
        "SELECT competencia FROM contribuicoes "
        "WHERE ativo = 1 AND COALESCE(competencia, '') <> '' "
        "GROUP BY competencia "
        "ORDER BY MAX(COALESCE(competencia_ordem, 0)) DESC, competencia DESC "
        "LIMIT 1"
    ).fetchone()[0]
    statement_lot = conn.execute("SELECT id, nome_arquivo, caminho_arquivo, layout_codigo, observacoes, total_movimentos, total_valor FROM extrato_lotes ORDER BY id DESC LIMIT 1").fetchone()
    pix_lot = conn.execute("SELECT id, nome_arquivo, caminho_arquivo FROM pix_lotes ORDER BY id DESC LIMIT 1").fetchone()
    cent_rule = conn.execute("SELECT id FROM pix_centavo_regras ORDER BY codigo_centavos LIMIT 1").fetchone()
    statement_movement = conn.execute("SELECT id FROM extrato_movimentos ORDER BY id DESC LIMIT 1").fetchone()
    pix_movement = conn.execute("SELECT id FROM pix_movimentos ORDER BY id DESC LIMIT 1").fetchone()
    receipt_row = conn.execute("SELECT id FROM recibos ORDER BY id DESC LIMIT 1").fetchone()
    envelope_table = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'envelopes'").fetchone()
    envelope_row = None
    envelope_lot_row = None
    pending_envelope_row = None
    launched_envelope_row = None
    if envelope_table:
        envelope_row = conn.execute("SELECT id, caminho_imagem FROM envelopes WHERE ativo = 1 ORDER BY id DESC LIMIT 1").fetchone()
        launched_envelope_row = conn.execute("SELECT id FROM envelopes WHERE ativo = 1 AND status = 'lancado' ORDER BY id DESC LIMIT 1").fetchone()
        envelope_lot_row = conn.execute("SELECT id FROM envelope_lotes ORDER BY id DESC LIMIT 1").fetchone()
        pending_envelope_row = conn.execute(
            "SELECT id FROM envelopes WHERE ativo = 1 AND status = 'aguardando_digitacao' ORDER BY lote_id DESC, id ASC LIMIT 1"
        ).fetchone()
    people_import_lot = conn.execute(
        "SELECT id FROM import_lotes WHERE tipo_importacao IN ('pessoas_membros', 'pessoas_complementar_incremental') ORDER BY id DESC LIMIT 1"
    ).fetchone()

if statement_lot:
    statement_pdf = Path(str(statement_lot["caminho_arquivo"]))
    if not statement_pdf.exists():
        raise AssertionError(f"PDF do lote de extrato nao encontrado: {statement_pdf}")
    statement_payload = statement_pdf.read_bytes()
    statement_note = str(statement_lot["observacoes"] or "").lower()
    portable_direct = (
        "motor pdf usado na importacao django: pymupdf" in statement_note
        and "comparacao swift/pymupdf aprovada" not in statement_note
    )
    if portable_direct:
        parsed_statement = _parse_upload_with_provider(
            "pymupdf",
            filename=str(statement_lot["nome_arquivo"]),
            payload=statement_payload,
            import_kind="statement",
            layout_code=str(statement_lot["layout_codigo"]),
        )
        parsed_summary = _parsed_summary(
            parsed_statement,
            import_kind="statement",
            requested_layout_code=str(statement_lot["layout_codigo"]),
        )
        db_count = int(statement_lot["total_movimentos"] or 0)
        db_total_cents = int(round(float(statement_lot["total_valor"] or 0) * 100))
        if int(parsed_summary["count"] or 0) != db_count:
            raise AssertionError(
                f"PyMuPDF reprocessou quantidade diferente do lote {statement_lot['id']}: "
                f"pdf={parsed_summary['count']} banco={db_count}"
            )
        if int(parsed_summary["total_cents"] or 0) != db_total_cents:
            raise AssertionError(
                f"PyMuPDF reprocessou total diferente do lote {statement_lot['id']}: "
                f"pdf={parsed_summary['total_cents']} banco={db_total_cents}"
            )
        print(f"pdf_provider_parse=statement:pymupdf:OK:{parsed_summary['count']}")
    else:
        statement_compare = compare_pdf_upload_providers(
            str(statement_lot["nome_arquivo"]),
            statement_payload,
            import_kind="statement",
            layout_code=str(statement_lot["layout_codigo"]),
        )
        if not statement_compare["ok"]:
            raise AssertionError(f"comparacao Swift/PyMuPDF divergente em extrato: {statement_compare['difference']}")
        print("pdf_provider_compare=statement:OK")
if pix_lot:
    pix_pdf = Path(str(pix_lot["caminho_arquivo"]))
    if not pix_pdf.exists():
        raise AssertionError(f"PDF do lote PIX nao encontrado: {pix_pdf}")
    pix_compare = compare_pdf_upload_providers(
        str(pix_lot["nome_arquivo"]),
        pix_pdf.read_bytes(),
        import_kind="pix_sicoob",
    )
    if not pix_compare["ok"]:
        raise AssertionError(f"comparacao Swift/PyMuPDF divergente em PIX: {pix_compare['difference']}")
    print("pdf_provider_compare=pix:OK")

paths = [
    "/",
    "/branding/logo",
    "/people/",
    "/people/?status=membro_ativo&city=Niteroi",
    "/people/new/",
    "/people/families/",
    "/people/families/?section=broad",
    "/people/families/?section=audit",
    "/people/families/?section=extended",
    f"/people/{person_id}/",
    f"/people/{person_id}/edit/",
    "/people/?q=Maria",
    "/people/export/?format=csv",
    "/people/export/?format=xlsx",
    "/people/export/?preset=familias_votacao&column=nome&column=familia_domiciliar&column=familia_tem_contribuinte&format=csv",
    "/people/imports/",
    "/contributors/",
    "/contributors/?tag=pf&section=contributors",
    "/contributors/?tag=pj&section=contributors",
    "/contributors/?tag=vinculado&section=contributors",
    "/contributors/?tag=sem_vinculo&section=contributors",
    "/contributors/?mode=recorrentes&tag=integracao&section=family_links",
    "/contributors/?mode=recorrentes&tag=familia_sugerida&section=family_groups",
    f"/contributors/{contributor_id}/",
    "/contributions/",
    "/contributions/manual/",
    "/contributions/envelopes/",
    "/contributions/envelopes/new/",
    "/contributions/envelopes/lookup/?phone=21999999999&address=Rua%20Teste%20Django%20100",
    "/contributions/envelopes/lots/new/",
    f"/contributions/{contribution_id}/",
    f"/contributions/{contribution_id}/split/",
    f"/contributions/new/?person_id={contribution_person_id}",
    f"/contributions/statements/{contribution_person_id}/",
    f"/contributions/?competencia={quote(latest_competence)}",
    "/receipts/",
    "/receipts/queue/",
    f"/receipts/?selected_person_id={contribution_person_id}",
    "/imports/",
    "/imports/rules/",
    "/reports/",
    f"/reports/?competencia={quote(latest_competence)}",
    f"/reports/contributions-period.pdf?competencia={quote(latest_competence)}&inline=1",
    "/reports/destinations/",
    f"/reports/destinations/?competencia={quote(latest_competence)}",
    f"/reports/contributions-destinations.pdf?competencia={quote(latest_competence)}&inline=1",
    "/audit/",
    "/audit/?modo=django",
    "/audit/?modo=emails",
    "/accounts/",
    "/accounts/login/",
]
if statement_lot:
    paths.append(f"/imports/statement/{statement_lot[0]}/")
    paths.append(f"/imports/statement/{statement_lot[0]}/?status=pendencias")
if pix_lot:
    paths.append(f"/imports/pix/{pix_lot[0]}/")
    paths.append(f"/imports/pix/{pix_lot[0]}/?status=pendencias")
if cent_rule:
    paths.append(f"/imports/rules/?edit_rule_id={cent_rule[0]}")
if statement_movement:
    paths.append(f"/imports/statement/movement/{statement_movement[0]}/")
if pix_movement:
    paths.append(f"/imports/pix/movement/{pix_movement[0]}/")
if receipt_row:
    paths.append(f"/receipts/{receipt_row[0]}/")
    paths.append(f"/receipts/{receipt_row[0]}/pdf/")
if envelope_row:
    paths.append(f"/contributions/envelopes/{envelope_row[0]}/")
    if envelope_row["caminho_imagem"]:
        paths.append(f"/contributions/envelopes/{envelope_row[0]}/image/")
if envelope_lot_row:
    paths.append(f"/contributions/envelopes/lots/{envelope_lot_row[0]}/")
if pending_envelope_row:
    paths.append(f"/contributions/envelopes/{pending_envelope_row[0]}/launch/")
if launched_envelope_row:
    paths.append(f"/contributions/envelopes/{launched_envelope_row[0]}/edit/")
if people_import_lot:
    paths.append(f"/people/imports/{people_import_lot[0]}/")
for path in paths:
    response = client.get(path)
    if getattr(response, "streaming", False):
        body = b"".join(response.streaming_content)
    else:
        body = response.content
    print(f"{path} status={response.status_code} bytes={len(body)}")
    if response.status_code != 200:
        raise AssertionError(f"{path} retornou {response.status_code}")
    if path == "/branding/logo":
        if not response["Content-Type"].startswith("image/jpeg"):
            raise AssertionError("/branding/logo nao retornou JPEG")
        if len(body) < 1000:
            raise AssertionError("/branding/logo retornou imagem vazia ou pequena demais")
        continue
    if path.startswith("/people/export/"):
        if "format=csv" in path:
            if not response["Content-Type"].startswith("text/csv"):
                raise AssertionError("exportacao CSV de pessoas nao retornou text/csv")
            if b"Nome" not in body or len(body) < 200:
                raise AssertionError("exportacao CSV de pessoas vazia ou sem cabecalho")
            if "familia_domiciliar" in path and b"Familia domiciliar" not in body:
                raise AssertionError("exportacao dinamica CSV sem coluna familiar esperada")
        if "format=xlsx" in path:
            if "spreadsheet" not in response["Content-Type"]:
                raise AssertionError("exportacao XLSX de pessoas nao retornou planilha")
            if not body.startswith(b"PK") or len(body) < 2000:
                raise AssertionError("exportacao XLSX de pessoas invalida")
        continue
    if path.startswith("/reports/contributions-period.pdf"):
        if not response["Content-Type"].startswith("application/pdf"):
            raise AssertionError("PDF de contribuicoes por periodo nao retornou application/pdf")
        if not body.startswith(b"%PDF") or len(body) < 1200:
            raise AssertionError("PDF de contribuicoes por periodo invalido ou pequeno demais")
        continue
    if path.startswith("/reports/contributions-destinations.pdf"):
        if not response["Content-Type"].startswith("application/pdf"):
            raise AssertionError("PDF de contribuicoes por destino nao retornou application/pdf")
        if not body.startswith(b"%PDF") or len(body) < 1200:
            raise AssertionError("PDF de contribuicoes por destino invalido ou pequeno demais")
        continue
    if re.match(r"^/receipts/\d+/pdf/$", path):
        if not response["Content-Type"].startswith("application/pdf"):
            raise AssertionError("PDF de recibo nao retornou application/pdf")
        if not body.startswith(b"%PDF") or len(body) < 1200:
            raise AssertionError("PDF de recibo invalido ou pequeno demais")
        continue
    if path.startswith("/contributions/envelopes/") and path.endswith("/image/"):
        if response["Content-Type"].startswith("text/html"):
            raise AssertionError("imagem de envelope retornou HTML em vez do arquivo arquivado")
        if len(body) < 10:
            raise AssertionError("imagem de envelope retornou arquivo vazio")
        continue
    content = body.decode("utf-8", errors="replace")
    if path.startswith("/contributions/envelopes/lookup/"):
        lowered = content.lower()
        for snippet in ['"ok": true', '"phone_matches"', '"address_matches"']:
            if snippet not in lowered:
                raise AssertionError(f"lookup de envelope nao respondeu JSON esperado: {snippet}")
        continue
    if "/branding/logo" not in content or "Navegacao principal" not in content:
        raise AssertionError(f"{path} nao renderizou o layout Django")
    if path == "/" and "/branding/logo" not in content:
        raise AssertionError("dashboard nao incluiu a logo")
    if path == "/" and "/contributions/envelopes/" not in content:
        raise AssertionError("dashboard nao incluiu atalho direto para envelopes digitalizados")
    if path == "/" and "/contributions/envelopes/new/" not in content:
        raise AssertionError("dashboard nao incluiu botao direto para subir envelope")
    if path == "/":
        for snippet in ["Domicilios da base", "Familias domiciliares", "Unipessoais", "Criterio amplo", "Quorum em Niteroi"]:
            if snippet not in content:
                raise AssertionError(f"dashboard sem indicador estrategico: {snippet}")
    if path == "/people/":
        for snippet in [
            "Exportar XLSX",
            "Exportar CSV",
            "Exportacao dinamica de pessoas",
            "Cadastro basico",
            "Familias e votacao",
            "Cidade",
            "Selecionar tudo",
            "Imprimir esta lista",
            f"Mostrando {people_data['total']} de {people_data['total']} registros",
        ]:
            if snippet not in content:
                raise AssertionError(f"lista de pessoas sem exportacao import-export: {snippet}")
        if inactive_person and f"/people/{inactive_person['id']}/" in content:
            raise AssertionError("lista operacional de pessoas exibiu ficha excluida/inativa")
    if path == "/people/?status=membro_ativo&city=Niteroi":
        filtered_people = list_people(status="membro_ativo", city="Niteroi")
        expected = f"Mostrando {filtered_people['total']} de {filtered_people['total']} registros"
        for snippet in ["Cidade", "Niteroi", expected]:
            if snippet not in content:
                raise AssertionError(f"lista de quorum sem filtro esperado: {snippet}")
    if path == "/people/new/" or path == f"/people/{person_id}/edit/":
        for snippet in [
            'enctype="multipart/form-data"',
            'name="foto"',
            'name="sexo"',
            'name="estado_civil"',
            "data-person-field-validator",
            "data-cep-lookup",
            "viacep.com.br",
            'data-validate-field="cpf"',
            'data-validate-field="email_principal"',
            "Nome social (apelido como e conhecido)",
            "Foto da pessoa",
            "Upload da foto",
        ]:
            if snippet not in content:
                raise AssertionError(f"formulario de pessoa sem upload de foto: {snippet}")
    if path == f"/people/{person_id}/":
        if "person-photo-large" not in content:
            raise AssertionError("ficha de pessoa nao usa foto ampliada para reconhecimento visual")
    if path == "/audit/?modo=django":
        for snippet in ["Rastreabilidade Django", "Eventos Django"]:
            if snippet not in content:
                raise AssertionError(f"auditoria Django sem trecho esperado: {snippet}")
    if path == "/audit/":
        for snippet in ["Mesclar fichas do cadastro", "Buscar ficha principal", "Buscar ficha duplicada"]:
            if snippet not in content:
                raise AssertionError(f"auditoria do cadastro sem entrada de merge: {snippet}")
    if path == "/audit/?modo=emails":
        for snippet in ["Relatorio de e-mails enviados", "Consolida recibos e extratos enviados pelo sistema", "Pessoa", "Conteudo", "Destino", "E-mails do sistema", "Reenviar", "Classificacao"]:
            if snippet not in content:
                raise AssertionError(f"auditoria de e-mails sem trecho esperado: {snippet}")
    if path == "/people/families/":
        for snippet in [
            "Familias domiciliares",
            "Todos os domicilios",
            "Nome automatico:",
            "Cabeca da familia",
            "Salvar identidade familiar",
            "Fila de auditoria",
            "Familias estendidas",
            "Situacao do domicilio",
            "Contribuicao na familia",
            "Unipessoal",
        ]:
            if snippet not in content:
                raise AssertionError(f"familias domiciliares Django sem trecho esperado: {snippet}")
    if path == "/people/families/?section=broad":
        for snippet in [
            "Criterio amplo",
            "Padrao inteligente do criterio amplo",
            "Consolidar familias selecionadas",
            "Consolidacao manual",
        ]:
            if snippet not in content:
                raise AssertionError(f"criterio amplo de familias sem trecho esperado: {snippet}")
    if path == f"/people/{person_id}/":
        for snippet in ["Mesclar ficha", "Dados cadastrais", "Contribuintes vinculados"]:
            if snippet not in content:
                raise AssertionError(f"ficha da pessoa sem merge esperado: {snippet}")
    if path == f"/people/{person_id}/merge/":
        for snippet in ["Mesclar ficha em", "Buscar ficha duplicada", "Justificativa da mesclagem"]:
            if snippet not in content:
                raise AssertionError(f"tela de merge sem trecho esperado: {snippet}")
    if path == "/people/families/?section=audit":
        required = [
            "Fila de auditoria",
        ]
        expected_groups = int(families_audit_data["audit"]["summary"]["shown_groups"] or 0)
        if expected_groups > 0:
            required.extend(
                [
                    "Padrao inteligente da auditoria",
                    "Aplicacao em lote",
                    "Hipoteses para auditoria",
                    "Ignorar sugestoes selecionadas",
                    "Criar familias selecionadas",
                    "Categoria inteligente",
                    "Acao sugerida:",
                ]
            )
        for snippet in required:
            if snippet not in content:
                raise AssertionError(f"auditoria de familias Django sem trecho esperado: {snippet}")
    if path == "/people/families/?section=extended":
        for snippet in [
            "Familias estendidas",
            "Nucleo domiciliar",
            "Financeiro",
            "Situacao",
        ]:
            if snippet not in content:
                raise AssertionError(f"familias estendidas Django sem trecho esperado: {snippet}")
    path_parts = [part for part in path.strip("/").split("/") if part]
    if len(path_parts) == 2 and path_parts[0] == "people" and path_parts[1].isdigit():
        for snippet in ["Familia domiciliar por endereco", "Sincronizar familias domiciliares por endereco", "Relacoes familiares ativas", "relationship-card", "Desassociar da familia domiciliar", "Ignorar sugestao", "data-person-relationship-search", "Registrar relacao familiar"]:
            if snippet not in content:
                raise AssertionError(f"ficha de pessoa Django sem relacao familiar: {snippet}")
    if path == "/imports/":
        for snippet in ["Importar extrato bancario", "extrato bancario completo", "Criar lote", "SICOOB_CONTA_CORRENTE", "SICOOB_RECEBIMENTOS", "Motor de leitura PDF", "Comparar Swift x PyMuPDF", "Abrir lote", "Arquivo e periodo", "Financeiro", "Fila", "Status do lote"]:
            if snippet not in content:
                raise AssertionError(f"central de importacoes Django sem trecho esperado: {snippet}")
        if not re.search(r'<option value="compare_pymupdf"[^>]*selected', content):
            raise AssertionError("central de importacoes nao usa comparacao Swift x PyMuPDF como default")
        if "Regras de centavos" not in content:
            raise AssertionError("central de importacoes Django sem atalho para regras de centavos")
        if "pix_sicoob" in content or "Sicoob PIX historico" in content:
            raise AssertionError("central de importacoes manteve o caminho PIX isolado nesta versao")
    if path == "/contributors/":
        for snippet in ["compact-marker-grid", "compact-check"]:
            if snippet not in content:
                raise AssertionError(f"central de contribuintes sem marcadores compactos: {snippet}")
        data = list_contributors()
        order = [
            (0 if item.get("group_kind") == "nome" else 1, str(item.get("sort_key") or ""), int(item.get("id") or 0))
            for item in data["items"]
        ]
        if len(data["items"]) != int(data["total"] or 0):
            raise AssertionError(f"central de contribuintes carregou {len(data['items'])} de {data['total']}")
        if order != sorted(order):
            raise AssertionError("central de contribuintes nao esta em ordem alfabetica")
        bad_named_numbers = [
            item["nome"]
            for item in data["items"]
            if item.get("group_kind") == "nome" and str(item.get("nome") or "")[:1].isdigit()
        ]
        if bad_named_numbers:
            raise AssertionError(f"central de contribuintes manteve documento antes do nome: {bad_named_numbers[0]}")
    if path.startswith("/contributors/") and path != f"/contributors/{contributor_id}/":
        for snippet in ["Central estrategica", "Marcadores estrategicos"]:
            if snippet not in content:
                raise AssertionError(f"central de contribuintes Django sem trecho esperado: {snippet}")
        if "section=family_links" not in path and "section=family_groups" not in path and "Tabela principal de contribuintes" not in content:
            raise AssertionError("central de contribuintes Django sem tabela principal")
        if "tag=pf" in path:
            data = list_contributors(tags=["pf"], section="contributors", limit=10000)
            if data["total"] != data["summary"]["pf"]:
                raise AssertionError("filtro PF do Django nao bate contagem estrategica")
        if "tag=pj" in path:
            data = list_contributors(tags=["pj"], section="contributors", limit=10000)
            if data["total"] != data["summary"]["pj"]:
                raise AssertionError("filtro PJ do Django nao bate contagem estrategica")
        if "tag=vinculado" in path:
            data = list_contributors(tags=["vinculado"], section="contributors", limit=10000)
            if any(not item["pessoa_id"] for item in data["items"]):
                raise AssertionError("filtro vinculado retornou contribuinte sem pessoa")
        if "tag=sem_vinculo" in path:
            data = list_contributors(tags=["sem_vinculo"], section="contributors", limit=10000)
            if any(item["pessoa_id"] for item in data["items"]):
                raise AssertionError("filtro sem vinculo retornou contribuinte vinculado")
        if "section=family_links" in path and "Contribuintes recorrentes ligados a familias ja cadastradas" not in content:
            raise AssertionError("central de contribuintes sem painel de associacoes familiares")
        if "section=family_links" in path:
            data = list_contributors(mode="recorrentes", tags=["integracao"], section="family_links", limit=10000)
            if data["family_links"]:
                for snippet in ["Criar frequentador", "Vincular a esta pessoa", "Risco"]:
                    if snippet not in content:
                        raise AssertionError(f"central de contribuintes sem auditoria inteligente de integracao: {snippet}")
        if "section=family_groups" in path and "Blocos familiares sugeridos" not in content:
            raise AssertionError("central de contribuintes sem painel de blocos familiares")
    if path == "/contributions/":
        total = int(all_contributions_data["total"] or 0)
        required = [
            "Central de envelopes",
            "Lancamentos",
            "Envelopes",
            "Envelopes ativos",
            "Total lancado",
            "Lotes recentes",
            "Abrir lista completa",
            "Subir lote",
            "Subir envelope",
            "Imprimir esta lista filtrada",
            f"Mostrando {total} de {total} lancamentos",
        ]
        for snippet in required:
            if snippet not in content:
                raise AssertionError(f"contribuicoes Django sem central operativa esperada: {snippet}")
    if path.startswith("/imports/rules/"):
        required = ["Regras por centavos", "Mapa atual", "Salvar regra", "Conta / campanha", "Criar/usar tipo proprio da destinacao"]
        if "edit_rule_id=" in path:
            required.append("Editar regra")
        for snippet in required:
            if snippet not in content:
                raise AssertionError(f"regras de centavos Django sem trecho esperado: {snippet}")
    if path.startswith("/imports/statement/") and "/movement/" not in path:
        for snippet in ["Processamento do lote", "Reprocessar lote", "Encerrar lote", "Auditar pendencias", "lot-movements-table", "Banco/Pix", "CPF cadastro", "Auditar / validar", "Editar regras de centavos"]:
            if snippet not in content:
                raise AssertionError(f"lote de extrato Django sem acao operacional: {snippet}")
    if path.startswith("/imports/pix/") and "/movement/" not in path:
        for snippet in ["Processamento do lote", "Reprocessar lote", "Sincronizar financeiro", "Encerrar lote", "Auditar pendencias", "Editar regras de centavos"]:
            if snippet not in content:
                raise AssertionError(f"lote PIX Django sem acao operacional: {snippet}")
    if path.startswith("/imports/statement/movement/"):
        for snippet in ["Auditoria operacional", "Buscar pessoa em todo o cadastro", "Confirmar movimento", "Mesma titularidade / origem interna", "Ignorar movimento"]:
            if snippet not in content:
                raise AssertionError(f"movimento de extrato Django sem auditoria operacional: {snippet}")
    if path.startswith("/imports/pix/movement/"):
        for snippet in ["Auditoria operacional", "Buscar pessoa em todo o cadastro", "Confirmar movimento", "Associar documento mascarado", "Ignorar movimento"]:
            if snippet not in content:
                raise AssertionError(f"movimento PIX Django sem auditoria operacional: {snippet}")
    if path.startswith("/contributions/?competencia="):
        period_data = list_contributions(competencia=latest_competence, limit=5000)
        items = period_data["items"]
        total = int(period_data["total"] or 0)
        keys = [
            (0 if item.get("group_kind") == "nome" else 1, str(item.get("sort_key") or ""), str(item.get("data_raw") or ""), int(item.get("competencia_ordem") or 0), int(item.get("id") or 0))
            for item in items
        ]
        if len(items) != total:
            raise AssertionError(f"contribuicoes Django carregou {len(items)} de {total} no periodo")
        if keys != sorted(keys):
            raise AssertionError("contribuicoes Django nao estao em ordem alfabetica no periodo")
        bad_named_numbers = [
            item["nome"]
            for item in items
            if item.get("group_kind") == "nome" and str(item.get("nome") or "")[:1].isdigit()
        ]
        if bad_named_numbers:
            raise AssertionError(f"contribuicoes Django mantiveram documento antes do nome: {bad_named_numbers[0]}")
        required = [
            "Visualizar relatorio alfabetico",
            "Imprimir esta lista filtrada",
            "contributions-table",
            f"Mostrando {total} de {total} lancamentos",
        ]
        for snippet in required:
            if snippet not in content:
                raise AssertionError(f"contribuicoes Django sem trecho esperado: {snippet}")
        print(f"contributions_period={latest_competence}:{total}:alfabetico")
    if path.startswith("/contributions/statements/"):
        for snippet in ["Extrato de contribuicoes", "Abrir PDF do extrato", "Total geral", "Extrato analitico"]:
            if snippet not in content:
                raise AssertionError(f"extrato individual Django sem trecho esperado: {snippet}")
    if path == "/contributions/manual/":
        for snippet in ["Lancamento manual assistido", "Rateio por pessoa, contribuinte e destinacao", "Total do comprovante/envelope"]:
            if snippet not in content:
                raise AssertionError(f"lancamento manual assistido Django sem trecho esperado: {snippet}")
    if path == "/contributions/envelopes/":
        total = int(all_envelopes_data["total"] or 0)
        for snippet in [
            "Envelopes de contribuicao",
            "Criar lote de envelopes",
            "Registrar envelope",
            "Central de envelopes",
            "Lancamentos",
            "Envelopes",
            "Abrir lote",
            "Abrir lista completa",
            "Subir lote",
            "Subir envelope",
            "Imprimir esta lista",
            "Reprocessar telefones/enderecos",
            f"Mostrando {total} de {total} envelope(s)",
        ]:
            if snippet not in content:
                raise AssertionError(f"lista de envelopes Django sem trecho esperado: {snippet}")
    if path == "/contributions/envelopes/new/":
        for snippet in [
            "Registrar envelope",
            "Mes de competencia",
            "Tipo principal do envelope",
            "Campanha / destinacao principal",
            "Rastreabilidade financeira",
            "Numero do cheque",
            "Operacao / autorizacao",
            "Por padrao usa o total do envelope",
            "data-envelope-zoom-image",
            "lupa de leitura manuscrita",
            "Envelope digitalizado",
            "Envelope conferido manualmente; imagem anexada para auditoria.",
            "Imagem ou PDF do envelope",
            "Previa para digitacao",
            "data-envelope-file-input",
            "Caminho local do arquivo",
            "Funciona como no rateio",
            "Rateio em cartoes por pessoa, contribuinte e destinacao",
            "Pessoa, contribuinte ou nome lido no envelope",
            "Sugestoes por telefone e endereco",
            "Salvar agora e lancar",
            "Usar tipo principal",
        ]:
            if snippet not in content:
                raise AssertionError(f"formulario de envelope Django sem trecho esperado: {snippet}")
        if "Forma identificada" in content:
            raise AssertionError("formulario de envelope voltou a duplicar forma de recebimento na rastreabilidade")
        if 'value="None"' in content or ">None<" in content:
            raise AssertionError("formulario de envelope exibiu None em campos de rastreabilidade")
    if path == "/contributions/envelopes/lots/new/":
        for snippet in [
            "Criar lote de envelopes",
            "Data padrao sugerida",
            "Upload multiplo de imagens/PDFs",
            "Pasta local no Mac",
            "Envelope digitalizado",
            "O lote organiza as imagens",
        ]:
            if snippet not in content:
                raise AssertionError(f"formulario de lote de envelopes sem trecho esperado: {snippet}")
    if re.match(r"^/contributions/envelopes/lots/\d+/$", path):
        for snippet in [
            "Digitar proximo envelope",
            "Fila de digitacao",
            "Aguardando digitacao",
            "Total lancado",
        ]:
            if snippet not in content and snippet != "Digitar proximo envelope":
                raise AssertionError(f"detalhe de lote de envelopes sem trecho esperado: {snippet}")
    if re.match(r"^/contributions/envelopes/\d+/launch/$", path):
        for snippet in [
            "Digitar envelope do lote",
            "Salvar envelope e ir para o proximo",
            "Salvar agora e ir para o proximo",
            "Ignorar envelope",
            "Justificativa para ignorar este envelope",
            "Tipo principal do envelope",
            "Rastreabilidade financeira",
            "Por padrao usa o total do envelope",
            "Rateio em cartoes",
            "data-envelope-zoom-image",
            "Funciona como no rateio",
            "Sugestoes por telefone e endereco",
        ]:
            if snippet not in content:
                raise AssertionError(f"digitacao de envelope pendente sem trecho esperado: {snippet}")
        if "Forma identificada" in content:
            raise AssertionError("digitacao de envelope voltou a duplicar forma de recebimento na rastreabilidade")
        if 'value="None"' in content or ">None<" in content:
            raise AssertionError("digitacao de envelope exibiu None em campos de rastreabilidade")
    if re.match(r"^/contributions/envelopes/\d+/edit/$", path):
        for snippet in [
            "Editar envelope lancado",
            "Correcao auditada",
            "Salvar correcao auditada",
            "versao anterior sera preservada na auditoria",
            "Rateio em cartoes",
            "Pessoa, contribuinte ou nome lido no envelope",
            "Rastreabilidade financeira",
            "Por padrao usa o total do envelope",
            "data-envelope-zoom-image",
            "Funciona como no rateio",
            "Sugestoes por telefone e endereco",
        ]:
            if snippet not in content:
                raise AssertionError(f"edicao de envelope lancado sem trecho esperado: {snippet}")
        if "Forma identificada" in content:
            raise AssertionError("edicao de envelope voltou a duplicar forma de recebimento na rastreabilidade")
        if 'value="None"' in content or ">None<" in content:
            raise AssertionError("edicao de envelope exibiu None em campos de rastreabilidade")
    if re.match(r"^/contributions/envelopes/\d+/$", path):
        for snippet in ["Imagem arquivada", "Linhas lancadas", "Hash", "Rastreabilidade financeira", "data-envelope-zoom-image"]:
            if snippet not in content:
                raise AssertionError(f"detalhe de envelope Django sem auditoria documental: {snippet}")
    if path.startswith("/contributions/") and path.endswith("/split/"):
        for snippet in ["Rateio manual", "Rateio com soma fechada", "Salvar rateio conferido"]:
            if snippet not in content:
                raise AssertionError(f"rateio de contribuicao Django sem trecho esperado: {snippet}")
    if path.startswith("/contributions/") and path.endswith("/") and path not in {"/contributions/", "/contributions/manual/", "/contributions/envelopes/", "/contributions/envelopes/new/"} and not path.startswith("/contributions/new/") and not path.startswith("/contributions/statements/") and not path.startswith("/contributions/envelopes/") and not path.endswith("/split/"):
        for snippet in ["Ajuste manual seguro", "Justificativa obrigatoria da alteracao", "Historico de auditoria", "Salvar ajuste com auditoria"]:
            if snippet not in content:
                raise AssertionError(f"detalhe de contribuicao Django sem ajuste auditavel: {snippet}")
    if re.match(r"^/contributions/statements/\d+/$", path):
        for snippet in ["Extrato de contribuicoes", "Abrir PDF do extrato", "Enviar extrato por e-mail", "E-mail atual da ficha", "name=\"update_person_email\"", "name=\"email_update_reason\""]:
            if snippet not in content:
                raise AssertionError(f"extrato individual Django sem envio auditavel: {snippet}")
    if path.startswith("/contributions/new/"):
        for snippet in ["Lancamento manual com auditoria", "Subir envelope com imagem", "Abrir tela de envelope com imagem", "Justificativa obrigatoria", "Salvar contribuicao"]:
            if snippet not in content:
                raise AssertionError(f"nova contribuicao Django sem lancamento auditavel: {snippet}")
    if path == "/receipts/":
        for snippet in ["Recibos", "Gerar recibo por pessoa", "Monitorar fila de envio", "Fila de envio em andamento", "Pesquisar recibos", "Lista de recibos", "Envio automatico"]:
            if snippet not in content:
                raise AssertionError(f"lista de recibos Django sem trecho esperado: {snippet}")
    if path == "/receipts/queue/":
        for snippet in ["Monitor de envio de recibos", "Filtros do monitor", "Pendentes", "Enviados", "Falhas", "Ultimos itens da fila", "Auditoria de e-mails", "Reprocessar falhas e pendencias deste filtro", "Sincronizar e-mail"]:
            if snippet not in content:
                raise AssertionError(f"monitor de fila de recibos sem trecho esperado: {snippet}")
    if path.startswith("/receipts/?selected_person_id="):
        for snippet in [
            "Gerar recibos para",
            "Gerar recibos por competencia",
            "Gerar um recibo consolidado do periodo filtrado",
            "E-mail do recibo",
            "Salvar somente o padrao",
            "Gerar e enviar recibo consolidado",
            "Gerar um recibo consolidado",
            "Ver recibos desta pessoa",
        ]:
            if snippet not in content:
                raise AssertionError(f"central de recibos Django sem trecho esperado: {snippet}")
    if re.match(r"^/receipts/\d+/$", path):
        for snippet in ["Recibo de contribuicoes", "Imprimir esta tela", "Contribuicoes do recibo", "Logo do cliente", "Enviar ou reenviar por e-mail", "Abrir PDF do recibo"]:
            if snippet not in content:
                raise AssertionError(f"detalhe de recibo Django sem trecho esperado: {snippet}")
    if path.startswith("/reports/?competencia="):
        report = contribution_report(competencia=latest_competence)
        items = report["items"]
        named = report["named_items"]
        documents = report["document_items"]
        if len(items) != int(report["summary"]["contribuintes"] or 0):
            raise AssertionError("relatorio Django nao bate resumo de contribuintes")
        if len(documents) != int(report["summary"]["somente_documento"] or 0):
            raise AssertionError("relatorio Django nao bate resumo de somente documento")
        order = [item["group_kind"] for item in items]
        if "nome" in order and "documento" in order and order.index("documento") < max(index for index, kind in enumerate(order) if kind == "nome"):
            raise AssertionError("relatorio Django misturou documentos antes do fim dos nomes")
        bad_named_numbers = [item["nome"] for item in named if str(item["nome"])[:1].isdigit()]
        if bad_named_numbers:
            raise AssertionError(f"relatorio Django manteve numero no bloco nominal: {bad_named_numbers[0]}")
        bad_upper = [
            item["nome"]
            for item in named
            if any(ch.isalpha() for ch in str(item["nome"]))
            and str(item["nome"]) == str(item["nome"]).upper()
            and len(str(item["nome"])) > 4
        ]
        if bad_upper:
            raise AssertionError(f"relatorio Django manteve nome todo em maiusculas: {bad_upper[0]}")
        hybrid_identity = contribution_report_identity("", "64.984.878 JULIANA MADEIRA DOS SANTOS", "64.984.878 0001-91")
        document_identity = contribution_report_identity("", "12345678901", "12345678901")
        if hybrid_identity.get("group_kind") != "nome" or hybrid_identity.get("name") != "Juliana Madeira dos Santos":
            raise AssertionError("relatorio Django nao limpou identidade hibrida numero+nome")
        if document_identity.get("group_kind") != "documento" or document_identity.get("name") != "123.456.789-01":
            raise AssertionError("relatorio Django nao separou identidade somente CPF")
        required_report_snippets = ["Contribuintes com nome", "Somente documento"]
        if documents:
            required_report_snippets.append("Somente documento/numero")
        required_report_snippets.extend(["Abrir PDF oficial para imprimir", "Baixar PDF"])
        required_report_snippets.extend(["report-summary-strip", "summary-pill", "report-table", "remittance-chip"])
        for snippet in required_report_snippets:
            if snippet not in content:
                raise AssertionError(f"relatorio Django sem bloco esperado: {snippet}")
        print(f"reports_period_identity={latest_competence}:{len(named)}:nomes:{len(documents)}:documentos")
    if path == "/reports/destinations/":
        for snippet in ["Contribuicoes por destino", "Resumo por destino", "Destino financeiro", "Abrir PDF oficial para imprimir", "Baixar PDF"]:
            if snippet not in content:
                raise AssertionError(f"relatorio por destino Django sem trecho esperado: {snippet}")
    if path.startswith("/reports/destinations/?competencia="):
        destination_report = contribution_destination_report(competencia=latest_competence)
        if int(destination_report["summary"]["remessas"] or 0) <= 0:
            raise AssertionError("relatorio por destino nao encontrou remessas no periodo")
        destination_total = round(sum(float(item["total"] or 0) for item in destination_report["destinations"]), 2)
        summary_total = round(float(destination_report["summary"]["total"] or 0), 2)
        if destination_total != summary_total:
            raise AssertionError("relatorio por destino nao fecha totais por destino")
        if not destination_report["destination_options"]:
            raise AssertionError("relatorio por destino nao retornou opcoes de destino")
        for destination in destination_report["destinations"]:
            destination_order = [
                (0 if item.get("group_kind") == "nome" else 1, str(item.get("sort_key") or ""), str(item.get("documento") or ""))
                for item in destination["items"]
            ]
            if destination_order != sorted(destination_order):
                raise AssertionError(f"relatorio por destino fora da ordem alfabetica em {destination['label']}")
        for snippet in ["Contribuicoes por destino", "Resumo por destino", "Contribuintes com nome", "SA membro ativo", "report-summary-strip", "report-table", "remittance-chip"]:
            if snippet not in content:
                raise AssertionError(f"relatorio por destino filtrado sem trecho esperado: {snippet}")
        print(f"reports_destinations={latest_competence}:{destination_report['summary']['destinos']}:{destination_report['summary']['remessas']}:destinos")

legacy_receipt_redirect = client.get(f"/receipts/new/?person_id={contribution_person_id}")
if legacy_receipt_redirect.status_code not in {301, 302}:
    raise AssertionError("rota legada /receipts/new/ nao redirecionou para a central nova")
redirect_target = legacy_receipt_redirect.headers.get("Location", "")
expected_receipt_target = f"/receipts/?selected_person_id={contribution_person_id}"
if expected_receipt_target not in redirect_target:
    raise AssertionError(
        f"rota legada /receipts/new/ redirecionou para destino inesperado: {redirect_target or '-'}"
    )

miguel_results = [
    item
    for item in list_people(q="Miguel de Souza Santos", limit=20)["items"]
    if str(item.get("nome") or "").strip() == "Miguel de Souza Santos"
]
if len(miguel_results) != 1:
    raise AssertionError(f"merge de Miguel ficou inconsistente: encontrados {len(miguel_results)} registros ativos")
miguel = miguel_results[0]
if str(miguel.get("codigo") or "") != "100521":
    raise AssertionError(f"merge de Miguel preservou codigo inesperado: {miguel.get('codigo')}")
if str(miguel.get("cpf") or "") != "18111732767":
    raise AssertionError(f"merge de Miguel preservou CPF inesperado: {miguel.get('cpf')}")
print("merge_miguel=OK")

invalid_cpf_response = client.get("/people/validate-field/?field=cpf&value=12345678901")
if invalid_cpf_response.status_code != 200 or invalid_cpf_response.json().get("ok") is not False:
    raise AssertionError("validador imediato de CPF invalido nao bloqueou")
invalid_email_response = client.get("/people/validate-field/?field=email_principal&value=email-invalido")
if invalid_email_response.status_code != 200 or invalid_email_response.json().get("ok") is not False:
    raise AssertionError("validador imediato de e-mail invalido nao bloqueou")
if cpf_person:
    duplicate_cpf_response = client.get(f"/people/validate-field/?field=cpf&value={cpf_person['cpf']}")
    if duplicate_cpf_response.status_code != 200 or duplicate_cpf_response.json().get("ok") is not False:
        raise AssertionError("validador imediato de CPF duplicado nao bloqueou")
search_response = client.get(f"/people/search/?q=Maria&person_id={person_id}")
if search_response.status_code != 200 or not search_response.json().get("results"):
    raise AssertionError("busca incremental de pessoa relacionada nao retornou resultados")
if inactive_person:
    inactive_response = client.get(f"/people/{inactive_person['id']}/")
    if inactive_response.status_code != 200 or "Pessoa nao encontrada" not in inactive_response.content.decode("utf-8", errors="replace"):
        raise AssertionError("ficha excluida/inativa ainda abriu como ficha operacional")
trash = list_secure_people_trash()
if not {"items", "total", "shown"}.issubset(trash):
    raise AssertionError("lixeira segura nao retornou estrutura de auditoria")
if int(trash["shown"] or 0) > int(trash["total"] or 0):
    raise AssertionError("lixeira segura retornou contagem inconsistente")
print("people_field_validator=OK")

print(f"pessoas={summary['people_total']}")
print(f"contribuicoes={summary['contributions_count']}")
print(f"total={summary['contributions_total_fmt']}")
print(f"django_groups={access['group_count']}")
print(f"django_permissions={access['permission_count']}")
"""


def build_checks(db_path: Path) -> list[Check]:
    checks: list[Check] = []
    checks.append(
        Check(
            "Banco legado configurado",
            "OK" if db_path.exists() else "FALHA",
            str(db_path) if db_path.exists() else "arquivo nao encontrado",
        )
    )
    manage_ok, manage_output = run_with_venv(["power_church_django/manage.py", "check"], db_path)
    checks.append(
        Check(
            "Django manage.py check",
            "OK" if manage_ok else "FALHA",
            manage_output.replace("\n", " | ") if manage_output else "sem saida",
        )
    )
    probe_ok, probe_output = run_with_venv(["-c", django_probe_code()], db_path)
    checks.append(
        Check(
            "Rotas Django em leitura",
            "OK" if probe_ok else "FALHA",
            probe_output.replace("\n", " | ") if probe_output else "sem saida",
        )
    )
    return checks


def write_report(checks: list[Check]) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    target = REPORT_DIR / f"django_funcional_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    failed = [check for check in checks if check.failed]
    lines = [
        "# Django Funcional",
        "",
        f"Gerado em: {datetime.now().isoformat(timespec='seconds')}",
        f"Resultado: {'FALHAS' if failed else 'OK'}",
        "",
        "| Check | Status | Detalhe |",
        "| --- | --- | --- |",
    ]
    for check in checks:
        lines.append(f"| {check.name} | {check.status} | {check.detail} |")
    lines.extend(
        [
            "",
            "## Escopo",
            "",
            "- Valida o Django como interface inicial em modo somente leitura.",
            "- Confirma rotas principais, resumo operacional e bloqueio de escrita no SQLite legado.",
            "- Confirma que a migracao de importacoes preserva botoes de lote e auditoria operacional sem executar escrita no banco real.",
            "- Confirma que a tela Django de regras de centavos permanece acessivel para operacao.",
            "- Confirma que contribuicoes no Django expoem ajuste manual com justificativa e tela de lancamento manual.",
            "- Confirma extrato individual por pessoa e telas de recibos no Django.",
            "- Confirma que a ficha de pessoa expoe vinculos familiares manuais e sugestoes por endereco.",
            "- Nao substitui ainda a homologacao funcional do prototipo.",
        ]
    )
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="Verifica se o Django esta funcional em leitura sobre o banco legado.")
    parser.add_argument("--db", default=str(ROOT / "data" / "power_church_membros_importado.db"), help="Caminho do banco SQLite legado.")
    parser.add_argument("--report", action="store_true", help="Grava relatorio markdown.")
    args = parser.parse_args()

    checks = build_checks(Path(args.db))
    for check in checks:
        print(f"- {check.status}: {check.name} ({check.detail})")
    if args.report:
        report = write_report(checks)
        print(f"\nRelatorio: {report}")
    return 1 if any(check.failed for check in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())

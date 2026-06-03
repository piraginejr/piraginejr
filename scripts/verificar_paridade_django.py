from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "data" / "homologacao"
DJANGO_DIR = ROOT / "power_church_django"
DJANGO_VENV_PYTHON = DJANGO_DIR / ".venv" / "bin" / "python"


@dataclass
class Check:
    module: str
    status: str
    detail: str

    @property
    def failed(self) -> bool:
        return self.status == "FALHA"


def run_inside_venv(db_path: Path) -> tuple[bool, str]:
    if not DJANGO_VENV_PYTHON.exists():
        return False, f"Python da venv Django nao encontrado: {DJANGO_VENV_PYTHON}"
    env = dict(os.environ)
    env.setdefault("PYTHONPYCACHEPREFIX", "/private/tmp/pycache_powerchurch")
    env["POWER_CHURCH_LEGACY_DB_PATH"] = str(db_path)
    completed = subprocess.run(
        [str(DJANGO_VENV_PYTHON), "-c", probe_code()],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )
    output = "\n".join(part for part in [completed.stdout.strip(), completed.stderr.strip()] if part)
    return completed.returncode == 0, output


def probe_code() -> str:
    return r'''
import os
import sqlite3
import sys
from pathlib import Path
from urllib.parse import quote

root = Path.cwd()
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "power_church_django"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "power_church_site.settings")

import django
django.setup()

from django.conf import settings
from django.test import Client

from power_church_django.services.legacy import (
    connect_legacy,
    contribution_destination_report,
    contribution_report,
    dashboard_summary,
    list_contributions,
    list_contributors,
)

if "testserver" not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append("testserver")

def emit(module, status, detail):
    print(f"PARIDADE|{module}|{status}|{detail}")

def get(path):
    response = client.get(path)
    body = b"".join(response.streaming_content) if getattr(response, "streaming", False) else response.content
    if response.status_code != 200:
        raise AssertionError(f"{path} retornou {response.status_code}")
    return body

def assert_html(path, snippets):
    body = get(path)
    content = body.decode("utf-8", errors="replace")
    missing = [snippet for snippet in snippets if snippet not in content]
    if missing:
        raise AssertionError(f"{path} sem trechos: {', '.join(missing)}")
    if "/branding/logo" not in content or "Navegacao principal" not in content:
        raise AssertionError(f"{path} nao renderizou layout Django")
    return content

client = Client()
summary = dashboard_summary()
with connect_legacy() as conn:
    people_total = conn.execute("SELECT COUNT(*) FROM pessoas WHERE ativo = 1").fetchone()[0]
    contributors_total = conn.execute("SELECT COUNT(*) FROM contribuintes WHERE ativo = 1").fetchone()[0]
    contributions_total = conn.execute("SELECT COUNT(*) FROM contribuicoes WHERE ativo = 1").fetchone()[0]
    bank_zero = conn.execute(
        """
        SELECT COUNT(*)
          FROM contribuicoes
         WHERE ativo = 1
           AND valor <= 0
           AND (pix_movimento_id IS NOT NULL OR extrato_movimento_id IS NOT NULL)
        """
    ).fetchone()[0]
    person_id = conn.execute("SELECT id FROM pessoas WHERE ativo = 1 ORDER BY id LIMIT 1").fetchone()[0]
    contributor_id = conn.execute("SELECT id FROM contribuintes WHERE ativo = 1 ORDER BY id LIMIT 1").fetchone()[0]
    contribution_row = conn.execute(
        "SELECT id, pessoa_id FROM contribuicoes WHERE ativo = 1 AND pessoa_id IS NOT NULL ORDER BY id DESC LIMIT 1"
    ).fetchone()
    contribution_id = contribution_row[0]
    contribution_person_id = contribution_row[1]
    latest_competence = conn.execute(
        "SELECT competencia FROM contribuicoes WHERE ativo = 1 AND COALESCE(competencia, '') <> '' "
        "GROUP BY competencia ORDER BY MAX(COALESCE(competencia_ordem, 0)) DESC, competencia DESC LIMIT 1"
    ).fetchone()[0]
    statement_lot = conn.execute("SELECT id FROM extrato_lotes ORDER BY id DESC LIMIT 1").fetchone()
    pix_lot = conn.execute("SELECT id FROM pix_lotes ORDER BY id DESC LIMIT 1").fetchone()
    statement_movement = conn.execute("SELECT id FROM extrato_movimentos ORDER BY id DESC LIMIT 1").fetchone()
    pix_movement = conn.execute("SELECT id FROM pix_movimentos ORDER BY id DESC LIMIT 1").fetchone()
    people_lot = conn.execute("SELECT id FROM import_lotes ORDER BY id DESC LIMIT 1").fetchone()
    receipt_row = conn.execute("SELECT id FROM recibos ORDER BY id DESC LIMIT 1").fetchone()

if summary["people_total"] != people_total:
    raise AssertionError("dashboard Django nao bate total de pessoas")
if summary["contributors_total"] != contributors_total:
    raise AssertionError("dashboard Django nao bate total de contribuintes")
if summary["contributions_count"] != contributions_total:
    raise AssertionError("dashboard Django nao bate total de contribuicoes")
if bank_zero:
    raise AssertionError(f"existem {bank_zero} contribuicoes bancarias ativas com valor <= 0")
emit("Dashboard", "OK", f"{people_total} pessoas, {contributors_total} contribuintes, {contributions_total} contribuicoes")

assert_html("/people/", ["Pessoas", "Importar pessoas", "Nova pessoa", "Exportar XLSX", "Exportar CSV", "Exportacao dinamica de pessoas", "Familias e votacao"])
csv_body = get("/people/export/?format=csv")
xlsx_body = get("/people/export/?q=Maria&format=xlsx")
dynamic_csv_body = get("/people/export/?preset=familias_votacao&column=nome&column=familia_domiciliar&column=familia_tem_contribuinte&format=csv")
if b"Nome" not in csv_body or len(csv_body) < 200:
    raise AssertionError("exportacao CSV de pessoas invalida")
if not xlsx_body.startswith(b"PK") or len(xlsx_body) < 2000:
    raise AssertionError("exportacao XLSX de pessoas invalida")
if b"Familia domiciliar" not in dynamic_csv_body or b"Familia tem contribuinte" not in dynamic_csv_body:
    raise AssertionError("exportacao dinamica CSV de pessoas nao trouxe colunas familiares")
assert_html(
    "/people/families/",
    ["Familias domiciliares", "Todos os domicilios", "Fila de auditoria", "Familias estendidas", "Contribuicao na familia"],
)
assert_html(
    "/people/families/?section=audit",
    ["Fila de auditoria", "Aplicacao em lote", "Hipoteses para auditoria", "Ignorar sugestoes selecionadas"],
)
assert_html(
    "/people/families/?section=extended",
    ["Familias estendidas", "Nucleo domiciliar", "Financeiro", "Situacao"],
)
assert_html(
    f"/people/{person_id}/",
    ["Dados cadastrais", "person-photo-large", "Mesclar ficha", "Familia domiciliar por endereco", "Sincronizar familias domiciliares por endereco", "Relacoes familiares ativas", "relationship-card", "Desassociar da familia domiciliar", "Ignorar sugestao", "data-person-relationship-search", "Contribuintes vinculados", "Ultimas contribuicoes"],
)
assert_html(
    f"/people/{person_id}/merge/",
    ["Mesclar ficha em", "Buscar ficha duplicada", "Justificativa da mesclagem"],
)
assert_html(
    f"/people/{person_id}/edit/",
    ["Editar", "Salvar", "Nome social (apelido como e conhecido)", "Foto da pessoa", "Upload da foto", "data-person-field-validator", "data-cep-lookup"],
)
assert_html(
    "/people/new/",
    ["Nova pessoa", "Criar ficha", "Nome social (apelido como e conhecido)", "Foto da pessoa", "Upload da foto", "data-person-field-validator", "data-cep-lookup"],
)
assert_html("/people/imports/", ["Importacao de pessoas", "Subir planilha Excel", "Lotes recentes de pessoas"])
if people_lot:
    assert_html(f"/people/imports/{people_lot[0]}/", ["Lote de pessoas", "Linhas importadas"])
assert_html("/audit/", ["Mesclar fichas do cadastro", "Buscar ficha principal", "Buscar ficha duplicada"])
emit("Pessoas e importacao de pessoas", "OK", "lista, familias organizadas/auditoria, ficha com vinculos familiares, edicao e auditoria de importacao no Django")

contributors_all = list_contributors(limit=10000)
contributors_pf = list_contributors(tags=["pf"], section="contributors", limit=10000)
contributors_pj = list_contributors(tags=["pj"], section="contributors", limit=10000)
contributors_linked = list_contributors(tags=["vinculado"], section="contributors", limit=10000)
contributors_unlinked = list_contributors(tags=["sem_vinculo"], section="contributors", limit=10000)
positive_names = {
    str(item.get("nome") or "").strip().upper()
    for item in contributors_all["items"]
    if int(item.get("contribuicoes_qtd") or 0) > 0 or float(item.get("total_contribuido") or 0) > 0
}
visible_shadows = [
    item
    for item in contributors_all["items"]
    if str(item.get("nome") or "").strip().upper() in positive_names
    and int(item.get("contribuicoes_qtd") or 0) == 0
    and float(item.get("total_contribuido") or 0) == 0
]
if visible_shadows:
    raise AssertionError(f"central de contribuintes ainda mostra identidade sombra sem contribuicao: {visible_shadows[0]['nome']}")
ccs_items = list_contributors(q="CCS", limit=10000)["items"]
if any(float(item.get("total_contribuido") or 0) == 0 for item in ccs_items):
    raise AssertionError("busca CCS ainda mostra identidade financeira zerada")
assert_html("/contributors/", ["Central estrategica", "Marcadores estrategicos", "Tabela principal de contribuintes"])
assert_html("/contributors/?tag=pf&section=contributors", ["Tabela principal de contribuintes"])
assert_html("/contributors/?tag=pj&section=contributors", ["Tabela principal de contribuintes"])
assert_html("/contributors/?mode=recorrentes&tag=integracao&section=family_links", ["Contribuintes recorrentes ligados a familias ja cadastradas"])
assert_html("/contributors/?mode=recorrentes&tag=familia_sugerida&section=family_groups", ["Blocos familiares sugeridos"])
assert_html(f"/contributors/{contributor_id}/", ["Ficha", "Contribuicoes vinculadas"])
if contributors_pf["total"] != contributors_all["summary"]["pf"]:
    raise AssertionError("total PF nao bate central estrategica")
if contributors_pj["total"] != contributors_all["summary"]["pj"]:
    raise AssertionError("total PJ nao bate central estrategica")
if contributors_linked["total"] + contributors_unlinked["total"] != contributors_all["summary"]["total"]:
    raise AssertionError("vinculados + sem vinculo nao fecha total de contribuintes")
contributor_order = [
    (0 if item.get("group_kind") == "nome" else 1, str(item.get("sort_key") or ""), int(item.get("id") or 0))
    for item in contributors_all["items"]
]
if contributor_order != sorted(contributor_order):
    raise AssertionError("contribuintes auxiliares nao estao em ordem alfabetica")
bad_contributor_numbers = [
    item["nome"]
    for item in contributors_all["items"]
    if item.get("group_kind") == "nome" and str(item.get("nome") or "")[:1].isdigit()
]
if bad_contributor_numbers:
    raise AssertionError(f"contribuinte auxiliar ainda inicia com numero: {bad_contributor_numbers[0]}")
emit("Contribuintes estrategicos", "OK", f"PF {contributors_pf['total']}, PJ {contributors_pj['total']}, vinculados {contributors_linked['total']}, sem vinculo {contributors_unlinked['total']}")

period_data = list_contributions(competencia=latest_competence, limit=10000)
if len(period_data["items"]) != int(period_data["total"] or 0):
    raise AssertionError("contribuicoes por periodo ainda estao truncadas")
period_order = [
    (0 if item.get("group_kind") == "nome" else 1, str(item.get("sort_key") or ""), str(item.get("data_raw") or ""), int(item.get("competencia_ordem") or 0), int(item.get("id") or 0))
    for item in period_data["items"]
]
if period_order != sorted(period_order):
    raise AssertionError("contribuicoes por periodo nao estao em ordem alfabetica")
bad_contribution_numbers = [
    item["nome"]
    for item in period_data["items"]
    if item.get("group_kind") == "nome" and str(item.get("nome") or "")[:1].isdigit()
]
if bad_contribution_numbers:
    raise AssertionError(f"contribuicao ainda inicia com numero antes do nome: {bad_contribution_numbers[0]}")
assert_html("/contributions/", ["Contribuicoes", "Visualizar relatorio alfabetico"])
assert_html("/contributions/manual/", ["Lancamento manual assistido", "Rateio por pessoa, contribuinte e destinacao"])
assert_html(f"/contributions/{contribution_id}/", ["Ajuste manual seguro", "Historico de auditoria"])
assert_html(f"/contributions/{contribution_id}/split/", ["Rateio manual", "Rateio com soma fechada"])
assert_html(f"/contributions/new/?person_id={contribution_person_id}", ["Lancamento manual com auditoria"])
assert_html(f"/contributions/statements/{contribution_person_id}/", ["Extrato de contribuicoes", "Abrir PDF do extrato"])
emit("Contribuicoes", "OK", f"{latest_competence}: {period_data['total']} lancamentos completos e alfabeticos")

assert_html("/receipts/", ["Recibos", "Gerar recibo por pessoa", "Pesquisar recibos", "Lista de recibos", "Envio automatico"])
assert_html(
    f"/receipts/?selected_person_id={contribution_person_id}",
    [
        "Gerar recibos para",
        "Gerar recibos por competencia",
        "Gerar um recibo consolidado do periodo filtrado",
        "E-mail do recibo",
        "Salvar somente o padrao",
        "Gerar e enviar recibo consolidado",
    ],
)
legacy_receipt_redirect = client.get(f"/receipts/new/?person_id={contribution_person_id}")
if legacy_receipt_redirect.status_code not in {301, 302}:
    raise AssertionError("rota legada /receipts/new/ nao redirecionou para a central de recibos")
if f"/receipts/?selected_person_id={contribution_person_id}" not in legacy_receipt_redirect.headers.get("Location", ""):
    raise AssertionError("rota legada /receipts/new/ redirecionou para destino inesperado")
if receipt_row:
    assert_html(
        f"/receipts/{receipt_row[0]}/",
        ["Logo do cliente", "Enviar ou reenviar por e-mail", "Abrir PDF", "Contribuicoes do recibo"],
    )
    receipt_pdf_body = get(f"/receipts/{receipt_row[0]}/pdf/")
    if not receipt_pdf_body.startswith(b"%PDF") or len(receipt_pdf_body) < 1200:
        raise AssertionError("PDF proprio de recibo invalido")
emit("Recibos", "OK", "lista, central de emissao por pessoa e detalhe/impressao disponiveis no Django")

assert_html("/imports/", ["Importar extrato bancario", "Sicoob PIX historico", "Sicoob Extrato Completo", "Criar lote", "Motor de leitura PDF", "Comparar Swift x PyMuPDF"])
assert_html("/imports/rules/", ["Regras por centavos", "Mapa atual", "Salvar regra"])
if statement_lot:
    assert_html(f"/imports/statement/{statement_lot[0]}/", ["Processamento do lote", "Reprocessar lote", "Encerrar lote", "lot-movements-table", "Banco/Pix", "CPF cadastro", "Confirmar sugestao", "Auditar / validar"])
if pix_lot:
    assert_html(f"/imports/pix/{pix_lot[0]}/", ["Processamento do lote", "Reprocessar lote", "Encerrar lote", "Auditar pendencias"])
if statement_movement:
    assert_html(f"/imports/statement/movement/{statement_movement[0]}/", ["Auditoria operacional", "Confirmar movimento"])
if pix_movement:
    assert_html(f"/imports/pix/movement/{pix_movement[0]}/", ["Auditoria operacional", "Confirmar movimento"])
emit("Importacoes bancarias", "OK", "central, lotes, movimentos e regras de centavos operacionais no Django")

report = contribution_report(competencia=latest_competence)
destination_report = contribution_destination_report(competencia=latest_competence)
assert_html("/reports/", ["Contribuicoes por periodo", "Abrir PDF oficial para imprimir", "Baixar PDF"])
assert_html(f"/reports/?competencia={quote(latest_competence)}", ["Contribuintes com nome", "Legenda do rol"])
assert_html("/reports/destinations/", ["Contribuicoes por destino", "Resumo por destino", "Destino financeiro"])
assert_html(f"/reports/destinations/?competencia={quote(latest_competence)}", ["Contribuicoes por destino", "Resumo por destino", "Contribuintes com nome"])
pdf_body = get(f"/reports/contributions-period.pdf?competencia={quote(latest_competence)}&inline=1")
destination_pdf_body = get(f"/reports/contributions-destinations.pdf?competencia={quote(latest_competence)}&inline=1")
if not pdf_body.startswith(b"%PDF") or len(pdf_body) < 1200:
    raise AssertionError("PDF de relatorio por periodo invalido")
if not destination_pdf_body.startswith(b"%PDF") or len(destination_pdf_body) < 1200:
    raise AssertionError("PDF de relatorio por destino invalido")
if int(report["summary"]["remessas"] or 0) <= 0:
    raise AssertionError("relatorio por periodo nao retornou remessas")
if int(destination_report["summary"]["remessas"] or 0) != int(report["summary"]["remessas"] or 0):
    raise AssertionError("relatorio por destino nao bate remessas do periodo")
emit("Relatorios e PDF", "OK", f"{latest_competence}: {report['summary']['contribuintes']} contribuintes, {destination_report['summary']['destinos']} destinos, PDFs {len(pdf_body)}/{len(destination_pdf_body)} bytes")

assert_html("/audit/", ["Auditoria"])
assert_html("/audit/?modo=django", ["Rastreabilidade Django", "Eventos Django"])
emit("Auditoria", "OK", "consulta de auditoria e eventos Django")

emit("Usuarios e privilegios", "ADIADO", "bloco 2 deixado para depois por decisao do operador")
emit("PostgreSQL, OCR e novos bancos", "ADIADO", "fora deste bloco de conclusao operacional")
'''


def parse_probe_output(output: str) -> list[Check]:
    checks: list[Check] = []
    for line in output.splitlines():
        if not line.startswith("PARIDADE|"):
            continue
        _prefix, module, status, detail = line.split("|", 3)
        checks.append(Check(module, status, detail))
    if not checks:
        checks.append(Check("Execucao", "FALHA", output or "sem saida do verificador"))
    return checks


def write_report(checks: list[Check]) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    target = REPORT_DIR / f"paridade_django_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    failed = [check for check in checks if check.failed]
    lines = [
        "# Paridade Django Operacional",
        "",
        f"Gerado em: {datetime.now().isoformat(timespec='seconds')}",
        f"Resultado: {'FALHAS' if failed else 'OK'}",
        "",
        "| Modulo | Status | Detalhe |",
        "| --- | --- | --- |",
    ]
    for check in checks:
        lines.append(f"| {check.module} | {check.status} | {check.detail} |")
    lines.extend(
        [
            "",
            "## Leitura",
            "",
            "- `OK` indica modulo operacional no Django para uso diario.",
            "- `ADIADO` indica item conscientemente fora deste bloco, sem bloquear a conclusao operacional.",
            "- `FALHA` bloqueia considerar o Django como interface principal.",
        ]
    )
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="Verifica paridade operacional entre prototipo e Django.")
    parser.add_argument("--db", default=str(ROOT / "data" / "power_church_membros_importado.db"), help="Caminho do banco SQLite legado.")
    parser.add_argument("--report", action="store_true", help="Grava relatorio markdown.")
    args = parser.parse_args()
    db_path = Path(args.db)
    if not db_path.exists():
        checks = [Check("Banco legado", "FALHA", f"nao encontrado: {db_path}")]
    else:
        ok, output = run_inside_venv(db_path)
        checks = parse_probe_output(output)
        if not ok and not any(check.failed for check in checks):
            checks.append(Check("Execucao", "FALHA", output or "verificador retornou erro"))
    for check in checks:
        print(f"- {check.status}: {check.module} ({check.detail})")
    if args.report:
        report = write_report(checks)
        print(f"\nRelatorio: {report}")
    return 1 if any(check.failed for check in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DJANGO_DIR = ROOT / "power_church_django"
DJANGO_VENV_PYTHON = DJANGO_DIR / ".venv" / "bin" / "python"
REPORT_DIR = ROOT / "data" / "homologacao"


@dataclass
class Check:
    name: str
    status: str
    detail: str

    @property
    def failed(self) -> bool:
        return self.status == "FALHA"


def _run_inside_venv() -> int | None:
    if Path(sys.executable).resolve() == DJANGO_VENV_PYTHON.resolve() or not DJANGO_VENV_PYTHON.exists():
        return None
    completed = subprocess.run(
        [str(DJANGO_VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]],
        cwd=str(ROOT),
        text=True,
        check=False,
    )
    return completed.returncode


def _contains_all(html: str, tokens: list[str]) -> tuple[bool, str]:
    missing = [token for token in tokens if token not in html]
    return not missing, "OK" if not missing else "faltando: " + ", ".join(missing)


def _index_of(html: str, token: str) -> int:
    index = html.find(token)
    return index if index >= 0 else 10**9


def build_checks(db_path: Path) -> list[Check]:
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(DJANGO_DIR))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "power_church_site.settings")
    os.environ["POWER_CHURCH_LEGACY_DB_PATH"] = str(db_path)

    import django

    django.setup()

    from django.conf import settings
    from django.test import Client

    from power_church_django.services.legacy import connect_legacy, list_contributions, list_contributors, list_envelopes, list_people

    if "testserver" not in settings.ALLOWED_HOSTS:
        settings.ALLOWED_HOSTS.append("testserver")

    client = Client()
    with connect_legacy() as conn:
        latest_competence = conn.execute(
            "SELECT competencia FROM contribuicoes WHERE ativo = 1 AND COALESCE(competencia, '') <> '' "
            "GROUP BY competencia ORDER BY MAX(COALESCE(competencia_ordem, 0)) DESC, competencia DESC LIMIT 1"
        ).fetchone()[0]
        statement_lot = conn.execute("SELECT id FROM extrato_lotes ORDER BY id DESC LIMIT 1").fetchone()
        contribution_person_id = conn.execute(
            "SELECT pessoa_id FROM contribuicoes WHERE ativo = 1 AND pessoa_id IS NOT NULL ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]

    def get(path: str) -> str:
        response = client.get(path)
        body = b"".join(response.streaming_content) if getattr(response, "streaming", False) else response.content
        if response.status_code != 200:
            raise AssertionError(f"{path} retornou {response.status_code}")
        return body.decode("utf-8", errors="replace")

    checks: list[Check] = []
    dashboard_html = get("/")
    ok, detail = _contains_all(
        dashboard_html,
        [
            "Dashboard operacional",
            "Domicilios da base",
            "Familias domiciliares",
            "Unipessoais",
            "Criterio amplo",
            "Quorum em Niteroi",
        ],
    )
    checks.append(Check("Dashboard expone domicilios e quorum de Niteroi", "OK" if ok else "FALHA", detail))
    imports_html = get("/imports/")
    ok, detail = _contains_all(
        imports_html,
        [
            "Importar extrato bancario",
            "Motor de leitura PDF",
            "Comparar Swift x PyMuPDF antes de gravar",
            "Criar lote",
            "Abrir lote",
            "Arquivo e periodo",
            "Financeiro",
            "Fila",
            "Status do lote",
        ],
    )
    checks.append(Check("Importacoes contem acoes principais", "OK" if ok else "FALHA", detail))
    default_compare = bool(re.search(r'<option value="compare_pymupdf"[^>]*selected', imports_html))
    swift_default = bool(re.search(r'<option value="swift_pdfkit"[^>]*selected', imports_html))
    checks.append(
        Check(
            "Importacoes default PyMuPDF seguro",
            "OK" if default_compare and not swift_default else "FALHA",
            "compare_pymupdf selecionado por padrao" if default_compare and not swift_default else "default incorreto",
        )
    )
    checks.append(
        Check(
            "Importacoes botao abrir lote",
            "OK" if 'class="button secondary compact-button"' in imports_html and 'class="fit-table imports-table"' in imports_html else "FALHA",
            "botao explicito Abrir lote presente em tabela compacta",
        )
    )

    if statement_lot:
        lot_html = get(f"/imports/statement/{statement_lot[0]}/")
        ok, detail = _contains_all(
            lot_html,
            ["Processamento do lote", "Reprocessar lote", "Encerrar lote", "Auditar pendencias", "lot-movements-table", "Banco/Pix", "CPF cadastro", "Confirmar sugestao", "Auditar / validar", "Editar regras de centavos"],
        )
        checks.append(Check("Lote bancario contem acoes operacionais", "OK" if ok else "FALHA", detail))

    contributors_html = get("/contributors/")
    ok, detail = _contains_all(contributors_html, ["compact-marker-grid", "compact-check", "Abrir selecao", "Tabela principal de contribuintes"])
    checks.append(Check("Contribuintes marcadores compactos", "OK" if ok else "FALHA", detail))
    contributors_data = list_contributors(limit=10000)
    contributor_items = contributors_data["items"]
    contributor_order = [
        (0 if item.get("group_kind") == "nome" else 1, str(item.get("sort_key") or ""), int(item.get("id") or 0))
        for item in contributor_items
    ]
    bad_contributor_name = next(
        (
            str(item.get("nome") or "")
            for item in contributor_items
            if item.get("group_kind") == "nome" and str(item.get("nome") or "")[:1].isdigit()
        ),
        "",
    )
    checks.append(
        Check(
            "Contribuintes alfabeticos sem documento na frente",
            "OK" if contributor_order == sorted(contributor_order) and not bad_contributor_name else "FALHA",
            "nomes limpos e ordenados" if contributor_order == sorted(contributor_order) and not bad_contributor_name else f"problema: {bad_contributor_name or 'ordem quebrada'}",
        )
    )
    checks.append(
        Check(
            "Contribuintes sem cards gigantes nos checkboxes",
            "OK" if "check-item card" not in contributors_html else "FALHA",
            "checkboxes usam layout compacto",
        )
    )
    contributor_links_html = get("/contributors/?mode=recorrentes&tag=integracao&section=family_links")
    contributor_links_data = list_contributors(mode="recorrentes", tags=["integracao"], section="family_links", limit=10000)
    contributor_link_tokens = ["Contribuintes recorrentes ligados a familias ja cadastradas"]
    if contributor_links_data["family_links"]:
        contributor_link_tokens.extend(["Criar frequentador", "Vincular a esta pessoa", "Risco"])
    ok, detail = _contains_all(contributor_links_html, contributor_link_tokens)
    checks.append(Check("Contribuintes exibem auditoria inteligente de integracao", "OK" if ok else "FALHA", detail))
    people_data = list_people()
    people_html = get("/people/")
    ok, detail = _contains_all(
        people_html,
        [
            "Imprimir lista",
            "Exportacao dinamica de pessoas",
            "Cadastro basico",
            "Contatos",
            "Familias e votacao",
            "Cidade",
            "Selecionar tudo",
            f"Mostrando {people_data['total']} de {people_data['total']} registros",
        ],
    )
    checks.append(Check("Pessoas exibem lista completa com exportacao dinamica", "OK" if ok else "FALHA", detail))
    families_html = get("/people/families/")
    ok, detail = _contains_all(
        families_html,
        [
            "Todos os domicilios",
            "Nome automatico:",
            "Cabeca da familia",
            "Salvar identidade familiar",
            "Fila de auditoria",
            "Familias estendidas",
            "Situacao do domicilio",
            "Contribuicao na familia",
            "Unipessoal",
            "Imprimir lista",
        ],
    )
    checks.append(Check("Familias organizadas exibem consulta imprimivel e identidade nominal", "OK" if ok else "FALHA", detail))
    families_audit_html = get("/people/families/?section=audit")
    ok, detail = _contains_all(
        families_audit_html,
        [
            "Fila de auditoria",
            "Aplicacao em lote",
            "Criar familias selecionadas",
            "Ignorar sugestoes selecionadas",
            "Padrao inteligente da auditoria",
            "Categoria inteligente",
            "Acao sugerida:",
        ],
    )
    checks.append(Check("Familias preservam fila de auditoria", "OK" if ok else "FALHA", detail))
    families_broad_html = get("/people/families/?section=broad")
    ok, detail = _contains_all(
        families_broad_html,
        [
            "Criterio amplo",
            "Consolidar familias selecionadas",
            "Padrao inteligente do criterio amplo",
            "Consolidacao manual",
        ],
    )
    checks.append(Check("Familias exibem criterio amplo para consolidacao manual", "OK" if ok else "FALHA", detail))
    families_extended_html = get("/people/families/?section=extended")
    ok, detail = _contains_all(
        families_extended_html,
        [
            "Familias estendidas",
            "Nucleo domiciliar",
            "Financeiro",
            "Situacao",
        ],
    )
    checks.append(Check("Familias estendidas agrupam sobrenomes e nucleos", "OK" if ok else "FALHA", detail))
    audit_html = get("/audit/")
    ok, detail = _contains_all(
        audit_html,
        [
            "Classificacao",
            "Descricao / Acao sugerida",
            "Risco",
        ],
    )
    checks.append(Check("Auditoria operacional exibe classificacao inteligente", "OK" if ok else "FALHA", detail))
    contributions_data = list_contributions()
    contributions_html = get("/contributions/")
    ok, detail = _contains_all(
        contributions_html,
        [
            "Central de envelopes",
            "Lancamentos",
            "Envelopes",
            "Envelopes ativos",
            "Total lancado",
            "Lotes recentes",
            "Subir lote",
            "Subir envelope",
            "Abrir lista completa",
            "Imprimir lista filtrada",
            f"Mostrando {contributions_data['total']} de {contributions_data['total']} lancamentos",
        ],
    )
    checks.append(Check("Contribuicoes exibem lista completa e hub de envelopes", "OK" if ok else "FALHA", detail))
    period_data = list_contributions(competencia=latest_competence, limit=10000)
    period_items = period_data["items"]
    period_order = [
        (0 if item.get("group_kind") == "nome" else 1, str(item.get("sort_key") or ""), str(item.get("data_raw") or ""), int(item.get("competencia_ordem") or 0), int(item.get("id") or 0))
        for item in period_items
    ]
    bad_period_name = next(
        (
            str(item.get("nome") or "")
            for item in period_items
            if item.get("group_kind") == "nome" and str(item.get("nome") or "")[:1].isdigit()
        ),
        "",
    )
    checks.append(
        Check(
            "Contribuicoes alfabeticas sem documento na frente",
            "OK" if period_order == sorted(period_order) and not bad_period_name else "FALHA",
            "nomes limpos e documentos separados" if period_order == sorted(period_order) and not bad_period_name else f"problema: {bad_period_name or 'ordem quebrada'}",
        )
    )
    envelope_html = get("/contributions/envelopes/new/")
    ok, detail = _contains_all(
        envelope_html,
        [
            "Registrar envelope",
            "Mes de competencia",
            "Nome do lote",
            "Imagem ou PDF do envelope",
            "Auditoria documental",
            "Previa para digitacao",
            "data-envelope-file-input",
            "Caminho local do arquivo",
            "Tipo principal do envelope",
            "Rastreabilidade financeira",
            "Numero do cheque",
            "NSU / TID",
            "Por padrao usa o total do envelope",
            "data-envelope-zoom-image",
            "lupa de leitura manuscrita",
            "Envelope digitalizado",
            "Envelope conferido manualmente; imagem anexada para auditoria.",
            "Funciona como no rateio",
            "Rateio em cartoes por pessoa, contribuinte e destinacao",
            "Pessoa, contribuinte ou nome lido no envelope",
            "Salvar agora e lancar",
            "Salvar envelope e lancar contribuicoes",
        ],
    )
    if "Forma identificada" in envelope_html:
        ok, detail = False, "formulario voltou a duplicar forma de recebimento na rastreabilidade"
    if 'value="None"' in envelope_html or ">None<" in envelope_html:
        ok, detail = False, "formulario exibiu None em campo de rastreabilidade"
    checks.append(Check("Envelopes mantem auditoria documental", "OK" if ok else "FALHA", detail))
    envelopes_data = list_envelopes()
    envelopes_html = get("/contributions/envelopes/")
    ok, detail = _contains_all(
        envelopes_html,
        [
            "Central de envelopes",
            "Lancamentos",
            "Envelopes",
            "Envelopes ativos",
            "Abrir lista completa",
            "Subir lote",
            "Subir envelope",
            "Imprimir lista",
            f"Mostrando {envelopes_data['total']} de {envelopes_data['total']} envelope(s)",
        ],
    )
    checks.append(Check("Lista de envelopes centraliza operacao e impressao", "OK" if ok else "FALHA", detail))
    receipts_html = get("/receipts/")
    ok, detail = _contains_all(
        receipts_html,
        [
            "Gerar recibo por pessoa",
            "Pesquisar pessoa",
            "Pesquisar recibos",
            "Lista de recibos",
            "Imprimir lista",
            "Envio automatico",
        ],
    )
    checks.append(Check("Recibos exibem busca central e impressao", "OK" if ok else "FALHA", detail))
    receipt_generator_html = get(f"/receipts/?selected_person_id={contribution_person_id}")
    ok, detail = _contains_all(
        receipt_generator_html,
        [
            "Gerar recibos para",
            "Gerar recibos por competencia",
            "Gerar um recibo consolidado do periodo filtrado",
            "E-mail do recibo",
            "Salvar somente o padrao",
            "Gerar e enviar recibo consolidado",
            "Gerar um recibo consolidado",
        ],
    )
    checks.append(Check("Recibos centralizam geracao na mesma tela", "OK" if ok else "FALHA", detail))
    statement_html = get(f"/contributions/statements/{contribution_person_id}/")
    ok, detail = _contains_all(
        statement_html,
        [
            "Extrato de contribuicoes",
            "Gerar PDF",
            "Enviar extrato por e-mail",
            "E-mail atual da ficha",
            "Atualizar a ficha desta pessoa com o destinatario informado acima",
            "Motivo da alteracao de e-mail na ficha",
        ],
    )
    checks.append(Check("Extrato individual permite PDF e envio auditavel", "OK" if ok else "FALHA", detail))
    latest_receipt_row = None
    with connect_legacy() as conn:
        latest_receipt_row = conn.execute("SELECT id FROM recibos ORDER BY id DESC LIMIT 1").fetchone()
    if latest_receipt_row:
        receipt_detail_html = get(f"/receipts/{latest_receipt_row[0]}/")
        ok, detail = _contains_all(
            receipt_detail_html,
            [
                "Logo do cliente",
                "Enviar ou reenviar por e-mail",
                "Abrir PDF do recibo",
                "Imprimir esta tela",
                "Contribuicoes do recibo",
            ],
        )
        checks.append(Check("Detalhe do recibo inclui logo, PDF e reenvio", "OK" if ok else "FALHA", detail))
    email_audit_html = get("/audit/?modo=emails")
    ok, detail = _contains_all(
        email_audit_html,
        [
            "Relatorio de e-mails enviados",
            "Consolida recibos e extratos enviados pelo sistema",
            "Pessoa",
            "Destino",
            "Assunto",
            "Conteudo",
            "E-mails do sistema",
            "Reenviar",
            "Classificacao",
        ],
    )
    checks.append(Check("Auditoria de e-mails exibe relatorio consolidado com filtro e reenvio", "OK" if ok else "FALHA", detail))
    envelope_lot_html = get("/contributions/envelopes/lots/new/")
    ok, detail = _contains_all(
        envelope_lot_html,
        [
            "Criar lote de envelopes",
            "Data padrao sugerida",
            "Upload multiplo de imagens/PDFs",
            "Pasta local no Mac",
            "Envelope digitalizado",
            "Nenhuma contribuicao financeira sera criada",
            "ate 5000 arquivos por lote",
            "file://",
        ],
    )
    checks.append(Check("Lote de envelopes separa organizacao de lancamento financeiro", "OK" if ok else "FALHA", detail))

    report_html = get(f"/reports/?competencia={latest_competence.replace(' ', '%20')}")
    ok, detail = _contains_all(report_html, ["Contribuintes com nome", "report-summary-strip", "summary-pill", "report-table", "remittance-chip", "report-name", "remittance-cell", "<th>Contribuinte</th>", "<th>Vinculo</th>"])
    checks.append(Check("Relatorio por periodo layout nominal", "OK" if ok else "FALHA", detail))
    name_before_link = _index_of(report_html, "<th>Contribuinte</th>") < _index_of(report_html, "<th>Vinculo</th>")
    checks.append(
        Check(
            "Relatorio inicia pelo contribuinte",
            "OK" if name_before_link else "FALHA",
            "coluna Contribuinte aparece antes da sigla/vinculo",
        )
    )

    base_html = (ROOT / "power_church_django" / "templates" / "power_church_django" / "base.html").read_text(encoding="utf-8")
    ok, detail = _contains_all(base_html, ["compact-marker-grid", "compact-button", "remittance-cell", "fit-table", "imports-table", "lot-movements-table", "lot-audit-actions", "report-summary-strip", "summary-pill", "report-table", "remittance-chip", "contributions-table", "subnav-tabs", "subnav-tab", 'onclick="window.print()"', "@media (max-width: 780px)"])
    checks.append(Check("CSS base contem ajustes visuais", "OK" if ok else "FALHA", detail))
    trash_template = (ROOT / "power_church_django" / "templates" / "power_church_django" / "people" / "trash.html").read_text(encoding="utf-8")
    purge_template = (ROOT / "power_church_django" / "templates" / "power_church_django" / "people" / "purge_confirm.html").read_text(encoding="utf-8")
    ok, detail = _contains_all(
        trash_template + purge_template,
        ["Purga final", "Purgar", "Purga bloqueada", "superusuario", "Confirmar purga segura"],
    )
    checks.append(Check("Lixeira segura expoe purga controlada", "OK" if ok else "FALHA", detail))
    return checks


def write_report(checks: list[Check]) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    target = REPORT_DIR / f"contrato_visual_django_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    failed = [check for check in checks if check.failed]
    lines = [
        "# Contrato Visual Django",
        "",
        f"Gerado em: {datetime.now().isoformat(timespec='seconds')}",
        f"Resultado: {'OK' if not failed else 'FALHAS'}",
        "",
        "Este verificador nao compara HTML bruto, porque algumas telas Django melhoraram o fluxo antigo. Ele valida contratos visuais e operacionais que nao podem regredir.",
        "",
        "| Check | Status | Detalhe |",
        "| --- | --- | --- |",
    ]
    for check in checks:
        lines.append(f"| {check.name} | {check.status} | {check.detail.replace('|', '/')} |")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def main() -> int:
    rerun = _run_inside_venv()
    if rerun is not None:
        return rerun
    parser = argparse.ArgumentParser(description="Valida contrato visual/operacional das telas Django.")
    parser.add_argument("--db", default=str(ROOT / "data" / "power_church_membros_importado.db"))
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args()
    checks = build_checks(Path(args.db))
    for check in checks:
        print(f"- {check.status}: {check.name} ({check.detail})")
    if args.report:
        print(f"\nRelatorio: {write_report(checks)}")
    return 1 if any(check.failed for check in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())

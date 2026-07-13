from __future__ import annotations

import argparse
import importlib.util
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "power_church_demo.py"
DB_PATH = ROOT / "data" / "power_church_membros_importado.db"
REPORT_DIR = ROOT / "data" / "homologacao"


def load_app_module():
    root_text = str(ROOT)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    spec = importlib.util.spec_from_file_location("power_church_demo", APP_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


def expect(checks: list[Check], name: str, condition: bool, detail: str = "") -> None:
    checks.append(Check(name, bool(condition), detail))


def build_checks(db_path: Path) -> list[Check]:
    root_text = str(ROOT)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)

    from power_church_core import banking
    from power_church_core import bank_lots
    from power_church_core import bank_parsers
    from power_church_core import contributors
    from power_church_core import designations
    from power_church_core import family
    from power_church_core import formatting
    from power_church_core import matching
    from power_church_core import normalization
    from power_church_core import ocr
    from power_church_core import pdf_text
    from power_church_core import signatures

    checks: list[Check] = []
    app = load_app_module()

    expect(checks, "Normalizacao de nomes", normalization.normalize_match_name("José  da Silva!") == "JOSE DA SILVA")
    expect(checks, "Documento mascarado compativel", normalization.masked_document_matches("***.456.789-**", "12345678901"))
    expect(checks, "CPF valido aceito", normalization.valid_cpf("529.982.247-25"))
    expect(checks, "CPF invalido bloqueado", not normalization.valid_cpf("123.456.789-01"))
    expect(checks, "Tipo documento Santander CPF", normalization.santander_document_type("12345678901") == "cpf")
    expect(checks, "Codigo de centavos", normalization.pix_code_from_amount(4000.12) == "12")
    expect(checks, "Complemento familiar AP equivalente", family.normalize_address_complement("Apto. 1101") == family.normalize_address_complement("1101"))
    expect(checks, "Complemento familiar bloco equivalente", family.normalize_address_complement("Bloco 02 AP 203") == family.normalize_address_complement("AP. 203 BL. 2"))

    expect(checks, "Formatacao de data", formatting.br_date("2026-01-09") == "09/01/2026")
    expect(checks, "Formatacao de moeda", formatting.br_money(5000) == "R$ 5.000,00")
    expect(checks, "Parser de moeda", formatting.parse_money("R$ 5.000,12") == 5000.12)
    expect(checks, "Competencia", formatting.competencia_from_date("2026-02-03") == ("Fevereiro 26", 202602))

    expect(checks, "Codigo conta centavos", designations.cent_rule_plan_account_code("7") == "CENT.07")
    expect(checks, "Codigo tipo centavos", designations.cent_rule_type_code("12") == "CENT_12")
    expect(
        checks,
        "Tipo de centavos gerenciado",
        designations.cent_rule_type_is_system_managed("CENT_12", "12", {"12": "MULHERES"}),
    )
    expect(checks, "Sugestao de regra especial", designations.suggested_type_for_cent_rule({"id": 1}) == "destinacao_especial")
    expect(checks, "Sugestao de dizimo default", designations.suggested_type_for_cent_rule(None) == "dizimo")

    expect(checks, "Layout Santander reconhecido", banking.statement_layout_is_santander("SANTANDER_CONSOLIDADO"))
    expect(checks, "Layout Bradesco suportado", banking.statement_layout_is_supported("BRADESCO_EXTRATO"))
    expect(
        checks,
        "Origem de contribuinte por layout",
        banking.statement_layout_contributor_source("SICOOB_RECEBIMENTOS") == "extrato_sicoob"
        and banking.statement_layout_contributor_source("SICOOB_CONTA_CORRENTE") == "extrato_sicoob",
    )
    expect(
        checks,
        "Nome contribuinte por identidade Santander",
        banking.statement_contributor_name_for_identity("SANTANDER_AUTO", "", "12345678901", "cpf") == "Santander CPF 12345678901",
    )
    expect(
        checks,
        "Mesma titularidade detectada",
        banking.statement_is_same_organization_origin("Primeira Igreja Batista", "Primeira Igreja Batista de Niteroi"),
    )
    expect(checks, "Parser Bradesco prefixo", bank_parsers.bradesco_match_prefix("Transferencia Pix Rem: Maria 09/01") == "Transferencia Pix")
    expect(
        checks,
        "Parser Bradesco periodo",
        bank_parsers.bradesco_period_from_text("01/01/2026 a 31/01/2026") == ("01/01/2026", "31/01/2026"),
    )
    expect(
        checks,
        "Parser Santander periodo",
        bank_parsers.santander_period_from_text("Periodos: 01/04/2026 a 30/04/2026", "SANTANDER_NAO_CONSOLIDADO")
        == ("2026-04-01", "2026-04-30"),
    )
    expect(
        checks,
        "Parser Santander periodo por movimentos",
        bank_parsers.santander_period_from_entries(
            [
                {"received_on": "2026-06-30"},
                {"received_on": "2026-06-01"},
                {"received_on": "2026-06-22"},
            ],
            "2026-06-01",
            "2026-07-13",
        )
        == ("2026-06-01", "2026-06-30"),
    )
    expect(
        checks,
        "Parser Sicoob remetente",
        bank_parsers.sicoob_receiving_extract_source_name(
            "CRED.TR.CT.INTERCRE",
            ["REM.: Maria da Silva", "DOC.: 123"],
        )[0]
        == "Maria da Silva",
    )
    sample_statement_entry = {
        "page_number": 1,
        "order_in_file": 7,
        "received_on": "2026-04-09",
        "competencia": "Abril 26",
        "competencia_ordem": 202604,
        "amount": 4000.12,
        "movement_kind": "pix",
        "receiving_code": "PIX",
        "bank_document": "12345678901",
        "document_type": "cpf",
        "prefix": "Pix Recebido",
        "source_name": "",
        "source_name_normalized": "",
        "origin_label": "Santander CPF 12345678901",
        "detail_text": "CPF 12345678901",
        "raw_text": "09/04 Pix Recebido 12345678901 4.000,12",
    }
    entry_plan = bank_lots.statement_entry_plan("SANTANDER_NAO_CONSOLIDADO", sample_statement_entry)
    expect(checks, "Planejamento lote calcula centavos", entry_plan.cent_code == "12")
    expect(checks, "Planejamento lote calcula fingerprint", len(entry_plan.fingerprint) == 64)
    expect(
        checks,
        "Planejamento lote forca revisao Santander",
        bank_lots.statement_force_person_review("SANTANDER_NAO_CONSOLIDADO", "12345678901", 0, False),
    )

    expect(checks, "CNPJ detectado", contributors.looks_like_cnpj("12.345.678/0001-90"))
    expect(checks, "Tipo contribuinte PJ por documento", contributors.contributor_kind_for_identity("Empresa", "cnpj", "12345678000190") == "pj")
    expect(checks, "Tipo contribuinte PF por nome", contributors.contributor_kind_for_identity("Maria Silva") == "pf")
    expect(checks, "Sigla membro ativo", contributors.contributor_membership_sigla("membro_ativo", 10)[0] == "SA")
    expect(checks, "Sigla sem vinculo", contributors.contributor_membership_sigla("membro_ativo", 0)[0] == "NR")

    pix_sig = signatures.pix_global_signature("2026-01-01", 100, "MARIA", "***.456.789-**", "cpf", "abc")
    pix_sig_again = signatures.pix_global_signature("2026-01-01", 100, "MARIA", "***.456.789-**", "cpf", "abc")
    stmt_sig = signatures.statement_global_signature("sicoob", "2026-01-01", 100, "MARIA", "pix", "123", "abc")
    expect(checks, "Assinatura PIX deterministica", len(pix_sig) == 64 and pix_sig == pix_sig_again)
    expect(checks, "Assinatura por origem difere", pix_sig != stmt_sig)

    people_cache = [
        {
            "id": 1,
            "nome": "Maria Jose Silva",
            "name_norm": normalization.normalize_match_name("Maria Jose Silva"),
            "status": "membro_ativo",
            "identifiers": [{"kind": "cpf", "value": "12345678901", "source_name": "Maria Jose Silva"}],
            "financial_aliases": [
                {
                    "name": "Maria J Silva",
                    "name_norm": normalization.normalize_match_name("Maria J Silva"),
                    "alias_kind": "financeiro",
                }
            ],
        },
        {
            "id": 2,
            "nome": "Joao Pereira",
            "name_norm": normalization.normalize_match_name("Joao Pereira"),
            "status": "membro_ativo",
            "identifiers": [{"kind": "cpf", "value": "98765432100", "source_name": "Joao Pereira"}],
            "financial_aliases": [],
        },
    ]
    match = matching.match_pix_entry("Maria J Silva", "***.456.789-**", "cpf", people_cache)
    suggestions = matching.pix_candidate_suggestions("Maria J Silva", "***.456.789-**", "cpf", people_cache)
    company_match = matching.match_pix_entry("Doxa Treinamento Ltda", "", "cnpj", [])
    expect(checks, "Motor matching escolhe alias/documento", match["person_id"] == 1 and match["confidence"] == "forte_doc_nome")
    expect(checks, "Motor matching sugere candidato", bool(suggestions) and suggestions[0]["id"] == 1)
    expect(checks, "Motor matching classifica PJ externo", company_match["confidence"] == "pj_ou_externo")

    expect(checks, "Fachada app formatacao", app.br_money(5000) == formatting.br_money(5000))
    expect(
        checks,
        "Fachada app assinatura PIX",
        app.pix_global_signature("2026-01-01", 100, "MARIA", "***.456.789-**", "cpf", "abc") == pix_sig,
    )
    expect(checks, "Fachada app contribuinte PJ", app.contributor_kind_for_identity("Empresa", "cnpj", "12345678000190") == "pj")
    expect(checks, "Fachada app layout", app.statement_layout_label("BRADESCO_EXTRATO") == banking.statement_layout_label("BRADESCO_EXTRATO"))
    expect(
        checks,
        "Fachada app parser bancario",
        app.bradesco_match_prefix("Transferencia Pix Rem: Maria 09/01") == bank_parsers.bradesco_match_prefix("Transferencia Pix Rem: Maria 09/01"),
    )
    expect(checks, "Fachada app planejamento lote", app.slugify_filename_text("Extrato Março.pdf") == bank_lots.slugify_filename_text("Extrato Março.pdf"))
    expect(checks, "Fachada app PDF", app.extract_pdf_pages.__module__ == "power_church_demo" and callable(pdf_text.extract_pdf_pages))
    statuses = {status.code: status for status in pdf_text.provider_statuses()}
    active_provider = pdf_text.active_provider_code()
    expect(checks, "Adaptador PDF lista provedores", {"swift_pdfkit", "pymupdf"}.issubset(statuses))
    expect(checks, "Adaptador PDF tem provedor ativo", active_provider in {status.code for status in statuses.values() if status.available}, active_provider)
    ocr_status = ocr.tesseract_status()
    expect(checks, "Motor OCR Tesseract disponivel", ocr_status.available, ocr_status.detail)
    expect(checks, "Motor OCR portugues", ocr_status.portuguese_available, ocr_status.command)

    app_text = APP_PATH.read_text(encoding="utf-8", errors="replace")
    expect(checks, "App sem Swift direto", "PDFKit" not in app_text and '"swift"' not in app_text and "subprocess.run" not in app_text)

    db = app.PowerChurchDB(db_path)
    try:
        organization_id = db.default_organization_id()
        people_for_match = db.people_for_pix_matching(organization_id)
        sample = db.conn.execute(
            """
            SELECT ct.nome, ct.documento_principal, ct.documento_tipo, ct.pessoa_id
            FROM contribuintes ct
            JOIN pessoas p ON p.id = ct.pessoa_id
            WHERE ct.ativo = 1
              AND p.ativo = 1
              AND TRIM(COALESCE(ct.nome, '')) <> ''
            ORDER BY
              CASE WHEN TRIM(COALESCE(ct.documento_principal, '')) <> '' THEN 0 ELSE 1 END,
              ct.id
            LIMIT 1
            """
        ).fetchone()
        if sample is None:
            checks.append(Check("Fachada DB matching real", True, "sem amostra vinculada"))
        else:
            db_match = db.match_pix_entry(
                organization_id,
                str(sample["nome"] or ""),
                str(sample["documento_principal"] or ""),
                str(sample["documento_tipo"] or ""),
                people_cache=people_for_match,
            )
            db_suggestions = db.pix_candidate_suggestions(
                organization_id,
                str(sample["nome"] or ""),
                str(sample["documento_principal"] or ""),
                str(sample["documento_tipo"] or ""),
                people_cache=people_for_match,
                limit=5,
            )
            expected_id = int(sample["pessoa_id"] or 0)
            suggestion_ids = {int(item["id"] or 0) for item in db_suggestions}
            expect(
                checks,
                "Fachada DB matching real",
                int(db_match.get("person_id") or 0) == expected_id or expected_id in suggestion_ids,
                f"amostra {sample['nome']}",
            )
    finally:
        db.close()

    return checks


def write_report(checks: list[Check]) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    target = REPORT_DIR / f"funcionalidade_transicao_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    failed = [check for check in checks if not check.ok]
    lines = [
        "# Funcionalidade Da Transicao",
        "",
        f"Gerado em: {datetime.now().isoformat(timespec='seconds')}",
        f"Resultado: {'OK' if not failed else 'FALHAS'}",
        "",
        "| Check | Status | Detalhe |",
        "| --- | --- | --- |",
    ]
    for check in checks:
        lines.append(f"| {check.name} | {'OK' if check.ok else 'FALHA'} | {check.detail} |")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="Verifica funcionalidade dos nucleos migrados na transicao.")
    parser.add_argument("--db", default=str(DB_PATH), help="Caminho do banco SQLite.")
    parser.add_argument("--report", action="store_true", help="Grava relatorio markdown em data/homologacao.")
    args = parser.parse_args()
    checks = build_checks(Path(args.db))
    for check in checks:
        print(f"- {'OK' if check.ok else 'FALHA'}: {check.name}" + (f" ({check.detail})" if check.detail else ""))
    if args.report:
        report = write_report(checks)
        print(f"\nRelatorio: {report}")
    return 1 if any(not check.ok for check in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())

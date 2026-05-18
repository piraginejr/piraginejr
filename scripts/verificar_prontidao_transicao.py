from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "power_church_membros_importado.db"
REPORT_DIR = ROOT / "data" / "homologacao"
APP_PATH = ROOT / "power_church_demo.py"


@dataclass
class Check:
    name: str
    status: str
    detail: str

    @property
    def ok(self) -> bool:
        return self.status != "FALHA"


def scalar(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row[0] or 0) if row else 0


def latest_stability_report() -> Path | None:
    reports = sorted(REPORT_DIR.glob("verificacao_estabilidade_*.md"))
    return reports[-1] if reports else None


def file_line_count(path: Path) -> int:
    if not path.exists():
        return 0
    return len(path.read_text(encoding="utf-8", errors="replace").splitlines())


def format_mb(size_bytes: int) -> str:
    return f"{size_bytes / (1024 * 1024):.1f} MB"


def bank_zero_value_detail(conn: sqlite3.Connection) -> tuple[int, str]:
    count = scalar(
        conn,
        """
        SELECT COUNT(*)
          FROM contribuicoes
         WHERE ativo = 1
           AND valor <= 0
           AND (pix_movimento_id IS NOT NULL OR extrato_movimento_id IS NOT NULL)
        """,
    )
    if count == 0:
        return 0, "0 lancamento(s) bancario(s) ativo(s) com valor <= 0"
    rows = conn.execute(
        """
        SELECT c.id AS contribuicao_id, c.data_recebimento AS data_ref, c.valor,
               'PIX' AS origem, pm.lote_id, 'SICOOB_PIX' AS banco,
               pm.id AS movimento_id, pm.nome_origem AS remetente
          FROM contribuicoes c
          JOIN pix_movimentos pm ON pm.id = c.pix_movimento_id
         WHERE c.ativo = 1 AND c.valor <= 0
        UNION ALL
        SELECT c.id AS contribuicao_id, COALESCE(c.data_recebimento, em.data_movimento) AS data_ref, c.valor,
               'EXTRATO' AS origem, em.lote_id, COALESCE(el.banco, el.layout_codigo, 'Extrato') AS banco,
               em.id AS movimento_id, COALESCE(em.nome_origem, em.origin_label, em.raw_text) AS remetente
          FROM contribuicoes c
          JOIN extrato_movimentos em ON em.id = c.extrato_movimento_id
          LEFT JOIN extrato_lotes el ON el.id = em.lote_id
         WHERE c.ativo = 1 AND c.valor <= 0
         LIMIT 5
        """
    ).fetchall()
    samples = [
        f"{row['origem']} lote {row['lote_id']} mov {row['movimento_id']} contrib {row['contribuicao_id']} "
        f"{row['data_ref'] or '-'} {row['banco'] or '-'} {row['remetente'] or '-'}"
        for row in rows
    ]
    return count, f"{count} lancamento(s) bancario(s) ativo(s) com valor <= 0: " + " ; ".join(samples)


def build_checks(db_path: Path) -> list[Check]:
    checks: list[Check] = []
    required_docs = [
        "PLANO_HOSPEDAGEM_MIGRACAO_E_OCR_V1.md",
        "PLANO_MIGRACAO_DJANGO_V1.md",
        "GUIA_IMPORTACOES_BANCARIAS_V1.md",
        "MATRIZ_HOMOLOGACAO_V1.md",
        "PLANO_TRANSICAO_POWER_CHURCH_V1.md",
    ]
    missing_docs = [name for name in required_docs if not (ROOT / name).exists()]
    checks.append(
        Check(
            "Documentacao minima de transicao",
            "OK" if not missing_docs else "FALHA",
            "todos os documentos existem" if not missing_docs else "faltando: " + ", ".join(missing_docs),
        )
    )
    functionality_scripts = [
        ROOT / "scripts" / "verificar_dependencias_servidor.py",
        ROOT / "scripts" / "verificar_funcionalidade_transicao.py",
        ROOT / "scripts" / "verificar_extratores_pdf.py",
        ROOT / "scripts" / "verificar_fixtures_pdf_bancos.py",
        ROOT / "scripts" / "verificar_pacote_instalacao.py",
        ROOT / "scripts" / "verificar_prontidao_django.py",
        ROOT / "scripts" / "verificar_funcionalidade_total.py",
    ]
    missing_functionality_scripts = [str(path.relative_to(ROOT)) for path in functionality_scripts if not path.exists()]
    checks.append(
        Check(
            "Scripts de funcionalidade da transicao",
            "OK" if not missing_functionality_scripts else "FALHA",
            "scripts presentes" if not missing_functionality_scripts else "faltando: " + ", ".join(missing_functionality_scripts),
        )
    )
    core_files = [
        ROOT / "power_church_core" / "__init__.py",
        ROOT / "power_church_core" / "banking.py",
        ROOT / "power_church_core" / "bank_lots.py",
        ROOT / "power_church_core" / "bank_parsers.py",
        ROOT / "power_church_core" / "contributors.py",
        ROOT / "power_church_core" / "designations.py",
        ROOT / "power_church_core" / "formatting.py",
        ROOT / "power_church_core" / "normalization.py",
        ROOT / "power_church_core" / "matching.py",
        ROOT / "power_church_core" / "pdf_text.py",
        ROOT / "power_church_core" / "signatures.py",
    ]
    missing_core_files = [str(path.relative_to(ROOT)) for path in core_files if not path.exists()]
    checks.append(
        Check(
            "Nucleo reutilizavel iniciado",
            "OK" if not missing_core_files else "FALHA",
            "power_church_core presente" if not missing_core_files else "faltando: " + ", ".join(missing_core_files),
        )
    )

    report = latest_stability_report()
    if report is None:
        checks.append(Check("Ultima estabilidade", "FALHA", "nenhum relatorio encontrado"))
    else:
        text = report.read_text(encoding="utf-8", errors="replace")
        status = "OK" if "Resultado: OK" in text else "FALHA"
        checks.append(Check("Ultima estabilidade", status, str(report)))

    if not db_path.exists():
        checks.append(Check("Banco atual", "FALHA", f"nao encontrado: {db_path}"))
        return checks
    checks.append(Check("Banco atual", "OK", f"{db_path} · {format_mb(db_path.stat().st_size)}"))

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        people = scalar(conn, "SELECT COUNT(*) FROM pessoas WHERE ativo = 1")
        contributors = scalar(conn, "SELECT COUNT(*) FROM contribuintes WHERE ativo = 1")
        contributions = scalar(conn, "SELECT COUNT(*) FROM contribuicoes WHERE ativo = 1")
        statement_lots = scalar(conn, "SELECT COUNT(*) FROM extrato_lotes")
        pix_lots = scalar(conn, "SELECT COUNT(*) FROM pix_lotes")
        checks.append(
            Check(
                "Massa de dados principal",
                "OK" if people and contributors and contributions else "FALHA",
                f"{people} pessoas, {contributors} contribuintes, {contributions} contribuicoes, {statement_lots} extratos, {pix_lots} PIX",
            )
        )

        unnamed_incremental = scalar(
            conn,
            """
            SELECT COUNT(*)
            FROM import_linhas il
            JOIN import_lotes lote ON lote.id = il.lote_id
            JOIN pessoas p ON p.id = il.registro_id
            WHERE lote.tipo_importacao = 'pessoas_complementar_incremental'
              AND p.ativo = 1
              AND p.nome = 'Nome nao informado'
              AND (
                il.dados_originais_json LIKE '%"nome":%'
                OR il.dados_originais_json LIKE '%"Nome Completo":%'
                OR il.dados_originais_json LIKE '%"Nome completo":%'
              )
            """,
        )
        checks.append(
            Check(
                "Importacao complementar sem perda de nomes",
                "OK" if unnamed_incremental == 0 else "FALHA",
                f"{unnamed_incremental} ficha(s) complementares sem nome",
            )
        )

        zero_value_contributions = scalar(conn, "SELECT COUNT(*) FROM contribuicoes WHERE ativo = 1 AND valor <= 0")
        checks.append(
            Check(
                "Contribuicoes ativas com valor valido",
                "OK" if zero_value_contributions == 0 else "FALHA",
                f"{zero_value_contributions} lancamento(s) com valor <= 0",
            )
        )
        bank_zero_count, bank_zero_detail = bank_zero_value_detail(conn)
        checks.append(
            Check(
                "Contribuicoes bancarias com valor valido",
                "OK" if bank_zero_count == 0 else "FALHA",
                bank_zero_detail,
            )
        )

        unlinked_contributions = scalar(
            conn,
            "SELECT COUNT(*) FROM contribuicoes WHERE ativo = 1 AND COALESCE(pessoa_id, 0) = 0",
        )
        checks.append(
            Check(
                "Fila de saneamento preservada",
                "OK",
                f"{unlinked_contributions} contribuicao(oes) sem pessoa continuam rastreaveis no contribuinte auxiliar",
            )
        )
    finally:
        conn.close()

    app_text = APP_PATH.read_text(encoding="utf-8", errors="replace") if APP_PATH.exists() else ""
    checks.append(
        Check(
            "App consome nucleo de normalizacao",
            "OK" if "from power_church_core import normalization as core_normalization" in app_text else "FALHA",
            "normalizacao delegada ao power_church_core"
            if "from power_church_core import normalization as core_normalization" in app_text
            else "import do nucleo nao encontrado no app",
        )
    )
    checks.append(
        Check(
            "App consome nucleo de formatacao",
            "OK"
            if "from power_church_core import formatting as core_formatting" in app_text
            and "core_formatting.competencia_from_date" in app_text
            and "core_formatting.parse_money" in app_text
            else "FALHA",
            "datas, moeda e competencia delegadas ao power_church_core"
            if "from power_church_core import formatting as core_formatting" in app_text
            and "core_formatting.competencia_from_date" in app_text
            and "core_formatting.parse_money" in app_text
            else "delegacao de formatacao nao encontrada no app",
        )
    )
    checks.append(
        Check(
            "App consome nucleo de matching",
            "OK"
            if "from power_church_core import matching as core_matching" in app_text
            and "core_matching.match_pix_entry" in app_text
            and "core_matching.pix_candidate_suggestions" in app_text
            else "FALHA",
            "motor de associacao delegado ao power_church_core"
            if "from power_church_core import matching as core_matching" in app_text
            and "core_matching.match_pix_entry" in app_text
            and "core_matching.pix_candidate_suggestions" in app_text
            else "delegacao de matching nao encontrada no app",
        )
    )
    checks.append(
        Check(
            "App consome nucleo de destinacoes",
            "OK"
            if "from power_church_core import designations as core_designations" in app_text
            and "core_designations.cent_rule_plan_account_code" in app_text
            and "core_designations.suggested_type_for_cent_rule" in app_text
            else "FALHA",
            "regras puras de centavos delegadas ao power_church_core"
            if "from power_church_core import designations as core_designations" in app_text
            and "core_designations.cent_rule_plan_account_code" in app_text
            and "core_designations.suggested_type_for_cent_rule" in app_text
            else "delegacao de destinacoes nao encontrada no app",
        )
    )
    checks.append(
        Check(
            "App consome nucleo de contribuintes",
            "OK"
            if "from power_church_core import contributors as core_contributors" in app_text
            and "core_contributors.contributor_kind_for_identity" in app_text
            and "core_contributors.contributor_membership_sigla" in app_text
            else "FALHA",
            "tipo de contribuinte e siglas delegados ao power_church_core"
            if "from power_church_core import contributors as core_contributors" in app_text
            and "core_contributors.contributor_kind_for_identity" in app_text
            and "core_contributors.contributor_membership_sigla" in app_text
            else "delegacao de contribuintes nao encontrada no app",
        )
    )
    checks.append(
        Check(
            "App consome nucleo de assinaturas",
            "OK"
            if "from power_church_core import signatures as core_signatures" in app_text
            and "core_signatures.pix_global_signature" in app_text
            and "core_signatures.statement_global_signature" in app_text
            else "FALHA",
            "assinaturas de duplicidade delegadas ao power_church_core"
            if "from power_church_core import signatures as core_signatures" in app_text
            and "core_signatures.pix_global_signature" in app_text
            and "core_signatures.statement_global_signature" in app_text
            else "delegacao de assinaturas nao encontrada no app",
        )
    )
    checks.append(
        Check(
            "App consome nucleo bancario",
            "OK"
            if "from power_church_core import banking as core_banking" in app_text
            and "core_banking.statement_layout_label" in app_text
            and "core_banking.statement_contributor_name_for_identity" in app_text
            else "FALHA",
            "contrato puro de layouts bancarios delegado ao power_church_core"
            if "from power_church_core import banking as core_banking" in app_text
            and "core_banking.statement_layout_label" in app_text
            and "core_banking.statement_contributor_name_for_identity" in app_text
            else "delegacao bancaria nao encontrada no app",
        )
    )
    checks.append(
        Check(
            "App consome parsers bancarios",
            "OK"
            if "from power_church_core import bank_parsers as core_bank_parsers" in app_text
            and "core_bank_parsers.parse_statement_pdf_by_layout" in app_text
            and "core_bank_parsers.bradesco_match_prefix" in app_text
            else "FALHA",
            "parsers de extrato delegados ao power_church_core"
            if "from power_church_core import bank_parsers as core_bank_parsers" in app_text
            and "core_bank_parsers.parse_statement_pdf_by_layout" in app_text
            and "core_bank_parsers.bradesco_match_prefix" in app_text
            else "delegacao de parsers bancarios nao encontrada no app",
        )
    )
    checks.append(
        Check(
            "App consome planejamento de lotes bancarios",
            "OK"
            if "from power_church_core import bank_lots as core_bank_lots" in app_text
            and "core_bank_lots.statement_entry_plan" in app_text
            and "core_bank_lots.statement_reprocess_plan" in app_text
            else "FALHA",
            "planejamento de lote bancario delegado ao power_church_core"
            if "from power_church_core import bank_lots as core_bank_lots" in app_text
            and "core_bank_lots.statement_entry_plan" in app_text
            and "core_bank_lots.statement_reprocess_plan" in app_text
            else "delegacao do planejamento de lote nao encontrada no app",
        )
    )
    checks.append(
        Check(
            "App consome adaptador de PDF",
            "OK"
            if "from power_church_core import pdf_text as core_pdf_text" in app_text
            and "core_pdf_text.extract_pdf_pages" in app_text
            and "core_pdf_text.extract_pdf_line_selections" in app_text
            else "FALHA",
            "extracao de PDF isolada em adaptador"
            if "from power_church_core import pdf_text as core_pdf_text" in app_text
            and "core_pdf_text.extract_pdf_pages" in app_text
            and "core_pdf_text.extract_pdf_line_selections" in app_text
            else "adaptador de PDF nao encontrado no app",
        )
    )
    pdf_adapter_text = (ROOT / "power_church_core" / "pdf_text.py").read_text(encoding="utf-8", errors="replace")
    swift_refs = pdf_adapter_text.count("PDFKit") + pdf_adapter_text.count('"swift"')
    checks.append(
        Check(
            "Dependencia de PDF portavel",
            "ALERTA" if swift_refs else "OK",
            f"{swift_refs} referencia(s) a Swift/PDFKit no adaptador atual"
            if swift_refs
            else "sem referencias diretas a Swift/PDFKit",
        )
    )
    deploy_files = [
        ROOT / "Dockerfile",
        ROOT / "Dockerfile.django",
        ROOT / "docker-compose.yml",
        ROOT / "docker-compose.django.yml",
        ROOT / "deploy" / "backup_sqlite.sh",
        ROOT / "deploy" / "restore_sqlite.sh",
    ]
    missing_deploy_files = [str(path.relative_to(ROOT)) for path in deploy_files if not path.exists()]
    checks.append(
        Check(
            "Pacote de instalacao repetivel",
            "OK" if not missing_deploy_files else "FALHA",
            "Docker, compose, backup e restore presentes"
            if not missing_deploy_files
            else "faltando: " + ", ".join(missing_deploy_files),
        )
    )
    app_env_terms = ["POWER_CHURCH_DB_PATH", "POWER_CHURCH_HOST", "POWER_CHURCH_PORT"]
    missing_env_terms = [term for term in app_env_terms if term not in app_text]
    checks.append(
        Check(
            "Configuracao por ambiente",
            "OK" if not missing_env_terms else "FALHA",
            "host, porta e banco parametrizados"
            if not missing_env_terms
            else "faltando: " + ", ".join(missing_env_terms),
        )
    )
    app_lines = file_line_count(APP_PATH)
    checks.append(
        Check(
            "Tamanho do arquivo principal",
            "ALERTA" if app_lines > 8000 else "OK",
            f"{app_lines} linhas em power_church_demo.py",
        )
    )
    stability_text = (ROOT / "scripts" / "verificar_estabilidade_demo.py").read_text(encoding="utf-8", errors="replace")
    guard_terms = [
        "Associacoes por novos cadastros",
        "Importacao complementar preserva nomes",
        "Auditoria lote pessoas",
        "Extrato permite trocar centavos 12 para Dizimo",
        "Motor de matching reutilizavel",
    ]
    missing_guards = [term for term in guard_terms if term not in stability_text]
    checks.append(
        Check(
            "Guarda de regressao operacional",
            "OK" if not missing_guards else "FALHA",
            "checks criticos presentes" if not missing_guards else "faltando: " + ", ".join(missing_guards),
        )
    )
    return checks


def write_report(checks: list[Check]) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    target = REPORT_DIR / f"prontidao_transicao_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    failed = [check for check in checks if check.status == "FALHA"]
    alerts = [check for check in checks if check.status == "ALERTA"]
    lines = [
        "# Prontidao De Transicao",
        "",
        f"Gerado em: {datetime.now().isoformat(timespec='seconds')}",
        f"Resultado: {'OK COM ALERTAS' if alerts and not failed else 'OK' if not failed else 'FALHAS'}",
        "",
        "| Check | Status | Detalhe |",
        "| --- | --- | --- |",
    ]
    for check in checks:
        lines.append(f"| {check.name} | {check.status} | {check.detail} |")
    lines.extend(
        [
            "",
            "## Leitura",
            "",
            "- `FALHA` bloqueia a transicao ate ser corrigida.",
            "- `ALERTA` nao bloqueia, mas indica trabalho obrigatorio das proximas fases.",
            "- A dependencia de Swift/PDFKit e esperada nesta fase, mas deve ser substituida antes de Linux/nuvem.",
        ]
    )
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="Verifica prontidao para iniciar a transicao arquitetural.")
    parser.add_argument("--db", default=str(DB_PATH), help="Caminho do banco SQLite.")
    parser.add_argument("--report", action="store_true", help="Grava relatorio markdown em data/homologacao.")
    args = parser.parse_args()
    checks = build_checks(Path(args.db))
    for check in checks:
        print(f"- {check.status}: {check.name} ({check.detail})")
    if args.report:
        report = write_report(checks)
        print(f"\nRelatorio: {report}")
    return 1 if any(check.status == "FALHA" for check in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())

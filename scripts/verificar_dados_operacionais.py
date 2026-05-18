from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "power_church_membros_importado.db"
REPORT_DIR = ROOT / "data" / "homologacao"


@dataclass
class Check:
    name: str
    status: str
    detail: str

    @property
    def failed(self) -> bool:
        return self.status == "FALHA"


def scalar(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> Any:
    row = conn.execute(sql, params).fetchone()
    return row[0] if row else None


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return bool(row)


def column_exists(conn: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    return any(str(row["name"]) == column_name for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall())


def sample_rows(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = (), limit: int = 5) -> str:
    rows = conn.execute(sql, (*params, limit)).fetchall()
    if not rows:
        return ""
    samples: list[str] = []
    for row in rows:
        parts = [f"{key}={row[key]}" for key in row.keys()]
        samples.append("{" + ", ".join(parts) + "}")
    return " amostras: " + " ; ".join(samples)


def count_check(
    conn: sqlite3.Connection,
    name: str,
    sql: str,
    expected: int = 0,
    sample_sql: str = "",
    params: tuple[Any, ...] = (),
) -> Check:
    count = int(scalar(conn, sql, params) or 0)
    status = "OK" if count == expected else "FALHA"
    detail = f"{count} ocorrencia(s)"
    if status == "FALHA" and sample_sql:
        detail += sample_rows(conn, sample_sql, params)
    return Check(name, status, detail)


def build_checks(db_path: Path) -> list[Check]:
    checks: list[Check] = []
    if not db_path.exists():
        return [Check("Banco SQLite", "FALHA", f"nao encontrado: {db_path}")]

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        integrity = str(scalar(conn, "PRAGMA integrity_check") or "")
        checks.append(Check("Integridade fisica SQLite", "OK" if integrity == "ok" else "FALHA", integrity))
        checks.append(
            count_check(
                conn,
                "Contribuicoes bancarias com valor valido",
                """
                SELECT COUNT(*)
                  FROM contribuicoes
                 WHERE ativo = 1
                   AND valor <= 0
                   AND (pix_movimento_id IS NOT NULL OR extrato_movimento_id IS NOT NULL)
                """,
                sample_sql="""
                SELECT id, data_recebimento, competencia, valor, pix_movimento_id, extrato_movimento_id
                  FROM contribuicoes
                 WHERE ativo = 1
                   AND valor <= 0
                   AND (pix_movimento_id IS NOT NULL OR extrato_movimento_id IS NOT NULL)
                 LIMIT ?
                """,
            )
        )
        checks.append(
            count_check(
                conn,
                "Movimentos PIX ativos com valor valido",
                """
                SELECT COUNT(*)
                  FROM pix_movimentos
                 WHERE ativo = 1
                   AND COALESCE(review_status, '') NOT IN ('ignorado', 'duplicado')
                   AND valor <= 0
                """,
                sample_sql="""
                SELECT id, lote_id, data_recebimento, valor, nome_origem, review_status
                  FROM pix_movimentos
                 WHERE ativo = 1
                   AND COALESCE(review_status, '') NOT IN ('ignorado', 'duplicado')
                   AND valor <= 0
                 LIMIT ?
                """,
            )
        )
        checks.append(
            count_check(
                conn,
                "Movimentos de extrato ativos com valor valido",
                """
                SELECT COUNT(*)
                  FROM extrato_movimentos
                 WHERE ativo = 1
                   AND COALESCE(review_status, '') NOT IN ('ignorado', 'duplicado')
                   AND valor <= 0
                """,
                sample_sql="""
                SELECT id, lote_id, data_movimento, valor, nome_origem, origin_label, review_status
                  FROM extrato_movimentos
                 WHERE ativo = 1
                   AND COALESCE(review_status, '') NOT IN ('ignorado', 'duplicado')
                   AND valor <= 0
                 LIMIT ?
                """,
            )
        )
        checks.append(
            count_check(
                conn,
                "Movimentos de extrato sem cabecalho como remetente",
                """
                SELECT COUNT(*)
                  FROM extrato_movimentos
                 WHERE ativo = 1
                   AND UPPER(REPLACE(REPLACE(REPLACE(nome_origem, 'ó', 'o'), 'Ó', 'O'), 'í', 'i')) LIKE '%DATA DOCUMENTO HIST%VALOR%'
                """,
                sample_sql="""
                SELECT id, lote_id, ordem_no_lote, pagina, data_movimento, valor, nome_origem, bank_document
                  FROM extrato_movimentos
                 WHERE ativo = 1
                   AND UPPER(REPLACE(REPLACE(REPLACE(nome_origem, 'ó', 'o'), 'Ó', 'O'), 'í', 'i')) LIKE '%DATA DOCUMENTO HIST%VALOR%'
                 LIMIT ?
                """,
            )
        )
        lote_sicoob_01a11 = scalar(
            conn,
            """
            SELECT id
              FROM extrato_lotes
             WHERE banco = 'Sicoob'
               AND nome_arquivo = 'sicoob_01a11.pdf'
             ORDER BY id DESC
             LIMIT 1
            """,
        )
        if lote_sicoob_01a11:
            checks.append(
                count_check(
                    conn,
                    "Sentinela Sicoob DOXA 08/05/2026",
                    """
                    SELECT COUNT(*)
                      FROM contribuicoes c
                      JOIN extrato_movimentos em ON em.id = c.extrato_movimento_id
                     WHERE c.ativo = 1
                       AND em.ativo = 1
                       AND em.lote_id = ?
                       AND c.pessoa_id = 1078
                       AND c.contribuinte_id = 781
                       AND c.data_recebimento = '2026-05-08'
                       AND ABS(ROUND(c.valor, 2) - 4000.00) <= 0.009
                       AND ABS(ROUND(em.valor, 2) - 4000.00) <= 0.009
                       AND UPPER(COALESCE(em.nome_origem, '')) LIKE '%DOXA%'
                       AND COALESCE(em.bank_document, '') LIKE '%52.033.084%'
                    """,
                    expected=1,
                    sample_sql="""
                    SELECT c.id AS contribuicao_id, em.id AS movimento_id, em.lote_id, c.data_recebimento,
                           c.valor AS valor_contribuicao, em.valor AS valor_movimento, em.nome_origem, em.bank_document
                      FROM contribuicoes c
                      JOIN extrato_movimentos em ON em.id = c.extrato_movimento_id
                     WHERE em.lote_id = ?
                     LIMIT ?
                    """,
                    params=(int(lote_sicoob_01a11),),
                )
            )
            checks.append(
                count_check(
                    conn,
                    "Sentinela lote Sicoob 01a11 total reparado",
                    """
                    SELECT COUNT(*)
                      FROM extrato_lotes
                     WHERE id = ?
                       AND total_movimentos = 352
                       AND ABS(ROUND(total_valor, 2) - 232892.91) <= 0.009
                    """,
                    expected=1,
                    sample_sql="""
                    SELECT id, banco, layout_codigo, nome_arquivo, total_movimentos, total_valor, status
                      FROM extrato_lotes
                     WHERE id = ?
                     LIMIT ?
                    """,
                    params=(int(lote_sicoob_01a11),),
                )
            )
        checks.append(
            count_check(
                conn,
                "Contribuintes auxiliares sem cabecalho bancario como nome",
                """
                SELECT COUNT(*)
                  FROM contribuintes
                 WHERE ativo = 1
                   AND UPPER(REPLACE(REPLACE(REPLACE(nome, 'ó', 'o'), 'Ó', 'O'), 'í', 'i')) LIKE '%DATA DOCUMENTO HIST%VALOR%'
                """,
                sample_sql="""
                SELECT id, nome, documento_principal, origem, pessoa_id
                  FROM contribuintes
                 WHERE ativo = 1
                   AND UPPER(REPLACE(REPLACE(REPLACE(nome, 'ó', 'o'), 'Ó', 'O'), 'í', 'i')) LIKE '%DATA DOCUMENTO HIST%VALOR%'
                 LIMIT ?
                """,
            )
        )
        reference_checks = [
            (
                "Contribuicoes sem pessoa orfa",
                """
                SELECT COUNT(*)
                  FROM contribuicoes c
                  LEFT JOIN pessoas p ON p.id = c.pessoa_id
                 WHERE c.ativo = 1 AND c.pessoa_id IS NOT NULL AND p.id IS NULL
                """,
            ),
            (
                "Contribuicoes sem contribuinte orfao",
                """
                SELECT COUNT(*)
                  FROM contribuicoes c
                  LEFT JOIN contribuintes ct ON ct.id = c.contribuinte_id
                 WHERE c.ativo = 1 AND c.contribuinte_id IS NOT NULL AND ct.id IS NULL
                """,
            ),
            (
                "Contribuicoes sem tipo orfao",
                """
                SELECT COUNT(*)
                  FROM contribuicoes c
                  LEFT JOIN tipos_contribuicao t ON t.id = c.tipo_contribuicao_id
                 WHERE c.ativo = 1 AND t.id IS NULL
                """,
            ),
            (
                "Contribuicoes sem movimento PIX orfao",
                """
                SELECT COUNT(*)
                  FROM contribuicoes c
                  LEFT JOIN pix_movimentos pm ON pm.id = c.pix_movimento_id
                 WHERE c.ativo = 1 AND c.pix_movimento_id IS NOT NULL AND pm.id IS NULL
                """,
            ),
            (
                "Contribuicoes sem movimento de extrato orfao",
                """
                SELECT COUNT(*)
                  FROM contribuicoes c
                  LEFT JOIN extrato_movimentos em ON em.id = c.extrato_movimento_id
                 WHERE c.ativo = 1 AND c.extrato_movimento_id IS NOT NULL AND em.id IS NULL
                """,
            ),
        ]
        for name, sql in reference_checks:
            checks.append(count_check(conn, name, sql))
        if table_exists(conn, "envelopes") and table_exists(conn, "envelope_itens"):
            traceability_columns = [
                "rastreio_forma_identificada",
                "rastreio_banco_operadora",
                "rastreio_numero_cheque",
                "rastreio_numero_operacao",
                "rastreio_nsu_tid",
                "rastreio_ultimos_digitos_cartao",
                "rastreio_data_operacao",
                "rastreio_valor_operacao",
                "rastreio_status_conciliacao",
                "rastreio_observacoes",
            ]
            missing_traceability_columns = [
                column for column in traceability_columns if not column_exists(conn, "envelopes", column)
            ]
            checks.append(
                Check(
                    "Rastreabilidade financeira de envelopes preparada",
                    "OK" if not missing_traceability_columns else "FALHA",
                    "colunas presentes" if not missing_traceability_columns else "faltando: " + ", ".join(missing_traceability_columns),
                )
            )
            checks.append(
                count_check(
                    conn,
                    "Envelopes com imagem auditavel",
                    """
                    SELECT COUNT(*)
                      FROM envelopes
                     WHERE ativo = 1
                       AND (
                            COALESCE(caminho_imagem, '') = ''
                            OR COALESCE(imagem_hash, '') = ''
                            OR COALESCE(nome_arquivo_original, '') = ''
                       )
                    """,
                    sample_sql="""
                    SELECT id, lote_id, competencia, data_recebimento, total_informado, caminho_imagem, imagem_hash
                      FROM envelopes
                     WHERE ativo = 1
                       AND (
                            COALESCE(caminho_imagem, '') = ''
                            OR COALESCE(imagem_hash, '') = ''
                            OR COALESCE(nome_arquivo_original, '') = ''
                       )
                     LIMIT ?
                    """,
                )
            )
            checks.append(
                count_check(
                    conn,
                    "Soma dos itens fecha com o envelope",
                    """
                    WITH sums AS (
                        SELECT envelope_id, ROUND(SUM(valor), 2) AS total
                          FROM envelope_itens
                         WHERE ativo = 1
                         GROUP BY envelope_id
                    )
                    SELECT COUNT(*)
                      FROM envelopes e
                      LEFT JOIN sums s ON s.envelope_id = e.id
                     WHERE e.ativo = 1
                       AND e.status = 'lancado'
                       AND ABS(COALESCE(s.total, 0) - ROUND(e.total_informado, 2)) > 0.009
                    """,
                    sample_sql="""
                    WITH sums AS (
                        SELECT envelope_id, ROUND(SUM(valor), 2) AS total
                          FROM envelope_itens
                         WHERE ativo = 1
                         GROUP BY envelope_id
                    )
                    SELECT e.id, e.lote_id, e.competencia, e.total_informado, COALESCE(s.total, 0) AS total_itens
                      FROM envelopes e
                      LEFT JOIN sums s ON s.envelope_id = e.id
                     WHERE e.ativo = 1
                       AND e.status = 'lancado'
                       AND ABS(COALESCE(s.total, 0) - ROUND(e.total_informado, 2)) > 0.009
                     LIMIT ?
                    """,
                )
            )
            checks.append(
                count_check(
                    conn,
                    "Itens de envelope com contribuicao ativa",
                    """
                    SELECT COUNT(*)
                      FROM envelope_itens ei
                      LEFT JOIN contribuicoes c ON c.id = ei.contribuicao_id AND c.ativo = 1
                     WHERE ei.ativo = 1 AND c.id IS NULL
                    """,
                    sample_sql="""
                    SELECT ei.id, ei.envelope_id, ei.contribuicao_id, ei.valor
                      FROM envelope_itens ei
                      LEFT JOIN contribuicoes c ON c.id = ei.contribuicao_id AND c.ativo = 1
                     WHERE ei.ativo = 1 AND c.id IS NULL
                     LIMIT ?
                    """,
                )
            )
            checks.append(
                count_check(
                    conn,
                    "Envelopes nao lancados fora do financeiro",
                    """
                    SELECT COUNT(*)
                      FROM envelopes e
                      JOIN envelope_itens ei ON ei.envelope_id = e.id AND ei.ativo = 1
                      JOIN contribuicoes c ON c.id = ei.contribuicao_id AND c.ativo = 1
                     WHERE e.ativo = 1
                       AND e.status IN ('aguardando_digitacao', 'ignorado', 'duplicado')
                    """,
                    sample_sql="""
                    SELECT e.id, e.lote_id, e.status, c.id AS contribuicao_id, c.valor
                      FROM envelopes e
                      JOIN envelope_itens ei ON ei.envelope_id = e.id AND ei.ativo = 1
                      JOIN contribuicoes c ON c.id = ei.contribuicao_id AND c.ativo = 1
                     WHERE e.ativo = 1
                       AND e.status IN ('aguardando_digitacao', 'ignorado', 'duplicado')
                     LIMIT ?
                    """,
                )
            )
            missing_files = []
            for row in conn.execute(
                """
                SELECT id, caminho_imagem
                  FROM envelopes
                 WHERE ativo = 1 AND COALESCE(caminho_imagem, '') <> ''
                 LIMIT 2000
                """
            ).fetchall():
                if not Path(str(row["caminho_imagem"])).exists():
                    missing_files.append(f"id={row['id']} caminho={row['caminho_imagem']}")
                    if len(missing_files) >= 5:
                        break
            checks.append(
                Check(
                    "Arquivos fisicos de envelopes existem",
                    "OK" if not missing_files else "FALHA",
                    "0 ocorrencia(s)" if not missing_files else f"{len(missing_files)} amostra(s): " + " ; ".join(missing_files),
                )
            )
            if not missing_traceability_columns:
                checks.append(
                    count_check(
                        conn,
                        "Envelopes com status de conciliacao valido",
                        """
                        SELECT COUNT(*)
                          FROM envelopes
                         WHERE ativo = 1
                           AND COALESCE(rastreio_status_conciliacao, 'pendente')
                               NOT IN ('pendente', 'conciliado', 'divergente', 'ignorado')
                        """,
                        sample_sql="""
                        SELECT id, lote_id, rastreio_status_conciliacao, nome_arquivo_original
                          FROM envelopes
                         WHERE ativo = 1
                           AND COALESCE(rastreio_status_conciliacao, 'pendente')
                               NOT IN ('pendente', 'conciliado', 'divergente', 'ignorado')
                         LIMIT ?
                        """,
                    )
                )
        checks.append(
            count_check(
                conn,
                "Contribuicoes ativas com data e competencia",
                """
                SELECT COUNT(*)
                  FROM contribuicoes
                 WHERE ativo = 1
                   AND (COALESCE(data_recebimento, '') = '' OR COALESCE(competencia, '') = '' OR competencia_ordem IS NULL)
                """,
                sample_sql="""
                SELECT id, data_recebimento, competencia, competencia_ordem, valor
                  FROM contribuicoes
                 WHERE ativo = 1
                   AND (COALESCE(data_recebimento, '') = '' OR COALESCE(competencia, '') = '' OR competencia_ordem IS NULL)
                 LIMIT ?
                """,
            )
        )
        checks.append(
            count_check(
                conn,
                "Movimentos PIX ativos lancados no financeiro",
                """
                SELECT COUNT(*)
                  FROM pix_movimentos pm
                  LEFT JOIN contribuicoes c ON c.pix_movimento_id = pm.id AND c.ativo = 1
                 WHERE pm.ativo = 1
                   AND COALESCE(pm.review_status, '') NOT IN ('ignorado', 'duplicado')
                   AND c.id IS NULL
                """,
                sample_sql="""
                SELECT pm.id, pm.lote_id, pm.data_recebimento, pm.valor, pm.nome_origem, pm.review_status
                  FROM pix_movimentos pm
                  LEFT JOIN contribuicoes c ON c.pix_movimento_id = pm.id AND c.ativo = 1
                 WHERE pm.ativo = 1
                   AND COALESCE(pm.review_status, '') NOT IN ('ignorado', 'duplicado')
                   AND c.id IS NULL
                 LIMIT ?
                """,
            )
        )
        checks.append(
            count_check(
                conn,
                "Movimentos de extrato ativos lancados no financeiro",
                """
                SELECT COUNT(*)
                  FROM extrato_movimentos em
                  LEFT JOIN contribuicoes c ON c.extrato_movimento_id = em.id AND c.ativo = 1
                 WHERE em.ativo = 1
                   AND COALESCE(em.review_status, '') NOT IN ('ignorado', 'duplicado')
                   AND c.id IS NULL
                """,
                sample_sql="""
                SELECT em.id, em.lote_id, em.data_movimento, em.valor, em.nome_origem, em.origin_label, em.review_status
                  FROM extrato_movimentos em
                  LEFT JOIN contribuicoes c ON c.extrato_movimento_id = em.id AND c.ativo = 1
                 WHERE em.ativo = 1
                   AND COALESCE(em.review_status, '') NOT IN ('ignorado', 'duplicado')
                   AND c.id IS NULL
                 LIMIT ?
                """,
            )
        )
        checks.append(
            count_check(
                conn,
                "Soma das contribuicoes PIX fecha com a remessa",
                """
                WITH sums AS (
                    SELECT pix_movimento_id AS mov_id, ROUND(SUM(valor), 2) AS total
                      FROM contribuicoes
                     WHERE ativo = 1 AND pix_movimento_id IS NOT NULL
                     GROUP BY pix_movimento_id
                )
                SELECT COUNT(*)
                  FROM sums s
                  JOIN pix_movimentos pm ON pm.id = s.mov_id
                 WHERE pm.ativo = 1
                   AND COALESCE(pm.review_status, '') NOT IN ('ignorado', 'duplicado')
                   AND ABS(s.total - ROUND(pm.valor, 2)) > 0.009
                """,
                sample_sql="""
                WITH sums AS (
                    SELECT pix_movimento_id AS mov_id, ROUND(SUM(valor), 2) AS total
                      FROM contribuicoes
                     WHERE ativo = 1 AND pix_movimento_id IS NOT NULL
                     GROUP BY pix_movimento_id
                )
                SELECT pm.id, pm.lote_id, pm.data_recebimento, pm.valor AS valor_movimento, s.total AS total_contribuicoes, pm.nome_origem
                  FROM sums s
                  JOIN pix_movimentos pm ON pm.id = s.mov_id
                 WHERE pm.ativo = 1
                   AND COALESCE(pm.review_status, '') NOT IN ('ignorado', 'duplicado')
                   AND ABS(s.total - ROUND(pm.valor, 2)) > 0.009
                 LIMIT ?
                """,
            )
        )
        checks.append(
            count_check(
                conn,
                "Soma das contribuicoes de extrato fecha com a remessa",
                """
                WITH sums AS (
                    SELECT extrato_movimento_id AS mov_id, ROUND(SUM(valor), 2) AS total
                      FROM contribuicoes
                     WHERE ativo = 1 AND extrato_movimento_id IS NOT NULL
                     GROUP BY extrato_movimento_id
                )
                SELECT COUNT(*)
                  FROM sums s
                  JOIN extrato_movimentos em ON em.id = s.mov_id
                 WHERE em.ativo = 1
                   AND COALESCE(em.review_status, '') NOT IN ('ignorado', 'duplicado')
                   AND ABS(s.total - ROUND(em.valor, 2)) > 0.009
                """,
                sample_sql="""
                WITH sums AS (
                    SELECT extrato_movimento_id AS mov_id, ROUND(SUM(valor), 2) AS total
                      FROM contribuicoes
                     WHERE ativo = 1 AND extrato_movimento_id IS NOT NULL
                     GROUP BY extrato_movimento_id
                )
                SELECT em.id, em.lote_id, em.data_movimento, em.valor AS valor_movimento, s.total AS total_contribuicoes,
                       COALESCE(em.nome_origem, em.origin_label, em.raw_text) AS origem
                  FROM sums s
                  JOIN extrato_movimentos em ON em.id = s.mov_id
                 WHERE em.ativo = 1
                   AND COALESCE(em.review_status, '') NOT IN ('ignorado', 'duplicado')
                   AND ABS(s.total - ROUND(em.valor, 2)) > 0.009
                 LIMIT ?
                """,
            )
        )
    finally:
        conn.close()
    return checks


def write_report(checks: list[Check]) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    target = REPORT_DIR / f"dados_operacionais_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    failed = [check for check in checks if check.failed]
    lines = [
        "# Dados Operacionais",
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
            "- Valida integridade fisica do SQLite.",
            "- Bloqueia valores bancarios ativos `<= 0`.",
            "- Mantem sentinelas condicionais para casos reais de parser ja corrigidos, como DOXA/Paschoal em 08/05/2026 no Sicoob.",
            "- Bloqueia referencias orfas em contribuicoes ativas.",
            "- Confirma que movimentos bancarios ativos nao ignorados geraram contribuicao financeira.",
            "- Confirma que a soma das contribuicoes ativas fecha com o valor da remessa bancaria.",
            "- Confirma que envelopes lancados possuem imagem/hash, itens fechando com o total e arquivo fisico preservado.",
            "- Confirma que envelopes pendentes, ignorados ou duplicados nao entram no financeiro.",
            "- Confirma que envelopes possuem estrutura de rastreabilidade financeira para conciliacao futura.",
        ]
    )
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="Verifica consistencia operacional direta no banco SQLite.")
    parser.add_argument("--db", default=str(DB_PATH), help="Caminho do banco SQLite.")
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

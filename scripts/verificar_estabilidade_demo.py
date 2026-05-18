from __future__ import annotations

import argparse
import importlib.util
import shutil
import sqlite3
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "power_church_demo.py"
DB_PATH = ROOT / "data" / "power_church_membros_importado.db"
REPORT_DIR = ROOT / "data" / "homologacao"
DEFAULT_RENDER_LIMIT_SECONDS = 2.0
LARGE_RENDER_LIMIT_SECONDS = 3.0
MOVEMENT_RENDER_LIMIT_SECONDS = 1.0
ASSOCIATION_RENDER_LIMIT_SECONDS = 15.0
CENT_RULE_OVERRIDE_LIMIT_SECONDS = 5.0


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
    elapsed_seconds: float | None = None
    max_seconds: float | None = None


def check_render(name: str, fn, max_seconds: float = DEFAULT_RENDER_LIMIT_SECONDS) -> Check:
    start = time.perf_counter()
    try:
        html = fn()
        elapsed = time.perf_counter() - start
        if not isinstance(html, str) or not html.strip():
            return Check(name, False, "render vazio", elapsed, max_seconds)
        if "Traceback" in html or "Internal Server Error" in html:
            return Check(name, False, "HTML contem erro interno", elapsed, max_seconds)
        detail = f"{len(html)} bytes em {elapsed:.3f}s"
        if elapsed > max_seconds:
            return Check(
                name,
                False,
                f"{detail}; limite {max_seconds:.1f}s",
                elapsed,
                max_seconds,
            )
        return Check(name, True, detail, elapsed, max_seconds)
    except Exception as exc:
        elapsed = time.perf_counter() - start
        return Check(name, False, f"{type(exc).__name__}: {exc}", elapsed, max_seconds)


def check_render_contains(
    name: str,
    fn,
    snippets: list[str],
    max_seconds: float = DEFAULT_RENDER_LIMIT_SECONDS,
) -> Check:
    start = time.perf_counter()
    try:
        html = fn()
        elapsed = time.perf_counter() - start
        if not isinstance(html, str) or not html.strip():
            return Check(name, False, "render vazio", elapsed, max_seconds)
        missing = [snippet for snippet in snippets if snippet not in html]
        if missing:
            return Check(name, False, "faltando: " + ", ".join(missing), elapsed, max_seconds)
        detail = f"{len(html)} bytes em {elapsed:.3f}s"
        if elapsed > max_seconds:
            return Check(name, False, f"{detail}; limite {max_seconds:.1f}s", elapsed, max_seconds)
        return Check(name, True, detail, elapsed, max_seconds)
    except Exception as exc:
        elapsed = time.perf_counter() - start
        return Check(name, False, f"{type(exc).__name__}: {exc}", elapsed, max_seconds)


def check_pdf(name: str, fn, min_bytes: int = 1200, max_seconds: float = DEFAULT_RENDER_LIMIT_SECONDS) -> Check:
    start = time.perf_counter()
    try:
        payload = fn()
        elapsed = time.perf_counter() - start
        if not isinstance(payload, (bytes, bytearray)):
            return Check(name, False, "retorno nao e bytes", elapsed, max_seconds)
        if not bytes(payload).startswith(b"%PDF"):
            return Check(name, False, "payload nao inicia como PDF", elapsed, max_seconds)
        if len(payload) < min_bytes:
            return Check(name, False, f"PDF muito pequeno: {len(payload)} bytes", elapsed, max_seconds)
        detail = f"{len(payload)} bytes em {elapsed:.3f}s"
        if elapsed > max_seconds:
            return Check(name, False, f"{detail}; limite {max_seconds:.1f}s", elapsed, max_seconds)
        return Check(name, True, detail, elapsed, max_seconds)
    except Exception as exc:
        elapsed = time.perf_counter() - start
        return Check(name, False, f"{type(exc).__name__}: {exc}", elapsed, max_seconds)


def check_contributions_period_print_order(
    name: str,
    app,
    db,
    max_seconds: float = LARGE_RENDER_LIMIT_SECONDS,
) -> Check:
    start = time.perf_counter()
    try:
        competence_row = db.conn.execute(
            """
            SELECT competencia
            FROM contribuicoes
            WHERE ativo = 1 AND COALESCE(competencia, '') <> ''
            GROUP BY competencia
            ORDER BY MAX(COALESCE(competencia_ordem, 0)) DESC, competencia DESC
            LIMIT 1
            """
        ).fetchone()
        if not competence_row:
            elapsed = time.perf_counter() - start
            return Check(name, False, "sem competencia com contribuicoes ativas", elapsed, max_seconds)
        competence = str(competence_row["competencia"] or "")
        rows = db.list_contributions(competencia=competence, limit=5000)
        summary = db.contributions_summary(competencia=competence)
        total = int(summary["quantidade"] or 0) if summary else 0
        if total != len(rows):
            elapsed = time.perf_counter() - start
            return Check(
                name,
                False,
                f"{competence}: filtro carregou {len(rows)} de {total} contribuicao(oes)",
                elapsed,
                max_seconds,
            )
        names = [
            str(row["pessoa_nome"] or row["contribuinte_nome"] or "Contribuinte nao identificado")
            for row in rows
        ]
        keys = [name.upper() for name in names]
        out_of_order = [
            f"{names[index - 1]} > {names[index]}"
            for index in range(1, len(keys))
            if keys[index - 1] > keys[index]
        ]
        if out_of_order:
            elapsed = time.perf_counter() - start
            return Check(name, False, f"{competence}: fora de ordem: {out_of_order[0]}", elapsed, max_seconds)
        html = app.render_contributions(db, {"competencia": [competence]})
        required = [
            "Relatorio alfabetico por periodo",
            "Imprimir lista filtrada",
            f"<h2>{total} contribuicao(oes) exibida(s)</h2>",
        ]
        missing = [snippet for snippet in required if snippet not in html]
        if missing:
            elapsed = time.perf_counter() - start
            return Check(name, False, f"{competence}: faltando " + ", ".join(missing), elapsed, max_seconds)
        elapsed = time.perf_counter() - start
        detail = f"{competence}: {total} contribuicao(oes) em ordem alfabetica, {elapsed:.3f}s"
        if elapsed > max_seconds:
            return Check(name, False, f"{detail}; limite {max_seconds:.1f}s", elapsed, max_seconds)
        return Check(name, True, detail, elapsed, max_seconds)
    except Exception as exc:
        elapsed = time.perf_counter() - start
        return Check(name, False, f"{type(exc).__name__}: {exc}", elapsed, max_seconds)


def check_contributor_period_report_identity(
    name: str,
    app,
    db,
    max_seconds: float = LARGE_RENDER_LIMIT_SECONDS,
) -> Check:
    start = time.perf_counter()
    try:
        competence_row = db.conn.execute(
            """
            SELECT competencia
            FROM contribuicoes
            WHERE ativo = 1 AND COALESCE(competencia, '') <> ''
            GROUP BY competencia
            ORDER BY MAX(COALESCE(competencia_ordem, 0)) DESC, competencia DESC
            LIMIT 1
            """
        ).fetchone()
        if not competence_row:
            elapsed = time.perf_counter() - start
            return Check(name, False, "sem competencia com contribuicoes ativas", elapsed, max_seconds)
        competence = str(competence_row["competencia"] or "")
        data = app.build_contributor_period_report_data(db, competencia=competence)
        groups = list(data["groups"])
        if not groups:
            elapsed = time.perf_counter() - start
            return Check(name, False, f"{competence}: relatorio sem grupos", elapsed, max_seconds)
        summary = dict(data["summary"])
        named = [item for item in groups if item.get("group_kind") == "nome"]
        documents = [item for item in groups if item.get("group_kind") == "documento"]
        if int(summary.get("contribuintes") or 0) != len(groups):
            elapsed = time.perf_counter() - start
            return Check(name, False, f"{competence}: resumo nao bate com grupos", elapsed, max_seconds)
        if int(summary.get("somente_documento") or 0) != len(documents):
            elapsed = time.perf_counter() - start
            return Check(name, False, f"{competence}: resumo de somente documento nao bate", elapsed, max_seconds)
        group_order = [str(item.get("group_kind") or "") for item in groups]
        if "nome" in group_order and "documento" in group_order and group_order.index("documento") < max(i for i, value in enumerate(group_order) if value == "nome"):
            elapsed = time.perf_counter() - start
            return Check(name, False, f"{competence}: documentos misturados antes do fim dos nomes", elapsed, max_seconds)
        bad_named_numbers = [str(item["nome"]) for item in named if str(item["nome"] or "")[:1].isdigit()]
        if bad_named_numbers:
            elapsed = time.perf_counter() - start
            return Check(name, False, f"{competence}: nome iniciado por numero: {bad_named_numbers[0]}", elapsed, max_seconds)
        bad_upper = [
            str(item["nome"])
            for item in named
            if any(ch.isalpha() for ch in str(item["nome"]))
            and str(item["nome"]) == str(item["nome"]).upper()
            and len(str(item["nome"])) > 4
        ]
        if bad_upper:
            elapsed = time.perf_counter() - start
            return Check(name, False, f"{competence}: nome todo em maiusculas: {bad_upper[0]}", elapsed, max_seconds)
        hybrid_identity = app.contribution_report_identity("", "64.984.878 JULIANA MADEIRA DOS SANTOS", "64.984.878 0001-91")
        document_identity = app.contribution_report_identity("", "12345678901", "12345678901")
        if hybrid_identity.get("group_kind") != "nome" or hybrid_identity.get("name") != "Juliana Madeira dos Santos":
            elapsed = time.perf_counter() - start
            return Check(name, False, "identidade hibrida numero+nome nao foi limpa corretamente", elapsed, max_seconds)
        if document_identity.get("group_kind") != "documento" or document_identity.get("name") != "123.456.789-01":
            elapsed = time.perf_counter() - start
            return Check(name, False, "identidade somente CPF nao foi separada como documento", elapsed, max_seconds)
        html = app.render_contributors(db, {"section": ["periodo"], "competencia": [competence]})
        required = ["Contribuintes com nome", "Somente documento", "period-section-row"]
        missing = [snippet for snippet in required if snippet not in html]
        if missing:
            elapsed = time.perf_counter() - start
            return Check(name, False, f"{competence}: faltando " + ", ".join(missing), elapsed, max_seconds)
        elapsed = time.perf_counter() - start
        detail = f"{competence}: {len(named)} com nome, {len(documents)} somente documento, nomes padronizados"
        if elapsed > max_seconds:
            return Check(name, False, f"{detail}; limite {max_seconds:.1f}s", elapsed, max_seconds)
        return Check(name, True, detail, elapsed, max_seconds)
    except Exception as exc:
        elapsed = time.perf_counter() - start
        return Check(name, False, f"{type(exc).__name__}: {exc}", elapsed, max_seconds)


def check_manual_cent_rule_override(
    name: str,
    app,
    db_path: Path,
    source: str,
    cent_code: str = "12",
    max_seconds: float = DEFAULT_RENDER_LIMIT_SECONDS,
) -> Check:
    start = time.perf_counter()
    tmp_file = tempfile.NamedTemporaryFile(prefix="power_church_centavos_", suffix=".db", delete=False)
    tmp_path = Path(tmp_file.name)
    tmp_file.close()
    try:
        shutil.copy2(db_path, tmp_path)
        db = app.PowerChurchDB(tmp_path)
        try:
            organization_id = db.default_organization_id()
            default_type_id = db.pix_default_type_id(organization_id)
            if not default_type_id:
                elapsed = time.perf_counter() - start
                return Check(name, False, "tipo DIZIMO nao encontrado", elapsed, max_seconds)
            if source == "extrato":
                sample = db.conn.execute(
                    """
                    SELECT id, imported_contribution_id, COALESCE(resolved_person_id, suggested_person_id, 0) AS person_id
                    FROM extrato_movimentos
                    WHERE ativo = 1 AND regra_id IS NOT NULL AND imported_contribution_id IS NOT NULL
                      AND codigo_centavos = ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (cent_code,),
                ).fetchone()
                if sample is None:
                    elapsed = time.perf_counter() - start
                    return Check(name, True, f"sem amostra de extrato com centavos {cent_code}", elapsed, max_seconds)
                movement_id = int(sample["id"])
                db.update_statement_movement_from_form(
                    movement_id,
                    {
                        "action": ["approve"],
                        "resolved_person_id": [str(int(sample["person_id"] or 0))],
                        "resolved_tipo_contribuicao_id": [str(default_type_id)],
                        "review_notes": ["Teste automatico: substituir regra de centavos por Dizimo."],
                    },
                )
                db.close()
                db = app.PowerChurchDB(tmp_path)
                movement = db.conn.execute("SELECT * FROM extrato_movimentos WHERE id = ?", (movement_id,)).fetchone()
                contribution_id = int(movement["imported_contribution_id"] or 0) if movement else 0
            elif source == "pix":
                sample = db.conn.execute(
                    """
                    SELECT id, imported_contribution_id, COALESCE(resolved_person_id, suggested_person_id, 0) AS person_id
                    FROM pix_movimentos
                    WHERE ativo = 1 AND regra_id IS NOT NULL AND imported_contribution_id IS NOT NULL
                      AND codigo_centavos = ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (cent_code,),
                ).fetchone()
                if sample is None:
                    elapsed = time.perf_counter() - start
                    return Check(name, True, f"sem amostra PIX com centavos {cent_code}", elapsed, max_seconds)
                movement_id = int(sample["id"])
                db.update_pix_movement_from_form(
                    movement_id,
                    {
                        "action": ["approve"],
                        "resolved_person_id": [str(int(sample["person_id"] or 0))],
                        "resolved_tipo_contribuicao_id": [str(default_type_id)],
                        "associate_masked_document": ["0"],
                        "review_notes": ["Teste automatico: substituir regra de centavos por Dizimo."],
                    },
                )
                movement = db.conn.execute("SELECT * FROM pix_movimentos WHERE id = ?", (movement_id,)).fetchone()
                contribution_id = int(movement["imported_contribution_id"] or 0) if movement else 0
            else:
                elapsed = time.perf_counter() - start
                return Check(name, False, f"origem desconhecida: {source}", elapsed, max_seconds)

            contribution = db.get_contribution(contribution_id) if contribution_id else None
            problems: list[str] = []
            if movement is None:
                problems.append("movimento nao encontrado apos auditoria")
            else:
                if int(movement["resolved_tipo_contribuicao_id"] or 0) != default_type_id:
                    problems.append("movimento nao manteve tipo DIZIMO")
                if int(movement["regra_id"] or 0) != 0:
                    problems.append("regra de centavos voltou ao movimento")
                if str(movement["tipo_sugerido"] or "") != "dizimo":
                    problems.append("tipo_sugerido nao virou dizimo")
                if str(movement["review_status"] or "") != "aprovado":
                    problems.append("movimento nao ficou aprovado")
            if contribution is None:
                problems.append("contribuicao importada nao encontrada")
            else:
                if int(contribution["tipo_contribuicao_id"] or 0) != default_type_id:
                    problems.append("contribuicao nao ficou como DIZIMO")
                if int(contribution["campanha_id"] or 0) != 0:
                    problems.append("campanha especial continuou vinculada")
            elapsed = time.perf_counter() - start
            detail = f"movimento {movement_id}, contribuicao {contribution_id}, {elapsed:.3f}s"
            if problems:
                return Check(name, False, "; ".join(problems) + f" ({detail})", elapsed, max_seconds)
            if elapsed > max_seconds:
                return Check(name, False, f"{detail}; limite {max_seconds:.1f}s", elapsed, max_seconds)
            return Check(name, True, detail, elapsed, max_seconds)
        finally:
            db.close()
    except Exception as exc:
        elapsed = time.perf_counter() - start
        return Check(name, False, f"{type(exc).__name__}: {exc}", elapsed, max_seconds)
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass


def check_core_matching_engine(
    name: str,
    db,
    max_seconds: float = DEFAULT_RENDER_LIMIT_SECONDS,
) -> Check:
    start = time.perf_counter()
    try:
        organization_id = db.default_organization_id()
        people_cache = db.people_for_pix_matching(organization_id)
        samples = db.conn.execute(
            """
            SELECT
                ct.nome,
                ct.documento_principal,
                ct.documento_tipo,
                ct.pessoa_id,
                p.nome AS pessoa_nome
            FROM contribuintes ct
            JOIN pessoas p ON p.id = ct.pessoa_id
            WHERE ct.ativo = 1
              AND p.ativo = 1
              AND p.status IN ('membro_ativo', 'membro_inativo', 'frequentador', 'visitante')
              AND TRIM(COALESCE(ct.nome, '')) <> ''
            ORDER BY
                CASE WHEN TRIM(COALESCE(ct.documento_principal, '')) <> '' THEN 0 ELSE 1 END,
                ct.id
            LIMIT 15
            """
        ).fetchall()
        if not samples:
            elapsed = time.perf_counter() - start
            return Check(name, False, "sem contribuintes vinculados para amostra", elapsed, max_seconds)

        tested = 0
        problems: list[str] = []
        for row in samples:
            expected_person_id = int(row["pessoa_id"] or 0)
            document = str(row["documento_principal"] or "")
            document_type = str(row["documento_tipo"] or "")
            match = db.match_pix_entry(
                organization_id,
                str(row["nome"] or ""),
                document,
                document_type,
                people_cache=people_cache,
            )
            suggestions = db.pix_candidate_suggestions(
                organization_id,
                str(row["nome"] or ""),
                document,
                document_type,
                people_cache=people_cache,
                limit=5,
            )
            suggested_ids = {int(item["id"] or 0) for item in suggestions}
            if int(match.get("person_id") or 0) != expected_person_id and expected_person_id not in suggested_ids:
                problems.append(f"{row['nome']} -> esperado {row['pessoa_nome']}")
            tested += 1
            if tested >= 3:
                break

        elapsed = time.perf_counter() - start
        detail = f"{tested} amostra(s), {elapsed:.3f}s"
        if problems:
            return Check(name, False, "; ".join(problems[:3]) + f" ({detail})", elapsed, max_seconds)
        if elapsed > max_seconds:
            return Check(name, False, f"{detail}; limite {max_seconds:.1f}s", elapsed, max_seconds)
        return Check(name, True, detail, elapsed, max_seconds)
    except Exception as exc:
        elapsed = time.perf_counter() - start
        return Check(name, False, f"{type(exc).__name__}: {exc}", elapsed, max_seconds)


def build_contributor_dashboard_pdf(app, db, section: str, tags: list[str] | None = None) -> bytes:
    data = app.build_contributors_dashboard_data(db, tags=tags or [])
    payload = app.build_contributor_report_payload(data, section=section, tags=tags or [])
    return app.build_contributor_report_pdf(
        str(payload["title"]),
        str(payload["subtitle"]),
        app.contributor_report_filter_label(tags=tags or []),
        list(payload["groups"]),
        str(payload["empty"]),
    )


def build_person_statement_pdf(app, db, person_id: int, type_ids: list[int] | None = None) -> bytes:
    statement = app.build_contribution_statement_data(db, person_id, type_ids=type_ids or [])
    person = statement["person"]
    if person is None:
        raise ValueError("Pessoa amostral sem ficha para extrato.")
    return app.build_contribution_statement_pdf(
        person,
        list(statement["entries"]),
        float(statement["total_general"]),
        int(statement["competence_count"]),
        str(statement["period_label"]),
        str(statement["competencia"] or "Todas"),
        str(statement["type_label"]),
    )


def scalar(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row[0] or 0) if row else 0


def scalar_float(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> float:
    row = conn.execute(sql, params).fetchone()
    return float(row[0] or 0) if row else 0.0


def br_money(value: object) -> str:
    amount = float(value or 0)
    text = f"{amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {text}"


def table_lines(headers: list[str], rows: list[list[object]]) -> list[str]:
    lines = [
        "|" + "|".join(headers) + "|",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    for row in rows:
        lines.append("|" + "|".join(str(item) for item in row) + "|")
    return lines


def append_sql_table(
    lines: list[str],
    title: str,
    headers: list[str],
    rows: list[sqlite3.Row],
    formatter,
) -> None:
    lines.extend(["", f"### {title}", ""])
    if not rows:
        lines.append("Sem registros.")
        return
    lines.extend(table_lines(headers, [formatter(row) for row in rows]))


def build_database_audit(conn: sqlite3.Connection) -> list[str]:
    lines: list[str] = ["", "## Auditoria objetiva do banco", ""]
    summary_rows = [
        ["Pessoas ativas", scalar(conn, "SELECT COUNT(*) FROM pessoas WHERE ativo = 1")],
        ["Contribuintes auxiliares ativos", scalar(conn, "SELECT COUNT(*) FROM contribuintes WHERE ativo = 1")],
        ["Contribuicoes financeiras ativas", scalar(conn, "SELECT COUNT(*) FROM contribuicoes WHERE ativo = 1")],
        ["Total financeiro ativo", br_money(scalar_float(conn, "SELECT COALESCE(SUM(valor), 0) FROM contribuicoes WHERE ativo = 1"))],
        ["Contribuicoes ativas sem pessoa", scalar(conn, "SELECT COUNT(*) FROM contribuicoes WHERE ativo = 1 AND pessoa_id IS NULL")],
        ["Contribuicoes inativas/ignoradas", scalar(conn, "SELECT COUNT(*) FROM contribuicoes WHERE ativo = 0 AND COALESCE(status_operacional, '') = 'ignorado'")],
        ["Lotes PIX", scalar(conn, "SELECT COUNT(*) FROM pix_lotes")],
        ["Movimentos PIX ativos", scalar(conn, "SELECT COUNT(*) FROM pix_movimentos WHERE ativo = 1")],
        ["Lotes de extrato", scalar(conn, "SELECT COUNT(*) FROM extrato_lotes")],
        ["Movimentos de extrato ativos", scalar(conn, "SELECT COUNT(*) FROM extrato_movimentos WHERE ativo = 1")],
        ["Pendencias PIX em saneamento", scalar(conn, "SELECT COUNT(*) FROM pix_movimentos WHERE ativo = 1 AND review_status IN ('revisar_pessoa', 'revisar_destinacao', 'revisar_duplicidade')")],
        ["Pendencias de extrato em saneamento", scalar(conn, "SELECT COUNT(*) FROM extrato_movimentos WHERE ativo = 1 AND review_status IN ('revisar_pessoa', 'revisar_destinacao', 'revisar_duplicidade')")],
    ]
    lines.extend(["### Resumo geral", ""])
    lines.extend(table_lines(["Indicador", "Valor"], summary_rows))

    contribution_source_rows = conn.execute(
        """
        SELECT
            CASE
                WHEN pix_movimento_id IS NOT NULL THEN 'PIX Sicoob'
                WHEN extrato_movimento_id IS NOT NULL THEN 'Extratos bancarios'
                ELSE 'Manual / outros'
            END AS origem,
            COUNT(*) AS quantidade,
            COALESCE(SUM(valor), 0) AS total
        FROM contribuicoes
        WHERE ativo = 1
        GROUP BY origem
        ORDER BY origem
        """
    ).fetchall()
    append_sql_table(
        lines,
        "Contribuicoes ativas por origem",
        ["Origem", "Qtd.", "Total"],
        contribution_source_rows,
        lambda row: [row["origem"], row["quantidade"], br_money(row["total"])],
    )

    status_rows = conn.execute(
        """
        SELECT
            COALESCE(NULLIF(status_operacional, ''), 'regular') AS status,
            COUNT(*) AS quantidade,
            COALESCE(SUM(valor), 0) AS total
        FROM contribuicoes
        WHERE ativo = 1
        GROUP BY COALESCE(NULLIF(status_operacional, ''), 'regular')
        ORDER BY quantidade DESC, status
        """
    ).fetchall()
    append_sql_table(
        lines,
        "Contribuicoes ativas por situacao operacional",
        ["Situacao", "Qtd.", "Total"],
        status_rows,
        lambda row: [row["status"], row["quantidade"], br_money(row["total"])],
    )

    cent_rule_rows = conn.execute(
        """
        SELECT
            r.codigo_centavos,
            r.nome_destinacao,
            tc.nome AS tipo_nome,
            pc.codigo AS conta_codigo,
            pc.nome AS conta_nome,
            ca.nome AS campanha_nome,
            r.ativo
        FROM pix_centavo_regras r
        LEFT JOIN tipos_contribuicao tc ON tc.id = r.tipo_contribuicao_id
        LEFT JOIN plano_contas pc ON pc.id = r.plano_conta_id
        LEFT JOIN campanhas ca ON ca.id = r.campanha_id
        ORDER BY r.codigo_centavos
        """
    ).fetchall()
    append_sql_table(
        lines,
        "Regras de centavos e destinacoes",
        ["Centavos", "Etiqueta", "Tipo", "Conta", "Campanha", "Status"],
        cent_rule_rows,
        lambda row: [
            row["codigo_centavos"],
            row["nome_destinacao"],
            row["tipo_nome"] or "-",
            f"{row['conta_codigo'] or '-'} · {row['conta_nome'] or '-'}",
            row["campanha_nome"] or "-",
            "ativa" if row["ativo"] else "inativa",
        ],
    )

    pix_rows = conn.execute(
        """
        SELECT
            l.id,
            l.banco,
            l.nome_arquivo,
            l.periodo_inicio,
            l.periodo_fim,
            l.status,
            COUNT(CASE WHEN m.ativo = 1 THEN 1 END) AS movimentos,
            COALESCE(SUM(CASE WHEN m.ativo = 1 THEN m.valor ELSE 0 END), 0) AS valor_movimentos,
            SUM(CASE WHEN m.ativo = 1 AND m.imported_contribution_id IS NOT NULL THEN 1 ELSE 0 END) AS com_financeiro,
            COALESCE(SUM(CASE WHEN c.ativo = 1 THEN c.valor ELSE 0 END), 0) AS valor_financeiro,
            SUM(CASE WHEN m.ativo = 1 AND m.review_status IN ('revisar_pessoa', 'revisar_destinacao', 'revisar_duplicidade') THEN 1 ELSE 0 END) AS pendentes,
            SUM(CASE WHEN m.ativo = 1 AND m.review_status = 'ignorado' THEN 1 ELSE 0 END) AS ignorados,
            SUM(CASE WHEN m.ativo = 1 AND TRIM(COALESCE(m.nome_origem, '')) = '' THEN 1 ELSE 0 END) AS sem_nome,
            SUM(CASE WHEN m.ativo = 1 AND c.ativo = 1 AND c.pessoa_id IS NULL AND m.review_status <> 'ignorado' THEN 1 ELSE 0 END) AS sem_pessoa
        FROM pix_lotes l
        LEFT JOIN pix_movimentos m ON m.lote_id = l.id
        LEFT JOIN contribuicoes c ON c.id = m.imported_contribution_id
        GROUP BY l.id
        ORDER BY l.id
        """
    ).fetchall()
    append_sql_table(
        lines,
        "Lotes PIX",
        ["Lote", "Banco", "Periodo", "Status", "Mov.", "Valor mov.", "Fin.", "Valor fin.", "Pend.", "Ign.", "Sem nome", "Sem pessoa", "Arquivo"],
        pix_rows,
        lambda row: [
            row["id"],
            row["banco"],
            f"{row['periodo_inicio'] or '-'} a {row['periodo_fim'] or '-'}",
            row["status"],
            row["movimentos"],
            br_money(row["valor_movimentos"]),
            row["com_financeiro"] or 0,
            br_money(row["valor_financeiro"]),
            row["pendentes"] or 0,
            row["ignorados"] or 0,
            row["sem_nome"] or 0,
            row["sem_pessoa"] or 0,
            row["nome_arquivo"],
        ],
    )

    statement_rows = conn.execute(
        """
        SELECT
            l.id,
            l.banco,
            l.layout_codigo,
            l.nome_arquivo,
            l.periodo_inicio,
            l.periodo_fim,
            l.status,
            COUNT(CASE WHEN m.ativo = 1 THEN 1 END) AS movimentos,
            COALESCE(SUM(CASE WHEN m.ativo = 1 THEN m.valor ELSE 0 END), 0) AS valor_movimentos,
            SUM(CASE WHEN m.ativo = 1 AND m.imported_contribution_id IS NOT NULL THEN 1 ELSE 0 END) AS com_financeiro,
            COALESCE(SUM(CASE WHEN c.ativo = 1 THEN c.valor ELSE 0 END), 0) AS valor_financeiro,
            SUM(CASE WHEN m.ativo = 1 AND m.review_status IN ('revisar_pessoa', 'revisar_destinacao', 'revisar_duplicidade') THEN 1 ELSE 0 END) AS pendentes,
            SUM(CASE WHEN m.ativo = 1 AND m.review_status = 'ignorado' THEN 1 ELSE 0 END) AS ignorados,
            SUM(CASE WHEN m.ativo = 1 AND TRIM(COALESCE(m.nome_origem, '')) = '' THEN 1 ELSE 0 END) AS sem_nome,
            SUM(CASE WHEN m.ativo = 1 AND c.ativo = 1 AND c.pessoa_id IS NULL AND m.review_status <> 'ignorado' THEN 1 ELSE 0 END) AS sem_pessoa,
            SUM(CASE
                WHEN m.ativo = 1
                 AND m.review_status = 'ignorado'
                 AND (
                    COALESCE(m.review_notes, '') LIKE '%mesma_titularidade%'
                    OR COALESCE(m.review_notes, '') LIKE '%origem_interna%'
                    OR ct.qualidade = 'mesma_titularidade'
                 )
                THEN 1 ELSE 0
            END) AS remessas_internas
        FROM extrato_lotes l
        LEFT JOIN extrato_movimentos m ON m.lote_id = l.id
        LEFT JOIN contribuicoes c ON c.id = m.imported_contribution_id
        LEFT JOIN contribuintes ct ON ct.id = COALESCE(m.resolved_contribuinte_id, m.suggested_contribuinte_id, c.contribuinte_id)
        GROUP BY l.id
        ORDER BY l.id
        """
    ).fetchall()
    append_sql_table(
        lines,
        "Lotes de extrato",
        ["Lote", "Banco/layout", "Periodo", "Status", "Mov.", "Valor mov.", "Fin.", "Valor fin.", "Pend.", "Ign.", "Rem. internas", "Sem nome", "Sem pessoa", "Arquivo"],
        statement_rows,
        lambda row: [
            row["id"],
            f"{row['banco']} / {row['layout_codigo']}",
            f"{row['periodo_inicio'] or '-'} a {row['periodo_fim'] or '-'}",
            row["status"],
            row["movimentos"],
            br_money(row["valor_movimentos"]),
            row["com_financeiro"] or 0,
            br_money(row["valor_financeiro"]),
            row["pendentes"] or 0,
            row["ignorados"] or 0,
            row["remessas_internas"] or 0,
            row["sem_nome"] or 0,
            row["sem_pessoa"] or 0,
            row["nome_arquivo"],
        ],
    )

    review_rows = conn.execute(
        """
        SELECT 'PIX' AS origem, review_status, COUNT(*) AS quantidade, COALESCE(SUM(valor), 0) AS total
        FROM pix_movimentos
        WHERE ativo = 1
        GROUP BY review_status
        UNION ALL
        SELECT 'Extrato' AS origem, review_status, COUNT(*) AS quantidade, COALESCE(SUM(valor), 0) AS total
        FROM extrato_movimentos
        WHERE ativo = 1
        GROUP BY review_status
        ORDER BY origem, review_status
        """
    ).fetchall()
    append_sql_table(
        lines,
        "Movimentos ativos por status de revisao",
        ["Origem", "Status", "Qtd.", "Total"],
        review_rows,
        lambda row: [row["origem"], row["review_status"], row["quantidade"], br_money(row["total"])],
    )

    consistency_rows = [
        [
            "Contribuicoes ativas com valor <= 0",
            scalar(conn, "SELECT COUNT(*) FROM contribuicoes WHERE ativo = 1 AND valor <= 0"),
            "Deve ser 0.",
        ],
        [
            "Movimentos PIX ativos nao ignorados com valor <= 0",
            scalar(conn, "SELECT COUNT(*) FROM pix_movimentos WHERE ativo = 1 AND review_status <> 'ignorado' AND valor <= 0"),
            "Deve ser 0.",
        ],
        [
            "Movimentos de extrato ativos nao ignorados com valor <= 0",
            scalar(conn, "SELECT COUNT(*) FROM extrato_movimentos WHERE ativo = 1 AND review_status <> 'ignorado' AND valor <= 0"),
            "Deve ser 0.",
        ],
        [
            "PIX com contribuicao importada inexistente/inativa",
            scalar(
                conn,
                """
                SELECT COUNT(*)
                FROM pix_movimentos m
                LEFT JOIN contribuicoes c ON c.id = m.imported_contribution_id
                WHERE m.ativo = 1
                  AND m.review_status <> 'ignorado'
                  AND m.imported_contribution_id IS NOT NULL
                  AND (c.id IS NULL OR c.ativo = 0)
                """,
            ),
            "Deve ser 0.",
        ],
        [
            "Extratos com contribuicao importada inexistente/inativa",
            scalar(
                conn,
                """
                SELECT COUNT(*)
                FROM extrato_movimentos m
                LEFT JOIN contribuicoes c ON c.id = m.imported_contribution_id
                WHERE m.ativo = 1
                  AND m.review_status <> 'ignorado'
                  AND m.imported_contribution_id IS NOT NULL
                  AND (c.id IS NULL OR c.ativo = 0)
                """,
            ),
            "Deve ser 0.",
        ],
        [
            "Divergencia valor PIX x contribuicao",
            scalar(
                conn,
                """
                SELECT COUNT(*)
                FROM pix_movimentos m
                JOIN contribuicoes c ON c.id = m.imported_contribution_id
                WHERE m.ativo = 1
                  AND c.ativo = 1
                  AND ABS(COALESCE(c.valor, 0) - COALESCE(m.valor, 0)) > 0.009
                """,
            ),
            "Deve ser 0.",
        ],
        [
            "Divergencia valor extrato x contribuicao",
            scalar(
                conn,
                """
                SELECT COUNT(*)
                FROM extrato_movimentos m
                JOIN contribuicoes c ON c.id = m.imported_contribution_id
                WHERE m.ativo = 1
                  AND c.ativo = 1
                  AND ABS(COALESCE(c.valor, 0) - COALESCE(m.valor, 0)) > 0.009
                """,
            ),
            "Deve ser 0.",
        ],
        [
            "Contribuicoes ativas ligadas a movimento PIX inativo/ignorado",
            scalar(
                conn,
                """
                SELECT COUNT(*)
                FROM contribuicoes c
                JOIN pix_movimentos m ON m.id = c.pix_movimento_id
                WHERE c.ativo = 1
                  AND (m.ativo = 0 OR m.review_status = 'ignorado')
                """,
            ),
            "Deve ser 0.",
        ],
        [
            "Contribuicoes ativas ligadas a extrato inativo/ignorado",
            scalar(
                conn,
                """
                SELECT COUNT(*)
                FROM contribuicoes c
                JOIN extrato_movimentos m ON m.id = c.extrato_movimento_id
                WHERE c.ativo = 1
                  AND (m.ativo = 0 OR m.review_status = 'ignorado')
                """,
            ),
            "Deve ser 0.",
        ],
    ]
    lines.extend(["", "### Alertas de consistencia", ""])
    lines.extend(table_lines(["Alerta", "Qtd.", "Referencia"], consistency_rows))
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description="Verifica estabilidade basica antes de demonstracoes.")
    parser.add_argument("--db", default=str(DB_PATH), help="Caminho do banco SQLite.")
    parser.add_argument("--report", action="store_true", help="Grava relatorio markdown em data/homologacao.")
    args = parser.parse_args()

    app = load_app_module()
    db_path = Path(args.db)
    checks: list[Check] = []
    db = app.PowerChurchDB(db_path)
    try:
        expected_people = db.scalar("SELECT COUNT(*) FROM pessoas WHERE ativo = 1")
        dizimo_type_id = db.scalar("SELECT id FROM tipos_contribuicao WHERE codigo = 'DIZIMO' ORDER BY id LIMIT 1")
        sample_person = db.conn.execute(
            """
            SELECT pessoa_id
            FROM contribuicoes
            WHERE ativo = 1 AND pessoa_id IS NOT NULL
            GROUP BY pessoa_id
            ORDER BY COUNT(*) DESC, pessoa_id
            LIMIT 1
            """
        ).fetchone()
        sample_person_id = int(sample_person["pessoa_id"] or 0) if sample_person else 0
        latest_people_lot = db.conn.execute(
            """
            SELECT id
            FROM import_lotes
            WHERE tipo_importacao IN ('pessoas_membros', 'pessoas_complementar_incremental')
            ORDER BY criado_em DESC, id DESC
            LIMIT 1
            """
        ).fetchone()
        latest_people_lot_id = int(latest_people_lot["id"] or 0) if latest_people_lot else 0
        checks.extend(
            [
                check_render_contains(
                    "Inicio expoe importacao de pessoas",
                    lambda: app.render_home(db),
                    ["/pessoas/importar", "Importar pessoas"],
                ),
                check_render_contains(
                    "Central separa lotes por origem",
                    lambda: app.render_imports_center(db, {}),
                    ["Extratos bancarios", "PIX historicos", "Lotes de pessoas", "/pessoas/importar", "Extrato Santander"],
                ),
                check_render_contains(
                    "Extratos expoem importacao Santander",
                    lambda: app.render_statement_home(db, {}),
                    ["Novo lote Santander", "SANTANDER_AUTO", "Santander CPF/CNPJ"],
                ),
                check_render_contains(
                    "Pessoas mostra base completa",
                    lambda: app.render_people(db, {}),
                    [f"<h2>{expected_people} pessoa(s) exibida(s)</h2>", "/pessoas/importar"],
                    max_seconds=LARGE_RENDER_LIMIT_SECONDS,
                ),
                check_render_contains(
                    "Importacao de pessoas preserva auditoria de lotes",
                    lambda: app.render_people_import(db, {}),
                    ["Lotes recentes de pessoas", "Sem nome", "Auditar lote", "campos nao mapeados"],
                ),
                check_render("PIX Sicoob", lambda: app.render_pix_home(db, {})),
                check_render("Contribuintes", lambda: app.render_contributors(db, {}), max_seconds=DEFAULT_RENDER_LIMIT_SECONDS),
                check_render("Contribuicoes", lambda: app.render_contributions(db, {})),
                check_contributions_period_print_order(
                    "Contribuicoes por periodo imprimem lista alfabetica completa",
                    app,
                    db,
                ),
                check_contributor_period_report_identity(
                    "Relatorio por periodo separa nomes e documentos",
                    app,
                    db,
                ),
                check_render_contains(
                    "Relatorio por periodo com impressao/PDF",
                    lambda: app.render_contributors(db, {"section": ["periodo"]}),
                    ["Contribuicoes por periodo", "Abrir PDF p/ imprimir", "Baixar PDF", "print-only"],
                    max_seconds=DEFAULT_RENDER_LIMIT_SECONDS,
                ),
                check_pdf(
                    "PDF contribuicoes por periodo",
                    lambda: app.build_contributor_period_report_pdf(
                        app.build_contributor_period_report_data(db)
                    ),
                    max_seconds=LARGE_RENDER_LIMIT_SECONDS,
                ),
                check_pdf(
                    "PDF contribuintes principal",
                    lambda: build_contributor_dashboard_pdf(app, db, "contributors"),
                    max_seconds=LARGE_RENDER_LIMIT_SECONDS,
                ),
                check_pdf(
                    "PDF relatorios estrategicos combinados",
                    lambda: build_contributor_dashboard_pdf(app, db, "combined", ["integracao", "familia_sugerida"]),
                    max_seconds=LARGE_RENDER_LIMIT_SECONDS,
                ),
            ]
        )
        if latest_people_lot_id:
            checks.append(
                check_render_contains(
                    f"Auditoria lote pessoas {latest_people_lot_id}",
                    lambda lot_id=latest_people_lot_id: app.render_people_import_lot(db, {"id": [str(lot_id)]}),
                    [
                        "Auditoria da importacao de pessoas",
                        "Fichas sem nome",
                        "Mapeamento de colunas",
                        "Ver associacoes por este lote",
                    ],
                    max_seconds=LARGE_RENDER_LIMIT_SECONDS,
                )
            )
            checks.append(
                check_render_contains(
                    f"Associacoes por novos cadastros lote {latest_people_lot_id}",
                    lambda lot_id=latest_people_lot_id: app.render_new_people_associations(db, {"people_lot_id": [str(lot_id)]}),
                    [
                        "Associacoes por novos cadastros",
                        "Selecionar lotes de pessoas",
                        "somente novos",
                        "Contribuinte pendente",
                    ],
                    max_seconds=ASSOCIATION_RENDER_LIMIT_SECONDS,
                )
            )
        if dizimo_type_id:
            checks.append(
                check_render_contains(
                    "Contribuicoes filtram por Dizimo",
                    lambda: app.render_contributions(db, {"tipo_id": [str(dizimo_type_id)]}),
                    ["Dizimo", "<h1>Contribuicoes</h1>", "Valor total"],
                    max_seconds=DEFAULT_RENDER_LIMIT_SECONDS,
                )
            )
        if sample_person_id:
            checks.append(
                check_render_contains(
                    "Extrato por pessoa com impressao/PDF",
                    lambda: app.render_contribution_statement(
                        db,
                        {"person_id": [str(sample_person_id)], "tipo_id": [str(dizimo_type_id)] if dizimo_type_id else []},
                    ),
                    ["Imprimir extrato", "Baixar PDF", "Extrato analitico"],
                    max_seconds=DEFAULT_RENDER_LIMIT_SECONDS,
                )
            )
            checks.append(
                check_pdf(
                    "PDF extrato de contribuicoes por pessoa",
                    lambda: build_person_statement_pdf(
                        app,
                        db,
                        sample_person_id,
                        [dizimo_type_id] if dizimo_type_id else [],
                    ),
                    max_seconds=DEFAULT_RENDER_LIMIT_SECONDS,
                )
            )
        for status in ("todos", "pendencias", "associacao", "destinacoes_especiais"):
            checks.append(
                check_render(
                    f"PIX lote 1 / {status}",
                    lambda status=status: app.render_pix_lot(db, {"id": ["1"], "status": [status]}),
                    max_seconds=LARGE_RENDER_LIMIT_SECONDS,
                )
            )
        statement_lot_ids = [
            int(row["id"])
            for row in db.conn.execute("SELECT id FROM extrato_lotes ORDER BY id").fetchall()
        ]
        for lot_id in statement_lot_ids:
            for status in ("todos", "pendencias", "associacao", "destinacoes_especiais"):
                checks.append(
                    check_render(
                        f"Extrato lote {lot_id} / {status}",
                        lambda lot_id=lot_id, status=status: app.render_statement_lot(
                            db,
                            {"id": [str(lot_id)], "status": [status]},
                        ),
                        max_seconds=LARGE_RENDER_LIMIT_SECONDS,
                    )
                )
            lot_status = db.conn.execute("SELECT status FROM extrato_lotes WHERE id = ?", (lot_id,)).fetchone()
            if lot_status and str(lot_status["status"] or "") != "encerrado":
                checks.append(
                    check_render_contains(
                        f"Extrato lote {lot_id} expoe encerramento",
                        lambda lot_id=lot_id: app.render_statement_lot(
                            db,
                            {"id": [str(lot_id)], "status": ["pendencias"]},
                        ),
                        ["Processamento do lote", "Encerrar processamento do lote", "/extratos/lote/encerrar"],
                        max_seconds=LARGE_RENDER_LIMIT_SECONDS,
                    )
                )
        movement_sample_lots: list[int] = []
        for row in db.conn.execute(
            """
            SELECT MIN(id) AS id
            FROM extrato_lotes
            GROUP BY layout_codigo
            UNION
            SELECT MAX(id) AS id
            FROM extrato_lotes
            GROUP BY layout_codigo
            ORDER BY id
            """
        ).fetchall():
            lot_id = int(row["id"] or 0)
            if lot_id and lot_id not in movement_sample_lots:
                movement_sample_lots.append(lot_id)
        for lot_id in movement_sample_lots:
            movement = db.conn.execute(
                """
                SELECT id
                FROM extrato_movimentos
                WHERE lote_id = ? AND ativo = 1
                ORDER BY CASE WHEN review_status = 'revisar_pessoa' THEN 0 ELSE 1 END, id
                LIMIT 1
                """,
                (lot_id,),
            ).fetchone()
            if movement:
                checks.append(
                    check_render(
                        f"Auditoria movimento extrato {movement['id']}",
                        lambda movement_id=movement["id"]: app.render_statement_movement(
                            db,
                            {"id": [str(movement_id)]},
                        ),
                        max_seconds=MOVEMENT_RENDER_LIMIT_SECONDS,
                    )
                )
        pix_movement = db.conn.execute(
            """
            SELECT id
            FROM pix_movimentos
            WHERE lote_id = 1
            ORDER BY CASE WHEN review_status = 'revisar_pessoa' THEN 0 ELSE 1 END, id
            LIMIT 1
            """
        ).fetchone()
        if pix_movement:
            checks.append(
                check_render(
                    f"Auditoria movimento PIX {pix_movement['id']}",
                    lambda movement_id=pix_movement["id"]: app.render_pix_movement(db, {"id": [str(movement_id)]}),
                    max_seconds=MOVEMENT_RENDER_LIMIT_SECONDS,
                )
            )
        checks.append(
            check_manual_cent_rule_override(
                "Extrato permite trocar centavos 12 para Dizimo",
                app,
                db_path,
                "extrato",
                cent_code="12",
                max_seconds=CENT_RULE_OVERRIDE_LIMIT_SECONDS,
            )
        )
        checks.append(
            check_manual_cent_rule_override(
                "PIX permite trocar centavos 12 para Dizimo",
                app,
                db_path,
                "pix",
                cent_code="12",
                max_seconds=CENT_RULE_OVERRIDE_LIMIT_SECONDS,
            )
        )
        checks.append(
            check_core_matching_engine(
                "Motor de matching reutilizavel",
                db,
                max_seconds=LARGE_RENDER_LIMIT_SECONDS,
            )
        )
    finally:
        db.close()

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    database_audit_lines: list[str] = []
    try:
        checks.append(
            Check(
                "Sicoob jan/fev/mar sem PIX sem nome",
                scalar(
                    conn,
                    """
                    SELECT COUNT(*)
                    FROM extrato_movimentos
                    WHERE lote_id IN (4, 5, 6)
                      AND ativo = 1
                      AND movement_kind = 'pix'
                      AND TRIM(COALESCE(nome_origem, '')) = ''
                    """,
                )
                == 0,
                "esperado 0",
            )
        )
        checks.append(
            Check(
                "PIX antigos jan/fev/mar desativados",
                scalar(
                    conn,
                    "SELECT COUNT(*) FROM pix_movimentos WHERE lote_id IN (2, 3, 4) AND ativo = 1",
                )
                == 0,
                "esperado 0",
            )
        )
        for name, expected in [
            ("DOXA TREINAMENTO LTDA", 3),
            ("Bravim Consultoria Ltda", 3),
        ]:
            count = scalar(
                conn,
                """
                SELECT COUNT(*)
                FROM contribuicoes c
                JOIN contribuintes ct ON ct.id = c.contribuinte_id
                WHERE c.ativo = 1
                  AND c.extrato_movimento_id IS NOT NULL
                  AND ct.nome = ?
                  AND c.data_recebimento BETWEEN '2026-01-01' AND '2026-03-31'
                """,
                (name,),
            )
            checks.append(Check(f"Sentinela {name}", count >= expected, f"{count} ocorrencia(s)"))
        missing_cent_destinations = scalar(
            conn,
            """
            SELECT COUNT(*)
            FROM pix_centavo_regras r
            WHERE r.ativo = 1
              AND (r.campanha_id IS NULL OR r.plano_conta_id IS NULL)
            """,
        )
        checks.append(Check("Regras de centavos ativas com conta/campanha", missing_cent_destinations == 0, "esperado 0"))
        divergent_cent_destinations = scalar(
            conn,
            """
            SELECT COUNT(*)
            FROM pix_centavo_regras r
            LEFT JOIN plano_contas pc ON pc.id = r.plano_conta_id
            LEFT JOIN campanhas ca ON ca.id = r.campanha_id
            WHERE r.ativo = 1
              AND (
                TRIM(COALESCE(r.nome_destinacao, '')) <> TRIM(COALESCE(pc.nome, ''))
                OR TRIM(COALESCE(r.nome_destinacao, '')) <> TRIM(COALESCE(ca.nome, ''))
              )
            """,
        )
        checks.append(Check("Etiqueta de centavos igual a conta/campanha", divergent_cent_destinations == 0, "esperado 0"))
        bad_incremental_people_names = scalar(
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
                "Importacao complementar preserva nomes",
                bad_incremental_people_names == 0,
                f"{bad_incremental_people_names} ficha(s) complementares sem nome",
            )
        )
        monthly_rows = conn.execute(
            """
            SELECT competencia,
                   MAX(COALESCE(competencia_ordem, 0)) AS ordem,
                   COUNT(*) AS lancamentos,
                   COUNT(DISTINCT COALESCE(pessoa_id, -contribuinte_id, -id)) AS contribuintes,
                   COALESCE(SUM(valor), 0) AS total
            FROM contribuicoes
            WHERE ativo = 1
              AND COALESCE(competencia_ordem, 0) > 0
            GROUP BY competencia
            ORDER BY ordem
            """
        ).fetchall()
        sicoob_statement_months = {
            str(row["competencia"])
            for row in conn.execute(
                """
                SELECT DISTINCT m.competencia
                FROM extrato_movimentos m
                JOIN extrato_lotes l ON l.id = m.lote_id
                WHERE m.ativo = 1
                  AND l.banco = 'Sicoob'
                  AND COALESCE(m.competencia, '') <> ''
                """
            ).fetchall()
        }
        sicoob_pix_months = {
            str(row["competencia"])
            for row in conn.execute(
                """
                SELECT DISTINCT m.competencia
                FROM pix_movimentos m
                JOIN pix_lotes l ON l.id = m.lote_id
                WHERE m.ativo = 1
                  AND l.banco = 'Sicoob'
                  AND COALESCE(m.competencia, '') <> ''
                """
            ).fetchall()
        }
        monthly_alerts: list[str] = []
        if len(monthly_rows) >= 4:
            latest_month = monthly_rows[-1]
            previous = monthly_rows[-4:-1]
            avg_count = sum(int(row["lancamentos"] or 0) for row in previous) / len(previous)
            avg_total = sum(float(row["total"] or 0) for row in previous) / len(previous)
            latest_count = int(latest_month["lancamentos"] or 0)
            latest_total = float(latest_month["total"] or 0)
            if avg_count and latest_count < avg_count * 0.70:
                monthly_alerts.append(
                    f"{latest_month['competencia']} tem {latest_count} lancamentos; media dos 3 meses anteriores {avg_count:.1f}"
                )
            if avg_total and latest_total < avg_total * 0.70:
                monthly_alerts.append(
                    f"{latest_month['competencia']} soma R$ {latest_total:,.2f}; media anterior R$ {avg_total:,.2f}"
                )
            latest_competence = str(latest_month["competencia"])
            if latest_competence in sicoob_pix_months and latest_competence not in sicoob_statement_months:
                monthly_alerts.append(f"{latest_competence} tem Sicoob via PIX, mas nao tem extrato Sicoob completo importado")
        coverage_detail = "ALERTA: " + "; ".join(monthly_alerts) if monthly_alerts else "sem lacunas mensais relevantes"
        checks.append(Check("Cobertura mensal por competencia", True, coverage_detail))
        database_audit_lines = build_database_audit(conn)
    finally:
        conn.close()

    failed = [item for item in checks if not item.ok]
    lines = [
        "# Verificacao de estabilidade",
        "",
        f"Gerado em: {datetime.now().isoformat(timespec='seconds')}",
        f"Banco: {db_path}",
        "",
        f"Resultado: {'OK' if not failed else 'FALHAS'}",
        "",
        "## Checks funcionais e de performance",
        "",
    ]
    for item in checks:
        marker = "OK" if item.ok else "FALHOU"
        line = f"- {marker}: {item.name}"
        if item.detail:
            line += f" ({item.detail})"
        lines.append(line)
        print(line)

    lines.extend(database_audit_lines)

    if args.report:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        target = REPORT_DIR / f"verificacao_estabilidade_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"\nRelatorio: {target}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

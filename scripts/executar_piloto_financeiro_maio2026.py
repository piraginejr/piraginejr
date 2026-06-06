from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "data" / "homologacao"
DEFAULT_DB = ROOT / "data" / "power_church_membros_importado.db"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.previsualizar_importacao_assistida_extratos import (  # noqa: E402
    PreviewCase,
    connect_readonly,
    load_cent_rules,
    load_existing_occurrences,
    load_org_name,
    preview_case,
)


DEFAULT_CASES = (
    PreviewCase(
        "Bradesco",
        "BRADESCO_EXTRATO",
        Path("/Users/piraginejr/Library/Mobile Documents/com~apple~CloudDocs/Downloads/Downloads/BRADESCO_MAIO26.pdf"),
    ),
    PreviewCase(
        "Santander",
        "SANTANDER_AUTO",
        Path("/Users/piraginejr/Library/Mobile Documents/com~apple~CloudDocs/Downloads/Downloads/SANTANDER_Maio2026.pdf"),
    ),
    PreviewCase(
        "Sicoob",
        "SICOOB_CONTA_CORRENTE",
        Path("/Users/piraginejr/Library/Mobile Documents/com~apple~CloudDocs/Downloads/Downloads/SICOOB_MAIO26.pdf"),
    ),
)


@dataclass(frozen=True)
class PilotDecision:
    status: str
    rationale: str
    next_step: str


def classify_result(result: dict[str, object]) -> PilotDecision:
    if not result.get("ok"):
        return PilotDecision(
            status="BLOQUEADO",
            rationale="O parser nao conseguiu ler o arquivo com seguranca suficiente para o piloto.",
            next_step="Corrigir leitura do arquivo ou revisar o layout solicitado antes de qualquer importacao.",
        )
    comparison = result.get("provider_comparison") or {}
    duplicates = int((result.get("status_counts") or {}).get("duplicidade", 0))
    reviews = int((result.get("status_counts") or {}).get("revisao", 0))
    if not comparison.get("ok"):
        return PilotDecision(
            status="BLOQUEADO_PORTABILIDADE",
            rationale=(
                "O leitor homologado atual e o leitor portavel divergem neste arquivo. "
                "Isso e critico para a migracao final para Linux/VM."
            ),
            next_step=(
                "Usar este banco apenas como massa de prova controlada e corrigir o parser portavel "
                "antes de liberar corte financeiro definitivo."
            ),
        )
    if duplicates > 0 or reviews > 0:
        return PilotDecision(
            status="APTO_COM_AUDITORIA",
            rationale=(
                "A leitura esta consistente, mas o lote toca movimentos ja importados ou casos de centavos especiais "
                "que exigem conferencia humana."
            ),
            next_step=(
                "Executar piloto controlado em ambiente de comparacao, validar duplicidades e centavos especiais, "
                "e so depois considerar corte deste banco."
            ),
        )
    return PilotDecision(
        status="APTO",
        rationale="Leitura estavel e sem risco relevante inicial detectado pela pre-analise.",
        next_step="Pode entrar como primeiro candidato a piloto controlado da Etapa 3.",
    )


def money(value: object) -> str:
    total = float(value or 0)
    return f"R$ {total:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def write_report(results: list[dict[str, object]]) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = REPORT_DIR / f"piloto_financeiro_maio2026_{stamp}.md"
    lines = [
        "# Piloto Financeiro Maio 2026",
        "",
        f"Gerado em: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "Este relatorio prepara a Etapa 3 com os extratos reais de maio/2026 do cliente, sem gravar novos lotes no financeiro.",
        "",
        "## Regra de uso",
        "",
        "- esta etapa serve para comparacao e validacao do parser;",
        "- nao autoriza corte financeiro por si so;",
        "- qualquer banco com divergencia entre leitor homologado atual e leitor portavel fica bloqueado para o corte final da migracao.",
        "",
        "## Resultado executivo",
        "",
        "| Banco | Layout | Periodo | Movimentos | Total | Novos | Revisao | Duplicidade | Portabilidade | Decisao |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for result in results:
        decision = classify_result(result)
        status_counts = result.get("status_counts") or {}
        comparison = result.get("provider_comparison") or {}
        total_fmt = money(result.get("total"))
        period = f"{result.get('period_start') or '-'} a {result.get('period_end') or '-'}"
        lines.append(
            "| {bank} | {layout} | {period} | {count} | {total} | {new} | {review} | {dup} | {portability} | {decision} |".format(
                bank=result.get("bank"),
                layout=result.get("layout_code") or result.get("layout_requested"),
                period=period,
                count=int(result.get("entries_count") or 0),
                total=total_fmt,
                new=int(status_counts.get("novo", 0)),
                review=int(status_counts.get("revisao", 0)),
                dup=int(status_counts.get("duplicidade", 0)),
                portability="OK" if comparison.get("ok") else "ATENCAO",
                decision=decision.status,
            )
        )
    for result in results:
        decision = classify_result(result)
        lines.extend(
            [
                "",
                f"## {result.get('bank')}",
                "",
                f"- Arquivo: `{result.get('path')}`",
                f"- Layout pedido: `{result.get('layout_requested')}`",
                f"- Layout detectado: `{result.get('layout_code') or result.get('layout_requested')}`",
                f"- Periodo: {result.get('period_start') or '-'} a {result.get('period_end') or '-'}",
                f"- Movimentos uteis: {int(result.get('entries_count') or 0)}",
                f"- Total lido: {money(result.get('total'))}",
                f"- Comparacao leitor homologado x portavel: {'OK' if (result.get('provider_comparison') or {}).get('ok') else 'ATENCAO'}",
                f"- Decisao: `{decision.status}`",
                f"- Motivo: {decision.rationale}",
                f"- Proximo passo: {decision.next_step}",
            ]
        )
        warnings = list(result.get("warnings") or [])
        if warnings:
            lines.append("- Alertas:")
            for warning in warnings:
                lines.append(f"  - {warning}")
        risk_rows = list(result.get("risk_rows") or [])
        if risk_rows:
            lines.extend(
                [
                    "",
                    "### Casos de revisao imediata",
                    "",
                    "| Data | Valor | Identidade | Riscos |",
                    "| --- | ---: | --- | --- |",
                ]
            )
            for row in risk_rows[:20]:
                identity = str(row.get("name") or row.get("document") or "-")
                lines.append(
                    f"| {row.get('date') or '-'} | {row.get('amount_fmt') or '-'} | {identity} | {', '.join(row.get('risks') or []) or '-'} |"
                )
            if len(risk_rows) > 20:
                lines.append(f"| ... | ... | ... | mais {len(risk_rows) - 20} caso(s) |")
        duplicate_rows = list(result.get("duplicate_rows") or [])
        if duplicate_rows:
            lines.extend(
                [
                    "",
                    "### Amostra de duplicidades provaveis",
                    "",
                    "| Data | Valor | Identidade | Referencia existente |",
                    "| --- | ---: | --- | --- |",
                ]
            )
            for row in duplicate_rows[:15]:
                matches = row.get("exact_matches") or row.get("operational_matches") or []
                if matches:
                    match = matches[0]
                    ref = f"{match.get('origem')} #{match.get('id')} lote #{match.get('lote_id')}"
                else:
                    ref = "-"
                identity = str(row.get("name") or row.get("document") or "-")
                lines.append(
                    f"| {row.get('date') or '-'} | {row.get('amount_fmt') or '-'} | {identity} | {ref} |"
                )
            if len(duplicate_rows) > 15:
                lines.append(f"| ... | ... | ... | mais {len(duplicate_rows) - 15} duplicidade(s) |")
    lines.extend(
        [
            "",
            "## Leitura de risco para a Etapa 3",
            "",
            "- `Bradesco`: leitura portavel aprovada; pode ser o primeiro banco do piloto controlado.",
            "- `Santander`: leitura funcional, mas portabilidade ainda nao homologada; bom para massa de prova, nao para corte final.",
            "- `Sicoob`: alto valor operacional e alta duplicidade com base ja importada; excelente massa de prova, mas bloqueado para corte final enquanto o leitor portavel divergir.",
            "",
            "## Proxima acao recomendada",
            "",
            "1. tratar a portabilidade de `Santander` e `Sicoob`;",
            "2. usar `Bradesco` como primeiro corte financeiro controlado;",
            "3. depois repetir a comparacao com os tres bancos antes de liberar o dominio financeiro completo.",
        ]
    )
    target.write_text("\n".join(lines), encoding="utf-8")
    return target


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Executa o piloto financeiro de maio/2026 sem gravar lotes no banco.")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Banco legado SQLite operacional.")
    parser.add_argument("--report", action="store_true", help="Gera um relatorio Markdown em data/homologacao.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_path = Path(args.db).expanduser().resolve()
    if not db_path.exists():
        print(f"FALHA: banco nao encontrado: {db_path}")
        return 2
    with connect_readonly(db_path) as conn:
        existing_by_key, existing_by_signature = load_existing_occurrences(conn)
        cent_rules = load_cent_rules(conn)
        org_name = load_org_name(conn)
        results = [
            preview_case(conn, case, existing_by_key, existing_by_signature, cent_rules, org_name)
            for case in DEFAULT_CASES
        ]
    for result in results:
        decision = classify_result(result)
        comparison = result.get("provider_comparison") or {}
        status_counts = result.get("status_counts") or {}
        print(
            "{bank}: {decision} | portabilidade={port} | mov={count} | novos={new} | revisao={review} | dup={dup}".format(
                bank=result.get("bank"),
                decision=decision.status,
                port="OK" if comparison.get("ok") else "ATENCAO",
                count=int(result.get("entries_count") or 0),
                new=int(status_counts.get("novo", 0)),
                review=int(status_counts.get("revisao", 0)),
                dup=int(status_counts.get("duplicidade", 0)),
            )
        )
    if args.report:
        report = write_report(results)
        print(f"Relatorio: {report}")
    blocked = [result for result in results if classify_result(result).status.startswith("BLOQUEADO")]
    return 1 if blocked else 0


if __name__ == "__main__":
    raise SystemExit(main())

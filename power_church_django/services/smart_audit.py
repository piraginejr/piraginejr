from __future__ import annotations

from collections import Counter
from typing import Any

from power_church_core.family import normalize_address_complement
from power_church_core.normalization import normalize_match_name, normalize_query


def smart_audit_payload(
    *,
    scope: str,
    category_key: str,
    category_label: str,
    risk_key: str,
    risk_label: str,
    confidence_key: str,
    confidence_label: str,
    suggested_action: str,
    operator_hint: str,
    rationale: str,
) -> dict[str, str]:
    return {
        "scope": scope,
        "category_key": category_key,
        "category_label": category_label,
        "risk_key": risk_key,
        "risk_label": risk_label,
        "confidence_key": confidence_key,
        "confidence_label": confidence_label,
        "suggested_action": suggested_action,
        "operator_hint": operator_hint,
        "rationale": rationale,
    }


def summarize_smart_audit(items: list[dict[str, Any]], field_name: str = "smart_audit") -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    counts = Counter(str((item.get(field_name) or {}).get("category_key") or "") for item in items if item.get(field_name))
    for item in items:
        audit = item.get(field_name) or {}
        key = str(audit.get("category_key") or "")
        if not key or key in grouped:
            continue
        grouped[key] = {
            "key": key,
            "label": audit.get("category_label") or key,
            "risk_label": audit.get("risk_label") or "",
            "confidence_label": audit.get("confidence_label") or "",
            "suggested_action": audit.get("suggested_action") or "",
            "count": counts.get(key, 0),
        }
    return sorted(grouped.values(), key=lambda item: (-int(item["count"]), str(item["label"])))


def classify_family_audit_group(complements: list[object]) -> dict[str, str]:
    normalized = [normalize_address_complement(value) for value in complements if normalize_query(value)]
    token_blob = " | ".join(normalized)
    if any(token in token_blob for token in ["COND", "LOTEAMENTO", "TRAVESSA", "RUA ", "ESTRADA ", "ALAMEDA "]):
        return smart_audit_payload(
            scope="familias_domiciliares",
            category_key="referencia_local_sem_unidade",
            category_label="Referencia de local sem unidade",
            risk_key="medio",
            risk_label="Risco medio",
            confidence_key="alto",
            confidence_label="Classificacao alta",
            suggested_action="Completar bloco, apartamento, casa numerada ou lote antes de criar a familia.",
            operator_hint="Priorize contato ou revisao do cadastro para obter a unidade residencial exata.",
            rationale="O complemento descreve condominio, loteamento, rua interna ou local de apoio, mas nao identifica a unidade onde a familia reside.",
        )
    if any(token in token_blob for token in ["FUNDOS", "FDS", "SOBRADO", "TERREO", "SUPERIOR", "INFERIOR"]):
        return smart_audit_payload(
            scope="familias_domiciliares",
            category_key="posicional_generico",
            category_label="Posicional generico",
            risk_key="medio",
            risk_label="Risco medio",
            confidence_key="alto",
            confidence_label="Classificacao alta",
            suggested_action="Confirmar se o complemento posicional representa a mesma casa ou uma unidade distinta.",
            operator_hint="Use visita, telefone ou comparacao manual do cadastro antes de unificar o nucleo.",
            rationale="Expressões como fundos, sobrado ou térreo indicam localizacao relativa, mas ainda nao garantem unidade domiciliar unica.",
        )
    if any(token in token_blob for token in ["IGREJA", "ASSEMBLEIA", "TEMPLO", "MISSAO", "DONA ", "SEU ", "CASA DE "]):
        return smart_audit_payload(
            scope="familias_domiciliares",
            category_key="referencia_pessoa_ou_instituicao",
            category_label="Referencia por pessoa ou instituicao",
            risk_key="medio",
            risk_label="Risco medio",
            confidence_key="medio",
            confidence_label="Classificacao media",
            suggested_action="Validar se o texto identifica moradia real ou apenas ponto de referencia local.",
            operator_hint="Nao automatize. Revise a ficha e confirme com a familia antes de consolidar o nucleo.",
            rationale="O complemento foi preenchido com nome de pessoa, igreja ou instituicao, o que ajuda a localizar o lugar, mas nao prova unidade domiciliar.",
        )
    if normalized and all(token in {"CASA", ""} for token in normalized) or not normalized:
        return smart_audit_payload(
            scope="familias_domiciliares",
            category_key="sem_unidade_especifica",
            category_label="Sem unidade especifica",
            risk_key="baixo",
            risk_label="Risco baixo",
            confidence_key="alto",
            confidence_label="Classificacao alta",
            suggested_action="Revisar se os moradores compartilham a mesma casa e, se sim, completar o cadastro quando possivel.",
            operator_hint="Boa candidata a unificacao, mas ainda merece confirmacao humana minima porque nao ha unidade descrita.",
            rationale="O cadastro traz casa ou vazio no complemento. Em geral indica o mesmo domicílio, mas sem detalhe suficiente para consolidar automaticamente.",
        )
    return smart_audit_payload(
        scope="familias_domiciliares",
        category_key="texto_livre_ambiguo",
        category_label="Texto livre ambiguo",
        risk_key="alto",
        risk_label="Risco alto",
        confidence_key="medio",
        confidence_label="Classificacao media",
        suggested_action="Auditar manualmente antes de qualquer consolidacao automatica.",
        operator_hint="Compare todos os campos do endereco e, se necessario, consulte a familia ou um supervisor.",
        rationale="O complemento possui texto livre que nao aponta unidade clara nem regra segura de consolidacao.",
    )


def classify_import_pendency(item: dict[str, Any]) -> dict[str, str]:
    kind = normalize_match_name(item.get("tipo"))
    action_text = normalize_match_name(item.get("acao_sugerida"))
    description = normalize_match_name(item.get("descricao"))
    origin = normalize_match_name(item.get("origem"))
    blob = " ".join(part for part in [kind, action_text, description, origin] if part)
    if any(token in blob for token in ["PIX", "TRANSFERENCIA", "EXTRATO", "BANCO", "CONCILIAC"]):
        return smart_audit_payload(
            scope="cadastro_importacao",
            category_key="conciliacao_bancaria_pix",
            category_label="Conciliacao banco/PIX",
            risk_key="alto",
            risk_label="Risco alto",
            confidence_key="alto",
            confidence_label="Classificacao alta",
            suggested_action="Conferir se o evento financeiro deve ser conciliado, reaproveitado ou mantido em auditoria.",
            operator_hint="Evite duplicar financeiro. Priorize rastreabilidade de extrato, PIX ou transferencia ja importados.",
            rationale="A pendencia tem sinais de conciliacao bancaria ou PIX, o que pode gerar duplicidade financeira se resolvido sem criterio.",
        )
    if any(token in blob for token in ["CARTAO", "CREDITO", "DEBITO"]):
        return smart_audit_payload(
            scope="cadastro_importacao",
            category_key="conciliacao_cartao",
            category_label="Conciliacao de cartao",
            risk_key="alto",
            risk_label="Risco alto",
            confidence_key="medio",
            confidence_label="Classificacao media",
            suggested_action="Conferir adquirente, evento original e finalidade antes de consolidar o recebimento.",
            operator_hint="Fluxo com intermediacao financeira. Nao automatizar sem correspondencia clara do evento.",
            rationale="Pendencias de cartao tendem a envolver eventos agrupados, taxas ou datas diferentes da origem do contribuinte.",
        )
    if any(token in blob for token in ["ENVELOPE", "RATEIO", "LOTE"]):
        return smart_audit_payload(
            scope="cadastro_importacao",
            category_key="digitacao_envelope",
            category_label="Envelope/digitacao",
            risk_key="medio",
            risk_label="Risco medio",
            confidence_key="alto",
            confidence_label="Classificacao alta",
            suggested_action="Revisar identificacao, finalidade e eventual rateio antes de concluir o lancamento.",
            operator_hint="Use imagem, lote e historico do contribuinte para evitar erro de leitura manual.",
            rationale="Pendencias de envelope costumam nascer de identificacao ruim, finalidade ambigua ou necessidade de rateio.",
        )
    if "CPF" in kind or "CPF" in action_text or "CPF" in description:
        return smart_audit_payload(
            scope="cadastro_importacao",
            category_key="documento_cpf",
            category_label="Documento/CPF",
            risk_key="alto",
            risk_label="Risco alto",
            confidence_key="alto",
            confidence_label="Classificacao alta",
            suggested_action="Conferir documento antes de mesclar ou ativar a ficha.",
            operator_hint="Nao automatizar quando houver conflito de CPF ou documento principal.",
            rationale="Pendencia envolve identidade documental, o que afeta unicidade e rastreabilidade do cadastro.",
        )
    if "EMAIL" in kind or "TELEFONE" in kind or "ENDERECO" in kind:
        return smart_audit_payload(
            scope="cadastro_importacao",
            category_key="contato_endereco",
            category_label="Contato/endereco",
            risk_key="medio",
            risk_label="Risco medio",
            confidence_key="alto",
            confidence_label="Classificacao alta",
            suggested_action="Revisar o dado de contato/endereco e consolidar o melhor valor.",
            operator_hint="Pode ser resolvido com comparacao manual simples, preservando historico da fonte.",
            rationale="A pendencia atinge telefone, e-mail ou endereco, geralmente resolvivel sem risco financeiro direto.",
        )
    if "NOME" in kind or "NOME" in description:
        return smart_audit_payload(
            scope="cadastro_importacao",
            category_key="nome_incompleto",
            category_label="Nome incompleto",
            risk_key="medio",
            risk_label="Risco medio",
            confidence_key="medio",
            confidence_label="Classificacao media",
            suggested_action="Completar ou confirmar o nome antes de publicar a ficha.",
            operator_hint="Use a linha original da importacao e o contexto familiar para corrigir o nome.",
            rationale="A ficha existe, mas a identificacao nominal ainda nao esta adequada para uso operacional.",
        )
    return smart_audit_payload(
        scope="cadastro_importacao",
        category_key="pendencia_cadastral_geral",
        category_label="Pendencia cadastral geral",
        risk_key="medio",
        risk_label="Risco medio",
        confidence_key="medio",
        confidence_label="Classificacao media",
        suggested_action="Revisar manualmente a pendencia e seguir a acao sugerida da importacao.",
        operator_hint="Esta categoria cobre pendencias que ainda nao possuem regra especializada.",
        rationale="A pendencia veio da importacao ou saneamento, mas nao corresponde a uma familia de regra mais especifica.",
    )


def classify_email_audit(item: dict[str, Any]) -> dict[str, str]:
    status = normalize_match_name(item.get("status_key") or item.get("status"))
    kind = normalize_match_name(item.get("kind_key") or item.get("tipo"))
    if status == "FALHOU":
        return smart_audit_payload(
            scope="email_sistema",
            category_key="falha_entrega",
            category_label="Falha de entrega",
            risk_key="alto",
            risk_label="Risco alto",
            confidence_key="alto",
            confidence_label="Classificacao alta",
            suggested_action="Revisar destinatario, credencial ou integracao antes de reenviar.",
            operator_hint="Use o botao de reenviar so depois de entender a causa da falha.",
            rationale="O sistema tentou enviar, mas a entrega nao foi concluida. Pode afetar recibo, extrato ou comunicacao obrigatoria.",
        )
    if kind == "EXTRATO":
        return smart_audit_payload(
            scope="email_sistema",
            category_key="envio_extrato_manual",
            category_label="Extrato manual",
            risk_key="baixo",
            risk_label="Risco baixo",
            confidence_key="alto",
            confidence_label="Classificacao alta",
            suggested_action="Manter trilha de auditoria e usar para esclarecimento de divergencias.",
            operator_hint="Fluxo manual, normalmente disparado por atendimento ou conferencia.",
            rationale="Extratos sao tipicamente usados em esclarecimento e auditoria detalhada, nao em envio automatico rotineiro.",
        )
    return smart_audit_payload(
        scope="email_sistema",
        category_key="envio_recibo_rotina",
        category_label="Recibo de rotina",
        risk_key="baixo",
        risk_label="Risco baixo",
        confidence_key="alto",
        confidence_label="Classificacao alta",
        suggested_action="Monitorar normalmente e reenviar apenas em caso de contestacao ou falha.",
        operator_hint="Fluxo regular do sistema para prestacao de contas ao contribuinte.",
        rationale="Recibo enviado em fluxo normal, com trilha operacional preservada.",
    )


def classify_contributor_link_block(block: dict[str, Any]) -> dict[str, str]:
    contributor = block.get("contributor") or {}
    matches = list(block.get("matches") or [])
    relation_kinds = {normalize_match_name(item.get("relation")) for item in matches}
    recurring = int(contributor.get("recorrencia_competencias") or 0)
    if "NUCLEAR" in relation_kinds and len(matches) == 1:
        return smart_audit_payload(
            scope="integracao_contribuinte",
            category_key="vinculo_familiar_forte",
            category_label="Vinculo familiar forte",
            risk_key="baixo",
            risk_label="Risco baixo",
            confidence_key="alto",
            confidence_label="Classificacao alta",
            suggested_action="Vincular a pessoa sugerida ou criar frequentador se ainda faltar ficha complementar.",
            operator_hint="Boa candidata a resolucao rapida, especialmente quando o historico financeiro e recorrente.",
            rationale="Há uma correspondência familiar nuclear clara e pouco ambigua entre contribuinte e pessoa cadastrada.",
        )
    if "NUCLEAR" in relation_kinds and len(matches) > 1:
        return smart_audit_payload(
            scope="integracao_contribuinte",
            category_key="vinculo_familiar_competitivo",
            category_label="Vinculo familiar competitivo",
            risk_key="medio",
            risk_label="Risco medio",
            confidence_key="medio",
            confidence_label="Classificacao media",
            suggested_action="Escolher manualmente a pessoa correta antes de vincular o contribuinte.",
            operator_hint="Compare documento, recorrencia e contexto familiar para evitar vinculo errado.",
            rationale="O sobrenome sugere familia nuclear, mas ha mais de uma pessoa plausivel no cadastro.",
        )
    if recurring >= 3:
        return smart_audit_payload(
            scope="integracao_contribuinte",
            category_key="contribuinte_recorrente_sem_ficha",
            category_label="Recorrente sem ficha consolidada",
            risk_key="medio",
            risk_label="Risco medio",
            confidence_key="alto",
            confidence_label="Classificacao alta",
            suggested_action="Criar frequentador ou vinculo operacional para nao perder rastreabilidade futura.",
            operator_hint="Priorize contribuinte recorrente que ainda nao entrou no cadastro formal.",
            rationale="A recorrencia financeira mostra que o contribuinte ja exige presenca estruturada no sistema.",
        )
    return smart_audit_payload(
        scope="integracao_contribuinte",
        category_key="sugestao_integracao_ampla",
        category_label="Sugestao ampla de integracao",
        risk_key="medio",
        risk_label="Risco medio",
        confidence_key="medio",
        confidence_label="Classificacao media",
        suggested_action="Revisar a familia sugerida e decidir entre vincular, criar frequentador ou manter apenas como pista.",
        operator_hint="Categoria geral para sugestoes de integracao ainda nao resolvidas.",
        rationale="Existe indicio util de relacionamento com o cadastro, mas sem certeza suficiente para automacao direta.",
    )

# Plano de Auditoria Inteligente V1

## Objetivo

Transformar as filas de auditoria do Power Church em uma camada operacional padronizada, na qual o sistema:

- classifica automaticamente a pendencia;
- informa risco e confianca;
- sugere a acao mais segura;
- registra a decisao humana;
- reaproveita o mesmo padrao em varias areas, sem depender de memoria futura.

## O que ja ficou implementado

### 1. Familias domiciliares

- Classificacao por tipo de ambiguidade do endereco/complemento.
- Resumo inteligente na fila de auditoria.
- Filtro por categoria inteligente.
- Acao sugerida e dica operacional em cada grupo.

### 2. Auditoria operacional do cadastro/importacao

- Classificacao padronizada das pendencias mostradas em `/audit/`.
- Resumo inteligente por categoria.
- Classificacao linha a linha com risco, confianca e dica ao operador.

### 3. Integracao de contribuintes auxiliares

- Classificacao inteligente das sugestoes de vinculo familiar.
- Resumo inteligente da lista de recorrentes ligados a familias ja cadastradas.
- Acao sugerida para vincular, criar frequentador ou revisar manualmente.

### 4. Auditoria de e-mails do sistema

- Classificacao de recibos de rotina, extratos manuais e falhas de entrega.
- Resumo inteligente na central `/audit/?modo=emails`.
- Classificacao operacional antes de reenviar.

## Padrao comum adotado

Cada item inteligente passa a ter:

- `scope`
- `category_key`
- `category_label`
- `risk_key`
- `risk_label`
- `confidence_key`
- `confidence_label`
- `suggested_action`
- `operator_hint`
- `rationale`

Essa estrutura vive em:

- [smart_audit.py](/Users/piraginejr/Documents/New project/Teste/Power Church/power_church_django/services/smart_audit.py)

## Estrategia para as proximas auditorias

### 5. Conciliacao de envelopes, PIX, transferencias, banco e cartao

Quando a tela ou fila mostrar ambiguidade de conciliacao, o sistema deve classificar:

- `conciliacao_unica_segura`
- `conciliacao_competitiva`
- `evento_sem_correspondencia`
- `rateio_sem_duplicidade`
- `risco_de_duplicidade_financeira`

Saidas esperadas:

- sugerir reaproveitar evento existente;
- sugerir criar complemento/rateio;
- sugerir parar para supervisao;
- impedir duplicidade silenciosa.

### 6. Divergencias de recibo

Quando esse fluxo for implementado, cada caso deve nascer classificado como:

- `divergencia_cadastral`
- `divergencia_financeira`
- `divergencia_de_finalidade`
- `divergencia_de_origem`
- `contestacao_sem_prova_suficiente`

Saidas esperadas:

- abrir caso para supervisor;
- anexar trilha do extrato/recibo;
- registrar resposta ao contribuinte;
- reabrir o mesmo caso em vez de pulverizar tickets.

### 7. Atualizacao de cadastro pelo proprio membro

Quando entrar a integracao com o app do cliente, o sistema deve classificar:

- `alteracao_simples_autoaprovavel`
- `alteracao_relevante_para_revisao`
- `mudanca_de_identidade`
- `novo_contato_confirmado`
- `foto_pendente_de_validacao`

Saidas esperadas:

- aplicar automaticamente o que for seguro;
- mandar para supervisao o que muda identidade, familia ou rastreabilidade;
- manter log completo de antes/depois e origem.

## Regra de projeto

Toda nova fila de auditoria do Power Church deve nascer com:

1. classificacao inteligente;
2. resumo inteligente;
3. sugestao operacional;
4. trilha da decisao humana;
5. cobertura nos scripts de verificacao.

## Regra de implementacao futura

Antes de criar uma nova auditoria, responder:

1. qual o risco real de erro?
2. o sistema consegue sugerir a melhor acao?
3. quando ele deve automatizar?
4. quando deve pedir decisao humana?
5. como a decisao ficara auditada?

## Resultado esperado

O sistema deixa de ter filas cegas e passa a ter auditorias orientadas por contexto, risco e acao recomendada, formando um padrao reutilizavel para financeiro, cadastro, contribuicoes, recibos, e-mails e relacionamento com o membro.

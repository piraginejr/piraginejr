# Power Church 100 Percent Operational Roadmap

- Data: 2026-07-01
- Base atual: `84%` de operacionalidade estimada
- Objetivo: definir a trilha recomendada para levar o Power Church de `84%` para `100%` operacional, com foco em uso real do operador.
- Escopo: planejamento operacional. Nenhuma implementacao de codigo foi feita nesta etapa.

## Fontes usadas

- [POWER_CHURCH_OPERATIONAL_COMPLETION_MATRIX.md](/Users/piraginejr/Documents/New%20project/Teste/Power%20Church/docs/operational/POWER_CHURCH_OPERATIONAL_COMPLETION_MATRIX.md)
- `reports/regression_audit_20260701_183556.md`
- `reports/regression_warns_triage_20260701_175638.md`

## Leitura executiva

O Power Church ja tem o nucleo principal funcionando em Django + PostgreSQL + Docker, com permissao por modulo corrigida e comprovada.

O que falta para chegar a `100%` nao e mais migracao estrutural, e sim:

- homologacao humana de fluxos criticos de escrita;
- validacao real dos fluxos de e-mail e fila na nuvem;
- disciplina operacional de restore e atualizacao;
- reducao dos WARNs que ainda representam atrito real;
- consolidacao de configuracoes operacionais.

## Regra de priorizacao

Cada item abaixo foi priorizado por:

1. impacto direto no uso do operador;
2. risco de incidente ou bloqueio de producao;
3. dependencia para outros itens;
4. capacidade de aumentar a confianca operacional rapidamente.

## Roadmap por etapa

| Etapa | Foco | Impacto operacional | Esforco | Risco | Ganho estimado | Dependencias | Validacao humana | Validacao automatica |
| --- | --- | --- | --- | --- | ---: | --- | --- | --- |
| 1 | Envelopes fim a fim | Muito alto | Medio | Alto | `+5 p.p.` | Permissoes ja corrigidas; massa real de envelopes; operadores disponiveis | Lancar pendente, corrigir lancado, ignorar, voltar ao lote, abrir proximo pendente, conferir contadores/status | Expandir `regression_audit` para diferenciar envelope pendente vs lancado e checar transicoes esperadas |
| 2 | Importacoes bancarias e de pessoas | Muito alto | Medio | Alto | `+4 p.p.` | Etapa 1 nao bloqueia, mas ajuda ter roteiro de estados; lotes reais de teste | Preparar lote, reprocessar lote, encerrar lote, revisar pendencias, validar lote de pessoas filtrado e impressao operacional | Reforcar `regression_audit` para estados de lotes, contadores, filtros e consistencia de movimentos importados |
| 3 | Recibos e e-mail real na nuvem | Muito alto | Medio | Alto | `+3 p.p.` | Provider Microsoft Graph funcional; acesso ao ambiente de nuvem; campanha pequena de teste | Gerar recibo, enviar manualmente, validar fila, reprocessar falha, validar envio de extrato por e-mail | Dry-run ja existe; ampliar checagem automatica de runtime Graph, templates e fila com cenarios controlados |
| 4 | Restore operacional e readiness de incidente | Alto | Medio | Alto | `+2 p.p.` | Backups atualizados; runtime padrao definido; operador da nuvem alinhado | Executar restore completo em ambiente de homologacao e validar login, dashboard, pessoas, contribuicoes, recibos e imports apos restore | Checklist automatizado pos-restore: health, login, totais, PDFs e `regression_audit` resumida |
| 5 | Configuracoes operacionais e trilha unica de operacao | Medio | Medio | Medio | `+1 p.p.` | Etapas 1 a 4 estabilizadas o suficiente para consolidacao | Validar com operador se a trilha ficou compreensivel: o que faz em tela, o que faz em script e quando chamar suporte | Checagem de presença de envs, paths, staticfiles, branding, uploads e scripts obrigatorios |
| 6 | Performance e WARNs residuais | Medio | Medio | Medio | `+1 p.p.` | Etapas anteriores validadas para evitar otimizar o fluxo errado | Confirmar se lentidao de familias/exports ainda e percebida pelo operador; validar fotos ausentes e 404s de envelope por estado | Medir tempos no `regression_audit`, classificar fotos ausentes e limpar/reclassificar movimentos orfaos |

## Ordem recomendada de execucao

1. Envelopes fim a fim
2. Importacoes bancarias e de pessoas
3. Recibos e e-mail real na nuvem
4. Restore operacional e readiness de incidente
5. Configuracoes operacionais e trilha unica de operacao
6. Performance e WARNs residuais

## Prioridades por impacto operacional

### Prioridade 1

- Envelopes fim a fim
- Importacoes bancarias e de pessoas
- Recibos e e-mail real na nuvem

Motivo:
- sao os fluxos mais proximos da operacao financeira diaria;
- concentram o maior risco de bloqueio real;
- ainda dependem mais de validacao humana do que de codigo estrutural.

### Prioridade 2

- Restore operacional
- Configuracoes operacionais

Motivo:
- nao travam o uso diario imediatamente, mas definem se o sistema aguenta incidente, troca de operador ou subida segura.

### Prioridade 3

- Performance
- WARNs residuais

Motivo:
- importam para maturidade e conforto, mas nao devem atrasar a homologacao dos fluxos criticos.

## Detalhamento por frente

### 1. Envelopes

- Objetivo: fechar o ciclo operacional completo de envelopes em estados reais.
- Esforco: medio
- Risco: alto
- Ganho estimado: `+5 p.p.`
- Dependencias:
  - controle `em_digitacao` ja ativo;
  - massa real com lotes parcial, pendente e lancado;
  - operador disponivel para teste assistido.
- Validacao humana obrigatoria:
  - abrir lote com envelopes pendentes;
  - abrir proximo pendente;
  - lancar envelope com 1 contribuicao;
  - lancar envelope com mais de 1 linha;
  - validar cenarios com rateio;
  - ignorar envelope e conferir retorno ao lote;
  - editar envelope realmente lancado;
  - conferir contadores e status depois de cada acao.
- Validacao automatica recomendada:
  - `regression_audit` state-aware para distinguir `launch` valido de `edit` valido por status;
  - testes de transicao de status e consistencia de itens gerados.

### 2. Importacoes

- Objetivo: tornar lotes e auditorias plenamente confiaveis para operador.
- Esforco: medio
- Risco: alto
- Ganho estimado: `+4 p.p.`
- Dependencias:
  - disponibilidade de lotes reais ou massa de homologacao;
  - envelopes estabilizados ajudam na confianca geral, mas nao bloqueiam.
- Validacao humana obrigatoria:
  - preparar lote de extrato;
  - reprocessar lote;
  - encerrar lote com criterio correto;
  - confirmar que pendencias preservam rastreabilidade;
  - abrir lote de importacao de pessoas;
  - filtrar pendencias por tipo;
  - imprimir relatorio filtrado do lote;
  - abrir ficha a partir da pendencia e corrigir dado.
- Validacao automatica recomendada:
  - checar contadores por status;
  - checar totais por lote;
  - reduzir ruído dos 6 movimentos com `imported_contribution_legacy_id` inexistente.

### 3. Recibos e e-mail na nuvem

- Objetivo: comprovar o maior diferencial operacional do sistema em ambiente real.
- Esforco: medio
- Risco: alto
- Ganho estimado: `+3 p.p.`
- Dependencias:
  - Graph configurado;
  - acesso ao runtime da nuvem;
  - campanha pequena e controlada.
- Validacao humana obrigatoria:
  - gerar recibo individual;
  - gerar recibo por competencia;
  - enviar manualmente;
  - validar monitor de fila;
  - reprocessar item falho;
  - validar envio de extrato por e-mail;
  - confirmar conteudo, remetente e anexo.
- Validacao automatica recomendada:
  - dry-run com anexo;
  - checagem do backend/provider;
  - smoke de templates e metadados da fila;
  - auditoria tecnica da fila apos campanha de teste.

### 4. Restore operacional

- Objetivo: garantir continuidade em caso de incidente.
- Esforco: medio
- Risco: alto
- Ganho estimado: `+2 p.p.`
- Dependencias:
  - backup padrao validado;
  - ambiente de homologacao para ensaio.
- Validacao humana obrigatoria:
  - executar restore completo;
  - abrir sistema restaurado;
  - confirmar login, dashboard, pessoas, contribuicoes, recibos e imports;
  - confirmar anexos principais.
- Validacao automatica recomendada:
  - healthcheck do runtime;
  - totals/paridade;
  - PDFs principais;
  - rodada resumida da `regression_audit`.

### 5. Configuracoes operacionais

- Objetivo: reduzir dependencia de memoria, pessoa-chave e documentos soltos.
- Esforco: medio
- Risco: medio
- Ganho estimado: `+1 p.p.`
- Dependencias:
  - trilha padrao de operacao ja aceita pelo time.
- Validacao humana obrigatoria:
  - confirmar se o operador sabe:
    - onde configura o que e de tela;
    - o que continua tecnico;
    - qual script usar em cada incidente;
    - qual checklist seguir pos-atualizacao.
- Validacao automatica recomendada:
  - checagem de arquivos/pastas/envs obrigatorios;
  - checklist de runtime, branding, staticfiles e uploads.

### 6. Performance e WARNs residuais

- Objetivo: remover os atritos que ainda diminuem a sensacao de produto pronto.
- Esforco: medio
- Risco: medio
- Ganho estimado: `+1 p.p.`
- Dependencias:
  - fluxos criticos principais ja homologados.
- Itens foco:
  - `/people/families/` lento;
  - exports CSV/XLSX perto de 1s;
  - fotos ausentes;
  - 404s de envelope que dependem de estado;
  - 6 movimentos de extrato com referencia orfa;
  - `GET /accounts/logout/` retornando `405` na auditoria.
- Validacao humana obrigatoria:
  - confirmar se a lentidao ainda e percebida como problema operacional;
  - confirmar politica de fotos ausentes.
- Validacao automatica recomendada:
  - tempos de resposta por endpoint;
  - classificacao state-aware dos 404s;
  - limpeza ou reclassificacao dos WARNs de consistencia.

## O que deve ser validado pelo operador humano

- Todos os fluxos que alteram estado real:
  - envelopes;
  - importacoes;
  - geracao e envio de recibos;
  - envio de extratos;
  - restore completo.
- Confirmacao de ergonomia:
  - o operador entende o que fazer sem depender de interpretacao tecnica.
- Confirmacao de trilha operacional:
  - o que faz na interface;
  - o que faz em script;
  - quando escalar suporte.

## O que pode e deve ser validado automaticamente

- permissao por view;
- healthcheck do runtime;
- login e rotas principais;
- PDFs e exports;
- totais e paridades;
- consistencia de referencias;
- presenca de arquivos e diretorios operacionais;
- dry-run de e-mail com anexo;
- tempos de resposta e WARNs de performance.

## O que nao deve ser tratado agora

- criar novo painel administrativo grande de configuracoes;
- refatoracoes amplas de arquitetura sem bug operacional claro;
- novas funcionalidades fora da trilha critica;
- Flutter/API nova alem do necessario para a operacao atual;
- otimizacoes finas antes de homologar envelopes, importacoes, recibos e restore;
- limpeza de legado puramente historico sem impacto no runtime.

## Marco recomendado de aceite

O Power Church pode ser considerado `100% operacional` quando:

1. envelopes estiverem homologados fim a fim com estados reais;
2. importacoes bancarias e de pessoas estiverem homologadas com lote completo;
3. recibos e e-mails estiverem provados na nuvem com fila real controlada;
4. restore completo tiver sido executado com sucesso e validado;
5. configuracoes operacionais estiverem consolidadas em trilha oficial clara;
6. WARNs residuais restantes forem apenas melhoria futura, nao risco operacional.

## Projecao de ganho por etapa

| Etapa | Base | Ganho estimado | Resultado acumulado |
| --- | ---: | ---: | ---: |
| Estado atual | `84%` | `-` | `84%` |
| 1. Envelopes | `84%` | `+5 p.p.` | `89%` |
| 2. Importacoes | `89%` | `+4 p.p.` | `93%` |
| 3. Recibos e e-mail na nuvem | `93%` | `+3 p.p.` | `96%` |
| 4. Restore operacional | `96%` | `+2 p.p.` | `98%` |
| 5. Configuracoes operacionais | `98%` | `+1 p.p.` | `99%` |
| 6. Performance e WARNs residuais | `99%` | `+1 p.p.` | `100%` |

## Top prioridades

1. Envelopes fim a fim
2. Importacoes bancarias e de pessoas
3. Recibos e e-mail real na nuvem
4. Restore operacional
5. Configuracoes operacionais

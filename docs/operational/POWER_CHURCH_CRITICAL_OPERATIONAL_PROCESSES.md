# Power Church Critical Operational Processes

- Data: 2026-07-02
- Objetivo: medir se o operador consegue executar as jornadas completas que o cliente percebe como essenciais no Power Church, do inicio ao fim.
- Escopo: mapeamento operacional por processo, sem alterar codigo, testes ou arquitetura.

## Fontes usadas

- `docs/operational/POWER_CHURCH_OPERATIONAL_COMPLETION_MATRIX.md`
- `docs/operational/POWER_CHURCH_100_PERCENT_OPERATIONAL_ROADMAP.md`
- `reports/regression_audit_20260702_070806.md`
- `reports/regression_warns_triage_20260701_175638.md`
- estado atual do runtime apos os commits de permissoes, envelopes e importacoes

## Criterio de classificacao

- `Operacional`: a jornada completa esta presente e tem evidencia tecnica forte de funcionamento fim a fim.
- `Parcialmente operacional`: a jornada principal existe, mas ainda depende de estado especifico, massa real, passo tecnico ou validacao incompleta.
- `Quebrado`: existe evidencia concreta de falha relevante na jornada.
- `Ausente`: a jornada esperada nao esta disponivel ao operador no sistema atual.
- `Exige validacao humana`: a trilha tecnica parece boa, mas ainda falta uma prova real de uso para confirmar a percepcao do cliente.

## Resumo executivo

### Resposta curta

Hoje, o operador **ja consegue executar boa parte das jornadas principais de leitura, consulta, relatorio, exportacao e auditoria**, e tambem uma parte relevante das jornadas de escrita mais sensiveis.

O que ainda impede a sensacao de "sistema 100% operacional" nao e mais falta estrutural do produto, e sim:

- jornadas criticas que ainda precisam de **validacao humana fim a fim**;
- processos que continuam tecnicamente possiveis, mas **dependem de operador tecnico** ou de contexto controlado;
- algumas jornadas de escrita cujo motor esta pronto, mas a prova real ainda precisa ser feita com massa viva.

### Leitura por processo

- Processos mapeados: `20`
- Processos operacionais: `8`
- Processos parcialmente operacionais: `4`
- Processos que exigem validacao humana: `8`
- Processos quebrados: `0`
- Processos ausentes: `0`

### Resposta de aceite

**O operador consegue executar as jornadas completas que o cliente espera?**

- `Sim`, nas jornadas de consulta, relatorio, exportacao, auditoria, permissao aplicada e boa parte dos fluxos de contribuicao/envelope/importacao.
- `Ainda nao com sensacao plena de 100%`, nas jornadas de criacao/alteracao mais sensiveis, envio real de recibos/e-mails, restore operacional e algumas trilhas administrativas.

**Quais jornadas ainda impedem a entrega com sensacao de sistema 100% operacional?**

- importar extrato bancario com PDF real e fechar o lote em amostra viva;
- emitir e reenviar recibos por e-mail em ambiente real de nuvem;
- editar envelope realmente lancado em massa operacional viva;
- criar pessoa, excluir com seguranca e mesclar pessoas com rodada humana recente;
- restore operacional completo validado;
- criacao de usuario e atribuicao de permissoes com prova operacional assistida.

## Processos 100% operacionais hoje

- localizar e editar pessoa
- auditar lote bancario
- lancar envelope
- gerar extrato por pessoa
- gerar relatorio financeiro
- exportar dados
- consultar auditoria
- conciliar contribuicao no fluxo auditado atual

## Processos parcialmente operacionais hoje

- importar pessoas
- editar envelope lancado
- executar backup
- validar configuracoes operacionais

## Processos que exigem validacao humana

- cadastrar nova pessoa
- excluir pessoa com seguranca
- mesclar pessoas
- importar extrato bancario
- emitir recibo
- reenviar recibo por e-mail
- criar usuario
- executar restore

## Processos bloqueados

Nenhum processo critico principal aparece hoje como `ausente` ou `quebrado` de forma estrutural. O risco atual esta concentrado mais em validacao operacional e prova real do que em falta de implementacao.

## Ordem recomendada para chegar a 100%

1. Importar extrato bancario com PDF real e fechar lote completo
2. Emitir recibo e reenviar por e-mail na nuvem
3. Editar envelope realmente lancado
4. Cadastrar, excluir com seguranca e mesclar pessoas com rodada humana assistida
5. Executar restore operacional completo
6. Criar usuario e atribuir permissoes com roteiro controlado
7. Consolidar backup e configuracoes operacionais em trilha mais simples

## Matriz por area

### 1. Secretaria / Pessoas

| Processo | Objetivo operacional | Modulos envolvidos | Fluxo inicio -> fim | Rotas / views relacionadas | Servicos relacionados | Status atual | Validacao automatica disponivel | Validacao humana necessaria | Risco operacional | Prioridade | Ganho estimado para 100% | Proxima acao recomendada |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | ---: | --- |
| Cadastrar nova pessoa | Permitir que a secretaria registre um novo cadastro completo sem depender do legado | people | abrir formulario -> preencher dados -> salvar -> abrir ficha criada | `/people/new/`, `apps/people/views.py:new`, `detail` | `apps/people/forms.py`, `apps/people/services/*` | Exige validacao humana | rota e formulario ativos; protecao de acesso OK | criar 1 cadastro controlado e conferir ficha/filtros | Medio | Alta | `+1.0 p.p.` | homologar criacao controlada com rollback ou massa descartavel |
| Localizar e editar pessoa | Encontrar rapidamente uma pessoa e ajustar cadastro sem sair da base nova | people | buscar -> abrir ficha -> abrir edicao -> salvar -> reconsultar | `/people/`, `/people/<id>/`, `/people/<id>/edit/` | `apps/people/views.py:index`, `detail`, `edit` | Operacional | `regression_audit` com busca, detalhe e edicao `200` | apenas amostra final de conforto do operador | Baixo | Alta | `+0.0 p.p.` | manter no verificador principal |
| Excluir pessoa com seguranca | Remover cadastro de forma segura, auditavel e reversivel antes de purga final | people, audit | abrir ficha -> enviar para lixeira -> revisar lixeira -> purgar se necessario | `/people/trash/`, `/people/trash/<id>/purge/` | `secure_trash`, `apps/people/views.py:trash`, `purge_trash` | Exige validacao humana | lixeira/purga seguras marcadas `OK` | testar exclusao controlada e retorno na lixeira | Alto | Media | `+0.8 p.p.` | homologar fluxo completo com dado descartavel |
| Mesclar pessoas | Resolver duplicidade com preservacao dos melhores dados | people | abrir merge -> escolher registros -> confirmar merge -> revisar ficha final | `/people/<id>/merge/`, `apps/people/views.py:merge` | `services/merge*`, historico de merge controlado | Exige validacao humana | tela de merge `200`; casos tecnicos anteriores ja tratados | rodar lote pequeno de casos reais homologados | Alto | Alta | `+1.2 p.p.` | separar 2 a 3 casos reais seguros e validar fim a fim |
| Importar pessoas | Subir planilha, auditar pendencias e corrigir fichas a partir do lote | people, reports | subir planilha -> abrir lote -> filtrar pendencias -> abrir ficha -> corrigir -> imprimir relatorio filtrado | `/people/imports/`, `/people/imports/<lot_id>/` | `services/people_import_native.py`, `apps/people/views.py:imports`, `import_lot` | Parcialmente operacional | POST sem arquivo OK; lotes e tela de auditoria `200`; filtros e impressao presentes | rodada humana com planilha real do operador | Medio | Alta | `+1.5 p.p.` | homologar uma planilha real e fechar pendencias do lote na UI |

### 2. Financeiro / Contribuicoes

| Processo | Objetivo operacional | Modulos envolvidos | Fluxo inicio -> fim | Rotas / views relacionadas | Servicos relacionados | Status atual | Validacao automatica disponivel | Validacao humana necessaria | Risco operacional | Prioridade | Ganho estimado para 100% | Proxima acao recomendada |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | ---: | --- |
| Conciliar contribuicao | Associar corretamente contribuicao a pessoa/tipo/destino e manter rastreabilidade | contributions, imports | localizar contribuicao ou movimento -> revisar dados -> ajustar tipo/pessoa -> confirmar -> reconsultar | `/contributions/`, `/contributions/<id>/`, `/contributions/<id>/split/`, `/imports/statement/movement/<id>/` | `services/contributions_native.py`, `apps/imports/services.py` | Operacional | detalhe, split e extratos respondendo `200`; lote bancario nativo com prepare/reprocess/close cobertos | validar alguns cenarios humanos de split | Medio | Alta | `+0.5 p.p.` | manter sentinelas e homologar 1, 2 e multiplos rateios |
| Gerar extrato por pessoa | Permitir consulta e entrega do extrato individual em HTML/PDF | contributions, reports | buscar pessoa -> abrir extrato -> gerar PDF -> conferir conteudo | `/contributions/statements/<id>/`, `/pdf/` | `person_statement_data_postgres`, `pdf_reports.py` | Operacional | HTML e PDF `200`; bug de `notes` ja corrigido | apenas conferencia visual final | Baixo | Alta | `+0.0 p.p.` | manter como sentinela fixa |
| Gerar relatorio financeiro | Entregar visao financeira por periodo e por destino com filtros reais | reports, contributions | abrir relatorio -> aplicar filtros -> revisar tela -> gerar PDF se necessario | `/reports/`, `/reports/destinations/`, PDFs correspondentes | `apps/reports/views.py`, `pdf_reports.py` | Operacional | filtros HTML/PDF e content types OK | validacao humana de leitura final | Baixo | Alta | `+0.0 p.p.` | manter cobertura automatica |
| Exportar dados | Permitir extracao operacional em CSV/XLSX, inclusive com colunas dinamicas | people, reports | abrir exportacao -> escolher preset/colunas -> baixar arquivo -> abrir arquivo | `/people/export/` | `apps/people/views.py:export` | Operacional | CSV/XLSX e export dinamico `OK` | apenas conferencia pontual em Excel | Baixo | Alta | `+0.0 p.p.` | manter como sentinela e medir performance |

### 3. Envelopes

| Processo | Objetivo operacional | Modulos envolvidos | Fluxo inicio -> fim | Rotas / views relacionadas | Servicos relacionados | Status atual | Validacao automatica disponivel | Validacao humana necessaria | Risco operacional | Prioridade | Ganho estimado para 100% | Proxima acao recomendada |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | ---: | --- |
| Lancar envelope | Permitir que o operador abra o lote, pegue o proximo pendente e lance a contribuicao | contributions | abrir lote -> abrir proximo pendente -> lancar -> salvar -> voltar ao lote | `/contributions/envelopes/lots/<id>/`, `/next/`, `/launch/` | `services/envelopes_native.py`, `apps/contributions/tests.py` | Operacional | detalhe, next, launch e concorrencia `em_digitacao` cobertos em auditoria/testes | homologar cenarios humanos com rateio simples e multiplo | Medio | Alta | `+0.5 p.p.` | manter no verificador mestre e fazer rodada viva curta |
| Editar envelope lancado | Corrigir um envelope ja processado e manter consistencia do lote e da contribuicao | contributions | localizar envelope lancado -> abrir editar -> ajustar -> salvar -> revisar lote | `/contributions/envelopes/<id>/edit/` | `services/envelopes_native.py`, `envelope_edit` | Parcialmente operacional | service coberto e link exposto; sem amostra `lancado` recente na auditoria | validar em envelope realmente lancado no runtime | Medio | Alta | `+1.3 p.p.` | separar uma amostra real de envelope lancado e homologar |

### 4. Importacoes

| Processo | Objetivo operacional | Modulos envolvidos | Fluxo inicio -> fim | Rotas / views relacionadas | Servicos relacionados | Status atual | Validacao automatica disponivel | Validacao humana necessaria | Risco operacional | Prioridade | Ganho estimado para 100% | Proxima acao recomendada |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | ---: | --- |
| Importar extrato bancario | Transformar PDF bancario em lote auditavel na base nova | imports, contributions | subir PDF -> criar lote -> abrir lote -> revisar movimentos | `/imports/`, `/imports/<kind>/<lot_id>/` | `create_statement_lot_postgres_native`, parser bancario, `apps/imports/services.py` | Exige validacao humana | POST sem arquivo OK; lotes e detalhes `200`; parser nativo ativo | subir 1 PDF real e conferir lote criado corretamente | Alto | Alta | `+2.0 p.p.` | homologar um extrato bancario real controlado |
| Auditar lote bancario | Revisar movimentos, aprovar com ou sem pessoa, ignorar mesma titularidade e encerrar com criterio correto | imports, contributions | abrir lote -> abrir movimento -> auditar -> preparar/reprocessar -> encerrar lote | `/imports/statement/<lot_id>/`, `/imports/statement/movement/<id>/` | `prepare_statement_lot_postgres_native`, `reprocess_statement_lot_postgres_native`, `close_statement_lot_postgres_native` | Operacional | testes nativos, runtime Docker controlado e rotas `200` | rodada humana com amostra viva para assinatura final | Alto | Alta | `+0.8 p.p.` | manter sentinela e fazer homologacao com operador |

### 5. Recibos e e-mail

| Processo | Objetivo operacional | Modulos envolvidos | Fluxo inicio -> fim | Rotas / views relacionadas | Servicos relacionados | Status atual | Validacao automatica disponivel | Validacao humana necessaria | Risco operacional | Prioridade | Ganho estimado para 100% | Proxima acao recomendada |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | ---: | --- |
| Emitir recibo | Gerar recibo individual ou consolidado e validar PDF final | contributions, reports | buscar pessoa -> gerar recibo -> abrir detalhe -> baixar PDF | `/receipts/`, `/receipts/new/`, `/receipts/<id>/pdf/` | `receipt_pdf`, `receipt_new`, fila de dispatch | Exige validacao humana | hub, detalhe e PDF `OK`; backend/provider configurados | gerar recibo real com dados vivos e conferir fila | Alto | Alta | `+1.6 p.p.` | homologar geracao manual e por competencia |
| Reenviar recibo por e-mail | Provar o diferencial de envio automatico/assistido na nuvem | contributions, audit, runtime email | abrir fila ou auditoria -> reenviar -> confirmar envio/auditoria | `/receipts/queue/`, `/audit/?modo=emails`, `/audit/emails/resend/` | Microsoft Graph provider, `ReceiptDispatch`, templates | Exige validacao humana | provider, templates e dry-run OK; fila vazia classificada como esperada | campanha pequena real, com envio e reprocesso | Alto | Alta | `+1.8 p.p.` | executar campanha controlada na nuvem e auditar resultado |

### 6. Relatorios e exports

| Processo | Objetivo operacional | Modulos envolvidos | Fluxo inicio -> fim | Rotas / views relacionadas | Servicos relacionados | Status atual | Validacao automatica disponivel | Validacao humana necessaria | Risco operacional | Prioridade | Ganho estimado para 100% | Proxima acao recomendada |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | ---: | --- |
| Gerar relatorio financeiro e imprimir/exportar | Entregar ao cliente e ao operador a visao financeira com saidas utilizaveis | reports, people | abrir relatorio -> filtrar -> exportar ou gerar PDF -> validar arquivo | `/reports/`, `/reports/destinations/`, `/people/export/` | `pdf_reports.py`, `apps/people/views.py:export` | Operacional | HTML, PDF, CSV e XLSX OK | apenas checagem humana de apresentacao final | Baixo | Media | `+0.0 p.p.` | manter como trilha automatica e monitorar performance |

### 7. Auditoria

| Processo | Objetivo operacional | Modulos envolvidos | Fluxo inicio -> fim | Rotas / views relacionadas | Servicos relacionados | Status atual | Validacao automatica disponivel | Validacao humana necessaria | Risco operacional | Prioridade | Ganho estimado para 100% | Proxima acao recomendada |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | ---: | --- |
| Consultar auditoria | Permitir rastrear eventos operacionais, tecnicos, Django e e-mails | audit | abrir auditoria -> filtrar modo/tipo -> abrir evidencias -> decidir acao | `/audit/` | `apps/audit/views.py:index` | Operacional | modos tecnico, django e emails `200`; filtros ativos | apenas leitura final pelo operador | Baixo | Alta | `+0.0 p.p.` | manter na trilha de suporte |

### 8. Usuarios e permissoes

| Processo | Objetivo operacional | Modulos envolvidos | Fluxo inicio -> fim | Rotas / views relacionadas | Servicos relacionados | Status atual | Validacao automatica disponivel | Validacao humana necessaria | Risco operacional | Prioridade | Ganho estimado para 100% | Proxima acao recomendada |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | ---: | --- |
| Criar usuario | Colocar um novo operador em uso com acesso controlado | accounts | abrir painel -> criar usuario -> definir dados -> salvar -> testar login | `/accounts/` | `apps/accounts/views.py`, `User`, `Group` | Exige validacao humana | painel `200`; enforcement por view comprovado | criar 1 usuario real de teste e validar login | Alto | Media | `+0.9 p.p.` | homologar criacao assistida com usuario descartavel |
| Atribuir permissoes | Garantir que cada perfil veja apenas o modulo correto | accounts, people, contributions, imports, reports, audit | abrir painel -> atribuir grupo/permissao -> testar acesso -> validar bloqueios | `/accounts/`, modulos protegidos | `services/access_control.py`, `MODULE_PERMISSIONS`, `DEFAULT_GROUPS` | Exige validacao humana | `regression_audit` comprovou `403` para usuarios sem grupo e `200` para superuser | validar atribuicao real de grupos operacionais | Alto | Alta | `+1.1 p.p.` | testar 1 perfil real de secretaria ou financeiro com jornada completa |

### 9. Operacao tecnica: backup, restore, deploy, sync

| Processo | Objetivo operacional | Modulos envolvidos | Fluxo inicio -> fim | Rotas / views relacionadas | Servicos relacionados | Status atual | Validacao automatica disponivel | Validacao humana necessaria | Risco operacional | Prioridade | Ganho estimado para 100% | Proxima acao recomendada |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | ---: | --- |
| Executar backup | Garantir copia recuperavel do banco e dos arquivos operacionais | runtime, postgres, scripts | acionar rotina -> gerar dump/copia -> validar existencia dos artefatos | sem rota UI principal; trilha tecnica documentada | `pg_dump`, scripts locais/runtime, docs operacionais | Parcialmente operacional | documentacao e scripts existem | validar rotina real no servidor da nuvem | Alto | Alta | `+1.0 p.p.` | formalizar e testar 1 backup real com operador da nuvem |
| Executar restore | Provar recuperacao completa do ambiente apos incidente | runtime, postgres, docker | restaurar banco/arquivos -> subir runtime -> validar login, dashboard e fluxos principais | sem rota UI principal; processo tecnico | `pg_restore`/`psql`, Docker runtime, checklist pos-restore | Exige validacao humana | healthcheck e regression audit podem apoiar | executar restore completo em homologacao e validar uso real | Muito alto | Alta | `+1.8 p.p.` | fazer ensaio controlado de restore ponta a ponta |
| Validar configuracoes operacionais | Confirmar que envs, arquivos, branding, uploads e staticfiles estao coerentes | runtime, deploy, docker | revisar ambiente -> rodar checks -> validar arquivos e providers | runtime local/nuvem | `check --deploy`, health, docs operacionais, envs | Parcialmente operacional | `regression_audit` valida arquivos, backend de e-mail, staticfiles e branding | operador tecnico precisa confirmar a trilha e o entendimento | Medio | Media | `+0.8 p.p.` | consolidar checklist curto de operacao e pos-deploy |

## Top 10 jornadas que mais impactam o cliente

1. Importar extrato bancario com PDF real
2. Auditar lote bancario e encerrar sem pendencia indevida
3. Emitir recibo e abrir o PDF correto
4. Reenviar recibo por e-mail na nuvem
5. Lancar envelope e voltar ao lote com contadores corretos
6. Editar envelope ja lancado
7. Importar pessoas por planilha e resolver pendencias
8. Localizar, editar e consolidar uma pessoa por merge
9. Gerar extrato por pessoa
10. Executar restore e voltar a operar sem perda funcional

## Top 10 prioridades restantes

1. Homologar upload real de extrato bancario
2. Homologar envio real de recibo/e-mail via Microsoft Graph
3. Homologar edicao de envelope `lancado`
4. Homologar criacao de pessoa na base nova
5. Homologar merge pequeno com casos reais seguros
6. Homologar exclusao segura com lixeira e purga
7. Executar restore operacional completo
8. Testar criacao de usuario e atribuicao de grupo real
9. Fechar a trilha humana da importacao de pessoas
10. Simplificar a trilha de backup/configuracao operacional

## Conclusao

Pela percepcao do cliente, o Power Church ja esta **muito perto de parecer um sistema completo**, porque as jornadas de consulta, busca, relatorio, exportacao, auditoria e boa parte do financeiro ja se sustentam bem.

O que falta para a sensacao de `100% operacional` nao e mais "ter tela", e sim **provar em uso real** as jornadas mais sensiveis:

- escrita financeira;
- envio real de e-mail/recibo;
- administracao de usuarios;
- recuperacao operacional em caso de incidente.

Quando essas jornadas forem homologadas, o salto de percepcao para o cliente tende a ser muito maior do que o numero bruto de bugs corrigidos, porque sao exatamente elas que definem confianca no sistema entregue.

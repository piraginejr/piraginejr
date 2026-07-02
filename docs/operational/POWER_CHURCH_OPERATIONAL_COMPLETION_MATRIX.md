# Power Church Operational Completion Matrix

- Data: 2026-07-01
- Objetivo: medir se o operador consegue executar, no runtime Django + PostgreSQL + Docker, tudo o que fazia no sistema anterior.
- Escopo: mapeamento e classificacao operacional do estado atual do runtime, considerando as correcoes ja incorporadas ate esta data.

## Fontes usadas

- Rotas Django ativas em:
  - `power_church_django/power_church_site/urls.py`
  - `power_church_django/apps/*/urls.py`
- Views, services e templates dos modulos ativos.
- Relatorios gerados:
  - `reports/regression_audit_20260701_193036.md`
  - `reports/regression_warns_triage_20260701_175638.md`
- Menus visiveis em `power_church_django/templates/power_church_django/base.html`
- Modelos ativos em `power_church_django/apps/*/models.py`
- Rotinas operacionais em `deploy/*` e `scripts/*`
- Funcionalidades ainda visiveis em codigo legado como referencia historica, sem consideralas como prova de operacionalidade atual.
- Bugs e lacunas relatados pelo operador durante a homologacao desta migracao.

## Criterio de classificacao

- `Operacional`: fluxo existe, esta exposto ao operador e possui evidencia tecnica forte de funcionamento.
- `Parcialmente operacional`: fluxo existe e funciona em parte, mas ainda depende de contexto, estado especifico ou cobertura incompleta.
- `Quebrada`: fluxo existe, mas ha evidencia concreta de falha funcional relevante.
- `Ausente`: capacidade esperada nao esta exposta nem coberta no sistema atual.
- `Implementada mas inacessivel`: existe em comando, script ou backend, mas nao chega ao operador comum na interface principal.
- `Exige validacao manual`: ha boa base tecnica, mas ainda falta prova operacional humana do fluxo fim a fim.
- `Melhoria futura`: funciona, mas com fragilidade, lentidao ou ergonomia abaixo do ideal.

## Metodo de estimativa

Os percentuais abaixo sao estimativas operacionais, nao metricas matematicas exatas. Eles consideram:

- cobertura real em tela/rota;
- sinais da `regression_audit`;
- qualidade do acesso do operador;
- pendencias de seguranca, permissao, arquivos, e-mail e operacao;
- relatos reais de uso da homologacao.

## Resumo Executivo

### Resposta curta

Hoje, o operador **consegue executar uma fatia ainda maior do nucleo operacional**, e a principal lacuna estrutural anterior, **o enforcement de permissoes por modulo**, foi fechada com evidencia tecnica no runtime atual.

O sistema agora esta **estimado em 86% de operacionalidade geral**.

O miolo de:

- pessoas,
- contribuicoes,
- envelopes,
- recibos,
- importacoes,
- relatorios,
- PDFs,

ja existe e roda em Django/PostgreSQL com evidencias tecnicas fortes e, agora, com **bloqueio real de acesso por perfil** nas views sensiveis.

O que ainda segura o selo de "100% operacional" nao e mais uma dependencia central do legado, e sim:

- validacao manual de fluxos de escrita mais sensiveis;
- rotinas operacionais de nuvem/restore/rollback ainda muito concentradas em script e operador tecnico;
- configuracoes operacionais ainda espalhadas, sem painel unico;
- alguns fluxos automatizados de e-mail e campanha ainda sem prova operacional completa em nuvem.

### Comparativo Antes / Depois

| Indicador | Antes | Depois | Evolucao |
| --- | ---: | ---: | ---: |
| Operacionalidade geral estimada | `77%` | `86%` | `+9 p.p.` |
| FAILs na `regression_audit` | `0` | `0` | estavel |
| WARNs na `regression_audit` | `19` | `19` | estavel |
| Enforcement de permissao por view | `Quebrada` | `Operacional` | ganho estrutural |
| Usuarios sem grupo acessando modulos sensiveis | `HTTP 200` | `HTTP 403` | corrigido |

### Percentual geral atualizado

- **Operacionalidade geral estimada:** `86%`

### Percentual por area e evolucao

| Area | Antes | Depois | Leitura resumida |
| --- | ---: | ---: | --- |
| Pessoas / Secretaria | `80%` | `83%` | Base forte: lista, ficha, edicao, familia, merge e importacao visiveis. Ganha robustez porque agora a leitura/escrita sensivel esta protegida por perfil. |
| Contribuicoes | `78%` | `81%` | Lista, detalhe, extrato e contribuintes auxiliares existem. Fluxos continuam fortes e agora protegidos por modulo; faltam homologacoes humanas de escrita. |
| Recibos | `82%` | `84%` | Hub, geracao, detalhe, PDF e monitor existem. Falta validar disparo real e fila em campanha viva na nuvem. |
| Envelopes | `76%` | `88%` | Fluxo fim a fim foi reforcado com suporte real a arquivo/pasta local, edicao visivel no lote, lancamento/ignorar/correcao cobertos em teste e auditoria sem FAILs da area. Restam amostras humanas de envelope lancado e sugestao cadastral viva. |
| Importacoes | `72%` | `84%` | Importacao de pessoas segue estavel e o lote bancario nativo agora prepara, reprocessa, audita e encerra com contribuicao nativa/sinalizacao correta; falta rodada humana com PDF bancario real. |
| Auditoria | `88%` | `90%` | Auditoria operacional, tecnica, Django e de e-mails formam um modulo consistente e agora protegido por perfil. |
| Relatorios | `92%` | `93%` | HTML, PDF e filtros principais estao funcionando bem. |
| Exports | `90%` | `90%` | CSV e XLSX, inclusive dinamicos, respondem corretamente; o que resta e performance. |
| Impressoes / PDFs | `89%` | `90%` | PDFs principais estao respondendo; impressao de tela ainda depende de validacao humana de navegador/impressora. |
| Usuarios e permissoes | `52%` | `90%` | Painel, grupos e permissoes padrao existem e o enforcement por view/modulo agora foi comprovado pela `regression_audit`. |
| Configuracoes operacionais | `56%` | `56%` | Regras de centavos e templates de e-mail existem, mas faltam painel unico e controles operacionais consolidados. |
| Backup / operacoes | `74%` | `74%` | Scripts e documentacao existem, mas boa parte ainda e tecnica, nao operacional simples para qualquer operador. |
| E-mails / notificacoes | `73%` | `74%` | Provider, templates, fila e auditoria existem; ainda falta prova operacional forte do envio automatico em campanha real na nuvem. |

### Evolucao por modulo

| Modulo | Antes | Depois | Leitura da evolucao |
| --- | --- | --- | --- |
| Dashboard | Parcialmente confiavel | Operacional com controle de acesso | Continua funcional e agora bloqueia usuarios sem permissao. |
| People | Operacional com risco de seguranca | Operacional protegido | O modulo segue forte; o ganho principal foi governanca de acesso. |
| Contributors | Operacional com risco de seguranca | Operacional protegido | Lista e detalhe seguem estaveis, agora sob `view_contributors` / `manage_contributors`. |
| Contributions | Operacional com risco de seguranca | Operacional protegido | Lista, detalhe, split, extrato e recibos continuam fortes com gate por perfil. |
| Imports | Parcialmente operacional | Parcialmente operacional forte e protegido | Permissoes fecharam e o motor nativo agora cobre preparo, reprocesso e encerramento; resta homologar upload real de PDF bancario e sanear pilotos antigos. |
| Reports | Operacional com risco baixo | Operacional protegido | HTML/PDF seguem estaveis e agora respeitam perfil. |
| Audit | Operacional com risco de exposicao | Operacional protegido | Ganhou fechamento claro do acesso ao modulo. |
| Accounts | Parcialmente operacional | Parcialmente operacional protegido | Painel segue tecnico, mas agora so perfis corretos entram. |

## Bloqueadores imediatos

Os principais bloqueadores para chamar o sistema de "100% operacional" hoje sao:

1. **Fluxos de escrita critica ainda sem homologacao manual completa no runtime atual**
   - Envelopes: lancar, corrigir, ignorar, reabrir por estados diferentes.
   - Importacoes: subir PDF bancario real e validar conciliação em amostra viva do operador.
   - Recibos: campanha automatica real e reprocessamento de fila com dados vivos.

2. **Configuracao operacional espalhada**
   - Parte da operacao fica em tela, parte em script, parte em `.env`, parte em documentos.
   - Impacto: dependencia maior de operador tecnico.

3. **Restante dos WARNs ainda aponta lacunas reais de maturidade**
   - Fotos ausentes em amostras reais.
   - Exportacoes e `/people/families/` com lentidao perceptivel.
   - 6 movimentos de extrato com referencia orfa em lote piloto.

## Pendencias importantes

- limpeza ou reclassificacao dos 6 movimentos piloto ignorados para reduzir ruido tecnico;
- politica operacional para fotos ausentes;
- otimizacao de `/people/families/` e exports;
- prova de restore real do runtime;
- validacao em nuvem de envio automatico via Microsoft Graph;
- consolidacao do que deve permanecer CLI/tecnico e do que precisa virar trilha UI do operador.

## Melhorias futuras

- painel unico de configuracoes operacionais;
- suite state-aware para envelopes e filas;
- drills recorrentes de backup/restore;
- verificacao pos-deploy ainda mais orientada a negocio;
- observabilidade operacional mais forte.

## Matriz Detalhada

### 1. Pessoas / Secretaria

| Area | Capacidade | Status | Evidencia | Arquivo / rota relacionada | Risco operacional | Prioridade | Proxima acao sugerida |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Pessoas / Secretaria | Listar, buscar e filtrar pessoas | Operacional | `regression_audit`: `/people/`, busca por nome, filtro por status e cidade com `200` | `apps/people/views.py:index`, `/people/` | Baixo | Alta | Manter no smoke principal |
| Pessoas / Secretaria | Abrir ficha da pessoa | Operacional | `regression_audit`: `/people/<id>/` com `200` | `apps/people/views.py:detail`, `/people/<id>/` | Baixo | Alta | Manter cobertura |
| Pessoas / Secretaria | Editar ficha | Operacional | `regression_audit`: `/people/<id>/edit/` com `200` | `apps/people/views.py:edit`, `/people/<id>/edit/` | Medio | Alta | Validar POSTs reais por operador |
| Pessoas / Secretaria | Criar nova pessoa | Exige validacao manual | Rota existe, formulario existe, sem prova recente de operacao fim a fim no runtime atual | `apps/people/urls.py`, `/people/new/` | Medio | Alta | Homologar criacao controlada com rollback |
| Pessoas / Secretaria | Merge de pessoas | Exige validacao manual | Tela existe e regras de merge foram bastante trabalhadas, mas a prova atual esta fragmentada em casos anteriores | `apps/people/views.py:merge`, `/people/<id>/merge/` | Medio | Alta | Rodar lote pequeno de casos reais homologados |
| Pessoas / Secretaria | Lixeira segura | Exige validacao manual | Consistencia `OK` e telas existem | `apps/people/views.py:trash`, `/people/trash/` | Medio | Media | Validar exclusao controlada em homologacao |
| Pessoas / Secretaria | Purga final | Exige validacao manual | Fluxo existe e depende de superusuario e confirmacao forte | `apps/people/views.py:purge_trash`, `/people/trash/<id>/purge/` | Alto | Media | Teste manual com massa descartavel |
| Pessoas / Secretaria | Auditoria familiar / criterio amplo | Operacional | `/people/families/` responde `200`; dashboard e tela ativos | `apps/people/views.py:families`, `/people/families/` | Medio por performance | Alta | Otimizar depois da homologacao principal |
| Pessoas / Secretaria | Foto da pessoa: upload e exibicao | Parcialmente operacional | View e service existem; casos `#1` e `#2` sem arquivo retornam `404` corretamente | `apps/people/views.py:photo`, `services/photos.py`, `/people/photo/<id>/` | Medio | Media | Definir politica de fotos ausentes e validar upload |
| Pessoas / Secretaria | Importacao de pessoas por planilha | Exige validacao manual | Tela, POST e lotes existem; POST sem arquivo responde corretamente; fluxo real precisa rodada humana | `apps/people/views.py:imports`, `/people/imports/` | Medio | Alta | Homologar upload real controlado |
| Pessoas / Secretaria | Auditoria do lote de importacao de pessoas | Operacional | Tela mostra pendencias, filtro, impressao e links para ficha/edicao; listas por tipo ja prontas | `templates/power_church_django/people/import_lot.html`, `/people/imports/<lot_id>/` | Baixo | Alta | Manter como fluxo oficial da secretaria |

### 2. Contribuicoes

| Area | Capacidade | Status | Evidencia | Arquivo / rota relacionada | Risco operacional | Prioridade | Proxima acao sugerida |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Contribuicoes | Listar, buscar e filtrar contribuicoes | Operacional | `regression_audit`: `/contributions/`, busca e filtro por periodo `200` | `apps/contributions/views.py:index`, `/contributions/` | Baixo | Alta | Manter cobertura |
| Contribuicoes | Abrir detalhe da contribuicao | Operacional | `regression_audit`: `/contributions/<id>/` com `200` | `apps/contributions/views.py:detail` | Baixo | Alta | Manter cobertura |
| Contribuicoes | Rateio / split de contribuicao | Exige validacao manual | GET do split respondeu `200`; regras recentes foram corrigidas; falta prova humana de varios cenarios | `apps/contributions/views.py:split`, `/contributions/<id>/split/` | Medio | Alta | Homologar 1 item, 2 itens e pessoas diferentes |
| Contribuicoes | Lancamento manual de contribuicao | Exige validacao manual | Rotas `/contributions/new/` e `/contributions/manual/` existem; sem prova automatica forte recente | `apps/contributions/urls.py` | Medio | Media | Validar com rollback controlado |
| Contribuicoes | Extrato HTML por pessoa | Operacional | `regression_audit`: `/contributions/statements/<id>/` com `200` | `apps/contributions/views.py:person_statement` | Baixo | Alta | Manter cobertura |
| Contribuicoes | Extrato PDF por pessoa | Operacional | Bug de `notes` corrigido; `/contributions/statements/2/pdf/` e `/3/pdf/` responderam `200 application/pdf` | `apps/contributions/views.py:person_statement_pdf_view`, `services/contributions_native.py` | Baixo | Alta | Manter como sentinela fixa |
| Contribuicoes | Contribuintes auxiliares: lista e detalhe | Operacional | `regression_audit`: `/contributors/` e `/contributors/<id>/` com `200` | `apps/contributions/views.py:contributors`, `contributor_detail` | Baixo | Alta | Manter cobertura |
| Contribuicoes | Vincular contribuinte auxiliar a pessoa | Exige validacao manual | Services existem e ha indico de uso real, mas nao houve rodada completa atual na matriz | `services/contributors_native.py` | Medio | Media | Homologar via operador com caso controlado |
| Contribuicoes | Criar frequentador a partir de identidade financeira | Implementada mas inacessivel | Ha logica em services legados/historicos, mas sem prova clara de fluxo atual exposto e homologado | `services/legacy_write.py`, `services/contributors_native.py` | Medio | Media | Decidir se entra no escopo operacional atual |

### 3. Recibos

| Area | Capacidade | Status | Evidencia | Arquivo / rota relacionada | Risco operacional | Prioridade | Proxima acao sugerida |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Recibos | Hub de recibos e pesquisa | Operacional | `/receipts/` com `200`, filtros por periodo e busca funcionando | `apps/contributions/views.py:receipts`, `/receipts/` | Baixo | Alta | Manter cobertura |
| Recibos | Buscar pessoa para gerar recibo | Operacional | Tela oferece busca por nome/CPF/codigo/e-mail e link para ficha/extrato | `templates/power_church_django/receipts/list.html` | Baixo | Alta | Manter fluxo como padrao |
| Recibos | Gerar recibo individual / consolidado | Exige validacao manual | View e formulario existem; regras de geracao e envio estao presentes; falta rodada humana focada no runtime atual | `apps/contributions/views.py:receipt_new`, `receipts` | Medio | Alta | Homologar geracao sem envio e com envio |
| Recibos | Detalhe do recibo | Operacional | `regression_audit`: `/receipts/<id>/` com `200` | `apps/contributions/views.py:receipt_detail` | Baixo | Alta | Manter cobertura |
| Recibos | PDF oficial do recibo | Operacional | `regression_audit`: `/receipts/<id>/pdf/` com `200 application/pdf` | `apps/contributions/views.py:receipt_pdf_view` | Baixo | Alta | Manter cobertura |
| Recibos | Monitor de fila de envio | Operacional | `/receipts/queue/` com `200`; filtro, reprocesso e sincronizacao de e-mail existem | `apps/contributions/views.py:receipt_queue_monitor`, `/receipts/queue/` | Medio | Alta | Validar com campanha real e itens vivos |
| Recibos | Fila vazia em ambiente sem campanha | Operacional | Triagem classificou como comportamento esperado | `reports/regression_warns_triage_20260701_175638.md` | Baixo | Baixa | Nenhuma acao imediata |
| Recibos | Campanha consolidada automatica | Implementada mas inacessivel | Existe em comandos `manage.py`, mas nao e um fluxo UI-first para operador comum | `apps/contributions/management/commands/*` | Medio | Media | Decidir se UI ou operacao tecnica continua aceitavel |

### 4. Envelopes

| Area | Capacidade | Status | Evidencia | Arquivo / rota relacionada | Risco operacional | Prioridade | Proxima acao sugerida |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Envelopes | Lista de envelopes | Operacional | `/contributions/envelopes/` com `200` | `apps/contributions/views.py:envelopes` | Baixo | Alta | Manter cobertura |
| Envelopes | Abrir detalhe do envelope | Operacional | `regression_audit`: `/contributions/envelopes/<id>/` com `200` | `apps/contributions/views.py:envelope_detail` | Baixo | Alta | Manter cobertura |
| Envelopes | Abrir imagem/PDF anexado | Operacional | `/contributions/envelopes/<id>/image/` com `200` | `apps/contributions/views.py:envelope_image` | Baixo | Alta | Manter cobertura |
| Envelopes | Abrir proximo pendente do lote | Operacional | `/contributions/envelopes/lots/<id>/next/` redirecionando corretamente; testes de lock por operador presentes | `apps/contributions/views.py:envelope_lot_next`, `apps/contributions/tests.py` | Baixo | Alta | Manter sentinela |
| Envelopes | Controle de concorrencia (`em_digitacao`) | Operacional | Testes unitarios cobrindo skip, reclaim e bloqueio entre operadores | `apps/contributions/tests.py` | Baixo | Alta | Incluir no verificador mestre sempre |
| Envelopes | Lancar envelope pendente | Operacional | `regression_audit` validou `launch` com `200`; testes automatizados cobrem lancamento, criacao de contribuicao e fechamento do lote | `apps/contributions/views.py:envelope_launch`, `apps/contributions/tests.py` | Medio | Alta | Homologar cenarios humanos de rateio multiplo |
| Envelopes | Corrigir envelope lancado | Parcialmente operacional | Service de correcao coberto em teste, detalhe do lote agora expone `Editar`, mas o banco real atual nao trouxe amostra `lancado` recente para a rota | `apps/contributions/views.py:envelope_edit`, `services/envelopes_native.py`, `templates/.../envelope_lot_detail.html` | Medio | Alta | Validar com envelope realmente `lancado` no runtime vivo |
| Envelopes | Ignorar envelope pendente | Operacional | Service coberto em teste e lotes atualizam status corretamente; GET/redirect validado na auditoria | `apps/contributions/views.py:envelope_ignore`, `apps/contributions/tests.py` | Baixo | Media | Manter cobertura e homologar uma rodada humana simples |
| Envelopes | Criar lote manual / subir envelope | Operacional | Criacao de lote e registro manual aceitam upload e caminho/pasta local; testes automatizados cobrem os dois fluxos | `apps/contributions/views.py:envelope_new`, `envelope_lot_new`, `services/envelopes_native.py`, `apps/contributions/tests.py` | Medio | Alta | Homologar com massa operacional do scanner |
| Envelopes | Aplicar/ignorar sugestoes cadastrais por envelope | Parcialmente operacional | Services e rotas existem; sem amostra viva recente de `profile_update` na regressao para exercitar o ciclo completo na UI | `apps/contributions/urls.py`, `services/envelopes_native.py`, `profile-updates/*` | Medio | Media | Validar com envelope que gere telefone/endereco divergente |

### 5. Importacoes

| Area | Capacidade | Status | Evidencia | Arquivo / rota relacionada | Risco operacional | Prioridade | Proxima acao sugerida |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Importacoes | Dashboard de importacoes bancarias | Operacional | `/imports/` com `200`; contadores seguem a mesma regra de pendencia humana do lote | `apps/imports/views.py:index`, `apps/imports/services.py` | Baixo | Alta | Manter cobertura |
| Importacoes | Subir PDF bancario para novo lote | Exige validacao manual | Fluxo `POST /imports/` segue ativo e o parser nativo cria lote Postgres; nesta rodada faltou apenas amostra PDF real para homologacao humana | `apps/imports/views.py:index`, `apps/imports/services.py:create_statement_lot_postgres_native` | Medio | Alta | Rodar 1 extrato real controlado com operador |
| Importacoes | Abrir lote e movimentos | Operacional | `/imports/<kind>/<lot_id>/` e detalhe de movimento ativos | `apps/imports/urls.py`, `templates/imports/lot_detail.html` | Baixo | Alta | Manter cobertura |
| Importacoes | Auditoria de movimento de extrato | Operacional | Tela mostra associacao, documento, status, destino e acao; testes nativos cobrem aprovar com pessoa, aprovar sem pessoa e mesma titularidade | `templates/power_church_django/imports/movement_detail.html`, `apps/imports/tests.py` | Medio | Alta | Manter como fluxo padrao |
| Importacoes | Preparar lote para auditoria | Operacional | Testes criam contribuicao nativa no `prepare`; runtime controlado no Docker confirmou lote preparado com movimentos importados | `apps/imports/services.py:prepare_statement_lot_postgres_native`, `apps/imports/tests.py` | Medio | Alta | Validar com PDF real do operador |
| Importacoes | Reprocessar lote | Operacional | Testes confirmam preservacao da decisao manual “sem vincular pessoa agora” durante o reprocesso | `apps/imports/services.py:reprocess_statement_lot_postgres_native`, `apps/imports/tests.py` | Medio | Alta | Manter sentinela e validar com lote vivo quando houver |
| Importacoes | Encerrar lote manualmente | Operacional | Testes e fluxo controlado no Docker confirmaram bloqueio por pendencia e encerramento apenas depois da auditoria, preservando sem-associacao corretamente | `apps/imports/services.py:close_statement_lot_postgres_native`, `apps/imports/tests.py` | Alto | Alta | Homologar uma rodada humana com amostra viva |
| Importacoes | Regras de centavos | Operacional | `/imports/rules/` com `200`; modulo ativo | `apps/imports/views.py:cent_rules`, `/imports/rules/` | Baixo | Alta | Manter cobertura |
| Importacoes | Importacao de pessoas por planilha | Operacional | Tela, POST, lotes, filtro e impressao seguem ativos; testes existentes cobrem o relatorio filtrado do lote | `apps/people/views.py:imports`, `apps/people/views.py:import_lot`, `apps/people/tests.py` | Baixo | Alta | Manter cobertura e fazer rodada humana apenas de amostra final |
| Importacoes | Historico/processamento de lotes de pessoas | Operacional | Dashboard e detalhe de lotes `#992`, `#991`, `#3`, `#2`, `#1` responderam `200`; nomes filtrados para correcao e impressao seguem disponiveis | `services/people_import_native.py`, `templates/power_church_django/people/import_lot.html`, `reports/regression_audit_20260701_183556.md` | Baixo | Alta | Manter como trilha oficial da secretaria |
| Importacoes | Consistencia de lotes piloto antigos | Parcialmente operacional | 6 movimentos ignorados com `imported_contribution_legacy_id` orfao nao bloqueiam operacao, mas poluem consistencia | `reports/regression_warns_triage_20260701_175638.md` | Baixo no uso, medio tecnico | Media | Decidir limpeza ou reclassificacao |

### 6. Auditoria

| Area | Capacidade | Status | Evidencia | Arquivo / rota relacionada | Risco operacional | Prioridade | Proxima acao sugerida |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Auditoria | Auditoria operacional do cadastro | Operacional | `/audit/` com `200`; filtros por tipo e severidade | `apps/audit/views.py:index`, `/audit/` | Baixo | Alta | Manter cobertura |
| Auditoria | Auditoria tecnica | Operacional | `modo=tecnica` exposto e paginado | `apps/audit/views.py:index` | Baixo | Media | Manter para suporte |
| Auditoria | Eventos Django | Operacional | `modo=django` exposto e paginado | `apps/audit/views.py:index` | Baixo | Media | Manter para rastreabilidade |
| Auditoria | Auditoria de e-mails | Operacional | `modo=emails` exposto com filtros, pessoa, conteudo e status | `apps/audit/views.py:index` | Baixo | Alta | Manter como tela de suporte principal |
| Auditoria | Reenvio de e-mail pela auditoria | Exige validacao manual | Botao e service existem; falta rodada humana recente com caso real | `apps/audit/views.py:email_resend`, `/audit/emails/resend/` | Medio | Media | Homologar com um item de fila falho |

### 7. Relatorios

| Area | Capacidade | Status | Evidencia | Arquivo / rota relacionada | Risco operacional | Prioridade | Proxima acao sugerida |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Relatorios | Relatorio HTML por periodo | Operacional | `/reports/` e filtros por data/competencia com `200` | `apps/reports/views.py:index` | Baixo | Alta | Manter cobertura |
| Relatorios | Relatorio HTML por destino | Operacional | `/reports/destinations/` com filtros e `200` | `apps/reports/views.py:destinations` | Baixo | Alta | Manter cobertura |
| Relatorios | Filtros por tipo/campanha/destino | Operacional | `regression_audit` validou filtros `campanha:3` e `tipo:1` | `apps/reports/views.py` | Baixo | Alta | Manter cobertura |
| Relatorios | Navegacao entre relatorio por periodo e por destino | Operacional | Botoes e links presentes nos templates | `templates/reports/index.html`, `destinations.html` | Baixo | Media | Manter |

### 8. Exports

| Area | Capacidade | Status | Evidencia | Arquivo / rota relacionada | Risco operacional | Prioridade | Proxima acao sugerida |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Exports | Exportacao CSV por preset | Operacional | `regression_audit`: CSV `200` com cabecalho esperado | `apps/people/views.py:export`, `/people/export/?format=csv...` | Baixo | Alta | Manter como sentinela |
| Exports | Exportacao CSV dinamica | Operacional | Colunas selecionadas preservadas | `apps/people/views.py:export` | Baixo | Alta | Manter como sentinela |
| Exports | Exportacao XLSX por preset | Operacional | `200` com content type correto | `apps/people/views.py:export` | Baixo | Alta | Manter como sentinela |
| Exports | Exportacao XLSX dinamica | Operacional | `200` com arquivo valido | `apps/people/views.py:export` | Baixo | Alta | Manter como sentinela |
| Exports | Performance dos exports | Melhoria futura | CSV/XLSX entre ~994 ms e ~1170 ms | `reports/regression_audit_20260701_183556.md` | Medio | Media | Otimizar dataset/query depois da estabilizacao |

### 9. Impressoes / PDFs

| Area | Capacidade | Status | Evidencia | Arquivo / rota relacionada | Risco operacional | Prioridade | Proxima acao sugerida |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Impressoes / PDFs | PDF de recibo | Operacional | `regression_audit`: `/receipts/<id>/pdf/` com `200` | `apps/contributions/views.py:receipt_pdf_view` | Baixo | Alta | Manter cobertura |
| Impressoes / PDFs | PDF de extrato por pessoa | Operacional | Bug recente corrigido; `200` confirmado | `apps/contributions/views.py:person_statement_pdf_view` | Baixo | Alta | Manter sentinela fixa |
| Impressoes / PDFs | PDF de relatorio por periodo | Operacional | `200 application/pdf` | `apps/reports/views.py:contribution_period_pdf_view` | Baixo | Alta | Manter cobertura |
| Impressoes / PDFs | PDF de relatorio por destino | Operacional | `200 application/pdf` | `apps/reports/views.py:contribution_destinations_pdf_view` | Baixo | Alta | Manter cobertura |
| Impressoes / PDFs | Impressao da tela operacional (`window.print`) | Exige validacao manual | Botoes presentes em varias telas, mas a impressao depende do navegador/ambiente do operador | `templates/*` com `window.print()` | Medio | Media | Validar impressao humana em desktop real |
| Impressoes / PDFs | Impressao filtrada do lote de importacao de pessoas | Operacional | Fluxo `print=1` existe e ja foi testado por template/teste | `templates/people/import_lot.html`, `apps/people/tests.py` | Baixo | Media | Manter recurso para triagem |

### 10. Usuarios e Permissoes

| Area | Capacidade | Status | Evidencia | Arquivo / rota relacionada | Risco operacional | Prioridade | Proxima acao sugerida |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Usuarios e permissoes | Login publico | Operacional | `/accounts/login/` responde `200` | `apps/accounts/urls.py` | Baixo | Alta | Manter cobertura |
| Usuarios e permissoes | Relogin / troca de sessao | Operacional | `/accounts/relogin/` ativo | `apps/accounts/views.py:relogin` | Baixo | Media | Manter |
| Usuarios e permissoes | Criar e atualizar usuarios | Operacional | Painel `/accounts/` permite criar primeiro admin e usuarios; formulario funcional | `apps/accounts/views.py:index`, `templates/accounts/index.html` | Medio | Alta | Homologar uma rodada simples no runtime |
| Usuarios e permissoes | Grupos e permissoes padrao | Operacional | `regression_audit`: `grupos=5`, `permissoes=14`, `missing=-` | `services/access_control.py` | Baixo | Alta | Manter |
| Usuarios e permissoes | Enforcement por perfil nas views | Operacional | `regression_audit`: `operador_shell` e `power_church_anonimo` bloqueados com `HTTP 403` em dashboard, people, contributions, imports, reports, audit e accounts | `reports/regression_audit_20260701_183556.md`, `services/access_control.py` | Baixo | Alta | Manter como sentinela fixa da auditoria |
| Usuarios e permissoes | Administracao via Django admin | Exige validacao manual | Link existe; nao ha prova recente de fluxo administrativo real | `templates/accounts/index.html`, `/admin/` | Medio | Media | Homologar com superusuario |

### 11. Configuracoes Operacionais

| Area | Capacidade | Status | Evidencia | Arquivo / rota relacionada | Risco operacional | Prioridade | Proxima acao sugerida |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Configuracoes operacionais | Regras de centavos | Operacional | Tela ativa em `/imports/rules/` | `apps/imports/views.py:cent_rules` | Baixo | Alta | Manter cobertura |
| Configuracoes operacionais | Template de e-mail de recibo | Operacional | Atualizacao embutida no modulo de recibos | `apps/contributions/views.py`, `services/receipt_delivery.py` | Baixo | Alta | Manter e documentar |
| Configuracoes operacionais | Branding / logo institucional | Operacional | `brand_logo` responde `200`; arquivo existe | `power_church_site/views.py`, `/branding/logo` | Baixo | Media | Manter |
| Configuracoes operacionais | Segredos e variaveis do runtime | Implementada mas inacessivel | Fica em `.env`/runtime e documentacao, nao em painel do sistema | `deploy/env.example`, `deploy/runtime.env.postgres.local.example` | Medio | Media | Decidir se continua tecnico ou vira painel admin |
| Configuracoes operacionais | Painel unico de configuracoes do sistema | Ausente | Nao ha modulo unico para e-mail, branding, paths, health, etc. | Navegacao atual e docs operacionais | Medio | Media | Projetar painel operacional unificado |

### 12. Backup / Operacoes

| Area | Capacidade | Status | Evidencia | Arquivo / rota relacionada | Risco operacional | Prioridade | Proxima acao sugerida |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Backup / operacoes | Subir/parar runtime local | Operacional | Scripts ativos e usados na homologacao | `scripts/subir_runtime_postgres_local.sh`, `parar_runtime_postgres_local.sh` | Baixo | Alta | Manter como trilha local |
| Backup / operacoes | Backup do runtime Docker/PostgreSQL | Operacional | Script especifico e documentado | `scripts/powerbackup_runtime.sh` | Medio | Alta | Manter e validar periodicidade na nuvem |
| Backup / operacoes | Restore documentado | Exige validacao manual | Documentacao e script existem, mas falta prova recorrente de restore completo | `deploy/README_INSTALACAO.md`, `deploy/restore_sqlite.sh` | Alto | Alta | Executar drill real de restore em homologacao |
| Backup / operacoes | Atualizacao automatica por `main` | Operacional | Documentada como trilha padrao atual | `deploy/ROTINA_ATUALIZACAO_NUVEM_RUNTIME.md` | Medio | Alta | Manter disciplina de commit/push validado |
| Backup / operacoes | Rollback controlado | Parcialmente operacional | Ha scripts e procedimento, mas caminho padrao mudou para sincronismo por `main` | `scripts/rollback_cloud_release.sh`, `deploy/ROTINA_ATUALIZACAO_NUVEM_RUNTIME.md` | Medio | Media | Fechar roteiro simples de rollback por `revert` + backup |
| Backup / operacoes | Validacao pos-deploy | Operacional | Checklist, health, login e smoke estao documentados | `deploy/CHECKLIST_ATUALIZACAO_CLOUD_RELEASE.md`, `docs/operational/POWER_OPS_DECISION_MATRIX.md` | Medio | Alta | Integrar com matriz operacional |

### 13. E-mails / Notificacoes

| Area | Capacidade | Status | Evidencia | Arquivo / rota relacionada | Risco operacional | Prioridade | Proxima acao sugerida |
| --- | --- | --- | --- | --- | --- | --- | --- |
| E-mails / notificacoes | Provider Microsoft Graph configurado | Operacional | `regression_audit`: configuracao `OK`, sender presente | `services/mail_dispatch.py`, envs runtime | Medio | Alta | Manter e validar em nuvem apos cada release |
| E-mails / notificacoes | Dry-run de envio com anexo | Operacional | `regression_audit` registrou `dry_run` bem-sucedido | `reports/regression_audit_20260701_183556.md` | Baixo | Media | Manter como smoke tecnico |
| E-mails / notificacoes | Envio manual de extrato por e-mail | Exige validacao manual | View e trilha de auditoria existem; falta rodada humana recente completa | `apps/contributions/views.py:person_statement` | Medio | Alta | Homologar no runtime e depois na nuvem |
| E-mails / notificacoes | Envio manual/automatico de recibo | Exige validacao manual | Views, templates, fila e services existem; falta prova operacional completa em campanha real | `apps/contributions/views.py`, `services/receipt_delivery.py` | Alto | Alta | Validar com campanha pequena real |
| E-mails / notificacoes | Monitor e reprocesso da fila | Operacional | Tela rica, filtros, reprocesso e sincronizacao por item/filtro | `templates/receipts/queue_monitor.html` | Medio | Alta | Exercitar com itens reais na nuvem |
| E-mails / notificacoes | Campanha consolidada automatica | Implementada mas inacessivel | Existe por `manage.py`, nao por trilha UI do operador | `apps/contributions/management/commands/run_consolidated_receipt_campaign.py` | Medio | Media | Decidir se segue tecnica ou vira acao de painel |
| E-mails / notificacoes | Auditoria e reenvio de e-mails | Operacional | Tela de auditoria de e-mails e botao de reenviar ativos | `apps/audit/views.py`, `/audit/?modo=emails` | Medio | Alta | Homologar com caso de falha real |

## Top 10 prioridades restantes para chegar a 100%

1. Homologar lancamento de envelope pendente em cenarios reais de 1 linha e multiplas linhas.
2. Homologar correcao de envelope ja lancado em estado compativel.
3. Homologar ignorar envelope com justificativa e retorno correto ao lote.
4. Homologar upload real de PDF bancario e a conciliacao de um lote nativo completo.
5. Validar campanha real de fila de recibos com itens pendentes, enviados e falhos.
6. Validar envio real de recibo via Microsoft Graph em nuvem.
7. Validar envio real de extrato por e-mail em nuvem.
8. Fechar politica de restore real com ensaio completo.
9. Tornar a `regression_audit` sensivel ao estado dos envelopes e reduzir WARNs operacionais residuais.
10. Consolidar configuracoes operacionais dispersas em trilha oficial clara, separando o que continua tecnico do que precisa chegar ao operador.

## Resposta Final Das Perguntas-Chave

### O operador consegue fazer tudo o que fazia antes?

**Ainda nao, mas esta mais perto do que na versao anterior da matriz.**

O operador ja consegue usar boa parte do nucleo do sistema antigo no novo ambiente, especialmente:

- pessoas,
- contribuicoes,
- extratos,
- recibos,
- envelopes,
- relatorios,
- exportacoes,
- auditorias.

Mas ainda faltam principalmente:

- homologacao humana completa de fluxos de escrita critica;
- maturidade operacional total de e-mail, restore e campanha automatica;
- consolidacao das configuracoes e da rotina de operacao.

### O que ainda falta para o Power Church estar operacional por completo?

Falta principalmente fechar a camada de **homologacao de escrita critica e governanca de operacao**, mais do que "migrar tela por tela".

Em termos práticos, o sistema so pode ser chamado de plenamente operacional quando:

- os perfis continuarem limitando o acesso por modulo sem regressao;
- envelopes, recibos, extratos e importacoes estiverem homologados em fluxo humano fim a fim;
- e-mail automatico e fila estiverem provados na nuvem;
- backup, restore e rollback estiverem prontos para incidente real;
- a matriz acima estiver reduzida a pendencias de melhoria, nao de risco operacional.

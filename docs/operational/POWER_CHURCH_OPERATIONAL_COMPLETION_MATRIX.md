# Power Church Operational Completion Matrix

- Data: 2026-07-01
- Objetivo: medir se o operador consegue executar, no runtime Django + PostgreSQL + Docker, tudo o que fazia no sistema anterior.
- Escopo: mapeamento e classificacao operacional. Nenhuma correcao de codigo foi aplicada nesta etapa.

## Fontes usadas

- Rotas Django ativas em:
  - `power_church_django/power_church_site/urls.py`
  - `power_church_django/apps/*/urls.py`
- Views, services e templates dos modulos ativos.
- Relatorios gerados:
  - `reports/regression_audit_20260701_174754.md`
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

Hoje, o operador **consegue executar grande parte do nucleo operacional**, mas **ainda nao consegue fazer "tudo o que fazia antes" com seguranca e maturidade operacional completas**.

O sistema esta **estimado em 77% de operacionalidade geral**.

O miolo de:

- pessoas,
- contribuicoes,
- envelopes,
- recibos,
- importacoes,
- relatorios,
- PDFs,

ja existe e roda em Django/PostgreSQL.

O que ainda segura o selo de "100% operacional" nao e mais uma dependencia central do legado, e sim:

- enforcement de perfis/permissoes;
- validacao manual de fluxos de escrita mais sensiveis;
- rotinas operacionais de nuvem/restore/rollback ainda muito concentradas em script e operador tecnico;
- configuracoes operacionais ainda espalhadas, sem painel unico;
- alguns fluxos automatizados de e-mail e campanha ainda sem prova operacional completa em nuvem.

### Percentual geral estimado

- **Operacionalidade geral estimada:** `77%`

### Percentual por area

| Area | Percentual estimado | Leitura resumida |
| --- | ---: | --- |
| Pessoas / Secretaria | `80%` | Base forte: lista, ficha, edicao, familia, merge e importacao visiveis. Ainda faltam validacoes humanas e maturidade de foto/alguns fluxos de escrita. |
| Contribuicoes | `78%` | Lista, detalhe, extrato e contribuintes auxiliares existem. Fluxos de escrita mais sensiveis ainda pedem rodada humana controlada. |
| Recibos | `82%` | Hub, geracao, detalhe, PDF e monitor existem. Falta validar melhor disparo real e fila em campanha viva. |
| Envelopes | `76%` | Nucleo existe e trava de concorrencia foi tratada, mas lancamento/edicao/ignorar ainda precisam cobertura humana forte por estado. |
| Importacoes | `72%` | Importacao de pessoas e auditoria de extrato existem, mas a trilha de lotes, reprocesso e encerramento ainda precisa homologacao mais profunda. |
| Auditoria | `88%` | Auditoria operacional, tecnica, Django e de e-mails ja formam um modulo consistente. |
| Relatorios | `92%` | HTML e filtros principais estao funcionando bem. |
| Exports | `90%` | CSV e XLSX, inclusive dinamicos, estao respondendo corretamente. |
| Impressoes / PDFs | `89%` | PDFs principais estao respondendo; impressao de tela ainda depende de validacao humana de navegador/impressora. |
| Usuarios e permissoes | `52%` | Painel existe, grupos existem, mas a evidencia atual aponta falha grave de enforcement por perfil nas views. |
| Configuracoes operacionais | `56%` | Regras de centavos e templates de e-mail existem, mas faltam painel unico e controles operacionais consolidados. |
| Backup / operacoes | `74%` | Scripts e documentacao existem, mas boa parte ainda e tecnica, nao operacional simples para qualquer operador. |
| E-mails / notificacoes | `73%` | Provider, templates, fila e auditoria existem; ainda falta prova operacional mais forte do envio automatico em campanha real na nuvem. |

## Bloqueadores imediatos

Os principais bloqueadores para chamar o sistema de "100% operacional" hoje sao:

1. **Permissoes nao comprovadas por view e provavelmente nao aplicadas de fato**
   - Evidencia: a `regression_audit` registrou `operador_shell` e `power_church_anonimo` acessando `dashboard`, `pessoas`, `contribuicoes`, `imports`, `relatorios` e `auditoria` com `HTTP 200`, mesmo sem grupos.
   - Impacto: risco operacional e de seguranca.

2. **Fluxos de escrita critica ainda sem homologacao manual completa no runtime atual**
   - Envelopes: lancar, corrigir, ignorar, reabrir por estados diferentes.
   - Importacoes: preparar, reprocessar e encerrar lote nativo.
   - Recibos: campanha automatica real e reprocessamento de fila com dados vivos.

3. **Configuracao operacional espalhada**
   - Parte da operacao fica em tela, parte em script, parte em `.env`, parte em documentos.
   - Impacto: dependencia maior de operador tecnico.

## Pendencias importantes

- limpeza ou reclassificacao dos 6 movimentos piloto ignorados para reduzir ruido tecnico;
- politica operacional para fotos ausentes;
- otimizacao de `/people/families/` e exports;
- prova de restore real do runtime;
- validacao em nuvem de envio automatico via Microsoft Graph;
- fechamento da matriz de permissao por perfil.

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
| Envelopes | Lancar envelope pendente | Exige validacao manual | GET e contexto ativos; POST precisa prova humana mais ampla por estado e rateio | `apps/contributions/views.py:envelope_launch` | Medio | Alta | Homologar cenarios de 1 e varias linhas |
| Envelopes | Corrigir envelope lancado | Exige validacao manual | Fluxo existe, mas a auditoria mostrou que a acao depende do status certo; precisa rodada manual real | `apps/contributions/views.py:envelope_edit`, `services/envelopes_native.py` | Medio | Alta | Homologar em envelope realmente `lancado` |
| Envelopes | Ignorar envelope pendente | Exige validacao manual | UI e service existem; auditoria automatica cobriu GET/redirect, nao operacao humana fim a fim | `apps/contributions/views.py:envelope_ignore` | Medio | Media | Validar com rollback controlado |
| Envelopes | Criar lote manual / subir envelope | Exige validacao manual | Rotas existem; ainda sem prova automatica forte recente | `apps/contributions/urls.py`, `/contributions/envelopes/new/`, `/lots/new/` | Medio | Media | Homologar com massa operacional |
| Envelopes | Aplicar/ignorar sugestoes cadastrais por envelope | Exige validacao manual | Rotas existem, mas sem cobertura recente na regressao | `apps/contributions/urls.py`, `profile-updates/*` | Medio | Media | Adicionar ao roteiro humano |

### 5. Importacoes

| Area | Capacidade | Status | Evidencia | Arquivo / rota relacionada | Risco operacional | Prioridade | Proxima acao sugerida |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Importacoes | Dashboard de importacoes bancarias | Operacional | `/imports/` com `200` | `apps/imports/views.py:index` | Baixo | Alta | Manter cobertura |
| Importacoes | Abrir lote e movimentos | Operacional | `/imports/<kind>/<lot_id>/` e detalhe de movimento ativos | `apps/imports/urls.py`, `templates/imports/lot_detail.html` | Baixo | Alta | Manter cobertura |
| Importacoes | Auditoria de movimento de extrato | Operacional | Tela mostra associacao, documento, status, destino e acao | `templates/power_church_django/imports/lot_detail.html` | Medio | Alta | Manter como fluxo padrao |
| Importacoes | Preparar lote para auditoria | Exige validacao manual | Botao exposto em lotes nativos; sem rodada automatica de negocio atual | `templates/imports/lot_detail.html` | Medio | Alta | Homologar com lote de teste controlado |
| Importacoes | Reprocessar lote | Exige validacao manual | Botao exposto; falta prova atual do comportamento pos-migracao | `templates/imports/lot_detail.html` | Medio | Alta | Homologar num lote isolado |
| Importacoes | Encerrar lote manualmente | Exige validacao manual | Acao exposta e importante para regra operacional; sem prova recente no runtime | `templates/imports/lot_detail.html` | Alto | Alta | Validar bloqueios e criterio real de encerramento |
| Importacoes | Regras de centavos | Operacional | `/imports/rules/` com `200`; modulo ativo | `apps/imports/views.py:cent_rules`, `/imports/rules/` | Baixo | Alta | Manter cobertura |
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
| Exports | Performance dos exports | Melhoria futura | CSV/XLSX entre ~963 ms e ~1158 ms | `reports/regression_audit_20260701_174754.md` | Medio | Media | Otimizar dataset/query depois da estabilizacao |

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
| Usuarios e permissoes | Enforcement por perfil nas views | Quebrada | `regression_audit`: usuarios sem grupos acessando modulos sensiveis com `HTTP 200` | `reports/regression_audit_20260701_174754.md` | Alto | Critica | Mapear e aplicar gate por permissao em cada modulo |
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
| E-mails / notificacoes | Dry-run de envio com anexo | Operacional | `regression_audit` registrou `dry_run` bem-sucedido | `reports/regression_audit_20260701_174754.md` | Baixo | Media | Manter como smoke tecnico |
| E-mails / notificacoes | Envio manual de extrato por e-mail | Exige validacao manual | View e trilha de auditoria existem; falta rodada humana recente completa | `apps/contributions/views.py:person_statement` | Medio | Alta | Homologar no runtime e depois na nuvem |
| E-mails / notificacoes | Envio manual/automatico de recibo | Exige validacao manual | Views, templates, fila e services existem; falta prova operacional completa em campanha real | `apps/contributions/views.py`, `services/receipt_delivery.py` | Alto | Alta | Validar com campanha pequena real |
| E-mails / notificacoes | Monitor e reprocesso da fila | Operacional | Tela rica, filtros, reprocesso e sincronizacao por item/filtro | `templates/receipts/queue_monitor.html` | Medio | Alta | Exercitar com itens reais na nuvem |
| E-mails / notificacoes | Campanha consolidada automatica | Implementada mas inacessivel | Existe por `manage.py`, nao por trilha UI do operador | `apps/contributions/management/commands/run_consolidated_receipt_campaign.py` | Medio | Media | Decidir se segue tecnica ou vira acao de painel |
| E-mails / notificacoes | Auditoria e reenvio de e-mails | Operacional | Tela de auditoria de e-mails e botao de reenviar ativos | `apps/audit/views.py`, `/audit/?modo=emails` | Medio | Alta | Homologar com caso de falha real |

## Top 20 itens para chegar a 100% operacional

1. Aplicar e comprovar enforcement real de permissao por view/modulo.
2. Homologar lancamento de envelope pendente em cenarios reais de 1 linha e multiplas linhas.
3. Homologar correcao de envelope ja lancado em estado compativel.
4. Homologar ignorar envelope com justificativa e retorno correto ao lote.
5. Homologar preparar, reprocessar e encerrar lote de importacao bancaria nativo.
6. Validar campanha real de fila de recibos com itens pendentes, enviados e falhos.
7. Validar envio real de recibo via Microsoft Graph em nuvem.
8. Validar envio real de extrato por e-mail em nuvem.
9. Fechar politica de restore real com ensaio completo.
10. Amarrar a matriz operacional ao checklist pos-deploy.
11. Tornar a `regression_audit` sensivel ao estado dos envelopes para reduzir falsos WARNs.
12. Decidir se limpa ou preserva os 6 movimentos piloto ignorados com link orfao.
13. Validar criacao de pessoa, merge e lixeira segura com roteiro humano curto.
14. Validar vinculo de contribuinte auxiliar e criacao de frequentador no fluxo atual.
15. Definir politica operacional de fotos ausentes e upload de foto.
16. Otimizar `/people/families/` para reduzir lentidao perceptivel.
17. Otimizar exports CSV/XLSX em cargas maiores.
18. Consolidar configuracoes operacionais dispersas em painel ou trilha oficial clara.
19. Validar `admin/` e a trilha de suporte administrativo em producao.
20. Fechar o mapa final do que continua tecnico/CLI e do que precisa estar na interface do operador.

## Resposta Final Das Perguntas-Chave

### O operador consegue fazer tudo o que fazia antes?

**Ainda nao.**

O operador ja consegue usar boa parte do nucleo do sistema antigo no novo ambiente, especialmente:

- pessoas,
- contribuicoes,
- extratos,
- recibos,
- envelopes,
- relatorios,
- exportacoes,
- auditorias.

Mas ainda faltam:

- seguranca/perfis confiaveis;
- homologacao humana completa de fluxos de escrita critica;
- maturidade operacional total de e-mail, restore e campanha automatica;
- consolidacao das configuracoes e da rotina de operacao.

### O que ainda falta para o Power Church estar operacional por completo?

Falta principalmente fechar a camada de **seguranca operacional, homologacao de escrita critica e governanca de operacao**, mais do que "migrar tela por tela".

Em termos práticos, o sistema so pode ser chamado de plenamente operacional quando:

- os perfis realmente limitarem o acesso por modulo;
- envelopes, recibos, extratos e importacoes estiverem homologados em fluxo humano fim a fim;
- e-mail automatico e fila estiverem provados na nuvem;
- backup, restore e rollback estiverem prontos para incidente real;
- a matriz acima estiver reduzida a pendencias de melhoria, nao de risco operacional.

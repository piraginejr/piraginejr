# Mapa Zero Hibrido Base Unica V1

## Objetivo

Eliminar a dependencia operacional do legado `SQLite` e consolidar a aplicacao em uma base unica de desenvolvimento e operacao sobre `Django + PostgreSQL`.

## Estado Final Alcancado No Runtime Operacional

Hoje, o `runtime operacional ativo` ja esta concentrado no `PostgreSQL`.

Isso significa que os fluxos principais usados no sistema novo ficaram no trilho nativo:

- cadastro web ativo;
- familias e merge assistido;
- extrato bancario e auditoria de movimento;
- contribuicoes e rateio;
- envelopes manuais, lotes e pendentes;
- pendencias cadastrais por envelope;
- recibos, fila e reenvio;
- relatorios financeiros;
- auditoria operacional e tecnica;
- contribuintes auxiliares e lookup de envelope.
- importacao de pessoas com dashboard, lote e auditoria materializados no Postgres.

## O Que Foi Fechado Em Postgres

### Cadastro

- `nova pessoa`, `editar pessoa`, `familia`, `lixeira segura` e `merge` em [people_native_write.py](/Users/piraginejr/Documents/New project/Teste/Power Church/power_church_django/services/people_native_write.py)
- leituras principais de pessoas, ficha e familias apoiadas nos snapshots de [models.py](/Users/piraginejr/Documents/New project/Teste/Power Church/power_church_django/apps/people/models.py)
- lotes de importacao de pessoas, pendencias e linhas espelhados em [people_import_native.py](/Users/piraginejr/Documents/New project/Teste/Power Church/power_church_django/services/people_import_native.py)

### Financeiro

- contribuicoes nativas em [contributions_native.py](/Users/piraginejr/Documents/New project/Teste/Power Church/power_church_django/services/contributions_native.py)
- envelopes nativos em [envelopes_native.py](/Users/piraginejr/Documents/New project/Teste/Power Church/power_church_django/services/envelopes_native.py)
- sugestoes cadastrais de envelopes em [envelopes_native.py](/Users/piraginejr/Documents/New project/Teste/Power Church/power_church_django/services/envelopes_native.py)
- recibos e fila nativos em [receipt_delivery.py](/Users/piraginejr/Documents/New project/Teste/Power Church/power_church_django/services/receipt_delivery.py)
- extratos nativos e snapshots bancarios em [imports/services.py](/Users/piraginejr/Documents/New project/Teste/Power Church/power_church_django/apps/imports/services.py)

### Relatorios E Auditoria

- relatorios nativos em [reports_native.py](/Users/piraginejr/Documents/New project/Teste/Power Church/power_church_django/services/reports_native.py)
- auditoria nativa em [audit_native.py](/Users/piraginejr/Documents/New project/Teste/Power Church/power_church_django/services/audit_native.py)
- contribuintes auxiliares nativos em [contributors_native.py](/Users/piraginejr/Documents/New project/Teste/Power Church/power_church_django/services/contributors_native.py)

## Evidencias Objetivas

Validacoes confirmadas no Postgres local:

- `PersonSnapshot`: `1559`
- `PersonSnapshot` ativos: `1527`
- `NativeContribution`: `4217`
- `NativeEnvelopeLot`: `5`
- `NativeEnvelope`: `244`
- `NativeEnvelopeItem`: `325`
- `NativeAuxContributor`: `701`
- `CentRuleSnapshot`: `14`

Servicos nativos validados nesta etapa:

- relatorio por periodo: `1043` grupos e total `R$ 2.880.892,24`
- relatorio por destino: `24` destinos
- auditoria operacional: `177` itens
- auditoria tecnica: `8586` eventos
- busca de recibo/pessoa: `20` resultados para `Maria`
- contribuintes auxiliares: `701` registros, `31` blocos familiares e `95` associacoes sugeridas

## O Que Ainda Existe Do Legado

O legado `ainda existe no repositorio`, mas agora fica restrito a estes papeis:

- historico e contingencia de referencia;
- scripts de migracao e backfill;
- utilitarios antigos que nao fazem mais parte do trilho principal;
- parser e normalizadores historicos reaproveitados por servicos novos;
- funcoes auxiliares de formatacao e compatibilidade.

Em outras palavras:

- `legado em runtime operacional principal`: nao
- `legado como ferramental/historico`: sim

## Leitura Honesta Do Zero Hibrido

Para a meta pratica de `base unica para continuar desenvolvendo`, o projeto chegou no ponto desejado:

- a operacao principal esta em `PostgreSQL`;
- os modulos novos podem ser desenvolvidos sobre a base nova;
- o `SQLite` deixou de ser dependencia do fluxo principal do sistema.

O que fica fora dessa afirmacao:

- scripts de migracao que ainda leem o legado para conferencias pontuais;
- funcoes antigas mantidas no codigo por historico, mas nao mais no caminho operacional padrao;
- pendencias deliberadamente adiadas que nao bloqueiam a base unica.

## Criterio Pratico De Conclusao

Esta meta passa a ser considerada concluida porque:

1. o fluxo web principal nao faz mais chamada direta operacional para `connect_legacy()`, `connect_legacy_write()` ou `PowerChurchDB(legacy_db_path())`;
2. os dados operacionais principais ja existem e ja sao lidos/escritos no Postgres;
3. recibos, envelopes, contribuicoes, extratos, familias e auditoria ja tem caminho nativo;
4. o legado deixou de ser necessario para operar o sistema no dia a dia.

## Pendencias Deliberadamente Adiadas

Itens importantes, mas `fora da trilha critica da base unica`, continuam registrados em:

- [PENDENCIAS_POS_ZERO_HIBRIDO_V1.md](/Users/piraginejr/Documents/New project/Teste/Power Church/PENDENCIAS_POS_ZERO_HIBRIDO_V1.md)

Principal destaque:

- `merge em lote controlado`, tratado como melhoria posterior, sem bloquear a conclusao do zero hibrido operacional.

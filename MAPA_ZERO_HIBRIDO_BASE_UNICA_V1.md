# Mapa Zero Hibrido Base Unica V1

## Objetivo

Eliminar a dependencia operacional do legado `SQLite` e concentrar a aplicacao em uma base unica de desenvolvimento e operacao sobre `Django + PostgreSQL`.

## Leitura Honesta Do Estado Atual

Hoje o projeto ja validou:

- operacao local no ambiente `Django + PostgreSQL`;
- snapshots de pessoas, financeiro e recibos;
- fila de recibos;
- lotes bancarios oficiais de maio/2026;
- verificador proprio da Fase 4.

Mas o projeto `ainda nao esta zero hibrido`.

O que existe hoje e:

- `PostgreSQL` como camada moderna validada;
- `SQLite legado` ainda sendo usado em partes da leitura e principalmente da escrita.

## Onde O Hibrido Ainda Existe

### 1. Escrita Cadastral

Arquivo principal:

- [legacy_write.py](/Users/piraginejr/Documents/New project/Teste/Power Church/power_church_django/services/legacy_write.py)

Fluxos ainda gravando no legado:

- `create_person`
- `update_person`
- `merge_people`
- `create_person_relationship`
- `update_person_relationship`
- criacao/ajuste de contribuintes auxiliares

Impacto:

- cadastro continua nascendo no `SQLite`;
- o Postgres recebe espelho depois.

### 2. Escrita Financeira e Bancaria

Arquivos principais:

- [legacy_write.py](/Users/piraginejr/Documents/New project/Teste/Power Church/power_church_django/services/legacy_write.py)
- [legacy_bank_write.py](/Users/piraginejr/Documents/New project/Teste/Power Church/power_church_django/services/legacy_bank_write.py)

Fluxos ainda gravando no legado:

- `create_contribution`
- `split_contribution`
- `create_envelope_image_lot`
- `create_envelope_contribution_batch`
- `launch_pending_envelope`
- geracao de recibos em:
  - `issue_receipt_for_contribution_ids`
  - `issue_period_receipts`
  - `issue_receipts_for_event_contributions`
  - `create_receipt`
- importacao/acoes de extrato:
  - `create_statement_lot_from_upload`
  - `reprocess_bank_lot`
  - `prepare_statement_lot_for_audit`
  - `close_bank_lot`

Impacto:

- o coracao financeiro ainda escreve primeiro no `SQLite`.

### 3. Leitura Residual De Dominio

Arquivo principal:

- [legacy.py](/Users/piraginejr/Documents/New project/Teste/Power Church/power_church_django/services/legacy.py)

Leituras ainda apoiadas no legado:

- detalhe de contribuicao
- detalhe de envelope
- parte da listagem de contribuicoes/envelopes/recibos/contribuintes
- `get_import_lot_detail`
- `get_bank_movement_detail`
- `person_statement_data`
- buscas de recibo/pessoa usadas em alguns fluxos

Impacto:

- varias telas ainda dependem de fallback ou leitura direta do `SQLite`.

### 4. Exportacoes E Utilitarios

Arquivo principal:

- [data_exchange.py](/Users/piraginejr/Documents/New project/Teste/Power Church/power_church_django/services/data_exchange.py)

Pontos residuais:

- exportacoes ainda com leituras do legado;
- alguns relatorios auxiliares ainda dependem da camada antiga.

### 5. Views Que Ainda Encostam No Legado

Arquivos principais:

- [imports/views.py](/Users/piraginejr/Documents/New project/Teste/Power Church/power_church_django/apps/imports/views.py)
- [imports/services.py](/Users/piraginejr/Documents/New project/Teste/Power Church/power_church_django/apps/imports/services.py)
- [contributions/views.py](/Users/piraginejr/Documents/New project/Teste/Power Church/power_church_django/apps/contributions/views.py)

Exemplos:

- detalhe de lote com fallback legado;
- detalhe de movimento com fallback legado;
- imagem de envelope lida por caminho vindo do legado;
- algumas consultas de extrato e recibo ainda usam funcoes antigas.

## O Que Significa Zero Hibrido Na Pratica

Para chamar de `base unica`, precisamos chegar a este estado:

1. toda `escrita` nasce no Postgres;
2. toda `leitura operacional` vem do Postgres;
3. o `SQLite` vira apenas:
   - backup historico congelado;
   - ou fonte de migracao unica, nao mais runtime.

## Ordem Recomendada De Corte

### Bloco A - Escrita Bancaria E Lote De Extrato

Objetivo:

- tirar do legado o fluxo de `extrato`.

Inclui:

- criacao de lote
- reprocessamento
- auditoria de movimento
- preparacao para auditoria
- encerramento manual

Motivo para vir primeiro:

- ja existe snapshot forte;
- e o extrato e o coracao da operacao atual.

### Bloco B - Recibos E Fila Nativos

Objetivo:

- fazer recibo e dispatch nascerem sem precisar gravar recibo no legado antes.

Inclui:

- emissao manual
- emissao por competencia
- consolidado
- automatico por evento/extrato
- fila e reenvio

Motivo:

- ja existe `ReceiptSnapshot`;
- falta trocar a escrita de origem.

### Bloco C - Escrita De Contribuicoes E Envelopes

Objetivo:

- mover para Postgres:
  - contribuicoes
  - rateios
  - envelopes
  - lotes de envelopes

Motivo:

- esse bloco fecha o financeiro fora do banco antigo.

### Bloco D - Cadastro, Familia E Merge

Objetivo:

- fazer `pessoa`, `familia` e `merge` nascerem direto no Postgres.

Motivo:

- a leitura ja esta mais madura;
- falta cortar a escrita.

### Bloco E - Leitura Residual, Exportacoes E Relatorios

Objetivo:

- remover os fallbacks finais do legado.

Inclui:

- `data_exchange.py`
- `legacy.py` residual
- detalhes e listagens ainda antigos

Motivo:

- esse e o acabamento final para dizer que o runtime ficou limpo.

## Sequencia Mais Rapida Sem Retrabalho

Para acelerar e evitar pontas soltas, eu recomendo:

1. `Extrato + recibos/fila`
2. `Contribuicoes + envelopes`
3. `Cadastro + familia + merge`
4. `Exportacoes + relatorios + fallbacks`

Assim a gente corta primeiro:

- o financeiro central;
- depois o cadastro;
- e deixa o polimento de leitura residual por ultimo.

## Critero Objetivo De Fim

So vamos chamar de `zero hibrido` quando:

- `rg` nao apontar mais runtime operacional relevante em:
  - `connect_legacy()`
  - `connect_legacy_write()`
  - `PowerChurchDB(legacy_db_path())`
- o verificador oficial puder checar apenas Postgres;
- o SQLite deixar de ser necessario para:
  - criar
  - editar
  - importar
  - auditar
  - emitir recibo
  - encerrar lote

## Conclusao

Hoje:

- `migracao validada`: sim
- `base unica`: ainda nao

Para chegar a `zero hibrido`, o proximo trabalho certo nao e mais infraestrutura.

O proximo trabalho certo e:

- cortar a `escrita bancaria/financeira`,
- depois `recibos`,
- depois `cadastro`,
- e por fim limpar os fallbacks restantes.

# Inventario Fase 1 Acoplamentos Ao Legado V1

## 1. Objetivo

Executar a `Fase 1` do plano de migracao local para a arquitetura alvo, identificando:

- o que ja pode operar no modelo moderno do Django;
- o que ainda depende do banco legado `SQLite`;
- quais modulos representam maior risco de migracao;
- qual deve ser a ordem tecnica das proximas fases.

Data desta leitura: `03/06/2026`.

## 2. Resumo Executivo

Hoje o Power Church ja funciona em uma arquitetura `hibrida`.

Existem dois mundos convivendo:

- `Django moderno`, com capacidade nativa de rodar em `PostgreSQL`;
- `nucleo legado`, ainda fortemente acoplado ao arquivo `data/power_church_membros_importado.db`.

Conclusao principal da Fase 1:

- migrar o `banco default do Django` para `PostgreSQL` local e viavel ja na proxima fase;
- isso `nao elimina` o legado;
- a primeira arquitetura alvo local deve ser `Django em PostgreSQL + ponte temporaria para o SQLite legado`.

## 3. Estado Atual Da Arquitetura

### 3.1 Banco do Django

O projeto ja suporta `PostgreSQL` no banco `default` do Django via ambiente em [settings.py](/Users/piraginejr/Documents/New project/Teste/Power Church/power_church_django/power_church_site/settings.py:106).

Variaveis ja previstas:

- `POWER_CHURCH_POSTGRES_DB`
- `POWER_CHURCH_POSTGRES_USER`
- `POWER_CHURCH_POSTGRES_PASSWORD`
- `POWER_CHURCH_POSTGRES_HOST`
- `POWER_CHURCH_POSTGRES_PORT`

Na ausencia dessas variaveis, o Django hoje cai no banco local `SQLite`.

### 3.2 Banco legado

O sistema tambem depende do banco legado configurado por `POWER_CHURCH_LEGACY_DB_PATH` em [settings.py](/Users/piraginejr/Documents/New project/Teste/Power Church/power_church_django/power_church_site/settings.py:125).

Padrao atual:

- [power_church_membros_importado.db](/Users/piraginejr/Documents/New project/Teste/Power Church/data/power_church_membros_importado.db)

Esse banco legado continua sendo a fonte de verdade de boa parte das operacoes de:

- pessoas;
- familias;
- contribuicoes;
- envelopes;
- recibos;
- importacoes bancarias;
- conciliacao financeira;
- historico e auditoria de negocio.

## 4. Evidencias Tecnicas Principais

### 4.1 Camada de leitura do legado

A principal camada de leitura e [legacy.py](/Users/piraginejr/Documents/New project/Teste/Power Church/power_church_django/services/legacy.py:100).

Pontos observados:

- `legacy_db_path()` em [legacy.py](/Users/piraginejr/Documents/New project/Teste/Power Church/power_church_django/services/legacy.py:100)
- `connect_legacy()` em [legacy.py](/Users/piraginejr/Documents/New project/Teste/Power Church/power_church_django/services/legacy.py:104)
- pelo menos `41` blocos `with connect_legacy()` dentro do mesmo arquivo

Leitura pratica:

- buscas;
- dashboards;
- familias;
- recibos;
- extratos;
- relatorios;
- exportacoes;
- detalhes operacionais continuam consultando o `SQLite legado`.

### 4.2 Camada de escrita no legado

A principal camada de escrita e [legacy_write.py](/Users/piraginejr/Documents/New project/Teste/Power Church/power_church_django/services/legacy_write.py:115).

Pontos observados:

- `connect_legacy_write()` em [legacy_write.py](/Users/piraginejr/Documents/New project/Teste/Power Church/power_church_django/services/legacy_write.py:115)
- pelo menos `34` blocos `with connect_legacy_write()` no arquivo

Essa camada cobre operacoes sensiveis como:

- cadastro e edicao de pessoas;
- merge de fichas;
- contribuicoes;
- rateios;
- envelopes;
- lotes de envelopes;
- recibos;
- ajustes de email/cadastro;
- importacao de pessoas.

### 4.3 Importacao bancaria e OCR

O modulo [legacy_bank_write.py](/Users/piraginejr/Documents/New project/Teste/Power Church/power_church_django/services/legacy_bank_write.py) ainda usa diretamente `PowerChurchDB(legacy_db_path())`.

Ocorrencias observadas:

- [legacy_bank_write.py:302](/Users/piraginejr/Documents/New project/Teste/Power Church/power_church_django/services/legacy_bank_write.py:302)
- [legacy_bank_write.py:329](/Users/piraginejr/Documents/New project/Teste/Power Church/power_church_django/services/legacy_bank_write.py:329)
- [legacy_bank_write.py:342](/Users/piraginejr/Documents/New project/Teste/Power Church/power_church_django/services/legacy_bank_write.py:342)
- [legacy_bank_write.py:365](/Users/piraginejr/Documents/New project/Teste/Power Church/power_church_django/services/legacy_bank_write.py:365)

Conclusao:

- importacao de extratos e PIX ainda depende diretamente do banco legado e da logica antiga.

### 4.4 Fila de recibos e e-mails

Aqui o sistema ja esta em estado `mais moderno`.

Modelos Django:

- [ReceiptEmailTemplate](/Users/piraginejr/Documents/New project/Teste/Power Church/power_church_django/apps/contributions/models.py:6)
- [ReceiptDispatch](/Users/piraginejr/Documents/New project/Teste/Power Church/power_church_django/apps/contributions/models.py:26)

Servico principal:

- [receipt_delivery.py](/Users/piraginejr/Documents/New project/Teste/Power Church/power_church_django/services/receipt_delivery.py)

Leitura honesta:

- a fila, o monitor e o historico de envio ja sao bons candidatos para `PostgreSQL`;
- mas a composicao do recibo ainda consulta dados do legado por `connect_legacy()` e `get_receipt_detail(...)`.

Portanto:

- a `operacao de envio` esta modernizada;
- o `conteudo de negocio` do recibo ainda depende do legado.

### 4.5 Exportacao e relatorios

O modulo [data_exchange.py](/Users/piraginejr/Documents/New project/Teste/Power Church/power_church_django/services/data_exchange.py:261) ainda consulta o legado diretamente por `connect_legacy()`.

Conclusao:

- a exportacao dinamica de pessoas ainda nao esta pronta para um banco de negocio totalmente moderno.

## 5. Tabelas Legadas Principais

O banco legado atual contem, entre outras, as seguintes tabelas criticas:

- `pessoas`
- `pessoa_contatos`
- `pessoa_enderecos`
- `pessoa_relacionamentos`
- `contribuintes`
- `contribuicoes`
- `recibos`
- `recibo_itens`
- `envelopes`
- `envelope_itens`
- `envelope_lotes`
- `extrato_lotes`
- `extrato_movimentos`
- `pix_lotes`
- `pix_movimentos`
- `rateios_lancamento`
- `lancamentos_financeiros`
- `auditoria`
- `import_lotes`
- `import_linhas`
- `import_pendencias`

Alguns volumes atuais da base:

- `pessoas`: `1559`
- `contribuicoes`: `7447`
- `recibos`: `579`
- `envelopes`: `244`
- `extrato_movimentos`: `3223`
- `auditoria`: `1739954`

Leitura importante:

- a tabela `auditoria` ja e muito grande;
- isso reforca a necessidade de migracao em camadas e teste de performance antes da nuvem.

## 6. Dependencias De Filesystem

### 6.1 Branding

A logo institucional vem de `POWER_CHURCH_BRAND_LOGO_PATH` em [settings.py](/Users/piraginejr/Documents/New project/Teste/Power Church/power_church_django/power_church_site/settings.py:129).

### 6.2 Uploads de envelopes

O root dos envelopes vem de [envelope_upload_root()](/Users/piraginejr/Documents/New project/Teste/Power Church/power_church_django/services/legacy_write.py:126), que hoje deriva da pasta do banco legado:

- [legacy_write.py:130](/Users/piraginejr/Documents/New project/Teste/Power Church/power_church_django/services/legacy_write.py:130)

Isso significa que a organizacao de arquivos ainda esta implicitamente colada ao legado.

### 6.3 Outras pastas operacionais

Ja ha uso real de pastas como:

- `data/envelope_uploads`
- `data/people_uploads`
- `data/pix_uploads`
- `data/statement_uploads`
- `data/homologacao`

Em [Dockerfile.django](/Users/piraginejr/Documents/New project/Teste/Power Church/Dockerfile.django:16), essas pastas ja sao preparadas como se fossem volumes de servidor.

Conclusao:

- a organizacao de arquivos ja aponta para um modelo portavel;
- mas ainda precisa ser formalizada como volumes persistentes e paths mais independentes do Mac.

## 7. Classificacao Por Area Funcional

### 7.1 Ja candidatos a PostgreSQL agora

- fila de recibos
- modelos Django de email
- parte da auditoria de envio
- configuracao do site
- contas/autenticacao Django
- suporte visual e templates

Risco de migracao:

- `baixo`

### 7.2 Hibridos

- recibos
- extratos
- monitor da fila
- auditoria de emails
- exportacao dinamica

Risco de migracao:

- `medio`

Motivo:

- operam parcialmente no Django moderno, mas ainda dependem de leitura de negocio no legado.

### 7.3 Fortemente acoplados ao legado

- pessoas
- familias
- contribuicoes
- rateio
- envelopes
- importacoes bancarias
- conciliacao PIX/transferencia
- merge de fichas
- historico operacional de negocio

Risco de migracao:

- `alto`

Motivo:

- essas areas ainda leem e escrevem diretamente no `SQLite legado`.

## 8. Matriz De Risco

### 8.1 Risco alto

- `contribuicoes`
- `envelopes`
- `importacoes`
- `merge`
- `recibos` no ponto em que dependem do detalhe financeiro legado

Essas areas nao devem ser reescritas em big bang.

### 8.2 Risco medio

- `familias`
- `pessoas`
- `relatorios`
- `exportacoes`
- `extratos`

Aqui ha espaco para migracao incremental.

### 8.3 Risco baixo

- `autenticacao Django`
- `templates de email`
- `monitor da fila`
- `auditoria de envios`
- `configuracao de deploy`

## 9. Leitura Do Deploy Atual

O material de deploy ja mostra a intencao de uma arquitetura mais moderna:

- [env.example](/Users/piraginejr/Documents/New project/Teste/Power Church/deploy/env.example:15) ja reserva variaveis para `PostgreSQL`
- [Dockerfile.django](/Users/piraginejr/Documents/New project/Teste/Power Church/Dockerfile.django:1) ja empacota o Django com `gunicorn`
- o mesmo Dockerfile ainda instala `sqlite3`, o que confirma a expectativa de convivencia temporaria entre `Django moderno` e `legado`

Conclusao:

- o deploy alvo ja esta desenhado para uma fase hibrida;
- a Fase 1 confirma que esse desenho faz sentido.

## 10. O Que Pode Migrar Ja Na Fase 2

Podemos seguir com seguranca para:

- instalar `PostgreSQL` local;
- mover o banco `default` do Django para `PostgreSQL`;
- aplicar migrations Django;
- manter o legado `SQLite` como ponte temporaria;
- validar login, fila, monitor, emails, auditoria Django e modelos modernos.

## 11. O Que Ainda Nao Pode Ser Tratado Como Concluido

Ainda nao podemos afirmar que:

- pessoas ja estao prontas para sair do legado;
- contribuicoes ja podem abandonar o `SQLite`;
- envelopes e importacoes ja estao prontos para um banco unico moderno;
- recibos ja ficaram totalmente independentes do legado.

## 12. Recomendacao Tecnica Da Fase 1

Seguir assim:

1. `Fase 2`
   Preparar `PostgreSQL` local e `.env` da arquitetura alvo.

2. `Fase 3`
   Migrar o banco `default` do Django para `PostgreSQL`.

3. `Fase 4`
   Operar em modo `hibrido controlado`:
   - Django moderno em Postgres
   - nucleo legado ainda em SQLite

4. Depois disso
   Escolher, com evidencia, qual subdominio atacar primeiro na reducao do legado:
   - recibos
   - contribuicoes
   - pessoas/familias
   - importacoes

## 13. Criterio De Encerramento Da Fase 1

Considero a `Fase 1` concluida porque agora temos:

- mapa claro do banco moderno e do banco legado;
- classificacao de risco por area;
- identificacao objetiva dos pontos de leitura e escrita no legado;
- leitura honesta do que ja pode ir para Postgres;
- base tecnica suficiente para iniciar a `Fase 2`.

# Auditoria Tecnica Power Church - Django/PostgreSQL/API/Flutter V1

Data: 2026-06-27
Repositorio: `piraginejr/piraginejr`
Branch analisada: `main`

## 1. Objetivo

Este documento registra a primeira auditoria tecnica do Power Church na fase em que o projeto ja possui Django, PostgreSQL via Docker runtime e caminho futuro para API e aplicativo Flutter.

O objetivo nao e substituir a arquitetura funcional ja existente, mas criar uma trilha tecnica clara para:

- consolidar o runtime Docker/PostgreSQL;
- preparar uma API segura;
- permitir app Flutter para iOS e Android;
- manter compatibilidade com a aplicacao web Django atual;
- reduzir risco de retrabalho na evolucao do sistema.

## 2. Estado Atual Identificado

### 2.1 Aplicacao Django

O projeto Django esta localizado em:

```text
power_church_django/
```

Apps Django atualmente identificados em `INSTALLED_APPS`:

```text
accounts
people
contributions
imports
audit
reports
```

Tambem ha uso de bibliotecas importantes para o painel web atual:

```text
auditlog
crispy_forms
crispy_bootstrap5
django_filters
django_tables2
djmoney
formtools
guardian
import_export
waffle
whitenoise
```

Leitura tecnica: a base atual esta organizada como uma aplicacao web Django tradicional, com templates, sessoes, permissoes e rotas web. Ainda nao ha uma camada API separada identificada.

### 2.2 Banco de dados

A configuracao atual usa PostgreSQL quando as variaveis `POWER_CHURCH_POSTGRES_*` estao presentes. Caso contrario, cai para SQLite.

Leitura tecnica:

- o fallback para SQLite e util para desenvolvimento local simples;
- o runtime Docker novo deve padronizar PostgreSQL;
- para producao e app mobile, o PostgreSQL deve ser tratado como banco principal.

### 2.3 Docker runtime

Arquivos principais:

```text
Dockerfile.django
docker-compose.runtime.yml
deploy/docker-entrypoint-django-runtime.sh
deploy/runtime.env.postgres.local.example
```

A separacao entre codigo-fonte e runtime local esta correta:

```text
Repositorio Git / projeto
  -> codigo, Dockerfile, compose, scripts, app Django

~/power_church_postgres_runtime
  -> env, postgres, data, logs, reports, volumes operacionais
```

Esta separacao evita misturar codigo versionado com dados locais sensiveis.

### 2.4 Rotas atuais

As rotas atuais expostas no projeto sao rotas web:

```text
/
/accounts/
/people/
/contributors/
/contributions/
/receipts/
/imports/
/audit/
/reports/
/admin/
```

Leitura tecnica: ainda nao ha namespace `/api/` para consumo por aplicativo Flutter.

### 2.5 Seguranca de sessao

O projeto ja possui middleware proprio para:

- exigir login fora de uma lista de rotas publicas;
- encerrar sessao por inatividade;
- impedir cache de paginas autenticadas.

Leitura tecnica: isto e bom para a aplicacao web. Para app mobile, sera necessario outro modelo de autenticacao, preferencialmente token/JWT ou sessao API bem controlada.

## 3. Pontos Fortes

1. O projeto ja possui visao modular documentada.
2. O Django esta separado do runtime operacional.
3. O PostgreSQL ja esta previsto e funcional no runtime Docker.
4. O uso de variaveis de ambiente ja esta bem encaminhado.
5. A aplicacao ja possui preocupacao com auditoria, permissoes, importacao e relatorios.
6. O dominio principal ja esta emergindo com clareza: pessoas, contribuicoes, recibos, importacao, auditoria e relatorios.
7. A estrategia de nao fazer acesso direto ao banco pelo futuro app esta correta: o app deve consumir API.

## 4. Riscos e Pontos de Atencao

### 4.1 API ainda nao existe

Nao foi identificada dependencia de Django REST Framework nem rotas `/api/`.

Risco: se o Flutter for iniciado antes da API, havera improvisacao, duplicacao de logica ou exposicao indevida de dados.

Decisao recomendada: criar a API antes do app.

### 4.2 Autenticacao web e mobile devem ser separadas

A aplicacao web usa sessao Django. O app mobile nao deve depender das mesmas telas de login.

Decisao recomendada:

- manter login web atual para o painel administrativo;
- criar login API para Flutter;
- usar tokens com expiracao e refresh token;
- planejar escopos/permissoes por perfil.

### 4.3 Dados sensiveis

O projeto lida com dados pessoais, CPF, e-mail, telefone, historico, contribuicoes e recibos.

Riscos:

- exposicao indevida via API;
- log de informacoes sensiveis;
- permissao ampla demais para usuarios comuns;
- backups sem protecao.

Decisao recomendada: aplicar principio de menor privilegio desde a primeira API.

### 4.4 Defaults de ambiente

O `Dockerfile.django` define `POWER_CHURCH_DJANGO_ALLOWED_HOSTS=*` como default. O exemplo de runtime restringe para `127.0.0.1,localhost`.

Risco: em producao, se o env correto nao for configurado, o sistema pode subir com hosts amplos demais.

Decisao recomendada: manter `ALLOWED_HOSTS` sempre explicito no runtime e evitar wildcard em ambientes de nuvem/producao.

### 4.5 Migrations automaticas no entrypoint

O entrypoint executa:

```text
python manage.py migrate --noinput
python manage.py collectstatic --noinput
```

Isso e pratico para ambiente local/homologacao.

Risco: em producao, migracoes automaticas podem causar indisponibilidade se houver mudancas pesadas.

Decisao recomendada:

- manter para runtime local;
- em producao, considerar etapa separada de migracao no deploy.

## 5. Decisoes Tecnicas Recomendadas

### 5.1 Padrao de arquitetura

Adotar arquitetura em camadas:

```text
PostgreSQL
  -> Django Models
  -> Services / Business Rules
  -> Web Views atuais
  -> API REST
  -> Flutter iOS/Android
```

Regra: a logica de negocio nao deve ficar duplicada dentro das views web nem dentro dos serializers da API.

### 5.2 API REST

Criar app Django dedicado:

```text
power_church_django/apps/api/
```

Ou, se preferir separacao por dominio:

```text
power_church_django/apps/people/api.py
power_church_django/apps/contributions/api.py
power_church_django/apps/reports/api.py
```

Recomendacao inicial: app `api` central para organizar versao, autenticacao e roteamento; serializers podem ficar por dominio.

Namespace recomendado:

```text
/api/v1/
```

### 5.3 Dependencias para API

Adicionar, em etapa propria:

```text
djangorestframework
djangorestframework-simplejwt
django-cors-headers
```

Possiveis dependencias futuras:

```text
drf-spectacular
```

para documentacao OpenAPI/Swagger.

### 5.4 Primeiro conjunto de endpoints

Criar inicialmente endpoints somente de leitura para reduzir risco:

```text
GET /api/v1/me/
GET /api/v1/people/
GET /api/v1/people/{id}/
GET /api/v1/people/{id}/contributions/
GET /api/v1/people/{id}/receipts/
GET /api/v1/receipts/{id}/
```

Depois avançar para operacoes sensiveis:

```text
POST /api/v1/prayer-requests/
POST /api/v1/attendance/
POST /api/v1/contributions/
```

### 5.5 Flutter

O Flutter deve ser iniciado apenas depois de existir uma API minima testavel.

Primeiro MVP mobile sugerido:

```text
1. Tela de login
2. Minha conta / meus dados
3. Minha familia ou vinculados, se houver permissao
4. Meus recibos
5. Agenda / avisos, quando existir fonte confiavel
6. Pedidos de oracao, futuramente
```

### 5.6 Permissoes

Separar perfis desde o inicio:

```text
admin
secretaria
financeiro
pastoral
lider_celula
membro
```

Regra: o app de membro nao deve herdar permissao administrativa do painel web.

## 6. Roadmap Tecnico Recomendado

### Fase 1 - Consolidacao do runtime

- Validar `docker compose` com PostgreSQL.
- Confirmar persistencia dos volumes externos.
- Confirmar backup e restore do PostgreSQL.
- Criar checklist de subida/parada/backup.
- Documentar variaveis obrigatorias.

### Fase 2 - Preparacao da API

- Adicionar Django REST Framework.
- Criar namespace `/api/v1/`.
- Criar endpoint de health interno da API.
- Criar autenticacao JWT.
- Criar endpoint `/api/v1/me/`.
- Criar serializers somente com campos seguros.

### Fase 3 - API Pessoas

- Listagem paginada de pessoas.
- Busca por nome, e-mail, telefone, status.
- Detalhe seguro da pessoa.
- Relacionamentos permitidos por permissao.
- Logs de acesso se necessario.

### Fase 4 - API Recibos/Contribuicoes

- Listagem de recibos por pessoa.
- Download/visualizacao segura de recibos.
- Dados resumidos de contribuicoes.
- Regras fortes de permissao para financeiro.

### Fase 5 - Flutter MVP

- Criar app Flutter.
- Configurar ambientes dev/homolog/prod.
- Consumir `/api/v1/auth/` e `/api/v1/me/`.
- Criar primeira navegacao.
- Publicar teste interno iOS/Android.

## 7. Checklist Imediato Para o Codex

Tarefa recomendada para o Codex, em PR separado:

```text
Criar a base inicial da API REST do Power Church sem alterar o comportamento web atual.

Escopo:
1. adicionar djangorestframework, djangorestframework-simplejwt e django-cors-headers em deploy/requirements/django.txt;
2. configurar INSTALLED_APPS e middleware necessario;
3. criar app power_church_django/apps/api;
4. criar rota /api/v1/health/ retornando {"status":"ok"};
5. criar rota /api/v1/me/ retornando dados basicos do usuario autenticado;
6. proteger /api/v1/me/ por autenticacao;
7. manter /api/v1/health/ publico ou documentar se for privado;
8. criar testes basicos para health e me;
9. nao mexer nas rotas web existentes.
```

## 8. Proxima Decisao Necessaria

Antes de iniciar Flutter, decidir:

```text
Quem sera o primeiro usuario do app?
```

Opcoes:

1. membro comum;
2. lider de celula;
3. pastor;
4. equipe financeira;
5. secretaria/admin.

Recomendacao tecnica: comecar pelo membro comum, porque o escopo e mais seguro e ajuda a validar login, dados pessoais e recibos. Depois criar app/area de lideres e pastoral.

## 9. Resumo Executivo

O Power Church esta bem posicionado para evoluir: ja possui Django, PostgreSQL via runtime Docker, apps centrais e documentacao arquitetural. A proxima etapa correta nao e iniciar diretamente o app Flutter, mas criar uma API REST segura e minima.

A ordem recomendada e:

```text
1. estabilizar runtime Docker/PostgreSQL
2. criar API /api/v1/
3. proteger autenticacao e permissoes
4. expor endpoints minimos de leitura
5. iniciar Flutter consumindo a API
6. evoluir modulos moveis progressivamente
```

Esta ordem reduz retrabalho, protege dados sensiveis e permite que web, mobile e futuras integracoes usem a mesma base de negocio.

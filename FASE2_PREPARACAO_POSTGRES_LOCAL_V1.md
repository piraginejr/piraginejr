# Fase 2 Preparacao PostgreSQL Local V1

## 1. Objetivo

Preparar o projeto para rodar o `Django` sobre `PostgreSQL` local, preservando:

- o ambiente atual em `SQLite`;
- o banco legado principal;
- a possibilidade de comparacao lado a lado antes da Fase 3.

## 2. O Que Ficou Pronto No Projeto

### 2.1 Ambiente separado da Fase 2

Foi criado o arquivo:

- [\.env.power_church_django.postgres.local](/Users/piraginejr/Documents/New project/Teste/Power Church/.env.power_church_django.postgres.local)

Ele:

- mantem o legado em `SQLite`;
- aponta o `default` do Django para `PostgreSQL`;
- usa a porta `63621` para nao conflitar com o ambiente atual em `63620`.

### 2.2 Launcher separado

Foi criado o atalho:

- [Abrir Power Church Django PostgreSQL.command](/Users/piraginejr/Documents/New project/Teste/Power Church/Abrir Power Church Django PostgreSQL.command)

Ele reaproveita o launcher principal, mas injeta os overrides da Fase 2.

### 2.3 Verificacao tecnica

Foi criado o script:

- [verificar_fase2_postgres_local.sh](/Users/piraginejr/Documents/New project/Teste/Power Church/scripts/verificar_fase2_postgres_local.sh)

Ele verifica:

- presenca de `psql`;
- presenca de `postgres`;
- presenca de `docker`;
- resposta da porta `5432`;
- capacidade de conexao do `psycopg`.

### 2.4 Atalho Docker para o Postgres

Foi criado o script:

- [iniciar_postgres_fase2_docker.sh](/Users/piraginejr/Documents/New project/Teste/Power Church/scripts/iniciar_postgres_fase2_docker.sh)

Ele sobe apenas o servico `postgres` do [docker-compose.django.yml](/Users/piraginejr/Documents/New project/Teste/Power Church/docker-compose.django.yml).

### 2.5 Operacao nativa com Postgres.app

Foram criados os scripts:

- [iniciar_postgres_fase2_postgresapp.sh](/Users/piraginejr/Documents/New project/Teste/Power Church/scripts/iniciar_postgres_fase2_postgresapp.sh)
- [parar_postgres_fase2_postgresapp.sh](/Users/piraginejr/Documents/New project/Teste/Power Church/scripts/parar_postgres_fase2_postgresapp.sh)
- [status_postgres_fase2_postgresapp.sh](/Users/piraginejr/Documents/New project/Teste/Power Church/scripts/status_postgres_fase2_postgresapp.sh)

Eles permitem operar o PostgreSQL local da Fase 2 sem depender da interface grafica do app.

## 3. Estado Atual Encontrado Em 03/06/2026

No ambiente desta maquina, no inicio da Fase 2:

- `psql` nao esta no PATH;
- `postgres` nao esta no PATH;
- `docker` nao esta no PATH;
- `brew` nao esta no PATH;
- `psycopg` ja esta instalado no virtualenv do Django.

Leitura pratica:

- o projeto ja esta pronto para falar com `PostgreSQL`;
- o que falta e apenas um `runtime local de PostgreSQL`.

Atualizacao da fase:

- o `Postgres.app` foi instalado em `/Applications/Postgres.app`;
- a operacao do banco local passou a poder usar diretamente os binarios desse app.

## 4. Opcoes Operacionais Para Desbloquear A Fase 3

### Opcao A: Docker Desktop

Melhor quando:

- voce quiser uma experiencia mais proxima da nuvem;
- quiser isolar o banco local;
- quiser reaproveitar o `docker-compose.django.yml`.

Depois de instalar/abrir o Docker Desktop:

1. rodar [iniciar_postgres_fase2_docker.sh](/Users/piraginejr/Documents/New project/Teste/Power Church/scripts/iniciar_postgres_fase2_docker.sh)
2. rodar [verificar_fase2_postgres_local.sh](/Users/piraginejr/Documents/New project/Teste/Power Church/scripts/verificar_fase2_postgres_local.sh)
3. abrir [Abrir Power Church Django PostgreSQL.command](/Users/piraginejr/Documents/New project/Teste/Power Church/Abrir Power Church Django PostgreSQL.command)

### Opcao B: Postgres.app

Melhor quando:

- voce quiser um banco local nativo do Mac;
- preferir menos camadas para o dia a dia.

Fluxo operacional agora:

1. rodar [iniciar_postgres_fase2_postgresapp.sh](/Users/piraginejr/Documents/New project/Teste/Power Church/scripts/iniciar_postgres_fase2_postgresapp.sh)
2. rodar [verificar_fase2_postgres_local.sh](/Users/piraginejr/Documents/New project/Teste/Power Church/scripts/verificar_fase2_postgres_local.sh)
3. abrir [Abrir Power Church Django PostgreSQL.command](/Users/piraginejr/Documents/New project/Teste/Power Church/Abrir Power Church Django PostgreSQL.command)

## 5. Criterio De Saida Da Fase 2

Considerarei a Fase 2 operacionalmente concluida quando:

- houver um servidor PostgreSQL local escutando em `127.0.0.1:5432`;
- o script [verificar_fase2_postgres_local.sh](/Users/piraginejr/Documents/New project/Teste/Power Church/scripts/verificar_fase2_postgres_local.sh) fechar sem bloqueios;
- o launcher [Abrir Power Church Django PostgreSQL.command](/Users/piraginejr/Documents/New project/Teste/Power Church/Abrir Power Church Django PostgreSQL.command) conseguir aplicar `migrate`;
- o Django subir na porta `63621`.

## 6. O Que Ja Ficou Preparado Para A Fase 3

- variaveis de ambiente separadas;
- launcher separado;
- caminho reversivel para comparar `63620` vs `63621`;
- scripts de verificacao;
- caminho Docker pronto, caso essa seja a opcao escolhida.

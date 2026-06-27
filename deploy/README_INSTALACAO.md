# Pacote De Instalacao Power Church

## Decisao Atual

O desenvolvimento continua neste Mac ate a fase combinada de hospedagem externa.

Este diretorio existe para preparar a futura instalacao em servidores de clientes sem improviso. A ideia e manter um pacote padrao, testavel e repetivel para cada nova igreja/cliente.

## Modelo Alvo Inicial

- `single-tenant`: uma instancia por cliente;
- Linux em nuvem como alvo preferencial;
- banco separado por cliente;
- backup e restore por cliente;
- dependencias instaladas por script;
- validacao automatica antes de liberar uso real.

## Pacote Confirmado Pelo Provedor

O gestor do servidor confirmou que a entrega esperada para hospedagem deve vir em:

- `Dockerfile`
- `docker-compose.yml`

Complementos obrigatorios do nosso lado:

- variaveis de ambiente;
- dump do banco para migracao;
- roteiro de restore/subida no servidor.

Observacao:

- `composer.json` nao faz parte deste projeto, porque o Power Church roda em `Python/Django`, nao em `PHP/Composer`.

## Estado Atual Da Migracao

A migracao funcional local para `Django + PostgreSQL` foi fechada antes da hospedagem.

Evidencias principais:

- [FECHAMENTO_ETAPA4_IMPORTACOES_CONCILIACOES_V1.md](/Users/piraginejr/Documents/New project/Teste/Power Church/data/homologacao/FECHAMENTO_ETAPA4_IMPORTACOES_CONCILIACOES_V1.md)
- [verificar_fase4_postgres_20260606_121107.md](/Users/piraginejr/Documents/New project/Teste/Power Church/data/homologacao/verificar_fase4_postgres_20260606_121107.md)

Com isso, este pacote passa a cuidar principalmente de:

- containerizacao;
- volumes;
- variaveis de ambiente;
- dump e restore;
- subida da aplicacao hospedada.

## Arquivos

- `../Dockerfile`: imagem padrao para servidor/container.
- `../docker-compose.yml`: execucao single-tenant com volume `./data`.
- `../Dockerfile.django`: imagem inicial do Django em paralelo.
- `../docker-compose.django.yml`: staging Django com PostgreSQL.
- `requirements/base.txt`: dependencias Python minimas para servidor portavel.
- `requirements/django.txt`: dependencias do projeto Django paralelo.
- `requirements/ocr.txt`: dependencias Python futuras para OCR de envelopes.
- `system/ubuntu-24.04.txt`: pacotes de sistema esperados no Linux alvo.
- `install_local_mac.sh`: prepara opcionalmente o ambiente local de desenvolvimento.
- `install_ubuntu_server.sh`: roteiro executavel para preparar servidor Linux.
- `backup_sqlite.sh`: ponto de entrada compativel; no modo atual ele prioriza backup do runtime Docker/PostgreSQL e, se esse runtime nao existir, cai no backup SQLite legado.
- `restore_sqlite.sh`: restaura backup preservando uma copia pre-restore.
- `env.example`: variaveis de ambiente padrao.
- `../scripts/verificar_dependencias_servidor.py`: valida se o ambiente esta pronto.
- `../scripts/verificar_pacote_instalacao.py`: valida se o pacote de instalacao continua completo.

## Entrega Minima Para A Infraestrutura Hospedada

Quando chegarmos ao corte para o servidor, o pacote precisa incluir:

- `Dockerfile`
- `docker-compose.yml`
- `env.example`
- dump do banco alvo
- instrucao de restauracao do banco
- instrucao de `docker compose up -d`

## Execucao Local Parametrizada

O padrao continua abrindo no Mac sem configurar nada:

```bash
python3 power_church_demo.py
```

Quando quisermos simular servidor local:

```bash
POWER_CHURCH_HOST=127.0.0.1 POWER_CHURCH_PORT=8000 python3 power_church_demo.py --no-browser
```

## Execucao Em Container

O banco nao fica embutido na imagem. Ele fica no volume `./data`, para permitir backup, restore e separacao por cliente.

```bash
docker compose up --build
```

Para o Django em staging paralelo:

```bash
docker compose -f docker-compose.django.yml up --build
```

## Runtime Local No Mesmo Modelo Da Nuvem

Para operar localmente ja no desenho de volumes persistentes que vai para a hospedagem, o projeto agora usa:

- `/Users/piraginejr/power_church_postgres_runtime/`

Esse runtime separado guarda:

- `data/` inteiro como volume persistente unico do Django;
- Postgres do container;
- banco legado de referencia dentro de `data/`;
- envelopes, extratos, planilhas, fotos, branding e homologacao dentro de `data/`;
- relatorios;
- backups;
- logs.

Esse caminho fica fora do `Documents/iCloud`, justamente para evitar lentidao e lock durante copia de envelopes, fotos e uploads.

Observacao importante:

- as credenciais atuais do runtime local/homologacao sao temporarias de ambiente de teste;
- elas nao devem ser tratadas como segredos definitivos de producao;
- antes da subida em nuvem/producao, trocar usuario, senha e demais segredos no `runtime.env` real.

Preparar a estrutura e sincronizar os dados atuais:

```bash
./scripts/preparar_runtime_postgres_local.sh --sync-existing-data
```

Subir o stack Docker local do Django/Postgres:

```bash
./scripts/subir_runtime_postgres_local.sh
```

Gerar backup do que esta rodando no runtime Docker:

```bash
./scripts/powerbackup_runtime.sh
```

Se algum atalho externo ainda chamar `deploy/backup_sqlite.sh`, ele agora redireciona automaticamente para esse backup do runtime quando o ambiente Docker/Postgres estiver configurado.

Parar o stack:

```bash
./scripts/parar_runtime_postgres_local.sh
```

Arquivos principais desse fluxo:

- [docker-compose.runtime.yml](/Users/piraginejr/Documents/New project/Teste/Power Church/docker-compose.runtime.yml)
- [runtime.env.postgres.local.example](/Users/piraginejr/Documents/New project/Teste/Power Church/deploy/runtime.env.postgres.local.example)
- [docker-entrypoint-django-runtime.sh](/Users/piraginejr/Documents/New project/Teste/Power Church/deploy/docker-entrypoint-django-runtime.sh)

Entrada publica local do ambiente novo:

```text
http://127.0.0.1:8001/accounts/login/
```

Depois de subir:

```bash
python3 scripts/verificar_pacote_instalacao.py --report
python3 scripts/verificar_dependencias_servidor.py --profile server --report
python3 scripts/verificar_prontidao_django.py --report
```

## Regra De Ouro

Nada aqui troca o desenvolvimento local agora.

Antes de migrar cliente real, vamos rodar:

```bash
python3 scripts/verificar_pacote_instalacao.py --report
python3 scripts/verificar_funcionalidade_total.py --report
python3 scripts/verificar_dependencias_servidor.py --profile server --report
```

So depois disso faz sentido publicar em servidor externo.

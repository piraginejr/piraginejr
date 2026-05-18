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
- `backup_sqlite.sh`: gera backup consistente do banco SQLite.
- `restore_sqlite.sh`: restaura backup preservando uma copia pre-restore.
- `env.example`: variaveis de ambiente padrao.
- `../scripts/verificar_dependencias_servidor.py`: valida se o ambiente esta pronto.
- `../scripts/verificar_pacote_instalacao.py`: valida se o pacote de instalacao continua completo.

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

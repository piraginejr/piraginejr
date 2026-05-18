# Power Church Django

Projeto Django paralelo ao prototipo atual.

Esta pasta nasce para migracao gradual. Ela nao substitui `power_church_demo.py` nesta fase.

## Regra

- O prototipo continua sendo a versao operacional local.
- O Django nasce consumindo `power_church_core`.
- As primeiras telas devem ser somente leitura.
- Escrita em banco real so depois de comparacao de totais e homologacao.

## Apps Iniciais

- `accounts`: usuarios, perfis e permissoes.
- `people`: pessoas, familias e vinculos.
- `contributions`: contribuicoes, tipos, campanhas e centavos.
- `imports`: lotes bancarios, parsers e saneamento.
- `audit`: trilha de auditoria.
- `reports`: relatorios e PDFs.

## Como Rodar Futuramente

O alvo recomendado e Python 3.11+ com Django 5.2 LTS.

```bash
cd power_church_django
python -m venv .venv
source .venv/bin/activate
pip install -r ../deploy/requirements/django.txt
python manage.py check
python manage.py runserver
```


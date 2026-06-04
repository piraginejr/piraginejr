# Fase 3 Migracao Django Para PostgreSQL V1

## 1. Objetivo

Concluir a mudanca do `banco default` do Django para `PostgreSQL`, nao apenas no schema, mas tambem nos dados modernos que o projeto ja acumulou no SQLite do Django.

## 2. O Que Deve Migrar

Nesta fase, o foco e migrar os dados `modernos do Django`, nao o banco legado de negocio.

Escopo atual:

- usuarios Django
- template padrao de e-mail de recibos
- fila de envio de recibos
- auditoria Django
- auditlog
- perfis domiciliares do app Django
- chaves de feature relevantes do Waffle

## 3. O Que Nao Muda Nesta Fase

- o banco legado principal continua em `SQLite`
- contribuicoes, pessoas, envelopes e importacoes continuam usando a ponte legada
- a mudanca aqui e do `default database` do Django e dos dados modernos associados a ele

## 4. Rotina Operacional Criada

Script:

- [migrar_django_sqlite_para_postgres_fase3.sh](/Users/piraginejr/Documents/New project/Teste/Power Church/scripts/migrar_django_sqlite_para_postgres_fase3.sh)

Ele faz:

1. exporta os dados modernos do SQLite do Django
2. cria backup do PostgreSQL atual
3. limpa o banco Django em PostgreSQL
4. recria os perfis de acesso padrao
5. carrega a fixture no PostgreSQL
6. roda `setup_access_profiles` novamente
7. executa `manage.py check`
8. grava relatorio comparativo

## 5. Criterio De Saida Da Fase 3

Consideraremos a Fase 3 concluida quando:

- o PostgreSQL estiver com contagens coerentes com o SQLite Django de origem
- o launcher em [Abrir Power Church Django PostgreSQL.command](/Users/piraginejr/Documents/New project/Teste/Power Church/Abrir Power Church Django PostgreSQL.command) continuar subindo o sistema
- `manage.py check` fechar sem erros
- as checagens funcionais principais continuarem aprovadas

# Plano Homologacao Operador Migracao V1

## 1. Objetivo

Padronizar a homologacao curta da migracao acelerada do Power Church para `Django + PostgreSQL`, combinando:

- relatorio tecnico automatico por etapa;
- roteiro curto do operador por etapa;
- criterio de liberacao claro antes de seguir para a etapa seguinte.

Este plano complementa:

- [PLANO_MIGRACAO_LOCAL_ARQUITETURA_ALVO_V1.md](/Users/piraginejr/Documents/New project/Teste/Power Church/PLANO_MIGRACAO_LOCAL_ARQUITETURA_ALVO_V1.md)
- [MATRIZ_HOMOLOGACAO_V1.md](/Users/piraginejr/Documents/New project/Teste/Power Church/MATRIZ_HOMOLOGACAO_V1.md)

## 2. Regra De Liberacao

Nenhuma etapa sera considerada concluida apenas por scripts.

Cada etapa exige:

1. scripts obrigatorios verdes;
2. relatorio tecnico automatico gerado;
3. roteiro do operador preenchido;
4. cenarios criticos marcados como `OK`.

Se algum cenario critico falhar:

- a etapa nao e liberada;
- o ajuste deve ser feito antes de seguir;
- o roteiro e o relatorio tecnico ficam como trilha de auditoria.

## 3. Relatorio Tecnico Automatico

Script oficial:

- [scripts/executar_homologacao_migracao.py](/Users/piraginejr/Documents/New project/Teste/Power Church/scripts/executar_homologacao_migracao.py)

Uso recomendado:

```bash
cd "/Users/piraginejr/Documents/New project/Teste/Power Church"
python3 scripts/executar_homologacao_migracao.py --stage 1
python3 scripts/executar_homologacao_migracao.py --stage 2
python3 scripts/executar_homologacao_migracao.py --stage 3
python3 scripts/executar_homologacao_migracao.py --stage 4
```

Padrao operacional:

- usa o banco legado em `data/power_church_membros_importado.db`;
- usa o ambiente Postgres de `/.env.power_church_django.postgres.local`;
- grava relatorio consolidado em `data/homologacao`.

## 4. Roteiros Do Operador

### Etapa 1

- roteiro: [ROTEIRO_OPERADOR_ETAPA1_FUNDACAO_V1.md](/Users/piraginejr/Documents/New project/Teste/Power Church/data/homologacao/ROTEIRO_OPERADOR_ETAPA1_FUNDACAO_V1.md)
- foco:
  - abrir sistema em Postgres
  - login
  - auditoria
  - monitor da fila
  - central de recibos
  - navegacao principal

### Etapa 2

- roteiro: [ROTEIRO_OPERADOR_ETAPA2_CADASTRO_FAMILIAS_V1.md](/Users/piraginejr/Documents/New project/Teste/Power Church/data/homologacao/ROTEIRO_OPERADOR_ETAPA2_CADASTRO_FAMILIAS_V1.md)
- fechamento tecnico: [FECHAMENTO_ETAPA2_CADASTRO_FAMILIAS_V1.md](/Users/piraginejr/Documents/New project/Teste/Power Church/data/homologacao/FECHAMENTO_ETAPA2_CADASTRO_FAMILIAS_V1.md)
- foco:
  - buscas
  - ficha da pessoa
  - edicao
  - contatos/endereco
  - familias domiciliares
  - auditoria familiar
  - merge

Observacao operacional:

- o fechamento tecnico da etapa ja separa o que ficou para o dominio financeiro da `Etapa 3`;
- se o `merge` for adiado por cautela com a base, isso nao impede o corte tecnico para o financeiro, mas deixa uma pendencia humana de homologacao a ser encerrada depois.

### Etapa 3

- roteiro: [ROTEIRO_OPERADOR_ETAPA3_FINANCEIRO_RECIBOS_V1.md](/Users/piraginejr/Documents/New project/Teste/Power Church/data/homologacao/ROTEIRO_OPERADOR_ETAPA3_FINANCEIRO_RECIBOS_V1.md)
- piloto financeiro real: [PLANO_ETAPA3_PILOTO_FINANCEIRO_MAIO2026_V1.md](/Users/piraginejr/Documents/New project/Teste/Power Church/PLANO_ETAPA3_PILOTO_FINANCEIRO_MAIO2026_V1.md)
- foco:
  - contribuicoes
  - envelopes
  - rateios
  - recibos
  - extratos
  - fila de envio
  - auditoria de e-mails

Observacao operacional:

- a Etapa 3 passa a usar os extratos reais de `maio/2026` como massa de prova principal;
- antes de qualquer corte financeiro definitivo, o piloto deve classificar os bancos entre `apto`, `apto com auditoria` e `bloqueado por portabilidade`.
- nesta versao, o caminho operacional oficial fica sendo o `extrato bancario completo`;
- o `PIX isolado` fica fora da entrada corrente para evitar lacunas entre meios de recebimento.

### Etapa 4

- roteiro: [ROTEIRO_OPERADOR_ETAPA4_IMPORTACOES_CONCILIACOES_V1.md](/Users/piraginejr/Documents/New project/Teste/Power Church/data/homologacao/ROTEIRO_OPERADOR_ETAPA4_IMPORTACOES_CONCILIACOES_V1.md)
- fechamento tecnico: [FECHAMENTO_ETAPA4_IMPORTACOES_CONCILIACOES_V1.md](/Users/piraginejr/Documents/New project/Teste/Power Church/data/homologacao/FECHAMENTO_ETAPA4_IMPORTACOES_CONCILIACOES_V1.md)
- foco:
  - importacoes
  - lotes de extrato
  - lotes PIX
  - pendencias
  - conciliacoes
  - reflexo financeiro
  - prontidao final para nuvem

## 5. Scripts Obrigatorios Por Etapa

### Etapa 1 - Fundacao

- `manage.py check`
- `scripts/verificar_django_funcional.py`
- `scripts/verificar_contrato_visual_django.py`
- `scripts/verificar_funcionalidade_total.py`

### Etapa 2 - Cadastro e familias

- `scripts/sincronizar_espelho_cadastro_postgres.py`
- `scripts/verificar_espelho_cadastro_postgres.py`
- `manage.py check`
- `scripts/verificar_django_funcional.py`
- `scripts/verificar_django_escrita_pessoas.py`
- `scripts/verificar_paridade_django.py`
- `scripts/verificar_funcionalidade_total.py`

### Etapa 3 - Financeiro, recibos e extratos

- `scripts/sincronizar_espelho_cadastro_postgres.py`
- `scripts/sincronizar_snapshots_financeiros_postgres.py`
- `manage.py check`
- `scripts/verificar_dados_operacionais.py`
- `scripts/verificar_snapshots_financeiros_postgres.py`
- `scripts/verificar_django_funcional.py`
- `scripts/verificar_paridade_django.py`
- `scripts/verificar_funcionalidade_total.py`
- fechamento tecnico: [FECHAMENTO_ETAPA3_FINANCEIRO_RECIBOS_V1.md](/Users/piraginejr/Documents/New project/Teste/Power Church/data/homologacao/FECHAMENTO_ETAPA3_FINANCEIRO_RECIBOS_V1.md)

### Etapa 4 - Importacoes e conciliacoes

- `scripts/verificar_fase4_postgres.py`
- `scripts/verificar_dados_operacionais.py`
- `scripts/verificar_prontidao_transicao.py`
- `scripts/verificar_paridade_django.py`
- `scripts/verificar_funcionalidade_total.py`
- fechamento tecnico: [FECHAMENTO_ETAPA4_IMPORTACOES_CONCILIACOES_V1.md](/Users/piraginejr/Documents/New project/Teste/Power Church/data/homologacao/FECHAMENTO_ETAPA4_IMPORTACOES_CONCILIACOES_V1.md)

## 6. Cenarios Criticos Que Nao Podem Ser Pulados

Mesmo na migracao acelerada, os testes abaixo sao obrigatorios quando a etapa tocar o dominio:

- cadastro: busca, ficha, edicao, endereco, contato
- familias: lista, filtros, fila de auditoria, merge
- contribuicoes: busca, lancamento, rateio
- envelopes: lote, lancamento, conciliacao
- recibos: competencia, consolidado, PDF, reenvio
- extratos: tela, PDF, envio
- importacoes: lote, revisao, conciliacao, saida da pendencia
- fila: monitor, falhas, reprocessamento

## 7. Massa De Prova Recomendada

Usar preferencialmente os mesmos casos sentinela ja aceitos na matriz:

- Paschoal Piragine Junior
- DOXA
- Bravim
- Ronaldo Santos Mendo
- Primeira Igreja Batis / mesma titularidade
- Kelly Mendonca do Car / centavos especiais
- lote Bradesco auditado
- lote PIX Sicoob auditado

## 8. Resultado Esperado

Ao final da Etapa 4, o projeto deve ter:

- relatorios tecnicos por etapa;
- roteiros do operador preenchiveis e reutilizaveis;
- homologacao curta, objetiva e auditavel;
- base local pronta para a preparacao da hospedagem containerizada na nuvem.

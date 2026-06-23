# Pendencias Pos Zero Hibrido V1

## Objetivo

Registrar descobertas importantes que `nao devem se perder`, mas que tambem `nao devem desviar` a execucao da meta principal atual:

- eliminar o runtime hibrido;
- operar em `base unica Django + PostgreSQL`;
- so depois tratar refinamentos e automacoes adjacentes.

## Regra De Priorizacao

Estas pendencias:

- `nao bloqueiam` a conclusao do zero hibrido;
- `nao autorizam` reabrir frentes paralelas agora;
- devem ser retomadas `depois` que o runtime principal estiver 100% fora do legado.

## Pendencias Registradas

### 1. Merge Em Lote Controlado Ainda Nao Confiavel

Status:

- `pendente para depois do zero hibrido`

O que foi observado:

- o `merge em lote/clone` ficou instavel no ambiente local durante o ensaio;
- houve travamentos no bootstrap do Django/venv;
- em tentativas anteriores, execucoes paralelas tambem geraram `database is locked` no legado SQLite;
- o problema observado foi do `runner de ensaio`, nao da regra funcional principal do merge em si.

Arquivos relacionados:

- [executar_merge_controlado.py](/Users/piraginejr/Documents/New project/Teste/Power Church/scripts/executar_merge_controlado.py)
- [executar_merge_controlado_lote.py](/Users/piraginejr/Documents/New project/Teste/Power Church/scripts/executar_merge_controlado_lote.py)

Decisao atual:

- `nao` gastar mais tempo nisso antes de concluir o zero hibrido;
- manter o aprendizado registrado;
- retomar depois, ja com a base unica estabilizada.

Hipotese tecnica principal:

- o ensaio em lote sofre com a combinacao de:
  - bootstrap pesado do Django/venv na pasta sincronizada;
  - dependencia residual do merge legado em SQLite;
  - custo de clone por caso;
  - e comportamento de lock do SQLite quando o ensaio e mal serializado.

O que fazer depois:

1. rerodar o ensaio com runtime mais leve e fora do iCloud, se necessario;
2. testar o `merge` em lote ja sobre a implementacao nativa em Postgres;
3. so entao decidir se o `merge em lote` vira ferramenta operacional oficial.

### 2. Casos Reais De Merge Que Ja Viraram Sentinela

Casos que `parecem bons candidatos` para validacao posterior:

- `Caio` `#159 + #160`
- `Francisca` `#463 + #462`
- `Gilza` `#497 + #498`
- `Luca` `#793 + #792`
- `Maria` `#950 + #916`

Casos que `devem continuar bloqueados por seguranca`, salvo regra humana especial futura:

- `Davi` `#271 + #270`
  - datas de nascimento validas e diferentes
- `Luiza` `#823 + #822`
  - datas de nascimento validas e diferentes
- `Marta`
  - retirada da bateria por `CPFs diferentes`

### 3. Regra Nova Ja Confirmada Para O Merge

Implementado em:

- [legacy_write.py](/Users/piraginejr/Documents/New project/Teste/Power Church/power_church_django/services/legacy_write.py)

Regra:

- se uma `data de nascimento` for invalida/impossivel e a outra for valida, preferir a valida;
- se as duas forem validas e diferentes, continuar bloqueando;
- isso fica mantido mesmo antes da versao nativa final do merge.

## Criterio De Reabertura

Estas pendencias so devem voltar para a frente principal quando:

1. o `zero hibrido` estiver fechado;
2. o merge principal ja estiver em escrita nativa no Postgres;
3. houver tempo para tratar `lote`, `runner` e `casos reais` sem travar a entrega principal.

### 4. Parser Historico Reaproveitado Na Importacao De Pessoas

Status:

- `nao bloqueia o runtime novo`

O que ficou decidido:

- o upload da planilha ja roda na trilha nova do Postgres;
- o que ficou reaproveitado foi o `parser` e parte das regras de normalizacao historicas;
- o resultado continua materializado e auditado no Postgres por:
  - dashboard do lote;
  - pendencias;
  - linhas importadas;
  - snapshots das pessoas afetadas.

O que fica para depois:

1. avaliar se vale extrair esse parser para um modulo neutro dedicado;
2. ou mantê-lo reaproveitado apenas como biblioteca interna de leitura/normalizacao.

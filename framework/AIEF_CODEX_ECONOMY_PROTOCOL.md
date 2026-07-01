# AIEF - Protocolo de Economia de Codex

Status: aprovado
Escopo: todos os projetos AIEF

## 1. Principio

O tempo de execucao do Codex deve ser tratado como recurso financeiro e operacional do projeto.

Por isso, toda tarefa enviada ao Codex deve ser a menor tarefa util capaz de produzir progresso verificavel.

## 2. Regra de delta

Antes de responder ou preparar uma tarefa, perguntar:

```text
O que mudou em relacao a arquitetura ou decisao ja aprovada?
```

Se nada mudou, nao reconstruir a arquitetura e nao repetir regras ja aprovadas.

Resposta padrao:

```text
Nada muda na arquitetura aprovada. O impacto se limita a: [delta].
```

## 3. Regras para economizar Codex

1. Nao pedir nova analise quando a decisao ja esta aprovada.
2. Dividir tarefas grandes em etapas pequenas.
3. Evitar diagnostico amplo quando um diagnostico minimo basta.
4. Evitar refatoracao fora do escopo.
5. Pedir sempre criterios de aceite claros.
6. Pedir commits pequenos e separados.
7. Informar claramente o que nao deve ser alterado.
8. Usar contexto minimo suficiente, sem historico desnecessario.

## 4. Formato padrao de pedido ao Codex

```text
Objetivo:
[uma frase]

Contexto minimo:
[o essencial]

Escopo:
- [item 1]
- [item 2]

Nao fazer:
- [limites]

Validacao:
- [teste ou comando]

Commit sugerido:
[mensagem]

Ao final retornar:
- arquivos alterados
- testes executados
- resultado
- commit SHA
```

## 5. Frases padrao

Quando a decisao ja estiver aprovada:

```text
A decisao ja esta aprovada. Nao reanalise a arquitetura. Implemente apenas o delta abaixo.
```

Quando o escopo precisar ser pequeno:

```text
Faca somente esta etapa. Nao avance para a proxima sem nova autorizacao.
```

Quando nao deve haver refatoracao:

```text
Nao faca refatoracao ampla. Corrija apenas o ponto necessario.
```

Quando o diagnostico deve ser limitado:

```text
Faca diagnostico minimo suficiente para confirmar a causa e aplicar a correcao.
```

## 6. Regra para incidentes

Em incidentes, preferir:

1. confirmar a camada provavel do problema;
2. aplicar uma correcao pequena;
3. validar na proxima janela;
4. aprofundar somente se a correcao falhar.

## 7. Frase-guia

> Decida uma vez. Registre. Depois avance por delta, economizando tempo, credito e energia.

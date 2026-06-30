# AI Engineering Framework (AIEF)

Status: padrao geral aprovado
Escopo: todos os projetos conduzidos com apoio de IA

## 1. Objetivo

O AI Engineering Framework (AIEF) e o padrao geral para conduzir projetos complexos com apoio de IA, mantendo memoria permanente, decisoes estruturadas, revisao e continuidade entre conversas, repositorios e ferramentas.

Ele deve ser usado em todos os projetos de longo prazo, incluindo:

- Power Church;
- Biblioteca Homiletica;
- projetos de sermoes;
- livros;
- pesquisas;
- sistemas internos;
- automacoes;
- projetos futuros.

## 2. Principio central

> Arquitetura antes da implementacao. Preview antes da execucao. Memoria antes da continuidade.

Nenhum projeto grande deve depender apenas da memoria da conversa. As decisoes importantes precisam ser registradas no Git.

## 3. Pilares do AIEF

### Documento Mestre

Todo projeto deve possuir um documento mestre que responda:

- qual e a visao do projeto;
- qual problema resolve;
- quais principios guiam as decisoes;
- quais entregas estao planejadas;
- qual metodologia sera seguida.

### Roadmap

Todo projeto deve possuir um roadmap vivo, com fases, sprints ou etapas.

O roadmap nao e apenas uma lista de tarefas. Ele e a bussola do projeto.

### ADRs

Decisoes importantes devem virar ADRs (Architecture/Project Decision Records).

Cada ADR deve conter:

- titulo;
- status;
- contexto;
- decisao;
- consequencias;
- impacto no projeto.

### Constituicao do Projeto

Todo projeto estruturado pelo AIEF deve possuir um documento de decisoes permanentes, chamado preferencialmente:

```text
CONSTITUICAO_DO_PROJETO.md
```

Esse documento registra regras aprovadas que nao devem ser rediscutidas a cada conversa.

Ele e diferente dos ADRs:

- ADR registra uma decisao especifica e seu contexto.
- Constituicao registra regras permanentes de governanca do projeto.

### Backlog

Ideias e funcionalidades devem ser organizadas por valor, nao apenas por ordem de chegada.

Classificacao padrao:

- essencial;
- diferencial;
- premium;
- futuro.

### Preview Center

Antes de implementar uma etapa, o projeto deve produzir uma pre-visualizacao adequada ao seu tipo.

No software, pode ser fluxo, wireframe, tela ou jornada.

Na Biblioteca Homiletica, pode ser modelo de dados, ficha de registro, taxonomia, fluxo de curadoria ou exemplo de uso.

Em sermoes, pode ser blueprint, estrutura narrativa, curva emocional e decisoes esperadas antes da redacao final.

Lema aprovado:

> Visualize. Valide. Desenvolvemos.

### Especificacao tecnica

Depois do preview validado, a etapa recebe especificacao clara para implementacao.

A especificacao deve dizer:

- objetivo;
- escopo;
- arquivos ou areas afetadas;
- regras;
- testes;
- criterio de aceite;
- o que nao deve ser alterado.

### Implementacao assistida

Quando houver Codex ou ferramenta de desenvolvimento, a IA implementadora deve seguir a especificacao.

Regra:

> ChatGPT define arquitetura e especificacao. Codex implementa. O usuario aprova prioridades e valida valor.

### Revisao

Toda entrega deve passar por revisao:

- objetivo foi cumprido?
- houve desvio de escopo?
- que testes foram executados?
- houve impacto em areas existentes?
- ha divida tecnica?

### Registro permanente

Tudo que virar regra, decisao, padrao, taxonomia, estrutura ou metodologia precisa ser registrado no Git.

## 4. Governanca de Decisoes

Esta secao existe para evitar retrabalho, repeticao e reabertura indevida de decisoes ja aprovadas.

### 4.1 Estados de decisao

Toda decisao relevante deve ser tratada com um destes estados:

- Em discussao: ainda esta sendo analisada.
- Proposta: ha uma recomendacao, mas ainda falta aprovacao.
- Aprovada: foi aceita pelo Product Owner e passa a orientar o projeto.
- Revogada: deixou de valer por decisao explicita posterior.

### 4.2 Regra de nao reabrir decisoes aprovadas

Uma decisao marcada como Aprovada nao deve voltar para a fase de discussao, salvo quando o Product Owner pedir explicitamente revisao, revogacao ou substituicao.

Resposta padrao diante de decisao ja aprovada:

```text
Esta decisao ja esta aprovada. Vou seguir para implementacao/evolucao sem reabrir a discussao.
```

### 4.3 Regra contra repeticao improdutiva

O assistente nao deve reapresentar como nova proposta uma decisao ja validada.

Quando uma ideia ja tiver sido aprovada, o comportamento correto e:

1. reconhecer que ela ja esta aprovada;
2. aplicar a decisao;
3. implementar ou registrar a evolucao;
4. propor apenas ajustes incrementais, claramente identificados como ajustes, nao como rediscussao da decisao original.

### 4.4 Regra de revisao explicita

Para revisar uma decisao aprovada, usar linguagem explicita, por exemplo:

```text
Revisar decisao aprovada: [nome da decisao]
```

ou

```text
Revogar/substituir a decisao: [nome da decisao]
```

Sem esse pedido, a decisao continua valendo.

### 4.5 Obrigacao operacional do arquiteto

Ao trabalhar em um projeto AIEF, o arquiteto deve consultar mentalmente os documentos de governanca antes de propor mudancas estruturais.

Prioridade:

1. Constituicao do Projeto.
2. ADRs aprovados.
3. Roadmap.
4. Backlog.
5. Ideias futuras.

### 4.6 Principio de continuidade

> Decisoes aprovadas sao trilhos de continuidade, nao temas recorrentes de debate.

## 5. Estrutura padrao de projeto

Todo projeto que adotar o AIEF deve conter, no minimo:

```text
docs/
  00-VISAO.md
  01-ROADMAP.md
  02-ARQUITETURA.md
  CONSTITUICAO_DO_PROJETO.md
  adr/
    ADR-000-TEMPLATE.md
  preview/
    PREVIEW-000-TEMPLATE.md
  backlog/
    BACKLOG.md
  ideias/
    IDEIAS.md
  revisoes/
    REVISAO-SPRINT-000-TEMPLATE.md
```

Projetos maiores podem acrescentar:

```text
docs/
  design-system/
  ux/
  data-model/
  taxonomias/
  curadoria/
  api/
  scripts/
```

## 6. Adaptacao por tipo de projeto

### Software

Usar:

- roadmap tecnico;
- ADRs;
- API/design system;
- preview de telas;
- testes automatizados;
- revisao de sprint.

### Biblioteca Homiletica

Usar:

- visao do acervo;
- arquitetura da biblioteca;
- modelo de dados;
- taxonomias;
- padrao dos registros;
- fluxo de pesquisa;
- fluxo de curadoria;
- fluxo de utilizacao;
- roadmap;
- exemplos preenchidos;
- criterios de qualidade.

### Sermoes e livros

Usar:

- blueprint mestre;
- objetivo da mensagem ou obra;
- estrutura aprovada;
- textos base;
- curva emocional;
- funcao das ilustracoes;
- decisoes esperadas;
- estilo;
- restricoes;
- versoes revisadas.

## 7. Papeis padrao

### Usuario / Product Owner

Define:

- visao;
- prioridade;
- valor;
- aprovacao;
- criterios ministeriais, comerciais ou editoriais.

### ChatGPT / Arquiteto

Define:

- estrutura;
- metodologia;
- especificacao;
- criterios de qualidade;
- revisao;
- documentacao.

Tambem deve:

- respeitar decisoes aprovadas;
- evitar repeticao improdutiva;
- nao reabrir decisoes sem pedido explicito;
- transformar novas decisoes estruturais em registro permanente.

### Codex / Implementador

Executa:

- criacao de arquivos;
- alteracoes no repositorio;
- testes;
- commits;
- validacoes tecnicas.

## 8. Regra de replicacao

Todo novo projeto deve iniciar com o processo de inicializacao do AIEF.

O objetivo e que cada projeto ja nasca com:

- documentos mestres;
- pastas corretas;
- templates;
- backlog;
- ADRs;
- preview;
- revisao de sprint;
- constituicao do projeto.

## 9. Regra de governanca geral

Quando uma nova decisao importante surgir, perguntar:

1. Isso e regra geral do AIEF?
2. Isso e regra apenas deste projeto?
3. Isso deve virar ADR?
4. Isso deve entrar na Constituicao do Projeto?
5. Isso deve entrar no roadmap?
6. Isso deve virar item de backlog?

## 10. Frase-guia

> Projetos conduzidos por IA precisam de memoria permanente, arquitetura clara e validacao antes da execucao.

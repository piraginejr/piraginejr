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

## 4. Estrutura padrao de projeto

Todo projeto que adotar o AIEF deve conter, no minimo:

```text
docs/
  00-VISAO.md
  01-ROADMAP.md
  02-ARQUITETURA.md
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

## 5. Adaptacao por tipo de projeto

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

## 6. Papeis padrao

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

### Codex / Implementador

Executa:

- criacao de arquivos;
- alteracoes no repositorio;
- testes;
- commits;
- validacoes tecnicas.

## 7. Regra de replicacao

Todo novo projeto deve iniciar com o processo de inicializacao do AIEF.

O objetivo e que cada projeto ja nasca com:

- documentos mestres;
- pastas corretas;
- templates;
- backlog;
- ADRs;
- preview;
- revisao de sprint.

## 8. Regra de governanca

Quando uma nova decisao importante surgir, perguntar:

1. Isso e regra geral do AIEF?
2. Isso e regra apenas deste projeto?
3. Isso deve virar ADR?
4. Isso deve entrar no roadmap?
5. Isso deve virar item de backlog?

## 9. Frase-guia

> Projetos conduzidos por IA precisam de memoria permanente, arquitetura clara e validacao antes da execucao.

# Roadmap Power Church

Status: documento mestre aprovado
Data inicial: 2026-06-27

## 1. Visao do Produto

O Power Church e uma plataforma modular de gestao ministerial para igrejas e organizacoes religiosas.

Seu objetivo nao e apenas armazenar dados, mas automatizar processos administrativos, apoiar decisoes ministeriais e liberar pessoas para cuidar melhor de pessoas.

Frase-guia:

> Automatizamos a administracao para que a igreja tenha mais tempo para cuidar de pessoas.

## 2. Pilares da Plataforma

A plataforma sera organizada em quatro pilares principais.

### 2.1 Power Church Core

O Core e o nucleo da plataforma.

Inclui:

- Django;
- PostgreSQL;
- Docker/runtime;
- API REST versionada;
- regras de negocio;
- autenticacao;
- seguranca;
- auditoria;
- integracoes base.

Regra: nenhum aplicativo, painel, integracao, modulo de BI ou modulo futuro de IA acessara diretamente o banco de dados. Tudo deve passar pela API ou por servicos internos controlados.

### 2.2 Operator Experience (OX)

A OX e a experiencia web administrativa.

Publico principal:

- secretaria;
- tesouraria;
- equipe financeira;
- pastores;
- administradores;
- operadores internos.

Objetivo:

> Maxima produtividade operacional.

Principios:

- menos cliques;
- filtros rapidos;
- tabelas densas quando fizer sentido;
- importacao e exportacao;
- edicao em massa;
- visao de muitos dados na mesma tela;
- atalhos e produtividade;
- nao simplificar a interface em prejuizo do operador.

### 2.3 Member Experience (MX)

A MX e a experiencia voltada ao membro, visitante, lider e voluntario.

Publico principal:

- membros;
- visitantes;
- lideres;
- professores;
- voluntarios;
- usuarios que acessam em celular.

Objetivo:

> Maxima simplicidade, relacionamento e automacao de servicos.

Principios:

- poucos toques;
- linguagem simples;
- cards e acoes claras;
- atualizacao automatica de dados;
- notificacoes autorizadas pelo usuario;
- identidade digital do membro;
- servicos ao membro;
- acesso rapido a agenda, recibos, votacoes, solicitacoes e dados pessoais.

### 2.4 Power Church BI

O Power Church BI e o pilar analitico da plataforma.

Objetivo:

> Transformar dados em decisoes.

Modalidades:

#### BI Standard

Incluido na plataforma.

- dashboards nativos;
- indicadores ministeriais;
- paineis executivos;
- relatorios operacionais;
- graficos e KPIs;
- versao desktop e mobile.

#### BI Premium

Opcional.

- integracao com Microsoft Power BI;
- uso de licencas Microsoft 365 ja existentes no cliente quando aplicavel;
- dashboards personalizados;
- exportacoes e conectores de dados;
- compartilhamento com liderancas.

Principio de UX:

> Desktop: BI analitico completo. Mobile: BI executivo, visual e acionavel.

### 2.5 Power Church AI

A IA sera modulo opcional e futuro.

Regra:

- o Core funciona integralmente sem IA;
- a IA nao sera dependencia de regra de negocio essencial;
- a IA sera consumidora da API;
- cada cliente podera contratar ou nao esse modulo.

## 3. Power Church Development Framework (PCDF)

O PCDF e a metodologia oficial de desenvolvimento do Power Church.

Pilares:

1. Arquitetura primeiro.
2. Preview antes da implementacao.
3. Automacao por padrao.
4. API First.
5. Experiencia centrada na igreja.
6. BI como pilar oficial da plataforma.
7. IA opcional, nunca obrigatoria.

## 4. Power Church Preview Center

O Power Church Preview Center e a etapa oficial de pre-visualizacao das funcionalidades antes do desenvolvimento.

Lema aprovado:

> Visualize. Valide. Desenvolvemos.

Objetivo:

> Permitir que o cliente visualize, compreenda e valide cada etapa antes do inicio do desenvolvimento.

Importante:

- Preview nao e sistema funcionando;
- Preview nao e homologacao;
- Preview nao deve exigir desenvolvimento inutil;
- Preview valida conceito, fluxo, campos, telas e experiencia;
- testes reais acontecem apenas apos implementacao.

Fluxo oficial:

```text
Power Church Preview Center
        -> Pre-visualizacao da etapa
        -> Validacao pelo cliente
        -> Aprovacao
        -> Especificacao tecnica
        -> Implementacao pelo Codex
        -> Revisao arquitetural
        -> Testes
        -> Publicacao
```

### 4.1 Operator Preview

Voltado para a OX.

Objetivo:

- produtividade;
- processos administrativos;
- telas web;
- operadores internos.

### 4.2 Member Preview

Voltado para a MX.

Objetivo:

- experiencia do membro;
- aplicativo;
- relacionamento;
- automacoes;
- servicos ao membro.

### 4.3 BI Preview

Voltado para paineis e indicadores.

Objetivo:

- dashboards;
- KPIs;
- visualizacao desktop;
- visualizacao mobile;
- integracao futura com Power BI.

## 5. Experiencias Separadas, Core Unico

Principio aprovado:

> Uma unica plataforma. Experiencias diferentes para necessidades diferentes.

Arquitetura conceitual:

```text
                    POWER CHURCH CORE
        (Django + PostgreSQL + API + Regras de Negocio)
                              |
          +-------------------+-------------------+
          |                   |                   |
   Operator Experience   Member Experience   Power Church BI
         (OX)                (MX)            (Web + Mobile)
```

O que permanece unificado:

- identidade visual;
- linguagem da marca;
- regras de negocio;
- API;
- seguranca;
- auditoria;
- permissoes;
- banco de dados;
- governanca tecnica.

O que pode ser diferente:

- densidade da tela;
- navegacao;
- fluxo de uso;
- componentes;
- produtividade versus simplicidade;
- comportamento por perfil;
- versao desktop versus mobile.

Regra:

> O Web Administrativo nunca sera simplificado em prejuizo da produtividade do operador apenas para se parecer com o aplicativo.

## 6. Automacao como Padrao

Principio aprovado:

> Toda tarefa repetitiva sera automatizada sempre que isso puder ser feito com seguranca, auditoria e possibilidade de configuracao pela igreja.

Exemplos:

- atualizacao de telefone pelo membro;
- atualizacao de e-mail;
- atualizacao de endereco;
- notificacoes autorizadas;
- recibos disponiveis;
- votacoes abertas;
- pendencias cadastrais;
- lembretes de eventos;
- avisos de celula.

Intervencao humana deve ser configuravel, nao obrigatoria por padrao, quando a automacao for segura.

## 7. Identidade Digital do Membro

No primeiro acesso da MX, o membro devera informar dados de identificacao.

Fluxo aprovado:

1. Usuario informa CPF.
2. Sistema valida matematicamente o CPF.
3. Sistema cruza CPF com data de nascimento e telefone ou e-mail.
4. Se encontrar cadastro existente, vincula automaticamente.
5. Se nao encontrar, cria pre-cadastro com acesso limitado.
6. Votacao so e liberada para cadastro vinculado e membro apto.

Regra:

> A automacao e o padrao, mas permissoes sensiveis, como votacao, dependem de vinculo confirmado e criterios formais de membresia.

## 8. Modulo de Votacao Online

O modulo de votacao online sera planejado como recurso sensivel.

Requisitos iniciais:

- CPF validado;
- cadastro vinculado;
- status de membro apto;
- voto unico;
- auditoria;
- possibilidade de voto secreto ou identificado;
- relatorio de participacao;
- abertura e encerramento controlados;
- exportacao para apoio de ata.

## 9. Comunicacao Inteligente

O Power Church deve comunicar automaticamente informacoes relevantes, respeitando:

- autorizacao do usuario;
- preferencias pessoais;
- configuracoes da igreja;
- perfil do usuario;
- regras de privacidade.

Canais possiveis:

- push notification;
- central de mensagens;
- e-mail;
- WhatsApp quando configurado.

## 10. Status das Sprints

### Sprint 1 - Fundacao

Status: concluida.

Entregas:

- arquitetura inicial;
- Django;
- PostgreSQL;
- Docker runtime;
- separacao de runtime operacional;
- documentacao inicial.

### Sprint 2 - API Base

Status: concluida.

Entregas:

- DRF;
- JWT;
- CORS;
- `/api/v1/health/`;
- `/api/v1/me/`;
- endpoints de token;
- testes automatizados.

### Sprint 3 - API Pessoas

Status: concluida.

Entregas:

- `/api/v1/people/`;
- `/api/v1/people/{id}/`;
- paginacao;
- busca;
- filtros;
- protecao JWT;
- lista sem CPF;
- detalhe com CPF;
- testes automatizados.

### Sprint 4 - Power Church Preview Center

Status: proxima etapa.

Objetivo:

- consolidar metodologia de Preview;
- criar primeiros previews da MX;
- criar primeiros previews da OX quando necessario;
- iniciar linguagem visual comum;
- desenhar BI como pilar de produto;
- definir artefatos de aprovacao de etapa.

## 11. Papéis do Projeto

### Product Owner

Paschoal Piragine Junior.

Responsavel por:

- visao ministerial;
- prioridades;
- validacao de valor;
- direcao do produto;
- relacionamento com clientes.

### Arquiteto de Software e Produto

ChatGPT.

Responsavel por:

- arquitetura;
- UX;
- roadmap;
- ADRs;
- API;
- seguranca;
- especificacoes tecnicas;
- revisao tecnica.

### Implementacao

Codex.

Responsavel por:

- desenvolvimento;
- refatoracao;
- testes;
- commits;
- integracao;
- validacao tecnica local.

## 12. Classificacao de Ideias

Toda nova ideia sera classificada como:

### Essencial

Necessaria para o sistema funcionar bem.

### Diferencial

Aumenta muito o valor do produto.

### Premium

Pode ser vendida como modulo adicional.

### Futuro

Boa ideia, mas nao para implementacao imediata.

## 13. Proximos Passos

1. Criar ADRs oficiais para as decisoes aprovadas.
2. Estruturar o Power Church Preview Center.
3. Criar primeira pre-visualizacao da Member Experience.
4. Consolidar a linguagem visual comum.
5. Planejar BI Standard e BI Premium.
6. Manter a evolucao da API em paralelo, sem iniciar Flutter antes do Preview aprovado.

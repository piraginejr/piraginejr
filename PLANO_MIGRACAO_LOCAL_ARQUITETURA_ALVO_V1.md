# Plano De Migracao Local Para Arquitetura Alvo V1

## 1. Objetivo

Migrar o Power Church, ainda na maquina local de desenvolvimento, para uma arquitetura o mais proxima possivel da producao futura, permitindo:

- validar o sistema antes da nuvem;
- reduzir a dependencia estrutural do modelo atual;
- preparar multiusuario com mais seguranca;
- separar claramente o que e problema de aplicacao e o que e problema de infraestrutura.

## 2. Meta Arquitetural

Chegar localmente a um estado em que:

- o `Django` rode sobre `PostgreSQL`;
- os processos de aplicacao fiquem empacotaveis e repetiveis;
- anexos e arquivos fiquem organizados como volumes persistentes;
- a fila de recibos e tarefas recorrentes possam rodar fora do navegador;
- as checagens funcionais continuem aprovando o sistema;
- a subida para a nuvem vire uma etapa operacional, e nao uma redescoberta tecnica.

## 3. O Que Sera Migrado Primeiro

### 3.1 Sim

Nesta fase, vamos migrar:

- banco `default` do Django para `PostgreSQL`;
- configuracao por ambiente;
- processo de aplicacao para modo mais proximo de servidor;
- organizacao de volumes e paths de dados;
- operacao da fila de recibos;
- ensaio de backup e restore do banco moderno.

### 3.2 Ainda nao

Nesta fase, ainda nao vamos:

- subir para nuvem;
- reescrever tudo para multi-tenant;
- trocar todo o nucleo legado de uma vez;
- fazer big bang do `SQLite` legado para `PostgreSQL`.

## 4. Principio Da Migracao

Nao faremos uma mudanca unica.

Vamos trabalhar em camadas:

1. preparar ambiente alvo local;
2. ligar o Django ao `PostgreSQL`;
3. validar os fluxos;
4. atacar gradualmente a dependencia do legado `SQLite`;
5. so depois abrir a etapa de nuvem.

## 5. Leitura Honesta Do Risco Atual

Hoje existem dois mundos no sistema:

- `mundo Django moderno`, que ja suporta `PostgreSQL`;
- `mundo legado`, que ainda depende muito do `SQLite` em `power_church_membros_importado.db`.

Isso significa que a migracao precisa distinguir:

- `banco do Django`;
- `banco legado`;
- `dados de arquivos`;
- `servicos operacionais`.

O maior risco nao e instalar Postgres.

O maior risco e assumir que trocar o banco do Django resolve sozinho o nucleo inteiro. Nao resolve.

## 6. Resultado Esperado Desta Fase

Ao final desta fase, queremos:

- Django funcional em `PostgreSQL` local;
- filas, e-mails e PDFs funcionando;
- checagens aprovadas;
- mapeamento claro do que ainda esta preso ao legado SQLite;
- plano tecnico objetivo do proximo passo: ou reduzir mais o legado, ou ja ir para staging em nuvem.

Tambem queremos deixar preparados, antes da nuvem:

- `Dockerfile` da aplicacao final;
- `docker-compose.yml` da aplicacao final;
- rotina oficial de `dump` do banco a ser restaurado no servidor.

## 7. Fases De Execucao

## 7.1 Modo acelerado aprovado

Para a fase atual do projeto, o cronograma operacional passou a usar um formato acelerado em `4 etapas`, com:

- `scripts automaticos + smoke operacional curto`;
- um `roteiro do operador` proprio por etapa;
- liberacao por etapa somente depois de `relatorio tecnico + roteiro do operador`.

Artefatos dessa camada:

- [PLANO_HOMOLOGACAO_OPERADOR_MIGRACAO_V1.md](/Users/piraginejr/Documents/New project/Teste/Power Church/PLANO_HOMOLOGACAO_OPERADOR_MIGRACAO_V1.md)
- [scripts/executar_homologacao_migracao.py](/Users/piraginejr/Documents/New project/Teste/Power Church/scripts/executar_homologacao_migracao.py)
- [ROTEIRO_OPERADOR_ETAPA1_FUNDACAO_V1.md](/Users/piraginejr/Documents/New project/Teste/Power Church/data/homologacao/ROTEIRO_OPERADOR_ETAPA1_FUNDACAO_V1.md)
- [ROTEIRO_OPERADOR_ETAPA2_CADASTRO_FAMILIAS_V1.md](/Users/piraginejr/Documents/New project/Teste/Power Church/data/homologacao/ROTEIRO_OPERADOR_ETAPA2_CADASTRO_FAMILIAS_V1.md)
- [ROTEIRO_OPERADOR_ETAPA3_FINANCEIRO_RECIBOS_V1.md](/Users/piraginejr/Documents/New project/Teste/Power Church/data/homologacao/ROTEIRO_OPERADOR_ETAPA3_FINANCEIRO_RECIBOS_V1.md)
- [ROTEIRO_OPERADOR_ETAPA4_IMPORTACOES_CONCILIACOES_V1.md](/Users/piraginejr/Documents/New project/Teste/Power Church/data/homologacao/ROTEIRO_OPERADOR_ETAPA4_IMPORTACOES_CONCILIACOES_V1.md)

### Fase 1: Inventario tecnico e pontos de acoplamento

Objetivo:

- identificar tudo o que ainda depende do SQLite legado;
- separar o que e:
  - configuracao,
  - leitura,
  - escrita,
  - fila,
  - arquivos,
  - importacao,
  - PDF,
  - auditoria.

Entregas:

- mapa de dependencias do legado;
- lista de tabelas e fluxos criticos;
- classificacao de risco por modulo.

Critério de saida:

- saber exatamente o que pode migrar ja para `PostgreSQL`
- e o que ainda precisa de ponte temporaria.

Artefato gerado:

- [INVENTARIO_FASE1_ACOPLAMENTOS_LEGADO_V1.md](/Users/piraginejr/Documents/New project/Teste/Power Church/INVENTARIO_FASE1_ACOPLAMENTOS_LEGADO_V1.md)

### Fase 2: Preparacao do ambiente local alvo

Objetivo:

- instalar e inicializar `PostgreSQL` local;
- criar banco, usuario e credenciais de desenvolvimento;
- criar `.env` especifico da arquitetura alvo local;
- manter o ambiente atual recuperavel.

Entregas:

- Postgres local operacional;
- configuracao local do Django apontando para Postgres;
- backup do estado anterior.

Critério de saida:

- `manage.py` sobe no novo banco;
- migrations Django aplicam sem erro.

Artefatos gerados:

- [FASE2_PREPARACAO_POSTGRES_LOCAL_V1.md](/Users/piraginejr/Documents/New project/Teste/Power Church/FASE2_PREPARACAO_POSTGRES_LOCAL_V1.md)
- [\.env.power_church_django.postgres.local](/Users/piraginejr/Documents/New project/Teste/Power Church/.env.power_church_django.postgres.local)
- [Abrir Power Church Django PostgreSQL.command](/Users/piraginejr/Documents/New project/Teste/Power Church/Abrir Power Church Django PostgreSQL.command)

Bloqueio atual identificado em `03/06/2026`:

- nao existe ainda um runtime local de `PostgreSQL` respondendo em `127.0.0.1:5432`;
- o projeto ficou preparado para a virada, mas a Fase 3 depende da instalacao/inicializacao desse servidor.

### Fase 3: Migracao do banco Django para PostgreSQL

Objetivo:

- mover o banco `default` do Django para `PostgreSQL`;
- popular estruturas e dados necessarios do lado Django;
- validar autenticacao, auditoria, fila e modelos Django.

Entregas:

- banco Django em Postgres;
- operacao local do Django preservada;
- smoke tests basicos funcionando.

Critério de saida:

- login, recibos, fila, auditoria e telas principais funcionando com o Django em Postgres.

### Fase 4: Compatibilidade com o legado

Objetivo:

- manter o que ainda depende do legado SQLite funcionando;
- explicitar as pontes entre Django/Postgres e legado/SQLite;
- reduzir o acoplamento onde for facil e seguro.

Subfrentes:

- leitura do legado;
- escrita auditada no legado;
- recibos e fila;
- importacoes bancarias;
- envelopes;
- pessoas e familias.

Critério de saida:

- sistema funcional em operacao local hibrida, com arquitetura compreendida.

### Fase 5: Padronizacao de volumes e arquivos

Objetivo:

- preparar o projeto para nao depender de caminhos “soltos” de Mac;
- organizar dados como se fossem volumes de servidor.

Inclui:

- uploads de envelopes;
- uploads de extratos;
- uploads de pessoas;
- branding;
- fotos;
- PDFs gerados;
- backups.

Critério de saida:

- estrutura de dados portavel e documentada.

### Fase 6: Fila, jobs e operacao em modo servidor

Objetivo:

- garantir que o sistema nao dependa de acao manual do navegador para continuar;
- transformar rotinas criticas em comandos operacionais.

Inclui:

- envio de fila de recibos;
- reprocessamento;
- campanhas;
- backups;
- tarefas recorrentes.

Critério de saida:

- os fluxos criticos conseguem rodar por comando, processo ou agendamento local.

### Fase 7: Homologacao pesada local

Objetivo:

- testar o comportamento da arquitetura alvo antes da nuvem.

Devemos validar:

- pessoas;
- familias;
- contribuicoes;
- rateio;
- envelopes;
- importacoes;
- extratos;
- recibos;
- e-mails;
- monitor de fila;
- merge;
- auditoria;
- backup e restore.

Critério de saida:

- checagens automaticas aprovadas;
- testes manuais criticos aprovados;
- sem regressao relevante aberta.

### Fase 8: Simulacao de operacao multiusuario local

Objetivo:

- ensaiar o comportamento com mais de uma sessao/operador;
- verificar concorrencia basica;
- observar fila, logs e banco.

Critério de saida:

- nenhum gargalo impeditivo identificado para seguir para staging.

### Fase 9: Go/No-Go para nuvem

Objetivo:

- decidir com base tecnica se:
  - ja vamos para nuvem;
  - ou se precisamos migrar mais alguma parte do legado antes.

Saida:

- aprovacao tecnica para `staging`;
- ou lista curta de bloqueios reais.

## 8. Ordem Pratica Recomendada

1. mapear acoplamentos do legado
2. instalar PostgreSQL local
3. ligar Django ao Postgres
4. rodar migrations e ajustar ambiente
5. validar fila, recibos e auditoria
6. organizar volumes e paths
7. padronizar jobs operacionais
8. rodar homologacao completa
9. simular multiusuario
10. decidir a ida para a nuvem

## 9. O Que Precisamos Produzir Durante A Migracao

- documento de inventario do legado;
- arquivo `.env` local da arquitetura alvo;
- scripts de setup do Postgres local;
- rotina de backup e restore do Postgres;
- checklist de homologacao da arquitetura alvo;
- lista de bloqueios remanescentes para staging.

## 10. Criterios De Aceite Da Migracao Local

So consideramos esta fase concluida quando:

- Django estiver usando Postgres localmente;
- `manage.py check` estiver limpo;
- checagens funcionais e visuais principais passarem;
- fila de recibos estiver operacional;
- envio de e-mail estiver operacional;
- importacoes criticas estiverem homologadas;
- backup e restore do banco moderno tiverem sido ensaiados;
- soubermos exatamente o que ainda depende do legado SQLite.

## 11. O Que Pode Dar Trabalho

Os pontos mais sensiveis provavelmente serao:

- dependencia do legado SQLite nas regras de negocio;
- diferencas de comportamento de query entre SQLite e Postgres;
- jobs operacionais hoje ainda disparados por contexto manual;
- caminhos de arquivo e volumes;
- importadores e rastreabilidade financeira.

## 12. Decisao De Desenvolvimento Durante A Migracao

Enquanto essa fase estiver em execucao:

- novas melhorias devem evitar aumentar acoplamento ao SQLite;
- toda mudanca deve privilegiar compatibilidade com Postgres;
- automacoes devem nascer como comandos reutilizaveis;
- caminhos de arquivos devem ser pensados como volumes de servidor.

## 13. Proximo Passo Imediato

Comecar pela `Fase 1: inventario tecnico e pontos de acoplamento`.

Esse e o melhor primeiro passo porque:

- nao quebra nada;
- reduz suposicoes;
- prepara uma migracao mais limpa;
- mostra exatamente o tamanho real do trabalho antes de mudar o banco.

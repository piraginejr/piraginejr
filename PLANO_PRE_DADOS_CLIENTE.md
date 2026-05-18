# Plano Pre-Dados Do Cliente

## 1. Objetivo

Este documento lista tudo o que podemos adiantar no Power Church enquanto o cliente ainda nao enviou as planilhas reais.

A regra central e:

```text
Podemos construir a base, o fluxo e os prototipos.
Nao devemos fechar regras finais dependentes da planilha real.
```

Assim ganhamos velocidade sem correr o risco de modelar o sistema em cima de suposicoes erradas.

## 2. O Que Podemos Adiantar Com Seguranca

### 2.1 Estrutura Do Projeto

Podemos criar:

- pasta exclusiva do projeto;
- documentacao inicial;
- estrutura de codigo;
- estrutura de banco;
- scripts de criacao do banco;
- arquivos de configuracao;
- convencao de nomes;
- separacao entre app, banco, documentos e testes.

Status atual:

```text
Concluido parcialmente.
```

Ja temos:

- `ARQUITETURA_POWER_CHURCH_V0.md`
- `BANCO_MODULAR_V0.md`
- `schema_power_church_v0.sql`

### 2.2 Banco Modular V0

Podemos adiantar:

- criacao real do banco SQLite;
- script de inicializacao;
- carga inicial de modulos;
- carga inicial de permissoes;
- carga inicial de tipos de contribuicao;
- carga inicial de formas de recebimento;
- organizacao demonstrativa;
- unidade demonstrativa;
- usuario administrador local.

Nao depende dos dados do cliente, porque sao estruturas gerais.

### 2.3 Dados Ficticios Para Teste

Podemos criar uma base de demonstracao com dados falsos.

Exemplos:

- pessoas ficticias;
- familias ficticias;
- membros ativos;
- frequentadores;
- visitantes;
- arquivo morto;
- dizimos ficticios;
- ofertas identificadas;
- ofertas nao identificadas;
- plano de contas generico;
- centros de custo genericos;
- lancamentos financeiros ficticios;
- metas ficticias.

Vantagem:

Permite testar telas, relatorios, filtros, recibos e importacao sem expor dados reais.

### 2.4 Importador Generico De Pessoas

Podemos adiantar o motor de importacao, mesmo sem a planilha real.

Funcionalidades que podem ser criadas agora:

- upload de Excel ou CSV;
- leitura de abas;
- deteccao de cabecalhos;
- pre-visualizacao;
- mapeamento de colunas;
- sugestao automatica de campos;
- criacao de campos personalizados;
- escolha de ignorar/enviar para observacoes;
- resumo antes de confirmar;
- gravacao em lote;
- desfazer lote;
- relatorio de importacao.

O que ficara para ajustar depois:

- sinonimos especificos usados pelo cliente;
- regras especificas de status;
- colunas proprias da denominacao;
- tratamento de formatos incomuns.

### 2.5 Mapeamento Inteligente De Campos

Podemos criar um dicionario inicial de sinonimos.

Exemplos:

```text
Nome -> nome, membro, membro nome, nome completo
CPF -> cpf, documento, cpf/cnpj
Telefone -> telefone, celular, contato, fone
WhatsApp -> whatsapp, whats, zap
Endereco -> endereco, logradouro, rua
Data de nascimento -> nascimento, dt nasc, aniversario
Batismo -> batismo, data batismo, batizado em
Status -> situacao, status, condicao
Conjuge -> esposo, esposa, conjuge
Filhos -> filhos, dependentes
```

Isso nao precisa esperar a planilha real. Depois ampliamos com os nomes reais encontrados.

### 2.6 Campos Personalizados

Podemos implementar a logica base:

- criar campo personalizado;
- escolher tipo do campo;
- salvar valor por pessoa;
- exibir no cadastro da pessoa;
- permitir usar em filtro/relatorio no futuro;
- preservar origem na importacao.

Tipos iniciais:

```text
texto
numero
data
sim_nao
lista
lista_multipla
valor_monetario
referencia_pessoa
observacao_longa
```

### 2.7 Duplicidade E Qualidade De Dados

Podemos criar regras gerais de auditoria:

- CPF repetido;
- CPF invalido;
- nome igual;
- nome parecido;
- mesmo telefone;
- mesmo email;
- data de nascimento igual com nome parecido;
- conjuge informado mas nao encontrado;
- campos obrigatorios ausentes;
- data invalida;
- status desconhecido.

Podemos tambem criar opcoes:

- importar como novo;
- unir com existente;
- manter em revisao;
- ignorar linha;
- corrigir campo;
- aplicar decisao em lote.

### 2.8 Cadastro De Pessoas MVP

Podemos criar telas basicas sem dados reais:

- lista de pessoas;
- busca por nome, CPF, telefone, perfil e status;
- cadastro manual;
- edicao;
- detalhe da pessoa;
- contatos;
- enderecos;
- perfis;
- historico;
- campos personalizados;
- relacoes familiares.

Isso e estrutural e nao depende da planilha real.

### 2.9 Arquivo Morto

Podemos adiantar a estrutura:

- status `arquivo morto`;
- campo `arquivo_morto`;
- historico de saida;
- motivo;
- destino;
- data;
- observacoes.

Tambem podemos preparar filtros:

- ativos;
- inativos;
- transferidos;
- falecidos;
- arquivo morto;
- todos.

### 2.10 Contribuicoes MVP

Podemos criar a estrutura de lancamento:

- dizimo;
- oferta identificada;
- oferta nao identificada;
- campanha;
- data de recebimento;
- competencia;
- valor;
- forma de recebimento;
- conta financeira;
- pessoa vinculada quando houver.

Tambem podemos validar regras:

```text
Dizimo exige pessoa.
Oferta identificada exige pessoa.
Oferta nao identificada nao exige pessoa.
```

### 2.11 Recibos

Podemos adiantar:

- numeracao;
- recibo por pessoa;
- recibo por periodo;
- recibo por contribuicao;
- recibo com varios itens;
- cancelamento de recibo;
- exportacao/impressao em HTML/PDF futuramente.

O modelo visual definitivo pode esperar, mas a estrutura logica pode nascer agora.

### 2.12 Financeiro Basico

Podemos construir:

- plano de contas generico;
- centros de custo genericos;
- contas financeiras;
- lancamentos financeiros;
- integracao de contribuicoes com financeiro;
- filtros por competencia, vencimento, pagamento, plano de contas e centro de custo.

O plano de contas final deve esperar o cliente.

Mas o mecanismo de plano de contas pode ser feito agora.

### 2.13 One Page Report - Motor, Nao Numeros Finais

Podemos adiantar o motor:

- estrutura de metricas;
- grupos do One Page;
- filtros por periodo;
- consultas por plano de contas;
- consultas por centro de custo;
- metas;
- necessidade financeira dos proximos 7 dias;
- layout inicial.

O que deve esperar:

- formulas finais;
- classificacoes exatas;
- nomes dos blocos do cliente;
- validacao contra a planilha real.

### 2.14 Relatorios Basicos

Podemos adiantar relatorios com dados ficticios:

- pessoas por status;
- pessoas por perfil;
- aniversariantes;
- arquivo morto;
- contribuicoes por pessoa;
- contribuicoes por tipo;
- recibos emitidos;
- financeiro por plano de contas;
- financeiro por centro de custo;
- One Page demonstrativo.

### 2.15 LGPD E Seguranca

Podemos adiantar decisoes de seguranca:

- CPF como dado sensivel;
- controle de acesso por usuario;
- permissao por modulo;
- auditoria de alteracoes;
- backup;
- exportacao;
- politica de exclusao logica;
- mascara de CPF em telas nao administrativas;
- trilha de importacao.

### 2.16 Experiencia Do Usuario

Podemos prototipar:

- tela inicial;
- menu por modulos ativos;
- visual executivo;
- mensagens de pendencia;
- fluxo de importacao guiado;
- central de relatorios;
- padrao de botoes;
- navegacao entre telas.

### 2.17 Testes Automatizados

Podemos criar testes para:

- criacao do banco;
- unicidade de CPF por organizacao;
- CPF vazio permitido;
- pessoa com varios perfis;
- campo personalizado;
- importacao ficticia;
- desfazer lote;
- contribuicao identificada;
- contribuicao nao identificada;
- recibo;
- lancamento financeiro.

## 3. O Que Deve Esperar Os Dados Reais

Algumas decisoes devem ser adiadas para evitar retrabalho.

### 3.1 Mapeamento Final Das Planilhas

Devemos esperar para fechar:

- nomes reais das colunas;
- abas existentes;
- formato de datas;
- estrutura de arquivo morto;
- campos proprios da igreja;
- campos obrigatorios reais.

### 3.2 Plano De Contas Final

Devemos esperar:

- codigos;
- hierarquia;
- grupos;
- classificacao orcamentaria;
- classificacao extraorcamentaria;
- vinculo com One Page.

### 3.3 Centros De Custo Finais

Devemos esperar:

- lista real de centros;
- se sao ligados a plano de contas;
- se permitem rateio;
- se representam ministerios, departamentos ou projetos.

### 3.4 Regras Finais Do One Page

Devemos esperar:

- formulas atuais;
- origem de cada indicador;
- filtros usados;
- comparacao com valores da planilha;
- metas e criterios.

### 3.5 Layout Final De Recibo

Podemos criar prototipo, mas o definitivo depende de:

- dados legais da igreja;
- texto desejado;
- numeracao;
- assinatura;
- logomarca;
- periodo;
- validade fiscal/administrativa esperada.

## 4. Sequencia Recomendada Enquanto Aguardamos

### Etapa A - Base Tecnica

1. Criar script que gera o banco real `power_church.db`.
2. Criar carga inicial de modulos e permissoes.
3. Criar organizacao demonstrativa.
4. Criar dados ficticios.

### Etapa B - Prototipo De Aplicacao

1. Criar app web separado do Power Finance.
2. Criar tela inicial.
3. Criar menu por modulos.
4. Criar tela de pessoas.
5. Criar tela de importacao.

### Etapa C - Importacao De Pessoas

1. Upload de planilha.
2. Leitura de abas.
3. Mapeamento de campos.
4. Campos personalizados.
5. Previa.
6. Confirmacao.
7. Desfazer lote.

### Etapa D - Pessoas E Relacionamento Basico

1. Cadastro manual.
2. Busca.
3. Detalhe.
4. Perfis.
5. Status.
6. Contatos.
7. Enderecos.
8. Historico.
9. Relacionamentos familiares.

### Etapa E - Contribuicoes E Recibos

1. Lancar dizimo.
2. Lancar oferta identificada.
3. Lancar oferta nao identificada.
4. Gerar recibo.
5. Relatorio por pessoa.

### Etapa F - Financeiro E One Page Demonstrativo

1. Plano de contas generico.
2. Centros de custo genericos.
3. Lancamentos ficticios.
4. One Page com dados de demonstracao.
5. Preparar comparador futuro contra a planilha real.

## 5. Priorizacao Sugerida

Se quisermos ganhar o maximo de produtividade antes da planilha real, a ordem ideal e:

```text
1. Criar o banco real local
2. Criar dados ficticios
3. Criar prototipo web
4. Criar importacao de pessoas
5. Criar campos personalizados
6. Criar cadastro de pessoas
7. Criar contribuicoes
8. Criar recibos
9. Criar financeiro basico
10. Criar One Page demonstrativo
```

## 6. Primeira Entrega Pratica Recomendada

A primeira entrega pratica antes dos dados do cliente deveria ser:

```text
Power Church Demo Local
```

Com:

- banco criado;
- igreja demonstrativa;
- dados ficticios;
- tela inicial;
- cadastro de pessoas;
- importacao de pessoas com planilha ficticia;
- campos personalizados;
- contribuicoes basicas;
- recibo simples;
- relatorio demonstrativo.

Isso nos permitiria receber a planilha real depois e encaixar no fluxo ja existente, em vez de comecar do zero naquele momento.

## 7. Risco Principal

O maior risco e construir detalhes especificos demais antes de ver a planilha real.

Para evitar isso:

- construir mecanismos genericos;
- usar dados ficticios;
- manter configuracoes flexiveis;
- nao congelar o plano de contas final;
- nao congelar o One Page final;
- nao assumir que todas as igrejas organizam os dados do mesmo jeito.

## 8. Resumo

Enquanto o cliente nao envia dados, podemos adiantar bastante:

- base tecnica;
- banco;
- app;
- importador;
- cadastro de pessoas;
- campos personalizados;
- contribuicoes;
- recibos;
- financeiro basico;
- One Page demonstrativo;
- testes.

O que deve esperar sao as regras finais dependentes da realidade do cliente.

Isso cria uma vantagem: quando a planilha chegar, estaremos ajustando e validando, nao comecando do zero.

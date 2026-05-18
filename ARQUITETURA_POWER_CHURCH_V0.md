# Arquitetura Power Church V0

## 1. Objetivo

O Power Church nasce como uma plataforma modular para gestao de igrejas e organizacoes religiosas.

A ideia nao e criar apenas um sistema financeiro, mas uma base empresarial capaz de integrar:

- cadastro de pessoas;
- membros, frequentadores, visitantes e arquivo morto;
- relacionamento pastoral e administrativo;
- dizimos, ofertas e recibos;
- financeiro com plano de contas e centros de custo;
- importacao assistida de planilhas;
- relatorios estrategicos, incluindo One Page Report;
- modulos futuros, como RH, ministerios, eventos, patrimonio e voluntarios.

O Power Finance V1 permanece como referencia funcional e produto domestico/simples. O Power Church deve ter arquitetura propria, preparada para uso profissional.

## 2. Principios Do Projeto

- O sistema deve absorver a realidade atual da igreja, sem obrigar redigitacao massiva.
- A importacao de planilhas e parte central da implantacao.
- O banco deve ser modular e preparado para expansao.
- O sistema deve permitir contratar/liberar modulos progressivamente.
- CPF deve ser campo de identificacao, mas nao chave primaria estrutural.
- Dados desconhecidos na importacao nao devem ser descartados.
- Campos novos devem poder ser criados como campos personalizados, sem alterar a estrutura basica.
- O One Page Report deve ser tratado como relatorio estrategico prioritario.
- A V0 deve ser simples o suficiente para iniciar, mas solida o suficiente para crescer.

## 3. Visao Modular

### 3.1 Nucleo Da Plataforma

Modulo obrigatorio para todos os clientes.

Responsabilidades:

- cadastrar organizacoes/igrejas;
- controlar unidades ou congregacoes;
- controlar usuarios;
- controlar permissoes;
- controlar modulos ativos;
- registrar auditoria;
- armazenar configuracoes gerais;
- servir de base para todos os modulos.

Tabelas candidatas:

```text
organizacoes
unidades
usuarios
perfis_acesso
permissoes
modulos
modulos_organizacao
configuracoes_organizacao
auditoria
documentos
```

### 3.2 Modulo De Pessoas

Modulo fundamental. Deve nascer antes ou junto com o financeiro, porque dizimos, ofertas identificadas, recibos e relacionamento dependem da pessoa correta.

Responsabilidades:

- cadastrar pessoas;
- classificar pessoas por perfis;
- controlar status;
- guardar contatos e enderecos;
- controlar relacionamentos familiares;
- registrar eventos historicos;
- preservar arquivo morto;
- permitir campos personalizados.

Perfis possiveis:

```text
membro
frequentador
visitante
doador
funcionario
voluntario
fornecedor
lider
pastor
```

Status possiveis:

```text
ativo
visitante
frequentador
membro ativo
membro inativo
transferido
desligado
falecido
arquivo morto
```

No contexto desta primeira planilha, `membro inativo` representa pessoa que ainda consta como membro, mas nao possui privilegios de votacao em assembleias.

Tabelas candidatas:

```text
pessoas
pessoa_perfis
pessoa_contatos
pessoa_enderecos
pessoa_relacionamentos
pessoa_historico
campos_personalizados
valores_campos_personalizados
```

Observacao importante:

```text
pessoas.id = chave primaria interna
pessoas.cpf = identificador unico opcional quando existir
```

O CPF deve facilitar busca, importacao e automacao, mas nao deve ser a chave primaria principal.

### 3.3 Modulo De Importacao Assistida

Modulo essencial para implantacao. Igrejas normalmente ja possuem planilhas de membros, arquivo morto, financas, dizimos, ofertas e relatorios.

Responsabilidades:

- importar arquivos Excel e CSV;
- permitir mapear campos;
- detectar duplicidades;
- sugerir campos conhecidos;
- permitir criar campos personalizados durante a importacao;
- preservar dados nao classificados;
- gerar relatorio de importacao;
- permitir desfazer lote de importacao;
- manter historico da origem dos dados.

Tipos de importacao previstos:

```text
pessoas ativas
arquivo morto
visitantes
contribuicoes
financeiro
plano de contas
centros de custo
one page report
```

Fluxo recomendado:

```text
1. Enviar planilha
2. Escolher tipo de importacao
3. Escolher organizacao/unidade destino
4. Mapear campos
5. Tratar campos nao reconhecidos
6. Pre-visualizar registros
7. Corrigir pendencias
8. Confirmar importacao
9. Gerar resumo do lote
```

### 3.4 Campos Personalizados Na Importacao

Problema:

Cada igreja possui planilhas com colunas proprias, como:

```text
Classe EBD
Ministerio
Pastor responsavel
Congregacao anterior
Data de reconciliacao
Grupo familiar
Forma de entrada
```

Regra:

Essas colunas nao devem ser descartadas e tambem nao devem virar colunas fisicas novas na tabela principal de pessoas.

Solucao:

Criar campos personalizados por organizacao e modulo.

Tabelas candidatas:

```text
campos_personalizados
- id
- organizacao_id
- modulo
- nome
- tipo
- obrigatorio
- visivel_no_cadastro
- usar_em_relatorios
- ativo

valores_campos_personalizados
- id
- campo_id
- registro_tipo
- registro_id
- valor
```

Tipos de campo:

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

Opcoes para cada coluna nao reconhecida:

```text
Ignorar
Enviar para observacoes
Criar campo personalizado
Associar a campo personalizado existente
Criar como lista de opcoes
Marcar para revisao posterior
```

Regra de evolucao:

Se um campo personalizado se tornar recorrente e estrutural, ele podera ser promovido para uma tabela oficial no futuro.

Exemplo:

```text
Ministerio como campo personalizado
-> no futuro vira modulo oficial de ministerios
```

### 3.5 Modulo De Contribuicoes

Modulo voltado a dizimos, ofertas e outras entradas da igreja.

Responsabilidades:

- registrar dizimos;
- registrar ofertas identificadas;
- registrar ofertas nao identificadas;
- vincular contribuicoes a pessoas quando aplicavel;
- gerar recibos;
- emitir relatorios individuais;
- controlar campanhas e finalidades;
- integrar receitas ao financeiro.

Tipos de contribuicao:

```text
dizimo
oferta identificada
oferta nao identificada
campanha
missoes
construcao
acao social
evento
outras receitas
```

Tabelas candidatas:

```text
contribuicoes
tipos_contribuicao
recibos
formas_recebimento
campanhas
```

Regras importantes:

- Dizimo normalmente exige pessoa vinculada.
- Oferta identificada exige pessoa vinculada.
- Oferta nao identificada pode ficar sem pessoa.
- CPF pode ser usado para automacao de importacao.
- Recibo deve apontar para pessoa e contribuicoes relacionadas.

### 3.6 Modulo Financeiro

Modulo para gestao financeira profissional.

Responsabilidades:

- controlar receitas e despesas;
- usar plano de contas;
- usar centros de custo;
- controlar contas financeiras/bancarias;
- controlar vencimentos e pagamentos;
- controlar status;
- permitir rateios;
- gerar relatorios financeiros.

Tabelas candidatas:

```text
lancamentos_financeiros
plano_contas
centros_custo
contas_financeiras
rateios_lancamento
fornecedores
documentos_financeiros
```

Classificacoes importantes:

```text
receita operacional
outras receitas
despesa operacional
despesa orcamentaria
despesa extraorcamentaria
```

Observacao:

Fornecedor, membro, doador, funcionario e voluntario devem idealmente nascer da mesma base de pessoas/entidades, evitando cadastros duplicados.

### 3.7 Modulo De Relatorios Estrategicos

Modulo responsavel por relatorios de gestao e decisao.

Relatorio prioritario:

```text
One Page Report
```

Blocos iniciais:

```text
receitas operacionais
outras receitas
total de receitas
despesas operacionais
despesas orcamentarias
despesas extraorcamentarias
necessidade financeira proximos 7 dias
metas
resultado operacional
saldos
alertas
```

Regra:

Antes de programar o One Page Report definitivo, criar um dicionario de metricas.

Exemplo:

```text
Metrica: Receitas operacionais
Origem: contribuicoes + lancamentos financeiros
Filtro: plano de contas classificado como receita operacional
Periodo: competencia selecionada
Calculo: soma dos valores
```

Exemplo:

```text
Metrica: Necessidade financeira proximos 7 dias
Origem: contas a pagar e contas a receber
Filtro: vencimento entre hoje e hoje + 7 dias, status pendente
Calculo: despesas previstas - receitas previstas
```

### 3.8 CRM Pastoral E Relacionamento

Modulo futuro, mas o banco ja deve permitir evolucao.

Responsabilidades futuras:

- acompanhar visitantes;
- acompanhar membros afastados;
- registrar visitas;
- registrar atendimentos;
- registrar pedidos de oracao;
- acompanhar novos convertidos;
- acompanhar candidatos ao batismo;
- organizar aniversariantes;
- acompanhar familias.

Tabelas candidatas futuras:

```text
atendimentos
visitas
pedidos_oracao
acompanhamentos
grupos
ministerios
eventos_pessoa
```

## 4. Modelo Conceitual Inicial

Visao resumida:

```text
Organizacao
  -> Unidades
  -> Usuarios
  -> Modulos ativos
  -> Pessoas
       -> Perfis
       -> Contatos
       -> Enderecos
       -> Relacionamentos familiares
       -> Historico
       -> Campos personalizados
  -> Contribuicoes
       -> Pessoa opcional
       -> Tipo de contribuicao
       -> Recibo
  -> Financeiro
       -> Plano de contas
       -> Centro de custo
       -> Conta financeira
       -> Lancamentos
       -> Rateios
  -> Relatorios
       -> One Page Report
       -> Relatorios individuais
       -> Relatorios por periodo
```

## 5. MVP Recomendado

Primeira versao profissional recomendada:

```text
1. Nucleo da plataforma
2. Organizacao e unidade
3. Usuarios simples
4. Modulos ativos
5. Cadastro de pessoas
6. Perfis e status de pessoas
7. Importacao assistida de pessoas
8. Campos personalizados na importacao
9. Contribuicoes basicas
10. Recibos basicos
11. Financeiro basico com plano de contas e centros de custo
12. One Page Report inicial
```

O MVP deve evitar complexidade excessiva de RH, patrimonio, eventos e ministerios oficiais. Esses modulos ficam previstos, mas nao precisam nascer completos.

## 6. Roadmap Inicial

### Fase 0 - Arquitetura

- consolidar este documento;
- definir entidades principais;
- definir o banco V0;
- definir fluxo de importacao;
- definir MVP.

### Fase 1 - Banco Modular V0

- criar schema inicial;
- criar tabelas centrais;
- criar tabelas de pessoas;
- criar tabelas de campos personalizados;
- criar tabelas de importacao;
- criar tabelas de contribuicoes;
- criar tabelas financeiras basicas.

### Fase 2 - Importacao De Pessoas

- upload de planilha;
- leitura de abas;
- mapeamento de campos;
- criacao de campos personalizados;
- deteccao de duplicidades;
- pre-visualizacao;
- confirmacao;
- desfazer lote.

### Fase 3 - Cadastro De Pessoas

- tela de busca;
- tela de detalhe;
- edicao de dados basicos;
- exibicao de campos personalizados;
- exibicao de historico;
- status e perfis.

### Fase 4 - Contribuicoes

- lancamento de dizimo;
- lancamento de oferta identificada;
- lancamento de oferta nao identificada;
- recibo;
- relatorio por pessoa;
- importacao por CPF quando houver.

### Fase 5 - Financeiro E Relatorios

- plano de contas;
- centros de custo;
- lancamentos financeiros;
- integracao com contribuicoes;
- One Page Report;
- comparacao com planilha original.

## 7. Decisoes Arquiteturais Iniciais

### 7.1 CPF

Decisao:

CPF sera campo unico quando informado, mas nao sera chave primaria principal.

Motivo:

- pode estar ausente;
- pode estar digitado errado;
- pode haver estrangeiros;
- pode haver menores;
- pode haver duplicidade temporaria durante importacao;
- CPF e dado sensivel.

### 7.2 Campos Personalizados

Decisao:

Campos novos vindos de planilha serao tratados como campos personalizados, nao como novas colunas na tabela principal.

Motivo:

- protege a estrutura basica;
- permite flexibilidade por igreja;
- evita perda de informacao;
- permite relatorios futuros;
- permite promocao futura para campos oficiais.

### 7.3 Modulos Progressivos

Decisao:

O sistema tera tabela de modulos ativos por organizacao.

Motivo:

- cliente pode contratar por etapas;
- telas podem ser liberadas progressivamente;
- base pode estar preparada sem expor tudo de uma vez.

### 7.4 Importacao Como Produto

Decisao:

Importacao assistida sera modulo essencial, nao ferramenta secundaria.

Motivo:

- igrejas ja possuem historico em planilhas;
- redigitacao reduz adesao;
- importacao bem feita aumenta confianca;
- auditoria da importacao facilita implantacao.

## 8. Pendencias Para Confirmar Com Planilhas Reais

Precisaremos analisar:

- planilha principal financeira;
- planilha de One Page Report;
- planilha de membros ativos;
- planilha de visitantes, se existir;
- planilha de arquivo morto, se existir;
- planilha de dizimos/ofertas, se separada;
- lista de plano de contas;
- lista de centros de custo.

Perguntas que as planilhas devem responder:

- Como a igreja define membro, frequentador, visitante e arquivo morto?
- Como registra batismo, entrada e transferencia?
- Como identifica doadores?
- Dizimos e ofertas possuem CPF?
- Recibos ja existem hoje?
- O financeiro usa competencia, vencimento, pagamento ou caixa?
- O One Page usa quais formulas e filtros?
- Centro de custo vem do plano de contas ou de campo proprio?
- Existem rateios?
- Existem multiplas unidades/congregacoes?

## 9. Proximo Passo

Proximo passo recomendado:

```text
Passo 2 - Desenhar o Banco Modular V0
```

Entregaveis do Passo 2:

- lista de tabelas;
- principais campos;
- relacoes entre tabelas;
- chaves primarias e estrangeiras;
- regras basicas de integridade;
- proposta de migracao/importacao inicial;
- definicao do que entra no MVP.

## 10. Resumo Executivo

O Power Church deve nascer como uma plataforma modular para igrejas, com base forte em pessoas, importacao e contribuicoes.

A planilha principal e os relatorios atuais serao usados como fonte de entendimento e validacao, mas o sistema nao deve ser apenas uma copia da planilha.

A arquitetura deve permitir que a igreja comece por poucos modulos e amplie gradualmente, sem trocar de sistema e sem perder dados.

O diferencial inicial sera:

- importar a memoria da igreja;
- preservar campos proprios de cada igreja;
- vincular contribuicoes a pessoas;
- gerar recibos e relatorios individuais;
- criar base para financeiro com plano de contas e centros de custo;
- gerar o One Page Report com confiabilidade.

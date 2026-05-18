# Banco Modular Power Church V0

## 1. Objetivo Do Banco

Este documento transforma a arquitetura do Power Church em uma primeira estrutura de banco de dados.

O objetivo do Banco Modular V0 e criar uma base preparada para:

- multiplas igrejas/organizacoes;
- liberacao progressiva de modulos;
- cadastro de pessoas;
- importacao assistida de planilhas;
- campos personalizados;
- contribuicoes, dizimos, ofertas e recibos;
- financeiro com plano de contas e centros de custo;
- One Page Report;
- expansao futura para CRM pastoral, ministerios, RH, eventos e patrimonio.

Esta V0 deve ser suficiente para iniciar o prototipo, sem tentar resolver todos os modulos futuros em detalhes.

## 2. Decisoes De Modelagem

### 2.1 Banco inicial

Para prototipo local, o schema inicial sera compativel com SQLite.

Isso permite:

- desenvolvimento rapido;
- portabilidade local;
- instalacao simples;
- baixo custo inicial.

O desenho, porem, deve evitar escolhas que impecam migracao futura para PostgreSQL.

### 2.2 Multi-organizacao

Quase todas as tabelas operacionais terao `organizacao_id`.

Isso permite que o sistema suporte varias igrejas no futuro, sem misturar dados.

Regra:

```text
Tabelas de cadastro global: podem nao ter organizacao_id.
Tabelas de dados da igreja: devem ter organizacao_id.
```

Exemplos globais:

```text
modulos
permissoes
```

Exemplos por organizacao:

```text
pessoas
contribuicoes
lancamentos_financeiros
plano_contas
centros_custo
```

### 2.3 CPF

CPF nao sera chave primaria.

Regra:

```text
pessoas.id = chave primaria interna
pessoas.cpf = identificador unico opcional por organizacao
```

Motivo:

- CPF pode estar ausente;
- pode estar incorreto;
- pode haver estrangeiros;
- pode haver menores;
- pode haver duplicidades temporarias durante importacao.

### 2.4 Pessoas como base comum

A tabela `pessoas` sera a base para:

- membros;
- frequentadores;
- visitantes;
- doadores;
- funcionarios;
- voluntarios;
- fornecedores;
- lideres;
- pastores.

Isso evita duplicidade de cadastros.

Uma pessoa pode ter varios perfis ao mesmo tempo por meio de `pessoa_perfis`.

### 2.5 Campos personalizados

Campos personalizados nao alteram a tabela principal.

Exemplo:

Se uma planilha tiver `Classe EBD`, o sistema cria:

```text
campos_personalizados.nome = Classe EBD
valores_campos_personalizados.valor_texto = Adultos
```

Isso preserva flexibilidade sem destruir a estrutura basica.

### 2.6 Importacao como entidade auditavel

Toda importacao deve gerar um lote.

O lote registra:

- arquivo original;
- tipo de importacao;
- abas;
- mapeamentos;
- linhas;
- pendencias;
- registros criados;
- possibilidade de desfazer ou auditar.

### 2.7 Contribuicoes integradas ao financeiro

Contribuicoes sao um modulo proprio, mas devem poder gerar lancamentos financeiros.

Exemplo:

```text
contribuicoes.id = 10
lancamentos_financeiros.origem_tipo = contribuicao
lancamentos_financeiros.origem_id = 10
```

Assim, relatorios financeiros e recibos continuam consistentes.

### 2.8 Rateio por centro de custo

Um lancamento pode ter um centro de custo principal.

Se precisar dividir entre varios centros, usa-se `rateios_lancamento`.

Regra:

```text
Sem rateio: usar lancamentos_financeiros.centro_custo_id
Com rateio: usar rateios_lancamento
```

## 3. Grupos De Tabelas

## 3.1 Nucleo Da Plataforma

### organizacoes

Representa uma igreja, associacao, instituicao ou cliente do sistema.

Campos principais:

```text
id
nome
nome_fantasia
cnpj
tipo
status
observacoes
criado_em
atualizado_em
```

### unidades

Representa congregacoes, filiais, sedes ou unidades internas.

Campos principais:

```text
id
organizacao_id
nome
tipo
cidade
uf
ativa
```

### usuarios

Representa pessoas com acesso ao sistema.

Campos principais:

```text
id
nome
email
senha_hash
ativo
```

### usuarios_organizacoes

Liga usuarios a organizacoes.

Campos principais:

```text
usuario_id
organizacao_id
perfil_acesso_id
ativo
```

### modulos

Lista global dos modulos existentes.

Exemplos:

```text
nucleo
pessoas
importacao
contribuicoes
financeiro
relatorios
crm
rh
ministerios
eventos
patrimonio
```

### modulos_organizacao

Controla quais modulos estao liberados para cada organizacao.

Campos principais:

```text
organizacao_id
modulo_id
ativo
plano
data_ativacao
```

### auditoria

Registra alteracoes relevantes.

Campos principais:

```text
organizacao_id
usuario_id
acao
tabela
registro_id
dados_antes_json
dados_depois_json
criado_em
```

## 3.2 Pessoas

### pessoas

Cadastro principal de pessoas.

Campos principais:

```text
id
organizacao_id
unidade_preferencial_id
codigo_interno
nome
nome_social
cpf
rg
data_nascimento
sexo
estado_civil
email_principal
telefone_principal
whatsapp_principal
status
arquivo_morto
observacoes
ativo
```

Status operacionais iniciais:

```text
ativo
membro_ativo
membro_inativo
frequentador
visitante
transferido
desligado
falecido
arquivo_morto
```

Regra de membresia:

```text
membro_inativo significa que a pessoa consta como membro, mas nao possui privilegios de votacao em assembleias.
```

### pessoa_perfis

Permite multiplos papeis por pessoa.

Exemplos:

```text
membro
frequentador
visitante
doador
funcionario
voluntario
fornecedor
pastor
lider
```

### pessoa_contatos

Guarda telefones, emails e contatos alternativos.

Campos:

```text
pessoa_id
tipo
valor
principal
observacoes
```

### pessoa_enderecos

Permite mais de um endereco por pessoa.

Campos:

```text
pessoa_id
tipo
cep
logradouro
numero
complemento
bairro
cidade
uf
principal
```

### pessoa_relacionamentos

Representa vinculos familiares ou pessoais.

Exemplos:

```text
conjuge
filho
pai
mae
responsavel
irmao
outro
```

### pessoa_historico

Registra eventos da vida da pessoa na igreja.

Exemplos:

```text
batismo
profissao de fe
transferencia recebida
transferencia enviada
desligamento
retorno
casamento
falecimento
mudanca de status
```

## 3.3 Campos Personalizados

### campos_personalizados

Define campos criados por organizacao.

Campos:

```text
organizacao_id
modulo
registro_tipo
nome
chave
tipo
opcoes_json
obrigatorio
visivel_no_cadastro
usar_em_relatorios
ativo
```

### valores_campos_personalizados

Armazena os valores dos campos personalizados.

Campos:

```text
campo_id
registro_tipo
registro_id
valor_texto
valor_numero
valor_data
valor_json
```

## 3.4 Importacao

### import_lotes

Representa cada importacao realizada ou em preparacao.

Campos:

```text
organizacao_id
unidade_id
tipo_importacao
arquivo_nome
status
total_linhas
linhas_importadas
linhas_ignoradas
linhas_com_erro
```

### import_abas

Representa abas de planilhas Excel.

Campos:

```text
lote_id
nome_aba
competencia_sugerida
total_linhas
```

### import_mapeamentos

Registra como cada coluna da planilha foi tratada.

Campos:

```text
lote_id
coluna_origem
campo_destino
acao
campo_personalizado_id
```

Acoes possiveis:

```text
mapear_campo
criar_campo_personalizado
associar_campo_personalizado
observacoes
ignorar
revisar_depois
```

### import_linhas

Guarda pre-visualizacao e resultado de cada linha.

Campos:

```text
lote_id
aba_id
numero_linha
status
dados_originais_json
registro_tipo
registro_id
```

### import_pendencias

Guarda erros, avisos e decisoes da importacao.

Exemplos:

```text
cpf duplicado
cpf invalido
nome semelhante
campo obrigatorio ausente
data invalida
categoria desconhecida
centro de custo desconhecido
```

## 3.5 Contribuicoes

### tipos_contribuicao

Define tipos como dizimo, oferta identificada e oferta nao identificada.

Campos:

```text
organizacao_id
codigo
nome
exige_pessoa
natureza_receita
plano_conta_id
ativo
```

### formas_recebimento

Formas de entrada financeira.

Exemplos:

```text
dinheiro
pix
cartao
transferencia
boleto
cheque
```

### campanhas

Campanhas ou finalidades especificas.

Exemplos:

```text
missoes
construcao
acao social
evento especial
```

### contribuicoes

Lancamentos de dizimos, ofertas e campanhas.

Campos principais:

```text
organizacao_id
unidade_id
pessoa_id
tipo_contribuicao_id
campanha_id
data_recebimento
competencia
valor
forma_recebimento_id
conta_financeira_id
observacoes
```

Regra:

```text
Dizimo e oferta identificada devem ter pessoa_id.
Oferta nao identificada pode ficar sem pessoa_id.
```

### recibos

Documento emitido para uma pessoa.

Campos:

```text
organizacao_id
pessoa_id
numero
data_emissao
periodo_inicio
periodo_fim
valor_total
status
```

### recibo_itens

Liga recibo a contribuicoes.

Campos:

```text
recibo_id
contribuicao_id
valor
```

## 3.6 Financeiro

### plano_contas

Plano de contas gerencial/financeiro.

Campos:

```text
organizacao_id
codigo
nome
pai_id
nivel
tipo
grupo_estrategico
aceita_lancamento
ativo
```

Tipos:

```text
receita
despesa
ativo
passivo
patrimonio
```

Grupos estrategicos:

```text
receita_operacional
outras_receitas
despesa_operacional
despesa_orcamentaria
despesa_extraorcamentaria
```

### centros_custo

Centros de custo gerenciais.

Campos:

```text
organizacao_id
codigo
nome
pai_id
ativo
```

### contas_financeiras

Caixa, contas bancarias e contas digitais.

Campos:

```text
organizacao_id
nome
tipo
banco
agencia
conta
saldo_inicial
data_saldo_inicial
ativa
```

### lancamentos_financeiros

Lancamentos financeiros gerais.

Campos principais:

```text
organizacao_id
unidade_id
tipo
origem_tipo
origem_id
entidade_pessoa_id
plano_conta_id
centro_custo_id
conta_financeira_id
competencia
data_emissao
data_vencimento
data_pagamento
valor
status
descricao
```

Status possiveis:

```text
previsto
pendente
pago
recebido
cancelado
```

### rateios_lancamento

Divide um lancamento entre centros de custo.

Campos:

```text
lancamento_id
centro_custo_id
percentual
valor
```

### metas

Metas para One Page Report e gestao.

Campos:

```text
organizacao_id
nome
indicador
periodo_inicio
periodo_fim
valor_alvo
plano_conta_id
centro_custo_id
```

### metricas_one_page

Dicionario configuravel de metricas do One Page Report.

Campos:

```text
organizacao_id
codigo
nome
grupo
formula_tipo
filtro_json
ordem
ativo
```

## 4. Relacoes Principais

```text
organizacoes 1 -> N unidades
organizacoes 1 -> N pessoas
pessoas 1 -> N pessoa_perfis
pessoas 1 -> N pessoa_contatos
pessoas 1 -> N pessoa_enderecos
pessoas 1 -> N pessoa_historico
pessoas N -> N pessoas via pessoa_relacionamentos
organizacoes 1 -> N campos_personalizados
campos_personalizados 1 -> N valores_campos_personalizados
organizacoes 1 -> N import_lotes
import_lotes 1 -> N import_abas
import_lotes 1 -> N import_mapeamentos
import_lotes 1 -> N import_linhas
import_linhas 1 -> N import_pendencias
pessoas 1 -> N contribuicoes
tipos_contribuicao 1 -> N contribuicoes
pessoas 1 -> N recibos
recibos 1 -> N recibo_itens
contribuicoes 1 -> N recibo_itens
plano_contas 1 -> N lancamentos_financeiros
centros_custo 1 -> N lancamentos_financeiros
contas_financeiras 1 -> N lancamentos_financeiros
lancamentos_financeiros 1 -> N rateios_lancamento
```

## 5. Regras De Integridade V0

- Toda pessoa deve pertencer a uma organizacao.
- Toda contribuicao deve pertencer a uma organizacao.
- Contribuicoes identificadas devem possuir pessoa vinculada por regra de aplicacao.
- CPF deve ser unico por organizacao quando preenchido.
- Codigo interno de pessoa deve ser unico por organizacao quando preenchido.
- Plano de contas deve ser unico por codigo dentro da organizacao.
- Centro de custo deve ser unico por codigo dentro da organizacao.
- Recibo deve ter numero unico por organizacao.
- Campos personalizados devem ter chave unica por organizacao e tipo de registro.
- Lotes de importacao devem manter a origem dos registros criados.
- Registros nao devem ser apagados fisicamente na rotina normal; usar `ativo`, `status` ou lote desfeito.
- Pessoa com data/motivo de inatividade deve manter o perfil `membro`, mas receber status operacional `membro_inativo`.

## 6. MVP Do Banco

Para a primeira versao executavel, o minimo viavel e:

```text
organizacoes
unidades
modulos
modulos_organizacao
usuarios
usuarios_organizacoes
pessoas
pessoa_perfis
pessoa_contatos
pessoa_enderecos
pessoa_relacionamentos
pessoa_historico
campos_personalizados
valores_campos_personalizados
import_lotes
import_abas
import_mapeamentos
import_linhas
import_pendencias
plano_contas
centros_custo
contas_financeiras
tipos_contribuicao
formas_recebimento
campanhas
contribuicoes
recibos
recibo_itens
lancamentos_financeiros
rateios_lancamento
metas
metricas_one_page
```

## 7. O Que Fica Para Fase Futura

Nao entram detalhados na V0:

- folha de pagamento;
- controle de ferias;
- escala de voluntarios;
- ministerios completos;
- eventos completos;
- controle patrimonial completo;
- integracao bancaria automatica;
- assinatura digital de recibos;
- permissao granular avancada.

O banco deixa espaco para esses modulos, mas nao carrega esse peso na primeira versao.

## 8. Proximo Passo

Com este desenho aprovado, o proximo passo tecnico e criar:

```text
schema_power_church_v0.sql
```

Esse arquivo deve materializar o desenho em SQL inicial, testavel localmente.

Depois disso, o passo seguinte sera:

```text
Passo 3 - Prototipo inicial do banco e importacao de pessoas
```

# Analise Da Planilha De Membros - 2026-04-20

## 1. Arquivo Analisado

Arquivo:

```text
Gestao_de_Membresia_Membros-2026_04_20_1659.xlsx
```

Tipo:

```text
Planilha Excel com uma aba chamada Dados.
```

Volume:

```text
1 aba
47 colunas
1363 registros de pessoas
```

Observacao de privacidade:

Este relatorio registra apenas estrutura, contagens e riscos de importacao. Nao lista nomes, CPFs completos, telefones ou enderecos.

## 2. Conclusao Executiva

A planilha esta bem estruturada para iniciar uma importacao assistida, mas nao deve ser importada diretamente sem ajustes.

Ela nao e apenas um cadastro simples de pessoas. Ela contem dados de:

- identificacao civil;
- contatos;
- endereco;
- membresia;
- batismo;
- forma de entrada;
- igreja de origem;
- inatividade;
- dados profissionais e complementares.

Antes de importar, recomendo ajustar principalmente as regras do importador para tratar dados eclesiasticos sem fugir da arquitetura original.

Principal decisao:

```text
Seguir a arquitetura planejada: pessoas, pessoa_perfis, pessoa_historico e campos_personalizados.
```

O CPF permanece como referencial importante, mas nao obrigatorio e nao primario. Campos acessorios, como recem-convertido e tipo de batismo, nao devem conduzir a modelagem principal. A inatividade deve ser tratada como criterio de membro inativo sem privilegio de voto, nao como exclusao automatica.

## 3. Campos Encontrados

Colunas da planilha:

```text
Data de criacao
Numero de membro
Nome completo
Sexo
Tipo
Igreja
Status
E pastor?
Faz parte da lideranca?
E recem-convertido?
Aceitou Jesus?
Aceitou Jesus em
Data que aceitou Jesus
E-Mail
Telefone
Celular
WhatsApp?
Estado Civil
Batizado?
Tipo de batismo
Batizado por
Data de entrada
Forma de entrada
Aniversario
Data de Casamento
Data de Batismo
CPF
Documento de Identificacao
Orgao Emissor
UF do RG
Endereco
Numero
Complemento
Bairro
CEP
Cidade
UF
Criado por
Entrevistado por
Data de inatividade
Motivo de inatividade
Escolaridade
Ocupacao
Tipo sanguineo
Nacionalidade
Naturalidade
Igreja de origem
```

## 4. Cobertura Dos Dados

Campos 100% preenchidos:

```text
Data de criacao
Nome completo
Sexo
Tipo
Igreja
Status
E pastor?
Faz parte da lideranca?
E recem-convertido?
Aceitou Jesus?
Aceitou Jesus em
WhatsApp?
Batizado?
Tipo de batismo
```

Campos quase completos:

```text
Numero de membro: 1362 preenchidos, 1 vazio
Endereco: 1319 preenchidos, 44 vazios
Bairro: 1315 preenchidos, 48 vazios
CEP: 1303 preenchidos, 60 vazios
Cidade: 1301 preenchidos, 62 vazios
UF: 1346 preenchidos, 17 vazios
Celular: 1197 preenchidos, 166 vazios
CPF: 1112 preenchidos, 251 vazios
```

Campos com baixa cobertura, mas relevantes:

```text
Data que aceitou Jesus: 275 preenchidos
Data de entrada: 630 preenchidos
Data de casamento: 339 preenchidos
Data de batismo: 536 preenchidos
Batizado por: 17 preenchidos
Entrevistado por: 54 preenchidos
Data de inatividade: 15 preenchidos
Motivo de inatividade: 15 preenchidos
```

## 5. Distribuicoes Importantes

Tipo:

```text
Membro: 1363
```

Igreja:

```text
Uma unica igreja em todos os registros.
```

Status:

```text
Aprovada(o): 1363
```

Sexo:

```text
Feminino: 805
Masculino: 558
```

Pastores e lideranca:

```text
Pastores marcados: 17
Lideranca marcada: 112
```

Batismo:

```text
Batizado S: 985
Batizado N: 378
```

Forma de entrada:

```text
Batismo: 581
Transferir: 141
Aclamacao: 96
Jurisdicao: 91
Reconciliacao: 15
Vazio: 439
```

Estado civil:

```text
Casado(a): 764
Escolha unica: 410
Vazio: 68
Divorciado(a): 73
Viuvo(a): 42
Uniao Estavel: 3
Noivo(a): 3
```

Observacao:

`Escolha unica` deve ser tratado como vazio ou pendencia, nao como estado civil real.

## 6. Qualidade Dos Dados

### 6.1 CPF

Resumo:

```text
CPF preenchido: 1112
CPF vazio: 251
CPF valido: 1106
CPF invalido: 6
CPF duplicado: 1 CPF aparece em 2 registros
```

Decisao recomendada:

- permitir importacao sem CPF;
- bloquear ou revisar CPFs invalidos;
- revisar duplicidade de CPF antes de confirmar;
- manter CPF como identificador unico opcional por organizacao.

### 6.2 Numero De Membro

Resumo:

```text
1362 preenchidos
1 vazio
nenhuma duplicidade encontrada entre os preenchidos
```

Decisao recomendada:

Usar como `pessoas.codigo_interno`.

O registro sem numero de membro deve ser aceito, mas marcado para revisao.

### 6.3 Nomes

Resumo:

```text
1363 nomes preenchidos
1 nome repetido em 2 registros
```

Decisao recomendada:

Nome repetido nao deve bloquear importacao sozinho. Deve gerar alerta de possivel duplicidade, combinado com CPF, telefone, email e data de nascimento.

### 6.4 Emails E Telefones

Resumo:

```text
E-mail preenchido: 1045
E-mail com formato invalido: 12
Telefone preenchido: 850
Celular preenchido: 1197
WhatsApp marcado como S: 1248
```

Observacoes:

- existem telefones e celulares em formatos diferentes;
- alguns contatos parecem ter DDI +55;
- outros estao sem DDI;
- alguns numeros de telefone/celular se repetem, possivelmente por familias.

Decisao recomendada:

- preservar valor original;
- criar valor normalizado para busca;
- nao tratar telefone repetido como erro automatico;
- sinalizar e-mails invalidos para revisao.

### 6.5 Endereco

Resumo:

```text
CEP tem 1303 preenchidos e todos possuem 8 digitos.
741 numeros de endereco vieram com sufixo decimal, como 169.0.
```

Decisao recomendada:

Normalizar numero de endereco removendo `.0` quando for numero inteiro.

### 6.6 Datas

Campos com datas ruins:

```text
Data que aceitou Jesus: 1 valor invalido
Data de entrada: 1 valor invalido
Data de casamento: 28 valores invalidos
Data de batismo: 1 valor invalido
```

Padrao encontrado:

```text
1/1/1
```

Decisao recomendada:

Tratar `1/1/1` como data invalida/vazia e gerar pendencia de revisao.

Aniversario:

```text
1099 preenchidos
264 vazios
1 data indica idade acima de 105 anos
42 registros indicam idade abaixo de 16 anos em 2026-04-20
```

Observacao:

Menores de 16 anos podem existir, mas como todos os registros estao como `Membro`, convem revisar a regra de membresia infantil/juvenil.

## 7. Inconsistencias Eclesiasticas

### 7.1 Batismo

Inconsistencias:

```text
378 registros com Batizado = N, mas Tipo de batismo preenchido.
1 registro com Batizado = N e Data de Batismo preenchida.
450 registros com Batizado = S, mas sem Data de Batismo.
405 registros com Batizado = S, mas sem Data de Entrada.
```

Interpretacao ajustada:

O cliente usa imersao como pratica padrao. Portanto, `Tipo de batismo` deve ser considerado acessorio e nao deve gerar erro relevante quando aparecer preenchido em massa.

Decisao recomendada:

- se Batizado = N, nao criar evento historico de batismo apenas por causa do Tipo de batismo;
- se Batizado = S e nao houver data, registrar como batizado sem data conhecida;
- nao criar evento historico de batismo quando a data for vazia ou invalida;
- preservar o valor original no lote de importacao ou como campo acessorio.

### 7.2 Aceitou Jesus

Resumo:

```text
Aceitou Jesus = S em 1361 registros
Aceitou Jesus = N em 2 registros
Aceitou Jesus em = Culto em 1363 registros
Data que aceitou Jesus preenchida em 275 registros
Aceitou Jesus = S sem data em 1086 registros
```

Interpretacao:

`Aceitou Jesus em` nao e data. E um contexto/local, neste arquivo sempre `Culto`.

Decisao recomendada:

- mapear `Aceitou Jesus em` como contexto/local;
- mapear `Data que aceitou Jesus` como data;
- nao exigir data para importar o campo `Aceitou Jesus`.

### 7.3 Recem-convertido

Resumo:

```text
E recem-convertido = S em 1126 registros
E recem-convertido = N em 237 registros
```

Decisao do projeto:

Esse campo deve ser considerado acessorio. Pode ser preservado como campo personalizado ou dado complementar, mas nao deve influenciar status, relatorios principais, CRM ou regras de membresia na V0.

### 7.4 Inatividade

Resumo:

```text
15 registros com Data de inatividade
15 registros com Motivo de inatividade
Status geral continua Aprovada(o)
```

Regra de negocio informada:

Inatividade e o criterio usado pelo cliente para indicar alguem que consta como membro, mas abandonou a igreja e nao goza dos privilegios de votacao em assembleias.

Decisao recomendada atualizada:

Criar regra:

```text
Se Data de inatividade ou Motivo de inatividade estiver preenchido, manter perfil membro, mas marcar status operacional como membro_inativo ou sem_direito_voto.
```

Isso nao deve ser tratado automaticamente como arquivo morto, exclusao ou desligamento definitivo.

## 8. Tratamento Pela Arquitetura Original

A planilha mostra varios campos eclesiasticos, mas a diretriz do projeto e seguir a arquitetura planejada antes de criar novas tabelas especificas.

Portanto, nesta fase, nao criaremos uma tabela pesada apenas para membresia.

Usaremos:

```text
pessoas
pessoa_perfis
pessoa_historico
campos_personalizados
valores_campos_personalizados
```

Campos que entram na estrutura principal:

```text
Numero de membro -> pessoas.codigo_interno
Nome completo -> pessoas.nome
CPF -> pessoas.cpf, referencial importante e nao obrigatorio
Sexo -> pessoas.sexo
Estado civil -> pessoas.estado_civil
Nascimento -> pessoas.data_nascimento
Contatos -> pessoa_contatos
Endereco -> pessoa_enderecos
Tipo membro -> pessoa_perfis
Pastor -> pessoa_perfis
Lideranca -> pessoa_perfis
```

Campos que entram como historico:

```text
Data que aceitou Jesus
Data de entrada
Forma de entrada
Data de batismo
Batizado por
Data de inatividade
Motivo de inatividade
```

Campos acessorios/personalizados:

```text
E recem-convertido
Tipo de batismo
Aceitou Jesus em
Igreja de origem
Escolaridade
Ocupacao
Tipo sanguineo
Nacionalidade
Naturalidade
Criado por
Entrevistado por
Orgao emissor
UF do RG
Data de casamento
```

Observacao:

Se no futuro esses campos acessorios se tornarem centrais para relatorios, eles podem ser promovidos para estrutura oficial.

## 9. Mapeamento Inicial Proposto

```text
Numero de membro -> pessoas.codigo_interno
Nome completo -> pessoas.nome
Sexo -> pessoas.sexo
Tipo -> pessoa_perfis
Igreja -> organizacao/unidade de origem
Status -> pessoas.status, com regra especial para inatividade
E pastor? -> pessoa_perfis pastor quando S
Faz parte da lideranca? -> pessoa_perfis lider quando S
E recem-convertido? -> campo personalizado acessorio
Aceitou Jesus? -> campo personalizado ou historico quando houver data
Aceitou Jesus em -> campo personalizado acessorio
Data que aceitou Jesus -> pessoa_historico quando houver data valida
E-Mail -> pessoa_contatos
Telefone -> pessoa_contatos
Celular -> pessoa_contatos
WhatsApp? -> marcar celular como WhatsApp quando aplicavel
Estado Civil -> pessoas.estado_civil
Batizado? -> pessoa_historico quando houver data valida ou campo personalizado quando sem data
Tipo de batismo -> campo personalizado acessorio
Batizado por -> pessoa_historico quando houver evento de batismo
Data de entrada -> pessoa_historico quando houver data valida
Forma de entrada -> pessoa_historico ou campo personalizado acessorio
Aniversario -> pessoas.data_nascimento
Data de Casamento -> campo civil/complementar
Data de Batismo -> pessoa_historico quando houver data valida
CPF -> pessoas.cpf
Documento de Identificacao -> pessoas.rg ou pessoa_documentos
Orgao Emissor -> documento/campo oficial
UF do RG -> documento/campo oficial
Endereco, Numero, Complemento, Bairro, CEP, Cidade, UF -> pessoa_enderecos
Criado por -> origem/importacao ou campo personalizado
Entrevistado por -> campo personalizado
Data de inatividade -> pessoas.status + pessoa_historico
Motivo de inatividade -> pessoa_historico
Escolaridade -> campo oficial complementar ou personalizado
Ocupacao -> campo oficial complementar ou personalizado
Tipo sanguineo -> campo oficial complementar
Nacionalidade -> campo oficial complementar
Naturalidade -> campo oficial complementar
Igreja de origem -> campo personalizado ou historico de entrada
```

## 10. O Que Fazer Antes De Importar

Recomendacao:

Nao importar ainda diretamente para o banco V0 atual.

Antes, fazer:

```text
1. Manter CPF como referencial importante, nao obrigatorio e nao primario.
2. Decidir onde guardar RG, orgao emissor e UF do RG.
3. Ajustar pessoa_contatos para preservar valor original e valor normalizado.
4. Definir regras de normalizacao de datas invalidas como 1/1/1.
5. Definir regra para Estado Civil = Escolha unica.
6. Tratar Tipo de batismo como acessorio, pois o cliente usa imersao como padrao.
7. Definir regra para membro inativo sem direito a voto quando houver Data/Motivo de inatividade.
8. Criar auditoria de CPF invalido e CPF duplicado.
9. Criar revisao para idade suspeita.
10. Importar E recem-convertido como campo acessorio, sem efeito operacional na V0.
```

## 11. Perguntas Para O Cliente

Antes da importacao definitiva, perguntar:

```text
1. O campo Status = Aprovada(o) significa membro registrado?
2. Estado Civil = Escolha unica deve ser tratado como vazio?
3. A igreja deseja controlar menores/criancas como membros no mesmo cadastro?
4. O numero de membro e identificador oficial?
5. Existe outra planilha com conjuge, filhos ou vinculos familiares?
6. A inatividade sempre retira direito a voto em assembleia?
```

## 12. Veredito

A planilha e boa e importavel. Com as regras de negocio esclarecidas, o Power Church pode seguir a arquitetura original, sem criar tabela pesada de membresia nesta fase.

```text
Usar pessoas + perfis + historico + campos personalizados, com regra especial para membro inativo sem direito a voto.
```

Depois disso, podemos criar uma importacao assistida segura, com:

- registros importados;
- pendencias de CPF;
- pendencias de datas;
- normalizacao de contatos;
- preservacao dos dados originais;
- revisao de inconsistencias;
- possibilidade de desfazer o lote.

Essa analise confirma que o caminho modular escolhido esta correto.

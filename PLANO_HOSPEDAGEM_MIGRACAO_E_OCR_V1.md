# Plano De Hospedagem, Migracao E OCR V1

## 1. Objetivo

Este documento registra as decisoes atuais sobre:

- onde o Power Church pode rodar durante o desenvolvimento;
- como o sistema deve evoluir de um prototipo local em Mac para ambiente Linux/nuvem;
- qual modelo de hospedagem e mais seguro para os primeiros clientes;
- como tratar o requisito futuro de OCR para envelopes manuscritos em portugues;
- qual a ordem correta entre auditoria, estabilizacao e migracao.

## 2. Decisao Atual

### 2.1 Continuar no Mac por enquanto

Nesta fase, o sistema pode continuar rodando no Mac do desenvolvimento.

Motivos:

- ainda estamos consolidando regras de negocio;
- os parsers bancarios ainda estao sendo validados com documentos reais;
- o custo de mudar de ambiente agora pode atrapalhar a estabilizacao;
- ainda existem fluxos em homologacao funcional.

Regra pratica:

- desenvolvimento e homologacao inicial podem continuar localmente;
- toda nova etapa deve evitar dependencias adicionais exclusivas de macOS.

### 2.3 Pacote de instalacao preparado

Mesmo mantendo o desenvolvimento no Mac, o projeto passa a manter um pacote de instalacao em `deploy/`.

Objetivo:

- evitar instalacoes artesanais no servidor de cada cliente;
- garantir que programas acessorios sejam instalados antes da migracao;
- validar ambiente local e servidor com script;
- permitir que cada cliente receba uma instancia padronizada.

Arquivos principais:

- `deploy/README_INSTALACAO.md`;
- `deploy/requirements/base.txt`;
- `deploy/requirements/ocr.txt`;
- `deploy/system/ubuntu-24.04.txt`;
- `deploy/install_local_mac.sh`;
- `deploy/install_ubuntu_server.sh`;
- `scripts/verificar_dependencias_servidor.py`.

Regra:

- no Mac, `swift_pdfkit` continua sendo o provedor homologado;
- em servidor Linux, o alvo sera `pymupdf`;
- a troca so deve ocorrer depois das fixtures bancarias baterem.

### 2.2 Auditoria vem antes da migracao

Antes de iniciar a migracao estrutural para nuvem ou framework novo, o sistema precisa passar por uma fase de auditoria/homologacao.

Ordem correta:

1. auditoria funcional;
2. auditoria financeira e de importacoes;
3. estabilizacao dos fluxos principais;
4. portabilidade;
5. migracao de infraestrutura;
6. evolucao para multi-cliente mais robusta.

## 3. Estado Atual Que Prende O Projeto Ao Mac

Hoje os principais pontos de dependencia local sao:

- `SQLite` como banco principal do prototipo;
- servidor HTTP proprio em Python;
- extracao de PDF bancario via `Swift` + `PDFKit`.

Esses pontos funcionam bem para descoberta e validacao local, mas precisam evoluir para:

- `PostgreSQL`;
- servico web mais robusto;
- extracao de PDF portavel para Linux;
- empacotamento em `Docker`.

## 4. Estrategia De Hospedagem

### 4.1 Fase atual

Rodar localmente no Mac durante a fase de auditoria e estabilizacao.

### 4.2 Fase seguinte

Criar um ambiente de `staging` na nuvem para:

- demonstracoes;
- teste com mais de um operador;
- validacao de backup e recuperacao;
- ensaio de deploy padrao.

### 4.3 Producao inicial recomendada

Adotar `single-tenant` por cliente.

Isto significa:

- uma instancia por igreja/cliente;
- banco separado por cliente;
- backup separado;
- menor risco de mistura de dados;
- suporte e restauracao mais simples.

### 4.4 Multi-tenant

Nao e a recomendacao para a primeira fase operacional.

So vale iniciar multi-tenant quando ja existirem:

- autenticacao robusta;
- perfis e permissoes maduros;
- trilha de auditoria por operador;
- estrategia clara de migracoes;
- monitoramento;
- rotina de backup e restore testada;
- regras bem fechadas para isolamento de dados.

## 5. Provedores E Infraestrutura

### 5.1 Recomendacao pratica

No curto prazo:

- `Linux` em nuvem, mesmo que o cliente tenha servidor Windows;
- aplicacao empacotada e padronizada;
- banco `PostgreSQL`;
- armazenamento separado para anexos e PDFs.

### 5.2 Modelo preferido

Para clientes reais, a preferencia atual e:

- instancia Linux dedicada por cliente;
- nao depender do servidor Windows do cliente;
- evitar adaptar manualmente instalacao a cada ambiente local.

## 6. Caminho Tecnico De Migracao

### 6.1 O que deve ser preservado

O trabalho atual nao deve ser descartado.

Precisam ser preservados:

- regras de negocio de contribuicoes;
- motor de importacoes bancarias;
- regras de centavos;
- contribuinte auxiliar;
- fluxos de saneamento;
- regras de mesma titularidade/origem interna;
- scripts de auditoria;
- lotes e trilha de auditoria.

### 6.2 O que precisa evoluir

Passos tecnicos previstos:

1. estabilizar o sistema atual;
2. criar testes de homologacao;
3. substituir a dependencia de `Swift/PDFKit` por extracao portavel;
4. migrar `SQLite` para `PostgreSQL`;
5. empacotar a aplicacao em `Docker`;
6. publicar em ambiente Linux;
7. avaliar framework mais robusto para longo prazo.

## 7. Framework E Plataforma Alvo

### 7.1 Direcao preferida

Direcao atual recomendada:

- `Django` como espinha dorsal futura;
- `PostgreSQL` como banco de producao;
- `Docker` como padrao de empacotamento;
- modulo proprio de importacoes bancarias mantido pelo projeto.

### 7.2 Regra de transicao

Nao fazer migracao em big bang.

Migracao recomendada:

1. homologar o que existe;
2. estabilizar modelos e regras;
3. desacoplar partes dependentes do Mac;
4. migrar gradualmente para a nova base.

## 8. Open Source De Apoio

### 8.1 ChurchCRM

`ChurchCRM` e considerado uma boa referencia funcional para:

- secretaria;
- rol;
- familias;
- doacoes basicas;
- grupos;
- eventos;
- relatorios administrativos.

Mas a recomendacao atual e:

- nao usar ChurchCRM como nucleo do Power Church;
- usar apenas como referencia funcional ou inspiracao de modelagem.

Motivo:

- o diferencial do Power Church esta no motor proprio de importacoes, saneamento e associacao bancaria;
- integrar profundamente um ChMS generico ao nucleo atual tende a custar mais do que aproveitar ideias dele.

### 8.2 Outras referencias

Podem servir de referencia futura:

- `ERPNext` para financeiro, CRM, permissoes e operacao empresarial;
- `CiviCRM` para membership, contribuicoes e relacionamento;
- `Dolibarr` como ERP/CRM modular simples;
- `SuiteCRM` como referencia de CRM puro.

Regra:

- modulos open source servem como apoio e benchmark;
- o motor diferencial do Power Church deve permanecer proprio.

## 9. OCR De Envelopes Manuscritos

### 9.1 Requisito reconhecido

Uma parte relevante das contribuicoes ocorre em `cash`, por meio de envelopes preenchidos a mao.

Essa entrada nao deve ficar fora do plano de recebimentos.

### 9.2 Objetivo futuro

Criar um fluxo de leitura assistida de envelopes manuscritos em portugues para:

- nome do contribuinte;
- valor;
- competencia;
- destinacao;
- observacoes e marcacoes do envelope.

### 9.3 Regra de implementacao

O OCR manuscrito nao deve entrar antes da fase de estabilizacao dos recebimentos bancarios.

Ordem sugerida:

1. consolidar PIX e extratos bancarios;
2. homologar contribuicoes e associacoes;
3. concluir a migracao operacional segura para Django;
4. definir fluxo de envelopes em especie;
5. avaliar OCR manuscrito com revisao humana obrigatoria;
6. integrar isso ao mesmo motor de contribuicoes e auditoria;
7. somente depois definir perfis de usuarios e privilegios finos com o cliente.

### 9.4 Principio importante

OCR manuscrito deve ser sempre:

- assistido;
- auditavel;
- com revisao humana;
- sem lancamento financeiro cego.

## 10. Decisao Operacional Atual

Resumo do que vale agora:

- continuar desenvolvendo localmente no Mac;
- iniciar auditoria/homologacao antes de migrar infraestrutura;
- preparar o codigo para portabilidade futura;
- planejar `single-tenant` por cliente como primeira producao;
- adiar multi-tenant para uma fase mais madura;
- tratar OCR manuscrito como proxima grande fase funcional dos recebimentos em especie depois da migracao Django segura;
- adiar usuarios/permissoes finas ate o sistema operacional completo estar claro para o cliente.

## 11. Proximos Passos Recomendados

1. concluir a paridade operacional do Django;
2. manter a bateria total verde com `scripts/verificar_funcionalidade_total.py --report`;
3. validar portabilidade de PDF com `PyMuPDF` sem bloquear o desenvolvimento no Mac;
4. iniciar laboratorio de OCR com envelopes reais ou anonimizados;
5. criar entrada assistida de envelopes com revisao humana obrigatoria;
6. integrar envelopes ao motor de contribuicoes, designacoes e auditoria;
7. depois da operacao completa, definir usuarios/permissoes com o cliente;
8. planejar o primeiro ambiente Linux de `staging`.

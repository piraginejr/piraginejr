# Plano De Transicao Power Church V1

## 1. Objetivo

Iniciar a passagem do prototipo local atual para uma plataforma profissional, portavel e preparada para nuvem, sem perder o que ja foi homologado.

A transicao nao deve ser feita em um unico salto. O sistema atual ja contem regras sensiveis de negocio, importacoes bancarias, saneamento, auditoria e relatorios. O caminho correto e preservar o funcionamento atual e, aos poucos, separar nucleo de negocio, web, banco e infraestrutura.

## 2. Estado Atual

Base atual em operacao local:

- aplicacao principal em `power_church_demo.py`;
- banco `SQLite` em `data/power_church_membros_importado.db`;
- importacao de pessoas, PIX, extratos Sicoob, Bradesco e Santander;
- contribuinte auxiliar;
- associacao a pessoa;
- regras de centavos;
- saneamento de lote;
- central de contribuintes;
- relatorios e PDFs;
- script de verificacao de estabilidade.

Pontos fortes atuais:

- regras de negocio ja validadas com dados reais;
- fluxo financeiro preserva os lancamentos mesmo sem pessoa vinculada;
- auditoria de importacoes bancarias e de pessoas ja com checks de regressao;
- parsers bancarios com historico de ajustes reais.

Pontos que exigem transicao:

- arquivo principal muito grande;
- dependencia de `Swift/PDFKit` para ler PDF;
- `SQLite` local ja grande para prototipo;
- ausencia de camada web profissional com autenticacao e permissoes completas;
- necessidade futura de OCR, multiusuario, backup e nuvem.

## 3. Principio De Transicao

Regra central:

> Nao migrar em big bang.

Cada fase precisa:

- manter o sistema atual demonstravel;
- passar no script de estabilidade;
- ter backup antes de mudancas estruturais;
- preservar trilha de auditoria;
- evitar retrabalho nos parsers ja homologados.

## 4. Arquitetura Alvo

Direcao recomendada:

- `Django` para web, autenticacao, permissoes, admin e formularios;
- `PostgreSQL` como banco de producao;
- `Docker` para empacotamento;
- `Linux` em nuvem como ambiente padrao;
- modulo proprio de importacoes bancarias;
- modulo proprio de OCR de envelopes no futuro.

Modelo de hospedagem inicial:

- `single-tenant` por cliente;
- uma instancia e um banco por igreja;
- backup separado;
- menor risco operacional na primeira fase.

## 5. Fases

### Fase 0: Congelar O Que Esta Bom

Objetivo:

- manter a versao atual como base de demonstracao e comparacao.

Entregaveis:

- script `scripts/verificar_estabilidade_demo.py` sempre verde;
- relatorio em `data/homologacao`;
- backups antes de qualquer migracao;
- documentos de arquitetura atualizados.

Criterio de saida:

- estabilidade completa `OK`;
- nenhuma ficha complementar sem nome;
- auditoria de importacao de pessoas visivel;
- fila de associacoes por novos cadastros visivel;
- parsers atuais abrindo os lotes ja importados.

### Fase 1: Separar Nucleo De Negocio Sem Trocar A Tela

Objetivo:

- reduzir o risco do arquivo monolitico extraindo regras para modulos reaproveitaveis.

Primeiros modulos a extrair:

- normalizacao de nomes e documentos;
- motor de match pessoa/contribuinte;
- regras de centavos;
- contrato de movimento bancario;
- importador de pessoas;
- geracao de relatorios.

Regra:

- a interface atual continua chamando os mesmos fluxos;
- cada extracao precisa passar no script de estabilidade.

Criterio de saida:

- nucleo de negocio importavel sem servidor HTTP;
- testes/checagens cobrindo os fluxos extraidos;
- comportamento igual ao prototipo.

### Fase 2: Criar Camada Portavel De PDF

Objetivo:

- deixar de depender diretamente de `Swift/PDFKit`.

Estrutura recomendada:

- criar um adaptador `PdfTextExtractor`;
- manter `Swift/PDFKit` como implementacao temporaria no Mac;
- adicionar implementacao Linux com biblioteca portavel;
- comparar saidas por documento real antes de trocar o default.

Candidatos tecnicos:

- `pypdfium2` para renderizacao/inspecao;
- `pymupdf` ou ferramenta equivalente para texto e coordenadas;
- OCR separado quando o PDF for imagem ou envelope manuscrito.

Criterio de saida:

- cada PDF de homologacao gera os mesmos movimentos ou diferencas auditadas;
- Bradesco, Sicoob e Santander sem perda de linha em quebra de pagina;
- scripts de homologacao atualizados.

### Fase 3: Criar Projeto Django Em Paralelo

Objetivo:

- iniciar o Django sem abandonar a aplicacao atual.

Apps iniciais:

- `accounts`: usuarios, perfis e permissoes;
- `people`: pessoas, familias, contatos, historico;
- `contributions`: contribuicoes, tipos, campanhas, centavos;
- `imports`: lotes, movimentos bancarios, saneamento;
- `audit`: trilha de auditoria;
- `reports`: relatorios e PDFs.

Regra:

- o Django nasce em paralelo;
- primeiro consome o nucleo de negocio separado;
- depois substitui telas uma a uma.

Criterio de saida:

- login funcional;
- admin basico;
- leitura do banco migrado em ambiente de teste;
- primeira tela de consulta sem alterar dados reais.

### Fase 4: Migrar Banco Para PostgreSQL

Objetivo:

- transformar o modelo atual em banco de producao.

Passos:

- mapear tabelas atuais para modelos Django;
- criar script de exportacao do SQLite;
- importar em PostgreSQL de staging;
- validar totais financeiros, lotes, contribuintes e pessoas;
- comparar relatorios antes/depois.

Criterio de saida:

- totais identicos entre SQLite e PostgreSQL;
- contribuicoes por periodo batendo;
- lotes bancarios rastreaveis;
- auditoria preservada.

### Fase 5: Docker E Staging

Objetivo:

- colocar ambiente demonstravel em nuvem.

Entregaveis:

- `Dockerfile`;
- `docker-compose` com app, banco e armazenamento;
- rotina de backup;
- restore testado;
- URL de staging por cliente piloto.

Criterio de saida:

- cliente acessa em navegador;
- mais de um operador consegue usar;
- backup e restore testados.

### Fase 6: OCR De Envelopes

Objetivo:

- tratar contribuicoes em cash por envelope manuscrito.

Regra:

- OCR nao entra misturado ao parser bancario;
- envelope vira uma origem propria de remessa;
- operador valida nome, valor, data e designacoes;
- a soma das designacoes precisa bater com o total da remessa.

## 6. Ordem Pratica A Partir De Agora

Proximo ciclo recomendado:

1. manter a versao atual demonstravel;
2. rodar estabilidade antes e depois de cada mudanca;
3. extrair o nucleo de normalizacao/match;
4. criar contrato comum de movimento bancario;
5. criar adaptador de PDF portavel;
6. so depois iniciar o projeto Django paralelo.

## 7. O Que Nao Fazer Agora

Evitar neste momento:

- substituir tudo por Django de uma vez;
- migrar diretamente para nuvem sem estabilizar PDF e banco;
- criar novo banco bancario antes de separar contrato de parser;
- iniciar OCR antes de fechar o fluxo de remessas/designacoes;
- mexer em dados financeiros sem backup e script de conferencia.

## 8. Checks Obrigatorios

Antes de qualquer etapa estrutural, rodar:

```bash
python3 scripts/verificar_estabilidade_demo.py --report
python3 scripts/verificar_prontidao_transicao.py --report
```

Se qualquer check critico falhar, a transicao deve pausar ate corrigir.

## 9. Progresso Da Fase 1

Primeiro corte iniciado:

- criado pacote `power_church_core`;
- criado modulo `power_church_core/banking.py`;
- criado modulo `power_church_core/contributors.py`;
- criado modulo `power_church_core/designations.py`;
- criado modulo `power_church_core/formatting.py`;
- criado modulo `power_church_core/normalization.py`;
- criado modulo `power_church_core/matching.py`;
- criado modulo `power_church_core/pdf_text.py`;
- criado modulo `power_church_core/signatures.py`;
- criado contrato `BankMovement` para movimentos bancarios normalizados;
- o app atual passou a delegar funcoes puras de normalizacao e documentos para o nucleo;
- o app atual passou a delegar datas, moeda e competencia para o nucleo;
- o app atual passou a delegar o motor de associacao e sugestoes para o nucleo;
- o app atual passou a delegar tipo/siglas de contribuintes para o nucleo;
- o app atual passou a delegar regras puras de centavos/destinacoes para o nucleo;
- o app atual passou a delegar assinaturas de duplicidade para o nucleo;
- o app atual passou a delegar funcoes puras de layout/origem bancaria para o nucleo;
- o app atual passou a delegar a extracao de PDF para um adaptador isolado;
- criado script `scripts/verificar_funcionalidade_transicao.py` para homologar os nucleos migrados;
- criado script `scripts/verificar_extratores_pdf.py` para validar provedores de PDF;
- criado script `scripts/verificar_fixtures_pdf_bancos.py` para congelar a linha de base dos PDFs reais por banco;
- criado script `scripts/verificar_funcionalidade_total.py` para rodar a bateria completa em um comando;
- criado pacote `deploy/` com requirements, pacotes de sistema e scripts de instalacao local/servidor;
- criado script `scripts/verificar_dependencias_servidor.py` para validar ambiente antes de migrar cliente;
- a verificacao de prontidao passou a checar a existencia do nucleo e o consumo pelo app.

Funcoes ja cobertas pelo nucleo:

- `moneyless_int`;
- `normalize_query`;
- `normalize_match_name`;
- `format_cpf`;
- `clean_cpf`;
- `cleaned_document_token`;
- `document_query_matches`;
- `masked_document_matches`;
- `santander_document_type`;
- `pix_code_from_amount`.

Funcoes de formatacao ja cobertas pelo nucleo:

- `br_date`;
- `br_datetime`;
- `br_money`;
- `parse_money`;
- `competencia_from_date`.

Funcoes de associacao ja cobertas pelo nucleo:

- `derived_pix_name_aliases`;
- `pix_name_is_expanded_variant`;
- `pix_name_has_company_hint`;
- `pix_origin_is_company`;
- `active_status_allows_auto_match`;
- `match_pix_entry`;
- `pix_candidate_suggestions`.

Funcoes de destinacao ja cobertas pelo nucleo:

- `cent_rule_digits`;
- `cent_rule_plan_account_code`;
- `cent_rule_type_code`;
- `cent_rule_type_is_system_managed`;
- `suggested_type_for_cent_rule`.

Funcoes de contribuintes ja cobertas pelo nucleo:

- `looks_like_cnpj`;
- `contributor_kind_for_identity`;
- `contributor_membership_sigla`;
- `contributor_membership_legend`.

Funcoes de assinaturas ja cobertas pelo nucleo:

- `signature_component`;
- `pix_global_signature`;
- `statement_global_signature`.

Funcoes bancarias ja cobertas pelo nucleo:

- `BankMovement`;
- `statement_layout_label`;
- `statement_layout_contributor_source`;
- `statement_layout_is_santander`;
- `statement_layout_is_supported`;
- `santander_document_display`;
- `statement_document_identity_label`;
- `statement_contributor_name_for_identity`.
- `statement_is_same_organization_origin`;
- `statement_same_organization_review_note`.

Adaptador de PDF iniciado:

- `extract_pdf_pages`;
- `extract_pdf_line_selections`;
- arquitetura de provedores criada com `swift_pdfkit` e `pymupdf`;
- implementacao homologada atual ainda usa `Swift/PDFKit`, mas esta isolada em `power_church_core/pdf_text.py`;
- `PyMuPDF` ainda nao esta instalado neste Mac, mas o provedor ja esta preparado para comparacao quando a dependencia entrar.
- fixtures atuais cobrem PIX Sicoob, extrato Sicoob, Bradesco e Santander.
- Bradesco janeiro possui alerta operacional esperado: parser atual encontra remessas internas de mesma titularidade que nao afetam o financeiro de contribuicoes do lote historico.

Proximo corte recomendado:

- criar fixtures de homologacao por banco;
- comparar a saida textual dos PDFs atuais com uma implementacao portavel;
- so trocar o default de PDF depois que Bradesco, Sicoob e Santander baterem nos documentos reais.

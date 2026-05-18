# Pesquisa De Ferramentas Django V1

Data da pesquisa: 2026-05-09

Status em 2026-05-09:

- `django-import-export` incorporado ao pacote Django e usado na exportacao CSV/XLSX de pessoas.
- `django-auditlog` incorporado ao Django, com tabela propria de eventos Django e ponte best-effort para escritas controladas no legado.
- `openpyxl` incorporado para exportacao XLSX.
- Pacotes de fundacao ativados com migracoes/verificacao: `django-filter`, `django-tables2`, `django-formtools`, `django-guardian`, `django-waffle`, `django-money`, `django-crispy-forms`, `crispy-bootstrap5`, `django-anymail`.
- Pacotes preparados para prova futura, sem assumir fluxo operacional: `django-allauth`, `django-rq`, `django-weasyprint`, `django-unfold`.
- `django-weasyprint` fica em alerta no Mac enquanto faltarem bibliotecas nativas Pango/GObject; o pacote de servidor Ubuntu ja lista dependencias de sistema.

Objetivo: identificar ferramentas prontas que possam reduzir horas de desenvolvimento no Power Church, sem perder controle sobre regras sensiveis de igreja, contribuicoes, auditoria e financeiro.

## Conclusao Executiva

Nao recomendo trocar o sistema atual por um produto pronto. O diferencial que estamos construindo esta nos parsers bancarios, auditoria de contribuicoes, associacao a rol/frequentadores/contribuintes auxiliares, regras de centavos, remessas internas e futura leitura de envelopes. Isso e muito especifico.

Recomendo usar ferramentas Django em camadas:

- adotar bibliotecas maduras para infraestrutura repetitiva;
- manter nosso dominio proprio para membros, contribuicoes, importacoes bancarias, associacoes e relatorios eclesiasticos;
- usar sistemas prontos de igreja, como ChurchCRM, apenas como referencia funcional, pois nao sao Django.

## Prioridade 1: Vale Aproveitar

### 1. Admin E Backoffice

Ferramentas:

- Django Admin nativo
- django-unfold ou django-jazzmin

Uso recomendado:

- painel administrativo interno para tabelas, usuarios, permissoes, regras, configuracoes e auditorias tecnicas;
- nao usar como tela principal do operador financeiro, pois o proprio Django alerta que o admin e melhor para gestao interna baseada em tabelas, nao para fluxos operacionais processuais.

Decisao sugerida:

- usar Django Admin como painel de administracao;
- avaliar `django-unfold` primeiro, pois e moderno, incremental e baseado no `django.contrib.admin`;
- manter nossas telas operacionais proprias para importacao, saneamento, ficha de pessoa, contribuicoes e relatorios.

Fontes:

- https://docs.djangoproject.com/en/stable/ref/contrib/admin/
- https://pypi.org/project/django-unfold/
- https://farridav.github.io/django-jazzmin/

### 2. Importacao E Exportacao De Dados

Ferramenta:

- django-import-export

Uso recomendado:

- importacao complementar de pessoas;
- preview antes de importar;
- exportacao CSV/XLSX de pessoas, contribuintes, relatorios e auditorias;
- importacao administrativa de tabelas auxiliares.

Decisao sugerida:

- forte candidato para substituir parte do codigo manual de importacao/exportacao de planilhas;
- nao substituir nossos parsers de PDF bancario, que continuam especializados.

Fontes:

- https://django-import-export.readthedocs.io/
- https://pypi.org/project/django-import-export/

### 3. Tabelas, Filtros E Exportacao Operacional

Ferramentas:

- django-filter
- django-tables2

Uso recomendado:

- listas grandes de pessoas, contribuintes, contribuicoes, lotes, movimentos, relatorios estrategicos;
- filtros por tag, periodo, pessoa, status, destino e banco;
- exportacao de tabelas em CSV/XLSX quando nao for PDF oficial.

Decisao sugerida:

- bom candidato para padronizar as listas do Django e reduzir codigo repetitivo;
- testar primeiro em uma tela de baixo risco, por exemplo central de contribuintes ou auditoria.

Fontes:

- https://django-tables2.readthedocs.io/en/latest/pages/filtering.html
- https://django-tables2.readthedocs.io/en/latest/pages/export.html

### 4. Auditoria E Historico De Alteracoes

Ferramentas:

- django-auditlog
- django-simple-history
- django-reversion

Uso recomendado:

- registrar quem alterou pessoa, contribuicao, associacao, regra de centavos, destino financeiro e rateio;
- preservar historico com usuario, data, antes/depois e justificativa;
- permitir reversao em areas sensiveis quando fizer sentido.

Decisao sugerida:

- `django-auditlog` para trilha simples de alteracoes;
- `django-simple-history` para historico completo de modelos importantes;
- `django-reversion` apenas se precisarmos de rollback/recovery pelo admin.

Fontes:

- https://django-auditlog.readthedocs.io/
- https://django-simple-history.readthedocs.io/en/stable/quick_start.html
- https://django-reversion.readthedocs.io/

### 5. Permissoes Finas

Ferramenta:

- django-guardian

Uso recomendado:

- permissoes por objeto, por exemplo igreja/cliente, lote, relatorio, area financeira, ficha sensivel;
- separar o que cada operador pode ver, editar, imprimir, exportar e auditar.

Decisao sugerida:

- usar depois que o cliente definir perfis reais;
- manter agora grupos Django simples, para nao criar permissoes no chute.

Fonte:

- https://django-guardian.readthedocs.io/

### 6. Formularios Em Etapas

Ferramenta:

- django-formtools

Uso recomendado:

- importacao guiada de banco novo;
- criacao de pessoa/frequentador em etapas;
- OCR de envelope com revisao: imagem, campos detectados, rateio, confirmacao final.

Decisao sugerida:

- muito util para o futuro OCR e para fluxos com conferencia humana.

Fonte:

- https://django-formtools.readthedocs.io/en/latest/wizard.html

### 7. PDFs E Relatorios Oficiais

Ferramentas:

- WeasyPrint
- django-weasyprint

Uso recomendado:

- relatorios oficiais, recibos, extratos, listas por periodo, relatorios por destino e documentos para impressao;
- gerar PDF a partir de HTML/CSS, com layout mais facil de manter do que desenho manual em canvas.

Decisao sugerida:

- forte candidato para migrar gradualmente os PDFs oficiais;
- manter os PDFs atuais ate haver paridade visual.

Fontes:

- https://weasyprint.org/
- https://pypi.org/project/django-weasyprint/

## Prioridade 2: Avaliar Com Cuidado

### 8. Financeiro / Contabilidade

Ferramentas:

- django-ledger
- django-money

Uso recomendado:

- `django-money` pode padronizar campos monetarios;
- `django-ledger` pode ser avaliado para contabilidade de dupla entrada, plano de contas, lancamentos e demonstrativos.

Risco:

- django-ledger e poderoso, mas pode impor modelo contabil mais complexo do que a fase atual precisa;
- nossas remessas/designacoes/doacoes precisam continuar no dominio Power Church;
- integrar contabilidade cedo demais pode travar a evolucao.

Decisao sugerida:

- usar `django-money` com baixa resistencia;
- fazer uma prova isolada com `django-ledger` antes de acoplar ao sistema.

Fontes:

- https://django-ledger.readthedocs.io/en/latest/README.html
- https://pypi.org/project/django-ledger/
- https://django-money.readthedocs.io/

### 9. Workflows E Processos

Ferramentas:

- django-viewflow
- django-waffle

Uso recomendado:

- Viewflow para fluxos formais: importacao, auditoria, aprovacao financeira, encerramento, OCR;
- Waffle para ativar funcionalidades novas por usuario/grupo durante homologacao.

Risco:

- Viewflow pode ser grande demais agora;
- Waffle e simples e pode ajudar muito na transicao.

Decisao sugerida:

- adotar `django-waffle` quando comecarmos a liberar recursos por cliente/operador;
- deixar `django-viewflow` para depois, se os fluxos de aprovacao ficarem complexos.

Fontes:

- https://django-viewflow.readthedocs.io/en/latest/index.html
- https://waffle.readthedocs.io/

### 10. Autenticacao, MFA E Email

Ferramentas:

- django-allauth
- django-anymail

Uso recomendado:

- `django-allauth`: login, recuperacao de senha, MFA, passkeys/social login se o cliente precisar;
- `django-anymail`: envio de emails por provedores diferentes sem prender o codigo a um fornecedor.

Decisao sugerida:

- adotar `django-allauth` quando fecharmos o modulo de usuarios/perfis;
- adotar `django-anymail` quando comecarem comunicacoes por email, recibos enviados ou notificacoes.

Fontes:

- https://allauth.org/
- https://docs.allauth.org/en/latest/
- https://anymail.dev/

### 11. Tarefas Em Segundo Plano

Ferramentas:

- django-rq
- Celery
- Django Tasks, quando migrarmos para Django 6

Uso recomendado:

- OCR de envelopes;
- importacoes grandes;
- geracao de PDFs pesados;
- rotinas de auditoria e reconciliacao.

Decisao sugerida:

- para nosso porte inicial, `django-rq` tende a ser mais simples que Celery;
- no futuro Django 6, avaliar o framework nativo de Tasks, lembrando que ele define a API, mas ainda precisa de um executor externo.

Fontes:

- https://docs.djangoproject.com/en/dev/topics/tasks/
- https://djangocfg.com/docs/features/integrations/django-rq/overview/

## Prioridade 3: Servem Mais Como Referencia Funcional

### 12. Sistemas Prontos De Igreja

Ferramentas/produtos:

- ChurchCRM
- ChurchCMS
- Django Church

Analise:

- ChurchCRM e bem alinhado ao dominio de igreja: membros, familias, grupos, eventos, presenca, doacoes e relatorios;
- porem ChurchCRM e LAMP/PHP, nao Django;
- ChurchCMS tambem nao e Django, usa Laravel/Vue;
- Django Church parece mais voltado a site/CMS de igreja do que gestao administrativa completa.

Decisao sugerida:

- nao usar como base tecnica;
- estudar telas e funcionalidades para comparar escopo: familias, grupos, eventos, frequencia, voluntarios, comunicacao, relatorios e portal do membro.

Fontes:

- https://churchcrm.io/
- https://churchcrm.io/church-management-software/
- https://github.com/ChurchCRM/CRM
- https://churchcms.app/
- https://www.djangochurch.org/

## OCR De Envelopes Manuscritos

Ferramentas candidatas:

- Tesseract: bom para impresso e ja instalado localmente, mas geralmente fraco para manuscrito;
- PaddleOCR: forte em documentos e possui suporte recente a reconhecimento de escrita manual;
- TrOCR: bom candidato para manuscrito, baseado em Transformers, com possibilidade de fine-tuning;
- Kraken: treinavel e forte para OCR especializado, mas mais comum em documentos historicos/linhas manuscritas.

Decisao sugerida:

- manter Tesseract para impresso e pre-processamento simples;
- testar PaddleOCR e TrOCR em um conjunto real de envelopes;
- criar fluxo sempre com conferencia humana, pois envelope manuscrito nao deve gerar lancamento financeiro sem validacao.

Fontes:

- https://www.paddleocr.ai/v3.3.1/en/index.html
- https://huggingface.co/docs/transformers/en/model_doc/trocr
- https://kraken.re/6.0.0/index.html

## Caminho Pratico Recomendado

### Etapa A: Ganhos Rapidos Sem Risco Alto

1. Instalar e testar `django-import-export` em importacao/exportacao de pessoas. Status: iniciado com exportacao CSV/XLSX de pessoas.
2. Testar `django-tables2` + `django-filter` em uma lista operacional. Status: pacotes instalados e ativos; falta aplicar em uma lista real.
3. Implantar `django-auditlog` ou `django-simple-history` em contribuicoes e pessoas. Status: `django-auditlog` instalado e eventos Django iniciados.
4. Avaliar `django-unfold` no admin, sem mexer nas telas operacionais.

5. Preparar `django-formtools`, `django-guardian`, `django-waffle`, `django-money`, `crispy-forms` e `anymail` como base segura. Status: instalados/ativos; uso funcional sera feito por modulo.

### Etapa B: Ganhos Para Operacao Financeira

1. Prova de conceito com `django-money`.
2. Prova isolada de `django-ledger`, sem acoplar ao banco principal.
3. Migrar PDFs oficiais gradualmente para WeasyPrint, se a qualidade visual for melhor e mais facil de manter.

### Etapa C: Futuro Proximo

1. `django-formtools` para OCR de envelopes e importacoes guiadas.
2. `django-rq` para OCR, PDFs e auditorias pesadas em segundo plano.
3. `django-guardian` depois que o cliente definir perfis reais.
4. `django-allauth` quando formos fechar usuarios, MFA e recuperacao de senha.

## Regra De Ouro

Usar biblioteca pronta para infraestrutura repetitiva. Nao terceirizar o nucleo do dominio:

- associacao de contribuicoes;
- saneamento bancario;
- remessas internas;
- contribuintes auxiliares;
- designacoes e regra de centavos;
- elegibilidade por contribuicao;
- relatorios eclesiasticos;
- OCR com conferencia humana.

Essas partes sao justamente o diferencial do Power Church.

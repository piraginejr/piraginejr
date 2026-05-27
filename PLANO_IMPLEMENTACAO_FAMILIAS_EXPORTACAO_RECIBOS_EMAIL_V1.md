# Plano De Implementacao De Familias, Exportacao, Recibos E Email V1

## 1. Objetivo

Registrar, antes da implementacao, a ordem recomendada de trabalho para os itens:

- familias domiciliares com consulta, auditoria e impressao;
- exportacao dinamica de pessoas;
- melhoria operacional de recibos;
- envio automatico de recibos por email.

A ideia e evitar retrabalho, preservar a trilha de auditoria e manter os scripts de checagem acompanhando cada etapa.

## 2. Principios

- nao misturar fila de auditoria com tela de consulta;
- nao enviar email no mesmo request do lancamento financeiro;
- nao gravar segredos em codigo, markdown ou repositorio;
- toda etapa nova deve entrar nos scripts de verificacao antes de seguir para a proxima;
- impressao, exportacao e busca precisam seguir o mesmo padrao operacional das telas ja homologadas.

## 3. Estado Atual Relevante

### Familias domiciliares

A tela atual em `power_church_django/templates/power_church_django/people/families.html` funciona majoritariamente como fila de auditoria por endereco.

A logica em `power_church_django/services/legacy.py` prioriza grupos com relacionamentos pendentes e nao entrega uma visao boa de familias ja organizadas para consulta ampla, impressao e regra de votacao.

### Exportacao

A exportacao atual em `power_church_django/services/data_exchange.py` ainda e fixa e limitada a um conjunto pequeno de colunas de pessoas.

### Recibos

A listagem em `power_church_django/templates/power_church_django/receipts/list.html` mostra recibos emitidos, mas ainda nao centraliza tambem o fluxo de gerar novo recibo por busca de pessoa.

A selecao de contribuicoes disponiveis para novo recibo em `power_church_django/services/legacy.py` ainda possui corte de `LIMIT 300`.

### Email

O projeto Django ja tem backend de email configuravel em `power_church_django/power_church_site/settings.py` e o pacote `django-anymail` instalado, mas ainda nao existe fluxo funcional de envio de recibos por email.

Para Office 365, a opcao recomendada inicial e SMTP nativo do Django com `STARTTLS`, sem depender de provedor externo adicional.

## 4. Ordem Recomendada

## Fase 1: Reestruturar Familias Domiciliares

Objetivo:

- separar claramente `auditoria` de `consulta`.

Entregaveis:

- aba `Auditoria` com a fila atual melhor organizada;
- aba `Nucleos organizados` com lista imprimivel das familias domiciliares ja formadas;
- aba `Familias estendidas` para agrupamentos por sobrenome/chave familiar;
- filtros por pessoa, codigo, CPF, endereco e sobrenome.

Criterio de saida:

- operador consegue localizar uma pessoa e enxergar rapidamente em qual familia domiciliar ela esta;
- operador consegue imprimir a lista organizada;
- a tela deixa de servir apenas como fila de sugestoes.

## Fase 2: Regra De Votacao E Indicadores Familiares

Objetivo:

- transformar a tela de familias em ferramenta de decisao.

Entregaveis:

- indicador `ha contribuinte na familia`;
- identificacao do membro contribuinte dentro do nucleo;
- resumo util para regra de votacao por familia domiciliar;
- pesquisa por familia estendida mostrando quantos nucleos existem e sua lista.

Dependencia:

- precisa da Fase 1 concluida para nao duplicar esforco entre auditoria e consulta.

Criterio de saida:

- ao pesquisar uma pessoa, o operador enxerga tambem a situacao da familia para fins de contribuicao e votacao.

## Fase 3: Exportacao Dinamica De Pessoas

Objetivo:

- permitir exportacao flexivel em `CSV` e `XLSX`, sem ficar presa ao layout fixo atual.

Entregaveis:

- tela de selecao de campos exportaveis;
- presets iniciais como `cadastro basico`, `contatos`, `familias`, `votacao`, `financeiro`;
- reaproveitamento do stack atual de exportacao no Django;
- exportacao dos indicadores criados nas fases de familia.

Dependencia:

- idealmente depois das fases 1 e 2, para a exportacao ja nascer com os campos familiares certos.

Criterio de saida:

- o operador escolhe colunas e baixa a visao desejada sem precisar de ajustes manuais posteriores.

## Fase 4: Central De Recibos

Objetivo:

- unificar consulta e geracao de recibos na mesma area operacional.

Entregaveis:

- bloco `Gerar recibo` dentro da tela de recibos;
- busca por pessoa diretamente em `Recibos`;
- atalho para abrir ficha, extrato e historico;
- remocao do corte de 300 contribuicoes selecionaveis para recibo.

Criterio de saida:

- nao e mais necessario sair para `Pessoas` so para iniciar um recibo.

## Fase 5: Padrao Do Recibo Anexado

Objetivo:

- fechar o formato oficial do documento que sera enviado por email.

Entregaveis:

- layout definitivo do recibo em PDF;
- convencao de nome do arquivo;
- definicao do periodo consolidado;
- mensagem base do recibo e campos de observacao;
- trilha de auditoria sobre geracao e reemissao.

Dependencia:

- a fase de email nao deve comecar antes daqui, para evitar retrabalho no anexo.

Criterio de saida:

- existe um recibo padrao, confiavel e auditavel para uso manual e automatico.

## Fase 6: Infraestrutura De Email No Django

Objetivo:

- preparar envio seguro e auditavel via Office 365.

Entregaveis:

- configuracao SMTP no Django via variaveis de ambiente;
- fila propria de envio com status, tentativas, erro e reenvio;
- comando de processamento enviando um email por vez;
- eventos de auditoria sobre criacao de fila, envio, falha e reenvio.

Decisao tecnica:

- iniciar com `django.core.mail` e SMTP nativo;
- deixar `django-anymail` como opcao futura se houver troca de provedor;
- nao depender de `django-rq` nesta primeira entrega.

Criterio de saida:

- o sistema consegue enfileirar e enviar recibos individualmente, sem travar a operacao de lancamento.

## Fase 7: Automacao Mensal Do Recibo Atualizado

Objetivo:

- automatizar o envio consolidado sem parecer disparo em massa.

Entregaveis:

- ao lancar contribuicao, o sistema marca o recibo mensal da pessoa como `pendente de atualizacao`;
- rotina de consolidacao recompõe o recibo mais recente do periodo;
- envio individual com intervalo operacional controlado;
- bloqueios contra duplicidade no mesmo periodo.

Dependencia:

- precisa das fases 5 e 6 prontas.

Criterio de saida:

- contribuicoes novas atualizam o recibo mensal sem gerar uma enxurrada de emails.

## 5. Ordem Resumida

1. familias domiciliares: consulta, auditoria e impressao;
2. indicadores de contribuicao/votacao por familia;
3. exportacao dinamica de pessoas;
4. central de recibos com busca por pessoa;
5. padrao oficial do recibo anexado;
6. SMTP Office 365 e fila de envio no Django;
7. automacao mensal do envio de recibos;
8. incremento dos scripts de checagem em todas as fases.

## 6. Decisoes Funcionais Que Precisam Ser Confirmadas Antes Do Codigo Final

- na regra de votacao, vale qualquer contribuicao historica ou um periodo especifico;
- a familia estendida sera apenas consulta por sobrenome ou tambem tera efeitos operacionais;
- o recibo mensal deve consolidar o mes civil, a competencia financeira ou um intervalo escolhido;
- o email automatico sera enviado imediatamente apos o lancamento ou por janela diaria/agendada;
- o texto institucional do email sera unico ou editavel por periodo.

Essas definicoes nao impedem comecar a estrutura das fases 1 a 4, mas influenciam o fechamento das fases 5 a 7.

## 7. Validacao Em Cada Etapa

Cada fase deve atualizar os scripts mais proximos do comportamento novo, em especial:

- `scripts/verificar_django_funcional.py`;
- `scripts/verificar_contrato_visual_django.py`;
- `scripts/verificar_funcionalidade_total.py`.

Quando houver logica de escrita, tambem pode ser necessario ampliar:

- `scripts/verificar_django_escrita_pessoas.py`;
- novos scripts especificos de recibo e email, se a complexidade justificar.

## 8. Recomendacao Operacional

Executar em quatro entregas grandes:

1. familias domiciliares completas;
2. exportacao dinamica;
3. central de recibos e recibo padrao;
4. email automatico e consolidacao mensal.

Essa divisao entrega valor cedo, reduz risco e facilita homologacao progressiva com o cliente.

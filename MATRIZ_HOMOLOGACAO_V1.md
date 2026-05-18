# Matriz de Homologacao V1

Objetivo: validar o que ja foi implantado antes de retomar novas evolucoes, reduzindo regressao e a necessidade de voltar etapas inteiras.

## Regra de uso

- Toda correcao relevante deve ser checada nesta matriz.
- Toda correcao visual ou de fluxo Django deve entrar no contrato visual automatico quando puder regredir tela operacional.
- Sempre usar uma base de teste conhecida e, quando houver risco financeiro, gerar backup antes.
- So considerar um modulo "homologado" quando os cenarios abaixo estiverem verdes.

## Massa de prova recomendada

- Lotes PIX Sicoob ja auditados e reconstruidos
- Lotes Bradesco ja auditados
- Casos sentinela:
  - DOXA / Paschoal Piragine Junior
  - Bravim
  - Ronaldo Santos Mendo
  - Primeira Igreja Batis / mesma titularidade
  - Kelly Mendonca do Car / centavos especiais

## Contribuintes

| Modulo | Cenario | Resultado esperado | Status |
| --- | --- | --- | --- |
| Contribuintes | Abrir dashboard sem filtros | Exibe somente resumo, busca rapida e marcadores estrategicos | Pendente |
| Contribuintes | Clicar em `Contribuintes ativos` | Abre lista completa de contribuintes | Pendente |
| Contribuintes | Clicar em `Pessoa fisica` | Lista muda para PF, sem repetir indevidamente a mesma amostra fixa | Pendente |
| Contribuintes | Clicar em `Pessoa juridica / externo` | Lista muda para PJ | Pendente |
| Contribuintes | Clicar em `Vinculados a pessoas` | Lista mostra somente vinculados | Pendente |
| Contribuintes | Clicar em `Fila pendente` | Lista mostra somente contribuintes com pendencias | Pendente |
| Contribuintes | Clicar em `Associacoes sugeridas` | Abre painel familiar sugerido, sem cair na tabela principal | Pendente |
| Contribuintes | Clicar em `Blocos familiares` | Abre blocos familiares, sem misturar com outras listas | Pendente |
| Contribuintes | Busca rapida por nome no dashboard | Abre a lista filtrada imediatamente | Pendente |
| Contribuintes | Busca por documento, `ID-` e `MEM-` | Retorna o contribuinte correto | Pendente |
| Contribuintes | Identidade financeira sombra | Se uma identidade sem contribuicao ativa tiver mesmo nome de outra com contribuicao real, ela nao aparece na central operacional nem polui filtros/relatorios | Pendente |
| Contribuintes | Ordem visual e nomes importados | Marcadores estrategicos ficam compactos na largura da tela; tabela principal ordena por nome limpo, sem documento numerico antes do nome | OK automatico |

## Contribuicoes

| Modulo | Cenario | Resultado esperado | Status |
| --- | --- | --- | --- |
| Contribuicoes | Buscar por nome da pessoa | Retorna contribuicoes da pessoa | Pendente |
| Contribuicoes | Buscar por nome do contribuinte financeiro | Retorna contribuicoes ligadas ao contribuinte | Pendente |
| Contribuicoes | Buscar por `ID-`, `MEM-` e CPF | Retorna os lancamentos corretos | Pendente |
| Contribuicoes | Abrir contribuicao sem pessoa e clicar `Ver contribuinte` | Abre a lista filtrada de contribuintes | Pendente |
| Contribuicoes | Sentinela de valor zero bancario | Nenhuma contribuicao ativa vinda de PIX ou extrato pode ter valor `<= 0`; se aparecer, a homologacao falha apontando lote, movimento, banco e remetente | OK automatico |
| Contribuicoes | Fechamento remessa-contribuicao | Todo movimento bancario ativo nao ignorado deve ter contribuicao ativa e a soma das contribuicoes deve fechar com o valor da remessa | OK automatico |
| Contribuicoes | Lancamento manual assistido | Permite registrar envelope/comprovante/e-mail com varias linhas por pessoa, contribuinte auxiliar e destinacao, exigindo soma fechada e auditoria | OK automatico |
| Contribuicoes | Envelopes digitalizados | Arquiva imagem/PDF por competencia e lote, grava hash, vincula itens a contribuicoes e bloqueia soma divergente | OK automatico |
| Contribuicoes | Rateio de contribuicao existente | Permite dividir uma contribuicao ja lancada em varias linhas, preservando o total original e registrando justificativa/auditoria | Pendente |
| Pessoas | CPF manual invalido | Criacao/edicao manual bloqueia CPF invalido antes de salvar; importacao em massa continua preservando CPF invalido para auditoria | OK automatico |
| Pessoas | CPF manual duplicado | Criacao/edicao manual bloqueia CPF ja usado por outra ficha ativa e orienta o operador a atualizar/vincular a ficha existente | OK automatico |
| Pessoas | E-mail manual invalido | Criacao/edicao manual bloqueia e-mail com aparencia invalida antes de salvar, sem exigir confirmacao por resposta de e-mail | OK automatico |
| Pessoas | Validacao imediata de campos | Ao sair dos campos CPF/e-mail, formulario avisa CPF invalido, CPF duplicado ou e-mail invalido antes da tentativa final de gravacao | OK automatico |
| Pessoas | Campos fechados de perfil | `sexo` e `estado civil` sao listas fechadas no Django para evitar variantes digitadas pelo operador | OK automatico |
| Pessoas | Familia domiciliar por endereco | Ficha cria familia domiciliar automaticamente quando outra pessoa tem endereco completo igual, sem alterar dados financeiros | OK automatico |
| Pessoas | Relacao familiar manual | Ficha permite registrar, modificar ou remover relacao familiar/familia estendida com auditoria | OK automatico |
| Pessoas | Hipoteses domiciliares em lote | Lista de familias domiciliares permite marcar varios grupos, criar relacoes auditadas em lote e deixa de mostrar grupos que ja estejam totalmente vinculados | OK automatico |
| Pessoas | Upload de foto na ficha | Criacao manual e edicao de pessoa exibem upload de foto, salvam em `data/fotos_membros`, substituem foto anterior e mantem exibicao ampliada na ficha | OK automatico |
| Pessoas | Padronizacao de fotos antigas | Script `scripts/padronizar_fotos_membros.py` simula e, com `--apply`, move fotos antigas para subpastas por ID com sufixo `__id_` sem quebrar a URL por ficha | OK ferramenta |
| Pessoas | Lixeira segura | Exclusao manual exige usuario autenticado, senha, nome exato e justificativa; ficha sai do cadastro ativo, mas snapshot fica em lixeira segura para recuperacao futura | OK automatico |
| Pessoas | Auditoria de exclusoes | Fichas excluidas nao aparecem na lista/ficha operacional; usuario autorizado audita em `Pessoas > Lixeira segura` com operador, data, motivo e snapshot preservado | OK automatico |
| Pessoas | Purga segura da lixeira | Depois de auditada, somente superusuario pode purgar definitivamente; se houver contribuicao, recibo ou lancamento financeiro vinculado, a purga e bloqueada | OK automatico |
| Pessoas | Cadastro familiar assistido | Futuro assistente deve reaproveitar endereco/dados comuns e criar varias fichas familiares com vinculos auditados | Documentado |
| Permissoes futuras | Acoes financeiras na ficha | Lancamento, rateio e ajuste financeiro devem ficar visiveis somente para operador financeiro quando o controle fino for ativado | Pendente |

## PIX Sicoob

| Modulo | Cenario | Resultado esperado | Status |
| --- | --- | --- | --- |
| PIX | Abrir lote em `Todos` | Ordem cronologica e totais coerentes | Pendente |
| PIX | Abrir `Destinacoes especiais` | Lista todos os codigos `01..12`, inclusive aprovados | Pendente |
| PIX | Aprovar associacao forte | Lancamento vai para pessoa correta e sai da fila | Pendente |
| PIX | Marcar recorrente como `NR` | Sai da fila do lote atual e pode reaparecer em lote futuro | Pendente |

## Extratos Bradesco

| Modulo | Cenario | Resultado esperado | Status |
| --- | --- | --- | --- |
| Bradesco | Abrir lote em `Todos` | Ordem cronologica e totais coerentes | Pendente |
| Bradesco | Abrir `Destinacoes especiais` | Lista todos os codigos especiais | Pendente |
| Bradesco | Confirmar `mesma titularidade` | Movimento vai para ignorados, sai da auditoria e nao conta como doacao | Pendente |
| Bradesco | Associar um nome repetido no lote | Replica a associacao para ocorrencias iguais no mesmo lote | Pendente |

## Central de importacoes

| Modulo | Cenario | Resultado esperado | Status |
| --- | --- | --- | --- |
| Importacoes | Dashboard principal | Mostra lotes do mais novo para o mais antigo | OK Django |
| Importacoes | Chave segura PDF | Operador pode comparar Swift/PyMuPDF antes de gravar; se houver divergencia, o lote nao e criado; no Django, esta opcao fica selecionada por padrao | OK automatico |
| Importacoes | Abrir lote | Lista de lotes sempre exibe acao explicita `Abrir lote`, sem depender de clicar em areas escondidas da tabela | OK automatico |
| Importacoes | Upload de lote novo | Cria lote novo e mostra retorno claro | Pendente |
| Importacoes | Upload de lote duplicado | Informa duplicidade e abre o lote existente | Pendente |
| Importacoes/exportacoes | Exportacao de pessoas | Lista de pessoas exporta CSV e XLSX preservando filtros e registrando evento Django | OK Django |
| Importacoes/exportacoes | Mapeador de campos de exportacao | Futuro painel deve permitir selecionar campos cadastrais, familiares e financeiros antes de gerar XLSX/CSV | Documentado |
| Auditoria | Eventos Django | Tela de auditoria exibe eventos Django e espelhos de escritas controladas no legado | OK Django |
| Arquitetura Django | Pacotes de fundacao | Pacotes de filtros, tabelas, formularios em etapas, auditoria, permissoes por objeto, feature flags, money fields, email e exportacao estao instalados, ativos quando seguros e verificados por script | OK automatico |
| Arquitetura Django | Contrato visual operacional | Importacoes, lotes, contribuintes auxiliares e relatorios principais devem preservar botoes, filtros, marcadores compactos e ordem visual validada por `verificar_contrato_visual_django.py` | OK automatico |

## Relatorios

| Modulo | Cenario | Resultado esperado | Status |
| --- | --- | --- | --- |
| Contribuintes por periodo | Abrir pelo card do dashboard | Relatorio abre corretamente | Pendente |
| Contribuintes por periodo | PDF | Mantem resumo, legenda e layout compacto | Pendente |
| Contribuintes por periodo | `Abrir PDF p/ imprimir` | Impressao usa o PDF oficial | Pendente |
| Contribuicoes por periodo | Ordem visual | Tabela inicia por nome/contribuinte, deixa sigla `SA/SI/NF/NV/NM/NR` em coluna propria e evita misturar nomes com documentos numericos | OK automatico |
| Contribuicoes | Lista operacional | Tela de contribuicoes ordena por identidade limpa e separa documentos numericos depois dos nomes | OK automatico |
| Relatorios | Visualizacao e PDF oficial | Tela, impressao e PDF usam resumo compacto, tabela tabular e remessas em chips/colunas para evitar layout poluido | OK automatico |
| Relatorios por destino | Dizimo, Missoes e campanhas | Cada destinacao deve ter relatorio separado com total proprio para conferencia e liberacao futura de verba | OK Django |
| Familias domiciliares | Complemento equivalente | `ap`, `apto`, `apartamento`, `bl` e `bloco` com mesmo numero devem aumentar o automatico sem confundir predios inteiros | OK automatico |
| Familias domiciliares | Dashboard e relatorio por CEP | Central deve contar familias domiciliares, listar pessoas com foto, separar SA de outras modalidades e permitir filtro por CEP/regiao | OK Django |
| Familias domiciliares | Auditoria de hipoteses | Mesmo CEP/logradouro/numero com complemento diferente deve ficar em lista de auditoria, nao em automatico cego | OK automatico |

## Proxima rotina de trabalho

1. Corrigir um problema por vez.
2. Rodar os cenarios do modulo afetado.
3. Marcar a linha da matriz.
4. So depois seguir para a proxima evolucao.

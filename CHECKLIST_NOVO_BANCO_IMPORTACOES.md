# Checklist Novo Banco De Importacoes

Use este checklist sempre que um novo banco entrar na Central de importacoes.

Regra arquitetural: banco novo deve ser desenvolvido primeiro no Django e no nucleo portavel (`power_church_core`), com leitura por PyMuPDF/PDFium quando possivel. O prototipo antigo fica apenas como fallback temporario dos bancos ja existentes.

## 1. Entrada

- [ ] Receber PDF real do banco
- [ ] Identificar periodo do extrato
- [ ] Identificar se o documento e PIX, extrato corrente, CSV ou outro layout
- [ ] Confirmar se o arquivo tem texto extraivel ou se e imagem

## 2. Parser

- [ ] Criar parser no nucleo portavel, sem nova tela ou dependencia no prototipo antigo
- [ ] Validar a chave segura Swift/PyMuPDF antes de liberar importacao operacional
- [ ] Localizar data do movimento
- [ ] Localizar valor de credito
- [ ] Separar debito de credito
- [ ] Identificar nome do remetente/origem
- [ ] Identificar documento mascarado, se existir
- [ ] Identificar numero interno do banco, se existir
- [ ] Mapear prefixos/historicos relevantes
- [ ] Tratar quebra de linha
- [ ] Tratar quebra de pagina
- [ ] Definir movimentos fora de escopo

## 3. Contrato Do Movimento

- [ ] data
- [ ] competencia
- [ ] valor
- [ ] nome_origem
- [ ] nome_normalizado
- [ ] codigo_centavos
- [ ] movement_kind
- [ ] receiving_code
- [ ] identificador interno
- [ ] raw_text
- [ ] signature_global

## 4. Integracao Com O Motor Comum

- [ ] criar lote
- [ ] inserir movimentos
- [ ] aplicar regra de centavos
- [ ] tentar match automatico
- [ ] criar contribuicao financeira
- [ ] alimentar contribuinte auxiliar
- [ ] habilitar reprocessamento
- [ ] habilitar encerramento do lote

## 5. Auditoria

- [ ] busca ampla consulta o cadastro inteiro
- [ ] resultados da busca ampla sobem para o topo
- [ ] badges de origem aparecem
- [ ] existe opcao `manter sem pessoa vinculada`
- [ ] existe opcao `ignorar`
- [ ] existe opcao `mesma titularidade / origem interna`, quando aplicavel

## 6. Consistencia Operacional

- [ ] `Todos` em ordem cronologica
- [ ] filas de saneamento em ordem de prioridade
- [ ] volta da auditoria para o mesmo filtro do lote
- [ ] lotes em ordem do mais novo para o mais antigo
- [ ] `NR revisado` sai da fila no lote atual
- [ ] `NR revisado` pode reaparecer em lote futuro

## 7. Casos Reais Para Testar

- [ ] nome exato
- [ ] nome truncado
- [ ] nome em segunda linha
- [ ] nome em pagina seguinte
- [ ] centavos especiais
- [ ] duplicidade entre lotes
- [ ] mesma titularidade
- [ ] movimento sem nome
- [ ] recorrencia do mesmo nome no mesmo lote

## 8. Homologacao Final

- [ ] total financeiro do lote bate com o extrato
- [ ] contagem de movimentos bate com o escopo decidido
- [ ] sem perda de nomes relevantes
- [ ] sem troca de valores
- [ ] sem quebra do Sicoob ou dos bancos anteriores
- [ ] central de importacoes continua coerente
- [ ] importacao Django usa `Comparar Swift x PyMuPDF` por padrao durante a transicao
- [ ] lista de lotes exibe `Abrir lote` de forma clara
- [ ] telas novas passam no contrato visual Django

## 9. Decisao De Encerramento

Um banco novo so entra em uso quando:

- [ ] o parser passou em casos reais
- [ ] o operador consegue trabalhar sem reaprender a interface
- [ ] o saneamento futuro consegue seguir pela central de contribuintes

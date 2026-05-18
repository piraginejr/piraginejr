# Arquitetura De Remessas E Designacoes V1

## 1. Regra Central

O sistema deve separar a origem financeira bruta da classificacao interna da contribuicao.

- Remessa: fato financeiro original. Exemplos: PIX Santander, extrato Bradesco, recebimento Sicoob, envelope cash.
- Designacao: destino interno usado em relatorios, recibos, elegibilidade e contabilidade. Exemplos: Dizimo, Missoes, Acao Social, campanha especifica.

## 2. Contrato De Consistencia

Uma remessa pode ter uma ou varias designacoes.

Regra obrigatoria:

```text
soma das designacoes = valor total da remessa
```

Se a soma nao bater, a alteracao nao deve ser salva.

## 3. Importacao Bancaria

Na importacao automatica:

- O valor da remessa nao deve ser editado manualmente.
- Quando nao houver regra especial, a designacao padrao sera Dizimo.
- Quando houver regra de centavos, a designacao segue a conta/campanha correspondente e passa pela validacao ja existente.
- Quando o banco trouxer CPF/CNPJ completo, o match principal deve ser por documento.
- Quando nao houver pessoa vinculada, a remessa deve ficar preservada em contribuinte auxiliar ou pendencia documental.

## 4. Edicao Manual Posterior

Quando houver envelope ou orientacao manual do contribuinte, o operador podera ratear a remessa em designacoes internas.

Cada edicao deve gravar:

- operador;
- data e hora;
- justificativa obrigatoria;
- remessa de origem;
- designacoes anteriores;
- novas designacoes;
- comprovacao de que a soma confere com a remessa.

## 5. Relatorios

Relatorios financeiros, recibos, extratos de pessoa, relatorios de Dizimo, ofertas e campanhas devem obedecer as designacoes, nao apenas as remessas bancarias.

Isso evita confundir:

- origem do dinheiro, usada para conciliacao e auditoria;
- destino interno do dinheiro, usado para relatorios, recibos, elegibilidade e prestacao de contas.

Relatorios por destino devem existir como conferencias independentes:

- Dizimo;
- Missoes;
- Acao Social;
- cada campanha ou destinacao especifica ativa.

Cada relatorio deve mostrar somente as linhas daquela destinacao, com totais proprios para conferencia e futura liberacao de verbas.

## 5.1 Ponto De Entrada Operacional

A ficha da pessoa pode ser usada como ponto natural para lancamento, rateio e consulta financeira, porque o operador enxerga historico, vinculos familiares e origem financeira no mesmo contexto.

Quando a camada fina de permissoes for ativada:

- operador financeiro ve lancamento manual, rateio, ajuste de destinacao e relatorios financeiros;
- operador de secretaria ve cadastro, contatos, endereco e vinculos familiares;
- operador de ingresso de pessoas nao deve ver nem alterar valores financeiros;
- auditor pode consultar rastreabilidade conforme perfil definido pelo cliente.

## 5.2 Nucleos Familiares

O cadastro de pessoas deve manter dois niveis familiares:

- nucleo familiar: criado automaticamente quando duas ou mais fichas possuem endereco completo e unidade/complemento equivalentes;
- familia estendida: criada ou ajustada manualmente quando ha parentesco sem o mesmo endereco.

O complemento deve aceitar equivalencias seguras de escrita, por exemplo:

- `ap`, `apto`, `apartamento` com o mesmo numero;
- `bl`, `bloco` com o mesmo numero;
- numeros isolados equivalentes a apartamento quando o restante do endereco for igual.

O vinculo familiar ajuda o operador a interpretar envelopes, rateios entre familiares e remessas feitas por uma pessoa em nome de outra. Ele nao deve alterar contribuicoes automaticamente.

Evolucao estrategica futura:

- card de dashboard com contagem de nucleos familiares de alta confianca e hipoteses;
- relatorio agrupando pessoas por endereco com fotos, status e siglas;
- filtro por CEP ou por quantidade de CEPs para planejar visitas, reunioes regionais e acoes pastorais;
- auditoria de hipoteses duvidosas quando ha mesmo CEP/logradouro/numero, mas complemento diferente ou incompleto.

## 6. Homologacao Obrigatoria

O script `scripts/verificar_estabilidade_demo.py` deve continuar validando:

- tela de contribuicoes;
- filtro de contribuicoes por tipo, incluindo Dizimo;
- relatorio de contribuicoes por periodo;
- PDF de contribuicoes por periodo;
- extrato de contribuicoes por pessoa;
- PDF do extrato de contribuicoes por pessoa;
- relatorios estrategicos de contribuintes em PDF.

Quando a tabela de designacoes/rateios for criada, estes mesmos relatorios deverao ser migrados para ler designacoes e nao a remessa bruta.

## 7. Santander

O Santander entra como extrato bancario, nao como lote PIX historico.

- O layout deve ser detectado automaticamente entre consolidado e nao consolidado.
- Nesta primeira etapa importamos apenas `Pix Recebido`, porque e o credito identificavel por CPF/CNPJ completo.
- Como o banco nao informa nome, o match automatico usa CPF/CNPJ completo contra pessoas e identidades financeiras.
- Documento com match unico entra como pronto, salvo regra de centavos, que continua indo para confirmacao de destinacao.
- Documento sem pessoa vinculada entra como contribuinte auxiliar documental e fica pendente de associacao.
- Associacoes manuais por documento devem ser replicadas para as demais ocorrencias iguais no mesmo lote e preservadas em reprocessamento.

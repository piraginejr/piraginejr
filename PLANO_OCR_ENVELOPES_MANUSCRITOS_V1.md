# Plano OCR De Envelopes Manuscritos V1

## 1. Decisao

O OCR de envelopes manuscritos passa a ser a proxima grande peca funcional do sistema de recebimentos depois da migracao operacional segura para Django.

Usuarios, perfis e privilegios finos serao definidos depois que o cliente enxergar o fluxo completo de operacao, incluindo bancos, contribuintes, relatorios, recibos e envelopes em especie.

Motivo: sem o fluxo completo, os niveis de acesso seriam desenhados no chute.

## 2. Objetivo

Criar uma entrada assistida para contribuicoes em especie registradas em envelopes preenchidos a mao.

O sistema deve ajudar o operador a ler:

- nome do contribuinte;
- valor total do envelope;
- competencia;
- destinacoes internas, como dizimo, missoes e acao social;
- observacoes escritas no envelope;
- possivel pessoa do rol ou contribuinte auxiliar.

## 3. Principio De Seguranca

OCR manuscrito nunca deve lancar contribuicao financeira de forma cega.

Todo envelope deve passar por revisao humana antes de gerar ou alterar contribuicoes.

O sistema deve guardar:

- imagem/PDF original do envelope;
- texto bruto lido pelo OCR;
- campos sugeridos;
- campos confirmados pelo operador;
- data/hora da confirmacao;
- operador responsavel;
- justificativa quando houver alteracao manual;
- soma das designacoes e valor total do envelope.

## 4. Fluxo Operacional Proposto

1. Operador sobe lote de envelopes digitalizados.
2. Sistema cria lote de OCR em estado `em_processamento`.
3. OCR tenta extrair texto e campos candidatos.
4. Sistema sugere pessoa, contribuinte auxiliar ou cadastro novo.
5. Operador revisa cada envelope.
6. Operador confirma valor total e designacoes.
7. Sistema valida se a soma das designacoes fecha com o total do envelope.
8. Sistema gera contribuicoes auditadas.
9. Envelope fica vinculado as contribuicoes geradas.
10. Relatorios financeiros passam a ler as designacoes confirmadas, nao apenas a remessa/envelope.

## 4.1 Entrada Manual Antes Do OCR

Mesmo antes do OCR, o sistema precisa aceitar entrada manual assistida para:

- envelope fisico lido pelo operador;
- comprovante de remessa anexado ao envelope;
- comprovante de cartao de credito;
- e-mail do contribuinte informando divisao interna;
- remessa unica que pertence a varias pessoas da familia;
- remessa unica que deve ser dividida em varias destinacoes.

Regra:

- a origem bancaria ou o comprovante continua sendo a prova do total;
- a tela manual registra a divisao interna;
- a soma das linhas deve fechar com o total informado;
- cada linha pode ter pessoa, contribuinte auxiliar, tipo de contribuicao, campanha e observacao propria;
- toda criacao ou edicao precisa de justificativa e auditoria.

## 4.2 Vinculos Familiares

A ficha da pessoa deve manter vinculos familiares para ajudar o operador quando uma remessa ou envelope pertencer a mais de uma pessoa da mesma casa.

Regras iniciais:

- nucleo familiar criado automaticamente por endereco completo e complemento equivalente quando a ficha for criada, editada ou sincronizada;
- familia estendida cadastrada manualmente quando houver parentesco sem mesmo endereco;
- o operador pode modificar o tipo do vinculo, observacoes ou remover o vinculo sem que isso altere contribuicoes;
- vinculo automatico por endereco nunca deve gerar alteracao financeira cega;
- no OCR, a familia ajuda a sugerir possiveis rateios, mas o operador continua confirmando pessoa, valor e destinacao.
- hipoteses duvidosas de mesmo predio/endereco, mas complemento diferente ou incompleto, devem ir para auditoria visual antes de virar nucleo.

## 4.3 Dashboard Futuro De Nucleos

Depois da migracao operacional, criar uma area estrategica para nucleos familiares:

- card no dashboard com contagem de nucleos familiares mapeados;
- relatorio agrupando pessoas por nucleo e endereco, com foto e sigla de status;
- filtro por quantidade de CEPs ou faixa/fragmento de CEP;
- separacao entre membros ativos e outras modalidades, como frequentadores, visitantes e arquivo morto;
- uso pastoral e logistico para visitas, reunioes por regiao e acompanhamento de familias.

## 5. Modelo De Dados Sugerido

Tabelas futuras provaveis:

- `envelope_lotes`: lote de digitalizacao/OCR;
- `envelope_documentos`: cada envelope/imagem;
- `envelope_ocr_resultados`: texto bruto, confianca e campos sugeridos;
- `envelope_revisoes`: confirmacao humana e auditoria;
- `envelope_designacoes`: divisao do valor entre tipos/campanhas/contas;
- vinculo entre `envelope_documentos` e `contribuicoes`.

## 6. Regras Financeiras

- A soma das designacoes deve ser igual ao valor total confirmado do envelope.
- Se houver divergencia, o envelope fica pendente.
- Se o contribuinte nao estiver no rol, a contribuicao entra ligada ao contribuinte auxiliar.
- Se houver match com pessoa do rol, a contribuicao pode ser vinculada a pessoa.
- Alteracoes posteriores devem exigir justificativa e trilha de auditoria.
- Uma contribuicao existente pode ser rateada manualmente quando o operador receber envelope, comprovante ou e-mail posterior informando a divisao correta.

## 7. Laboratorio Inicial

Antes de programar o fluxo completo, criar uma massa pequena de teste:

- 20 a 50 envelopes reais ou anonimizados;
- casos com letra clara;
- casos com letra dificil;
- casos com varias designacoes;
- casos sem nome completo;
- casos com membro, frequentador, visitante e contribuinte sem cadastro.

Objetivo do laboratorio:

- medir qualidade real do OCR;
- descobrir campos que o envelope usa;
- definir o layout da tela de revisao;
- decidir se sera necessario OCR local, servico externo ou abordagem hibrida.

## 8. Criterios De Aceite

O modulo so deve ser considerado operacional quando:

- nenhum envelope confirmado gera contribuicao sem trilha de auditoria;
- nenhum envelope confirmado fica com soma divergente;
- o operador consegue corrigir nome, valor, competencia e designacoes;
- relatorios por periodo incluem envelopes confirmados;
- ficha da pessoa/contribuinte mostra a origem `envelope`;
- imagem original pode ser consultada a partir da contribuicao;
- bateria de homologacao possui sentinelas de soma, origem e auditoria.

## 9. Fora Do Escopo Inicial

Nao entra no primeiro bloco:

- reconhecimento perfeito sem revisao humana;
- permissao fina por operador;
- multi-tenant;
- desligamento do prototipo antigo;
- automacao completa de caixa sem conferencia.

## 10. Ordem Recomendada

1. Fechar migracao Django operacional.
2. Validar portabilidade minima de PDF/OCR em ambiente Linux ou Docker.
3. Criar laboratorio de envelopes.
4. Implementar lote de OCR e tela de revisao.
5. Integrar designacoes e contribuicoes.
6. Incluir verificadores automaticos.
7. Demonstrar fluxo completo ao cliente.
8. Definir usuarios, perfis e privilegios finos com base no fluxo real.

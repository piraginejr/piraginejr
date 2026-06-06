# Plano Etapa 3 Piloto Financeiro Maio 2026 V1

## 1. Objetivo

Usar os extratos reais de `maio/2026` dos bancos `Bradesco`, `Santander` e `Sicoob` como massa oficial de prova para abrir a `Etapa 3` da migracao financeira com criterio forte e risco controlado.

## 2. Principio

Nao vamos pular a `Etapa 3`.

Vamos usá-la com um piloto real:

- primeiro validar a leitura dos arquivos;
- depois medir duplicidades, pendencias e centavos especiais;
- depois escolher um banco para `corte controlado`;
- so ao final liberar a migracao financeira ampla.

Decisao operacional desta versao:

- `PIX isolado` fica fora da operacao corrente;
- o caminho oficial passa a ser sempre o `extrato bancario completo`;
- o financeiro pode ser lancado antes da auditoria humana, preservando o saldo bancario;
- os recibos automaticos saem apenas do que ficou `regular/pronto`;
- o lote deve permanecer `parcial` ate o operador encerrar manualmente a auditoria;
- isso reduz risco de lacuna entre PIX, TED, transferencia e outras formas de recebimento no mesmo periodo.

## 3. Arquivos oficiais desta rodada

- `BRADESCO_MAIO26.pdf`
- `SANTANDER_Maio2026.pdf`
- `SICOOB_MAIO26.pdf`

## 4. Regra de aceite do piloto

Um banco so pode ser usado para `corte controlado` se:

1. o parser ler o arquivo corretamente;
2. o leitor homologado atual e o leitor portavel tiverem comportamento equivalente;
3. a amostra real nao apontar erro estrutural de identidade, valor ou periodo;
4. as duplicidades e os centavos especiais puderem ser explicados pela base ja importada.

## 5. Ferramenta oficial do piloto

Script:

- [executar_piloto_financeiro_maio2026.py](/Users/piraginejr/Documents/New project/Teste/Power Church/scripts/executar_piloto_financeiro_maio2026.py)
- [executar_piloto_financeiro_controlado.py](/Users/piraginejr/Documents/New project/Teste/Power Church/scripts/executar_piloto_financeiro_controlado.py)
- [comparar_fluxo_django_bradesco_controlado.py](/Users/piraginejr/Documents/New project/Teste/Power Church/scripts/comparar_fluxo_django_bradesco_controlado.py)
- [verificar_piloto_financeiro_bradesco.py](/Users/piraginejr/Documents/New project/Teste/Power Church/scripts/verificar_piloto_financeiro_bradesco.py)
- [verificar_snapshot_piloto_bradesco_postgres.py](/Users/piraginejr/Documents/New project/Teste/Power Church/scripts/verificar_snapshot_piloto_bradesco_postgres.py)

Ele:

- nao grava novos lotes;
- le os tres arquivos reais;
- compara o leitor atual com o leitor portavel;
- cruza os movimentos com a base ja importada;
- classifica cada banco como:
  - `APTO`
  - `APTO_COM_AUDITORIA`
  - `BLOQUEADO_PORTABILIDADE`
  - `BLOQUEADO`

Para o primeiro corte seguro em clone:

- `executar_piloto_financeiro_controlado.py` copia o banco operacional para `data/sandboxes`
- importa o extrato somente no clone
- entrega o status real do lote sem qualquer risco para a base operacional

Para a validacao formal do comportamento:

- `comparar_fluxo_django_bradesco_controlado.py` compara o `fluxo Django atual` com o `piloto controlado`
- `verificar_piloto_financeiro_bradesco.py` valida o lote clone com sentinelas objetivas
- `verificar_snapshot_piloto_bradesco_postgres.py` valida a materializacao desse lote nos modelos nativos do Postgres

## 6. Leitura inicial desta rodada

### Bradesco

- parser leu corretamente;
- portabilidade aprovada;
- ha duplicidades esperadas contra lote ja importado;
- ja foi validado tambem em `piloto controlado em clone`;
- ja foi comparado `100%` com o fluxo Django atual;
- ja foi materializado em modelos nativos de piloto no Postgres;
- e o melhor candidato para o primeiro corte controlado.

### Santander

- parser funcional;
- portabilidade ainda nao homologada;
- bom como massa de prova;
- nao deve ser usado como corte final antes de corrigirmos a divergencia do leitor portavel.

### Sicoob

- parser funcional e volume alto;
- excelente massa de prova real;
- grande quantidade de duplicidades ja encontradas na base;
- portabilidade ainda bloqueada, com divergencia forte entre leitor atual e leitor portavel.

## 7. Estrategia recomendada

1. usar `Bradesco` como primeiro piloto controlado da Etapa 3;
2. manter `Santander` e `Sicoob` como massa de prova paralela ate fechar a portabilidade;
3. depois da correcao do leitor portavel, repetir a comparacao dos tres bancos;
4. so entao liberar o dominio financeiro completo para corte.

Observacao de escopo:

- o modulo `PIX` continua preservado para historico e compatibilidade;
- mas a operacao desta versao nao deve abrir novos lotes PIX isolados quando houver extrato completo equivalente.

## 8. O que muda no plano da migracao

O plano original da `Etapa 3` continua valendo, mas agora com uma frente concreta:

- `piloto financeiro maio/2026`

Isso melhora muito a seguranca, porque a etapa deixa de ser abstrata e passa a ser medida por dados bancarios reais do cliente.

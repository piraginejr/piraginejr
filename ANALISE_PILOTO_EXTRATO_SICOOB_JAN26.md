# Analise Piloto: Extrato Sicoob Jan/2026

Arquivo analisado:

- `/Users/piraginejr/Library/CloudStorage/Dropbox/arquivos temporários/extrato de recebimentos sicoob jan26.pdf`

Lote comparado na base atual:

- `PIX lote #2`
- arquivo salvo no sistema: `Sicoob comprovante (23-04-2026 14-17-03).pdf`
- periodo: `2026-01-01` a `2026-01-31`

## Resultado principal

O extrato de recebimentos do Sicoob se mostrou forte o suficiente para virar candidato serio a **fonte canonica do banco**.

Ele traz:

- PIX
- transferencias com remetente
- TED com nome e CPF
- deposito em dinheiro com CPF/envelope
- deposito em cheque bloqueado com CPF/envelope
- liberacao de deposito bloqueado
- estorno PIX
- transferencia Sicoob com identificacao do remetente

Isso significa que ele cobre **mais do que o fluxo atual chamado PIX Sicoob**.

## Leitura tecnica do PDF

O PDF veio com estrutura muito boa para parser:

- linha principal com `data + historico + valor`
- linhas seguintes com detalhes
- nome e documento aparecem logo abaixo do movimento
- em varios casos o historico ja informa o tipo da remessa

Exemplos:

- `PIX RECEB.OUTRA IF`
- `CRED.TR.CT.INTERCRE`
- `CRÉD.TED-STR`
- `DEP.CHEQUE BLOQ.1D`
- `DEP.DINHEIRO INTERC`
- `LIBER.DEPÓSITO BLOQ`
- `EST.PIX EMIT.OUT.IF`
- `TRANSF.RECEB-PIX SI`

## Quantitativo do extrato

Entradas parseadas, excluindo saldos:

- `562` eventos de recebimento
- total bruto dos recebimentos: `R$ 362.195,82`

Resumo por historico:

- `PIX RECEB.OUTRA IF`: `541` lancamentos | `R$ 327.748,02`
- `CRED.TR.CT.INTERCRE`: `9` lancamentos | `R$ 5.105,80`
- `CRÉD.TED-STR`: `6` lancamentos | `R$ 15.772,00`
- `DEP.CHEQUE BLOQ.1D`: `2` lancamentos | `R$ 2.820,00`
- `DEP.DINHEIRO INTERC`: `1` lancamento | `R$ 3.000,00`
- `EST.PIX EMIT.OUT.IF`: `1` lancamento | `R$ 2.400,00`
- `LIBER.DEPÓSITO BLOQ`: `1` lancamento | `R$ 1.350,00`
- `TRANSF.RECEB-PIX SI`: `1` lancamento | `R$ 4.000,00`

## Comparacao com o lote atual do banco

`PIX lote #2` hoje tem:

- `559` movimentos ativos
- total de `R$ 341.213,82`

Na comparacao aproximada `nome + valor`:

- `543` casos do extrato encontram correspondente claro no lote atual
- `19` casos do extrato ficaram sem correspondente aproximado no lote atual

## O que isso revela

### 1. O modulo atual do Sicoob nao esta trazendo so PIX

Apesar do nome atual `PIX Sicoob`, a base ja mostra que o fluxo importado absorveu tambem parte de:

- `CRED.TR.CT.INTERCRE`
- `TRANSF.RECEB-PIX SI`
- pelo menos algumas entradas nao estritamente PIX

Entao o nome do modulo ficou menor do que a realidade do que ja esta entrando.

### 2. Mesmo assim, o extrato ainda mostra recebimentos faltantes

Os `19` nao casados do piloto apontam que o extrato tem informacao adicional relevante.

Casos concretos encontrados:

- `05/01 | CRÉD.TED-STR | R$ 2.060,00 | LIVIA M M FALCAO`
- `05/01 | CRÉD.TED-STR | R$ 1.761,00 | DACIA SANTANA`
- `05/01 | CRÉD.TED-STR | R$ 2.100,00 | ROBERTO MACEDO RIBEIRO`
- `08/01 | CRÉD.TED-STR | R$ 3.000,00 | WANDER F MARTINS`
- `19/01 | CRÉD.TED-STR | R$ 4.551,00 | JOSE ELOY A CERQUEIRA`
- `30/01 | CRÉD.TED-STR | R$ 2.300,00 | SONIA REGINA DE OLIVEIRA`

Tambem apareceram casos em PIX com nome/documento que merecem parser melhor:

- `JOSE A L VARGAS`
- `57.942.151 MARIA JOSE DE SOUSA PONCE RIB`
- `43.136.305 LAURA HALMENSCHLAGER HUBERT`
- `DANIELLE DE CARVALHO MARTINS DE OLIVEIRA`

E ha ainda casos que pertencem mais ao fluxo de especie/envelope:

- `DEP.CHEQUE BLOQ.1D`
- `DEP.DINHEIRO INTERC`
- `LIBER.DEPÓSITO BLOQ`

### 3. O extrato permite separar melhor as categorias

Isso e muito valioso para arquitetura.

O sistema pode classificar desde a entrada:

- `pix`
- `transferencia`
- `ted`
- `deposito_especie`
- `deposito_cheque_bloqueado`
- `liberacao_deposito`
- `estorno`
- `mesma_titularidade`

## Recomendacao arquitetural

### Recomendacao principal

Tratar o **Extrato Sicoob de recebimentos** como candidato a **fonte canonica** para novos meses.

### Como operar sem risco

1. Nao importar `PIX Sicoob` e `Extrato Sicoob` como fluxo normal para o mesmo periodo.
2. Para meses antigos ja carregados via `PIX Sicoob`, usar o extrato primeiro em **homologacao**.
3. Comparar:
   - quantidade
   - total
   - nomes
   - documentos
   - centavos especiais
   - mesma titularidade
   - especie/envelope
4. So depois decidir:
   - reconstruir o mes pela fonte extrato
   - ou manter o lote atual e importar so faltantes

## Estrategia recomendada para os proximos passos

1. Implementar um parser novo `Sicoob Extrato de Recebimentos`.
2. Reaproveitar o motor central que ja existe:
   - contribuinte auxiliar
   - associacao a pessoa
   - centavos
   - mesma titularidade
   - ignorados operacionais
   - encerramento de lote
3. Rodar esse parser primeiro em homologacao para:
   - janeiro
   - fevereiro
   - marco
4. So depois decidir a consolidacao definitiva desses meses na base real.

## Regra especial a preservar

Transferencias da propria `PIB Niteroi` devem seguir a mesma logica ja adotada no Bradesco:

- identificar como `mesma titularidade / origem interna`
- nao contar como contribuicao
- permanecer auditavel

## Ferramenta criada para reaproveitar esta analise

Foi criado o script:

- `/Users/piraginejr/Documents/New project/Teste/Power Church/scripts/analisar_extrato_sicoob_recebiveis.py`

Uso:

```bash
python3 "/Users/piraginejr/Documents/New project/Teste/Power Church/scripts/analisar_extrato_sicoob_recebiveis.py" "/caminho/do/extrato.pdf" --lot-id 2
```

Ele:

- extrai os recebimentos do PDF
- resume por historico
- compara com um lote atual do banco
- lista exemplos nao casados


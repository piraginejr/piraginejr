# Triage Dos WARNs Da Regression Audit

- Data: 01/07/2026 17:56:38
- Relatorio base: `reports/regression_audit_20260701_174754.md`
- Ambiente verificado: runtime Docker local Django + PostgreSQL

## Resumo Executivo

Nesta rodada, os WARNs pedidos foram separados entre comportamento esperado do fluxo, dado legado/piloto inconsistente e melhorias futuras de performance. Nao apareceu, nesta amostra, um bloqueador operacional que exija correcao imediata de codigo.

## Classificacao

| Item | Evidencia | Classificacao | Leitura operacional | Risco |
| --- | --- | --- | --- | --- |
| 6 movimentos de extrato com `imported_contribution_legacy_id` inexistente | IDs `16344, 16432, 16436, 16505, 16515, 16521`; todos com `review_status='ignorado'`; todos com notas de "mesma_titularidade / origem_interna"; `imported_contribution_legacy_id` = `2041, 2134, 2138, 2207, 2217, 2223`; sem correspondente em `NativeContribution` nem `PersonContributionSnapshot` | `Dado legado inconsistente` | Sao residuos em lotes piloto de extrato ja marcados como ignorados por transferencia interna entre contas da igreja. Como o status final e `ignorado`, isso nao entra como pendencia humana nem como contribuicao valida no fluxo atual. | Baixo no uso diario. Medio para auditoria tecnica, porque polui sentinelas de consistencia e pode confundir leitura historica. |
| Envelopes com 404 em `launch/edit` | Envelope `243` esta com `status='duplicado'`; envelopes `242` e `244` estao com `status='em_digitacao'`; `envelope_launch` so atende pendentes/disponiveis; `envelope_edit` so atende `status='lancado'` | `Comportamento esperado` | O WARN veio de a auditoria testar a rota pelo ID recente, sem considerar se aquele envelope esta num estado compativel com a acao. O 404 e coerente com a regra atual da view. | Baixo no runtime. Medio apenas para qualidade da auditoria, que hoje gera falso positivo ao nao ser state-aware. |
| `staticfiles` ausente no runtime Docker | Verificacao atual: `STATIC_ROOT=/app/power_church_django/staticfiles`, diretorio existe e contem arquivos; o proprio `regression_audit_20260701_174754.md` marcou `Static root` como `OK` | `Comportamento esperado` | Nao reproduz no runtime local atual. Se houve ausencia em outro ambiente, foi especifica daquele deploy/build, nao um problema presente nesta base local. | Baixo aqui. Medio em deploy futuro se o checklist de `collectstatic` nao for seguido. |
| Fotos ausentes em `/people/photo/<id>/` | Pessoas `#1` e `#2`: `find_member_photo(...) -> None` e `list_member_photo_variants(...) -> []`; a view `people.photo` retorna `404` quando nao encontra arquivo | `Dado legado ausente` | A rota esta correta: ela responde 404 quando a foto fisica nao existe. O WARN nao indica quebra da view; indica ausencia de asset para aquelas pessoas. | Baixo, desde que a interface so mostre link/imagem quando houver foto. Medio se alguma tela renderizar URL de foto sem checagem previa. |
| Fila de recibos vazia | `ReceiptDispatch.objects.count() = 0`; rota `/receipts/queue/` respondeu `200`; backend e templates de e-mail estao configurados | `Comportamento esperado` | Fila vazia significa apenas que nao ha campanha preparada ou pendencias de envio neste momento. Nao e falha por si. | Baixo. So vira problema se o operador esperar campanha automatica em andamento e ela nao tiver sido preparada. |
| Lentidao em `/people/families/` e exports | `/people/families/` entre `2406 ms` e `2463 ms`; exports CSV/XLSX entre `963 ms` e `1158 ms` | `Melhoria futura` | As telas e exports funcionam e devolvem `200`, mas ja mostram custo acima do ideal para operacao sob carga. | Medio para experiencia do operador e escalabilidade. Baixo como bloqueio imediato. |

## Detalhamento Por Item

### 1. Movimentos de extrato com contribuicao inexistente

Os 6 registros problematicos sao:

- `16344` (lote `4`, movimento fonte `87`, contribuicao `2041`)
- `16432` (lote `5`, movimento fonte `180`, contribuicao `2134`)
- `16436` (lote `5`, movimento fonte `184`, contribuicao `2138`)
- `16505` (lote `6`, movimento fonte `253`, contribuicao `2207`)
- `16515` (lote `6`, movimento fonte `263`, contribuicao `2217`)
- `16521` (lote `6`, movimento fonte `269`, contribuicao `2223`)

Padrao comum:

- `review_status='ignorado'`
- remetente `Primeira Igreja Batis`
- `review_notes` indicando remessa interna / mesma titularidade
- sem espelho atual em contribuicoes nativas ou em `PersonContributionSnapshot`

Leitura: o dado ficou "semi preenchido" em lotes piloto antigos. Nao parece um bug vivo do fluxo corrente; parece sobra historica de classificacao ja encerrada como ignorada.

### 2. Envelopes 404 em `launch/edit`

Estados observados:

- envelope `243` -> `duplicado`
- envelope `242` -> `em_digitacao`
- envelope `244` -> `em_digitacao`

Regras atuais das views:

- `launch` atende apenas envelope pendente/disponivel para lancamento
- `edit` atende apenas envelope `lancado`

Leitura: os 404 fazem sentido para esses IDs. O ajuste futuro, se quisermos limpar o ruido da auditoria, e ensinar o auditor a selecionar exemplos por status compativel.

### 3. Staticfiles no runtime

No runtime local atual:

- `STATIC_ROOT=/app/power_church_django/staticfiles`
- diretorio existe
- ha arquivos coletados

Leitura: nao ha WARN real aqui no ambiente verificado agora. Isso entra mais como item de checklist de deploy do que como bug do projeto.

### 4. Fotos ausentes

Casos validados:

- pessoa `#1` -> sem arquivo de foto
- pessoa `#2` -> sem arquivo de foto

Como a URL de foto e servida sob demanda, a resposta `404` da rota esta correta quando o arquivo nao existe.

Leitura: o WARN sinaliza asset ausente, nao defeito de view.

### 5. Fila de recibos vazia

Estado atual:

- `ReceiptDispatch` com `0` registros
- monitor da fila abrindo normalmente

Leitura: esperado enquanto nenhuma campanha/manual dispatch tiver sido preparado.

### 6. Performance

Os tempos mais altos ficaram concentrados em:

- `/people/families/`
- `/people/export/`
- exports CSV/XLSX de pessoas

Leitura: o sistema funciona, mas esse bloco merece futura otimizacao de query e/ou geracao de dataset.

## Itens Fora Do Escopo Principal, Mas Vistos Nos WARNs

| Item | Classificacao | Leitura |
| --- | --- | --- |
| `GET /accounts/logout/` retornando `405` | `Comportamento esperado` | `LogoutView` do Django normalmente exige `POST`; o WARN e do tipo "rota descoberta por GET", nao bug funcional do logout. |

## O Que Exige Correcao Imediata

Nenhum dos itens triados aqui exige correcao imediata de codigo para manter a operacao atual.

## O Que Pode Esperar

- limpeza dos 6 movimentos piloto ignorados
- tornar a `regression_audit` sensivel ao status real dos envelopes
- melhorar performance de familias e exports
- definir politica operacional para cobertura de fotos ausentes

## Pontos Que Merecem Decisao

- Se desejamos limpar os 6 movimentos piloto historicos para zerar o WARN tecnico ou se eles devem permanecer como trilha historica de lotes antigos.
- Se a auditoria deve continuar registrando 404/405 esperados como WARN ou se deve reclassificar esses casos para reduzir ruido.

## Conclusao

Pelo estado atual do runtime local:

- nao apareceu bug bloqueador novo neste grupo de WARNs;
- ha `1` grupo de `dado legado inconsistente` nos lotes piloto de extrato;
- ha `1` grupo de `dado legado ausente` nas fotos;
- o restante cai em `comportamento esperado` ou `melhoria futura`.

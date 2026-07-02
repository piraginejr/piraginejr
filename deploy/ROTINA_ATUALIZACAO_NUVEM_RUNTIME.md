# Rotina De Atualizacao Da Nuvem

## Objetivo

Padronizar a atualizacao do Power Church quando o runtime Docker/PostgreSQL **ja esta rodando na nuvem** e a implantacao depende de um terceiro ou de uma automacao do servidor.

A meta desta rotina e:

- evitar deploy por improviso;
- deixar claro **o que mudou**;
- garantir **backup antes da troca**;
- validar o ambiente logo depois;
- manter um **caminho de rollback** simples;
- trabalhar no ritmo real do servidor, que agora acompanha `main`.

## Regra Operacional Atual

O metodo oficial mudou.

Hoje, o servidor possui um mecanismo interno mais pratico, que:

- acompanha a branch `main`;
- sincroniza o codigo em janela fixa de **30 em 30 minutos**;
- reduz dependencia de acao manual a cada pequena correcao.

Por isso, o modelo operacional recomendado passa a ser:

1. desenvolver localmente;
2. validar no runtime Docker local;
3. dar `push` para `main`;
4. aguardar a janela automatica do servidor;
5. validar o ambiente depois da sincronizacao;
6. registrar qual `commit SHA` ficou ativo na nuvem.

Regra pratica:

- `main` virou a trilha oficial de desenvolvimento **e** entrega;
- nada incompleto deve ser enviado para `main`;
- `cloud-release` deixa de ser a trilha padrao e fica apenas como contingencia ou historico.

## Historico Da Primeira Cloud Release

Antes da adocao do sincronismo automatico por `main`, a primeira entrega unificada para a nuvem foi preparada em `cloud-release` com:

- `89bd22b` `Add envelope in-progress locking`
- `29a2dc3` `Fix single envelope launch without split`
- `19cc163` `Add cloud-release deployment workflow`
- `6b6cab6` `Add envelope split coverage tests`

Resumo historico:

- essa primeira `cloud-release` levou o que o cliente e o operador precisavam sentir na nuvem;
- a cobertura de testes de rateio foi junto para validar o ambiente real;
- alguns arquivos de teste acompanharam commits mistos de runtime, mas sem alterar comportamento de producao.

## Tipos De Mudanca

### 1. Mudanca De Codigo Do App

Exemplos:

- `views.py`
- `services/*.py`
- `templates/*.html`
- `power_church_site/settings.py`
- `apps/api/*`

Efeito:

- exige `rebuild` da imagem Django;
- normalmente exige `docker compose up -d --build` quando a sincronizacao manual for necessaria;
- por isso, nada desse tipo deve subir para `main` sem validacao local.

### 2. Mudanca De Schema

Exemplos:

- novos arquivos em `apps/*/migrations/`
- alteracao de models que gere migration

Efeito:

- exige backup obrigatorio antes;
- exige aplicar migrations;
- precisa de atencao extra no rollback.

Observacao:

- hoje o entrypoint do runtime ja roda `migrate` automaticamente;
- mesmo assim, em nuvem, a rotina deve tratar isso como atualizacao sensivel.

### 3. Mudanca So De Teste Ou Documentacao

Exemplos:

- `tests.py`
- `.md`

Efeito:

- nao muda comportamento de producao por si so;
- pode acompanhar outras mudancas;
- isoladamente nao precisa janela urgente.

### 4. Mudanca Em Scripts Operacionais

Exemplos:

- `scripts/powerbackup_runtime.sh`
- `scripts/empacotar_entrega_operador_runtime.sh`
- `deploy/*`

Efeito:

- pode nao alterar a tela do usuario;
- mas altera a rotina do administrador;
- deve ser documentada com cuidado porque afeta suporte, backup, restore e rollback.

## Metodologia Recomendada

### Estrategia De Liberacao

Usar um nivel principal de liberacao:

- `main`: integracao, homologacao e entrega operacional continua

A liberacao deve sempre sair com:

- lista de commits;
- SHA final esperado;
- checklist de execucao;
- checklist de rollback.

### Quando Usar `cloud-release`

Na fase atual, `cloud-release` nao e mais a trilha operacional padrao.

Ela so deve ser usada se houver um caso excepcional, por exemplo:

- necessidade de segurar um conjunto especial de commits fora da esteira normal;
- operacao de contingencia com terceiro;
- experimento de rollout separado da linha principal.

Fora desses casos, a nuvem deve acompanhar `main`.

## Rotina Local Antes De Enviar Para A Nuvem

### 1. Fechar o lote de mudancas

Registrar os commits localmente e validar o estado:

```bash
git status --short --branch
git log --oneline origin/main..HEAD
```

### 2. Validar no runtime local

No minimo:

```bash
docker compose --env-file "$HOME/power_church_postgres_runtime/env/runtime.env" -f docker-compose.runtime.yml up -d --build
docker compose --env-file "$HOME/power_church_postgres_runtime/env/runtime.env" -f docker-compose.runtime.yml exec -T power-church-django-runtime sh -lc 'cd /app/power_church_django && python manage.py test power_church_django.apps.api'
docker compose --env-file "$HOME/power_church_postgres_runtime/env/runtime.env" -f docker-compose.runtime.yml exec -T power-church-django-runtime sh -lc 'cd /app/power_church_django && python manage.py test power_church_django.apps.contributions.tests'
```

Se a entrega tocar outras areas, rodar tambem os verificadores maiores do projeto.

### 3. Publicar em `main`

Depois da validacao local:

```bash
git push origin main
```

Em seguida:

- aguardar a janela automatica de sincronizacao do servidor;
- validar se o SHA da nuvem acompanhou a `main`.

### 4. Gerar a entrega rastreavel

Mesmo com atualizacao automatica, ainda vale gerar um pacote de referencia com changelog e lista de arquivos:

```bash
./scripts/preparar_entrega_atualizacao_nuvem.sh origin/main HEAD
```

Esse pacote serve para:

- documentar o que mudou;
- facilitar auditoria;
- orientar o administrador em caso de duvida.

## Rotina Do Administrador Da Nuvem

### 1. Registrar a versao atual

Antes de validar uma sincronizacao, registrar:

- data e hora;
- SHA atualmente em producao;
- motivo da atualizacao.

### 2. Fazer backup antes da troca

Se o servidor tiver o repositorio completo:

```bash
./scripts/powerbackup_runtime.sh
```

Se nao tiver, o administrador deve executar o equivalente operacional:

- `pg_dump` do Postgres;
- compactacao do volume persistente `data/`;
- preservacao do `runtime.env`.

### 3. Atualizar o codigo no servidor

O fluxo normal agora e passivo:

- o servidor acompanha `main`;
- a atualizacao ocorre na janela interna de 30 minutos;
- o operador so precisa monitorar e validar.

Se houver necessidade de intervencao manual extraordinaria, a contingencia recomendada e:

```bash
git fetch origin main
git checkout main
git pull --rebase origin main
docker compose --env-file "$POWER_CHURCH_RUNTIME_DIR/env/runtime.env" -f docker-compose.runtime.yml up -d --build
```

Os scripts `deploy_cloud_release.sh` e `rollback_cloud_release.sh` continuam uteis como referencia de operacao controlada, mas nao sao mais o caminho padrao do servidor.

### 4. Verificacao tecnica minima

Checar:

```bash
docker compose --env-file "$POWER_CHURCH_RUNTIME_DIR/env/runtime.env" -f docker-compose.runtime.yml ps
docker compose --env-file "$POWER_CHURCH_RUNTIME_DIR/env/runtime.env" -f docker-compose.runtime.yml logs --tail=80 power-church-django-runtime
curl -I http://127.0.0.1:8001/accounts/login/
curl -i http://127.0.0.1:8001/api/v1/health/
```

## Rotina Temporaria De Recuperacao De Recibos

Quando houver regressao de gatilho e precisarmos recuperar recibos que deveriam ter sido emitidos automaticamente, a estrategia mais segura e:

- reenfileirar os faltantes;
- tratar tudo como recuperacao retroativa;
- drenar a fila com cadencia controlada, no estilo dos extratos.

Isso agora pode ser acoplado temporariamente a uma atualizacao do runtime.

### Como ativar

No `runtime.env` da nuvem, definir temporariamente:

```env
POWER_CHURCH_TEMP_RECEIPT_RECOVERY_ENABLED=true
POWER_CHURCH_TEMP_RECEIPT_RECOVERY_STAMP=receipt_recovery_20260702
POWER_CHURCH_TEMP_RECEIPT_RECOVERY_DRAIN_QUEUE=true
POWER_CHURCH_TEMP_RECEIPT_RECOVERY_LIMIT=40
POWER_CHURCH_TEMP_RECEIPT_RECOVERY_SLEEP_SECONDS=3
POWER_CHURCH_TEMP_RECEIPT_RECOVERY_PAUSE_EVERY=40
POWER_CHURCH_TEMP_RECEIPT_RECOVERY_PAUSE_SECONDS=60
```

### O que acontece na subida

Durante o startup do container Django:

1. roda `python manage.py backfill_automatic_event_receipts`;
2. reenfileira os recibos faltantes apenas para quem tem e-mail;
3. se `DRAIN_QUEUE=true`, roda `python manage.py process_receipt_dispatch_queue --drain` com a cadencia configurada;
4. grava um carimbo em `/app/data/runtime_flags/<STAMP>.done`.

### Garantia de execucao unica

Se o mesmo `STAMP` ja tiver sido executado, a rotina nao roda de novo.

Isso evita:

- duplicidade de envio;
- nova drenagem indevida em reinicios futuros;
- repeticao acidental na mesma versao.

### Desligamento depois da recuperacao

Depois da execucao bem-sucedida:

- conferir o log em `/app/logs/<STAMP>.log`;
- voltar `POWER_CHURCH_TEMP_RECEIPT_RECOVERY_ENABLED=false`;
- manter o `STAMP` registrado para auditoria.

Observacao:

- essa rotina e excepcional;
- nao substitui o fluxo normal restaurado dos envelopes e extratos;
- o uso recomendado e apenas para limpar passivo criado por regressao operacional.

### 5. Smoke test funcional

Minimo recomendado:

- abrir login;
- autenticar;
- abrir dashboard;
- abrir tela de envelopes;
- testar o fluxo afetado pela mudanca.

Para a fila atual de commits, o smoke test funcional obrigatorio continua sendo:

- abrir lote com envelopes pendentes;
- pedir proximo envelope em dois navegadores/operadores diferentes;
- confirmar que um envelope em `em_digitacao` nao cai de novo para o outro operador;
- salvar um envelope simples de dizimo sem rateio;
- salvar um envelope com rateio de duas linhas.

### 6. Registrar o novo estado

Guardar:

- SHA implantado;
- horario da implantacao;
- backup relacionado;
- resultado do smoke test;
- se a entrada veio por janela automatica ou por contingencia manual.

## Rollback

Se a atualizacao falhar:

1. identificar o SHA ou commit problematico em `main`;
2. decidir entre `git revert` do problema ou reposicionamento manual emergencial;
3. rebuildar e subir a versao anterior, se necessario;
4. se houve problema de dados, restaurar o backup correspondente;
5. registrar que o rollback foi executado e em qual SHA.

Padrao preferencial:

```bash
git revert <sha_problematico>
git push origin main
```

Regra pratica:

- preferir `revert` em `main` quando houver tempo e rastreabilidade;
- usar rollback manual de infraestrutura apenas quando a urgencia operacional exigir.

## Cadencia Recomendada

Como o servidor agora sincroniza `main` automaticamente a cada 30 minutos, a cadencia deixa de ser manual.

O cuidado passa a ser outro:

- nao empurrar para `main` algo ainda incompleto;
- tratar cada `push` para `main` como candidato real de entrada na nuvem;
- usar verificacao local antes de cada envio.

## Criterio Para Mandar Atualizacao

Mandar para `main` quando houver pelo menos um destes:

- correcao de bug que afeta operador;
- mudanca de regra operacional;
- ajuste de API consumida por outro componente;
- mudanca de seguranca ou backup;
- grupo coerente de pequenas melhorias ja testadas.

Nao vale a pena mandar para `main`:

- experimento incompleto;
- refatoracao nao validada;
- trabalho ainda em aberto que o servidor nao deveria puxar.

## Resumo Da Metodologia

O fluxo funcional fica:

1. desenvolver e validar localmente;
2. dar `push` para `main`;
3. opcionalmente gerar pacote de referencia com changelog;
4. aguardar a janela automatica do servidor;
5. validar;
6. registrar SHA ativo;
7. manter rollback pronto, preferencialmente por `revert` em `main`.

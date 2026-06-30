# Rotina De Atualizacao Da Nuvem

## Objetivo

Padronizar a atualizacao do Power Church quando o runtime Docker/PostgreSQL **ja esta rodando na nuvem** e a implantacao depende de um terceiro.

A meta desta rotina e:

- evitar deploy por improviso;
- deixar claro **o que mudou**;
- garantir **backup antes da troca**;
- aplicar a atualizacao com **rebuild controlado**;
- validar o ambiente logo depois;
- manter um **caminho de rollback** simples.

## Regra Operacional

Nao usar `git pull` direto em horarios aleatorios nem deploy automatico de toda alteracao local.

O modelo recomendado passa a ser:

- `main` para desenvolvimento e integracao;
- `cloud-release` para o que esta aprovado para a nuvem.

O fluxo recomendado e:

1. consolidar os commits localmente em `main`;
2. validar no runtime Docker local;
3. promover para `cloud-release` apenas o que esta homologado;
4. dar `push` de `cloud-release`;
5. o administrador da nuvem roda a rotina padrao de backup, deploy e smoke test;
6. registrar qual `commit SHA` ficou ativo na nuvem.

## Estado Atual Da Primeira Cloud Release

Para a primeira atualizacao unificada, a branch `cloud-release` foi preparada com:

- `89bd22b` `Add envelope in-progress locking`
- `29a2dc3` `Fix single envelope launch without split`
- `19cc163` `Add cloud-release deployment workflow`

Classificacao pratica:

- `89bd22b`: **impacta runtime**. Altera comportamento de envelopes multioperador.
- `29a2dc3`: **impacta runtime**. Corrige salvamento de envelope simples sem rateio.
- `19cc163`: **impacta operacao de deploy**. Padroniza backup, deploy, rollback e checklist.

Fora desta primeira atualizacao:

- `c3504b8` `Add envelope split coverage tests`

Resumo:

- esta primeira `cloud-release` leva o que o cliente e o operador precisam sentir na nuvem;
- a cobertura adicional de testes ficou em `main`, fora da primeira janela operacional;
- alguns arquivos de teste podem acompanhar commits mistos de runtime, mas nao alteram o comportamento de producao.

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
- normalmente exige `docker compose up -d --build`.

### 2. Mudanca De Schema

Exemplos:

- novos arquivos em `apps/*/migrations/`
- alteracao de models que gere migration

Efeito:

- exige backup obrigatorio antes;
- exige aplicar migrations;
- precisa de atencao extra no rollback.

Observacao:

- hoje o entrypoint do runtime ja roda `migrate` automaticamente.
- mesmo assim, em nuvem, a rotina deve tratar isso como atualizacao sensivel.

### 3. Mudanca So De Teste Ou Documentacao

Exemplos:

- `tests.py`
- `.md`

Efeito:

- nao muda comportamento de producao por si so;
- pode ser implantada junto com outras mudancas;
- isoladamente nao precisa janela urgente.

### 4. Mudanca Em Scripts Operacionais

Exemplos:

- `scripts/powerbackup_runtime.sh`
- `scripts/empacotar_entrega_operador_runtime.sh`
- `deploy/*`

Efeito:

- pode nao alterar a tela do usuario;
- mas altera a rotina do administrador;
- como o container faz `COPY . /app`, esses arquivos devem entrar no pacote da nuvem.

Importante:

- o app ainda importa `scripts/importar_membros_xlsx.py`;
- portanto, o pacote de atualizacao precisa incluir `scripts/`.

## Metodologia Recomendada

### Estrategia De Liberacao

Usar dois niveis de controle:

- `main`: integracao local/equipe
- `cloud-release`: liberacao homologada para o ambiente do cliente

A liberacao deve sempre sair com:

- lista de commits;
- SHA final esperado;
- branch de liberacao atualizada;
- checklist de execucao;
- checklist de rollback.

### Como Promover Para `cloud-release`

Se a nuvem deve acompanhar apenas o que foi homologado, a equipe local nao deve apontar o servidor para `main`.

O recomendado e:

```bash
git checkout cloud-release
git merge --ff-only main
git push origin cloud-release
```

Se a liberacao precisar ser parcial:

```bash
git checkout cloud-release
git cherry-pick <sha1> <sha2> ...
git push origin cloud-release
```

Depois disso, o servidor so precisa puxar `cloud-release`.

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

### 3. Gerar a entrega rastreavel

Mesmo quando a nuvem for atualizar por `git pull`, ainda vale gerar um pacote de referencia com changelog e lista de arquivos:

```bash
./scripts/preparar_entrega_atualizacao_nuvem.sh origin/main HEAD
```

Esse pacote serve para:

- documentar o que mudou;
- facilitar auditoria;
- orientar o administrador em caso de duvida.

## Rotina Do Administrador Da Nuvem

### 1. Registrar a versao atual

Antes de atualizar, registrar:

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

Na nuvem, a rotina recomendada passa a ser um unico comando de deploy controlado:

```bash
./scripts/deploy_cloud_release.sh
```

Esse script:

- valida pre-requisitos;
- executa backup;
- busca a branch `cloud-release`;
- posiciona o codigo no SHA alvo;
- rebuilda o container Django;
- sobe o runtime;
- espera login e healthcheck responderem;
- grava um relatorio e um estado de deploy em `logs/cloud_release/`.

Se o administrador precisar forcar uma ref especifica:

```bash
./scripts/deploy_cloud_release.sh --ref origin/cloud-release
```

### 4. Verificacao tecnica minima

Checar:

```bash
docker compose --env-file "$POWER_CHURCH_RUNTIME_DIR/env/runtime.env" -f docker-compose.runtime.yml ps
docker compose --env-file "$POWER_CHURCH_RUNTIME_DIR/env/runtime.env" -f docker-compose.runtime.yml logs --tail=80 power-church-django-runtime
curl -I http://127.0.0.1:8001/accounts/login/
curl -i http://127.0.0.1:8001/api/v1/health/
```

### 5. Smoke test funcional

Minimo recomendado:

- abrir login;
- autenticar;
- abrir dashboard;
- abrir tela de envelopes;
- testar o fluxo afetado pela release.

Para a fila atual de commits, o smoke test funcional obrigatorio e:

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
- resultado do smoke test.

## Rollback

Se a atualizacao falhar:

1. rodar o rollback do `cloud-release`;
2. rebuildar e subir a versao anterior;
3. se houve problema de dados, restaurar o backup correspondente;
4. registrar que o rollback foi executado e em qual SHA.

Comando padrao:

```bash
./scripts/rollback_cloud_release.sh
```

Se o administrador quiser voltar para um SHA especifico:

```bash
./scripts/rollback_cloud_release.sh --sha <sha_anterior>
```

Esse script:

- executa backup antes do rollback;
- reposiciona a branch `cloud-release` no SHA escolhido;
- rebuilda o container Django;
- sobe o runtime;
- revalida login e healthcheck;
- grava relatorio de rollback em `logs/cloud_release/`.

## Cadencia Recomendada

Como a implantacao depende de terceiro, o melhor modelo nao e deploy a cada commit.

Cadencia sugerida:

- **janela fixa 1 vez por dia** em fase intensa; ou
- **2 a 3 vezes por semana** em fase estavel.

Sempre em lote homologado, nunca por impulso.

## Criterio Para Mandar Atualizacao

Mandar para a nuvem quando houver pelo menos um destes:

- correcao de bug que afeta operador;
- mudanca de regra operacional;
- ajuste de API consumida por outro componente;
- mudanca de seguranca ou backup;
- grupo coerente de pequenas melhorias ja testadas.

Nao vale a pena acionar o administrador apenas para:

- documentacao;
- testes isolados;
- refatoracao sem efeito real na nuvem.

## Resumo Da Metodologia

O fluxo funcional fica:

1. desenvolver e validar localmente;
2. promover apenas o aprovado para `cloud-release`;
3. dar `push` de `cloud-release`;
4. opcionalmente gerar pacote de referencia com changelog;
5. fazer backup na nuvem;
6. rodar `./scripts/deploy_cloud_release.sh`;
7. validar;
8. registrar SHA ativo;
9. manter rollback pronto com `./scripts/rollback_cloud_release.sh`.

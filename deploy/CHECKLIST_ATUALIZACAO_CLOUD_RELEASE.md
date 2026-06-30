# Checklist De Atualizacao Do Main Na Nuvem

Use esta lista toda vez que a nuvem receber atualizacao do Power Church.

Observacao:

- o nome do arquivo foi mantido por compatibilidade historica;
- a rotina abaixo ja foi atualizada para o metodo novo, baseado em `main`.

## Antes Da Atualizacao

- [ ] confirmar a janela de atualizacao com o responsavel
- [ ] confirmar a branch de liberacao: `main`
- [ ] confirmar o SHA alvo informado pela equipe
- [ ] confirmar se a sincronizacao automatica de 30 minutos esta ativa
- [ ] confirmar se a mudanca afeta:
- [ ] envelopes
- [ ] recibos
- [ ] extratos/importacoes
- [ ] API
- [ ] somente testes/documentacao
- [ ] confirmar que existe espaco em disco para backup
- [ ] confirmar que o `runtime.env` atual esta preservado

## Backup Obrigatorio

- [ ] rodar backup antes de qualquer troca
- [ ] guardar o caminho do dump Postgres
- [ ] guardar o caminho do tar.gz dos arquivos persistentes
- [ ] guardar o caminho do manifesto do backup
- [ ] registrar data e hora do backup

Comando padrao:

```bash
./scripts/powerbackup_runtime.sh
```

## Atualizacao Do Codigo

- [ ] confirmar que o worktree do servidor esta limpo
- [ ] confirmar que a branch local do servidor esta em `main`
- [ ] confirmar que o servidor puxou a `main` na janela prevista
- [ ] se nao puxou, executar a contingencia manual

Contingencia manual:

```bash
git fetch origin main
git checkout main
git pull --rebase origin main
docker compose --env-file "$POWER_CHURCH_RUNTIME_DIR/env/runtime.env" -f docker-compose.runtime.yml up -d --build
```

## Rebuild E Subida

- [ ] rebuild do container Django executado, quando aplicavel
- [ ] `docker compose up -d` executado, quando aplicavel
- [ ] migrations rodaram sem erro
- [ ] collectstatic rodou sem erro
- [ ] containers ficaram `Up` e `healthy`

## Verificacao Tecnica Minima

- [ ] `docker compose ps` conferido
- [ ] logs recentes do container Django conferidos
- [ ] login respondeu em `200`
- [ ] `/api/v1/health/` respondeu `200`

## Smoke Test Funcional

- [ ] abrir login
- [ ] autenticar
- [ ] abrir dashboard
- [ ] abrir contribuicoes
- [ ] abrir envelopes

Quando a release afetar envelopes:

- [ ] abrir lote com envelopes pendentes
- [ ] pedir proximo envelope
- [ ] validar `em_digitacao`
- [ ] validar que outro operador nao cai no mesmo envelope
- [ ] salvar envelope simples sem rateio
- [ ] salvar envelope com duas linhas de rateio

Quando a release afetar API:

- [ ] validar `/api/v1/health/`
- [ ] validar token JWT
- [ ] validar `/api/v1/me/`
- [ ] validar `/api/v1/people/`, se aplicavel

## Registro Da Implantacao

- [ ] registrar SHA anterior
- [ ] registrar SHA novo
- [ ] registrar se a entrada veio pela janela automatica ou por contingencia manual
- [ ] registrar horario da implantacao
- [ ] registrar quem executou
- [ ] registrar caminhos dos backups
- [ ] registrar resultado do smoke test

## Quando Fazer Rollback

Fazer rollback se houver qualquer um destes:

- [ ] container nao sobe corretamente
- [ ] login nao responde
- [ ] dashboard principal quebra
- [ ] fluxo operacional critico falha
- [ ] migrations causam comportamento invalido

Padrao preferencial:

```bash
git revert <sha_problematico>
git push origin main
```

Contingencia manual para retorno imediato:

```bash
git checkout main
git reset --hard <sha_anterior_aprovado>
docker compose --env-file "$POWER_CHURCH_RUNTIME_DIR/env/runtime.env" -f docker-compose.runtime.yml up -d --build
```

## Depois Do Rollback

- [ ] confirmar login
- [ ] confirmar healthcheck
- [ ] confirmar fluxo afetado voltou ao normal
- [ ] registrar motivo do rollback
- [ ] registrar SHA restaurado

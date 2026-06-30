# Checklist De Atualizacao Cloud Release

Use esta lista toda vez que a nuvem receber atualizacao do Power Church.

## Antes Da Atualizacao

- [ ] confirmar a janela de atualizacao com o responsavel
- [ ] confirmar a branch de liberacao: `cloud-release`
- [ ] confirmar o SHA alvo informado pela equipe
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
- [ ] confirmar que a branch local do servidor esta em `cloud-release`
- [ ] buscar atualizacoes do remoto
- [ ] atualizar para a ref aprovada

Comando padrao:

```bash
./scripts/deploy_cloud_release.sh
```

Opcional, para ref especifica:

```bash
./scripts/deploy_cloud_release.sh --ref origin/cloud-release
```

## Rebuild E Subida

- [ ] rebuild do container Django executado
- [ ] `docker compose up -d` executado
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

Comando padrao:

```bash
./scripts/rollback_cloud_release.sh
```

Rollback para SHA especifico:

```bash
./scripts/rollback_cloud_release.sh --sha <sha_anterior>
```

## Depois Do Rollback

- [ ] confirmar login
- [ ] confirmar healthcheck
- [ ] confirmar fluxo afetado voltou ao normal
- [ ] registrar motivo do rollback
- [ ] registrar SHA restaurado

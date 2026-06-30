# Primeira Cloud Release

## Objetivo

Executar **uma unica atualizacao controlada** na nuvem, levando:

- correcoes operacionais de envelopes;
- trava multioperador com `em_digitacao`;
- rotina padronizada de backup, deploy e rollback.

## Branch De Liberacao

- branch: `cloud-release`
- base: `origin/main`

## Commits Incluidos

- `89bd22b` `Add envelope in-progress locking`
- `29a2dc3` `Fix single envelope launch without split`
- `19cc163` `Add cloud-release deployment workflow`
- `6b6cab6` `Add envelope split coverage tests`

Observacao:

- esse ultimo commit nao muda comportamento de producao por si so;
- ele foi incluido para permitir validacao real do rateio no proprio ambiente da nuvem.

## O Que O Operador Da Nuvem Deve Validar

### Tecnico

- login publico responde
- `/api/v1/health/` responde `200`
- container Django sobe sem erro
- migrations e `collectstatic` concluem
- containers ficam `healthy`

### Funcional

- abrir lote de envelopes
- pedir proximo envelope em duas sessoes/operadores
- confirmar que o segundo operador nao cai no mesmo envelope
- salvar envelope simples de dizimo sem rateio
- salvar envelope com duas linhas de rateio

## Comandos Da Primeira Atualizacao

No servidor:

```bash
git fetch origin cloud-release
./scripts/deploy_cloud_release.sh --ref origin/cloud-release
```

Se houver falha:

```bash
./scripts/rollback_cloud_release.sh
```

## Evidencia Esperada De Retorno

O administrador deve devolver:

- SHA anterior
- SHA novo
- caminho dos backups gerados
- resultado do smoke test
- qualquer erro ou observacao encontrada

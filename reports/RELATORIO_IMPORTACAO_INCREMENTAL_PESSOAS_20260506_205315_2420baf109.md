# Relatorio De Importacao Incremental De Pessoas

## 1. Resultado Geral

- Arquivo importado: `2026-05-06_gestao_de_membresia_usuarios_2026_05_04_1054_2420baf109.xlsx`
- Aba: `Dados`
- Banco atualizado: `/Users/piraginejr/Documents/New project/Teste/Power Church/data/power_church_membros_importado.db`
- Backup antes da importacao: `/Users/piraginejr/Documents/New project/Teste/Power Church/data/backups/power_church_membros_importado_before_incremental_people_import_20260506_205315.db`
- Lote de importacao: `3`
- Registros lidos: `25`
- Pessoas ativas no banco apos importacao: `1556`

## 2. Politica Aplicada

- Importacao complementar/incremental.
- Nenhuma ficha existente foi apagada.
- Fichas existentes foram reconhecidas por CPF valido, numero de membro ou nome completo + data de nascimento.
- Em ficha existente, o importador preencheu apenas campos vazios e adicionou contatos/historicos/campos complementares faltantes.
- Mudanca de status, conflito de chaves ou match fraco foram enviados para pendencia de auditoria.
- CPF invalido/duplicado nao foi usado como chave automatica.

## 3. Linhas Por Status

| Item | Quantidade |
|---|---:|
| importado | 25 |

## 4. Acoes Executadas

| Item | Quantidade |
|---|---:|
| campos_personalizados_adicionados | 188 |
| criados | 25 |
| perfis_adicionados | 25 |
| enderecos_adicionados | 24 |
| contatos_adicionados | 22 |

## 5. Pendencias

Pendencias aqui nao significam falha de importacao. Elas indicam itens que precisam de revisao antes de uma alteracao sensivel.

### Por Severidade

Nenhum registro.

### Por Tipo

Nenhum registro.

## 6. Observacao De Privacidade

Este relatorio nao lista nomes, CPFs completos, telefones ou enderecos.

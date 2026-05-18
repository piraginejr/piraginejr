# Relatorio De Importacao De Membros

## 1. Resultado Geral

- Arquivo importado: `Gestao_de_Membresia_Membros-2026_04_20_1659.xlsx`
- Aba: `Dados`
- Banco gerado: `/Users/piraginejr/Documents/New project/Teste/Power Church/data/power_church_membros_importado.db`
- Lote de importacao: `1`
- Registros lidos: `1363`

## 2. Registros Gerados

| Item | Quantidade |
|---|---:|
| valores_campos_personalizados | 16106 |
| pessoa_contatos | 3092 |
| pessoa_perfis | 1492 |
| pessoa_historico | 1454 |
| pessoas | 1363 |
| pessoa_enderecos | 1347 |
| import_pendencias | 115 |
| campos_personalizados | 20 |

## 3. Status Operacional

| Item | Quantidade |
|---|---:|
| membro_ativo | 1347 |
| membro_inativo | 16 |

## 4. Perfis Criados

| Item | Quantidade |
|---|---:|
| membro | 1363 |
| lider | 112 |
| pastor | 17 |

## 5. Contatos

| Item | Quantidade |
|---|---:|
| celular | 1197 |
| email | 1045 |
| telefone | 850 |

## 6. Historico

| Item | Quantidade |
|---|---:|
| entrada_membresia | 629 |
| batismo | 535 |
| aceitou_jesus | 274 |
| inatividade | 16 |

## 7. Campos Personalizados Mais Preenchidos

| Item | Quantidade |
|---|---:|
| aceitou_jesus_contexto | 1363 |
| batizado | 1363 |
| data_criacao_origem | 1363 |
| recem_convertido | 1363 |
| status_origem | 1363 |
| tipo_batismo | 1363 |
| nacionalidade | 1185 |
| naturalidade | 1163 |
| forma_entrada | 924 |
| orgao_emissor_rg | 853 |
| ocupacao | 803 |
| tipo_sanguineo | 743 |
| igreja_origem | 692 |
| escolaridade | 584 |
| uf_rg | 416 |
| data_casamento | 339 |
| criado_por_origem | 164 |
| entrevistado_por | 54 |
| cpf_original_revisao | 8 |

## 8. Pendencias E Revisoes

Pendencias aqui nao significam falha de importacao. Elas indicam itens que devem ser revisados ou conferidos depois.

### Por Severidade

| Item | Quantidade |
|---|---:|
| info | 102 |
| aviso | 13 |

### Por Tipo

| Item | Quantidade |
|---|---:|
| menor_16 | 42 |
| data_invalida | 31 |
| membro_inativo_sem_voto | 16 |
| email_invalido | 12 |
| cpf_invalido | 6 |
| nome_repetido | 4 |
| cpf_duplicado | 2 |
| idade_suspeita | 1 |
| numero_membro_vazio | 1 |

## 9. Normalizacoes Aplicadas

| Item | Quantidade |
|---|---:|
| importados | 1363 |
| numero_endereco_normalizado | 741 |
| estado_civil_tratado_como_vazio | 410 |

## 10. Decisoes Aplicadas

- CPF valido foi gravado em `pessoas.cpf`.
- CPF invalido ou duplicado nao foi gravado como CPF principal; foi preservado em campo personalizado para revisao.
- CPF vazio foi aceito.
- Pessoa com Data/Motivo de inatividade recebeu status `membro_inativo`, mantendo perfil `membro`.
- `E recem-convertido?` foi importado como campo acessorio.
- `Tipo de batismo` foi importado como campo acessorio.
- `Estado Civil = Escolha unica` foi tratado como vazio.
- Datas `1/1/1` foram tratadas como invalidas e viraram pendencia.
- Numeros de endereco terminados em `.0` foram normalizados.

## 11. Observacao De Privacidade

Este relatorio nao lista nomes, CPFs completos, telefones ou enderecos.

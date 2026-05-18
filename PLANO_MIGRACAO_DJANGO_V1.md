# Plano De Migracao Para Django V1

## 1. Decisao

A migracao para Django deve comecar somente depois da camada atual estar homologada e com o nucleo de negocio separado do prototipo web.

Nesta fase, o Power Church continua rodando no Mac, mas as regras principais passam a viver em `power_church_core/`, para que o futuro projeto Django reaproveite o que ja foi validado com dados reais.

Status atual:

- projeto paralelo em `power_church_django/` ja possui dashboard, pessoas, importacao de pessoas, contribuintes estrategicos, contribuicoes, recibos, importacoes bancarias, regras de centavos, auditoria e relatorios;
- os fluxos operacionais principais ja sao verificados por `scripts/verificar_paridade_django.py`;
- o prototipo `power_church_demo.py` permanece como fallback temporario;
- usuarios/permissoes finas, PostgreSQL, OCR e novos bancos ficam para blocos posteriores;
- por decisao operacional, usuarios/permissoes finas serao definidos depois que o cliente enxergar o sistema completo, incluindo recebimentos bancarios e envelopes em especie.

## 2. Fronteira Atual

### Nucleo reutilizavel

O Django deve reaproveitar diretamente:

- `power_church_core/normalization.py`: nomes, documentos, CPF/CNPJ, codigos de centavos;
- `power_church_core/formatting.py`: datas, moeda e competencia;
- `power_church_core/matching.py`: motor de associacao de pessoas e aliases financeiros;
- `power_church_core/designations.py`: regras puras de centavos, contas e campanhas;
- `power_church_core/banking.py`: contratos de layouts bancarios;
- `power_church_core/bank_parsers.py`: parsers Bradesco, Sicoob Recebimentos e Santander;
- `power_church_core/bank_lots.py`: planejamento de lote, assinatura, fingerprint e regra de revisao;
- `power_church_core/pdf_text.py`: adaptador de extracao de PDF;
- `power_church_core/signatures.py`: assinaturas de duplicidade;
- `power_church_core/contributors.py`: classificacao de contribuinte e siglas.

### Prototipo atual

O arquivo `power_church_demo.py` continua temporariamente responsavel por:

- servidor HTTP local;
- telas HTML;
- acesso direto ao SQLite;
- persistencia de lotes, movimentos, pessoas, contribuicoes e auditoria.

### Futuro Django

O projeto Django devera substituir gradualmente ou ja substitui operacionalmente:

- rotas e telas;
- autenticacao e permissoes;
- formularios;
- modelos ORM;
- admin;
- tarefas de importacao e reprocessamento;
- relatorios com controle por perfil.

### Criterio operacional atual

O Django pode ser considerado interface principal quando:

- `python3 scripts/verificar_funcionalidade_total.py --report` passa;
- `scripts/verificar_paridade_django.py` retorna `OK`;
- nao existe contribuicao bancaria ativa com valor `<= 0`;
- os itens marcados como `ADIADO` no relatorio de paridade sao conscientemente mantidos fora do ciclo atual.

## 3. Ordem Recomendada

1. Congelar uma base homologada com `scripts/verificar_funcionalidade_total.py --report`.
2. Usar o Django como interface operacional principal enquanto o prototipo fica como fallback.
3. Completar a paridade operacional que ainda faltar no Django, sem criar novas regras de permissao no chute.
4. Iniciar o modulo de OCR/entrada assistida de envelopes manuscritos, pois ele fecha a peca ausente do sistema de recebimentos.
5. Com todos os fluxos operacionais visiveis, levantar com o cliente os perfis reais de usuarios, niveis de acesso, impressao, exportacao e alteracao.
6. Depois migrar infraestrutura: PostgreSQL, servidor/nuvem e novos bancos.
7. So apos estabilizar em servidor, planejar retirada definitiva do prototipo.

## 4. Criterio De Prontidao

Antes de iniciar o Django, deve passar:

```bash
python3 scripts/verificar_funcionalidade_total.py --report
python3 scripts/verificar_prontidao_django.py --report
```

Alertas esperados nesta fase:

- `PyMuPDF` pode ainda nao estar instalado no Mac;
- `Swift/PDFKit` ainda pode existir como provedor local temporario;
- `power_church_demo.py` ainda e grande.

Esses alertas nao bloqueiam o inicio do Django, desde que os parsers reais, os lotes, os relatorios e a estabilidade estejam `OK`.

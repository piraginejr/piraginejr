# Power Church

Projeto exclusivo para a plataforma modular de gestao de igrejas.

Este diretorio foi separado do Power Finance V1 para evitar mistura de arquivos, banco de dados e decisoes arquiteturais.

Documento inicial:

- `ARQUITETURA_POWER_CHURCH_V0.md`
- `BANCO_MODULAR_V0.md`
- `GUIA_IMPORTACOES_BANCARIAS_V1.md`
- `CHECKLIST_NOVO_BANCO_IMPORTACOES.md`
- `PLANO_HOSPEDAGEM_MIGRACAO_E_OCR_V1.md`
- `PLANO_TRANSICAO_POWER_CHURCH_V1.md`
- `MATRIZ_HOMOLOGACAO_V1.md`
- `ANALISE_PILOTO_EXTRATO_SICOOB_JAN26.md`
- `schema_power_church_v0.sql`
- `PLANO_PRE_DADOS_CLIENTE.md`
- `CHECKLIST_DADOS_CLIENTE.md`
- `ANALISE_PLANILHA_MEMBROS_2026_04_20.md`
- `scripts/importar_membros_xlsx.py`
- `data/power_church_membros_importado.db`
- `reports/RELATORIO_IMPORTACAO_MEMBROS_2026_04_20.md`
- `power_church_demo.py`
- `Abrir Power Church.command`
- `Abrir Power Church Django.command`

## Abrir o Django no Mac

Para abrir o Django sem depender do Codex, de dois cliques em:

- `Abrir Power Church Django.command`

O atalho sobe o servidor local em `http://127.0.0.1:63620/`, abre o navegador automaticamente e usa o banco atual em `data/power_church_membros_importado.db`. Para encerrar, volte na janela do Terminal aberta pelo atalho e pressione `CONTROL+C`.

Proximo passo planejado:

- manter a versao atual demonstravel com `scripts/verificar_estabilidade_demo.py`;
- rodar `scripts/verificar_prontidao_transicao.py` antes de cada etapa estrutural;
- separar o nucleo de negocio do servidor atual;
- preparar portabilidade de PDF;
- iniciar Django/PostgreSQL em paralelo, sem big bang.

Regra de seguranca daqui para frente:

- toda correcao implementada deve ganhar uma sentinela no script de checagem correspondente, para evitar regressao silenciosa.

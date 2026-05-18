PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS organizacoes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL,
    nome_fantasia TEXT,
    cnpj TEXT,
    tipo TEXT NOT NULL DEFAULT 'igreja',
    status TEXT NOT NULL DEFAULT 'ativa',
    observacoes TEXT,
    criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em TEXT
);

CREATE TABLE IF NOT EXISTS unidades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organizacao_id INTEGER NOT NULL,
    nome TEXT NOT NULL,
    tipo TEXT NOT NULL DEFAULT 'sede',
    cidade TEXT,
    uf TEXT,
    ativa INTEGER NOT NULL DEFAULT 1,
    criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em TEXT,
    FOREIGN KEY (organizacao_id) REFERENCES organizacoes(id)
);

CREATE TABLE IF NOT EXISTS usuarios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    senha_hash TEXT,
    ativo INTEGER NOT NULL DEFAULT 1,
    criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em TEXT
);

CREATE TABLE IF NOT EXISTS perfis_acesso (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organizacao_id INTEGER,
    nome TEXT NOT NULL,
    descricao TEXT,
    padrao INTEGER NOT NULL DEFAULT 0,
    ativo INTEGER NOT NULL DEFAULT 1,
    criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em TEXT,
    FOREIGN KEY (organizacao_id) REFERENCES organizacoes(id)
);

CREATE TABLE IF NOT EXISTS permissoes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    codigo TEXT NOT NULL UNIQUE,
    modulo TEXT NOT NULL,
    descricao TEXT
);

CREATE TABLE IF NOT EXISTS perfil_permissoes (
    perfil_acesso_id INTEGER NOT NULL,
    permissao_id INTEGER NOT NULL,
    PRIMARY KEY (perfil_acesso_id, permissao_id),
    FOREIGN KEY (perfil_acesso_id) REFERENCES perfis_acesso(id),
    FOREIGN KEY (permissao_id) REFERENCES permissoes(id)
);

CREATE TABLE IF NOT EXISTS usuarios_organizacoes (
    usuario_id INTEGER NOT NULL,
    organizacao_id INTEGER NOT NULL,
    perfil_acesso_id INTEGER,
    ativo INTEGER NOT NULL DEFAULT 1,
    criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (usuario_id, organizacao_id),
    FOREIGN KEY (usuario_id) REFERENCES usuarios(id),
    FOREIGN KEY (organizacao_id) REFERENCES organizacoes(id),
    FOREIGN KEY (perfil_acesso_id) REFERENCES perfis_acesso(id)
);

CREATE TABLE IF NOT EXISTS modulos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    codigo TEXT NOT NULL UNIQUE,
    nome TEXT NOT NULL,
    descricao TEXT,
    ordem INTEGER NOT NULL DEFAULT 0,
    ativo INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS modulos_organizacao (
    organizacao_id INTEGER NOT NULL,
    modulo_id INTEGER NOT NULL,
    ativo INTEGER NOT NULL DEFAULT 0,
    plano TEXT,
    data_ativacao TEXT,
    data_cancelamento TEXT,
    PRIMARY KEY (organizacao_id, modulo_id),
    FOREIGN KEY (organizacao_id) REFERENCES organizacoes(id),
    FOREIGN KEY (modulo_id) REFERENCES modulos(id)
);

CREATE TABLE IF NOT EXISTS configuracoes_organizacao (
    organizacao_id INTEGER NOT NULL,
    chave TEXT NOT NULL,
    valor TEXT,
    atualizado_em TEXT,
    PRIMARY KEY (organizacao_id, chave),
    FOREIGN KEY (organizacao_id) REFERENCES organizacoes(id)
);

CREATE TABLE IF NOT EXISTS documentos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organizacao_id INTEGER NOT NULL,
    unidade_id INTEGER,
    registro_tipo TEXT,
    registro_id INTEGER,
    nome_arquivo TEXT NOT NULL,
    caminho TEXT,
    url TEXT,
    mime_type TEXT,
    tamanho_bytes INTEGER,
    hash_arquivo TEXT,
    criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (organizacao_id) REFERENCES organizacoes(id),
    FOREIGN KEY (unidade_id) REFERENCES unidades(id)
);

CREATE TABLE IF NOT EXISTS auditoria (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organizacao_id INTEGER,
    usuario_id INTEGER,
    acao TEXT NOT NULL,
    tabela TEXT NOT NULL,
    registro_id INTEGER,
    dados_antes_json TEXT,
    dados_depois_json TEXT,
    criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (organizacao_id) REFERENCES organizacoes(id),
    FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
);

CREATE TABLE IF NOT EXISTS import_lotes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organizacao_id INTEGER NOT NULL,
    unidade_id INTEGER,
    tipo_importacao TEXT NOT NULL,
    arquivo_nome TEXT NOT NULL,
    arquivo_hash TEXT,
    status TEXT NOT NULL DEFAULT 'preparado',
    total_linhas INTEGER NOT NULL DEFAULT 0,
    linhas_importadas INTEGER NOT NULL DEFAULT 0,
    linhas_ignoradas INTEGER NOT NULL DEFAULT 0,
    linhas_com_erro INTEGER NOT NULL DEFAULT 0,
    criado_por_usuario_id INTEGER,
    criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    confirmado_em TEXT,
    desfeito_em TEXT,
    FOREIGN KEY (organizacao_id) REFERENCES organizacoes(id),
    FOREIGN KEY (unidade_id) REFERENCES unidades(id),
    FOREIGN KEY (criado_por_usuario_id) REFERENCES usuarios(id)
);

CREATE TABLE IF NOT EXISTS import_abas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lote_id INTEGER NOT NULL,
    nome_aba TEXT NOT NULL,
    competencia_sugerida TEXT,
    total_linhas INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (lote_id) REFERENCES import_lotes(id)
);

CREATE TABLE IF NOT EXISTS import_mapeamentos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lote_id INTEGER NOT NULL,
    coluna_origem TEXT NOT NULL,
    campo_destino TEXT,
    acao TEXT NOT NULL,
    campo_personalizado_id INTEGER,
    configuracao_json TEXT,
    FOREIGN KEY (lote_id) REFERENCES import_lotes(id)
);

CREATE TABLE IF NOT EXISTS import_linhas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lote_id INTEGER NOT NULL,
    aba_id INTEGER,
    numero_linha INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pendente',
    dados_originais_json TEXT NOT NULL,
    dados_normalizados_json TEXT,
    registro_tipo TEXT,
    registro_id INTEGER,
    criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (lote_id) REFERENCES import_lotes(id),
    FOREIGN KEY (aba_id) REFERENCES import_abas(id)
);

CREATE TABLE IF NOT EXISTS import_pendencias (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lote_id INTEGER NOT NULL,
    linha_id INTEGER,
    tipo TEXT NOT NULL,
    severidade TEXT NOT NULL DEFAULT 'aviso',
    descricao TEXT NOT NULL,
    acao_sugerida TEXT,
    resolucao TEXT,
    resolvido INTEGER NOT NULL DEFAULT 0,
    resolvido_em TEXT,
    FOREIGN KEY (lote_id) REFERENCES import_lotes(id),
    FOREIGN KEY (linha_id) REFERENCES import_linhas(id)
);

CREATE TABLE IF NOT EXISTS pessoas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organizacao_id INTEGER NOT NULL,
    unidade_preferencial_id INTEGER,
    codigo_interno TEXT,
    nome TEXT NOT NULL,
    nome_social TEXT,
    cpf TEXT,
    rg TEXT,
    data_nascimento TEXT,
    sexo TEXT,
    estado_civil TEXT,
    email_principal TEXT,
    telefone_principal TEXT,
    whatsapp_principal TEXT,
    status TEXT NOT NULL DEFAULT 'ativo',
    arquivo_morto INTEGER NOT NULL DEFAULT 0,
    observacoes TEXT,
    import_lote_id INTEGER,
    ativo INTEGER NOT NULL DEFAULT 1,
    criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em TEXT,
    FOREIGN KEY (organizacao_id) REFERENCES organizacoes(id),
    FOREIGN KEY (unidade_preferencial_id) REFERENCES unidades(id),
    FOREIGN KEY (import_lote_id) REFERENCES import_lotes(id)
);

CREATE TABLE IF NOT EXISTS pessoa_perfis (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organizacao_id INTEGER NOT NULL,
    pessoa_id INTEGER NOT NULL,
    perfil TEXT NOT NULL,
    data_inicio TEXT,
    data_fim TEXT,
    ativo INTEGER NOT NULL DEFAULT 1,
    observacoes TEXT,
    criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (organizacao_id) REFERENCES organizacoes(id),
    FOREIGN KEY (pessoa_id) REFERENCES pessoas(id)
);

CREATE TABLE IF NOT EXISTS pessoa_contatos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organizacao_id INTEGER NOT NULL,
    pessoa_id INTEGER NOT NULL,
    tipo TEXT NOT NULL,
    valor TEXT NOT NULL,
    principal INTEGER NOT NULL DEFAULT 0,
    observacoes TEXT,
    criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (organizacao_id) REFERENCES organizacoes(id),
    FOREIGN KEY (pessoa_id) REFERENCES pessoas(id)
);

CREATE TABLE IF NOT EXISTS pessoa_enderecos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organizacao_id INTEGER NOT NULL,
    pessoa_id INTEGER NOT NULL,
    tipo TEXT NOT NULL DEFAULT 'residencial',
    cep TEXT,
    logradouro TEXT,
    numero TEXT,
    complemento TEXT,
    bairro TEXT,
    cidade TEXT,
    uf TEXT,
    principal INTEGER NOT NULL DEFAULT 0,
    criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em TEXT,
    FOREIGN KEY (organizacao_id) REFERENCES organizacoes(id),
    FOREIGN KEY (pessoa_id) REFERENCES pessoas(id)
);

CREATE TABLE IF NOT EXISTS pessoa_relacionamentos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organizacao_id INTEGER NOT NULL,
    pessoa_id INTEGER NOT NULL,
    pessoa_relacionada_id INTEGER NOT NULL,
    tipo_relacionamento TEXT NOT NULL,
    observacoes TEXT,
    ativo INTEGER NOT NULL DEFAULT 1,
    criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (organizacao_id) REFERENCES organizacoes(id),
    FOREIGN KEY (pessoa_id) REFERENCES pessoas(id),
    FOREIGN KEY (pessoa_relacionada_id) REFERENCES pessoas(id)
);

CREATE TABLE IF NOT EXISTS pessoa_historico (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organizacao_id INTEGER NOT NULL,
    pessoa_id INTEGER NOT NULL,
    tipo_evento TEXT NOT NULL,
    data_evento TEXT,
    titulo TEXT,
    descricao TEXT,
    origem TEXT,
    destino TEXT,
    responsavel_pessoa_id INTEGER,
    import_lote_id INTEGER,
    criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (organizacao_id) REFERENCES organizacoes(id),
    FOREIGN KEY (pessoa_id) REFERENCES pessoas(id),
    FOREIGN KEY (responsavel_pessoa_id) REFERENCES pessoas(id),
    FOREIGN KEY (import_lote_id) REFERENCES import_lotes(id)
);

CREATE TABLE IF NOT EXISTS campos_personalizados (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organizacao_id INTEGER NOT NULL,
    modulo TEXT NOT NULL,
    registro_tipo TEXT NOT NULL,
    nome TEXT NOT NULL,
    chave TEXT NOT NULL,
    tipo TEXT NOT NULL,
    opcoes_json TEXT,
    obrigatorio INTEGER NOT NULL DEFAULT 0,
    visivel_no_cadastro INTEGER NOT NULL DEFAULT 1,
    usar_em_relatorios INTEGER NOT NULL DEFAULT 0,
    ativo INTEGER NOT NULL DEFAULT 1,
    criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em TEXT,
    FOREIGN KEY (organizacao_id) REFERENCES organizacoes(id)
);

CREATE TABLE IF NOT EXISTS valores_campos_personalizados (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organizacao_id INTEGER NOT NULL,
    campo_id INTEGER NOT NULL,
    registro_tipo TEXT NOT NULL,
    registro_id INTEGER NOT NULL,
    valor_texto TEXT,
    valor_numero REAL,
    valor_data TEXT,
    valor_json TEXT,
    criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em TEXT,
    FOREIGN KEY (organizacao_id) REFERENCES organizacoes(id),
    FOREIGN KEY (campo_id) REFERENCES campos_personalizados(id)
);

CREATE TABLE IF NOT EXISTS plano_contas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organizacao_id INTEGER NOT NULL,
    codigo TEXT NOT NULL,
    nome TEXT NOT NULL,
    pai_id INTEGER,
    nivel INTEGER NOT NULL DEFAULT 1,
    tipo TEXT NOT NULL,
    grupo_estrategico TEXT,
    aceita_lancamento INTEGER NOT NULL DEFAULT 1,
    ativo INTEGER NOT NULL DEFAULT 1,
    criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em TEXT,
    FOREIGN KEY (organizacao_id) REFERENCES organizacoes(id),
    FOREIGN KEY (pai_id) REFERENCES plano_contas(id)
);

CREATE TABLE IF NOT EXISTS centros_custo (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organizacao_id INTEGER NOT NULL,
    codigo TEXT NOT NULL,
    nome TEXT NOT NULL,
    pai_id INTEGER,
    ativo INTEGER NOT NULL DEFAULT 1,
    criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em TEXT,
    FOREIGN KEY (organizacao_id) REFERENCES organizacoes(id),
    FOREIGN KEY (pai_id) REFERENCES centros_custo(id)
);

CREATE TABLE IF NOT EXISTS contas_financeiras (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organizacao_id INTEGER NOT NULL,
    nome TEXT NOT NULL,
    tipo TEXT NOT NULL DEFAULT 'caixa',
    banco TEXT,
    agencia TEXT,
    conta TEXT,
    saldo_inicial REAL NOT NULL DEFAULT 0,
    data_saldo_inicial TEXT,
    ativa INTEGER NOT NULL DEFAULT 1,
    criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em TEXT,
    FOREIGN KEY (organizacao_id) REFERENCES organizacoes(id)
);

CREATE TABLE IF NOT EXISTS tipos_contribuicao (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organizacao_id INTEGER NOT NULL,
    codigo TEXT NOT NULL,
    nome TEXT NOT NULL,
    exige_pessoa INTEGER NOT NULL DEFAULT 0,
    natureza_receita TEXT NOT NULL DEFAULT 'receita_operacional',
    plano_conta_id INTEGER,
    ativo INTEGER NOT NULL DEFAULT 1,
    criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em TEXT,
    FOREIGN KEY (organizacao_id) REFERENCES organizacoes(id),
    FOREIGN KEY (plano_conta_id) REFERENCES plano_contas(id)
);

CREATE TABLE IF NOT EXISTS formas_recebimento (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organizacao_id INTEGER NOT NULL,
    codigo TEXT NOT NULL,
    nome TEXT NOT NULL,
    ativo INTEGER NOT NULL DEFAULT 1,
    criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (organizacao_id) REFERENCES organizacoes(id)
);

CREATE TABLE IF NOT EXISTS campanhas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organizacao_id INTEGER NOT NULL,
    nome TEXT NOT NULL,
    descricao TEXT,
    data_inicio TEXT,
    data_fim TEXT,
    status TEXT NOT NULL DEFAULT 'ativa',
    plano_conta_id INTEGER,
    centro_custo_id INTEGER,
    criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em TEXT,
    FOREIGN KEY (organizacao_id) REFERENCES organizacoes(id),
    FOREIGN KEY (plano_conta_id) REFERENCES plano_contas(id),
    FOREIGN KEY (centro_custo_id) REFERENCES centros_custo(id)
);

CREATE TABLE IF NOT EXISTS contribuicoes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organizacao_id INTEGER NOT NULL,
    unidade_id INTEGER,
    pessoa_id INTEGER,
    tipo_contribuicao_id INTEGER NOT NULL,
    campanha_id INTEGER,
    data_recebimento TEXT NOT NULL,
    competencia TEXT NOT NULL,
    competencia_ordem INTEGER NOT NULL,
    valor REAL NOT NULL,
    forma_recebimento_id INTEGER,
    conta_financeira_id INTEGER,
    observacoes TEXT,
    import_lote_id INTEGER,
    ativo INTEGER NOT NULL DEFAULT 1,
    criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em TEXT,
    FOREIGN KEY (organizacao_id) REFERENCES organizacoes(id),
    FOREIGN KEY (unidade_id) REFERENCES unidades(id),
    FOREIGN KEY (pessoa_id) REFERENCES pessoas(id),
    FOREIGN KEY (tipo_contribuicao_id) REFERENCES tipos_contribuicao(id),
    FOREIGN KEY (campanha_id) REFERENCES campanhas(id),
    FOREIGN KEY (forma_recebimento_id) REFERENCES formas_recebimento(id),
    FOREIGN KEY (conta_financeira_id) REFERENCES contas_financeiras(id),
    FOREIGN KEY (import_lote_id) REFERENCES import_lotes(id)
);

CREATE TABLE IF NOT EXISTS recibos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organizacao_id INTEGER NOT NULL,
    pessoa_id INTEGER NOT NULL,
    numero TEXT NOT NULL,
    data_emissao TEXT NOT NULL,
    periodo_inicio TEXT,
    periodo_fim TEXT,
    valor_total REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'emitido',
    arquivo_path TEXT,
    observacoes TEXT,
    criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    cancelado_em TEXT,
    FOREIGN KEY (organizacao_id) REFERENCES organizacoes(id),
    FOREIGN KEY (pessoa_id) REFERENCES pessoas(id)
);

CREATE TABLE IF NOT EXISTS recibo_itens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recibo_id INTEGER NOT NULL,
    contribuicao_id INTEGER NOT NULL,
    valor REAL NOT NULL,
    FOREIGN KEY (recibo_id) REFERENCES recibos(id),
    FOREIGN KEY (contribuicao_id) REFERENCES contribuicoes(id)
);

CREATE TABLE IF NOT EXISTS envelope_lotes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organizacao_id INTEGER NOT NULL,
    competencia TEXT NOT NULL,
    competencia_ordem INTEGER NOT NULL,
    nome TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'aberto',
    total_envelopes INTEGER NOT NULL DEFAULT 0,
    total_valor REAL NOT NULL DEFAULT 0,
    caminho_pasta TEXT,
    observacoes TEXT,
    criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em TEXT,
    FOREIGN KEY (organizacao_id) REFERENCES organizacoes(id)
);

CREATE TABLE IF NOT EXISTS envelopes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lote_id INTEGER NOT NULL,
    organizacao_id INTEGER NOT NULL,
    competencia TEXT NOT NULL,
    competencia_ordem INTEGER NOT NULL,
    data_recebimento TEXT NOT NULL,
    total_informado REAL NOT NULL,
    total_linhas REAL NOT NULL DEFAULT 0,
    nome_informado TEXT,
    telefone_informado TEXT,
    endereco_informado TEXT,
    pessoa_id INTEGER,
    contribuinte_id INTEGER,
    forma_recebimento_id INTEGER,
    origem_operacional TEXT,
    caminho_imagem TEXT,
    nome_arquivo_original TEXT,
    imagem_hash TEXT,
    imagem_content_type TEXT,
    imagem_tamanho INTEGER,
    status TEXT NOT NULL DEFAULT 'lancado',
    observacoes TEXT,
    justificativa TEXT,
    ocr_json TEXT,
    ativo INTEGER NOT NULL DEFAULT 1,
    criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em TEXT,
    FOREIGN KEY (lote_id) REFERENCES envelope_lotes(id),
    FOREIGN KEY (organizacao_id) REFERENCES organizacoes(id),
    FOREIGN KEY (pessoa_id) REFERENCES pessoas(id),
    FOREIGN KEY (contribuinte_id) REFERENCES contribuintes(id),
    FOREIGN KEY (forma_recebimento_id) REFERENCES formas_recebimento(id)
);

CREATE TABLE IF NOT EXISTS envelope_itens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    envelope_id INTEGER NOT NULL,
    organizacao_id INTEGER NOT NULL,
    pessoa_id INTEGER,
    contribuinte_id INTEGER,
    tipo_contribuicao_id INTEGER NOT NULL,
    campanha_id INTEGER,
    valor REAL NOT NULL,
    observacoes TEXT,
    contribuicao_id INTEGER,
    ativo INTEGER NOT NULL DEFAULT 1,
    criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em TEXT,
    FOREIGN KEY (envelope_id) REFERENCES envelopes(id),
    FOREIGN KEY (organizacao_id) REFERENCES organizacoes(id),
    FOREIGN KEY (pessoa_id) REFERENCES pessoas(id),
    FOREIGN KEY (contribuinte_id) REFERENCES contribuintes(id),
    FOREIGN KEY (tipo_contribuicao_id) REFERENCES tipos_contribuicao(id),
    FOREIGN KEY (campanha_id) REFERENCES campanhas(id),
    FOREIGN KEY (contribuicao_id) REFERENCES contribuicoes(id)
);

CREATE TABLE IF NOT EXISTS lancamentos_financeiros (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organizacao_id INTEGER NOT NULL,
    unidade_id INTEGER,
    tipo TEXT NOT NULL,
    origem_tipo TEXT NOT NULL DEFAULT 'manual',
    origem_id INTEGER,
    entidade_pessoa_id INTEGER,
    plano_conta_id INTEGER,
    centro_custo_id INTEGER,
    conta_financeira_id INTEGER,
    competencia TEXT NOT NULL,
    competencia_ordem INTEGER NOT NULL,
    data_emissao TEXT,
    data_vencimento TEXT,
    data_pagamento TEXT,
    valor REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'pendente',
    forma_pagamento TEXT,
    descricao TEXT NOT NULL,
    documento_id INTEGER,
    import_lote_id INTEGER,
    ativo INTEGER NOT NULL DEFAULT 1,
    criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em TEXT,
    FOREIGN KEY (organizacao_id) REFERENCES organizacoes(id),
    FOREIGN KEY (unidade_id) REFERENCES unidades(id),
    FOREIGN KEY (entidade_pessoa_id) REFERENCES pessoas(id),
    FOREIGN KEY (plano_conta_id) REFERENCES plano_contas(id),
    FOREIGN KEY (centro_custo_id) REFERENCES centros_custo(id),
    FOREIGN KEY (conta_financeira_id) REFERENCES contas_financeiras(id),
    FOREIGN KEY (documento_id) REFERENCES documentos(id),
    FOREIGN KEY (import_lote_id) REFERENCES import_lotes(id)
);

CREATE TABLE IF NOT EXISTS rateios_lancamento (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lancamento_id INTEGER NOT NULL,
    centro_custo_id INTEGER NOT NULL,
    percentual REAL,
    valor REAL NOT NULL,
    FOREIGN KEY (lancamento_id) REFERENCES lancamentos_financeiros(id),
    FOREIGN KEY (centro_custo_id) REFERENCES centros_custo(id)
);

CREATE TABLE IF NOT EXISTS metas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organizacao_id INTEGER NOT NULL,
    nome TEXT NOT NULL,
    indicador TEXT NOT NULL,
    periodo_inicio TEXT NOT NULL,
    periodo_fim TEXT NOT NULL,
    valor_alvo REAL NOT NULL,
    plano_conta_id INTEGER,
    centro_custo_id INTEGER,
    ativa INTEGER NOT NULL DEFAULT 1,
    criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em TEXT,
    FOREIGN KEY (organizacao_id) REFERENCES organizacoes(id),
    FOREIGN KEY (plano_conta_id) REFERENCES plano_contas(id),
    FOREIGN KEY (centro_custo_id) REFERENCES centros_custo(id)
);

CREATE TABLE IF NOT EXISTS metricas_one_page (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organizacao_id INTEGER NOT NULL,
    codigo TEXT NOT NULL,
    nome TEXT NOT NULL,
    grupo TEXT NOT NULL,
    formula_tipo TEXT NOT NULL,
    filtro_json TEXT,
    ordem INTEGER NOT NULL DEFAULT 0,
    ativo INTEGER NOT NULL DEFAULT 1,
    criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em TEXT,
    FOREIGN KEY (organizacao_id) REFERENCES organizacoes(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_unidades_org_nome
ON unidades(organizacao_id, nome);

CREATE UNIQUE INDEX IF NOT EXISTS idx_perfis_org_nome
ON perfis_acesso(organizacao_id, nome);

CREATE UNIQUE INDEX IF NOT EXISTS idx_pessoas_org_cpf
ON pessoas(organizacao_id, cpf)
WHERE cpf IS NOT NULL AND cpf <> '';

CREATE UNIQUE INDEX IF NOT EXISTS idx_pessoas_org_codigo
ON pessoas(organizacao_id, codigo_interno)
WHERE codigo_interno IS NOT NULL AND codigo_interno <> '';

CREATE INDEX IF NOT EXISTS idx_pessoas_org_nome
ON pessoas(organizacao_id, nome);

CREATE INDEX IF NOT EXISTS idx_pessoas_org_status
ON pessoas(organizacao_id, status, ativo);

CREATE UNIQUE INDEX IF NOT EXISTS idx_pessoa_perfis_unico_ativo
ON pessoa_perfis(organizacao_id, pessoa_id, perfil)
WHERE ativo = 1;

CREATE UNIQUE INDEX IF NOT EXISTS idx_campos_personalizados_chave
ON campos_personalizados(organizacao_id, registro_tipo, chave);

CREATE INDEX IF NOT EXISTS idx_valores_campos_registro
ON valores_campos_personalizados(organizacao_id, registro_tipo, registro_id);

CREATE INDEX IF NOT EXISTS idx_import_lotes_org
ON import_lotes(organizacao_id, tipo_importacao, status);

CREATE INDEX IF NOT EXISTS idx_import_linhas_lote
ON import_linhas(lote_id, status);

CREATE UNIQUE INDEX IF NOT EXISTS idx_plano_contas_codigo
ON plano_contas(organizacao_id, codigo);

CREATE UNIQUE INDEX IF NOT EXISTS idx_centros_custo_codigo
ON centros_custo(organizacao_id, codigo);

CREATE UNIQUE INDEX IF NOT EXISTS idx_tipos_contribuicao_codigo
ON tipos_contribuicao(organizacao_id, codigo);

CREATE UNIQUE INDEX IF NOT EXISTS idx_formas_recebimento_codigo
ON formas_recebimento(organizacao_id, codigo);

CREATE INDEX IF NOT EXISTS idx_contribuicoes_pessoa
ON contribuicoes(organizacao_id, pessoa_id, data_recebimento);

CREATE INDEX IF NOT EXISTS idx_contribuicoes_competencia
ON contribuicoes(organizacao_id, competencia_ordem, tipo_contribuicao_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_recibos_numero
ON recibos(organizacao_id, numero);

CREATE UNIQUE INDEX IF NOT EXISTS idx_recibo_itens_unico
ON recibo_itens(recibo_id, contribuicao_id);

CREATE UNIQUE INDEX IF NOT EXISTS ux_envelope_lotes_competencia_nome
ON envelope_lotes(organizacao_id, competencia_ordem, nome);

CREATE INDEX IF NOT EXISTS idx_envelopes_lote
ON envelopes(lote_id, ativo, data_recebimento);

CREATE INDEX IF NOT EXISTS idx_envelopes_competencia
ON envelopes(organizacao_id, competencia_ordem, ativo);

CREATE INDEX IF NOT EXISTS idx_envelopes_hash
ON envelopes(organizacao_id, imagem_hash);

CREATE INDEX IF NOT EXISTS idx_envelope_itens_envelope
ON envelope_itens(envelope_id, ativo);

CREATE INDEX IF NOT EXISTS idx_envelope_itens_contribuicao
ON envelope_itens(contribuicao_id);

CREATE INDEX IF NOT EXISTS idx_lancamentos_competencia
ON lancamentos_financeiros(organizacao_id, competencia_ordem, tipo, status);

CREATE INDEX IF NOT EXISTS idx_lancamentos_datas
ON lancamentos_financeiros(organizacao_id, data_vencimento, data_pagamento);

CREATE UNIQUE INDEX IF NOT EXISTS idx_metricas_one_page_codigo
ON metricas_one_page(organizacao_id, codigo);

INSERT OR IGNORE INTO modulos(codigo, nome, descricao, ordem, ativo) VALUES
('nucleo', 'Nucleo', 'Base da plataforma, organizacoes, usuarios e permissoes.', 1, 1),
('pessoas', 'Pessoas', 'Cadastro de membros, frequentadores, visitantes e contatos.', 2, 1),
('importacao', 'Importacao', 'Importacao assistida de planilhas e auditoria de lotes.', 3, 1),
('contribuicoes', 'Contribuicoes', 'Dizimos, ofertas, campanhas e recibos.', 4, 1),
('financeiro', 'Financeiro', 'Lancamentos financeiros, plano de contas e centros de custo.', 5, 1),
('relatorios', 'Relatorios', 'Relatorios gerenciais e One Page Report.', 6, 1),
('crm', 'CRM Pastoral', 'Relacionamento, visitas e acompanhamentos pastorais.', 7, 1),
('ministerios', 'Ministerios', 'Ministerios, equipes e voluntarios.', 8, 1),
('eventos', 'Eventos', 'Eventos, inscricoes e presencas.', 9, 1),
('rh', 'RH', 'Funcionarios, contratos e rotinas de recursos humanos.', 10, 1),
('patrimonio', 'Patrimonio', 'Bens, manutencoes e controle patrimonial.', 11, 1);

INSERT OR IGNORE INTO permissoes(codigo, modulo, descricao) VALUES
('pessoas.visualizar', 'pessoas', 'Visualizar cadastro de pessoas.'),
('pessoas.editar', 'pessoas', 'Criar e editar cadastro de pessoas.'),
('importacao.executar', 'importacao', 'Executar importacoes assistidas.'),
('contribuicoes.visualizar', 'contribuicoes', 'Visualizar contribuicoes.'),
('contribuicoes.editar', 'contribuicoes', 'Criar e editar contribuicoes.'),
('financeiro.visualizar', 'financeiro', 'Visualizar financeiro.'),
('financeiro.editar', 'financeiro', 'Criar e editar lancamentos financeiros.'),
('relatorios.visualizar', 'relatorios', 'Visualizar relatorios.'),
('admin.configurar', 'nucleo', 'Configurar organizacao, usuarios e modulos.');

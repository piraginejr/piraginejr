# Arquitetura Do Cadastro De Pessoas V1

## Decisoes Aplicadas

- Entrada manual de CPF deve bloquear CPF matematicamente invalido antes de gravar.
- Entrada manual de CPF deve bloquear CPF ja existente em outra ficha ativa; nesses casos o operador deve atualizar a ficha existente, por exemplo mudando de frequentador para membro.
- Importacoes em massa podem preservar CPF invalido em auditoria, porque o operador nao esta digitando ficha a ficha.
- Entrada manual de e-mail deve bloquear erros evidentes de formato, sem depender de confirmacao externa por resposta de e-mail.
- O formulario deve avisar CPF/e-mail invalido ao sair do campo; para CPF, tambem deve consultar duplicidade ativa no banco antes da gravacao final.
- `sexo` e `estado civil` devem ser listas fechadas para evitar variantes como `masc`, `M`, `masculino`, `casad`, etc.
- `nome social` passa a ser tratado visualmente como `Nome social (apelido como e conhecido)`.
- Foto da ficha deve ser grande o suficiente para reconhecimento visual.

## Fotos

As fotos continuam fora do SQLite, em arquivos:

- raiz padrao: `data/fotos_membros`
- override tecnico: `POWER_CHURCH_PHOTO_DIR`
- novas fotos: subpastas por faixa de ID, por exemplo `data/fotos_membros/0001/`

Motivo:

- banco relacional nao deve carregar binarios de foto;
- a URL continua sendo por ID da pessoa, entao a tela nao depende do caminho fisico;
- subpastas evitam dezenas ou centenas de milhares de arquivos em uma unica pasta;
- o nome novo do arquivo contem o ID no prefixo e no sufixo, facilitando reconstrucao em caso de desastre.

Formato do nome novo:

`membro_000123__nome_da_pessoa__id_000123__cpf_00000000000.jpg`

Arquivos antigos continuam localizaveis por fallback.

Para padronizar fotos antigas sem perder vinculo:

- rodar primeiro `python3 scripts/padronizar_fotos_membros.py` em modo simulacao;
- conferir a lista de origem e destino;
- depois rodar `python3 scripts/padronizar_fotos_membros.py --apply` somente quando aprovado;
- a busca da foto continua por ID da ficha, entao a tela nao depende do caminho antigo.

No banco atual, essa rotina resolve arquivos antigos como:

`membro_001078__paschoal_piragine_junior__cpf_03782138813.jpg`

para o padrao:

`0001/membro_001078__paschoal_piragine_junior__id_001078__cpf_03782138813.jpg`

## Duplicidades Cadastrais

Regra recomendada:

- CPF valido duplicado: bloqueio duro, porque identifica a mesma pessoa com alta confianca.
- Nome igual com mesma data de nascimento: alerta forte, sugerindo abrir a ficha existente antes de criar nova.
- Nome igual sem data de nascimento: nao deve bloquear automaticamente, porque pode haver homonimos reais; deve virar lista de possiveis duplicidades.
- Mudanca de frequentador para membro deve ocorrer por edicao da ficha existente, preservando historico, contribuintes, nucleo familiar e auditoria.

Fluxo futuro ideal: antes de concluir uma nova ficha, mostrar uma etapa "possiveis cadastros existentes" quando houver CPF, nome, nascimento, telefone ou e-mail semelhante.

## Exportacao Avancada

A exportacao simples XLSX/CSV continua disponivel, mas a evolucao correta e um mapeador de campos:

- grupo cadastro: ID, nome, status, CPF, nascimento, sexo, estado civil, contatos e endereco;
- grupo familia: nucleo familiar, relacionamentos e endereco comum;
- grupo financeiro: total por periodo, quantidade de remessas, ultima contribuicao, destinos financeiros e situacao no rol;
- perfis salvos: secretaria, financeiro, auditoria, estrategia pastoral.

Assim o operador monta exportacoes amplas sem alterar o banco e sem misturar dados sensiveis para usuarios sem permissao.

## Lixeira Segura

Exclusao de ficha nunca deve apagar fisicamente a pessoa no fluxo normal.

Fluxo aplicado:

- operador precisa estar autenticado;
- operador precisa informar senha novamente;
- operador precisa digitar exatamente o nome da pessoa;
- operador precisa justificar;
- sistema grava snapshot em `pessoas_lixeira_segura`;
- sistema marca `pessoas.ativo = 0`;
- auditoria recebe `excluir_ficha_lixeira_segura_django`;
- contribuições, recibos, historico e foto permanecem preservados.
- a lista operacional e a ficha direta mostram somente `pessoas.ativo = 1`;
- auditoria administrativa das exclusoes fica em `Pessoas > Lixeira segura`.

Futuro:

- tela de lixeira apenas para administrador;
- restauracao controlada pelo administrador;
- eliminacao definitiva somente por superusuario superior, quando este papel for definido.

## Nucleo Familiar Na Criacao

Melhor estrategia:

1. No cadastro individual, manter primeiro os campos pessoais.
2. Apos preencher endereco, sugerir pessoas do mesmo endereco ou parentes buscados manualmente.
3. Permitir marcar pai, mae, conjuge, filho, irmao ou nucleo familiar.
4. Gravar os vinculos junto com a ficha, todos auditados.

Isso atende o caso de filho que se batiza enquanto os pais ja existem no banco.

## Entrada De Familia Inteira

Para familia completa que ingressa de uma vez, o melhor fluxo futuro e um assistente em etapas:

1. Dados comuns da familia: endereco, contatos familiares, origem, observacoes.
2. Pessoa 1: dados individuais.
3. Pessoa 2, 3, 4...: repetir somente dados pessoais.
4. Vinculos familiares: conjuge, filhos, responsaveis, familia estendida.
5. Revisao final antes de gravar tudo.

Esse modelo evita retrabalho e reduz erro, mas precisa nascer como assistente proprio para nao poluir a ficha individual.

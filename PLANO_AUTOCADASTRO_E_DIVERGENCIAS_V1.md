# Plano de Autocadastro e Divergencias V1

Data: 29/05/2026

## Objetivo

Registrar a estrategia funcional e tecnica para duas evolucoes futuras:

1. Atualizacao de cadastro pela propria pessoa, a partir do aplicativo web do cliente.
2. Abertura e tratamento seguro de divergencias de recibos/extratos, com trilha de auditoria e resposta supervisionada.

Este documento foi criado para preservar o desenho antes da validacao final com o cliente e evitar perda de contexto entre entregas.

## Item 5: Autocadastro autenticado

### Diretriz principal

Usar o login ja existente do aplicativo do cliente como porta de autenticacao. O Power Church nao deve expor um formulario publico capaz de alterar cadastro sensivel sem sessao autenticada.

### Fluxo recomendado

1. O e-mail disparado pelo sistema inclui um botao `Atualize seu cadastro`.
2. O botao leva a uma area autenticada do aplicativo do cliente.
3. Depois do login, a pessoa acessa um formulario preenchido com seus dados atuais.
4. A pessoa confirma ou altera:
   - nome
   - contatos
   - enderecos
   - foto
   - dados complementares definidos pelo cliente
5. O sistema grava:
   - antes
   - depois
   - data/hora
   - origem `autocadastro_app_cliente`
   - usuario autenticado
6. A alteracao pode seguir uma de duas politicas:
   - aplicacao imediata com auditoria
   - aplicacao em fila de revisao, por campo

### Recomendacao tecnica

- Criar endpoint seguro de integracao entre o app do cliente e o Power Church.
- Exigir identificador confiavel da pessoa autenticada.
- Gravar evento no historico Django e, quando necessario, refletir no legado.
- Tratar foto como upload auditavel.

### Decisoes a validar com o cliente

- Quais campos entram direto e quais exigem supervisao.
- Se foto pode ser autoaprovada.
- Se o app do cliente vai chamar API do Power Church ou redirecionar para uma tela hospedada no Power Church.

## Item 6: Divergencias de recibo/extrato

### Diretriz principal

Divergencia nao deve editar financeiro diretamente. Ela deve abrir um caso de auditoria supervisionado.

### Fluxo recomendado

1. O e-mail do recibo/extrato inclui um botao `Reportar divergencia`.
2. O botao leva a uma area autenticada.
3. A pessoa primeiro revisa e confirma/atualiza seu cadastro.
4. Depois preenche um formulario de divergencia com:
   - tipo de problema
   - descricao
   - periodo/recibo/extrato de referencia
   - anexos, se houver
5. O sistema cria um `caso de divergencia`.
6. Um supervisor de auditoria analisa o caso.
7. O supervisor registra:
   - conclusao
   - ajustes realizados
   - resposta ao solicitante
8. O sistema envia a resposta por e-mail.
9. A pessoa informa se a resposta foi satisfatoria:
   - se sim, o caso encerra
   - se nao, o mesmo caso reabre, preservando o historico completo

### Recomendacao tecnica

- Criar entidades proprias de caso, mensagens e resolucao.
- Integrar com a auditoria Django.
- Gravar referencia cruzada no historico do extrato/recibo.
- Exigir autenticacao.
- Evitar links publicos capazes de alterar ou consultar dados financeiros sem sessao valida.

### Campos minimos do caso

- pessoa_id
- origem (`recibo`, `extrato`, `email`, `app_cliente`)
- referencia funcional (numero do recibo, periodo, dispatch, pessoa)
- status
- prioridade
- descricao do contribuinte
- resposta do supervisor
- satisfacao final

### Decisoes a validar com o cliente

- Quem pode atuar como supervisor.
- Prazo de resposta esperado.
- Se o contribuinte pode anexar documentos/imagens.
- Se o supervisor pode corrigir diretamente ou apenas abrir tarefa para operador.

## Estrategia de integracao com app de terceiro

Como o aplicativo e de outro fornecedor, a recomendacao e manter a fronteira assim:

- Autenticacao e sessao: app do cliente
- Cadastro financeiro e trilha operacional: Power Church
- Comunicacao: API segura ou SSO simples com token assinado e expiracao curta

## Status

- Itens 1 a 4: podem seguir implementacao local no Power Church.
- Itens 5 e 6: aguardam validacao funcional do cliente antes da codificacao.

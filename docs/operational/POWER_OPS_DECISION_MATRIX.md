# Power Ops Decision Matrix

## Objetivo

Este quadro consolida as decisoes operacionais do projeto para a fase Docker + Django + PostgreSQL, deixando claro o que o Power Church deve usar agora, o que vale adotar depois e o que nao faz sentido ativar neste momento.

Principio guia:

> O Power Church Operations deve ser uma camada de orquestracao sobre ferramentas nativas e consolidadas, e nao uma substituicao dessas ferramentas.

## Legenda

- `Usar agora`: entra na trilha operacional da subida e sustentacao do ambiente atual.
- `Usar depois`: vale preparar como evolucao, mas nao precisa bloquear a operacao imediata.
- `Nao usar agora`: existe no ecossistema, mas traz custo, risco ou complexidade acima do ganho nesta fase.

## Deploy

| Usar agora | Usar depois | Nao usar agora |
| --- | --- | --- |
| **Tecnologia/Ferramenta:** Docker Compose + Dockerfile.django + Git branch `main` + atualizador interno do servidor em janela de 30 minutos + Ubuntu shell<br>**Funcao:** buildar, publicar e sincronizar a versao homologada<br>**Decisao:** Usar agora<br>**Justificativa:** e o modelo real ja adotado, reduz operacao manual e acelera correcoes pequenas sem perder rastreabilidade<br>**Como entra no Power Church:** `main` vira a trilha oficial; depois do teste local, o servidor acompanha essa branch automaticamente<br>**Risco ou cuidado:** `main` passa a ser branch operacional e nao pode receber trabalho incompleto | **Tecnologia/Ferramenta:** GitHub Actions para pipeline de build e verificacoes antes da subida<br>**Funcao:** automatizar validacoes de release antes do operador executar no servidor<br>**Decisao:** Usar depois<br>**Justificativa:** reduz erro humano, mas depende de fechar primeiro a trilha operacional local/nuvem e os checks confiaveis<br>**Como entra no Power Church:** pipeline rodando testes, lint e artefatos do release antes da sincronizacao automatica ou de uma intervencao manual<br>**Risco ou cuidado:** se entrar cedo demais, automatiza fragilidade em vez de estabilidade | **Tecnologia/Ferramenta:** Kubernetes/Helm<br>**Funcao:** orquestracao distribuida de containers<br>**Decisao:** Nao usar agora<br>**Justificativa:** complexidade muito acima do tamanho e momento do projeto<br>**Como entra no Power Church:** nao entra nesta fase<br>**Risco ou cuidado:** consome tempo operacional e aumenta a superficie de falha sem ganho proporcional |

## Backup

| Usar agora | Usar depois | Nao usar agora |
| --- | --- | --- |
| **Tecnologia/Ferramenta:** `pg_dump` + compactacao externa + armazenamento fora do host + copia versionada de `data` operacional<br>**Funcao:** backup logico do PostgreSQL e preservacao dos arquivos necessarios ao sistema<br>**Decisao:** Usar agora<br>**Justificativa:** e o caminho mais simples, auditavel e portavel para o estado atual<br>**Como entra no Power Church:** rotina operacional separada para banco e arquivos operacionais, com retencao definida pelo ambiente da nuvem<br>**Risco ou cuidado:** backup de banco sem arquivos ou vice-versa gera restauracao incompleta | **Tecnologia/Ferramenta:** snapshots automatizados do volume/servidor e politica formal de retencao<br>**Funcao:** acelerar recuperacao e reduzir janela de perda<br>**Decisao:** Usar depois<br>**Justificativa:** e util, mas depende da infraestrutura do provedor e de uma politica de restauracao testada<br>**Como entra no Power Church:** complementar o backup logico, nao substitui-lo<br>**Risco ou cuidado:** snapshot sem teste de restore cria falsa sensacao de seguranca | **Tecnologia/Ferramenta:** commit de dados no Git<br>**Funcao:** versionar backups e massa operacional no repositorio<br>**Decisao:** Nao usar agora<br>**Justificativa:** o Git nao e repositorio de dados operacionais pesados nem de segredos<br>**Como entra no Power Church:** nao entra<br>**Risco ou cuidado:** repositorio cresce demais e mistura codigo com dados sensiveis |

## Restore

| Usar agora | Usar depois | Nao usar agora |
| --- | --- | --- |
| **Tecnologia/Ferramenta:** `pg_restore` ou `psql` conforme o formato do dump + restauracao controlada de arquivos de `data`<br>**Funcao:** reconstituir banco e arquivos operacionais apos falha ou migracao<br>**Decisao:** Usar agora<br>**Justificativa:** e o par natural do backup logico e cabe no modelo atual do runtime<br>**Como entra no Power Church:** procedimento operacional documentado no servidor e em homologacao<br>**Risco ou cuidado:** restore parcial pode subir sistema aparentemente funcional, mas quebrado em anexos, imagens ou auditorias | **Tecnologia/Ferramenta:** restore automatizado em ambiente de homologacao para teste periodico<br>**Funcao:** validar se os backups realmente voltam com sucesso<br>**Decisao:** Usar depois<br>**Justificativa:** aumenta confiabilidade, mas exige tempo e infraestrutura dedicada<br>**Como entra no Power Church:** restaurar periodicamente em ambiente espelho para prova de recuperacao<br>**Risco ou cuidado:** sem isolamento, pode sobrescrever ambiente errado | **Tecnologia/Ferramenta:** PITR completo com WAL shipping desde ja<br>**Funcao:** recuperacao ponto-no-tempo<br>**Decisao:** Nao usar agora<br>**Justificativa:** poderoso, mas mais complexo do que o necessario para fechar a operacao atual<br>**Como entra no Power Church:** adiado para fase de maturidade operacional maior<br>**Risco ou cuidado:** configuracao incompleta de WAL pode piorar a seguranca em vez de melhora-la |

## Sync local

| Usar agora | Usar depois | Nao usar agora |
| --- | --- | --- |
| **Tecnologia/Ferramenta:** volumes bindados e espelhamento seletivo do `data` para o runtime local<br>**Funcao:** manter ambiente local funcional sem duplicar tudo desnecessariamente<br>**Decisao:** Usar agora<br>**Justificativa:** foi o modelo que melhor equilibrou agilidade e proximidade com a nuvem<br>**Como entra no Power Church:** runtime local usa a mesma estrutura logica do ambiente alvo, com ligacoes controladas para arquivos pesados<br>**Risco ou cuidado:** iCloud, WSL ou mounts lentos podem degradar muito a leitura | **Tecnologia/Ferramenta:** `rsync` seletivo com filtros por diretorio operacional<br>**Funcao:** sincronizar somente o que precisa entre ambientes<br>**Decisao:** Usar depois<br>**Justificativa:** melhora performance e controle, mas precisa consolidar a politica definitiva de pastas do projeto<br>**Como entra no Power Church:** sincronizacao deliberada de arquivos relevantes antes de homologacao ou suporte<br>**Risco ou cuidado:** filtro errado pode omitir anexos criticos | **Tecnologia/Ferramenta:** espelhamento total e continuo de todo `data` em toda maquina de desenvolvimento<br>**Funcao:** manter copia identica e permanente de tudo o tempo todo<br>**Decisao:** Nao usar agora<br>**Justificativa:** gera lentidao, ruido e custo operacional desnecessario<br>**Como entra no Power Church:** nao entra como padrao<br>**Risco ou cuidado:** aumenta chance de conflito, copia redundante e timeout de leitura |

## Healthcheck

| Usar agora | Usar depois | Nao usar agora |
| --- | --- | --- |
| **Tecnologia/Ferramenta:** healthcheck do Docker Compose + `pg_isready` + endpoint `/api/v1/health/` + verificador de login HTTP<br>**Funcao:** confirmar que containers e aplicacao estao vivos e respondendo<br>**Decisao:** Usar agora<br>**Justificativa:** combina saude de infraestrutura com saude minima da app<br>**Como entra no Power Church:** check basico obrigatorio apos subir e antes de liberar operadores<br>**Risco ou cuidado:** container healthy nao garante regra de negocio funcionando | **Tecnologia/Ferramenta:** healthchecks por modulo critico e endpoint de readiness mais rico<br>**Funcao:** validar dependencias internas como DB, fila, PDF e arquivos<br>**Decisao:** Usar depois<br>**Justificativa:** e valioso, mas so depois de fechar o conjunto principal de verificadores<br>**Como entra no Power Church:** ampliar o health para dependencias funcionais criticas<br>**Risco ou cuidado:** endpoint muito pesado pode virar causa de lentidao | **Tecnologia/Ferramenta:** monitoramento enterprise completo tipo Prometheus + Grafana + Alertmanager desde o inicio<br>**Funcao:** telemetria e alerta em profundidade<br>**Decisao:** Nao usar agora<br>**Justificativa:** bom no futuro, mas hoje a dor principal e estabilizar fluxos, nao observabilidade de alto porte<br>**Como entra no Power Church:** adiado<br>**Risco ou cuidado:** custo de configuracao e manutencao alto para a fase atual |

## Logs

| Usar agora | Usar depois | Nao usar agora |
| --- | --- | --- |
| **Tecnologia/Ferramenta:** `docker compose logs`, logging do Django para stdout/stderr e arquivos operacionais de release<br>**Funcao:** diagnostico rapido de falhas de subida, login, importacao e runtime<br>**Decisao:** Usar agora<br>**Justificativa:** e o caminho nativo do runtime atual e resolve bem suporte de campo<br>**Como entra no Power Church:** logs do app e do banco consultados diretamente pelo operador tecnico na nuvem<br>**Risco ou cuidado:** sem padrao de mensagem, diagnostico fica dependente de leitura manual | **Tecnologia/Ferramenta:** agregacao centralizada com rotacao e consulta historica<br>**Funcao:** retenção organizada e busca de incidentes<br>**Decisao:** Usar depois<br>**Justificativa:** melhora bastante a sustentacao, mas nao precisa travar o go-live<br>**Como entra no Power Church:** camada de observabilidade depois da estabilizacao operacional<br>**Risco ou cuidado:** armazenar log com dado sensivel exige saneamento | **Tecnologia/Ferramenta:** logs extensos em arquivos dentro do repositorio ou volume sem rotacao<br>**Funcao:** persistir tudo indefinidamente no host<br>**Decisao:** Nao usar agora<br>**Justificativa:** cresce sem controle e dificulta manutencao<br>**Como entra no Power Church:** nao entra como estrategia<br>**Risco ou cuidado:** enche disco e piora performance |

## Rollback

| Usar agora | Usar depois | Nao usar agora |
| --- | --- | --- |
| **Tecnologia/Ferramenta:** Git commit/SHA em `main` + `git revert` como primeira opcao + rebuild do runtime + backup pre-deploy<br>**Funcao:** voltar rapidamente para uma versao estavel sem perder trilha de auditoria<br>**Decisao:** Usar agora<br>**Justificativa:** combina bem com sincronizacao automatica do servidor e preserva historico limpo de incidente e resposta<br>**Como entra no Power Church:** cada envio para `main` precisa registrar o SHA; se houver problema, a resposta preferencial e `revert` seguido de nova sincronizacao automatica<br>**Risco ou cuidado:** rollback de codigo sem considerar migration pode quebrar compatibilidade | **Tecnologia/Ferramenta:** politica de migrations reversiveis e trilha formal de rollback por release<br>**Funcao:** reduzir risco em alteracoes estruturais de banco<br>**Decisao:** Usar depois<br>**Justificativa:** importante conforme a API e as regras de negocio avancarem<br>**Como entra no Power Church:** checklist obrigatorio para releases que alterem schema ou dados sensiveis<br>**Risco ou cuidado:** nem toda migration e reversivel; precisa criterio de engenharia | **Tecnologia/Ferramenta:** rollback improvisado por edicao manual de container em producao<br>**Funcao:** tentar consertar no susto sem trilha de release<br>**Decisao:** Nao usar agora<br>**Justificativa:** e perigoso e dificil de auditar<br>**Como entra no Power Church:** nao entra<br>**Risco ou cuidado:** cria divergencia entre codigo local, Git e servidor |

## Segredos

| Usar agora | Usar depois | Nao usar agora |
| --- | --- | --- |
| **Tecnologia/Ferramenta:** `.env` fora do Git + variaveis de ambiente do Docker/host + controle manual do operador da nuvem<br>**Funcao:** manter credenciais do Django, PostgreSQL, Microsoft Graph e afins fora do codigo<br>**Decisao:** Usar agora<br>**Justificativa:** e o nivel correto para a fase atual e ja se encaixa no runtime existente<br>**Como entra no Power Church:** segredos ficam no servidor e no runtime, nunca no repositorio<br>**Risco ou cuidado:** precisa disciplina para nao vazar em backup, log ou commit | **Tecnologia/Ferramenta:** secret manager do provedor ou cofre dedicado<br>**Funcao:** centralizar gestao de segredos com rotacao e controle de acesso<br>**Decisao:** Usar depois<br>**Justificativa:** melhor pratica clara, mas pode esperar a operacao estabilizar<br>**Como entra no Power Church:** substituir gradualmente `.env` manual por referencia segura no host/provedor<br>**Risco ou cuidado:** migracao parcial pode duplicar fontes de verdade | **Tecnologia/Ferramenta:** segredos embutidos em `docker-compose`, scripts, commits ou imagens exportadas<br>**Funcao:** simplificar acesso copiando credenciais junto do codigo<br>**Decisao:** Nao usar agora<br>**Justificativa:** viola segregacao basica de seguranca<br>**Como entra no Power Church:** nao entra<br>**Risco ou cuidado:** vazamento direto de credenciais e necessidade de rotacao emergencial |

## Agendamento

| Usar agora | Usar depois | Nao usar agora |
| --- | --- | --- |
| **Tecnologia/Ferramenta:** atualizador interno do servidor em janela de 30 minutos + `cron` ou mecanismo equivalente do host para rotinas de backup, fila e manutencao<br>**Funcao:** sincronizar codigo e agendar operacao recorrente<br>**Decisao:** Usar agora<br>**Justificativa:** ja existe no ambiente e resolve o maior atrito operacional desta fase<br>**Como entra no Power Church:** o servidor puxa `main` automaticamente; tarefas de backup e manutencao continuam agendadas no host<br>**Risco ou cuidado:** automacao sem politica de validacao local aumenta risco de propagar regressao rapidamente | **Tecnologia/Ferramenta:** systemd timers<br>**Funcao:** substituir ou complementar cron com mais controle de servico, log e dependencia<br>**Decisao:** Usar depois<br>**Justificativa:** melhor observabilidade, mas o agendamento atual resolve primeiro com menor atrito<br>**Como entra no Power Church:** evolucao de rotinas mais criticas e permanentes<br>**Risco ou cuidado:** exige mais maturidade operacional do host | **Tecnologia/Ferramenta:** scheduler proprio dentro do Power Church para tudo<br>**Funcao:** recriar agendamento de sistema operacional na aplicacao<br>**Decisao:** Nao usar agora<br>**Justificativa:** reinvencao desnecessaria nesta fase<br>**Como entra no Power Church:** nao entra como primeira camada<br>**Risco ou cuidado:** mais codigo para manter, depurar e operar |

## Validacao pos-deploy

| Usar agora | Usar depois | Nao usar agora |
| --- | --- | --- |
| **Tecnologia/Ferramenta:** `python manage.py check --deploy`, `migrate`, `collectstatic`, health HTTP, login e smoke funcional minimo<br>**Funcao:** validar que a aplicacao subiu corretamente e nao quebrou o basico<br>**Decisao:** Usar agora<br>**Justificativa:** cobre infraestrutura e aplicacao com ferramentas nativas do Django e runtime atual<br>**Como entra no Power Church:** etapa obrigatoria do release antes de liberar operador<br>**Risco ou cuidado:** check tecnico sem teste funcional pode deixar bug operacional passar | **Tecnologia/Ferramenta:** pipeline formal de pos-deploy com relatorio e checkpoints por modulo<br>**Funcao:** transformar a validacao em rotina repetivel e auditavel<br>**Decisao:** Usar depois<br>**Justificativa:** vale muito a pena, mas primeiro precisamos estabilizar a matriz e o verificador mestre<br>**Como entra no Power Church:** camada de homologacao curta apos cada release em nuvem<br>**Risco ou cuidado:** excesso de passos pode travar releases pequenos | **Tecnologia/Ferramenta:** confianca apenas em container healthy ou em subida sem erro visivel<br>**Funcao:** assumir que se abriu o login, tudo esta certo<br>**Decisao:** Nao usar agora<br>**Justificativa:** ja vimos no projeto que isso mascara dashboards zerados, rotas quebradas e regras falhando<br>**Como entra no Power Church:** nao entra<br>**Risco ou cuidado:** regressao funcional passa despercebida |

## Smoke tests de negocio

| Usar agora | Usar depois | Nao usar agora |
| --- | --- | --- |
| **Tecnologia/Ferramenta:** verificador mestre do runtime + smoke controlado de login, dashboard, envelopes, recibos, extratos, API e PDFs<br>**Funcao:** provar que os fluxos reais continuam operando apos mudancas<br>**Decisao:** Usar agora<br>**Justificativa:** o risco principal do projeto nao e infra pura; e regressao de regra operacional<br>**Como entra no Power Church:** vira a camada principal do Power Church Operations por cima das ferramentas nativas<br>**Risco ou cuidado:** smoke mal desenhado pode dar falso positivo se nao cobrir os bloqueadores reais | **Tecnologia/Ferramenta:** suite ampliada por perfil, permissao, concorrencia e massa anonima de regressao<br>**Funcao:** aprofundar cobertura sem depender so do operador humano<br>**Decisao:** Usar depois<br>**Justificativa:** deve crescer junto com a API e com os modulos mais sensiveis<br>**Como entra no Power Church:** segunda geracao do verificador, mais completa e mais proxima do uso real em nuvem<br>**Risco ou cuidado:** testes muito pesados podem dificultar ciclo rapido de deploy | **Tecnologia/Ferramenta:** teste exclusivamente manual e exploratorio do operador como unica barreira<br>**Funcao:** descobrir bugs apenas apos a subida<br>**Decisao:** Nao usar agora<br>**Justificativa:** deixa o ambiente do cliente absorver o custo da validacao<br>**Como entra no Power Church:** nao entra como estrategia principal<br>**Risco ou cuidado:** aumenta retrabalho, desgaste com o operador e percepcao de retrocesso |

## Regra de execucao assistida

Para evitar silencio operacional em validacoes longas, o Power Church Operations passa a adotar a seguinte regra:

1. Qualquer comando, auditoria ou teste que fique **7 minutos sem progresso observavel** deve ser interrompido.
2. Antes de interromper, registrar o ponto em que a execucao parou e o objetivo do comando.
3. Depois da interrupcao, a validacao deve ser retomada em blocos menores, com checkpoints explicitos por modulo, rota ou lote.
4. Sempre que possivel, preferir comandos com timeout, saida incremental ou relatorios parciais em vez de uma auditoria unica e opaca.
5. Se o processo for propositalmente demorado, isso deve ser avisado antes da execucao, com a estimativa esperada e o criterio de abandono.

Objetivo pratico da regra:

- evitar janelas longas sem retorno;
- reduzir risco de travamento invisivel;
- manter a homologacao auditavel;
- permitir troca rapida para uma estrategia mais granular quando a verificacao ampla nao responder.

## Arquitetura operacional final proposta

### Camada base nativa

- **Deploy:** Git + Docker Compose + Dockerfile.django + Ubuntu shell.
- **Banco:** PostgreSQL com backup logico por `pg_dump` e restore por `pg_restore` ou `psql`.
- **Aplicacao:** Django com `migrate`, `collectstatic`, `check --deploy` e endpoint de health.
- **Host:** Ubuntu com `cron`, logs de servico e controle de ambiente por `.env`.

### Camada Power Church Operations

- Orquestra o uso dessas ferramentas nativas em uma rotina unica de release.
- Padroniza checklist, ordem de execucao, validacao pos-deploy e rollback.
- Adiciona smoke tests de negocio que as ferramentas nativas nao conhecem.
- Registra evidencias operacionais sem reinventar banco, deploy, scheduler ou secret manager.

### Regra de desenho

- **Usar diretamente** o que ja e robusto no ecossistema.
- **Encapsular** o que precisa virar procedimento repetivel do projeto.
- **Desenvolver codigo proprio** apenas para o que e especifico do negocio: smoke tests, verificadores operacionais, checklist guiado e integracao entre as etapas.

### Conclusao enxuta

O Power Church nao precisa criar um novo deploy, um novo sistema de backup ou um novo agendador. O que ele precisa e de uma camada de orquestracao operacional confiavel, centrada em Docker, Django, PostgreSQL, Git e Ubuntu, com verificacao funcional do negocio antes e depois de cada release.

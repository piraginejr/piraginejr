# Plano De Migracao Para Nuvem Multiusuario V1

## 1. Objetivo

Planejar a passagem do Power Church do ambiente local no Mac para uma infraestrutura Linux/containerizada na nuvem, com acesso multiusuario, mantendo a continuidade do desenvolvimento e reduzindo o risco operacional.

## 2. Leitura Atual Do Projeto

Estado observado no codigo:

- o Django ja aceita `PostgreSQL` via variaveis `POWER_CHURCH_POSTGRES_*` em `power_church_django/power_church_site/settings.py`;
- ainda existe dependencia forte do banco legado em `SQLite` via `POWER_CHURCH_LEGACY_DB_PATH`;
- ja existe imagem de servidor em `Dockerfile.django` com `gunicorn`;
- o pacote `deploy/` ja prepara instalacao Linux e padronizacao futura;
- anexos, PDFs, uploads e branding ainda dependem de disco local/volume;
- a fila de recibos ja existe, mas a operacao ainda depende de processo/rotina do proprio servidor.

Conclusao pratica:

- o projeto ja tem um bom ponto de partida para `staging` em nuvem;
- para `producao multiusuario real`, o principal cuidado nao e o Django em si, e sim a dependencia funcional do banco legado SQLite.

## 2.1 Demandas Ja Recebidas Do Gestor Do Servidor

Pedidos explicitamente recebidos para a etapa de hospedagem:

- gerar `aplicacao + banco` para rodar em `Docker/container`;
- entregar manifesto de orquestracao do ambiente;
- entregar arquivo de `dump` do banco para migracao ao banco do servidor de producao.

Confirmacao obtida com o provedor:

- o pacote esperado e `Dockerfile + docker-compose.yml`;
- `composer.json` nao faz parte da entrega deste projeto.

Traducao tecnica fechada para este projeto:

- `Dockerfile` da aplicacao;
- `docker-compose.yml` do ambiente;
- `docker-compose.django.yml` enquanto o Django/PostgreSQL ainda conviver com o legado;
- arquivo de `dump` do banco-alvo de producao;
- snapshot/backup do legado enquanto a transicao ainda existir.

Bundle final de entrega para o servidor:

- `Dockerfile`;
- `docker-compose.yml`;
- `.env`/`env.example` com variaveis exigidas;
- dump do banco de producao alvo;
- roteiro de restauracao e subida.

## 3. Recomendacao Arquitetural

### 3.1 Fase de staging recomendada

Para a primeira subida na nuvem:

- `1 ambiente Linux/containerizado` Ubuntu 24.04 ou equivalente;
- `Docker + Docker Compose`;
- `Django + Gunicorn`;
- `PostgreSQL` para o banco Django;
- volume persistente para:
  - uploads de envelopes,
  - uploads de extratos,
  - uploads de pessoas,
  - PDFs e relatorios,
  - fotos,
  - backups.

### 3.2 Fase de producao recomendada

Para uso real do cliente:

- `single-tenant`: uma instancia por igreja/cliente;
- `PostgreSQL` como banco principal de producao;
- armazenamento persistente separado do container;
- HTTPS obrigatorio;
- rotina automatica de backup;
- monitoramento de logs e filas.

### 3.3 O que eu nao recomendo como producao final

Nao recomendo assumir como arquitetura final:

- `SQLite` como banco central multiusuario permanente;
- deploy manual direto no servidor sem empacotamento;
- usar a maquina local como ambiente principal de atendimento;
- misturar desenvolvimento e producao na mesma instancia.

## 4. Duas Estrategias Possiveis

### Estrategia A: subida rapida para staging

Objetivo:

- colocar o sistema na nuvem rapidamente para testes com mais de um operador;
- manter o desenvolvimento principal ainda local;
- validar rede, acesso, login, fila, backups e impressos.

Caracteristicas:

- menor risco de mudanca agora;
- mais rapida;
- boa para demonstracao e homologacao multiusuario;
- ainda nao resolve completamente o problema estrutural do legado SQLite.

### Estrategia B: migracao estrutural para producao

Objetivo:

- preparar a base para uso real continuo na nuvem;
- reduzir gargalos e risco de corrupcao/concorrencia;
- profissionalizar backup, restore e operacao.

Caracteristicas:

- mais lenta;
- mais segura no medio prazo;
- exige tratar seriamente a migracao do legado SQLite para modelo portavel de producao.

## 5. Minha Recomendacao

Fazer em duas etapas:

1. `staging em nuvem` agora;
2. `producao definitiva` depois da estabilizacao da camada de dados.

Isso permite:

- liberar acesso multiusuario mais cedo;
- continuar homologando com o cliente;
- nao travar o desenvolvimento;
- evitar um big bang de infraestrutura e banco ao mesmo tempo.

## 6. Etapas Necessarias

### Etapa 1: congelar a fotografia tecnica atual

Antes de subir:

- gerar backup completo do banco legado;
- gerar backup do banco Django;
- congelar `.env` de staging;
- registrar versao do codigo e migration plan;
- listar pastas que precisam de volume persistente.

Saida:

- pacote de recuperacao testado;
- versao base de staging identificada.

### Etapa 2: definir o perfil da infraestrutura alvo

Perfil recomendado inicial:

- Ubuntu 24.04;
- IP fixo;
- disco SSD com margem para PDFs, uploads e backups;
- acesso SSH com chave, sem senha.

Observacao:

- para `staging`, pode ser um ambiente unico;
- para `producao`, depois podemos separar banco e aplicacao se o uso crescer.

### Etapa 3: preparar a rede e seguranca

Precisamos configurar:

- dominio ou subdominio;
- HTTPS;
- firewall;
- restricao de portas;
- usuarios administradores do servidor;
- rotacao e guarda de segredos.

### Etapa 4: publicar a aplicacao

Publicacao recomendada:

- container do Django com `gunicorn`;
- reverse proxy na frente;
- variaveis de ambiente externas ao codigo;
- `collectstatic`;
- migrations do Django;
- criacao de usuario administrador.

Entregaveis obrigatorios desta etapa para o gestor do servidor:

- `Dockerfile` da aplicacao aprovado;
- `docker-compose.yml` aprovado para o ambiente alvo;
- `.env`/segredos entregues por canal seguro;
- instrucao objetiva de `up`, `restart`, `logs` e `healthcheck`.
- comando validado para restaurar o dump do banco no servidor.

### Etapa 5: organizar os dados persistentes

Separar claramente:

- banco Django;
- banco legado;
- uploads e anexos;
- fotos;
- backups;
- logs.

Nada disso deve depender do filesystem interno descartavel do container.

Entregavel adicional exigido pelo servidor:

- mapa de volumes persistentes com caminho de montagem e politica de backup.

### Etapa 6: filas, automacoes e tarefas recorrentes

Precisamos mover para a nuvem de forma controlada:

- fila de recibos;
- reprocessamento de falhas;
- campanhas retroativas;
- backups;
- possiveis jobs de importacao e OCR no futuro.

Modelo inicial aceitavel:

- comandos agendados por `cron` ou `systemd timer`.

Modelo futuro melhor:

- worker dedicado com fila mais robusta.

### Etapa 7: testes de homologacao na nuvem

Antes de abrir para uso:

- login simultaneo de mais de um operador;
- recibo manual;
- recibo automatico;
- extrato PDF;
- importacao bancaria;
- envio de e-mail;
- monitor de fila;
- backup + restore de teste;
- reinicio do ambiente e subida automatica.

O aceite dessa etapa precisa incluir tambem:

- `docker compose up -d` reproduzivel no servidor;
- validacao de restauracao a partir do dump entregue ao gestor.

### Etapa 8: decidir o corte operacional

So depois disso decidir:

- se o cliente vai usar apenas staging;
- se vai virar producao;
- se o Mac continua como desenvolvimento e homologacao local;
- se a nuvem vira o ambiente principal.

## 7. Principal Ponto De Atencao

Hoje a aplicacao ainda conversa com um banco legado `SQLite` para regras centrais de negocio.

Isso significa:

- em um ambiente unico Linux/containerizado, com poucos usuarios, ainda pode funcionar para staging e ate operacao inicial muito controlada;
- para producao multiusuario mais confiavel, o ideal e migrar essa camada de dados para `PostgreSQL` ou reduzir fortemente a dependencia dela.

Traduzindo:

- `subir na nuvem`: sim, ja e viavel;
- `assumir isso como arquitetura final`: ainda nao e o ideal.

## 8. Como Continuar O Desenvolvimento A Partir Da Nuvem

### Modelo recomendado

Manter `3 ambientes`:

- `local`: desenvolvimento rapido e experimentacao;
- `staging`: homologacao compartilhada com o cliente;
- `producao`: uso real.

### Fluxo recomendado

1. desenvolver localmente;
2. rodar checagens locais;
3. subir para branch de staging;
4. publicar em staging;
5. validar com o cliente;
6. promover para producao quando aprovado.

### Regra pratica

Nao desenvolver direto em producao.

Mesmo depois da migracao:

- o codigo continua nascendo localmente;
- a nuvem vira ambiente de integracao e operacao;
- hotfix em producao so quando estritamente necessario, e sempre voltando o ajuste para o repositorio.

## 9. Pipeline Operacional Recomendado

### Curto prazo

- Git + deploy manual controlado;

## 10. Entregaveis Obrigatorios Para O Servidor De Producao

Antes da ida real para a infraestrutura hospedada, o projeto precisa entregar:

1. `bundle de containerizacao`
   - `Dockerfile`
   - `docker-compose.yml`
   - `env.example`
   - documentacao de start/stop

2. `dump de migracao`
   - `PostgreSQL`: dump logico do banco que sera restaurado no servidor de producao;
   - `SQLite legado`: backup/snapshot separado enquanto o legado ainda existir como contingencia.

3. `roteiro de restauracao`
   - comando de restauracao no banco do servidor;
   - ordem correta de subida da aplicacao apos restore;
   - smoke tests minimos pos-restore.

4. `checklist de aceite do provedor`
   - container sobe;
   - banco responde;
   - volumes persistem;
   - aplicacao responde;
   - login funciona;
   - fila/recibos e importacoes criticas passam.
- `powerbackup`;
- atualizacao por script de release;
- restart controlado dos containers/servicos.

### Medio prazo

- deploy automatizado para staging;
- smoke tests automaticos;
- aprovacao manual para producao.

## 10. Ordem Recomendada Dos Trabalhos

1. consolidar o plano e escolher o provedor
2. criar ambiente de staging
3. subir stack com Docker Compose
4. configurar HTTPS, dominio e segredos
5. validar backups
6. validar multiusuario e filas
7. ensaiar restore completo
8. definir se o legado SQLite ainda sustenta o uso inicial
9. planejar a migracao estrutural para PostgreSQL no nucleo de negocio
10. abrir producao

## 11. Decisoes Que Precisam Ser Tomadas

Antes de executar, ainda precisamos fechar:

- qual provedor/infraestrutura sera usado;
- se staging e producao serao ambientes separados;
- se o banco ficara no mesmo ambiente ou separado;
- onde ficarão os backups;
- quem tera acesso SSH;
- qual dominio/subdominio sera usado;
- quando o cliente vai considerar staging suficiente para abrir operacao real.

## 12. Proximo Passo Recomendado

O proximo passo certo e montar um `plano de staging na nuvem`, com:

- topologia exata;
- variaveis de ambiente;
- volumes;
- rotina de backup;
- passo a passo de deploy;
- checklist de aceite.

Esse deve ser o documento operacional da primeira subida.

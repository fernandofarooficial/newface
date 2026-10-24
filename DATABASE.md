# Banco de Dados — NewFace

Documentação do schema PostgreSQL usado pela aplicação. Fonte de verdade: `app/models.py` (SQLAlchemy) e `migrations/*.sql` (DDL). Se este arquivo divergir do código, o código vence — atualize aqui ao alterar `models.py` ou adicionar uma migration.

## Conexão

| Item | Valor |
|------|-------|
| Host | `72.60.58.241` (mesma máquina do app) |
| Porta | `5432` |
| Banco | `lojas` (minúsculo) |
| Schema | `itumbiara` |
| Usuário | `fefa_dev` |

String de conexão montada em `app/config.py`, com `search_path=itumbiara` passado via `options` na URI do SQLAlchemy.

## Migrations

Não há migration runner — os arquivos em `migrations/` são aplicados manualmente via `psql`/DBeaver, na ordem numérica:

```bash
psql -U fefa_dev -h localhost -d lojas -f migrations/001_create_tables.sql
psql -U fefa_dev -h localhost -d lojas -f migrations/002_add_face_image_url.sql
psql -U fefa_dev -h localhost -d lojas -f migrations/003_camera_fk.sql
psql -U fefa_dev -h localhost -d lojas -f migrations/004_check_constraints.sql
psql -U fefa_dev -h localhost -d lojas -f migrations/005_sync_control_history_index.sql
psql -U fefa_dev -h localhost -d lojas -f migrations/006_pessoas_duplicadas.sql
psql -U fefa_dev -h localhost -d lojas -f migrations/007_evento_intruso.sql
psql -U fefa_dev -h localhost -d lojas -f migrations/008_evento_indesejada.sql
```

| Arquivo | O que faz |
|---------|-----------|
| `001_create_tables.sql` | Cria o schema `itumbiara` e as 6 tabelas, índices, comments, e semeia 1 estabelecimento + 3 câmeras de exemplo |
| `002_add_face_image_url.sql` | `ALTER TABLE eventos_faciais ADD COLUMN face_image_url TEXT` |
| `003_camera_fk.sql` | Backfill de câmeras órfãs + FK `eventos_faciais.camera_id` → `cameras.camera_id` (`NOT VALID`, ver nota abaixo) |
| `004_check_constraints.sql` | CHECK constraints de domínio (gênero, score/similarity/quality em `[0,1]`, status de sync) (`NOT VALID`) |
| `005_sync_control_history_index.sql` | Índice por `criado_em` em `sync_control`, agora que é histórico append-only |
| `006_pessoas_duplicadas.sql` | Tabela `pessoas_duplicadas` (pares marcados pelo usuário como o mesmo rosto) e view `vw_grupos_duplicatas` (grupos) |
| `007_evento_intruso.sql` | Colunas `intruso`/`intruso_em` em `eventos_faciais` (captura marcada pelo usuário como rosto de outra pessoa) |
| `008_evento_indesejada.sql` | Colunas `indesejada`/`indesejada_em` em `eventos_faciais` (captura marcada pelo usuário como coleta indesejada: de costas, encoberta, baixa resolução/luz...) |

**Sobre `NOT VALID`** (migrations 003 e 004): a constraint passa a valer para todo `INSERT`/`UPDATE` novo imediatamente, mas não varre as linhas já existentes na hora do `ALTER TABLE` (evita lock longo em tabela grande). Cada arquivo termina com os `SELECT`s para checar dados antigos e os `ALTER TABLE ... VALIDATE CONSTRAINT ...` para rodar depois, manualmente, quando a checagem vier vazia.

**Status em produção (2026-08-20):** migrations 001–005 aplicadas em `72.60.58.241`. Checagem de dados antigos não encontrou nenhuma violação (145 eventos, 7 câmeras, 0 órfãos) e todas as 7 constraints já foram `VALIDATE`d — não estão mais em modo "só vale daqui pra frente", valem para a base inteira.

## Diagrama de relacionamentos

```
estabelecimentos ──< cameras
       │
       └──< eventos_faciais >── pessoas
                  │
                  └──< evento_matches

pessoas ──< pessoas_duplicadas >── pessoas   (par A–B marcado pelo usuário, A < B)
                  │
                  └── vw_grupos_duplicatas (view: grupos conexos de pares)

sync_control  (tabela solitária, 1 linha por execução de coleta)
```

- `cameras.estabelecimento_id` → `estabelecimentos.id`
- `eventos_faciais.estabelecimento_id` → `estabelecimentos.id`
- `eventos_faciais.pessoa_id` → `pessoas.id` (nullable — pode não ter sido possível resolver a pessoa)
- `evento_matches.evento_id` → `eventos_faciais.id`, **ON DELETE CASCADE**
- `eventos_faciais.camera_id` → `cameras.camera_id` (FK adicionada na migration 003; `_ensure_camera` em `app/collector.py` auto-cadastra qualquer câmera nova vista na API antes de inserir o evento, então a FK não quebra a coleta)

## Tabelas

### `estabelecimentos`
Lojas da rede.

| Coluna | Tipo | Notas |
|--------|------|-------|
| `id` | SERIAL PK | |
| `store_id` | INTEGER UNIQUE NOT NULL | ID vindo da API Facial |
| `nome` | VARCHAR(120) NOT NULL | |
| `descricao` | TEXT | |
| `ativo` | BOOLEAN DEFAULT TRUE | |
| `criado_em` / `atualizado_em` | TIMESTAMPTZ | |

### `cameras`
Cadastro de câmeras com nome amigável e localização.

| Coluna | Tipo | Notas |
|--------|------|-------|
| `id` | SERIAL PK | |
| `camera_id` | VARCHAR(20) UNIQUE NOT NULL | Formato público `cam_1`, `cam_2`... retornado pela API |
| `estabelecimento_id` | INTEGER NOT NULL → `estabelecimentos.id` | |
| `nome` | VARCHAR(120) NOT NULL | |
| `localizacao` | VARCHAR(200) | |
| `ativa` | BOOLEAN DEFAULT TRUE | |
| `criado_em` / `atualizado_em` | TIMESTAMPTZ | |

Índice: `idx_cameras_estabelecimento`.

### `pessoas`
Identidades únicas reconhecidas (uma linha por `person_unique_id`).

| Coluna | Tipo | Notas |
|--------|------|-------|
| `id` | SERIAL PK | |
| `person_unique_id` | VARCHAR(20) UNIQUE NOT NULL | Formato `cli_000001`, vem da API |
| `nome` | VARCHAR(200) | Preenchido manualmente no dashboard (`POST /api/pessoas/<id>/nome`) |
| `genero_estimado` | VARCHAR(10) | `male`/`female`/null (CHECK `chk_pessoas_genero`, migration 004) — só preenchido se ainda vazio, nunca sobrescrito |
| `faixa_etaria` | VARCHAR(20) | Ex: `25-31` — mesma regra de "só preenche se vazio" |
| `primeira_deteccao` | TIMESTAMPTZ | Menor `timestamp_evento` já visto para essa pessoa |
| `ultima_deteccao` | TIMESTAMPTZ | Maior `timestamp_evento` já visto |
| `total_deteccoes` | INTEGER DEFAULT 0 | Incrementado a cada evento novo do collector |
| `ativo` | BOOLEAN DEFAULT TRUE | |
| `criado_em` / `atualizado_em` | TIMESTAMPTZ | |

Índices: `idx_pessoas_person_unique_id`, `idx_pessoas_ultima_deteccao DESC`.

### `eventos_faciais`
Um registro por evento de detecção/reconhecimento recebido da API Facial.

| Coluna | Tipo | Notas |
|--------|------|-------|
| `id` | SERIAL PK | |
| `event_id` | VARCHAR(40) UNIQUE NOT NULL | Ex: `evt-20260610-000123` — chave de deduplicação do collector |
| `timestamp_evento` | TIMESTAMPTZ NOT NULL | |
| `pessoa_id` | INTEGER → `pessoas.id`, nullable | |
| `person_unique_id` | VARCHAR(20) | Redundante com `pessoas.person_unique_id`, guardado aqui para joins rápidos |
| `camera_id` | VARCHAR(20) | FK → `cameras.camera_id` (migration 003, `NOT VALID`) |
| `estabelecimento_id` | INTEGER → `estabelecimentos.id`, nullable | Copiado de `cameras.estabelecimento_id` da câmera do evento (não do `store_id` da API) — ver "Coisas não óbvias" |
| `person_track_id` | VARCHAR(40) | |
| `matched` | BOOLEAN | `true` = pessoa já existia na base; `false` = pessoa nova |
| `match_score` | NUMERIC(5,4) | Similaridade 0.0000–1.0000 (CHECK `chk_ef_match_score` em `[0,1]`, migration 004) |
| `match_type` | VARCHAR(20) | `exact` / `new` — sem CHECK de propósito, ver nota em "Coisas não óbvias" |
| `person_count_frame` | INTEGER | |
| `genero_estimado` / `faixa_etaria` | VARCHAR | Snapshot demográfico *deste* evento (independente do que está em `pessoas`); `genero_estimado` tem o mesmo CHECK `male`/`female`/null (`chk_ef_genero`) |
| `face_quality` | NUMERIC(5,4) | CHECK `chk_ef_face_quality` em `[0,1]`, migration 004 |
| `face_image_url` | TEXT | Adicionado na migration 002; caminho servido via proxy `GET /api/face-image?path=...` |
| `model_version` / `processing_node` | VARCHAR | Metadados do sistema de reconhecimento |
| `intruso` | BOOLEAN NOT NULL DEFAULT false | Migration 007. `true` = o usuário marcou que o rosto desta captura **não é** da pessoa associada (a API errou a associação). Só sinaliza: `pessoa_id` não muda e `pessoas.total_deteccoes`/`primeira_deteccao`/`ultima_deteccao` não são recalculados. A única regra que muda é a "primeira foto" do Tabuleiro/Duplicatas (`_primeiras_faces`), que pula capturas intrusas. Alterado por `POST /api/eventos/<id>/intruso`; o collector nunca toca. Listado na aba Intrusas / Indesejadas (`GET /api/marcadas`) e contado no relatório (`GET /api/relatorio`) pela data da coleta, não pela data da marcação |
| `intruso_em` | TIMESTAMPTZ | Quando foi marcada como intrusa; volta a null ao desmarcar |
| `indesejada` | BOOLEAN NOT NULL DEFAULT false | Migration 008. `true` = o usuário marcou a captura como **coleta indesejada**: o rosto é da pessoa certa, mas a foto não serve (de costas, algo na frente do rosto, chapéu muito grande, baixa resolução, pouca luz). Independente de `intruso` (uma captura pode ter as duas marcações). Mesmas regras da intrusa: só sinaliza, nada é recalculado, e `_primeiras_faces` também a pula. Alterado por `POST /api/eventos/<id>/indesejada`; o collector nunca toca. Também aparece em `GET /api/marcadas` e no relatório |
| `indesejada_em` | TIMESTAMPTZ | Quando foi marcada como indesejada; volta a null ao desmarcar |
| `criado_em` | TIMESTAMPTZ | |

Índices: `idx_ef_timestamp DESC`, `idx_ef_pessoa`, `idx_ef_person_unique`, `idx_ef_camera`, `idx_ef_estabelecimento`, `idx_ef_match_type`.

### `evento_matches`
Ocorrências anteriores da mesma pessoa que vieram junto no payload do evento (`recognition.matches` da API). Um evento pode ter N matches.

| Coluna | Tipo | Notas |
|--------|------|-------|
| `id` | SERIAL PK | |
| `evento_id` | INTEGER NOT NULL → `eventos_faciais.id` ON DELETE CASCADE | |
| `event_id_ref` | VARCHAR(40) NOT NULL | `event_id` do evento anterior referenciado — **pode não existir** na tabela `eventos_faciais` (evento fora da janela coletada) |
| `timestamp_ref` | TIMESTAMPTZ | |
| `camera_id_ref` | VARCHAR(20) | |
| `store_id_ref` | INTEGER | Valor cru do `store_id` da API — **não confiável** (não bate com o cadastro de câmeras); nada filtra por ele |
| `similarity` | NUMERIC(5,4) | CHECK `chk_em_similarity` em `[0,1]`, migration 004 |
| `criado_em` | TIMESTAMPTZ | |

Índice: `idx_em_evento`. Sem FK para `event_id_ref` — é só um texto de referência; a foto do match (`face_image_url`) é resolvida em `routes.py` fazendo lookup por `event_id_ref` contra `eventos_faciais.event_id` quando existir.

### `pessoas_duplicadas`
Pares de pessoas que o **usuário** marcou como o mesmo rosto cadastrado com `person_unique_id` diferentes (migration 006). Nada aqui é automático: o collector não lê nem grava esta tabela. A marcação é feita no dashboard (checkbox no Tabuleiro ou na aba Pessoas → "Marcar como mesmo rosto" → `POST /api/duplicatas`), que liga a pessoa de menor `id` a cada uma das outras selecionadas (N-1 pares).

| Coluna | Tipo | Notas |
|--------|------|-------|
| `id` | SERIAL PK | |
| `pessoa_a_id` / `pessoa_b_id` | INTEGER NOT NULL → `pessoas.id` ON DELETE CASCADE | Sempre `pessoa_a_id < pessoa_b_id` (CHECK `chk_pd_ordem`) e `UNIQUE (pessoa_a_id, pessoa_b_id)` (`uq_pd_par`) — o mesmo par nunca aparece duas vezes nem invertido |
| `criado_em` | TIMESTAMPTZ | Quando o par foi marcado |

Índices: `uq_pd_par` (cobre buscas por `pessoa_a_id`), `idx_pd_pessoa_b`. Desfazer uma marcação = apagar o par (`DELETE /api/duplicatas/<id>`, botão "Remover" na aba Duplicatas); não há status nem soft-delete.

### `vw_grupos_duplicatas` (view)
Os pares são só as "ligações"; quando o mesmo rosto está em 3+ pessoas (A–B, A–C, ou marcações feitas em momentos diferentes como A–B e depois B–C), o grupo é o componente conexo do grafo de pares. A view resolve isso com um `WITH RECURSIVE` e devolve uma linha por pessoa marcada: `pessoa_id`, `grupo_id` (= menor `pessoas.id` do grupo) e `tamanho_grupo`. O grupo não é gravado em lugar nenhum de propósito: remover um par pode tirar uma pessoa do grupo ou quebrá-lo em dois, e a próxima consulta já sai certa sem recalcular nada.

O relatório (`GET /api/relatorio`) usa a view para contar **duplicatas excedentes**: pessoa criada no período que está num grupo e não é a mais antiga dele (`grupo_id <> pessoa_id`) — um cadastro a mais para um rosto que já existia. Um grupo de N pessoas conta N-1 excedentes.

### `sync_control`
Histórico de execuções do collector — **append-only desde a migration 005**: cada ciclo de coleta insere uma linha nova (`status="running"` no início, atualizada para `idle`/`error` no fim da mesma execução) em vez de reaproveitar/sobrescrever a última. `GET /api/status` continua lendo só a mais recente (`ORDER BY id DESC LIMIT 1`), mas agora dá pra consultar o histórico completo (`idx_sync_control_criado_em`).

Antes de 2026-08-20 esse histórico podia ter linhas "em par" (dois `criado_em` quase idênticos) porque `gunicorn.conf.py` sobe 2 workers e cada um iniciava seu próprio `BackgroundScheduler` — o mesmo ciclo rodava duas vezes em paralelo. Corrigido em `app/__init__.py` (`_acquire_scheduler_lock`, flock exclusivo por processo); ver "Coisas não óbvias" abaixo.

| Coluna | Tipo | Notas |
|--------|------|-------|
| `id` | SERIAL PK | |
| `ultimo_event_id` | VARCHAR(40) | `event_id` do último evento inserido com sucesso *nesta* execução |
| `ultimo_sync` | TIMESTAMPTZ | Timestamp do fim desta execução |
| `total_inseridos` | INTEGER DEFAULT 0 | Inseridos **nesta** execução (não é mais cumulativo — antes da migration 005 somava desde sempre) |
| `total_ignorados` | INTEGER DEFAULT 0 | Ignorados **nesta** execução (eventos que já existiam, duplicados por `event_id`) |
| `status` | VARCHAR(20) DEFAULT 'idle' | `idle` / `running` / `error` (CHECK `chk_sync_status`, migration 004) |
| `mensagem` | TEXT | Resumo textual ou erro |
| `criado_em` | TIMESTAMPTZ | Momento de início desta execução |

Para um total acumulado histórico, agregue com `SUM(total_inseridos)`/`SUM(total_ignorados)` sobre a tabela em vez de ler um único campo.

## Fluxo de escrita (`app/collector.py`, a cada `FACIAL_POLL_SECS`)

1. Insere uma nova linha em `sync_control` com `status="running"` (uma por execução, ver `sync_control` acima).
2. `GET {FACIAL_API_BASE}/api/face-events?limit=...&matches_limit=...` (Basic Auth).
3. Para cada evento do payload:
   - Se `event_id` já existe em `eventos_faciais` → conta como **ignorado**; se a linha existente não tinha `face_image_url` e o payload trouxe uma, faz backfill.
   - Senão: resolve/cria `Pessoa` por `person_unique_id` (dados demográficos só preenchem campos vazios, nunca sobrescrevem); atualiza `primeira_deteccao`/`ultima_deteccao`/`total_deteccoes` da pessoa; resolve `estabelecimento_id` pela câmera cadastrada (`cameras.estabelecimento_id`); se a câmera ainda não existe, usa o `store_id` da API e **auto-registra a câmera em `cameras`** com esse estabelecimento (`_ensure_camera`, necessário para a FK da migration 003); insere `EventoFacial`; insere um `EventoMatch` por item de `recognition.matches`; commit por evento (rollback isolado se um evento falhar, sem abortar o lote).
4. Ao final, atualiza a mesma linha de `sync_control` desta execução com os totais **deste ciclo**, `ultimo_event_id`, `ultimo_sync` e `status="idle"` (ou `"error"` se a chamada à API falhou).

## Coisas não óbvias / pegadinhas

- **`fk_ef_camera` e as CHECKs da migration 004 foram criadas `NOT VALID` mas já validadas** (ver "Status em produção" acima) — se você reaplicar isso em outro ambiente/banco (ex: um dev DB separado no futuro), lembre de rodar o `VALIDATE CONSTRAINT` de cada uma; sem isso o Postgres não garante conformidade das linhas que já existiam antes do `ALTER TABLE`.
- **`evento_matches.event_id_ref` pode "apontar para o nada".** A API manda matches contra eventos fora da janela `limit`/`matches_limit` coletada; `routes.py` trata isso fazendo lookup best-effort, não assume que sempre existe. Isso é normal e não indexado à parte — não há query que filtre `evento_matches` por esse campo, só o caminho inverso (`eventos_faciais.event_id IN (...)`, já coberto pelo `UNIQUE`).
- **Dados demográficos existem em dois lugares** (`pessoas.genero_estimado`/`faixa_etaria` vs `eventos_faciais.genero_estimado`/`faixa_etaria`): o de `pessoas` é "melhor palpite consolidado, primeiro valor não-nulo vence"; o de `eventos_faciais` é o snapshot daquele evento específico. Não espere que sejam sempre iguais.
- **`match_type` não tem CHECK constraint de propósito.** Ao contrário de gênero/status, o domínio de valores que a API Facial pode mandar em `match_type` não é garantido só `exact`/`new` para sempre; travar isso no banco arriscaria descartar eventos legítimos se a API mudar.
- **O `store_id` da API Facial não é confiável — o cadastro de `cameras` é a fonte de verdade do estabelecimento.** A API reporta câmeras sob estabelecimentos errados (ex: `cam_5` vinha como store 1, mas pertence ao Condomínio; `cam_8`/`cam_9` vinham como Condomínio, mas são de Porto Alegre). Até 2026-10-12 o collector gravava esse valor direto em `eventos_faciais.estabelecimento_id`, o que fazia o filtro Estabelecimento da tela Eventos Recentes trazer eventos de outras câmeras. Corrigido em três frentes: (1) o collector passou a copiar `cameras.estabelecimento_id`; (2) os filtros `store_id` de `/api/eventos` e `/api/pessoas-eventos` filtram por `camera_id IN (câmeras do estabelecimento)` (`_store_cameras_filter` em `routes.py`); (3) os 115 eventos antigos foram realinhados em produção com `UPDATE itumbiara.eventos_faciais e SET estabelecimento_id = c.estabelecimento_id FROM itumbiara.cameras c WHERE c.camera_id = e.camera_id AND e.estabelecimento_id IS DISTINCT FROM c.estabelecimento_id`. Se uma câmera mudar de estabelecimento no cadastro, rode esse mesmo `UPDATE` para realinhar os eventos dela. Câmeras novas são auto-registradas com o `store_id` da API — confira o estabelecimento delas no cadastro.
- **Datas ficam em UTC, mas os "dias" do usuário são de São Paulo.** `timestamp_evento` e os outros `TIMESTAMPTZ` são gravados/comparados em UTC; os filtros `date_from`/`date_to` (`_filtro_periodo` em `routes.py`) e o relatório cortam o dia em `America/Sao_Paulo` (`TZ_LOCAL`). Em SQL manual, agrupe/filtre por `(timestamp_evento AT TIME ZONE 'America/Sao_Paulo')::date`, não por `timestamp_evento::date` — senão as coletas depois das 21h caem no dia seguinte. Até 2026-10-24 os filtros de Eventos Recentes cortavam em UTC.
- **`nome` em `pessoas` é o único campo editável pelo usuário** via UI; todo o resto vem só da API/collector.
- **`collect_events` só roda em um processo por vez**, mesmo com múltiplos workers do gunicorn. `create_app()` (`app/__init__.py`) só registra o job do APScheduler no worker que conseguir um `flock` exclusivo em `/tmp/newface_scheduler.lock`; os outros workers seguem servindo HTTP normalmente, só não agendam a coleta. No Windows local (sempre processo único) isso é pulado. Sem esse lock, dois workers rodando o mesmo ciclo ao mesmo tempo causavam `UniqueViolation` esporádico ao inserir `event_id` e dobravam as chamadas à API Facial.
- **`POST /api/reset-base`** (botão "Reset Base" no header, só visível/permitido para a sessão com acesso total) roda `TRUNCATE itumbiara.eventos_faciais, itumbiara.pessoas RESTART IDENTITY CASCADE`. O `CASCADE` também esvazia `evento_matches` (FK para `eventos_faciais`) e `pessoas_duplicadas` (FK para `pessoas`) e o `RESTART IDENTITY` zera as sequences de `id` das três tabelas — a próxima pessoa/evento inserido volta a começar do `id=1`. `cameras`, `estabelecimentos` e `sync_control` **não** são tocados, então o histórico de execuções do collector sobrevive ao reset. As marcações de intrusa/indesejada (colunas de `eventos_faciais`) e de duplicata somem junto. Não há como desfazer — não existe soft-delete nem backup automático antes do truncate.

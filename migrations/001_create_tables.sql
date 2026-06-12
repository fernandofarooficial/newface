-- =============================================================
-- newface - DDL de criação das tabelas
-- Schema: itumbiara | Banco: Lojas
-- Execute no DBeaver conectado ao PostgreSQL 72.60.58.241
-- =============================================================

-- Garantir que o schema existe
CREATE SCHEMA IF NOT EXISTS itumbiara;

SET search_path TO itumbiara;

-- -------------------------------------------------------------
-- 1. Estabelecimentos (lojas)
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS itumbiara.estabelecimentos (
    id              SERIAL PRIMARY KEY,
    store_id        INTEGER NOT NULL UNIQUE,   -- ID vindo da API (store_id)
    nome            VARCHAR(120) NOT NULL,
    descricao       TEXT,
    ativo           BOOLEAN NOT NULL DEFAULT TRUE,
    criado_em       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    atualizado_em   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  itumbiara.estabelecimentos IS 'Cadastro dos estabelecimentos/lojas da rede';
COMMENT ON COLUMN itumbiara.estabelecimentos.store_id IS 'ID numérico retornado pela API Facial (store_id)';

-- -------------------------------------------------------------
-- 2. Cameras
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS itumbiara.cameras (
    id              SERIAL PRIMARY KEY,
    camera_id       VARCHAR(20) NOT NULL UNIQUE,  -- ex: cam_1, cam_3
    estabelecimento_id INTEGER NOT NULL REFERENCES itumbiara.estabelecimentos(id),
    nome            VARCHAR(120) NOT NULL,
    localizacao     VARCHAR(200),
    ativa           BOOLEAN NOT NULL DEFAULT TRUE,
    criado_em       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    atualizado_em   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  itumbiara.cameras IS 'Cadastro das câmeras com nome amigável';
COMMENT ON COLUMN itumbiara.cameras.camera_id IS 'ID público da câmera no formato cam_1 (retornado pela API)';

CREATE INDEX IF NOT EXISTS idx_cameras_estabelecimento ON itumbiara.cameras(estabelecimento_id);

-- -------------------------------------------------------------
-- 3. Pessoas (identidades reconhecidas)
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS itumbiara.pessoas (
    id                  SERIAL PRIMARY KEY,
    person_unique_id    VARCHAR(20) NOT NULL UNIQUE,  -- ex: cli_000001
    nome                VARCHAR(200),                  -- preenchido manualmente
    genero_estimado     VARCHAR(10),                   -- male / female / null
    faixa_etaria        VARCHAR(20),                   -- ex: 25-31
    primeira_deteccao   TIMESTAMPTZ,
    ultima_deteccao     TIMESTAMPTZ,
    total_deteccoes     INTEGER NOT NULL DEFAULT 0,
    ativo               BOOLEAN NOT NULL DEFAULT TRUE,
    criado_em           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    atualizado_em       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  itumbiara.pessoas IS 'Identidades únicas reconhecidas pelo sistema de reconhecimento facial';
COMMENT ON COLUMN itumbiara.pessoas.person_unique_id IS 'ID público da pessoa no formato cli_000001 (retornado pela API)';
COMMENT ON COLUMN itumbiara.pessoas.nome IS 'Nome real preenchido manualmente após identificação';

CREATE INDEX IF NOT EXISTS idx_pessoas_person_unique_id ON itumbiara.pessoas(person_unique_id);
CREATE INDEX IF NOT EXISTS idx_pessoas_ultima_deteccao  ON itumbiara.pessoas(ultima_deteccao DESC);

-- -------------------------------------------------------------
-- 4. Eventos de reconhecimento facial
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS itumbiara.eventos_faciais (
    id                  SERIAL PRIMARY KEY,
    event_id            VARCHAR(40) NOT NULL UNIQUE,  -- evt-20260610-000123
    timestamp_evento    TIMESTAMPTZ NOT NULL,
    pessoa_id           INTEGER REFERENCES itumbiara.pessoas(id),
    person_unique_id    VARCHAR(20),                  -- redundância para joins rápidos
    camera_id           VARCHAR(20),
    estabelecimento_id  INTEGER REFERENCES itumbiara.estabelecimentos(id),
    person_track_id     VARCHAR(40),
    matched             BOOLEAN,                      -- true = pessoa conhecida
    match_score         NUMERIC(5,4),                 -- similaridade 0.0000 a 1.0000
    match_type          VARCHAR(20),                  -- exact / new
    person_count_frame  INTEGER,
    genero_estimado     VARCHAR(10),
    faixa_etaria        VARCHAR(20),
    face_quality        NUMERIC(5,4),
    model_version       VARCHAR(30),
    processing_node     VARCHAR(60),
    criado_em           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  itumbiara.eventos_faciais IS 'Registro de cada evento de detecção/reconhecimento facial recebido da API';
COMMENT ON COLUMN itumbiara.eventos_faciais.matched IS 'true = pessoa já existia na base; false = nova pessoa criada';
COMMENT ON COLUMN itumbiara.eventos_faciais.match_score IS 'Similaridade entre 0 e 1 retornada pela API';

CREATE INDEX IF NOT EXISTS idx_ef_timestamp       ON itumbiara.eventos_faciais(timestamp_evento DESC);
CREATE INDEX IF NOT EXISTS idx_ef_pessoa          ON itumbiara.eventos_faciais(pessoa_id);
CREATE INDEX IF NOT EXISTS idx_ef_person_unique   ON itumbiara.eventos_faciais(person_unique_id);
CREATE INDEX IF NOT EXISTS idx_ef_camera          ON itumbiara.eventos_faciais(camera_id);
CREATE INDEX IF NOT EXISTS idx_ef_estabelecimento ON itumbiara.eventos_faciais(estabelecimento_id);
CREATE INDEX IF NOT EXISTS idx_ef_match_type      ON itumbiara.eventos_faciais(match_type);

-- -------------------------------------------------------------
-- 5. Correspondências entre eventos (matches históricos)
--    Cada evento pode trazer N ocorrências anteriores da mesma pessoa
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS itumbiara.evento_matches (
    id                  SERIAL PRIMARY KEY,
    evento_id           INTEGER NOT NULL REFERENCES itumbiara.eventos_faciais(id) ON DELETE CASCADE,
    event_id_ref        VARCHAR(40) NOT NULL,   -- event_id do evento anterior (pode não estar na base)
    timestamp_ref       TIMESTAMPTZ,
    camera_id_ref       VARCHAR(20),
    store_id_ref        INTEGER,
    similarity          NUMERIC(5,4),
    criado_em           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  itumbiara.evento_matches IS 'Ocorrências anteriores da mesma pessoa vinculadas a um evento (recognition.matches da API)';

CREATE INDEX IF NOT EXISTS idx_em_evento ON itumbiara.evento_matches(evento_id);

-- -------------------------------------------------------------
-- 6. Controle de sincronização
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS itumbiara.sync_control (
    id              SERIAL PRIMARY KEY,
    ultimo_event_id VARCHAR(40),
    ultimo_sync     TIMESTAMPTZ,
    total_inseridos INTEGER NOT NULL DEFAULT 0,
    total_ignorados INTEGER NOT NULL DEFAULT 0,
    status          VARCHAR(20) DEFAULT 'idle',   -- idle / running / error
    mensagem        TEXT,
    criado_em       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO itumbiara.sync_control (status) VALUES ('idle');

-- -------------------------------------------------------------
-- Dados de exemplo para câmeras e estabelecimentos
-- (ajuste conforme sua realidade)
-- -------------------------------------------------------------
INSERT INTO itumbiara.estabelecimentos (store_id, nome, descricao)
VALUES (1, 'Loja Centro', 'Estabelecimento principal')
ON CONFLICT (store_id) DO NOTHING;

INSERT INTO itumbiara.cameras (camera_id, estabelecimento_id, nome, localizacao)
VALUES
    ('cam_1', 1, 'Entrada Principal', 'Porta frontal'),
    ('cam_2', 1, 'Entrada Lateral',   'Porta lateral'),
    ('cam_3', 1, 'Caixa',             'Área do caixa')
ON CONFLICT (camera_id) DO NOTHING;

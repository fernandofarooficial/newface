-- =============================================================
-- 006 - Pessoas marcadas pelo usuário como o mesmo rosto
-- Execute no DBeaver/psql conectado ao PostgreSQL 72.60.58.241
-- =============================================================

-- A API Facial às vezes cria dois (ou mais) person_unique_id para o mesmo
-- rosto. O usuário aponta isso no dashboard selecionando as pessoas e
-- clicando "Marcar como mesmo rosto" — cada marcação grava pares (A, B).
-- A tabela guarda só os pares; os grupos de 3+ pessoas são calculados pela
-- view vw_grupos_duplicatas abaixo (componentes conexos do grafo de pares).
CREATE TABLE IF NOT EXISTS itumbiara.pessoas_duplicadas (
    id           SERIAL PRIMARY KEY,
    pessoa_a_id  INTEGER NOT NULL REFERENCES itumbiara.pessoas(id) ON DELETE CASCADE,
    pessoa_b_id  INTEGER NOT NULL REFERENCES itumbiara.pessoas(id) ON DELETE CASCADE,
    criado_em    TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT uq_pd_par    UNIQUE (pessoa_a_id, pessoa_b_id),
    CONSTRAINT chk_pd_ordem CHECK (pessoa_a_id < pessoa_b_id)
);

-- uq_pd_par já cobre buscas por pessoa_a_id; este cobre o outro lado.
CREATE INDEX IF NOT EXISTS idx_pd_pessoa_b ON itumbiara.pessoas_duplicadas(pessoa_b_id);

COMMENT ON TABLE itumbiara.pessoas_duplicadas IS
    'Pares de pessoas marcados pelo usuário como o mesmo rosto. pessoa_a_id < pessoa_b_id sempre';

-- Grupo de cada pessoa marcada = menor pessoas.id alcançável pelos pares.
-- UNION (não UNION ALL) é o que garante o fim da recursão.
CREATE OR REPLACE VIEW itumbiara.vw_grupos_duplicatas AS
WITH RECURSIVE arestas AS (
    SELECT pessoa_a_id AS de, pessoa_b_id AS para FROM itumbiara.pessoas_duplicadas
    UNION
    SELECT pessoa_b_id, pessoa_a_id FROM itumbiara.pessoas_duplicadas
),
alcance AS (
    SELECT de AS pessoa_id, de AS raiz FROM arestas
    UNION
    SELECT a.pessoa_id, ar.para
      FROM alcance a JOIN arestas ar ON ar.de = a.raiz
),
grupos AS (
    SELECT pessoa_id, MIN(raiz) AS grupo_id FROM alcance GROUP BY pessoa_id
)
SELECT pessoa_id,
       grupo_id,
       COUNT(*) OVER (PARTITION BY grupo_id) AS tamanho_grupo
  FROM grupos;

COMMENT ON VIEW itumbiara.vw_grupos_duplicatas IS
    'Uma linha por pessoa marcada como duplicata: grupo_id = menor pessoas.id do grupo conexo';

-- Conferência:
--   SELECT * FROM itumbiara.vw_grupos_duplicatas ORDER BY grupo_id, pessoa_id;

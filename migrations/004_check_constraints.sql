-- =============================================================
-- 004 - CHECK constraints de domínio
-- Execute no DBeaver/psql conectado ao PostgreSQL 72.60.58.241
-- =============================================================

-- Todas NOT VALID: passam a valer para todo INSERT/UPDATE novo a partir de
-- agora, sem varrer/travar a tabela validando as linhas já existentes.
-- match_type NÃO tem CHECK aqui de propósito: a API Facial pode introduzir
-- novos valores além de "exact"/"new" sem aviso prévio, e travar esse campo
-- faria o collector descartar eventos legítimos por engano.

ALTER TABLE itumbiara.pessoas
    ADD CONSTRAINT chk_pessoas_genero
    CHECK (genero_estimado IS NULL OR genero_estimado IN ('male', 'female'))
    NOT VALID;

ALTER TABLE itumbiara.eventos_faciais
    ADD CONSTRAINT chk_ef_genero
    CHECK (genero_estimado IS NULL OR genero_estimado IN ('male', 'female'))
    NOT VALID;

ALTER TABLE itumbiara.eventos_faciais
    ADD CONSTRAINT chk_ef_match_score
    CHECK (match_score IS NULL OR (match_score >= 0 AND match_score <= 1))
    NOT VALID;

ALTER TABLE itumbiara.eventos_faciais
    ADD CONSTRAINT chk_ef_face_quality
    CHECK (face_quality IS NULL OR (face_quality >= 0 AND face_quality <= 1))
    NOT VALID;

ALTER TABLE itumbiara.evento_matches
    ADD CONSTRAINT chk_em_similarity
    CHECK (similarity IS NULL OR (similarity >= 0 AND similarity <= 1))
    NOT VALID;

ALTER TABLE itumbiara.sync_control
    ADD CONSTRAINT chk_sync_status
    CHECK (status IS NULL OR status IN ('idle', 'running', 'error'))
    NOT VALID;

-- -------------------------------------------------------------
-- Passo manual: confira se algum dado existente já viola as regras antes
-- de validar (senão o VALIDATE abaixo falha e aponta a constraint culpada):
--
--   SELECT DISTINCT genero_estimado FROM itumbiara.pessoas
--     WHERE genero_estimado IS NOT NULL AND genero_estimado NOT IN ('male','female');
--   SELECT DISTINCT genero_estimado FROM itumbiara.eventos_faciais
--     WHERE genero_estimado IS NOT NULL AND genero_estimado NOT IN ('male','female');
--   SELECT event_id, match_score FROM itumbiara.eventos_faciais
--     WHERE match_score IS NOT NULL AND (match_score < 0 OR match_score > 1);
--   SELECT event_id, face_quality FROM itumbiara.eventos_faciais
--     WHERE face_quality IS NOT NULL AND (face_quality < 0 OR face_quality > 1);
--   SELECT id, similarity FROM itumbiara.evento_matches
--     WHERE similarity IS NOT NULL AND (similarity < 0 OR similarity > 1);
--   SELECT DISTINCT status FROM itumbiara.sync_control
--     WHERE status IS NOT NULL AND status NOT IN ('idle','running','error');
--
-- Se todas vierem vazias:
--
--   ALTER TABLE itumbiara.pessoas          VALIDATE CONSTRAINT chk_pessoas_genero;
--   ALTER TABLE itumbiara.eventos_faciais  VALIDATE CONSTRAINT chk_ef_genero;
--   ALTER TABLE itumbiara.eventos_faciais  VALIDATE CONSTRAINT chk_ef_match_score;
--   ALTER TABLE itumbiara.eventos_faciais  VALIDATE CONSTRAINT chk_ef_face_quality;
--   ALTER TABLE itumbiara.evento_matches   VALIDATE CONSTRAINT chk_em_similarity;
--   ALTER TABLE itumbiara.sync_control     VALIDATE CONSTRAINT chk_sync_status;
-- -------------------------------------------------------------

-- =============================================================
-- 007 - Captura intrusa (rosto associado à pessoa errada)
-- Execute no DBeaver/psql conectado ao PostgreSQL 72.60.58.241
-- =============================================================

-- O usuário marca no dashboard um evento cuja foto é de outra pessoa, mas
-- que a API Facial associou a este person_unique_id. O evento continua
-- ligado à pessoa (pessoa_id não muda); a marcação só o sinaliza e o tira
-- da escolha da "primeira foto" do Tabuleiro. Default constante: no
-- PostgreSQL 11+ o ADD COLUMN é instantâneo, sem reescrever a tabela.
ALTER TABLE itumbiara.eventos_faciais
    ADD COLUMN IF NOT EXISTS intruso    BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS intruso_em TIMESTAMPTZ;

COMMENT ON COLUMN itumbiara.eventos_faciais.intruso IS
    'true = usuário marcou que o rosto desta captura não é da pessoa associada';
COMMENT ON COLUMN itumbiara.eventos_faciais.intruso_em IS
    'Quando a captura foi marcada como intrusa (null se não marcada)';

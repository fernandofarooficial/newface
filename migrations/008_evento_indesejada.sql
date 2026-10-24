-- =============================================================
-- 008 - Coleta indesejada (captura com qualidade/condição ruim)
-- Execute no DBeaver/psql conectado ao PostgreSQL 72.60.58.241
-- =============================================================

-- O usuário marca no dashboard um evento cuja foto é da pessoa certa, mas
-- foi coletada em condição indesejada (de costas, rosto encoberto, chapéu
-- grande, baixa resolução, pouca luz...). Independente de `intruso`
-- (migration 007): uma captura pode ser uma, outra, ambas ou nenhuma. Como
-- a intrusa, só sinaliza e tira a captura da escolha da "primeira foto" do
-- Tabuleiro. Default constante: ADD COLUMN instantâneo no PostgreSQL 11+.
ALTER TABLE itumbiara.eventos_faciais
    ADD COLUMN IF NOT EXISTS indesejada    BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS indesejada_em TIMESTAMPTZ;

COMMENT ON COLUMN itumbiara.eventos_faciais.indesejada IS
    'true = usuário marcou a captura como coleta indesejada (de costas, encoberta, baixa resolução/luz...)';
COMMENT ON COLUMN itumbiara.eventos_faciais.indesejada_em IS
    'Quando a captura foi marcada como indesejada (null se não marcada)';

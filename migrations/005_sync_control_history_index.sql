-- =============================================================
-- 005 - Índice de apoio ao histórico de sync_control
-- Execute no DBeaver/psql conectado ao PostgreSQL 72.60.58.241
-- =============================================================

-- app/collector.py agora insere uma linha nova em sync_control a cada
-- execução (histórico append-only) em vez de reaproveitar/sobrescrever a
-- última linha. Este índice acelera consultas de histórico por período,
-- ex: SELECT * FROM sync_control WHERE criado_em > now() - interval '1 day'.
CREATE INDEX IF NOT EXISTS idx_sync_control_criado_em ON itumbiara.sync_control(criado_em DESC);

COMMENT ON TABLE itumbiara.sync_control IS 'Histórico de execuções do collector: uma linha por ciclo de coleta (append-only desde a migration 005)';

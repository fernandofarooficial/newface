-- =============================================================
-- 003 - FK de eventos_faciais.camera_id -> cameras.camera_id
-- Execute no DBeaver/psql conectado ao PostgreSQL 72.60.58.241
-- =============================================================

-- Backfill: registra em `cameras` qualquer camera_id já visto em eventos
-- mas ainda não cadastrado manualmente, usando o estabelecimento do próprio
-- evento. Necessário para a FK abaixo não rejeitar dados existentes.
-- Eventos cujo estabelecimento_id também não foi resolvido (raro) ficam de
-- fora do backfill e são reportados pela query de checagem mais abaixo.
INSERT INTO itumbiara.cameras (camera_id, estabelecimento_id, nome)
SELECT DISTINCT ef.camera_id, ef.estabelecimento_id, ef.camera_id
FROM itumbiara.eventos_faciais ef
WHERE ef.camera_id IS NOT NULL
  AND ef.estabelecimento_id IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM itumbiara.cameras c WHERE c.camera_id = ef.camera_id
  )
ON CONFLICT (camera_id) DO NOTHING;

-- Adicionada NOT VALID: aplica a partir de agora (todo INSERT/UPDATE novo
-- precisa satisfazer a FK) sem varrer/travar a tabela toda validando as
-- linhas existentes. app/collector.py já foi ajustado para auto-registrar
-- em `cameras` qualquer camera_id novo antes de inserir o evento
-- (ver _ensure_camera), então esta constraint não deve mais falhar em
-- produção depois deste deploy.
ALTER TABLE itumbiara.eventos_faciais
    ADD CONSTRAINT fk_ef_camera
    FOREIGN KEY (camera_id) REFERENCES itumbiara.cameras(camera_id)
    NOT VALID;

-- -------------------------------------------------------------
-- Passo manual: depois de rodar o backfill acima, confira se ainda
-- sobra alguma linha órfã (evento com camera_id sem estabelecimento_id
-- resolvido, então não coberto pelo INSERT acima):
--
--   SELECT DISTINCT ef.camera_id
--   FROM itumbiara.eventos_faciais ef
--   WHERE ef.camera_id IS NOT NULL
--     AND NOT EXISTS (SELECT 1 FROM itumbiara.cameras c WHERE c.camera_id = ef.camera_id);
--
-- Se vier vazio, valide a constraint (passa a checar todas as linhas,
-- mas sem bloquear escritas concorrentes como um ALTER normal faria):
--
--   ALTER TABLE itumbiara.eventos_faciais VALIDATE CONSTRAINT fk_ef_camera;
-- -------------------------------------------------------------

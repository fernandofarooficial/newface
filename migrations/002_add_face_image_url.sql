-- Adiciona coluna face_image_url em eventos_faciais
ALTER TABLE itumbiara.eventos_faciais
    ADD COLUMN IF NOT EXISTS face_image_url TEXT;

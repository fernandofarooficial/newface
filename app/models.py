from datetime import datetime
from . import db


class Estabelecimento(db.Model):
    __tablename__ = "estabelecimentos"
    __table_args__ = {"schema": "itumbiara"}

    id             = db.Column(db.Integer, primary_key=True)
    store_id       = db.Column(db.Integer, nullable=False, unique=True)
    nome           = db.Column(db.String(120), nullable=False)
    descricao      = db.Column(db.Text)
    ativo          = db.Column(db.Boolean, default=True)
    criado_em      = db.Column(db.DateTime(timezone=True), default=datetime.utcnow)
    atualizado_em  = db.Column(db.DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    cameras  = db.relationship("Camera", back_populates="estabelecimento", lazy="dynamic")
    eventos  = db.relationship("EventoFacial", back_populates="estabelecimento", lazy="dynamic")

    def to_dict(self):
        return {"id": self.id, "store_id": self.store_id, "nome": self.nome, "ativo": self.ativo}


class Camera(db.Model):
    __tablename__ = "cameras"
    __table_args__ = {"schema": "itumbiara"}

    id                 = db.Column(db.Integer, primary_key=True)
    camera_id          = db.Column(db.String(20), nullable=False, unique=True)
    estabelecimento_id = db.Column(db.Integer, db.ForeignKey("itumbiara.estabelecimentos.id"), nullable=False)
    nome               = db.Column(db.String(120), nullable=False)
    localizacao        = db.Column(db.String(200))
    ativa              = db.Column(db.Boolean, default=True)
    criado_em          = db.Column(db.DateTime(timezone=True), default=datetime.utcnow)
    atualizado_em      = db.Column(db.DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    estabelecimento = db.relationship("Estabelecimento", back_populates="cameras")

    def to_dict(self):
        return {"id": self.id, "camera_id": self.camera_id, "nome": self.nome,
                "localizacao": self.localizacao, "ativa": self.ativa,
                "estabelecimento_id": self.estabelecimento_id}


class Pessoa(db.Model):
    __tablename__ = "pessoas"
    __table_args__ = {"schema": "itumbiara"}

    id               = db.Column(db.Integer, primary_key=True)
    person_unique_id = db.Column(db.String(20), nullable=False, unique=True)
    nome             = db.Column(db.String(200))
    genero_estimado  = db.Column(db.String(10))
    faixa_etaria     = db.Column(db.String(20))
    primeira_deteccao= db.Column(db.DateTime(timezone=True))
    ultima_deteccao  = db.Column(db.DateTime(timezone=True))
    total_deteccoes  = db.Column(db.Integer, default=0)
    ativo            = db.Column(db.Boolean, default=True)
    criado_em        = db.Column(db.DateTime(timezone=True), default=datetime.utcnow)
    atualizado_em    = db.Column(db.DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    eventos = db.relationship("EventoFacial", back_populates="pessoa", lazy="dynamic")

    def to_dict(self):
        return {
            "id": self.id,
            "person_unique_id": self.person_unique_id,
            "nome": self.nome or "—",
            "genero_estimado": self.genero_estimado,
            "faixa_etaria": self.faixa_etaria,
            "primeira_deteccao": self.primeira_deteccao.isoformat() if self.primeira_deteccao else None,
            "ultima_deteccao": self.ultima_deteccao.isoformat() if self.ultima_deteccao else None,
            "total_deteccoes": self.total_deteccoes,
        }


class EventoFacial(db.Model):
    __tablename__ = "eventos_faciais"
    __table_args__ = {"schema": "itumbiara"}

    id                 = db.Column(db.Integer, primary_key=True)
    event_id           = db.Column(db.String(40), nullable=False, unique=True)
    timestamp_evento   = db.Column(db.DateTime(timezone=True), nullable=False)
    pessoa_id          = db.Column(db.Integer, db.ForeignKey("itumbiara.pessoas.id"))
    person_unique_id   = db.Column(db.String(20))
    camera_id          = db.Column(db.String(20))
    estabelecimento_id = db.Column(db.Integer, db.ForeignKey("itumbiara.estabelecimentos.id"))
    person_track_id    = db.Column(db.String(40))
    matched            = db.Column(db.Boolean)
    match_score        = db.Column(db.Numeric(5, 4))
    match_type         = db.Column(db.String(20))
    person_count_frame = db.Column(db.Integer)
    genero_estimado    = db.Column(db.String(10))
    faixa_etaria       = db.Column(db.String(20))
    face_quality       = db.Column(db.Numeric(5, 4))
    face_image_url     = db.Column(db.Text)
    model_version      = db.Column(db.String(30))
    processing_node    = db.Column(db.String(60))
    criado_em          = db.Column(db.DateTime(timezone=True), default=datetime.utcnow)

    pessoa         = db.relationship("Pessoa", back_populates="eventos")
    estabelecimento= db.relationship("Estabelecimento", back_populates="eventos")
    matches        = db.relationship("EventoMatch", back_populates="evento", cascade="all, delete-orphan")

    def to_dict(self, match_images=None):
        imgs = match_images or {}
        return {
            "id": self.id,
            "event_id": self.event_id,
            "timestamp_evento": self.timestamp_evento.isoformat() if self.timestamp_evento else None,
            "person_unique_id": self.person_unique_id,
            "pessoa_nome": self.pessoa.nome if self.pessoa and self.pessoa.nome else "—",
            "camera_id": self.camera_id,
            "matched": self.matched,
            "match_score": float(self.match_score) if self.match_score else None,
            "match_type": self.match_type,
            "genero_estimado": self.genero_estimado,
            "faixa_etaria": self.faixa_etaria,
            "face_quality": float(self.face_quality) if self.face_quality else None,
            "face_image_url": self.face_image_url,
            "matches": [
                {
                    "event_id_ref": m.event_id_ref,
                    "timestamp_ref": m.timestamp_ref.isoformat() if m.timestamp_ref else None,
                    "camera_id_ref": m.camera_id_ref,
                    "similarity": float(m.similarity) if m.similarity else None,
                    "face_image_url": imgs.get(m.event_id_ref),
                }
                for m in self.matches
            ],
        }


class EventoMatch(db.Model):
    __tablename__ = "evento_matches"
    __table_args__ = {"schema": "itumbiara"}

    id            = db.Column(db.Integer, primary_key=True)
    evento_id     = db.Column(db.Integer, db.ForeignKey("itumbiara.eventos_faciais.id"), nullable=False)
    event_id_ref  = db.Column(db.String(40), nullable=False)
    timestamp_ref = db.Column(db.DateTime(timezone=True))
    camera_id_ref = db.Column(db.String(20))
    store_id_ref  = db.Column(db.Integer)
    similarity    = db.Column(db.Numeric(5, 4))
    criado_em     = db.Column(db.DateTime(timezone=True), default=datetime.utcnow)

    evento = db.relationship("EventoFacial", back_populates="matches")


class SyncControl(db.Model):
    __tablename__ = "sync_control"
    __table_args__ = {"schema": "itumbiara"}

    id              = db.Column(db.Integer, primary_key=True)
    ultimo_event_id = db.Column(db.String(40))
    ultimo_sync     = db.Column(db.DateTime(timezone=True))
    total_inseridos = db.Column(db.Integer, default=0)
    total_ignorados = db.Column(db.Integer, default=0)
    status          = db.Column(db.String(20), default="idle")
    mensagem        = db.Column(db.Text)
    criado_em       = db.Column(db.DateTime(timezone=True), default=datetime.utcnow)

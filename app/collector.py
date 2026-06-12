"""
collector.py
Responsável por consultar a API Facial e persistir os eventos no PostgreSQL.
Roda em thread background via APScheduler.
"""
import logging
from datetime import datetime, timezone
from dateutil import parser as dateparser

import requests
from requests.auth import HTTPBasicAuth

from . import db
from .models import Pessoa, EventoFacial, EventoMatch, SyncControl, Estabelecimento, Camera
from .config import Config

logger = logging.getLogger("newface.collector")


def _get_or_create_pessoa(session, recognition: dict, demographics: dict) -> Pessoa:
    """Busca ou cria o registro de Pessoa a partir dos dados de reconhecimento."""
    pid = recognition.get("person_unique_id")
    if not pid:
        return None

    pessoa = session.query(Pessoa).filter_by(person_unique_id=pid).first()
    if not pessoa:
        pessoa = Pessoa(
            person_unique_id=pid,
            genero_estimado=demographics.get("predicted_gender"),
            faixa_etaria=demographics.get("predicted_age_range"),
            total_deteccoes=0,
        )
        session.add(pessoa)
        session.flush()
        logger.info(f"Nova pessoa criada: {pid}")
    else:
        # Atualiza dados demográficos se ainda não preenchidos
        if not pessoa.genero_estimado and demographics.get("predicted_gender"):
            pessoa.genero_estimado = demographics.get("predicted_gender")
        if not pessoa.faixa_etaria and demographics.get("predicted_age_range"):
            pessoa.faixa_etaria = demographics.get("predicted_age_range")

    return pessoa


def _get_estabelecimento_id(session, store_id: int) -> int | None:
    est = session.query(Estabelecimento).filter_by(store_id=store_id).first()
    return est.id if est else None


def _parse_ts(ts_str: str):
    if not ts_str:
        return None
    try:
        return dateparser.parse(ts_str)
    except Exception:
        return None


def collect_events(app) -> dict:
    """
    Consulta a API Facial, insere eventos novos no banco.
    Retorna resumo: {inseridos, ignorados, erros}
    """
    cfg = Config()
    base_url   = cfg.FACIAL_API_BASE
    auth       = HTTPBasicAuth(cfg.FACIAL_API_USER, cfg.FACIAL_API_PASS)
    limit      = cfg.FACIAL_LIMIT
    m_limit    = cfg.FACIAL_MATCHES_LIM

    result = {"inseridos": 0, "ignorados": 0, "erros": 0}

    with app.app_context():
        sync = db.session.query(SyncControl).order_by(SyncControl.id.desc()).first()
        if not sync:
            sync = SyncControl(status="running")
            db.session.add(sync)
        else:
            sync.status = "running"
        db.session.commit()

        try:
            url = f"{base_url}/api/face-events?limit={limit}&matches_limit={m_limit}"
            resp = requests.get(url, auth=auth, timeout=15)
            resp.raise_for_status()
            events = resp.json()
        except Exception as e:
            logger.error(f"Erro ao chamar API Facial: {e}")
            sync.status = "error"
            sync.mensagem = str(e)
            db.session.commit()
            return result

        for ev in events:
            event_id = ev.get("event_id")
            if not event_id:
                continue

            # Verifica duplicata
            exists = db.session.query(EventoFacial).filter_by(event_id=event_id).first()
            if exists:
                if exists.face_image_url is None and ev.get("face_image_url"):
                    exists.face_image_url = ev.get("face_image_url")
                    db.session.commit()
                result["ignorados"] += 1
                continue

            try:
                recognition  = ev.get("recognition") or {}
                demographics = ev.get("demographics") or {}
                system_info  = ev.get("system") or {}

                pessoa = _get_or_create_pessoa(db.session, recognition, demographics)
                ts = _parse_ts(ev.get("timestamp"))

                # Atualiza estatísticas da pessoa
                if pessoa:
                    if not pessoa.primeira_deteccao or (ts and ts < pessoa.primeira_deteccao):
                        pessoa.primeira_deteccao = ts
                    if not pessoa.ultima_deteccao or (ts and ts > pessoa.ultima_deteccao):
                        pessoa.ultima_deteccao = ts
                    pessoa.total_deteccoes = (pessoa.total_deteccoes or 0) + 1

                est_id = _get_estabelecimento_id(db.session, ev.get("store_id"))

                evento = EventoFacial(
                    event_id           = event_id,
                    timestamp_evento   = ts,
                    pessoa_id          = pessoa.id if pessoa else None,
                    person_unique_id   = recognition.get("person_unique_id"),
                    camera_id          = ev.get("camera_id"),
                    estabelecimento_id = est_id,
                    person_track_id    = ev.get("person_track_id"),
                    matched            = recognition.get("matched"),
                    match_score        = recognition.get("match_score"),
                    match_type         = recognition.get("match_type"),
                    person_count_frame = ev.get("person_count_frame"),
                    genero_estimado    = demographics.get("predicted_gender"),
                    faixa_etaria       = demographics.get("predicted_age_range"),
                    face_quality       = ev.get("face_quality"),
                    face_image_url     = ev.get("face_image_url"),
                    model_version      = system_info.get("model_version"),
                    processing_node    = system_info.get("processing_node"),
                )
                db.session.add(evento)
                db.session.flush()

                # Persiste os matches históricos
                for m in recognition.get("matches", []):
                    em = EventoMatch(
                        evento_id    = evento.id,
                        event_id_ref = m.get("event_id", ""),
                        timestamp_ref= _parse_ts(m.get("timestamp")),
                        camera_id_ref= m.get("camera_id"),
                        store_id_ref = m.get("store_id"),
                        similarity   = m.get("similarity"),
                    )
                    db.session.add(em)

                db.session.commit()
                result["inseridos"] += 1
                sync.ultimo_event_id = event_id

            except Exception as e:
                db.session.rollback()
                logger.error(f"Erro ao inserir evento {event_id}: {e}")
                result["erros"] += 1

        sync.ultimo_sync     = datetime.now(timezone.utc)
        sync.total_inseridos = (sync.total_inseridos or 0) + result["inseridos"]
        sync.total_ignorados = (sync.total_ignorados or 0) + result["ignorados"]
        sync.status          = "idle"
        sync.mensagem        = f"Última coleta: {result['inseridos']} novos, {result['ignorados']} ignorados"
        db.session.commit()

    logger.info(f"Coleta finalizada: {result}")
    return result

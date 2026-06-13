from datetime import datetime, timezone, timedelta

from flask import Blueprint, jsonify, render_template, request, current_app, Response
from sqlalchemy import func, desc
import requests as http_requests
from requests.auth import HTTPBasicAuth
from . import db
from .models import EventoFacial, Pessoa, Camera, Estabelecimento, SyncControl
from .collector import collect_events
from .config import Config

bp = Blueprint("main", __name__)


# ------------------------------------------------------------------
# Dashboard
# ------------------------------------------------------------------
@bp.route("/")
def index():
    return render_template("index.html")


# ------------------------------------------------------------------
# API - Status de sincronização
# ------------------------------------------------------------------
@bp.route("/api/status")
def status():
    sync = db.session.query(SyncControl).order_by(SyncControl.id.desc()).first()
    total_eventos  = db.session.query(func.count(EventoFacial.id)).scalar()
    total_pessoas  = db.session.query(func.count(Pessoa.id)).scalar()
    total_novas    = db.session.query(func.count(EventoFacial.id)).filter(EventoFacial.match_type == "new").scalar()
    total_conhecidas = db.session.query(func.count(EventoFacial.id)).filter(EventoFacial.match_type == "exact").scalar()

    return jsonify({
        "sync": {
            "status": sync.status if sync else "—",
            "ultimo_sync": sync.ultimo_sync.isoformat() if sync and sync.ultimo_sync else None,
            "mensagem": sync.mensagem if sync else None,
            "total_inseridos": sync.total_inseridos if sync else 0,
        },
        "stats": {
            "total_eventos": total_eventos,
            "total_pessoas": total_pessoas,
            "deteccoes_novas": total_novas,
            "deteccoes_conhecidas": total_conhecidas,
        }
    })


# ------------------------------------------------------------------
# API - Eventos recentes
# ------------------------------------------------------------------
@bp.route("/api/eventos")
def eventos():
    limit  = min(int(request.args.get("limit", 50)), 200)
    offset = int(request.args.get("offset", 0))

    q = db.session.query(EventoFacial).order_by(desc(EventoFacial.timestamp_evento))

    camera_id = request.args.get("camera_id")
    if camera_id:
        q = q.filter(EventoFacial.camera_id == camera_id)

    store_id = request.args.get("store_id")
    if store_id:
        q = q.filter(EventoFacial.estabelecimento_id == store_id)

    match_type = request.args.get("match_type")
    if match_type:
        q = q.filter(EventoFacial.match_type == match_type)

    pessoa_id = request.args.get("pessoa_id")
    if pessoa_id:
        q = q.filter(EventoFacial.pessoa_id == pessoa_id)

    date_from = request.args.get("date_from")
    if date_from:
        try:
            dt = datetime.strptime(date_from, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            q = q.filter(EventoFacial.timestamp_evento >= dt)
        except ValueError:
            pass

    date_to = request.args.get("date_to")
    if date_to:
        try:
            dt = datetime.strptime(date_to, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
            q = q.filter(EventoFacial.timestamp_evento < dt)
        except ValueError:
            pass

    total = q.count()
    items = q.offset(offset).limit(limit).all()

    return jsonify({
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [e.to_dict() for e in items]
    })


# ------------------------------------------------------------------
# API - Pessoas
# ------------------------------------------------------------------
@bp.route("/api/pessoas")
def pessoas():
    limit  = min(int(request.args.get("limit", 50)), 200)
    offset = int(request.args.get("offset", 0))

    q = db.session.query(Pessoa).order_by(desc(Pessoa.ultima_deteccao))
    total = q.count()
    items = q.offset(offset).limit(limit).all()

    return jsonify({
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [p.to_dict() for p in items]
    })


@bp.route("/api/pessoas/<int:pessoa_id>/nome", methods=["POST"])
def atualizar_nome(pessoa_id):
    data  = request.get_json()
    nome  = (data or {}).get("nome", "").strip()
    p     = db.session.get(Pessoa, pessoa_id)
    if not p:
        return jsonify({"error": "Pessoa não encontrada"}), 404
    p.nome = nome
    db.session.commit()
    return jsonify({"ok": True, "nome": p.nome})


# ------------------------------------------------------------------
# API - Coleta manual
# ------------------------------------------------------------------
@bp.route("/api/coletar", methods=["POST"])
def coletar():
    result = collect_events(current_app._get_current_object())
    return jsonify(result)


# ------------------------------------------------------------------
# API - Câmeras e Estabelecimentos
# ------------------------------------------------------------------
@bp.route("/api/cameras")
def cameras():
    items = db.session.query(Camera).all()
    return jsonify([c.to_dict() for c in items])


@bp.route("/api/estabelecimentos")
def estabelecimentos():
    items = db.session.query(Estabelecimento).all()
    return jsonify([e.to_dict() for e in items])


# ------------------------------------------------------------------
# Proxy de imagens da API Facial (requer Basic Auth no origem)
# ------------------------------------------------------------------
@bp.route("/api/face-image")
def face_image():
    path = request.args.get("path", "")
    if not path or not path.startswith("/media/"):
        return "", 400
    url = Config.FACIAL_API_BASE + path
    try:
        r = http_requests.get(
            url,
            auth=HTTPBasicAuth(Config.FACIAL_API_USER, Config.FACIAL_API_PASS),
            timeout=10,
        )
        content_type = r.headers.get("Content-Type", "image/jpeg")
        return Response(r.content, status=r.status_code, content_type=content_type)
    except Exception:
        return "", 502

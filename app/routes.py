from datetime import datetime, time, timezone, timedelta

from flask import Blueprint, jsonify, render_template, request, current_app, Response, session, redirect, url_for
from sqlalchemy import func, desc, asc, or_, text, bindparam
from sqlalchemy.orm import subqueryload
from sqlalchemy.dialects.postgresql import insert as pg_insert
import requests as http_requests
from requests.auth import HTTPBasicAuth
from . import db
from .models import EventoFacial, Pessoa, Camera, Estabelecimento, SyncControl, PessoaDuplicada
from .collector import collect_events
from .config import Config

bp = Blueprint("main", __name__)

PUBLIC_ENDPOINTS = {"main.login_page", "main.login_submit"}

# Fuso dos "dias" mostrados ao usuário (filtros de data e relatório)
TZ_LOCAL = "America/Sao_Paulo"


def _allowed_cameras():
    """None = acesso a todas as câmeras; lista = câmeras liberadas para a sessão."""
    scope = session.get("allowed_cameras", "*")
    return None if scope == "*" else scope


@bp.before_request
def require_login():
    if request.endpoint in PUBLIC_ENDPOINTS:
        return
    if not session.get("authenticated"):
        if request.path.startswith("/api/"):
            return jsonify({"error": "unauthorized"}), 401
        return redirect(url_for("main.login_page"))


# ------------------------------------------------------------------
# Login (senha -> escopo de câmeras liberadas)
# ------------------------------------------------------------------
@bp.route("/login", methods=["GET"])
def login_page():
    return render_template("login.html", error=None)


@bp.route("/login", methods=["POST"])
def login_submit():
    codigo = (request.form.get("codigo") or "").strip()
    if codigo not in Config.ACCESS_CODES:
        return render_template("login.html", error="Senha inválida"), 401

    session["authenticated"] = True
    session["allowed_cameras"] = Config.ACCESS_CODES[codigo]
    return redirect(url_for("main.index"))


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("main.login_page"))


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
    allowed = _allowed_cameras()

    evt_q = db.session.query(EventoFacial)
    if allowed is not None:
        evt_q = evt_q.filter(EventoFacial.camera_id.in_(allowed))

    total_eventos  = evt_q.with_entities(func.count(EventoFacial.id)).scalar()
    total_novas    = evt_q.filter(EventoFacial.match_type == "new").with_entities(func.count(EventoFacial.id)).scalar()
    total_conhecidas = evt_q.filter(EventoFacial.match_type == "exact").with_entities(func.count(EventoFacial.id)).scalar()

    if allowed is None:
        total_pessoas = db.session.query(func.count(Pessoa.id)).scalar()
    else:
        total_pessoas = evt_q.with_entities(func.count(func.distinct(EventoFacial.pessoa_id))).scalar()

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


def _store_cameras_filter(store_id):
    """Filtra eventos pelas câmeras cadastradas no estabelecimento.

    Usa `cameras.estabelecimento_id` (mesma fonte do dropdown de câmeras) em vez
    de `eventos_faciais.estabelecimento_id`, que vem do `store_id` da API Facial
    e não bate com o cadastro das câmeras.
    """
    cams = db.session.query(Camera.camera_id).filter(Camera.estabelecimento_id == store_id)
    return EventoFacial.camera_id.in_(cams.scalar_subquery())


def _parse_dia(valor):
    try:
        return datetime.strptime(valor, "%Y-%m-%d").date() if valor else None
    except ValueError:
        return None


def _inicio_do_dia(dia):
    """00:00 de `dia` no fuso de São Paulo, como timestamptz (conversão feita no PostgreSQL)."""
    return func.timezone(TZ_LOCAL, datetime.combine(dia, time.min))


def _filtro_periodo(q, date_from, date_to):
    """Recorta eventos por dias inclusivos (`YYYY-MM-DD`) no fuso de São Paulo; datas inválidas são ignoradas.

    O dia do usuário é o de São Paulo, não o UTC — cortar em UTC jogava as
    coletas depois das 21h para o dia seguinte.
    """
    ini, fim = _parse_dia(date_from), _parse_dia(date_to)
    if ini:
        q = q.filter(EventoFacial.timestamp_evento >= _inicio_do_dia(ini))
    if fim:
        q = q.filter(EventoFacial.timestamp_evento < _inicio_do_dia(fim + timedelta(days=1)))
    return q


def _grupos_duplicata(pessoa_ids=None):
    """Membros dos grupos de duplicata (vw_grupos_duplicatas, migration 006).

    Retorna {grupo_id: [{id, person_unique_id, nome}, ...]} só com os grupos que
    contêm alguma das `pessoa_ids` (ou todos, se None). Membros sem evento em
    câmera liberada para a sessão ficam de fora, e grupos que sobram com menos
    de 2 membros visíveis são descartados — uma sessão restrita não fica
    sabendo de pessoas de outras câmeras.
    """
    if pessoa_ids is not None and not pessoa_ids:
        return {}
    sql = """
        SELECT v.grupo_id, p.id, p.person_unique_id, p.nome
          FROM itumbiara.vw_grupos_duplicatas v
          JOIN itumbiara.pessoas p ON p.id = v.pessoa_id
         WHERE 1 = 1
    """
    params = {}
    binds = []
    if pessoa_ids is not None:
        sql += " AND v.grupo_id IN (SELECT grupo_id FROM itumbiara.vw_grupos_duplicatas WHERE pessoa_id IN :pids)"
        params["pids"] = [int(i) for i in pessoa_ids]
        binds.append(bindparam("pids", expanding=True))
    allowed = _allowed_cameras()
    if allowed is not None:
        sql += (" AND EXISTS (SELECT 1 FROM itumbiara.eventos_faciais e"
                " WHERE e.pessoa_id = p.id AND e.camera_id IN :cams)")
        params["cams"] = list(allowed)
        binds.append(bindparam("cams", expanding=True))
    sql += " ORDER BY v.grupo_id, p.id"

    grupos = {}
    for r in db.session.execute(text(sql).bindparams(*binds), params):
        grupos.setdefault(r.grupo_id, []).append(
            {"id": r.id, "person_unique_id": r.person_unique_id, "nome": r.nome or "—"})
    return {g: m for g, m in grupos.items() if len(m) >= 2}


def _duplicata_por_pessoa(pessoa_ids):
    """{pessoa_id: {grupo_id, membros}} para anexar o selo de duplicata nas respostas."""
    out = {}
    for grupo_id, membros in _grupos_duplicata(pessoa_ids).items():
        for m in membros:
            out[m["id"]] = {"grupo_id": grupo_id, "membros": membros}
    return out


def _primeiras_faces(pessoa_ids):
    """{pessoa_id: row(face_image_url, timestamp_evento)} do primeiro evento de cada pessoa.

    Ignora capturas marcadas como intrusas — a foto de outra pessoa não pode
    virar a "primeira foto" de ninguém — e as marcadas como indesejadas (de
    costas, encobertas, pouca luz...), que não servem para reconhecer a pessoa.
    """
    if not pessoa_ids:
        return {}
    # Numera os eventos de cada pessoa por ordem cronológica para achar o primeiro (rn == 1)
    rn = func.row_number().over(
        partition_by=EventoFacial.pessoa_id,
        order_by=asc(EventoFacial.timestamp_evento),
    ).label("rn")
    sub = db.session.query(
        EventoFacial.pessoa_id.label("pessoa_id"),
        EventoFacial.face_image_url.label("face_image_url"),
        EventoFacial.timestamp_evento.label("timestamp_evento"),
        rn,
    ).filter(EventoFacial.pessoa_id.in_(pessoa_ids),
             EventoFacial.intruso == False,
             EventoFacial.indesejada == False).subquery()

    rows = db.session.query(sub.c.pessoa_id, sub.c.face_image_url, sub.c.timestamp_evento)\
        .filter(sub.c.rn == 1).all()
    return {r.pessoa_id: r for r in rows}


# ------------------------------------------------------------------
# API - Eventos recentes
# ------------------------------------------------------------------
@bp.route("/api/eventos")
def eventos():
    limit  = min(int(request.args.get("limit", 50)), 200)
    offset = int(request.args.get("offset", 0))

    q = db.session.query(EventoFacial).options(subqueryload(EventoFacial.matches)).order_by(desc(EventoFacial.timestamp_evento))

    allowed = _allowed_cameras()
    if allowed is not None:
        q = q.filter(EventoFacial.camera_id.in_(allowed))

    camera_id = request.args.get("camera_id")
    if camera_id:
        q = q.filter(EventoFacial.camera_id == camera_id)

    store_id = request.args.get("store_id")
    if store_id:
        q = q.filter(_store_cameras_filter(store_id))

    match_type = request.args.get("match_type")
    if match_type:
        q = q.filter(EventoFacial.match_type == match_type)

    pessoa_ids = request.args.getlist("pessoa_id")
    if pessoa_ids:
        q = q.filter(EventoFacial.pessoa_id.in_(pessoa_ids))

    q = _filtro_periodo(q, request.args.get("date_from"), request.args.get("date_to"))

    if request.args.get("has_matches") == "1":
        q = q.filter(EventoFacial.matched == True)

    total = q.count()
    items = q.offset(offset).limit(limit).all()

    # Batch-fetch face_image_url dos eventos referenciados nos matches
    all_refs = [m.event_id_ref for ev in items for m in ev.matches]
    match_images = {}
    if all_refs:
        rows = db.session.query(EventoFacial.event_id, EventoFacial.face_image_url)\
            .filter(EventoFacial.event_id.in_(all_refs)).all()
        match_images = {row.event_id: row.face_image_url for row in rows}

    return jsonify({
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [e.to_dict(match_images=match_images) for e in items]
    })


# ------------------------------------------------------------------
# API - Pessoas
# ------------------------------------------------------------------
@bp.route("/api/pessoas")
def pessoas():
    limit  = min(int(request.args.get("limit", 50)), 200)
    offset = int(request.args.get("offset", 0))

    q = db.session.query(Pessoa).order_by(desc(Pessoa.ultima_deteccao))

    allowed = _allowed_cameras()
    if allowed is not None:
        q = q.filter(Pessoa.eventos.any(EventoFacial.camera_id.in_(allowed)))

    search = request.args.get("q", "").strip()
    if search:
        like = f"%{search}%"
        q = q.filter(or_(Pessoa.person_unique_id.ilike(like), Pessoa.nome.ilike(like)))

    total = q.count()
    items = q.offset(offset).limit(limit).all()
    dups = _duplicata_por_pessoa([p.id for p in items])

    return jsonify({
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [{**p.to_dict(), "duplicata": dups.get(p.id)} for p in items]
    })


@bp.route("/api/pessoas/<int:pessoa_id>/nome", methods=["POST"])
def atualizar_nome(pessoa_id):
    data  = request.get_json()
    nome  = (data or {}).get("nome", "").strip()
    p     = db.session.get(Pessoa, pessoa_id)
    if not p:
        return jsonify({"error": "Pessoa não encontrada"}), 404

    allowed = _allowed_cameras()
    if allowed is not None and not p.eventos.filter(EventoFacial.camera_id.in_(allowed)).first():
        return jsonify({"error": "Pessoa não encontrada"}), 404

    p.nome = nome
    db.session.commit()
    return jsonify({"ok": True, "nome": p.nome})


@bp.route("/api/eventos/<int:evento_id>/intruso", methods=["POST"])
def marcar_intruso(evento_id):
    """Marca/desmarca uma captura cujo rosto não é da pessoa associada."""
    intruso = bool((request.get_json() or {}).get("intruso", True))
    ev = db.session.get(EventoFacial, evento_id)
    allowed = _allowed_cameras()
    if not ev or (allowed is not None and ev.camera_id not in allowed):
        return jsonify({"error": "Evento não encontrado"}), 404

    ev.intruso = intruso
    ev.intruso_em = datetime.now(timezone.utc) if intruso else None
    db.session.commit()
    return jsonify({"ok": True, "id": ev.id, "intruso": ev.intruso})


@bp.route("/api/eventos/<int:evento_id>/indesejada", methods=["POST"])
def marcar_indesejada(evento_id):
    """Marca/desmarca uma coleta indesejada (de costas, encoberta, pouca luz...)."""
    indesejada = bool((request.get_json() or {}).get("indesejada", True))
    ev = db.session.get(EventoFacial, evento_id)
    allowed = _allowed_cameras()
    if not ev or (allowed is not None and ev.camera_id not in allowed):
        return jsonify({"error": "Evento não encontrado"}), 404

    ev.indesejada = indesejada
    ev.indesejada_em = datetime.now(timezone.utc) if indesejada else None
    db.session.commit()
    return jsonify({"ok": True, "id": ev.id, "indesejada": ev.indesejada})


# ------------------------------------------------------------------
# API - Coleta manual
# ------------------------------------------------------------------
@bp.route("/api/coletar", methods=["POST"])
def coletar():
    result = collect_events(current_app._get_current_object())
    return jsonify(result)


# ------------------------------------------------------------------
# API - Reset da base (apaga eventos e pessoas)
# ------------------------------------------------------------------
@bp.route("/api/reset-base", methods=["POST"])
def reset_base():
    if _allowed_cameras() is not None:
        return jsonify({"error": "unauthorized"}), 403

    db.session.execute(text(
        "TRUNCATE TABLE itumbiara.eventos_faciais, itumbiara.pessoas RESTART IDENTITY CASCADE"
    ))
    db.session.commit()
    return jsonify({"ok": True})


# ------------------------------------------------------------------
# API - Câmeras e Estabelecimentos
# ------------------------------------------------------------------
@bp.route("/api/cameras")
def cameras():
    allowed = _allowed_cameras()
    q = db.session.query(Camera)
    if allowed is not None:
        q = q.filter(Camera.camera_id.in_(allowed))
    items = q.all()
    return jsonify([c.to_dict() for c in items])


@bp.route("/api/estabelecimentos")
def estabelecimentos():
    items = db.session.query(Estabelecimento).all()
    return jsonify([e.to_dict() for e in items])


# ------------------------------------------------------------------
# API - Eventos agrupados por pessoa (ordem pelo evento mais recente)
# ------------------------------------------------------------------
def _build_event_filters(q):
    """Aplica os filtros de evento comuns vindo da query string."""
    allowed = _allowed_cameras()
    if allowed is not None:
        q = q.filter(EventoFacial.camera_id.in_(allowed))
    camera_id = request.args.get("camera_id")
    if camera_id:
        q = q.filter(EventoFacial.camera_id == camera_id)
    store_id = request.args.get("store_id")
    if store_id:
        q = q.filter(_store_cameras_filter(store_id))
    match_type = request.args.get("match_type")
    if match_type:
        q = q.filter(EventoFacial.match_type == match_type)
    q = _filtro_periodo(q, request.args.get("date_from"), request.args.get("date_to"))
    if request.args.get("has_matches") == "1":
        q = q.filter(EventoFacial.matched == True)
    return q


@bp.route("/api/pessoas-eventos")
def pessoas_eventos():
    pessoa_ids = request.args.getlist("pessoa_id")

    # Seleciona as pessoas a exibir, ordenadas pela detecção mais recente
    pq = db.session.query(Pessoa).filter(Pessoa.ultima_deteccao.isnot(None))\
        .order_by(desc(Pessoa.ultima_deteccao))
    allowed = _allowed_cameras()
    if allowed is not None:
        pq = pq.filter(Pessoa.eventos.any(EventoFacial.camera_id.in_(allowed)))
    if pessoa_ids:
        pq = pq.filter(Pessoa.id.in_(pessoa_ids))
        pessoas = pq.all()
    else:
        pessoas = pq.limit(50).all()

    dups = _duplicata_por_pessoa([p.id for p in pessoas])
    items = []
    for p in pessoas:
        # Query de eventos específica para esta pessoa, com todos os filtros
        eq = _build_event_filters(
            db.session.query(EventoFacial)
            .options(subqueryload(EventoFacial.matches))
            .filter(EventoFacial.pessoa_id == p.id)
            .order_by(desc(EventoFacial.timestamp_evento))
        )
        eventos = eq.limit(20).all()
        if not eventos:
            continue

        all_refs = [m.event_id_ref for ev in eventos for m in ev.matches]
        match_images = {}
        if all_refs:
            rows = db.session.query(EventoFacial.event_id, EventoFacial.face_image_url)\
                .filter(EventoFacial.event_id.in_(all_refs)).all()
            match_images = {row.event_id: row.face_image_url for row in rows}

        items.append({
            "pessoa": {**p.to_dict(), "duplicata": dups.get(p.id)},
            "eventos": [ev.to_dict(match_images=match_images) for ev in eventos],
        })

    return jsonify({"total": len(items), "items": items})


# ------------------------------------------------------------------
# API - Tabuleiro (primeira facial de cada pessoa)
# ------------------------------------------------------------------
TABULEIRO_LIMIT = 120
TABULEIRO_SORT_COLUMNS = {
    "ultima_deteccao": Pessoa.ultima_deteccao,
    "primeira_deteccao": Pessoa.primeira_deteccao,
}


@bp.route("/api/tabuleiro")
def tabuleiro():
    sort = request.args.get("sort", "ultima_deteccao")
    sort_col = TABULEIRO_SORT_COLUMNS.get(sort, Pessoa.ultima_deteccao)
    offset = max(int(request.args.get("offset", 0)), 0)

    pq = db.session.query(Pessoa).filter(Pessoa.ultima_deteccao.isnot(None))

    allowed = _allowed_cameras()
    if allowed is not None:
        pq = pq.filter(Pessoa.eventos.any(EventoFacial.camera_id.in_(allowed)))

    camera_id = request.args.get("camera_id")
    if camera_id:
        pq = pq.filter(Pessoa.eventos.any(EventoFacial.camera_id == camera_id))

    store_id = request.args.get("store_id")
    if store_id:
        pq = pq.filter(Pessoa.eventos.any(_store_cameras_filter(store_id)))

    pq = pq.order_by(desc(sort_col))
    total = pq.count()
    pessoas = pq.offset(offset).limit(TABULEIRO_LIMIT).all()

    pessoa_ids = [p.id for p in pessoas]
    primeiras = _primeiras_faces(pessoa_ids)
    dups = _duplicata_por_pessoa(pessoa_ids)

    items = []
    for p in pessoas:
        pf = primeiras.get(p.id)
        items.append({
            "pessoa": {**p.to_dict(), "duplicata": dups.get(p.id)},
            "primeira_face_image_url": pf.face_image_url if pf else None,
            "primeira_timestamp": pf.timestamp_evento.isoformat() if pf and pf.timestamp_evento else None,
        })

    return jsonify({
        "total": total,
        "limit": TABULEIRO_LIMIT,
        "offset": offset,
        "sort": sort,
        "items": items,
    })


# ------------------------------------------------------------------
# API - Relatório de estatísticas (coletas, pessoas, não conformidades)
# ------------------------------------------------------------------
# Uma linha por evento no recorte, já com as flags que o relatório soma.
# - criou: é o primeiro evento da pessoa no banco inteiro (= a coleta que criou a pessoa)
# - dup_excedente: criou uma pessoa que está num grupo de duplicata e não é o
#   membro mais antigo do grupo (grupo_id = menor pessoas.id) — é um cadastro
#   a mais para um rosto que já existia
# GROUPING SETS devolve na mesma consulta o total, a quebra por dia e por câmera.
_RELATORIO_SQL = """
WITH primeiro AS (
    SELECT DISTINCT ON (pessoa_id) pessoa_id, id AS evento_id
      FROM itumbiara.eventos_faciais
     WHERE pessoa_id IS NOT NULL
     ORDER BY pessoa_id, timestamp_evento, id
),
ev AS (
    SELECT e.pessoa_id,
           e.camera_id,
           e.intruso,
           e.indesejada,
           (e.timestamp_evento AT TIME ZONE '{tz}')::date AS dia,
           pr.evento_id IS NOT NULL AS criou,
           pr.evento_id IS NOT NULL AND v.grupo_id IS NOT NULL AS criou_em_dup,
           pr.evento_id IS NOT NULL AND v.grupo_id IS NOT NULL AND v.grupo_id <> e.pessoa_id AS dup_excedente,
           CASE WHEN pr.evento_id IS NOT NULL THEN v.grupo_id END AS grupo_criado
      FROM itumbiara.eventos_faciais e
      LEFT JOIN primeiro pr ON pr.evento_id = e.id
      LEFT JOIN itumbiara.vw_grupos_duplicatas v ON v.pessoa_id = e.pessoa_id
     WHERE {where}
)
SELECT GROUPING(dia) AS g_dia,
       GROUPING(camera_id) AS g_cam,
       dia,
       camera_id,
       COUNT(*)                                        AS coletas,
       COUNT(DISTINCT pessoa_id)                       AS pessoas_vistas,
       COUNT(*) FILTER (WHERE criou)                   AS pessoas_criadas,
       COUNT(*) FILTER (WHERE intruso)                 AS intrusas,
       COUNT(*) FILTER (WHERE indesejada)              AS indesejadas,
       COUNT(*) FILTER (WHERE intruso OR indesejada)   AS coletas_nc,
       COUNT(*) FILTER (WHERE criou_em_dup)            AS pessoas_em_duplicata,
       COUNT(*) FILTER (WHERE dup_excedente)           AS duplicatas_excedentes,
       COUNT(DISTINCT grupo_criado)                    AS grupos_duplicata,
       MIN(dia)                                        AS primeiro_dia,
       MAX(dia)                                        AS ultimo_dia
  FROM ev
 GROUP BY GROUPING SETS ((), (dia), (camera_id))
"""

_RELATORIO_CAMPOS = ("coletas", "pessoas_vistas", "pessoas_criadas", "intrusas", "indesejadas",
                     "coletas_nc", "pessoas_em_duplicata", "duplicatas_excedentes", "grupos_duplicata")


@bp.route("/api/relatorio")
def relatorio():
    """Estatísticas do recorte: banco inteiro ou `date_from`..`date_to` (dias no fuso de São Paulo, inclusivos).

    Também aceita `camera_id`/`store_id` e respeita as câmeras liberadas para a sessão.
    """
    date_from = _parse_dia(request.args.get("date_from"))
    date_to = _parse_dia(request.args.get("date_to"))

    where = ["1 = 1"]
    params = {}
    binds = []
    if date_from:
        where.append(f"e.timestamp_evento >= CAST(:ini AS date)::timestamp AT TIME ZONE '{TZ_LOCAL}'")
        params["ini"] = date_from.isoformat()
    if date_to:
        where.append(f"e.timestamp_evento < (CAST(:fim AS date) + 1)::timestamp AT TIME ZONE '{TZ_LOCAL}'")
        params["fim"] = date_to.isoformat()
    allowed = _allowed_cameras()
    if allowed is not None:
        where.append("e.camera_id IN :cams")
        params["cams"] = list(allowed)
        binds.append(bindparam("cams", expanding=True))
    camera_id = request.args.get("camera_id")
    if camera_id:
        where.append("e.camera_id = :camera_id")
        params["camera_id"] = camera_id
    store_id = request.args.get("store_id")
    if store_id:
        where.append("e.camera_id IN (SELECT camera_id FROM itumbiara.cameras WHERE estabelecimento_id = :store_id)")
        params["store_id"] = int(store_id)

    sql = text(_RELATORIO_SQL.format(tz=TZ_LOCAL, where=" AND ".join(where))).bindparams(*binds)

    total = dict.fromkeys(_RELATORIO_CAMPOS, 0)
    total.update(primeiro_dia=None, ultimo_dia=None)
    por_dia, por_camera = [], []
    for r in db.session.execute(sql, params):
        linha = {c: getattr(r, c) for c in _RELATORIO_CAMPOS}
        if r.g_dia and r.g_cam:
            total.update(linha,
                         primeiro_dia=r.primeiro_dia.isoformat() if r.primeiro_dia else None,
                         ultimo_dia=r.ultimo_dia.isoformat() if r.ultimo_dia else None)
        elif not r.g_dia:
            por_dia.append({"dia": r.dia.isoformat(), **linha})
        else:
            por_camera.append({"camera_id": r.camera_id, **linha})

    por_dia.sort(key=lambda x: x["dia"])
    por_camera.sort(key=lambda x: x["coletas"], reverse=True)
    return jsonify({
        "date_from": date_from.isoformat() if date_from else None,
        "date_to": date_to.isoformat() if date_to else None,
        "total": total,
        "por_dia": por_dia,
        "por_camera": por_camera,
    })


# ------------------------------------------------------------------
# API - Capturas marcadas pelo usuário como intrusas/indesejadas (migrations 007/008)
# ------------------------------------------------------------------
MARCADAS_LIMIT = 120


@bp.route("/api/marcadas")
def marcadas():
    """Capturas marcadas como intrusas e/ou indesejadas, marcação mais recente primeiro.

    `tipo`: `intruso`, `indesejada` ou vazio (as duas). Cada item traz a
    primeira foto da pessoa (que já ignora capturas marcadas) para comparação.
    """
    tipo = request.args.get("tipo", "")
    offset = max(int(request.args.get("offset", 0)), 0)

    q = db.session.query(EventoFacial).options(subqueryload(EventoFacial.matches))
    if tipo == "intruso":
        q = q.filter(EventoFacial.intruso == True)
    elif tipo == "indesejada":
        q = q.filter(EventoFacial.indesejada == True)
    else:
        q = q.filter(or_(EventoFacial.intruso == True, EventoFacial.indesejada == True))

    allowed = _allowed_cameras()
    if allowed is not None:
        q = q.filter(EventoFacial.camera_id.in_(allowed))

    # GREATEST ignora nulls: ordena pela marcação mais recente entre as duas
    q = q.order_by(desc(func.greatest(EventoFacial.intruso_em, EventoFacial.indesejada_em)),
                   desc(EventoFacial.id))
    total = q.count()
    eventos = q.offset(offset).limit(MARCADAS_LIMIT).all()

    primeiras = _primeiras_faces(list({ev.pessoa_id for ev in eventos if ev.pessoa_id}))

    items = []
    for ev in eventos:
        pf = primeiras.get(ev.pessoa_id)
        items.append({
            **ev.to_dict(),
            "pessoa_id": ev.pessoa_id,
            "intruso_em": ev.intruso_em.isoformat() if ev.intruso_em else None,
            "indesejada_em": ev.indesejada_em.isoformat() if ev.indesejada_em else None,
            "primeira_face_image_url": pf.face_image_url if pf else None,
            "primeira_timestamp": pf.timestamp_evento.isoformat() if pf and pf.timestamp_evento else None,
        })

    return jsonify({
        "total": total,
        "limit": MARCADAS_LIMIT,
        "offset": offset,
        "items": items,
    })


# ------------------------------------------------------------------
# API - Pessoas marcadas pelo usuário como o mesmo rosto (migration 006)
# ------------------------------------------------------------------
def _pessoas_visiveis(pessoa_ids):
    """Subconjunto de `pessoa_ids` com ao menos um evento em câmera liberada para a sessão."""
    allowed = _allowed_cameras()
    if allowed is None:
        rows = db.session.query(Pessoa.id).filter(Pessoa.id.in_(pessoa_ids))
    else:
        rows = db.session.query(EventoFacial.pessoa_id).filter(
            EventoFacial.pessoa_id.in_(pessoa_ids),
            EventoFacial.camera_id.in_(allowed),
        ).distinct()
    return {r[0] for r in rows}


@bp.route("/api/duplicatas")
def duplicatas():
    grupos = _grupos_duplicata()
    membro_ids = [m["id"] for membros in grupos.values() for m in membros]
    if not membro_ids:
        return jsonify({"total": 0, "items": []})

    pessoas = {p.id: p for p in db.session.query(Pessoa).filter(Pessoa.id.in_(membro_ids))}
    primeiras = _primeiras_faces(membro_ids)
    pares = db.session.query(PessoaDuplicada).filter(
        PessoaDuplicada.pessoa_a_id.in_(membro_ids),
        PessoaDuplicada.pessoa_b_id.in_(membro_ids),
    ).order_by(PessoaDuplicada.id).all()

    items = []
    for grupo_id, membros in grupos.items():
        ids = {m["id"] for m in membros}
        items.append({
            "grupo_id": grupo_id,
            "pessoas": [{
                **pessoas[i].to_dict(),
                "primeira_face_image_url": primeiras[i].face_image_url if i in primeiras else None,
            } for i in sorted(ids)],
            "pares": [par.to_dict() for par in pares if par.pessoa_a_id in ids and par.pessoa_b_id in ids],
        })
    items.sort(key=lambda g: len(g["pessoas"]), reverse=True)

    return jsonify({"total": len(items), "items": items})


@bp.route("/api/duplicatas", methods=["POST"])
def marcar_duplicatas():
    """Marca as pessoas selecionadas como o mesmo rosto.

    Liga a de menor id a cada uma das outras (N-1 pares) — basta para a view
    juntar todas no mesmo grupo, inclusive com grupos que já existiam.
    """
    try:
        ids = sorted({int(i) for i in (request.get_json() or {}).get("pessoa_ids", [])})
    except (TypeError, ValueError):
        return jsonify({"error": "pessoa_ids inválido"}), 400
    if len(ids) < 2:
        return jsonify({"error": "Selecione ao menos 2 pessoas"}), 400
    if _pessoas_visiveis(ids) != set(ids):
        return jsonify({"error": "Pessoa não encontrada"}), 404

    t = PessoaDuplicada.__table__
    stmt = pg_insert(t).values([{"pessoa_a_id": ids[0], "pessoa_b_id": outra} for outra in ids[1:]])        .on_conflict_do_nothing(index_elements=[t.c.pessoa_a_id, t.c.pessoa_b_id])
    criados = db.session.execute(stmt).rowcount
    db.session.commit()
    return jsonify({"ok": True, "criados": criados})


@bp.route("/api/duplicatas/<int:par_id>", methods=["DELETE"])
def remover_duplicata(par_id):
    par = db.session.get(PessoaDuplicada, par_id)
    if not par:
        return jsonify({"error": "Par não encontrado"}), 404
    # Sessão restrita só remove par cujas duas pessoas aparecem nas suas câmeras
    if len(_pessoas_visiveis([par.pessoa_a_id, par.pessoa_b_id])) < 2:
        return jsonify({"error": "Par não encontrado"}), 404

    db.session.delete(par)
    db.session.commit()
    return jsonify({"ok": True})


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

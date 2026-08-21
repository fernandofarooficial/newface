import os
import tempfile

from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from apscheduler.schedulers.background import BackgroundScheduler
from werkzeug.middleware.proxy_fix import ProxyFix
from .config import Config

if os.name == "posix":
    import fcntl

db = SQLAlchemy()
scheduler = BackgroundScheduler(daemon=True)

# Mantido vivo pelo tempo de vida do processo: fechar o descritor libera o
# flock. Não precisa de cleanup explícito — o SO libera o lock sozinho
# quando o processo termina (mesmo com kill -9).
_scheduler_lock_file = None


def _acquire_scheduler_lock() -> bool:
    """Garante que só um processo rode o scheduler de coleta automática.

    Em produção o gunicorn sobe múltiplos workers (`workers = 2` em
    gunicorn.conf.py) e cada worker importa `run.py`/chama create_app()
    de forma independente — sem este lock, cada worker cria seu próprio
    BackgroundScheduler e collect_events roda em duplicidade a cada
    intervalo (ficou evidente depois que sync_control virou histórico
    append-only: pares de linhas com o mesmo timestamp).

    No Windows local (`python run.py`, sempre processo único) o lock é
    desnecessário e `fcntl` não existe, então sempre retorna True.
    """
    global _scheduler_lock_file
    if os.name != "posix":
        return True

    lock_path = os.path.join(tempfile.gettempdir(), "newface_scheduler.lock")
    lock_file = open(lock_path, "w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_file.close()
        return False

    _scheduler_lock_file = lock_file
    return True


def create_app():
    app = Flask(__name__)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
    app.config.from_object(Config)

    db.init_app(app)

    from .routes import bp
    app.register_blueprint(bp)

    # Inicia o scheduler de coleta automática só no processo/worker que
    # obtiver o lock (ver _acquire_scheduler_lock).
    if _acquire_scheduler_lock():
        from .collector import collect_events
        interval = Config.FACIAL_POLL_SECS

        scheduler.add_job(
            func=collect_events,
            args=[app],
            trigger="interval",
            seconds=interval,
            id="facial_collector",
            replace_existing=True,
        )
        if not scheduler.running:
            scheduler.start()

    return app

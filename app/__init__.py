from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from apscheduler.schedulers.background import BackgroundScheduler
from .config import Config

db = SQLAlchemy()
scheduler = BackgroundScheduler(daemon=True)


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)

    from .routes import bp
    app.register_blueprint(bp)

    # Inicia o scheduler de coleta automática
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

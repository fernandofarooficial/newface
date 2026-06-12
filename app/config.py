import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Flask
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-in-production")
    DEBUG = os.getenv("DEBUG", "false").lower() == "true"

    # PostgreSQL
    DB_HOST     = os.getenv("DB_HOST", "72.60.58.241")
    DB_PORT     = int(os.getenv("DB_PORT", "5432"))
    DB_NAME     = os.getenv("DB_NAME", "Lojas")
    DB_SCHEMA   = os.getenv("DB_SCHEMA", "itumbiara")
    DB_USER     = os.getenv("DB_USER", "fefa_dev")
    DB_PASSWORD = os.getenv("DB_PASSWORD", "Fd7493dt")

    SQLALCHEMY_DATABASE_URI = (
        f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}"
        f"@{DB_HOST}:{DB_PORT}/{DB_NAME}"
        f"?options=-csearch_path%3D{DB_SCHEMA}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 300,
    }

    # API Facial
    FACIAL_API_BASE    = os.getenv("FACIAL_API_BASE", "http://201.71.234.84:8000")
    FACIAL_API_USER    = os.getenv("FACIAL_API_USER", "admin")
    FACIAL_API_PASS    = os.getenv("FACIAL_API_PASS", "admin@facial26")
    FACIAL_POLL_SECS   = int(os.getenv("FACIAL_POLL_SECS", "30"))   # intervalo de polling
    FACIAL_LIMIT       = int(os.getenv("FACIAL_LIMIT", "50"))        # eventos por requisição
    FACIAL_MATCHES_LIM = int(os.getenv("FACIAL_MATCHES_LIM", "10"))

    # App
    PORT = int(os.getenv("PORT", "5006"))

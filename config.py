import os
from datetime import timedelta


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


db_url = os.getenv("DATABASE_URL", "")

if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

engine_options = {
    "pool_pre_ping": True,
    "pool_recycle": 300,
}

if db_url.startswith("postgresql://"):
    engine_options["connect_args"] = {
        "sslmode": "require"
    }


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")
    SQLALCHEMY_DATABASE_URI = db_url
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = engine_options

    SMTP_HOST = os.getenv("SMTP_HOST", "")
    SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
    MAIL_FROM = os.getenv("MAIL_FROM", "")
    MAIL_TO = os.getenv("MAIL_TO", "")

    MAX_CONTENT_LENGTH = int(os.getenv("MAX_CONTENT_LENGTH", str(10 * 1024 * 1024)))

    SESSION_COOKIE_NAME = os.getenv("SESSION_COOKIE_NAME", "muraz_session")
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = os.getenv("SESSION_COOKIE_SAMESITE", "Lax")
    SESSION_COOKIE_SECURE = _env_bool("SESSION_COOKIE_SECURE", True)
    SESSION_REFRESH_EACH_REQUEST = True
    PERMANENT_SESSION_LIFETIME = timedelta(hours=int(os.getenv("SESSION_LIFETIME_HOURS", "12")))

    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE
    REMEMBER_COOKIE_SAMESITE = SESSION_COOKIE_SAMESITE

    PREFERRED_URL_SCHEME = "https"
    WTF_CSRF_TIME_LIMIT = None

    SECURITY_HEADERS_ENABLED = _env_bool("SECURITY_HEADERS_ENABLED", True)
    DEFAULT_SRC_CSP = "'self'"

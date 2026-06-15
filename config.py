import os

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
    DAILY_USED_TALONS_MAIL_TO = os.getenv("DAILY_USED_TALONS_MAIL_TO", "muraztalon@gmail.com")

    MAX_CONTENT_LENGTH = int(os.getenv("MAX_CONTENT_LENGTH", str(10 * 1024 * 1024)))
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = os.getenv("SESSION_COOKIE_SAMESITE", "Lax")
    SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "1") == "1"
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE
    REMEMBER_COOKIE_SAMESITE = SESSION_COOKIE_SAMESITE

    PERMANENT_SESSION_LIFETIME = int(os.getenv("PERMANENT_SESSION_LIFETIME", str(60 * 60 * 8)))
    APP_LOG_FILE = os.getenv("APP_LOG_FILE", "logs/app.log")
    BACKUP_INCLUDE_FILES = os.getenv("BACKUP_INCLUDE_FILES", "1") == "1"
    BACKUP_UPLOAD_TO_SUPABASE = os.getenv("BACKUP_UPLOAD_TO_SUPABASE", "1") == "1"
    BACKUP_SUPABASE_BUCKET = os.getenv("BACKUP_SUPABASE_BUCKET", "backups")
    BACKUP_SUPABASE_PATH = os.getenv("BACKUP_SUPABASE_PATH", "auto").strip('/ ')
    BACKUP_KEEP_LAST = int(os.getenv("BACKUP_KEEP_LAST", "30"))

"""App settings. FLASK_ENV=production picks ProdConfig; anything else is development."""
import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
INSTANCE_DIR = BASE_DIR / "instance"
INSTANCE_DIR.mkdir(exist_ok=True)


def _dev_secret(name):
    """A per-machine random secret kept in instance/ (git-ignored) so it survives restarts. Dev only."""
    path = INSTANCE_DIR / name
    if not path.exists():
        path.write_text(secrets.token_urlsafe(48))
    return path.read_text().strip()


def _dev_fernet_key():
    path = INSTANCE_DIR / "dev_encryption.key"
    if not path.exists():
        from cryptography.fernet import Fernet
        path.write_text(Fernet.generate_key().decode())
    return path.read_text().strip()


class BaseConfig:
    APP_NAME = "AI Project Dashboard"
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 12      # signed-in session lasts 12 hours
    WTF_CSRF_TIME_LIMIT = None                     # CSRF token lives as long as the session
    LOGIN_MAX_TRIES = 5
    LOGIN_LOCK_MINUTES = 15
    SETUP_LINK_HOURS = 48
    PUBLIC_URL = os.environ.get("PUBLIC_URL", "")   # e.g. https://dash.company.com — for links in alerts sent by timers
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL") or f"sqlite:///{INSTANCE_DIR / 'app.db'}"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SESSION_COOKIE_HTTPONLY = True
    # Server only: Claude's checkouts live outside instance/ (which keeps the DB and keys, mode 700), and the project's
    # test command runs as a separate low-rights Linux user that can't read the dashboard's data or other websites.
    WORKSPACE_ROOT = os.environ.get("WORKSPACE_ROOT", "")
    TEST_RUNNER_USER = os.environ.get("TEST_RUNNER_USER", "")
    CLAUDE_CODE_BIN = os.environ.get("CLAUDE_CODE_BIN", "claude")   # Claude Code CLI (engine "claude_code")
    # where Claude Code reaches the dashboard's beta tools (MCP) — Gunicorn's own local address, never through the web server
    INTERNAL_URL = os.environ.get("INTERNAL_URL") or "http://" + os.environ.get("GUNICORN_BIND", "127.0.0.1:5070")
    SESSION_COOKIE_SAMESITE = "Lax"


class DevConfig(BaseConfig):
    DEBUG = True
    SECRET_KEY = os.environ.get("SECRET_KEY") or _dev_secret("dev_secret.key")
    # Fernet key for secrets stored in the DB (authenticator secrets, later SSH / API keys)
    ENCRYPTION_KEY = os.environ.get("ENCRYPTION_KEY") or _dev_fernet_key()


class ProdConfig(BaseConfig):
    DEBUG = False
    SECRET_KEY = os.environ.get("SECRET_KEY")
    ENCRYPTION_KEY = os.environ.get("ENCRYPTION_KEY")
    SESSION_COOKIE_SECURE = True


def get_config():
    return ProdConfig if os.environ.get("FLASK_ENV") == "production" else DevConfig


def validate_production(config):
    """Refuse to start in production without real secrets (a forgeable session cookie is worse than a crash)."""
    if config is ProdConfig:
        missing = [k for k in ("SECRET_KEY", "ENCRYPTION_KEY") if not getattr(config, k)]
        if missing:
            raise RuntimeError(f"Set {', '.join(missing)} in production. Never change ENCRYPTION_KEY later — "
                               "everything encrypted with it becomes unreadable.")

"""Configuration for NXBlogger.

Values come from environment variables. In production they are kept in
/etc/nxblogger/nxblogger.env (written by install.sh). For local development
nothing needs to be set: data goes to ./instance and a secret key is
generated automatically.
"""
import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_ENV_FILE = "/etc/nxblogger/nxblogger.env"


def load_env_file(path=None):
    """Load KEY=VALUE lines from the env file without overriding real env vars."""
    path = Path(path or os.environ.get("NXB_ENV_FILE", DEFAULT_ENV_FILE))
    if not path.is_file():
        return
    try:
        text = path.read_text(encoding="utf-8")
    except (PermissionError, OSError):
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


def _bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _secret_key(data_dir: Path) -> str:
    key = os.environ.get("NXB_SECRET_KEY")
    if key:
        return key
    # Development fallback: persist a generated key inside the data dir.
    key_file = data_dir / "secret_key"
    if key_file.is_file():
        return key_file.read_text().strip()
    data_dir.mkdir(parents=True, exist_ok=True)
    key = secrets.token_hex(32)
    key_file.write_text(key)
    return key


def build_config(overrides=None):
    load_env_file()
    overrides = overrides or {}
    data_dir = Path(overrides.get("DATA_DIR") or os.environ.get("NXB_DATA_DIR") or (BASE_DIR / "instance"))
    max_upload_mb = int(os.environ.get("NXB_MAX_UPLOAD_MB", "200"))
    cfg = {
        "DATA_DIR": data_dir,
        "DATABASE": Path(os.environ.get("NXB_DATABASE") or (data_dir / "data" / "nxblogger.db")),
        "UPLOAD_DIR": Path(os.environ.get("NXB_UPLOAD_DIR") or (data_dir / "uploads")),
        "BACKUP_DIR": Path(os.environ.get("NXB_BACKUP_DIR") or (data_dir / "backups")),
        "ADMIN_USER": os.environ.get("NXB_ADMIN_USER", "admin"),
        "DEFAULT_TITLE": os.environ.get("NXB_BLOG_TITLE", "My Blog"),
        "TIMEZONE": os.environ.get("NXB_TIMEZONE", "Europe/Helsinki"),
        "MAX_CONTENT_LENGTH": max_upload_mb * 1024 * 1024,
        "MAX_IMAGE_MB": int(os.environ.get("NXB_MAX_IMAGE_MB", "25")),
        "SESSION_COOKIE_SECURE": _bool("NXB_COOKIE_SECURE", False),
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": "Lax",
        "SESSION_COOKIE_NAME": "nxb_session",
        "PERMANENT_SESSION_LIFETIME": int(os.environ.get("NXB_SESSION_HOURS", "12")) * 3600,
        "BEHIND_PROXY": _bool("NXB_BEHIND_PROXY", False),
        "APP_PORT": int(os.environ.get("NXB_APP_PORT", "8801")),
        "HTTPS_PORT": int(os.environ.get("NXB_HTTPS_PORT", "8800")),
    }
    cfg.update(overrides)
    cfg["DATA_DIR"] = Path(cfg["DATA_DIR"])
    if "DATABASE" not in overrides and "DATA_DIR" in overrides and not os.environ.get("NXB_DATABASE"):
        cfg["DATABASE"] = cfg["DATA_DIR"] / "data" / "nxblogger.db"
    if "UPLOAD_DIR" not in overrides and "DATA_DIR" in overrides and not os.environ.get("NXB_UPLOAD_DIR"):
        cfg["UPLOAD_DIR"] = cfg["DATA_DIR"] / "uploads"
    if "BACKUP_DIR" not in overrides and "DATA_DIR" in overrides and not os.environ.get("NXB_BACKUP_DIR"):
        cfg["BACKUP_DIR"] = cfg["DATA_DIR"] / "backups"
    cfg["SECRET_KEY"] = overrides.get("SECRET_KEY") or _secret_key(cfg["DATA_DIR"])
    return cfg

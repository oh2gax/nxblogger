"""Authentication: single admin user, hashed password, CSRF and login throttling."""
import hmac
import secrets
import time
from functools import wraps

from flask import abort, current_app, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .content import now_str
from .db import connect, get_db

MAX_ATTEMPTS = 5          # failed logins allowed ...
ATTEMPT_WINDOW = 15 * 60  # ... within this many seconds per IP
MIN_PASSWORD_LEN = 8


def hash_password(password: str) -> str:
    # Werkzeug uses scrypt with a random salt; only the hash is stored.
    return generate_password_hash(password, method="scrypt")


def set_admin_password(db_path, username: str, password: str):
    """Create or update the admin user (used by CLI, install and settings page)."""
    if len(password) < MIN_PASSWORD_LEN:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LEN} characters.")
    conn = connect(db_path)
    try:
        now = now_str_safe()
        pw_hash = hash_password(password)
        row = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
        if row:
            conn.execute("UPDATE users SET password_hash=?, updated_at=? WHERE id=?", (pw_hash, now, row["id"]))
        else:
            conn.execute(
                "INSERT INTO users(username, password_hash, created_at, updated_at) VALUES(?,?,?,?)",
                (username, pw_hash, now, now),
            )
        conn.commit()
    finally:
        conn.close()


def now_str_safe():
    try:
        return now_str()
    except RuntimeError:  # outside app context
        from datetime import datetime
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def admin_exists(db_path, username: str) -> bool:
    conn = connect(db_path)
    try:
        return conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone() is not None
    finally:
        conn.close()


def verify_login(username: str, password: str) -> bool:
    row = get_db().execute("SELECT password_hash FROM users WHERE username=?", (username,)).fetchone()
    if row is None:
        # Spend similar time to avoid user enumeration by timing.
        check_password_hash(hash_password("dummy-password"), password)
        return False
    return check_password_hash(row["password_hash"], password)


def client_ip() -> str:
    return request.remote_addr or "unknown"


def too_many_attempts(ip: str) -> bool:
    db = get_db()
    cutoff = time.time() - ATTEMPT_WINDOW
    db.execute("DELETE FROM login_attempts WHERE ts < ?", (cutoff,))
    db.commit()
    count = db.execute("SELECT COUNT(*) AS c FROM login_attempts WHERE ip=?", (ip,)).fetchone()["c"]
    return count >= MAX_ATTEMPTS


def record_failed_attempt(ip: str):
    db = get_db()
    db.execute("INSERT INTO login_attempts(ip, ts) VALUES(?, ?)", (ip, time.time()))
    db.commit()


def clear_attempts(ip: str):
    db = get_db()
    db.execute("DELETE FROM login_attempts WHERE ip=?", (ip,))
    db.commit()


def is_logged_in() -> bool:
    return session.get("user") == current_app.config["ADMIN_USER"]


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not is_logged_in():
            if request.path.startswith("/admin/api/") or request.headers.get("X-Requested-With") == "fetch":
                abort(401)
            return redirect(url_for("admin.login", next=request.full_path))
        return view(*args, **kwargs)
    return wrapped


# --- CSRF -------------------------------------------------------------------

def csrf_token() -> str:
    token = session.get("_csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf"] = token
    return token


def check_csrf():
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return
    sent = request.headers.get("X-CSRF-Token") or request.form.get("_csrf", "")
    expected = session.get("_csrf", "")
    if not expected or not hmac.compare_digest(str(sent), str(expected)):
        abort(400, description="Invalid or missing security token. Reload the page and try again.")

"""NXBlogger - a small, calm blog publishing system."""
from datetime import timedelta

from flask import Flask, render_template, request

from . import db as dbmod
from .auth import check_csrf, csrf_token, is_logged_in
from .config import build_config
from .content import parse_dt

__version__ = "1.0.0"


def create_app(overrides=None):
    app = Flask(__name__, static_folder="static", template_folder="templates")
    cfg = build_config(overrides)
    app.config.update(cfg)
    app.permanent_session_lifetime = timedelta(seconds=cfg["PERMANENT_SESSION_LIFETIME"])

    if app.config.get("BEHIND_PROXY"):
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)

    for key in ("DATA_DIR", "UPLOAD_DIR"):
        app.config[key].mkdir(parents=True, exist_ok=True)
    dbmod.init_db(app.config["DATABASE"])
    app.teardown_appcontext(dbmod.close_db)

    from .admin import bp as admin_bp
    from .public import bp as public_bp
    app.register_blueprint(public_bp)
    app.register_blueprint(admin_bp)

    @app.before_request
    def _csrf():
        if request.blueprint == "admin":
            check_csrf()

    @app.context_processor
    def _globals():
        return {
            "csrf_token": csrf_token,
            "logged_in": is_logged_in(),
            "blog_title": dbmod.get_setting("blog_title", app.config["DEFAULT_TITLE"]),
            "blog_subtitle": dbmod.get_setting("blog_subtitle", "Notes, thoughts and projects"),
            "footer_text": dbmod.get_setting("footer_text", ""),
            "app_version": __version__,
        }

    @app.template_filter("date")
    def _date(value, fmt="%d-%m-%Y"):
        dt = parse_dt(value) if isinstance(value, str) else value
        return dt.strftime(fmt) if dt else ""

    @app.after_request
    def _headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        if request.path.startswith("/admin"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.errorhandler(404)
    def _not_found(_e):
        return render_template("error.html", code=404, message="Page not found."), 404

    @app.errorhandler(400)
    def _bad_request(e):
        return render_template("error.html", code=400, message=e.description or "Bad request."), 400

    @app.errorhandler(413)
    def _too_large(_e):
        mb = app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
        return render_template("error.html", code=413, message=f"Upload is too large (max {mb} MB)."), 413

    return app

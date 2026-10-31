"""Public blog pages."""
import math
from email.utils import format_datetime

from flask import (Blueprint, Response, abort, current_app, redirect, render_template,
                   request, send_from_directory, url_for)

from .auth import is_logged_in
from .content import get_post_tags, parse_dt, slugify, tz
from .db import get_db, get_setting

bp = Blueprint("public", __name__)


def _per_page():
    try:
        return max(1, min(50, int(get_setting("posts_per_page", "10"))))
    except ValueError:
        return 10


def _page_arg():
    try:
        return max(1, int(request.args.get("page", 1)))
    except ValueError:
        return 1


def _list_posts(where="", params=(), join=""):
    db = get_db()
    per_page, page = _per_page(), _page_arg()
    base = f"FROM posts p {join} WHERE p.status='published' {where}"
    total = db.execute(f"SELECT COUNT(DISTINCT p.id) AS c {base}", params).fetchone()["c"]
    rows = db.execute(
        f"SELECT DISTINCT p.* {base} ORDER BY p.published_at DESC LIMIT ? OFFSET ?",
        (*params, per_page, (page - 1) * per_page),
    ).fetchall()
    pages = max(1, math.ceil(total / per_page))
    return rows, page, pages, total


@bp.route("/")
def index():
    q = request.args.get("q", "").strip()
    if q:
        like = f"%{q}%"
        posts, page, pages, total = _list_posts("AND (p.title LIKE ? OR p.content LIKE ?)", (like, like))
    else:
        posts, page, pages, total = _list_posts()
    return render_template("index.html", posts=posts, page=page, pages=pages, total=total, q=q)


@bp.route("/tag/<path:tag>")
def tag(tag):
    posts, page, pages, total = _list_posts(
        "AND t.tag = ? COLLATE NOCASE", (tag,), join="JOIN post_tags t ON t.post_id = p.id"
    )
    return render_template("index.html", posts=posts, page=page, pages=pages, total=total, tag=tag)


@bp.route("/post/<slug>")
def post(slug):
    db = get_db()
    row = db.execute("SELECT * FROM posts WHERE slug=?", (slug,)).fetchone()
    if row is None or (row["status"] != "published" and not is_logged_in()):
        abort(404)
    tags = get_post_tags(db, row["id"])
    newer = db.execute(
        "SELECT title, slug FROM posts WHERE status='published' AND published_at > ? ORDER BY published_at ASC LIMIT 1",
        (row["published_at"],),
    ).fetchone()
    older = db.execute(
        "SELECT title, slug FROM posts WHERE status='published' AND published_at < ? ORDER BY published_at DESC LIMIT 1",
        (row["published_at"],),
    ).fetchone()
    return render_template("post.html", post=row, tags=tags, newer=newer, older=older)


@bp.route("/archive")
def archive():
    rows = get_db().execute(
        "SELECT title, slug, published_at FROM posts WHERE status='published' ORDER BY published_at DESC"
    ).fetchall()
    years = {}
    for r in rows:
        years.setdefault(r["published_at"][:4], []).append(r)
    tags = get_db().execute(
        "SELECT t.tag, COUNT(*) AS c FROM post_tags t JOIN posts p ON p.id=t.post_id "
        "WHERE p.status='published' GROUP BY t.tag COLLATE NOCASE ORDER BY t.tag COLLATE NOCASE"
    ).fetchall()
    return render_template("archive.html", years=years, tags=tags)


@bp.route("/feed.xml")
def feed():
    rows = get_db().execute(
        "SELECT * FROM posts WHERE status='published' ORDER BY published_at DESC LIMIT 20"
    ).fetchall()
    items = []
    for r in rows:
        dt = parse_dt(r["published_at"]).replace(tzinfo=tz())
        items.append({"post": r, "pubdate": format_datetime(dt)})
    xml = render_template("feed.xml", items=items)
    return Response(xml, mimetype="application/rss+xml")


@bp.route("/healthz")
def healthz():
    get_db().execute("SELECT 1").fetchone()
    return {"status": "ok"}


@bp.route("/uploads/<path:filename>")
def uploads(filename):
    # In production nginx serves /uploads directly; this is the fallback.
    return send_from_directory(current_app.config["UPLOAD_DIR"], filename, max_age=30 * 86400)


# --- Old WordPress permalinks -> new URLs ----------------------------------

def _redirect_slug(slug):
    row = get_db().execute(
        "SELECT slug FROM posts WHERE slug IN (?, ?) AND status='published'", (slug, slugify(slug))
    ).fetchone()
    if row is None:
        abort(404)
    return redirect(url_for("public.post", slug=row["slug"]), code=301)


@bp.route("/<int:year>/<int:month>/<int:day>/<slug>/")
@bp.route("/<int:year>/<int:month>/<int:day>/<slug>")
def wp_day_permalink(year, month, day, slug):
    return _redirect_slug(slug)


@bp.route("/<int:year>/<int:month>/<slug>/")
@bp.route("/<int:year>/<int:month>/<slug>")
def wp_month_permalink(year, month, slug):
    return _redirect_slug(slug)


@bp.before_app_request
def wp_query_permalink():
    """Support ?p=123 style WordPress links on the front page."""
    if request.path == "/" and request.args.get("p", "").isdigit():
        row = get_db().execute(
            "SELECT slug FROM posts WHERE wp_id=? AND status='published'", (int(request.args["p"]),)
        ).fetchone()
        if row:
            return redirect(url_for("public.post", slug=row["slug"]), code=301)
    return None

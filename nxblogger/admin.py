"""Maintenance pages: login, posts, uploads, settings, WordPress import."""
import math
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from flask import (Blueprint, abort, current_app, flash, jsonify, redirect, render_template,
                   request, session, url_for)

from . import auth
from .content import (first_image, get_post_tags, make_excerpt, normalize_dt, now_str, parse_dt,
                      parse_tags, sanitize_html, set_post_tags, unique_slug)
from .db import get_db, get_setting, set_setting
from .media import UploadError, extract_data_uris, save_file_storage

bp = Blueprint("admin", __name__, url_prefix="/admin")

PAGE_SIZE = 30


def _safe_next(target):
    if not target:
        return None
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc or not target.startswith("/"):
        return None
    return target.rstrip("?") or None


# --- Login -------------------------------------------------------------------

@bp.route("/login", methods=["GET", "POST"])
def login():
    if auth.is_logged_in():
        return redirect(url_for("admin.dashboard"))
    if request.method == "POST":
        ip = auth.client_ip()
        if auth.too_many_attempts(ip):
            flash("Too many failed attempts. Please wait 15 minutes and try again.", "error")
            return render_template("admin/login.html"), 429
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if username == current_app.config["ADMIN_USER"] and auth.verify_login(username, password):
            auth.clear_attempts(ip)
            csrf = session.get("_csrf")
            session.clear()
            if csrf:
                session["_csrf"] = csrf
            session.permanent = True
            session["user"] = username
            return redirect(_safe_next(request.args.get("next")) or url_for("admin.dashboard"))
        auth.record_failed_attempt(ip)
        flash("Wrong username or password.", "error")
    return render_template("admin/login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("public.index"))


# --- Posts ---------------------------------------------------------------------

@bp.route("/")
@auth.login_required
def dashboard():
    db = get_db()
    q = request.args.get("q", "").strip()
    status = request.args.get("status", "")
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1
    where, params = ["1=1"], []
    if q:
        where.append("(title LIKE ? OR content LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]
    if status in ("draft", "published"):
        where.append("status=?")
        params.append(status)
    clause = " AND ".join(where)
    total = db.execute(f"SELECT COUNT(*) AS c FROM posts WHERE {clause}", params).fetchone()["c"]
    posts = db.execute(
        f"SELECT id, title, slug, status, published_at, updated_at FROM posts WHERE {clause} "
        "ORDER BY published_at DESC LIMIT ? OFFSET ?",
        (*params, PAGE_SIZE, (page - 1) * PAGE_SIZE),
    ).fetchall()
    counts = {r["status"]: r["c"] for r in db.execute("SELECT status, COUNT(*) AS c FROM posts GROUP BY status")}
    return render_template(
        "admin/dashboard.html", posts=posts, q=q, status=status, page=page,
        pages=max(1, math.ceil(total / PAGE_SIZE)), counts=counts, total=total,
    )


def _form_values(row=None, tags=None):
    if row is None:
        return {"title": "", "content": "", "tags": "", "status": "draft", "slug": "", "excerpt": "",
                "published_at": parse_dt(now_str()).strftime("%Y-%m-%dT%H:%M")}
    excerpt = row["excerpt"]
    if excerpt == make_excerpt(row["content"]):
        excerpt = ""  # auto-generated, keep the field empty
    return {
        "title": row["title"], "content": row["content"], "tags": ", ".join(tags or []),
        "status": row["status"], "slug": row["slug"], "excerpt": excerpt,
        "published_at": parse_dt(row["published_at"]).strftime("%Y-%m-%dT%H:%M"),
    }


def _save_post(row=None):
    db = get_db()
    form = request.form
    title = form.get("title", "").strip()[:300]
    content = sanitize_html(extract_data_uris(form.get("content", "")))
    status = "published" if form.get("status") == "published" else "draft"
    values = {
        "title": title, "content": content, "tags": form.get("tags", ""), "status": status,
        "slug": form.get("slug", "").strip(), "excerpt": form.get("excerpt", "").strip(),
        "published_at": form.get("published_at", ""),
    }
    if not title:
        flash("Please give the post a title.", "error")
        return None, values

    now = now_str()
    slug = unique_slug(db, values["slug"] or title, exclude_id=row["id"] if row else None)
    published_at = normalize_dt(values["published_at"], fallback=now)
    excerpt = values["excerpt"][:1000] or make_excerpt(content)
    cover = first_image(content)
    if not cover and row is not None and row["cover_image"] and row["cover_image"] not in row["content"]:
        cover = row["cover_image"]  # keep imported WordPress featured image

    if row is None:
        cur = db.execute(
            "INSERT INTO posts(title, slug, content, excerpt, cover_image, status, created_at, updated_at, published_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (title, slug, content, excerpt, cover, status, now, now, published_at),
        )
        post_id = cur.lastrowid
    else:
        post_id = row["id"]
        db.execute(
            "UPDATE posts SET title=?, slug=?, content=?, excerpt=?, cover_image=?, status=?, updated_at=?, "
            "published_at=? WHERE id=?",
            (title, slug, content, excerpt, cover, status, now, published_at, post_id),
        )
    set_post_tags(db, post_id, parse_tags(values["tags"]))
    db.commit()
    return post_id, values


def _after_save(post_id):
    row = get_db().execute("SELECT slug, status FROM posts WHERE id=?", (post_id,)).fetchone()
    flash("Post published." if row["status"] == "published" else "Draft saved.", "success")
    if request.form.get("action") == "view":
        return redirect(url_for("public.post", slug=row["slug"]))
    return redirect(url_for("admin.edit_post", post_id=post_id))


@bp.route("/post/new", methods=["GET", "POST"])
@auth.login_required
def new_post():
    if request.method == "POST":
        post_id, values = _save_post()
        if post_id:
            return _after_save(post_id)
        return render_template("admin/edit.html", post=None, values=values)
    return render_template("admin/edit.html", post=None, values=_form_values())


@bp.route("/post/<int:post_id>/edit", methods=["GET", "POST"])
@auth.login_required
def edit_post(post_id):
    db = get_db()
    row = db.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
    if row is None:
        abort(404)
    if request.method == "POST":
        saved_id, values = _save_post(row)
        if saved_id:
            return _after_save(saved_id)
        return render_template("admin/edit.html", post=row, values=values)
    return render_template("admin/edit.html", post=row, values=_form_values(row, get_post_tags(db, post_id)))


@bp.route("/post/<int:post_id>/delete", methods=["POST"])
@auth.login_required
def delete_post(post_id):
    db = get_db()
    row = db.execute("SELECT title FROM posts WHERE id=?", (post_id,)).fetchone()
    if row is None:
        abort(404)
    db.execute("DELETE FROM posts WHERE id=?", (post_id,))
    db.commit()
    flash(f"Deleted “{row['title']}”.", "success")
    return redirect(url_for("admin.dashboard"))


@bp.route("/post/<int:post_id>/status", methods=["POST"])
@auth.login_required
def toggle_status(post_id):
    db = get_db()
    row = db.execute("SELECT status FROM posts WHERE id=?", (post_id,)).fetchone()
    if row is None:
        abort(404)
    new = "draft" if row["status"] == "published" else "published"
    db.execute("UPDATE posts SET status=?, updated_at=? WHERE id=?", (new, now_str(), post_id))
    db.commit()
    flash("Post published." if new == "published" else "Post moved to drafts.", "success")
    return redirect(request.referrer or url_for("admin.dashboard"))


# --- Uploads (AJAX from the editor) -------------------------------------------

@bp.route("/api/upload", methods=["POST"])
@auth.login_required
def upload():
    files = request.files.getlist("file")
    if not files:
        return jsonify(error="No file received."), 400
    urls = []
    try:
        for fs in files:
            urls.append(save_file_storage(fs))
    except UploadError as exc:
        return jsonify(error=str(exc), urls=urls), 400
    return jsonify(urls=urls, url=urls[0])


# --- Settings -------------------------------------------------------------------

@bp.route("/settings", methods=["GET", "POST"])
@auth.login_required
def settings():
    if request.method == "POST":
        form_name = request.form.get("form")
        if form_name == "site":
            title = request.form.get("blog_title", "").strip() or current_app.config["DEFAULT_TITLE"]
            set_setting("blog_title", title[:120])
            set_setting("blog_subtitle", request.form.get("blog_subtitle", "").strip()[:200])
            set_setting("footer_text", request.form.get("footer_text", "").strip()[:300])
            try:
                ppp = max(1, min(50, int(request.form.get("posts_per_page", "10"))))
            except ValueError:
                ppp = 10
            set_setting("posts_per_page", str(ppp))
            flash("Settings saved.", "success")
        elif form_name == "password":
            user = current_app.config["ADMIN_USER"]
            current = request.form.get("current_password", "")
            new = request.form.get("new_password", "")
            confirm = request.form.get("confirm_password", "")
            if not auth.verify_login(user, current):
                flash("Current password is wrong.", "error")
            elif new != confirm:
                flash("New passwords do not match.", "error")
            elif len(new) < auth.MIN_PASSWORD_LEN:
                flash(f"New password must be at least {auth.MIN_PASSWORD_LEN} characters.", "error")
            else:
                auth.set_admin_password(current_app.config["DATABASE"], user, new)
                flash("Password changed.", "success")
        return redirect(url_for("admin.settings"))
    values = {
        "blog_title": get_setting("blog_title", current_app.config["DEFAULT_TITLE"]),
        "blog_subtitle": get_setting("blog_subtitle", "Notes, thoughts and projects"),
        "footer_text": get_setting("footer_text", ""),
        "posts_per_page": get_setting("posts_per_page", "10"),
    }
    return render_template("admin/settings.html", values=values, admin_user=current_app.config["ADMIN_USER"])


# --- WordPress import -------------------------------------------------------------

@bp.route("/import", methods=["GET", "POST"])
@auth.login_required
def wp_import():
    from .wpimport import ImportOptions, run_import

    report = None
    if request.method == "POST":
        xml_file = request.files.get("wxr")
        if not xml_file or not xml_file.filename:
            flash("Choose the WordPress export (.xml) file.", "error")
            return redirect(url_for("admin.wp_import"))
        opts = ImportOptions(
            include_pages=bool(request.form.get("include_pages")),
            include_drafts=bool(request.form.get("include_drafts")),
            update_existing=bool(request.form.get("update_existing")),
            download_images=bool(request.form.get("download_images")),
        )
        with tempfile.TemporaryDirectory() as tmp:
            xml_path = Path(tmp) / "export.xml"
            xml_file.save(xml_path)
            zip_path = None
            zip_file = request.files.get("uploads_zip")
            if zip_file and zip_file.filename:
                zip_path = Path(tmp) / "uploads.zip"
                zip_file.save(zip_path)
            try:
                report = run_import(get_db(), xml_path, zip_path, opts,
                                    upload_dir=Path(current_app.config["UPLOAD_DIR"]))
            except Exception as exc:  # noqa: BLE001 - show any parse error to the admin
                current_app.logger.exception("WordPress import failed")
                flash(f"Import failed: {exc}", "error")
                return redirect(url_for("admin.wp_import"))
        flash(f"Import finished: {report.imported} new, {report.updated} updated, {report.skipped} skipped.", "success")
    return render_template("admin/import.html", report=report)

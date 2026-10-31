import io
import zipfile

from PIL import Image

from conftest import PASSWORD, get_csrf
from nxblogger.db import connect
from nxblogger.wpimport import ImportOptions, run_import, wpautop


def _png_bytes(color=(63, 134, 198)):
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), color).save(buf, "PNG")
    return buf.getvalue()


def _new_post(admin, **kw):
    data = {"_csrf": admin.csrf, "title": "Test post", "content": "<p>Hello</p>", "status": "published",
            "tags": "", "published_at": "2026-10-31T10:00", "action": "save"}
    data.update(kw)
    return admin.post("/admin/post/new", data=data)


def test_home_shows_title(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "OH2GAX Blog" in resp.get_data(as_text=True)


def test_admin_requires_login(client):
    resp = client.get("/admin/")
    assert resp.status_code == 302
    assert "/admin/login" in resp.headers["Location"]


def test_wrong_password_and_throttle(client):
    token = get_csrf(client)
    for _ in range(5):
        resp = client.post("/admin/login", data={"username": "oh2gax", "password": "nope", "_csrf": token})
        assert resp.status_code == 200
    resp = client.post("/admin/login", data={"username": "oh2gax", "password": PASSWORD, "_csrf": token})
    assert resp.status_code == 429


def test_csrf_required(client):
    resp = client.post("/admin/login", data={"username": "oh2gax", "password": PASSWORD})
    assert resp.status_code == 400


def test_create_edit_delete_post(admin):
    resp = _new_post(admin, title="Hyvää päivää", tags="Radio, Antennas",
                     content='<p>Hi<script>alert(1)</script></p><img src="/uploads/x.png" onerror="x">')
    assert resp.status_code == 302
    page = admin.get("/post/hyvaa-paivaa").get_data(as_text=True)
    assert "Hyvää päivää" in page and "<script>alert" not in page and "onerror" not in page
    assert "31-10-2026" in page
    assert admin.get("/tag/radio").status_code == 200
    assert "Hyvää päivää" in admin.get("/tag/Radio").get_data(as_text=True)

    resp = admin.post("/admin/post/1/edit", data={"_csrf": admin.csrf, "title": "Changed", "content": "<p>New</p>",
                                                  "status": "draft", "tags": "", "published_at": "", "action": "save",
                                                  "slug": "hyvaa-paivaa"})
    assert resp.status_code == 302
    admin.post("/admin/logout", data={"_csrf": admin.csrf})
    assert admin.get("/post/hyvaa-paivaa").status_code == 404  # draft hidden from visitors


def test_delete_post(admin):
    _new_post(admin)
    assert admin.post("/admin/post/1/delete", data={"_csrf": admin.csrf}).status_code == 302
    assert admin.get("/post/test-post").status_code == 404


def test_duplicate_titles_get_unique_slugs(admin):
    _new_post(admin)
    _new_post(admin)
    assert admin.get("/post/test-post").status_code == 200
    assert admin.get("/post/test-post-2").status_code == 200


def test_image_upload(admin, app):
    resp = admin.post("/admin/api/upload", data={"file": (io.BytesIO(_png_bytes()), "image.png")},
                      headers={"X-CSRF-Token": admin.csrf}, content_type="multipart/form-data")
    assert resp.status_code == 200, resp.get_json()
    url = resp.get_json()["url"]
    assert url.startswith("/uploads/") and url.endswith(".png")
    assert admin.get(url).status_code == 200


def test_upload_rejects_non_image(admin):
    resp = admin.post("/admin/api/upload", data={"file": (io.BytesIO(b"<html>"), "evil.png")},
                      headers={"X-CSRF-Token": admin.csrf}, content_type="multipart/form-data")
    assert resp.status_code == 400


def test_data_uri_images_are_stored(admin):
    import base64
    data = base64.b64encode(_png_bytes()).decode()
    _new_post(admin, content=f'<p><img src="data:image/png;base64,{data}"></p>')
    page = admin.get("/post/test-post").get_data(as_text=True)
    assert "data:image" not in page and '/uploads/' in page


def test_change_password(admin, app):
    resp = admin.post("/admin/settings", data={"_csrf": admin.csrf, "form": "password",
                                               "current_password": PASSWORD, "new_password": "brand-new-pw",
                                               "confirm_password": "brand-new-pw"})
    assert resp.status_code == 302
    conn = connect(app.config["DATABASE"])
    row = conn.execute("SELECT password_hash FROM users WHERE username='oh2gax'").fetchone()
    assert row["password_hash"].startswith("scrypt:") and "brand-new-pw" not in row["password_hash"]


def test_feed_and_archive(admin):
    _new_post(admin, title="Feed me")
    assert "Feed me" in admin.get("/feed.xml").get_data(as_text=True)
    assert "Feed me" in admin.get("/archive").get_data(as_text=True)


def test_wpautop():
    out = wpautop("First line\nsecond line\n\nNew paragraph\n<ul><li>a</li></ul>")
    assert out.startswith("<p>First line<br />\nsecond line</p>")
    assert "<p>New paragraph</p>" in out
    assert "<ul>\n<li>a</li>\n</ul>" in out and "<p><ul>" not in out


WXR = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:excerpt="http://wordpress.org/export/1.2/excerpt/"
  xmlns:content="http://purl.org/rss/1.0/modules/content/" xmlns:dc="http://purl.org/dc/elements/1.1/"
  xmlns:wp="http://wordpress.org/export/1.2/">
<channel>
  <title>Old blog</title>
  <link>https://old.example.com</link>
  <wp:base_site_url>https://old.example.com</wp:base_site_url>
  <item>
    <title>Antenna build</title>
    <content:encoded><![CDATA[Building a dipole.
Second line.

[caption id="attachment_5" align="aligncenter" width="300"]<img class="size-medium wp-image-5" src="https://old.example.com/wp-content/uploads/2021/05/dipole-300x200.png" alt="Dipole" width="300" height="200" /> The finished dipole[/caption]

https://www.youtube.com/watch?v=dQw4w9WgXcQ

[gallery ids="5"]]]></content:encoded>
    <excerpt:encoded><![CDATA[]]></excerpt:encoded>
    <wp:post_id>10</wp:post_id>
    <wp:post_date><![CDATA[2021-05-03 18:22:00]]></wp:post_date>
    <wp:post_name><![CDATA[antenna-build]]></wp:post_name>
    <wp:status><![CDATA[publish]]></wp:status>
    <wp:post_type><![CDATA[post]]></wp:post_type>
    <category domain="category" nicename="uncategorized"><![CDATA[Uncategorized]]></category>
    <category domain="post_tag" nicename="antennas"><![CDATA[Antennas]]></category>
    <wp:postmeta><wp:meta_key>_thumbnail_id</wp:meta_key><wp:meta_value>5</wp:meta_value></wp:postmeta>
  </item>
  <item>
    <title>Gutenberg post</title>
    <content:encoded><![CDATA[<!-- wp:paragraph -->
<p>Block <strong>editor</strong> text.</p>
<!-- /wp:paragraph -->]]></content:encoded>
    <wp:post_id>11</wp:post_id>
    <wp:post_date><![CDATA[2022-01-01 10:00:00]]></wp:post_date>
    <wp:post_name><![CDATA[gutenberg-post]]></wp:post_name>
    <wp:status><![CDATA[draft]]></wp:status>
    <wp:post_type><![CDATA[post]]></wp:post_type>
  </item>
  <item>
    <title>dipole</title>
    <wp:post_id>5</wp:post_id>
    <wp:post_parent>10</wp:post_parent>
    <wp:post_type><![CDATA[attachment]]></wp:post_type>
    <wp:status><![CDATA[inherit]]></wp:status>
    <wp:attachment_url><![CDATA[https://old.example.com/wp-content/uploads/2021/05/dipole.png]]></wp:attachment_url>
  </item>
  <item>
    <title>About</title>
    <content:encoded><![CDATA[About page]]></content:encoded>
    <wp:post_id>2</wp:post_id>
    <wp:status><![CDATA[publish]]></wp:status>
    <wp:post_type><![CDATA[page]]></wp:post_type>
  </item>
</channel>
</rss>
"""


def test_wordpress_import(app, tmp_path):
    xml = tmp_path / "export.xml"
    xml.write_text(WXR, encoding="utf-8")
    zip_path = tmp_path / "uploads.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("wp-content/uploads/2021/05/dipole.png", _png_bytes())
        zf.writestr("wp-content/uploads/2021/05/dipole-300x200.png", _png_bytes())
        zf.writestr("wp-content/uploads/../../evil.png", _png_bytes())

    with app.app_context():
        conn = connect(app.config["DATABASE"])
        report = run_import(conn, xml, zip_path, ImportOptions(), upload_dir=app.config["UPLOAD_DIR"])
        assert (report.imported, report.skipped) == (2, 0)
        assert report.files_copied == 2

        post = conn.execute("SELECT * FROM posts WHERE wp_id=10").fetchone()
        assert post["slug"] == "antenna-build" and post["status"] == "published"
        assert post["published_at"] == "2021-05-03 18:22:00"
        assert "<p>Building a dipole.<br>" in post["content"] or "<p>Building a dipole.<br />" in post["content"]
        assert '<figure class="wp-caption aligncenter">' in post["content"]
        assert "<figcaption>The finished dipole</figcaption>" in post["content"]
        assert "/uploads/wp/2021/05/dipole-300x200.png" in post["content"]
        assert "old.example.com" not in post["content"]
        assert "youtube-nocookie.com/embed/dQw4w9WgXcQ" in post["content"]
        assert 'class="gallery"' in post["content"]
        assert post["cover_image"] == "/uploads/wp/2021/05/dipole.png"
        tags = [r["tag"] for r in conn.execute("SELECT tag FROM post_tags WHERE post_id=?", (post["id"],))]
        assert tags == ["Antennas"]

        draft = conn.execute("SELECT * FROM posts WHERE wp_id=11").fetchone()
        assert draft["status"] == "draft" and "wp:paragraph" not in draft["content"]
        assert "<strong>editor</strong>" in draft["content"]

        # Re-import skips existing posts.
        report2 = run_import(conn, xml, None, ImportOptions(), upload_dir=app.config["UPLOAD_DIR"])
        assert report2.imported == 0 and report2.skipped == 2
        conn.close()

    assert not (app.config["UPLOAD_DIR"].parent / "evil.png").exists()
    client = app.test_client()
    assert client.get("/post/antenna-build").status_code == 200
    assert client.get("/2021/05/03/antenna-build/").status_code == 301
    assert client.get("/?p=10").status_code == 301
    assert client.get("/uploads/wp/2021/05/dipole.png").status_code == 200


def test_web_import_page(admin, tmp_path):
    resp = admin.post("/admin/import", data={"_csrf": admin.csrf, "include_drafts": "1",
                                             "wxr": (io.BytesIO(WXR.encode()), "export.xml")},
                      content_type="multipart/form-data")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Import report" in body
    assert "still point to the old site" in body  # no zip given, images remain remote

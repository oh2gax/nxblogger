"""Import posts from a WordPress export (WXR .xml) file.

WordPress: Tools -> Export -> "All content" (or "Posts") produces the XML file.
Images are not inside the XML; they live in wp-content/uploads. They can be
brought over by
  * uploading a .zip of the wp-content/uploads folder (e.g. from a backup), or
  * pointing the CLI to an already copied uploads directory, or
  * letting the importer download them from the old site while it is still online.
Image links are rewritten to /uploads/wp/... when the file is available locally.
"""
import html
import re
import shutil
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

from defusedxml import ElementTree as ET

from .content import (DATE_FMT, first_image, make_excerpt, normalize_dt, now_str, sanitize_html,
                      set_post_tags, slugify, unique_slug)

FILE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".pdf", ".mp3", ".mp4", ".m4a", ".ogg", ".webm", ".txt"}
SKIP_CATEGORIES = {"uncategorized", "yleinen", "uncategorised"}
MAX_DOWNLOAD = 100 * 1024 * 1024


@dataclass
class ImportOptions:
    include_pages: bool = False
    include_drafts: bool = True
    update_existing: bool = False
    download_images: bool = False


@dataclass
class ImportReport:
    imported: int = 0
    updated: int = 0
    skipped: int = 0
    files_copied: int = 0
    images_downloaded: int = 0
    image_failures: int = 0
    remote_images_left: int = 0
    messages: list = field(default_factory=list)


# --- XML helpers -------------------------------------------------------------

def _split(tag):
    if tag.startswith("{"):
        ns, name = tag[1:].split("}", 1)
        return ns, name
    return "", tag


def _child(elem, local, ns_hint=None):
    for child in elem:
        ns, name = _split(child.tag)
        if name == local and (ns_hint is None or ns_hint in ns):
            return child
    return None


def _text(elem, local, ns_hint=None, default=""):
    child = _child(elem, local, ns_hint)
    return (child.text or default) if child is not None else default


def _children(elem, local, ns_hint=None):
    out = []
    for child in elem:
        ns, name = _split(child.tag)
        if name == local and (ns_hint is None or ns_hint in ns):
            out.append(child)
    return out


# --- Content conversion ------------------------------------------------------

_BLOCKS = (r"(?:table|thead|tfoot|caption|col|colgroup|tbody|tr|td|th|div|dl|dd|dt|ul|ol|li|pre|form|map|area|"
           r"blockquote|address|math|style|p|h[1-6]|hr|fieldset|legend|section|article|aside|hgroup|header|"
           r"footer|nav|figure|figcaption|details|menu|summary|iframe|video|audio)")


def wpautop(text: str) -> str:
    """Python port of the essential parts of WordPress' wpautop()."""
    if not text or not text.strip():
        return ""
    pres = []

    def _keep_pre(m):
        pres.append(m.group(0))
        return f"<pre wp-pre-tag-{len(pres) - 1}></pre>"

    text = re.sub(r"<pre[^>]*>.*?</pre>", _keep_pre, text, flags=re.S | re.I)
    text = text.replace("\r\n", "\n").replace("\r", "\n") + "\n"
    text = re.sub(r"<[^>]*>", lambda m: m.group(0).replace("\n", " "), text)  # newlines inside tags
    text = re.sub(r"<br\s*/?>\s*<br\s*/?>", "\n\n", text, flags=re.I)
    text = re.sub(r"(<" + _BLOCKS + r"[\s/>])", r"\n\n\1", text, flags=re.I)
    text = re.sub(r"(</" + _BLOCKS + r">)", r"\1\n\n", text, flags=re.I)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    parts = re.split(r"\n\s*\n", text)
    text = "".join("<p>" + p.strip("\n") + "</p>\n" for p in parts if p.strip())
    text = re.sub(r"<p>\s*</p>", "", text)
    text = re.sub(r"<p>([^<]+)</(div|address|form)>", r"<p>\1</p></\2>", text)
    text = re.sub(r"<p>\s*(</?" + _BLOCKS + r"[^>]*>)\s*</p>", r"\1", text, flags=re.I)
    text = re.sub(r"<p>(<li.+?)</p>", r"\1", text)
    text = re.sub(r"<p><blockquote([^>]*)>", r"<blockquote\1><p>", text, flags=re.I)
    text = text.replace("</blockquote></p>", "</p></blockquote>")
    text = re.sub(r"<p>\s*(</?" + _BLOCKS + r"[^>]*>)", r"\1", text, flags=re.I)
    text = re.sub(r"(</?" + _BLOCKS + r"[^>]*>)\s*</p>", r"\1", text, flags=re.I)
    text = re.sub(r"<br\s*/?>", "<br />", text, flags=re.I)
    text = re.sub(r"(?<!<br />)\s*\n", "<br />\n", text)
    text = re.sub(r"(</?" + _BLOCKS + r"[^>]*>)\s*<br />", r"\1", text, flags=re.I)
    text = re.sub(r"<br />(\s*</?(?:p|li|div|dl|dd|dt|th|pre|td|ul|ol)[^>]*>)", r"\1", text, flags=re.I)
    text = re.sub(r"\n</p>$", "</p>", text)
    for i, pre in enumerate(pres):
        text = text.replace(f"<pre wp-pre-tag-{i}></pre>", pre)
    return text.strip()


def _attrs(raw: str) -> dict:
    out = {}
    for m in re.finditer(r"""([\w-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|(\S+))""", raw or ""):
        out[m.group(1).lower()] = html.unescape(m.group(2) or m.group(3) or m.group(4) or "")
    return out


def _caption(m):
    attrs = _attrs(m.group(1))
    inner = m.group(2).strip()
    img = re.match(r"^((?:<a\b[^>]*>\s*)?<img\b[^>]*>(?:\s*</a>)?)(.*)$", inner, flags=re.S | re.I)
    if img:
        media, caption = img.group(1), img.group(2).strip()
    else:
        media, caption = inner, ""
    caption = caption or attrs.get("caption", "")
    align = attrs.get("align", "")
    figcap = f"<figcaption>{caption}</figcaption>" if caption else ""
    return f'<figure class="wp-caption {html.escape(align)}">{media}{figcap}</figure>'


YOUTUBE_RE = re.compile(
    r"(^|>|\n)[ \t]*(?:<a[^>]*>)?(https?://(?:www\.|m\.)?(?:youtube\.com/watch\?(?:[^\s<]*&(?:amp;)?)?v=|youtu\.be/)"
    r"([\w-]{11})[^\s<]*)(?:</a>)?[ \t]*(?=<|\n|$)",
    re.I,
)


def embed_youtube(text: str, wrap=None) -> str:
    def _embed(m):
        frame = (f'<div class="video-embed"><iframe src="https://www.youtube-nocookie.com/embed/{m.group(3)}" '
                 'width="560" height="315" allowfullscreen frameborder="0"></iframe></div>')
        return m.group(1) + (wrap(frame) if wrap else frame)
    return YOUTUBE_RE.sub(_embed, text)


def convert_content(raw: str, attachments: dict, children: list) -> str:
    """Convert WordPress post content (classic or block editor) to clean HTML."""
    text = raw or ""
    is_blocks = "<!-- wp:" in text
    text = re.sub(r"<!--\s*/?wp:.*?-->", "", text, flags=re.S)
    text = re.sub(r"<!--more.*?-->|<!--nextpage-->", "", text)

    # Block-level replacements are parked behind placeholders so wpautop()
    # does not wrap or split them (WordPress also runs shortcodes after autop).
    blocks = []

    def _park(html_block):
        blocks.append(html_block)
        return f"\n\nnxbblock{len(blocks) - 1}x\n\n"

    text = re.sub(r"\[caption([^\]]*)\](.*?)\[/caption\]", lambda m: _park(_caption(m)), text, flags=re.S | re.I)

    def _gallery(m):
        attrs = _attrs(m.group(1))
        ids = [i.strip() for i in attrs.get("ids", "").split(",") if i.strip()]
        urls = [attachments[i] for i in ids if i in attachments] if ids else [attachments[c] for c in children if c in attachments]
        if not urls:
            return ""
        imgs = "".join(f'<a href="{html.escape(u)}"><img src="{html.escape(u)}" alt="" loading="lazy"></a>' for u in urls)
        return _park(f'<div class="gallery">{imgs}</div>')

    text = re.sub(r"\[gallery([^\]]*)\]", _gallery, text, flags=re.I)
    text = re.sub(r"\[embed[^\]]*\](.*?)\[/embed\]", lambda m: "\n" + m.group(1).strip() + "\n", text, flags=re.S | re.I)

    def _media(m):
        kind, attrs = m.group(1).lower(), _attrs(m.group(2))
        src = attrs.get("src") or next((v for k, v in attrs.items() if k in ("mp4", "webm", "ogv", "mp3", "m4a", "ogg", "wav")), "")
        return _park(f'<{kind} controls src="{html.escape(src)}"></{kind}>') if src else ""

    text = re.sub(r"\[(video|audio)([^\]]*)\](?:\s*\[/\1\])?", _media, text, flags=re.I)
    text = re.sub(r"\[/?(?:playlist|contact-form-7|contact-form|wpforms|vc_\w+|et_pb_\w+)[^\]]*\]", "", text, flags=re.I)

    text = embed_youtube(text, _park)
    if not is_blocks:
        text = wpautop(text)

    for i, block in enumerate(blocks):
        token = f"nxbblock{i}x"
        text = re.sub(rf"<p>\s*{token}\s*(?:<br\s*/?>)?\s*</p>", lambda _m, b=block: b, text)
        text = re.sub(rf"{token}(?:<br\s*/?>)?", lambda _m, b=block: b, text)

    # Tidy up: empty paragraphs and the blank lines the block editor leaves behind.
    text = re.sub(r"<p[^>]*>(?:\s|&nbsp;| |<br\s*/?>)*</p>", "", text, flags=re.I)
    text = re.sub(r"\n[ \t]*(?:\n[ \t]*)+", "\n", text)
    return text.strip()


# --- Files: zip/dir copy, URL rewriting, downloads ----------------------------

def _rel_parts(path_str: str):
    parts = [p for p in path_str.replace("\\", "/").split("/") if p not in ("", ".")]
    if ".." in parts:
        return None
    if "uploads" in parts:
        idx = len(parts) - 1 - parts[::-1].index("uploads")
        parts = parts[idx + 1:]
    else:
        for i, p in enumerate(parts):
            if re.fullmatch(r"(19|20)\d\d", p) and i + 1 < len(parts) and re.fullmatch(r"\d\d", parts[i + 1]):
                parts = parts[i:]
                break
    if not parts or Path(parts[-1]).suffix.lower() not in FILE_EXTS:
        return None
    return parts


def copy_uploads_zip(zip_path: Path, dest: Path, report: ImportReport):
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            parts = _rel_parts(info.filename)
            if not parts:
                continue
            target = dest.joinpath(*parts)
            if target.exists() and target.stat().st_size == info.file_size:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
            report.files_copied += 1


def copy_uploads_dir(src_dir: Path, dest: Path, report: ImportReport):
    for path in Path(src_dir).rglob("*"):
        if not path.is_file():
            continue
        parts = _rel_parts(path.relative_to(src_dir).as_posix())
        if not parts:
            continue
        target = dest.joinpath(*parts)
        if target.exists() and target.stat().st_size == path.stat().st_size:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        report.files_copied += 1


class UrlLocalizer:
    """Maps old wp-content/uploads URLs to local /uploads/wp/... URLs."""

    def __init__(self, dest: Path, base_hosts: set, download: bool, report: ImportReport, log=None):
        self.dest, self.base_hosts, self.download = dest, base_hosts, download
        self.report, self.log, self.cache = report, log, {}

    def _host_ok(self, host):
        host = (host or "").lower()
        bare = {h[4:] if h.startswith("www.") else h for h in self.base_hosts}
        h = host[4:] if host.startswith("www.") else host
        return h in bare

    def __call__(self, url: str) -> str:
        if url in self.cache:
            return self.cache[url]
        result = url
        marker = "/wp-content/uploads/"
        if marker in url:
            rest = unquote(url.split(marker, 1)[1].split("?", 1)[0].split("#", 1)[0])
            parts = _rel_parts(rest)
            if parts:
                target = self.dest.joinpath(*parts)
                local = "/uploads/wp/" + quote("/".join(parts))
                if target.is_file():
                    result = local
                elif self.download and self._host_ok(urlparse(url).hostname) and self._fetch(url, target):
                    result = local
                else:
                    self.report.remote_images_left += 1
        self.cache[url] = result
        return result

    def _fetch(self, url, target: Path) -> bool:
        if url.startswith("//"):
            url = "https:" + url
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "NXBlogger-importer/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - http(s) only, host checked
                data = resp.read(MAX_DOWNLOAD + 1)
            if len(data) > MAX_DOWNLOAD:
                raise ValueError("file too large")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            self.report.images_downloaded += 1
            if self.log:
                self.log(f"  downloaded {url}")
            return True
        except Exception as exc:  # noqa: BLE001 - keep importing on network errors
            self.report.image_failures += 1
            self.report.messages.append(f"Could not download {url}: {exc}")
            return False


_URL_ATTR = re.compile(r"""\b(src|href)=(["'])([^"']*?/wp-content/uploads/[^"']+)\2""", re.I)


def rewrite_urls(content: str, localize: UrlLocalizer) -> str:
    return _URL_ATTR.sub(lambda m: f'{m.group(1)}="{html.escape(localize(html.unescape(m.group(3))))}"', content)


# --- Main entry point ----------------------------------------------------------

def run_import(db, xml_path, uploads_source=None, opts=None, upload_dir=None, log=None) -> ImportReport:
    opts = opts or ImportOptions()
    report = ImportReport()
    upload_dir = Path(upload_dir)
    wp_dir = upload_dir / "wp"
    wp_dir.mkdir(parents=True, exist_ok=True)

    root = ET.parse(str(xml_path)).getroot()
    channel = _child(root, "channel")
    if channel is None:
        raise ValueError("This does not look like a WordPress export file (no <channel>).")

    base_hosts = set()
    for local, hint in (("link", None), ("base_site_url", "wordpress.org"), ("base_blog_url", "wordpress.org")):
        host = urlparse(_text(channel, local, hint).strip()).hostname
        if host:
            base_hosts.add(host.lower())

    if uploads_source:
        src = Path(uploads_source)
        if src.is_dir():
            copy_uploads_dir(src, wp_dir, report)
        else:
            copy_uploads_zip(src, wp_dir, report)
        if log:
            log(f"Copied {report.files_copied} files into {wp_dir}")

    items = _children(channel, "item")
    attachments, children = {}, {}
    for item in items:
        if _text(item, "post_type", "wordpress.org") == "attachment":
            aid = _text(item, "post_id", "wordpress.org").strip()
            url = _text(item, "attachment_url", "wordpress.org").strip()
            if aid and url:
                attachments[aid] = url
                parent = _text(item, "post_parent", "wordpress.org").strip()
                children.setdefault(parent, []).append(aid)

    localize = UrlLocalizer(wp_dir, base_hosts, opts.download_images, report, log)
    allowed_types = {"post", "page"} if opts.include_pages else {"post"}

    for item in items:
        post_type = _text(item, "post_type", "wordpress.org")
        if post_type not in allowed_types:
            continue
        wp_status = _text(item, "status", "wordpress.org")
        if wp_status in ("publish", "future"):
            status = "published"
        elif wp_status in ("draft", "pending", "private") and opts.include_drafts:
            status = "draft"
        else:
            report.skipped += 1
            continue

        wp_id = int(_text(item, "post_id", "wordpress.org", "0") or 0) or None
        title = html.unescape(_text(item, "title").strip()) or "(untitled)"
        existing = db.execute("SELECT * FROM posts WHERE wp_id=?", (wp_id,)).fetchone() if wp_id else None
        if existing and not opts.update_existing:
            report.skipped += 1
            continue

        raw = _text(item, "encoded", "purl.org/rss/1.0/modules/content")
        content = convert_content(raw, attachments, children.get(str(wp_id), []))
        content = rewrite_urls(content, localize)
        content = sanitize_html(content)

        date = _text(item, "post_date", "wordpress.org").strip()
        if not date or date.startswith("0000"):
            date = _text(item, "post_date_gmt", "wordpress.org").strip()
        published_at = normalize_dt(date, fallback=now_str()) if date and not date.startswith("0000") else now_str()

        excerpt_raw = _text(item, "encoded", "/excerpt/").strip()
        excerpt = make_excerpt(excerpt_raw or content)

        cover = None
        for meta in _children(item, "postmeta", "wordpress.org"):
            if _text(meta, "meta_key", "wordpress.org") == "_thumbnail_id":
                aid = _text(meta, "meta_value", "wordpress.org").strip()
                if aid in attachments:
                    cover = localize(attachments[aid])
        cover = cover or first_image(content)

        tags = []
        for cat in _children(item, "category"):
            if cat.get("domain") in ("category", "post_tag"):
                name = html.unescape((cat.text or "").strip())
                if name and (cat.get("nicename", "").lower() not in SKIP_CATEGORIES) and name.lower() not in SKIP_CATEGORIES:
                    if name.lower() not in {t.lower() for t in tags}:
                        tags.append(name)

        now = now_str()
        if existing:
            db.execute(
                "UPDATE posts SET title=?, content=?, excerpt=?, cover_image=?, status=?, updated_at=?, published_at=? "
                "WHERE id=?",
                (title, content, excerpt, cover, status, now, published_at, existing["id"]),
            )
            post_id = existing["id"]
            report.updated += 1
        else:
            name = unquote(_text(item, "post_name", "wordpress.org").strip())
            slug = unique_slug(db, name or title)
            cur = db.execute(
                "INSERT INTO posts(title, slug, content, excerpt, cover_image, status, created_at, updated_at, "
                "published_at, wp_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (title, slug, content, excerpt, cover, status, now, now, published_at, wp_id),
            )
            post_id = cur.lastrowid
            report.imported += 1
        set_post_tags(db, post_id, tags)
        if log:
            log(f"{'Updated' if existing else 'Imported'}: {title}")

    db.commit()
    if report.remote_images_left:
        report.messages.insert(0, f"{report.remote_images_left} image link(s) still point to the old site. "
                                  "Upload the wp-content/uploads zip or enable downloading to copy them.")
    return report


__all__ = ["ImportOptions", "ImportReport", "run_import", "wpautop", "convert_content", "DATE_FMT"]

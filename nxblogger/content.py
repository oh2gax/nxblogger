"""Content helpers: HTML sanitising, slugs, excerpts, dates and tags."""
import html
import re
import unicodedata
from datetime import datetime
from zoneinfo import ZoneInfo

import nh3
from flask import current_app

DATE_FMT = "%Y-%m-%d %H:%M:%S"

ALLOWED_TAGS = {
    "p", "br", "h1", "h2", "h3", "h4", "h5", "h6", "strong", "b", "em", "i", "u",
    "s", "strike", "del", "ins", "sub", "sup", "small", "mark", "a", "ul", "ol", "li",
    "blockquote", "pre", "code", "kbd", "img", "figure", "figcaption", "hr",
    "table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption", "colgroup", "col",
    "div", "span", "iframe", "video", "audio", "source", "dl", "dt", "dd",
}

ALLOWED_ATTRIBUTES = {
    "*": {"class", "title"},
    "a": {"href", "target", "name"},
    "img": {"src", "alt", "width", "height", "loading"},
    "iframe": {"src", "width", "height", "allow", "allowfullscreen", "frameborder"},
    "video": {"src", "controls", "width", "height", "poster", "preload"},
    "audio": {"src", "controls", "preload"},
    "source": {"src", "type"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan", "scope"},
    "ol": {"start", "type"},
    "col": {"span"},
    "colgroup": {"span"},
}

# Only these embed sources are kept for <iframe>.
IFRAME_PREFIXES = (
    "https://www.youtube.com/embed/",
    "https://www.youtube-nocookie.com/embed/",
    "https://player.vimeo.com/video/",
)


def _attribute_filter(element, attribute, value):
    if element == "iframe" and attribute == "src":
        return value if value.startswith(IFRAME_PREFIXES) else None
    if element == "a" and attribute == "target":
        return "_blank" if value == "_blank" else None
    return value


def sanitize_html(raw: str) -> str:
    if not raw:
        return ""
    cleaned = nh3.clean(
        raw,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        attribute_filter=_attribute_filter,
        url_schemes={"http", "https", "mailto"},
        link_rel="noopener noreferrer",
        strip_comments=True,
    )
    # Drop empty iframes whose src was rejected.
    cleaned = re.sub(r"<iframe(?![^>]*\ssrc=)[^>]*>\s*</iframe>", "", cleaned)
    return cleaned.strip()


_TRANSLIT = str.maketrans({"ß": "ss", "æ": "ae", "ø": "o", "đ": "d", "ł": "l", "Æ": "ae", "Ø": "o"})


def slugify(text: str, max_len: int = 80) -> str:
    text = (text or "").translate(_TRANSLIT)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    text = text[:max_len].strip("-")
    return text or "post"


def unique_slug(db, base: str, exclude_id=None) -> str:
    base = slugify(base)
    slug, n = base, 2
    while True:
        row = db.execute("SELECT id FROM posts WHERE slug=?", (slug,)).fetchone()
        if row is None or (exclude_id is not None and row["id"] == exclude_id):
            return slug
        slug = f"{base}-{n}"
        n += 1


def html_to_text(content: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", content or "", flags=re.S | re.I)
    text = re.sub(r"<br\s*/?>|</p>|</h\d>|</li>|</div>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def make_excerpt(content: str, length: int = 280) -> str:
    text = html_to_text(content)
    if len(text) <= length:
        return text
    cut = text[:length].rsplit(" ", 1)[0]
    return cut.rstrip(".,;:!?-") + "…"


_IMG_SRC = re.compile(r"<img[^>]+src=\"([^\"]+)\"", re.I)


def first_image(content: str):
    m = _IMG_SRC.search(content or "")
    return html.unescape(m.group(1)) if m else None


def tz():
    try:
        return ZoneInfo(current_app.config.get("TIMEZONE", "Europe/Helsinki"))
    except Exception:  # noqa: BLE001 - fall back to UTC on bad tz name
        return ZoneInfo("UTC")


def now_str() -> str:
    """Current time in the blog's timezone, stored as naive local time."""
    return datetime.now(tz()).strftime(DATE_FMT)


def parse_dt(value):
    if not value:
        return None
    for fmt in (DATE_FMT, "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue
    return None


def normalize_dt(value, fallback=None) -> str:
    dt = parse_dt(value)
    if dt is None:
        return fallback or now_str()
    return dt.strftime(DATE_FMT)


def parse_tags(raw: str):
    seen, tags = set(), []
    for part in re.split(r"[,;]", raw or ""):
        tag = re.sub(r"\s+", " ", part).strip()[:60]
        if tag and tag.lower() not in seen:
            seen.add(tag.lower())
            tags.append(tag)
    return tags


def set_post_tags(db, post_id: int, tags):
    db.execute("DELETE FROM post_tags WHERE post_id=?", (post_id,))
    for tag in tags:
        db.execute("INSERT OR IGNORE INTO post_tags(post_id, tag) VALUES(?, ?)", (post_id, tag))


def get_post_tags(db, post_id: int):
    rows = db.execute("SELECT tag FROM post_tags WHERE post_id=? ORDER BY tag COLLATE NOCASE", (post_id,))
    return [r["tag"] for r in rows]

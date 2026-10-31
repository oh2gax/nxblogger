"""Image upload handling (file picker, clipboard paste, drag & drop, data: URIs)."""
import base64
import io
import re
import secrets
from datetime import datetime
from pathlib import Path

from flask import current_app
from PIL import Image, UnidentifiedImageError
from werkzeug.utils import secure_filename

FORMAT_EXT = {"PNG": "png", "JPEG": "jpg", "GIF": "gif", "WEBP": "webp"}


class UploadError(ValueError):
    pass


def _detect_format(data: bytes) -> str:
    try:
        with Image.open(io.BytesIO(data)) as img:
            fmt = img.format
            img.verify()
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise UploadError("File is not a valid image.") from exc
    if fmt not in FORMAT_EXT:
        raise UploadError("Only PNG, JPEG, GIF and WebP images are allowed.")
    return FORMAT_EXT[fmt]


def save_image_bytes(data: bytes, original_name: str = "") -> str:
    """Validate and store an image; return its public URL (/uploads/...)."""
    max_bytes = current_app.config["MAX_IMAGE_MB"] * 1024 * 1024
    if not data:
        raise UploadError("Empty file.")
    if len(data) > max_bytes:
        raise UploadError(f"Image is larger than {current_app.config['MAX_IMAGE_MB']} MB.")
    ext = _detect_format(data)

    now = datetime.now()
    rel_dir = Path(f"{now:%Y}") / f"{now:%m}"
    stem = secure_filename(Path(original_name or "").stem).lower()[:40]
    if not stem or stem in ("image", "blob", "clipboard"):
        stem = "image"
    name = f"{stem}-{now:%Y%m%d-%H%M%S}-{secrets.token_hex(3)}.{ext}"

    target_dir = Path(current_app.config["UPLOAD_DIR"]) / rel_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / name).write_bytes(data)
    return f"/uploads/{rel_dir.as_posix()}/{name}"


def save_file_storage(fs) -> str:
    return save_image_bytes(fs.read(), fs.filename or "")


_DATA_URI = re.compile(r"""src=(["'])data:image/(png|jpe?g|gif|webp);base64,([A-Za-z0-9+/=\s]+)\1""", re.I)


def extract_data_uris(content: str) -> str:
    """Store inline base64 images (e.g. pasted from Word) as files."""
    def _replace(m):
        try:
            data = base64.b64decode(re.sub(r"\s+", "", m.group(3)), validate=True)
            url = save_image_bytes(data, "pasted")
            return f'src="{url}"'
        except (UploadError, ValueError):
            return 'src=""'
    return _DATA_URI.sub(_replace, content or "")

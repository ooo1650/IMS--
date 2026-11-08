"""Product photos, stored content-addressed so their URLs never change.

An upload is fully decoded and re-encoded (drops EXIF, scripts and anything
else that isn't pixels), then saved as two WebP files named by the hash of the
upload:

    products/<key>.webp     up to 800 px, product detail
    products/<key>-t.webp   up to 160 px, POS tiles and lists

A new photo is a new URL, so browsers and the POS offline cache keep the files
forever (nginx send Cache-Control: immutable) and never show a stale one.
Files live on disk (MEDIA_ROOT), never in the database.
"""
import hashlib
from io import BytesIO
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 40_000_000  # decompression-bomb guard (a 40 MP photo is plenty)
FORMATS = ["JPEG", "PNG", "WEBP"]
SIZES = {"": 800, "-t": 160}


def media_dir() -> Path:
    return Path(settings.MEDIA_ROOT) / "products"


def image_urls(key):
    """(full, thumb) URLs, or (None, None) without a photo."""
    if not key:
        return None, None
    base = f"{settings.MEDIA_URL}products/{key}"
    return f"{base}.webp", f"{base}-t.webp"


def store_image(upload) -> str:
    """Validate, resize and save an uploaded photo; returns its key."""
    if upload.size > MAX_UPLOAD_BYTES:
        raise ValidationError(f"Image is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    raw = upload.read()
    key = hashlib.sha256(raw).hexdigest()[:32]
    folder = media_dir()
    if all((folder / f"{key}{s}.webp").exists() for s in SIZES):
        return key
    try:
        img = Image.open(BytesIO(raw), formats=FORMATS)
        if img.width * img.height > MAX_PIXELS:
            raise ValidationError("Image is too large (over 40 megapixels)")
        img.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as e:
        raise ValidationError("Not a readable image. Use JPEG, PNG or WebP.") from e
    img = ImageOps.exif_transpose(img)
    alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
    img = img.convert("RGBA" if alpha else "RGB")
    folder.mkdir(parents=True, exist_ok=True)
    for suffix, px in SIZES.items():
        out = img.copy()
        out.thumbnail((px, px), Image.Resampling.LANCZOS)
        tmp = folder / f".{key}{suffix}.tmp"
        out.save(tmp, "WEBP", quality=80, method=4)
        tmp.replace(folder / f"{key}{suffix}.webp")  # atomic: readers never see half a file
    return key


def remove_if_unused(key):
    """Delete a replaced photo's files unless another product still uses them."""
    from .models import Product

    if key and not Product.objects.filter(image_key=key).exists():
        for suffix in SIZES:
            (media_dir() / f"{key}{suffix}.webp").unlink(missing_ok=True)

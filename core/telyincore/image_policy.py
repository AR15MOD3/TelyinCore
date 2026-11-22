"""Adapter/local-vision image normalization, matching the core composer limits.

Only downscale, at most 2048px on the long edge and 6 MiB after encoding.
Preserve eligible PNG/JPEG bytes; animated formats use a still first frame.
Cached originals and session records are never rewritten.
"""
from __future__ import annotations

import io
import warnings

MAX_IMAGE_BYTES = 6 * 1024 * 1024
MAX_IMAGE_EDGE = 2048
IMAGE_MIMES = {"image/png", "image/jpeg", "image/webp", "image/gif"}


def normalize_image(raw: bytes, mime: str) -> tuple[bytes, str]:
    from PIL import Image
    if mime not in IMAGE_MIMES:
        raise ValueError("只接受 PNG / JPEG / WebP / GIF 图片")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw), formats=("PNG", "JPEG", "WEBP", "GIF")) as picture:
                if picture.width * picture.height > 16_000_000:
                    raise ValueError("图片像素超过 1600 万")
                picture.seek(0)
                picture.load()
                if (mime in ("image/gif", "image/webp") or len(raw) > MAX_IMAGE_BYTES
                        or max(picture.size) > MAX_IMAGE_EDGE):
                    picture.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE))
                    output = io.BytesIO()
                    picture.convert("RGB").save(output, format="PNG")
                    raw, mime = output.getvalue(), "image/png"
                    if len(raw) > MAX_IMAGE_BYTES:
                        output = io.BytesIO()
                        picture.convert("RGB").save(output, format="JPEG", quality=85)
                        raw, mime = output.getvalue(), "image/jpeg"
    except (OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError("图片无效或过大，请使用 PNG / JPEG / WebP / GIF") from exc
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("图片转换后仍超过 6 MB 限制")
    return raw, mime

"""JPEG snapshots and expiring links for Instagram's server-side image fetch."""

import hashlib
import hmac
import io
import re
import time
from urllib.parse import urlencode, urlsplit

from PIL import Image

from app.config import settings
from app.services.image_storage import decode_image, storage_path

LINK_LIFETIME_SECONDS = 15 * 60


def media_signature(image_id: str, expires: int) -> str:
    # A separate purpose prevents these links from becoming login credentials.
    message = f"instagram-media-v1:{image_id}:{expires}".encode()
    return hmac.new(settings.jwt_secret.encode(), message, hashlib.sha256).hexdigest()


def create_image_link(source_key: str, source_sha256: str) -> dict:
    """Freeze a JPEG copy; the original approved PNG remains unchanged."""
    if not source_sha256:
        raise ValueError("The attached image has no integrity checksum")
    try:
        raw = storage_path(source_key).read_bytes()
    except OSError as exc:
        raise ValueError("The attached image file is missing") from exc
    if hashlib.sha256(raw).hexdigest() != source_sha256:
        raise ValueError("The attached image changed; select and review the image again")
    return create_image_link_from_bytes(raw)


def validate_instagram_image(raw: bytes) -> None:
    image = decode_image(raw)
    ratio = image.width / image.height
    if image.width < 320 or not 0.8 <= ratio <= 1.91:
        raise ValueError("Instagram needs an image at least 320px wide with an aspect ratio from 4:5 to 1.91:1. Use a square image.")


def create_image_link_from_bytes(raw: bytes) -> dict:
    """Prepare a fresh link from the immutable scheduled image bytes."""
    base = (settings.public_media_base_url or "").strip().rstrip("/")
    origin = urlsplit(base)
    if (origin.scheme != "https" or not origin.hostname or origin.username
            or origin.password or origin.query or origin.fragment):
        raise ValueError("Configure PUBLIC_MEDIA_BASE_URL with this API's public HTTPS URL")
    image = decode_image(raw).convert("RGBA")
    background = Image.new("RGB", image.size, "white")
    background.paste(image, mask=image.getchannel("A"))
    if background.width > 1080:
        background = background.resize((1080, round(background.height * 1080 / background.width)), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    background.save(buffer, "JPEG", quality=95, optimize=True)
    jpeg = buffer.getvalue()
    if len(jpeg) > 8 * 1024 * 1024:
        raise ValueError("The JPEG exceeds 8 MB; use a smaller campaign image")
    image_id = hashlib.sha256(jpeg).hexdigest()
    path = storage_path(f"instagram_exports/{image_id}.jpg")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(jpeg)
    expires = int(time.time()) + LINK_LIFETIME_SECONDS
    query = urlencode({"expires": expires, "signature": media_signature(image_id, expires)})
    return {"image_url": f"{base}/media/instagram/{image_id}.jpg?{query}",
            "expires_at": expires, "content_type": "image/jpeg"}


def read_signed_image(image_id: str, expires: int, signature: str) -> bytes:
    """Authorize only one JPEG snapshot, without granting any other app access."""
    now = int(time.time())
    if (not re.fullmatch(r"[a-f0-9]{64}", image_id)
            or not re.fullmatch(r"[a-f0-9]{64}", signature)
            or expires <= now or expires > now + LINK_LIFETIME_SECONDS
            or not hmac.compare_digest(signature, media_signature(image_id, expires))):
        raise ValueError("Invalid or expired image link")
    try:
        raw = storage_path(f"instagram_exports/{image_id}.jpg").read_bytes()
    except OSError as exc:
        raise ValueError("Image is no longer available") from exc
    if hashlib.sha256(raw).hexdigest() != image_id:
        raise ValueError("Image is no longer available")
    return raw

"""Compose the exact image displayed, approved and delivered with a post."""

import hashlib
import io
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import select, update

from app.models import PlatformVariant, utcnow
from app.services.image_storage import storage_path


def _font(size: int, bold=False):
    candidates = [
        f"/usr/share/fonts/truetype/dejavu/DejaVuSans{'-Bold' if bold else ''}.ttf",
        f"/System/Library/Fonts/Supplemental/Arial{' Bold' if bold else ''}.ttf",
    ]
    for filename in candidates:
        if Path(filename).is_file():
            return ImageFont.truetype(filename, size)
    return ImageFont.load_default(size=size)


def _lines(draw, text, font, width):
    result, current = [], ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if current and draw.textlength(candidate, font=font) > width:
            result.append(current)
            current = word
        else:
            current = candidate
    if current:
        result.append(current)
    return result


def render_visual(visual) -> tuple[str, str]:
    if visual.status != "succeeded" or not visual.image_key:
        raise ValueError("Wait until the image is ready")
    try:
        with Image.open(storage_path(visual.image_key)) as original:
            image = original.convert("RGBA")
    except OSError as exc:
        raise ValueError("The generated image file is missing or invalid") from exc
    width, height = image.size
    overlay = Image.new("RGBA", image.size)
    draw = ImageDraw.Draw(overlay)
    start = int(height * .64)
    for y in range(start, height):
        fraction = (y - start) / max(1, height - start)
        alpha = int(min(.96, fraction / .35 * .8) * 255)
        draw.line((0, y, width, y), fill=(8, 18, 15, alpha))
    image = Image.alpha_composite(image, overlay)
    draw = ImageDraw.Draw(image)
    margin = int(width * .045)
    max_width = width - 2 * margin
    size = max(8, int(min(width, height) * .05))
    while True:
        headline_font, cta_font = _font(size, True), _font(max(8, int(size * .53)))
        headline = _lines(draw, visual.headline or "", headline_font, max_width)
        cta = _lines(draw, visual.call_to_action or "", cta_font, max_width)
        cta_size = max(8, int(size * .53))
        total = len(headline) * size * 1.2 + len(cta) * cta_size * 1.35 + size * .4
        if total <= height * .27 or size <= 8:
            break
        size = max(8, int(size * .93))
    y = height - height * .04 - total
    for line in headline:
        draw.text((margin, y), line, font=headline_font, fill="white", anchor="lt")
        y += size * 1.2
    y += size * .4
    for line in cta:
        draw.text((margin, y), line, font=cta_font, fill="#d6f4c6", anchor="lt")
        y += cta_size * 1.35
    stream = io.BytesIO()
    image.convert("RGB").save(stream, "PNG")
    raw = stream.getvalue()
    key = f"posts/{uuid4().hex}.png"
    path = storage_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return key, hashlib.sha256(raw).hexdigest()


def automatically_attach(db, visual) -> None:
    variant = db.scalar(select(PlatformVariant).where(
        PlatformVariant.source_post_id == visual.source_post_id,
        PlatformVariant.platform == visual.platform, PlatformVariant.status == "draft",
        PlatformVariant.image_required.is_(True),
        PlatformVariant.selected_visual_id.is_(None)))
    if variant is None:
        return
    key, digest = render_visual(visual)
    db.execute(update(PlatformVariant).where(
        PlatformVariant.id == variant.id, PlatformVariant.status == "draft",
        PlatformVariant.image_required.is_(True),
        PlatformVariant.selected_visual_id.is_(None)
    ).values(selected_visual_id=visual.id, selected_image_key=key, selected_image_sha256=digest,
             image_required=True, updated_at=utcnow()))


def ensure_media_ready(variant) -> None:
    if variant.platform == "instagram" and not variant.selected_image_key:
        raise ValueError("Instagram requires an image. Select or generate one before approval.")
    if variant.image_required and not variant.selected_image_key:
        raise ValueError("This post needs its image. Wait for generation, select an image, or explicitly choose text only.")
    if variant.selected_image_key and not storage_path(variant.selected_image_key).is_file():
        raise ValueError("The attached image file is missing. Restore or replace it before scheduling.")
    if variant.platform == "instagram":
        from app.services.instagram_media import validate_instagram_image
        raw = storage_path(variant.selected_image_key).read_bytes()
        if hashlib.sha256(raw).hexdigest() != variant.selected_image_sha256:
            raise ValueError("The attached image changed; select it again before approval")
        validate_instagram_image(raw)

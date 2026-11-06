"""Private local storage and preparation of campaign reference images."""

import base64
import io
import warnings
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageOps, UnidentifiedImageError

from app.config import settings

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 25_000_000
MAX_OUTPUT_BYTES = 20 * 1024 * 1024
WORKING_EDGE = 1600


def decode_image(raw: bytes) -> Image.Image:
    """Validate raster contents and pixel limits before allocating a full image."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as image:
                if image.format not in {"JPEG", "PNG", "WEBP"}:
                    raise ValueError("Use a JPEG, PNG, or WebP image")
                if image.width * image.height > MAX_PIXELS:
                    raise ValueError("Images must contain at most 25 million pixels")
                if getattr(image, "n_frames", 1) != 1:
                    raise ValueError("Upload a still image, not an animation")
                image.load()
                oriented = ImageOps.exif_transpose(image)
                return oriented.convert("RGBA" if "A" in oriented.getbands()
                                        or "transparency" in oriented.info else "RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as exc:
        raise ValueError("This image is invalid or too large to process") from exc


def storage_path(key: str) -> Path:
    root = Path(settings.image_storage_dir).resolve()
    path = (root / key).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Invalid image storage key")
    return path


def save_reference(raw: bytes) -> dict:
    image = decode_image(raw)
    identifier = uuid4().hex
    original_key = f"{identifier}/original"
    prepared_key = f"{identifier}/prepared.png"
    original = storage_path(original_key)
    original.parent.mkdir(parents=True, exist_ok=True)
    original.write_bytes(raw)
    original_width, original_height = image.size
    image.thumbnail((WORKING_EDGE, WORKING_EDGE), Image.Resampling.LANCZOS)
    # Saving a fresh pixel image strips EXIF and other source metadata.
    clean = Image.new(image.mode, image.size)
    clean.paste(image)
    clean.save(storage_path(prepared_key), "PNG")
    return {"original_key": original_key, "prepared_key": prepared_key,
            "width": image.width, "height": image.height,
            "original_width": original_width, "original_height": original_height}


def as_data_url(key: str) -> str:
    return "data:image/png;base64," + base64.b64encode(storage_path(key).read_bytes()).decode("ascii")


def save_generated(raw: bytes) -> tuple[str, int, int]:
    if len(raw) > MAX_OUTPUT_BYTES:
        raise ValueError("Generated image exceeded the output size limit")
    image = decode_image(raw)
    key = f"generated/{uuid4().hex}.png"
    path = storage_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    clean = Image.new(image.mode, image.size)
    clean.paste(image)
    clean.save(path, "PNG")
    return key, image.width, image.height

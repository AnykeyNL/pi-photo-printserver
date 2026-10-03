from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps

from app.config import MEDIA_SIZES

# Must match lp -o Resolution=334dpi (Gutenprint internal raster for UP-DR200).
CUPS_DPI = 334

_MEDIA_INCHES: dict[str, tuple[float, float]] = {
    "4x6": (4.0, 6.0),
    "5x7": (5.0, 7.0),
    "6x8": (6.0, 8.0),
}


def _clamp_crop(
    img_w: int,
    img_h: int,
    x: int,
    y: int,
    width: int,
    height: int,
) -> tuple[int, int, int, int]:
    width = max(1, min(width, img_w))
    height = max(1, min(height, img_h))
    x = max(0, min(x, img_w - width))
    y = max(0, min(y, img_h - height))
    return x, y, width, height


def _compose_dimensions(media: str, orientation: str) -> tuple[int, int]:
    """Pixel size for crop/fit (matches UI preview aspect)."""
    base_w, base_h = MEDIA_SIZES[media]
    if orientation == "landscape":
        return base_h, base_w
    return base_w, base_h


def _cups_compose_dimensions(media: str, orientation: str) -> tuple[int, int]:
    """Print raster size at CUPS_DPI (landscape = wide×tall, no extra rotation)."""
    w_in, h_in = _MEDIA_INCHES[media]
    if orientation == "landscape":
        w_in, h_in = h_in, w_in
    return round(w_in * CUPS_DPI), round(h_in * CUPS_DPI)


def prepare_print_image(
    source: Path,
    dest: Path,
    media: str,
    fit: str = "cover",
    orientation: str = "portrait",
    crop: tuple[int, int, int, int] | None = None,
    quality: int = 95,
    cups_output: bool = False,
) -> tuple[int, int]:
    if media not in MEDIA_SIZES:
        raise ValueError(f"Unknown media: {media}")

    if cups_output:
        compose_w, compose_h = _cups_compose_dimensions(media, orientation)
    else:
        compose_w, compose_h = _compose_dimensions(media, orientation)
    if fit not in ("cover", "contain", "manual"):
        raise ValueError(f"Unknown fit mode: {fit}")

    with Image.open(source) as img:
        img = ImageOps.exif_transpose(img)
        img = img.convert("RGB")
        src_w, src_h = img.size

        if crop is not None:
            x, y, cw, ch = crop
            x, y, cw, ch = _clamp_crop(src_w, src_h, x, y, cw, ch)
            canvas = img.crop((x, y, x + cw, y + ch))
            canvas = canvas.resize((compose_w, compose_h), Image.Resampling.LANCZOS)
        elif fit == "contain":
            scale = min(compose_w / src_w, compose_h / src_h)
            new_w = max(1, int(round(src_w * scale)))
            new_h = max(1, int(round(src_h * scale)))
            resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
            canvas = Image.new("RGB", (compose_w, compose_h), (255, 255, 255))
            paste_x = (compose_w - new_w) // 2
            paste_y = (compose_h - new_h) // 2
            canvas.paste(resized, (paste_x, paste_y))
        else:
            scale = max(compose_w / src_w, compose_h / src_h)
            new_w = max(1, int(round(src_w * scale)))
            new_h = max(1, int(round(src_h * scale)))
            resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
            left = (new_w - compose_w) // 2
            top = (new_h - compose_h) // 2
            canvas = resized.crop((left, top, left + compose_w, top + compose_h))

        dest.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(dest, format="JPEG", quality=quality, optimize=True)
        return canvas.size[0], canvas.size[1]


def image_dimensions(path: Path) -> tuple[int, int]:
    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img)
        return img.size


def sniff_image(data: bytes) -> bool:
    try:
        with Image.open(BytesIO(data)) as img:
            img.verify()
        return True
    except Exception:
        return False

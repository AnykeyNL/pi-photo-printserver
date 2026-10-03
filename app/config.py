import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

HOST = os.environ.get("PRINT_SERVER_HOST", "0.0.0.0")
PORT = int(os.environ.get("PRINT_SERVER_PORT", "8080"))

PRINTER_NAME = os.environ.get("PRINT_SERVER_PRINTER", "UP-DR200")

_default_upload = BASE_DIR / "data" / "uploads"
UPLOAD_DIR = Path(os.environ.get("PRINT_SERVER_UPLOAD_DIR", str(_default_upload)))

_default_history = BASE_DIR / "data" / "history.json"
HISTORY_PATH = Path(os.environ.get("PRINT_SERVER_HISTORY_PATH", str(_default_history)))
HISTORY_MAX_ITEMS = int(os.environ.get("PRINT_SERVER_HISTORY_MAX", "500"))

_default_print_log = BASE_DIR / "data" / "print.log.jsonl"
PRINT_LOG_PATH = Path(os.environ.get("PRINT_SERVER_PRINT_LOG", str(_default_print_log)))

MAX_UPLOAD_BYTES = int(os.environ.get("PRINT_SERVER_MAX_BYTES", str(25 * 1024 * 1024)))

ALLOWED_CONTENT_TYPES = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/webp",
    }
)

# Print area at 300 DPI (must match loaded ribbon/paper).
MEDIA_SIZES: dict[str, tuple[int, int]] = {
    "4x6": (1200, 1800),
    "5x7": (1500, 2100),
    "6x8": (1800, 2400),
}

DEFAULT_MEDIA = "4x6"


def target_dimensions(media: str, orientation: str = "portrait") -> tuple[int, int]:
    if media not in MEDIA_SIZES:
        raise ValueError(f"Unknown media: {media}")
    width, height = MEDIA_SIZES[media]
    if orientation == "landscape":
        return height, width
    if orientation == "portrait":
        return width, height
    raise ValueError(f"Unknown orientation: {orientation}")

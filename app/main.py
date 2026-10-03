import uuid
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, model_validator
from starlette.requests import Request

from app import imaging
from app.config import (
    ALLOWED_CONTENT_TYPES,
    BASE_DIR,
    DEFAULT_MEDIA,
    HISTORY_MAX_ITEMS,
    HISTORY_PATH,
    MAX_UPLOAD_BYTES,
    MEDIA_SIZES,
    UPLOAD_DIR,
    target_dimensions,
)
from app.history import HistoryStore
from app.paths import preview_path as stored_preview_path
from app.paths import resolve_source as stored_resolve_source
from app.paths import validate_file_id as stored_validate_file_id
from app.print_log import get_print_logger
from app.print_queue import print_queue
from app.printer import job_status, printer_status

history_store = HistoryStore(HISTORY_PATH, max_items=HISTORY_MAX_ITEMS)

app = FastAPI(title="Print Server", version="1.0.0")

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
static_dir = BASE_DIR / "static"
if static_dir.is_dir():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


class CropBox(BaseModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class PrintRequest(BaseModel):
    file_id: str
    copies: int = Field(default=1, ge=1, le=99)
    fit: str = Field(default="cover")
    media: str = Field(default=DEFAULT_MEDIA)
    orientation: Literal["portrait", "landscape"] = "portrait"
    crop: CropBox | None = None

    @model_validator(mode="after")
    def manual_requires_crop(self):
        if self.fit == "manual" and self.crop is None:
            raise ValueError("manual fit requires crop coordinates")
        return self


class PrepareRequest(BaseModel):
    file_id: str
    media: str = Field(default=DEFAULT_MEDIA)
    fit: str = Field(default="cover")
    orientation: Literal["portrait", "landscape"] = "portrait"
    crop: CropBox | None = None

    @model_validator(mode="after")
    def manual_requires_crop(self):
        if self.fit == "manual" and self.crop is None:
            raise ValueError("manual fit requires crop coordinates")
        return self


def _record_print_history(file_id: str, settings: dict, job_id: str | None) -> None:
    history_store.record_print(file_id=file_id, settings=settings, job_id=job_id)


@app.on_event("startup")
def ensure_upload_dir() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    print_queue._on_print_success = _record_print_history
    print_queue.start()
    get_print_logger().log("server.start", "Print server started", level="info")


@app.get("/")
def index(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "media_sizes": list(MEDIA_SIZES.keys()),
            "default_media": DEFAULT_MEDIA,
        },
    )


def _validate_file_id(file_id: str) -> None:
    try:
        stored_validate_file_id(file_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _preview_path(file_id: str) -> Path:
    return stored_preview_path(UPLOAD_DIR, file_id)


def _resolve_source(file_id: str) -> Path:
    try:
        return stored_resolve_source(UPLOAD_DIR, file_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _crop_tuple(crop: CropBox | None) -> tuple[int, int, int, int] | None:
    if crop is None:
        return None
    return crop.x, crop.y, crop.width, crop.height


def _settings_from_request(body: PrintRequest | PrepareRequest) -> dict:
    settings: dict = {
        "media": body.media,
        "orientation": body.orientation,
        "fit": body.fit,
    }
    if isinstance(body, PrintRequest):
        settings["copies"] = body.copies
    if body.crop is not None:
        settings["crop"] = body.crop.model_dump()
    else:
        settings["crop"] = None
    return settings


def _history_item_response(item: dict) -> dict:
    file_id = item["file_id"]
    preview = _preview_path(file_id)
    thumb_url = f"/api/files/{file_id}"
    if not preview.is_file():
        thumb_url = f"/api/files/{file_id}/source"
    return {
        **item,
        "preview_url": f"/api/files/{file_id}",
        "source_url": f"/api/files/{file_id}/source",
        "thumb_url": thumb_url,
    }


def _prepare_for_printer(
    file_id: str,
    media: str,
    fit: str,
    orientation: str = "portrait",
    crop: CropBox | None = None,
) -> tuple[int, int]:
    if media not in MEDIA_SIZES:
        raise HTTPException(status_code=400, detail=f"Unknown media {media!r}")
    if fit not in ("cover", "contain", "manual"):
        raise HTTPException(status_code=400, detail="fit must be cover, contain, or manual")
    if orientation not in ("portrait", "landscape"):
        raise HTTPException(status_code=400, detail="orientation must be portrait or landscape")
    if fit == "manual" and crop is None:
        raise HTTPException(status_code=400, detail="manual fit requires crop")

    source = _resolve_source(file_id)
    preview = _preview_path(file_id)
    try:
        return imaging.prepare_print_image(
            source=source,
            dest=preview,
            media=media,
            fit=fit,
            orientation=orientation,
            crop=_crop_tuple(crop),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Image processing failed: {exc}") from exc


@app.post("/api/upload")
async def upload_image(
    file: UploadFile = File(...),
    media: str = Form(default=DEFAULT_MEDIA),
    fit: str = Form(default="cover"),
    orientation: str = Form(default="portrait"),
):
    content_type = (file.content_type or "").split(";")[0].strip().lower()
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported type {content_type!r}. Use JPEG, PNG, or WebP.",
        )

    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large")

    if not imaging.sniff_image(data):
        raise HTTPException(status_code=400, detail="Invalid image data")

    file_id = uuid.uuid4().hex
    ext = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
    }.get(content_type, ".bin")
    source_path = UPLOAD_DIR / f"{file_id}-source{ext}"
    source_path.write_bytes(data)

    try:
        orig_w, orig_h = imaging.image_dimensions(source_path)
    except Exception as exc:
        source_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"Could not read image: {exc}") from exc

    tw, th = target_dimensions(media, orientation)
    print_w, print_h = (tw, th)
    if fit != "manual":
        print_w, print_h = _prepare_for_printer(file_id, media, fit, orientation, None)

    upload_settings = {
        "media": media,
        "orientation": orientation,
        "fit": fit,
        "copies": 1,
        "crop": None,
    }
    history_store.add_upload(
        file_id=file_id,
        filename=file.filename,
        original_width=orig_w,
        original_height=orig_h,
        settings=upload_settings,
    )

    return {
        "file_id": file_id,
        "filename": file.filename,
        "original_width": orig_w,
        "original_height": orig_h,
        "width": print_w,
        "height": print_h,
        "media": media,
        "fit": fit,
        "orientation": orientation,
        "preview_url": f"/api/files/{file_id}",
        "source_url": f"/api/files/{file_id}/source",
        "media_sizes": MEDIA_SIZES,
    }


@app.post("/api/prepare")
def prepare_preview(body: PrepareRequest):
    print_w, print_h = _prepare_for_printer(
        body.file_id,
        body.media,
        body.fit,
        body.orientation,
        body.crop,
    )
    return {
        "file_id": body.file_id,
        "width": print_w,
        "height": print_h,
        "media": body.media,
        "fit": body.fit,
        "orientation": body.orientation,
        "preview_url": f"/api/files/{body.file_id}",
    }


@app.get("/api/files/{file_id}/source")
def get_source(file_id: str):
    return FileResponse(_resolve_source(file_id))


@app.get("/api/files/{file_id}")
def get_upload(file_id: str):
    _validate_file_id(file_id)
    preview = _preview_path(file_id)
    if preview.is_file():
        return FileResponse(preview, media_type="image/jpeg")
    return FileResponse(_resolve_source(file_id))


@app.post("/api/print")
def print_image(body: PrintRequest):
    log = get_print_logger()
    if body.media not in MEDIA_SIZES:
        log.log(
            "print.rejected",
            f"Unknown media {body.media!r}",
            level="warn",
            file_id=body.file_id,
        )
        raise HTTPException(status_code=400, detail=f"Unknown media {body.media!r}")
    if body.fit not in ("cover", "contain", "manual"):
        log.log(
            "print.rejected",
            f"Invalid fit {body.fit!r}",
            level="warn",
            file_id=body.file_id,
        )
        raise HTTPException(status_code=400, detail="fit must be cover, contain, or manual")
    try:
        _resolve_source(body.file_id)
    except HTTPException as exc:
        log.log(
            "print.rejected",
            str(exc.detail),
            level="warn",
            file_id=body.file_id,
        )
        raise exc

    hist = history_store.get(body.file_id)
    filename = (hist or {}).get("filename") or "Photo"
    settings = _settings_from_request(body)
    log.log(
        "print.request",
        f"Print requested for {filename!r}",
        level="info",
        file_id=body.file_id,
        filename=filename,
        settings=settings,
    )
    queue_id = print_queue.enqueue(
        file_id=body.file_id,
        filename=filename,
        settings=settings,
    )

    return {
        "ok": True,
        "queue_id": queue_id,
        "status": "queued",
        "message": "Print job queued. You can switch photos while it runs.",
    }


@app.get("/api/logs")
def list_print_logs(limit: int = 200, queue_id: str | None = None, level: str | None = None):
    limit = max(1, min(limit, 2000))
    entries = get_print_logger().list_entries(limit=limit, level=level, queue_id=queue_id)
    return {"entries": entries, "count": len(entries)}


@app.delete("/api/logs")
def clear_print_logs():
    get_print_logger().clear()
    get_print_logger().log("logs.cleared", "Print log cleared from UI", level="info")
    return {"ok": True}


@app.get("/api/queue")
def list_print_queue(limit: int = 50):
    limit = max(1, min(limit, 200))
    return {"jobs": print_queue.list_jobs(limit=limit)}


@app.get("/api/queue/{queue_id}")
def get_print_queue_item(queue_id: str):
    job = print_queue.get_job(queue_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Queue job not found")
    return job


@app.get("/api/history")
def list_history(limit: int = 100):
    limit = max(1, min(limit, 500))
    items = [_history_item_response(item) for item in history_store.list_items(limit=limit)]
    return {"items": items}


@app.get("/api/history/{file_id}")
def get_history_item(file_id: str):
    _validate_file_id(file_id)
    item = history_store.get(file_id)
    if item is None:
        raise HTTPException(status_code=404, detail="History entry not found")
    try:
        source = _resolve_source(file_id)
        w, h = imaging.image_dimensions(source)
        item["original_width"] = w
        item["original_height"] = h
    except HTTPException:
        pass
    return _history_item_response(item)


@app.delete("/api/history/{file_id}")
def delete_history_item(file_id: str):
    _validate_file_id(file_id)
    if not history_store.remove(file_id):
        raise HTTPException(status_code=404, detail="History entry not found")
    return {"ok": True, "file_id": file_id}


@app.delete("/api/history")
def clear_history():
    removed = history_store.clear_all()
    return {"ok": True, "removed": removed}


@app.get("/api/status")
def status():
    return printer_status()


@app.get("/api/jobs/{job_id}")
def jobs(job_id: str):
    if not job_id.isdigit():
        raise HTTPException(status_code=400, detail="job_id must be numeric")
    info = job_status(job_id)
    if not info.get("ok"):
        raise HTTPException(status_code=404, detail=info.get("error", "Job not found"))
    return info


def main() -> None:
    import uvicorn

    from app.config import HOST, PORT

    uvicorn.run("app.main:app", host=HOST, port=PORT, reload=False)


if __name__ == "__main__":
    main()

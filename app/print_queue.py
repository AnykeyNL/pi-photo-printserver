import queue
import threading
import traceback
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from app import imaging
from app.config import UPLOAD_DIR
from app.cups_options import lp_options_for_job
from app.paths import job_print_path, resolve_source
from app.print_log import get_print_logger
from app.printer import job_status, submit_print, wait_for_cups_job


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class PrintQueueService:
    def __init__(
        self,
        on_print_success: Callable[[str, dict, str | None], None] | None = None,
        max_items: int = 200,
    ) -> None:
        self._work: queue.Queue[str] = queue.Queue()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()
        self._max_items = max_items
        self._on_print_success = on_print_success
        self._worker = threading.Thread(target=self._run_worker, name="print-queue", daemon=True)
        self._started = False
        self._log = get_print_logger()

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        (UPLOAD_DIR / "jobs").mkdir(parents=True, exist_ok=True)
        self._worker.start()
        self._log.log("queue.worker", "Background print worker started", level="info")

    def enqueue(self, file_id: str, filename: str, settings: dict[str, Any]) -> str:
        queue_id = uuid.uuid4().hex
        job = {
            "queue_id": queue_id,
            "file_id": file_id,
            "filename": filename or "Photo",
            "status": "queued",
            "created_at": _utc_now_iso(),
            "updated_at": _utc_now_iso(),
            "settings": settings,
            "cups_job_id": None,
            "error": None,
            "thumb_url": f"/api/files/{file_id}",
        }
        with self._lock:
            self._jobs[queue_id] = job
            self._order.insert(0, queue_id)
            if len(self._order) > self._max_items:
                for old_id in self._order[self._max_items :]:
                    self._jobs.pop(old_id, None)
                self._order = self._order[: self._max_items]
        self._work.put(queue_id)
        self._log.log(
            "print.enqueued",
            f"Queued print job for {filename!r}",
            level="info",
            queue_id=queue_id,
            file_id=file_id,
            settings=settings,
        )
        return queue_id

    def _touch(self, job: dict[str, Any], status: str) -> None:
        job["status"] = status
        job["updated_at"] = _utc_now_iso()
        self._log.log(
            "print.status",
            f"Job status → {status}",
            level="info",
            queue_id=job.get("queue_id"),
            file_id=job.get("file_id"),
            status=status,
        )

    def _run_worker(self) -> None:
        while True:
            queue_id = self._work.get()
            try:
                self._process_job(queue_id)
            except Exception as exc:
                self._log.log(
                    "print.worker.error",
                    "Unhandled worker exception",
                    level="error",
                    queue_id=queue_id,
                    error=str(exc),
                    traceback=traceback.format_exc(),
                )
            finally:
                self._work.task_done()

    def _process_job(self, queue_id: str) -> None:
        with self._lock:
            job = self._jobs.get(queue_id)
        if job is None:
            self._log.log(
                "print.worker.missing",
                "Job id not found in queue store",
                level="error",
                queue_id=queue_id,
            )
            return

        settings = job["settings"]
        self._log.log(
            "print.worker.start",
            f"Processing job for {job.get('filename')!r}",
            level="info",
            queue_id=queue_id,
            file_id=job["file_id"],
            settings=settings,
        )

        try:
            self._touch(job, "preparing")
            source = resolve_source(UPLOAD_DIR, job["file_id"])
            dest = job_print_path(UPLOAD_DIR, queue_id)
            dest.parent.mkdir(parents=True, exist_ok=True)

            self._log.log(
                "print.prepare.start",
                "Preparing print JPEG",
                level="info",
                queue_id=queue_id,
                file_id=job["file_id"],
                source=str(source),
                dest=str(dest),
                source_bytes=source.stat().st_size if source.is_file() else None,
            )

            crop = settings.get("crop")
            crop_tuple = None
            if crop:
                crop_tuple = (crop["x"], crop["y"], crop["width"], crop["height"])

            tw, th = imaging.prepare_print_image(
                source=source,
                dest=dest,
                media=settings["media"],
                fit=settings["fit"],
                orientation=settings["orientation"],
                crop=crop_tuple,
                cups_output=True,
            )

            dest_size = dest.stat().st_size if dest.is_file() else 0
            self._log.log(
                "print.prepare.done",
                f"Prepared {tw}×{th}px JPEG ({dest_size} bytes)",
                level="info",
                queue_id=queue_id,
                file_id=job["file_id"],
                width=tw,
                height=th,
                dest_bytes=dest_size,
                dest=str(dest),
            )

            self._touch(job, "submitting")
            copies = int(settings.get("copies") or 1)
            media = settings.get("media", "4x6")
            orientation = settings.get("orientation", "portrait")
            lp_opts = lp_options_for_job(media, orientation)
            self._log.log(
                "print.lp.start",
                f"Submitting to CUPS ({copies} cop{'y' if copies == 1 else 'ies'})",
                level="info",
                queue_id=queue_id,
                file_id=job["file_id"],
                copies=copies,
                lp_options=lp_opts,
            )

            result = submit_print(
                dest,
                copies=copies,
                title=f"web-{queue_id[:8]}",
                media=media,
                orientation=orientation,
            )

            self._log.log(
                "print.lp.result",
                result.get("message") or ("OK" if result.get("ok") else "lp failed"),
                level="info" if result.get("ok") else "error",
                queue_id=queue_id,
                file_id=job["file_id"],
                ok=result.get("ok"),
                cups_job_id=result.get("job_id"),
                cups_queue_name=result.get("queue_name"),
                lp_message=result.get("message"),
                lp_error=result.get("error"),
                lp_options=result.get("lp_options"),
            )

            if not result.get("ok"):
                job["error"] = result.get("error", "Print failed")
                self._touch(job, "failed")
                self._log.log(
                    "print.failed",
                    job["error"],
                    level="error",
                    queue_id=queue_id,
                    file_id=job["file_id"],
                )
                return

            cups_job_id = result.get("job_id")
            job["cups_job_id"] = cups_job_id
            job["cups_queue_name"] = result.get("queue_name")
            self._touch(job, "printing")

            if not cups_job_id:
                job["error"] = "Could not parse CUPS job id from lp output"
                self._touch(job, "failed")
                self._log.log(
                    "print.failed",
                    job["error"],
                    level="error",
                    queue_id=queue_id,
                    lp_message=result.get("message"),
                )
                return

            self._log.log(
                "print.cups.wait",
                f"Waiting for CUPS job {cups_job_id}",
                level="info",
                queue_id=queue_id,
                cups_job_id=cups_job_id,
            )
            cups_final = wait_for_cups_job(str(cups_job_id))
            self._log.log(
                "print.cups.final",
                cups_final.get("detail") or cups_final.get("state") or "done",
                level="info" if cups_final.get("state") == "completed" else "error",
                queue_id=queue_id,
                cups_job_id=cups_job_id,
                cups_state=cups_final.get("state"),
                cups_detail=cups_final.get("detail"),
                cups_error=cups_final.get("error"),
            )

            final_state = cups_final.get("state")
            if final_state == "completed":
                self._touch(job, "completed")
                if self._on_print_success:
                    self._on_print_success(job["file_id"], settings, cups_job_id)
                self._log.log(
                    "print.completed",
                    f"Printed successfully (CUPS #{cups_job_id})",
                    level="info",
                    queue_id=queue_id,
                    file_id=job["file_id"],
                    cups_job_id=cups_job_id,
                )
            else:
                job["error"] = (
                    cups_final.get("detail")
                    or cups_final.get("error")
                    or f"CUPS job ended with state: {final_state}"
                )
                self._touch(job, "failed")
                self._log.log(
                    "print.failed",
                    job["error"],
                    level="error",
                    queue_id=queue_id,
                    file_id=job["file_id"],
                    cups_job_id=cups_job_id,
                    cups_state=final_state,
                )
        except Exception as exc:
            job["error"] = str(exc)
            self._touch(job, "failed")
            self._log.log(
                "print.failed",
                str(exc),
                level="error",
                queue_id=queue_id,
                file_id=job.get("file_id"),
                traceback=traceback.format_exc(),
            )

    def _sync_cups_state(self, job: dict[str, Any]) -> None:
        cups_id = job.get("cups_job_id")
        if not cups_id:
            return
        info = job_status(str(cups_id))
        if not info.get("ok"):
            self._log.log(
                "print.cups.poll",
                info.get("error") or "Could not read CUPS job status",
                level="warn",
                queue_id=job.get("queue_id"),
                cups_job_id=cups_id,
            )
            return
        state = info.get("state")
        prev = job.get("_last_cups_state")
        if state != prev:
            job["_last_cups_state"] = state
            self._log.log(
                "print.cups.state",
                info.get("detail") or f"CUPS job state: {state}",
                level="info",
                queue_id=job.get("queue_id"),
                cups_job_id=cups_id,
                cups_state=state,
            )
        if state == "completed":
            self._touch(job, "completed")
        elif state == "printing":
            self._touch(job, "printing")

    def refresh_cups_states(self) -> None:
        with self._lock:
            jobs = list(self._jobs.values())
        for job in jobs:
            if job.get("status") in ("printing", "submitting") and job.get("cups_job_id"):
                self._sync_cups_state(job)

    def list_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        self.refresh_cups_states()
        with self._lock:
            out = []
            for qid in self._order[:limit]:
                job = self._jobs.get(qid)
                if job:
                    out.append(dict(job))
            return out

    def get_job(self, queue_id: str) -> dict[str, Any] | None:
        self.refresh_cups_states()
        with self._lock:
            job = self._jobs.get(queue_id)
            return dict(job) if job else None


print_queue = PrintQueueService()

import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from app.config import PRINTER_NAME
from app.cups_options import lp_options_for_job

# e.g. "request id is UP-DR200-9 (1 file(s))"
_LP_REQUEST_RE = re.compile(
    r"request id is (?P<dest>\S+?)-(?P<id>\d+)\b",
    re.I,
)


def _run(args: list[str], timeout: float = 120.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def parse_lp_job_id(combined: str) -> tuple[str | None, str | None]:
    """Return (job_id, full_queue_name) from lp stdout/stderr."""
    match = _LP_REQUEST_RE.search(combined)
    if not match:
        return None, None
    dest = match.group("dest")
    job_id = match.group("id")
    return job_id, f"{dest}-{job_id}"


def cups_available() -> bool:
    return shutil.which("lp") is not None and shutil.which("lpstat") is not None


def submit_print(
    path: Path,
    copies: int = 1,
    title: str = "web-upload",
    media: str = "4x6",
    orientation: str = "portrait",
) -> dict:
    if not cups_available():
        return {"ok": False, "error": "CUPS client (lp) not installed on this host"}

    copies = max(1, min(copies, 99))
    cmd = [
        "lp",
        "-d",
        PRINTER_NAME,
        "-n",
        str(copies),
        "-t",
        title,
    ]
    for opt in lp_options_for_job(media, orientation):
        cmd.extend(["-o", opt])
    cmd.append(str(path))

    result = _run(cmd)
    combined = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0:
        return {
            "ok": False,
            "error": combined.strip() or f"lp exited {result.returncode}",
            "message": combined.strip(),
            "returncode": result.returncode,
        }

    job_id, queue_name = parse_lp_job_id(combined)
    return {
        "ok": True,
        "job_id": job_id,
        "queue_name": queue_name,
        "message": combined.strip(),
        "returncode": 0,
        "lp_options": lp_options_for_job(media, orientation),
    }


def printer_status() -> dict:
    if not cups_available():
        return {
            "cups_available": False,
            "printer": PRINTER_NAME,
            "state": "unknown",
            "detail": "lpstat not available",
        }

    enabled = _run(["lpstat", "-p", PRINTER_NAME])
    detail = (enabled.stdout or "") + (enabled.stderr or "")
    state = "unknown"
    if enabled.returncode == 0:
        line = (enabled.stdout or "").splitlines()[0] if enabled.stdout else ""
        if "idle" in line.lower():
            state = "idle"
        elif "printing" in line.lower():
            state = "printing"
        elif "disabled" in line.lower():
            state = "disabled"
        else:
            state = "ready" if line else "unknown"
    else:
        state = "missing"

    backend_status = get_cached_backend_status()
    return {
        "cups_available": True,
        "printer": PRINTER_NAME,
        "state": state,
        "detail": detail.strip(),
        "backend": backend_status,
    }


_BACKEND_CACHE_TTL = 20.0
_BACKEND_PROBE_TIMEOUT = 10.0
_backend_lock = threading.Lock()
_backend_cache: dict[str, Any] = {"at": 0.0, "data": None}


def get_cached_backend_status() -> dict | None:
    """USB backend probe is slow; cache so /api/status stays responsive under polling."""
    now = time.time()
    with _backend_lock:
        if now - float(_backend_cache["at"]) < _BACKEND_CACHE_TTL:
            return _backend_cache["data"]
        data = query_backend_status()
        if data is not None:
            _backend_cache["data"] = data
            _backend_cache["at"] = now
        return data


def _find_gutenprint_backend() -> Path | None:
    for name in ("gutenprint53+usb", "gutenprint52+usb"):
        path = Path("/usr/lib/cups/backend") / name
        if path.is_file():
            return path
    return None


def query_backend_status() -> dict | None:
    backend = _find_gutenprint_backend()
    if backend is None:
        return None

    device = _run(["lpstat", "-v", PRINTER_NAME])
    if device.returncode != 0:
        return {"error": (device.stderr or device.stdout or "").strip()}

    uri = None
    for line in (device.stdout or "").splitlines():
        if "device for" in line.lower() and "://" in line:
            parts = line.split(":", 1)
            if len(parts) == 2:
                uri = parts[1].strip()
                break
    if not uri:
        return {"error": "Could not read device URI from lpstat -v"}

    result = _run(
        ["env", "BACKEND=sonyupd", str(backend), "-s", uri],
        timeout=_BACKEND_PROBE_TIMEOUT,
    )
    text = (result.stdout or "") + (result.stderr or "")
    return {
        "uri": uri,
        "exit_code": result.returncode,
        "text": text.strip(),
    }


def _parse_completed_job_block(block: str) -> dict[str, str]:
    info: dict[str, str] = {"summary": block.splitlines()[0].strip() if block else ""}
    for line in block.splitlines()[1:]:
        line = line.strip()
        if line.lower().startswith("status:"):
            info["status_message"] = line.split(":", 1)[1].strip()
        elif line.lower().startswith("alerts:"):
            info["alerts"] = line.split(":", 1)[1].strip()
    return info


_JOB_HEADER_RE = re.compile(rf"^{re.escape(PRINTER_NAME)}-\d+\s")


def _completed_job_block(job_id: str) -> dict[str, str] | None:
    result = _run(["lpstat", "-W", "completed", "-l", "-o"])
    if result.returncode != 0:
        return None
    needle = f"{PRINTER_NAME}-{job_id}"
    lines = (result.stdout or "").splitlines()
    for i, line in enumerate(lines):
        if line.startswith(needle):
            block_lines = [line]
            for follow in lines[i + 1 :]:
                if _JOB_HEADER_RE.match(follow):
                    break
                block_lines.append(follow)
            return _parse_completed_job_block("\n".join(block_lines))
    return None


def _job_line_for_id(job_id: str, which: str) -> str | None:
    if which == "completed":
        result = _run(["lpstat", "-W", "completed", "-o"])
    else:
        result = _run(["lpstat", "-o"])
    if result.returncode != 0:
        return None
    needle = f"{PRINTER_NAME}-{job_id}"
    for line in (result.stdout or "").splitlines():
        if line.startswith(needle):
            return line.strip()
    return None


def _completed_job_failed(info: dict[str, str]) -> bool:
    text = " ".join(
        [
            info.get("status_message", ""),
            info.get("alerts", ""),
            info.get("summary", ""),
        ]
    ).lower()
    markers = (
        "canceled-at-device",
        "cancel",
        "abort",
        "data length mismatch",
        "error",
    )
    return any(m in text for m in markers)


def job_status(job_id: str) -> dict:
    if not cups_available():
        return {"ok": False, "error": "CUPS client not available"}

    active_line = _job_line_for_id(job_id, "active")
    if active_line:
        lower = active_line.lower()
        state = "queued"
        if "processing" in lower or "printing" in lower:
            state = "printing"
        return {"ok": True, "job_id": job_id, "state": state, "detail": active_line}

    completed = _completed_job_block(job_id)
    if completed:
        detail = completed.get("status_message") or completed.get("summary", "")
        if _completed_job_failed(completed):
            return {
                "ok": True,
                "job_id": job_id,
                "state": "cancelled",
                "detail": detail,
                "alerts": completed.get("alerts"),
            }
        return {
            "ok": True,
            "job_id": job_id,
            "state": "completed",
            "detail": detail,
            "alerts": completed.get("alerts"),
        }

    return {
        "ok": False,
        "job_id": job_id,
        "state": "unknown",
        "error": f"Job {PRINTER_NAME}-{job_id} not found in active or completed queue",
    }


def wait_for_cups_job(
    job_id: str,
    poll_interval: float = 2.0,
    timeout: float = 600.0,
) -> dict:
    """Poll until CUPS job completes, is cancelled, or timeout."""
    deadline = time.time() + timeout
    last: dict = {"ok": False, "state": "unknown"}
    while time.time() < deadline:
        last = job_status(job_id)
        if not last.get("ok"):
            time.sleep(poll_interval)
            continue
        state = last.get("state")
        if state in ("completed", "cancelled"):
            return last
        time.sleep(poll_interval)
    last["state"] = "timeout"
    last["error"] = last.get("error") or f"CUPS job {job_id} did not finish within {timeout}s"
    return last

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class PrintEventLog:
    """Persistent print troubleshooting log (JSON lines on disk + in-memory tail)."""

    def __init__(self, path: Path, max_memory: int = 3000) -> None:
        self.path = path
        self.max_memory = max_memory
        self._entries: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._load_tail()

    def _load_tail(self) -> None:
        if not self.path.is_file():
            return
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
            for line in lines[-self.max_memory :]:
                line = line.strip()
                if not line:
                    continue
                self._entries.append(json.loads(line))
        except (OSError, json.JSONDecodeError):
            pass

    def log(
        self,
        event: str,
        message: str,
        level: str = "info",
        **fields: Any,
    ) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "ts": _utc_now_iso(),
            "level": level,
            "event": event,
            "message": message,
        }
        for key, value in fields.items():
            if value is not None:
                entry[key] = value

        with self._lock:
            self._entries.append(entry)
            if len(self._entries) > self.max_memory:
                self._entries = self._entries[-self.max_memory :]
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            except OSError:
                pass

        return entry

    def list_entries(
        self,
        limit: int = 200,
        level: str | None = None,
        queue_id: str | None = None,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 2000))
        with self._lock:
            items = list(self._entries)
        if level:
            items = [e for e in items if e.get("level") == level]
        if queue_id:
            items = [e for e in items if e.get("queue_id") == queue_id]
        return items[-limit:]

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            try:
                if self.path.is_file():
                    self.path.unlink()
            except OSError:
                pass


print_logger: PrintEventLog | None = None


def get_print_logger() -> PrintEventLog:
    global print_logger
    if print_logger is None:
        from app.config import PRINT_LOG_PATH

        print_logger = PrintEventLog(PRINT_LOG_PATH)
    return print_logger

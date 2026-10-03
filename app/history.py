import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_lock = threading.Lock()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class HistoryStore:
    def __init__(self, path: Path, max_items: int = 500) -> None:
        self.path = path
        self.max_items = max_items

    def _load_unlocked(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"items": []}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _save_unlocked(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _find_index(self, items: list[dict], file_id: str) -> int | None:
        for i, item in enumerate(items):
            if item.get("file_id") == file_id:
                return i
        return None

    def add_upload(
        self,
        file_id: str,
        filename: str | None,
        original_width: int,
        original_height: int,
        settings: dict[str, Any],
    ) -> dict[str, Any]:
        entry = {
            "file_id": file_id,
            "filename": filename or "image",
            "uploaded_at": _utc_now_iso(),
            "printed_at": None,
            "print_count": 0,
            "last_job_id": None,
            "original_width": original_width,
            "original_height": original_height,
            "last_settings": settings,
        }
        with _lock:
            data = self._load_unlocked()
            items = data.setdefault("items", [])
            idx = self._find_index(items, file_id)
            if idx is not None:
                items[idx] = {**items[idx], **entry}
            else:
                items.insert(0, entry)
            if len(items) > self.max_items:
                data["items"] = items[: self.max_items]
            self._save_unlocked(data)
        return entry

    def record_print(
        self,
        file_id: str,
        settings: dict[str, Any],
        job_id: str | None,
    ) -> dict[str, Any] | None:
        with _lock:
            data = self._load_unlocked()
            items = data.setdefault("items", [])
            idx = self._find_index(items, file_id)
            if idx is None:
                return None
            item = items[idx]
            item["printed_at"] = _utc_now_iso()
            item["print_count"] = int(item.get("print_count") or 0) + 1
            item["last_job_id"] = job_id
            item["last_settings"] = settings
            items.pop(idx)
            items.insert(0, item)
            self._save_unlocked(data)
            return item

    def list_items(self, limit: int = 100) -> list[dict[str, Any]]:
        with _lock:
            data = self._load_unlocked()
            items = list(data.get("items", []))

        def sort_key(row: dict) -> str:
            return row.get("printed_at") or row.get("uploaded_at") or ""

        items.sort(key=sort_key, reverse=True)
        return items[:limit]

    def get(self, file_id: str) -> dict[str, Any] | None:
        with _lock:
            data = self._load_unlocked()
            for item in data.get("items", []):
                if item.get("file_id") == file_id:
                    return dict(item)
        return None

    def remove(self, file_id: str) -> bool:
        with _lock:
            data = self._load_unlocked()
            items = data.setdefault("items", [])
            idx = self._find_index(items, file_id)
            if idx is None:
                return False
            items.pop(idx)
            self._save_unlocked(data)
            return True

    def clear_all(self) -> int:
        with _lock:
            data = self._load_unlocked()
            count = len(data.get("items", []))
            data["items"] = []
            self._save_unlocked(data)
            return count

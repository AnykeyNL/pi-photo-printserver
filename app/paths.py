from pathlib import Path


def validate_file_id(file_id: str) -> None:
    if not file_id.isalnum() or len(file_id) != 32:
        raise ValueError("Invalid file_id")


def validate_queue_id(queue_id: str) -> None:
    if not queue_id.isalnum() or len(queue_id) != 32:
        raise ValueError("Invalid queue_id")


def preview_path(upload_dir: Path, file_id: str) -> Path:
    validate_file_id(file_id)
    return upload_dir / f"{file_id}.jpg"


def job_print_path(upload_dir: Path, queue_id: str) -> Path:
    validate_queue_id(queue_id)
    return upload_dir / "jobs" / f"{queue_id}.jpg"


def resolve_source(upload_dir: Path, file_id: str) -> Path:
    validate_file_id(file_id)
    sources = list(upload_dir.glob(f"{file_id}-source.*"))
    if sources:
        return sources[0]
    legacy = [
        p
        for p in upload_dir.glob(f"{file_id}.*")
        if p.name != f"{file_id}.jpg" and not p.name.endswith("-print.jpg")
    ]
    if legacy:
        return legacy[0]
    raise FileNotFoundError(f"Upload not found: {file_id}")

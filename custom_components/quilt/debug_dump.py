from __future__ import annotations

import base64
import threading
from pathlib import Path

DEFAULT_DEBUG_MAX_FILES = 500
DEFAULT_DEBUG_MAX_BYTES = 50 * 1024 * 1024
_DEBUG_DUMP_LOCK = threading.Lock()


def write_debug_dump(
    debug_dir: Path,
    filename: str,
    payload: bytes,
    *,
    max_files: int = DEFAULT_DEBUG_MAX_FILES,
    max_bytes: int = DEFAULT_DEBUG_MAX_BYTES,
) -> None:
    """Write a debug payload and prune older dumps within fixed retention caps."""
    if not payload:
        return

    with _DEBUG_DUMP_LOCK:
        debug_dir.mkdir(parents=True, exist_ok=True)
        path = debug_dir / filename
        if path.exists():
            stem = path.stem
            suffix = path.suffix
            counter = 1
            while path.exists():
                path = debug_dir / f"{stem}.{counter}{suffix}"
                counter += 1
        path.write_text(
            base64.b64encode(payload).decode("ascii") + "\n", encoding="utf-8"
        )
        _prune_debug_dir(debug_dir, max_files=max_files, max_bytes=max_bytes)


def _prune_debug_dir(debug_dir: Path, *, max_files: int, max_bytes: int) -> None:
    dumps = sorted(
        (p for p in debug_dir.glob("*.b64") if p.is_file()),
        key=lambda p: (p.stat().st_mtime, p.name),
    )

    while len(dumps) > max_files:
        victim = dumps.pop(0)
        victim.unlink(missing_ok=True)

    total_bytes = sum(p.stat().st_size for p in dumps)
    while dumps and total_bytes > max_bytes:
        victim = dumps.pop(0)
        size = victim.stat().st_size
        victim.unlink(missing_ok=True)
        total_bytes -= size

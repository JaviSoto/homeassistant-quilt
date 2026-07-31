from __future__ import annotations

import base64
import os
import threading
import time

import custom_components.quilt.debug_dump as debug_dump
from custom_components.quilt.debug_dump import write_debug_dump


def test_debug_dump_writes_base64_payload(tmp_path) -> None:
    write_debug_dump(tmp_path, "payload.b64", b"hello")

    assert (tmp_path / "payload.b64").read_text(encoding="utf-8") == (
        base64.b64encode(b"hello").decode("ascii") + "\n"
    )


def test_debug_dump_prunes_oldest_files_by_count(tmp_path) -> None:
    base = time.time() - 10
    for index in range(5):
        path = tmp_path / f"old-{index}.b64"
        path.write_text("x\n", encoding="utf-8")
        os.utime(path, (base + index, base + index))

    write_debug_dump(tmp_path, "new.b64", b"new", max_files=3, max_bytes=10_000)

    assert sorted(p.name for p in tmp_path.glob("*.b64")) == [
        "new.b64",
        "old-3.b64",
        "old-4.b64",
    ]


def test_debug_dump_prunes_oldest_files_by_total_size(tmp_path) -> None:
    base = time.time() - 10
    for index in range(3):
        path = tmp_path / f"old-{index}.b64"
        path.write_text("x" * 10, encoding="utf-8")
        os.utime(path, (base + index, base + index))

    write_debug_dump(tmp_path, "new.b64", b"new", max_files=10, max_bytes=8)

    assert sorted(p.name for p in tmp_path.glob("*.b64")) == ["new.b64"]


def test_debug_dump_serializes_write_and_prune_transaction(
    monkeypatch, tmp_path
) -> None:  # noqa: ANN001
    original_prune = debug_dump._prune_debug_dir  # noqa: SLF001
    metrics_lock = threading.Lock()
    active_prunes = 0
    max_active_prunes = 0

    def tracked_prune(*args, **kwargs):  # noqa: ANN002, ANN003
        nonlocal active_prunes, max_active_prunes
        with metrics_lock:
            active_prunes += 1
            max_active_prunes = max(max_active_prunes, active_prunes)
        try:
            time.sleep(0.01)
            return original_prune(*args, **kwargs)
        finally:
            with metrics_lock:
                active_prunes -= 1

    monkeypatch.setattr(debug_dump, "_prune_debug_dir", tracked_prune)
    start = threading.Barrier(8)

    def write(index: int) -> None:
        start.wait()
        write_debug_dump(tmp_path, f"concurrent-{index}.b64", b"payload", max_files=100)

    threads = [threading.Thread(target=write, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert all(not thread.is_alive() for thread in threads)
    assert max_active_prunes == 1


def test_debug_dump_keeps_same_filename_payloads_under_concurrency(tmp_path) -> None:
    start = threading.Barrier(2)

    def write(payload: bytes) -> None:
        start.wait()
        write_debug_dump(tmp_path, "same-second.b64", payload, max_files=10)

    threads = [
        threading.Thread(target=write, args=(b"first",)),
        threading.Thread(target=write, args=(b"second",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert all(not thread.is_alive() for thread in threads)
    payloads = {
        base64.b64decode(path.read_text(encoding="utf-8"))
        for path in tmp_path.glob("*.b64")
    }
    assert payloads == {b"first", b"second"}

from __future__ import annotations

import base64
import os
import time

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

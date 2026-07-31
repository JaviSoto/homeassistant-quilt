from __future__ import annotations

from pathlib import Path

from custom_components.quilt.notifier import QuiltNotifier


def test_notifier_debug_dumps_are_disabled_without_directory() -> None:
    notifier = QuiltNotifier(hass=object(), api=object(), coordinator=object())  # type: ignore[arg-type]

    assert notifier._debug_dir is None  # noqa: SLF001
    notifier._debug_dump("req", b"secret")  # noqa: SLF001


def test_notifier_debug_dumps_use_opt_in_directory(tmp_path: Path) -> None:
    coordinator = type("Coordinator", (), {"name": "Quilt Office"})()
    notifier = QuiltNotifier(
        hass=object(),
        api=object(),
        coordinator=coordinator,  # type: ignore[arg-type]
        debug_dir=tmp_path,
    )

    notifier._debug_dump("req", b"payload")  # noqa: SLF001

    assert list(tmp_path.glob("*.b64"))

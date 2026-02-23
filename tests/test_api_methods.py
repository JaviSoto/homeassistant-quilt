from __future__ import annotations

import asyncio

import pytest

from custom_components.quilt.api import QuiltApi, QuiltApiConfig


def test_api_update_methods_and_publish_heartbeat_call_unary_unary(monkeypatch) -> None:  # noqa: ANN001
    calls: list[tuple[str, bytes]] = []

    async def fake_unary(self, method: str, request: bytes) -> bytes:  # noqa: ANN001
        calls.append((method, request))
        return b"ok"

    monkeypatch.setattr(QuiltApi, "_unary_unary", fake_unary)

    api = QuiltApi(QuiltApiConfig(host="example.com", id_token="tok", refresh_token="r"), aiohttp_session=None)
    asyncio.run(api.async_update_space(space_message=b"\x01"))
    asyncio.run(api.async_update_comfort_setting(comfort_setting_message=b"\x02"))
    asyncio.run(api.async_update_indoor_unit(indoor_unit_message=b"\x03"))
    asyncio.run(api.async_publish_heartbeat("sys-1"))

    methods = [m for (m, _req) in calls]
    assert any(m.endswith("/UpdateSpace") for m in methods)
    assert any(m.endswith("/UpdateComfortSetting") for m in methods)
    assert any(m.endswith("/UpdateIndoorUnit") for m in methods)
    assert any(m.endswith("/Publish") for m in methods)


def test_api_ensure_connected_raises_if_connect_does_not_init_channel(monkeypatch) -> None:  # noqa: ANN001
    api = QuiltApi(QuiltApiConfig(host="example.com", id_token="tok", refresh_token="r"), aiohttp_session=None)

    async def noop_connect():  # noqa: ANN001
        return None

    monkeypatch.setattr(api, "async_connect", noop_connect)
    with pytest.raises(RuntimeError):
        asyncio.run(api._ensure_connected())  # type: ignore[attr-defined]


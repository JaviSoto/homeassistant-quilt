from __future__ import annotations

import asyncio

from custom_components.quilt.api import QuiltApi, QuiltApiConfig


class _FakeUnaryUnary:
    def __init__(self, method: str, record):  # noqa: ANN001
        self._method = method
        self._record = record

    def __call__(self, request: bytes, metadata=None, timeout=None):  # noqa: ANN001
        self._record.append((self._method, request))
        return b"\x00"


class _FakeChannel:
    def __init__(self) -> None:
        self.calls = []

    def unary_unary(self, method: str, request_serializer=None, response_deserializer=None):  # noqa: ANN001
        return _FakeUnaryUnary(method, self.calls)

    def close(self) -> None:
        return None


def test_list_systems_and_get_hds_request_paths(monkeypatch) -> None:  # noqa: ANN001
    import custom_components.quilt.api as api_mod

    parsed = {"list": False, "hds": False}

    def fake_parse_list(raw: bytes):  # noqa: ANN001
        parsed["list"] = True
        return []

    def fake_parse_hds(raw: bytes):  # noqa: ANN001
        parsed["hds"] = True
        return type("H", (), {"system_id": "sys"})()

    monkeypatch.setattr(api_mod, "parse_list_systems_response", fake_parse_list)
    monkeypatch.setattr(api_mod, "parse_get_home_datastore_system_response", fake_parse_hds)

    api = QuiltApi(QuiltApiConfig(host="example.com", id_token="tok", refresh_token="r"), aiohttp_session=None)
    api._channel = _FakeChannel()  # type: ignore[attr-defined]
    api.token_expires_soon = lambda within_seconds=120: False  # type: ignore[assignment]

    asyncio.run(api.async_list_systems())
    assert parsed["list"] is True

    asyncio.run(api.async_get_home_datastore_system("sys-1"))
    assert parsed["hds"] is True

    methods = [m for (m, _req) in api._channel.calls]  # type: ignore[attr-defined]
    assert any(m.endswith("/ListSystems") for m in methods)
    assert any(m.endswith("/GetHomeDatastoreSystem") for m in methods)


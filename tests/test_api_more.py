from __future__ import annotations

import asyncio

import grpc
import pytest

from custom_components.quilt.api import QuiltApi, QuiltApiConfig
from custom_components.quilt.cognito import CognitoError


class _FakeChannel:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def unary_unary(
        self, method: str, request_serializer=None, response_deserializer=None
    ):  # noqa: ANN001
        def call(req, metadata=None, timeout=None):  # noqa: ANN001
            return b"\x00"

        return call


def test_api_connect_close(monkeypatch) -> None:  # noqa: ANN001
    import custom_components.quilt.api as api_mod

    ch = _FakeChannel()

    def fake_secure_channel(host, creds, options=None):  # noqa: ANN001
        assert host == "example.com"
        return ch

    monkeypatch.setattr(api_mod.grpc, "secure_channel", fake_secure_channel)

    api = QuiltApi(
        QuiltApiConfig(host="example.com", id_token="tok", refresh_token="r"),
        aiohttp_session=None,
    )
    asyncio.run(api.async_connect())
    assert api._channel is ch  # type: ignore[attr-defined]
    asyncio.run(api.async_close())
    assert ch.closed is True
    # Closing again should be a no-op.
    asyncio.run(api.async_close())


def test_api_misc_accessors_and_notifier_callables() -> None:
    class Chan(_FakeChannel):
        def stream_stream(self, *a, **k):  # noqa: ANN001
            return "ss"

        def unary_stream(self, *a, **k):  # noqa: ANN001
            return "us"

    api = QuiltApi(
        QuiltApiConfig(host="example.com", id_token="tok", refresh_token="r"),
        aiohttp_session=None,
    )
    assert api.host == "example.com"
    assert api.grpc_channel_options()
    try:
        api.notifier_stream_callable()
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected RuntimeError when channel missing")

    api._channel = Chan()  # type: ignore[attr-defined]
    assert api.notifier_stream_callable() == "ss"
    assert api.notifier_unary_stream_callable() == "us"

    api2 = QuiltApi(
        QuiltApiConfig(host="example.com", id_token="tok", refresh_token="r"),
        aiohttp_session=None,
    )
    try:
        api2.notifier_unary_stream_callable()
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected RuntimeError when channel missing")


def test_api_debug_dump_writes_file(tmp_path) -> None:
    import custom_components.quilt.api as api_mod

    api_mod.parse_list_systems_response = lambda raw: []  # type: ignore[assignment]
    api = QuiltApi(
        QuiltApiConfig(
            host="example.com",
            id_token="tok",
            refresh_token="r",
            debug_dir=str(tmp_path),
        ),
        aiohttp_session=None,
    )
    api._channel = _FakeChannel()  # type: ignore[attr-defined]
    api.token_expires_soon = lambda within_seconds=120: False  # type: ignore[assignment]
    asyncio.run(api.async_list_systems())
    assert any(p.suffix == ".b64" for p in tmp_path.iterdir())


def test_api_debug_dump_failure_does_not_change_rpc_result(
    monkeypatch, tmp_path
) -> None:  # noqa: ANN001
    import custom_components.quilt.api as api_mod

    def fail_write(*args, **kwargs):  # noqa: ANN002, ANN003
        raise OSError("disk full")

    monkeypatch.setattr(api_mod, "write_debug_dump", fail_write)
    monkeypatch.setattr(api_mod, "parse_list_systems_response", lambda raw: [])
    api = QuiltApi(
        QuiltApiConfig(
            host="example.com",
            id_token="tok",
            refresh_token="r",
            debug_dir=str(tmp_path),
        ),
        aiohttp_session=None,
    )
    api._channel = _FakeChannel()  # type: ignore[attr-defined]
    api.token_expires_soon = lambda within_seconds=120: False  # type: ignore[assignment]

    assert asyncio.run(api.async_list_systems()) == []


def test_api_unary_unary_propagates_grpc_errors(monkeypatch) -> None:  # noqa: ANN001
    class Boom(grpc.RpcError):
        pass

    class BadChannel(_FakeChannel):
        def unary_unary(
            self, method: str, request_serializer=None, response_deserializer=None
        ):  # noqa: ANN001
            def call(req, metadata=None, timeout=None):  # noqa: ANN001
                raise Boom("nope")

            return call

    api = QuiltApi(
        QuiltApiConfig(host="example.com", id_token="tok", refresh_token="r"),
        aiohttp_session=None,
    )
    api._channel = BadChannel()  # type: ignore[attr-defined]
    api.token_expires_soon = lambda within_seconds=120: False  # type: ignore[assignment]
    try:
        asyncio.run(api.async_list_systems())
    except grpc.RpcError:
        pass
    else:
        raise AssertionError("expected grpc.RpcError")


def test_api_token_parsing_and_refresh_errors(monkeypatch) -> None:  # noqa: ANN001
    import custom_components.quilt.api as api_mod

    api = QuiltApi(
        QuiltApiConfig(host="example.com", id_token="not-a-jwt", refresh_token=""),
        aiohttp_session=None,
    )
    assert api.token_expires_soon() is True
    try:
        asyncio.run(api._ensure_fresh_token())  # type: ignore[attr-defined]
    except api_mod.ConfigEntryAuthFailed:
        pass
    else:
        raise AssertionError(
            "expected ConfigEntryAuthFailed when refresh token missing"
        )

    # Bad/invalid jwt payload should safely return None.
    assert api_mod.QuiltApi._jwt_exp_unix("abc") is None
    assert api_mod.QuiltApi._jwt_exp_unix("a.@@@.c") is None

    api2 = QuiltApi(
        QuiltApiConfig(host="example.com", id_token="tok", refresh_token="r"),
        aiohttp_session=None,
    )
    api2.token_expires_soon = lambda within_seconds=120: True  # type: ignore[assignment]

    async def boom_refresh(session, *, refresh_token: str):  # noqa: ANN001
        raise api_mod.CognitoAuthError("bad token")

    monkeypatch.setattr(api_mod, "refresh_with_refresh_token", boom_refresh)
    try:
        asyncio.run(api2.async_get_authorization_header())
    except api_mod.ConfigEntryAuthFailed:
        pass
    else:
        raise AssertionError("expected ConfigEntryAuthFailed")

    async def transient_refresh(session, *, refresh_token: str):  # noqa: ANN001
        raise CognitoError("service unavailable")

    monkeypatch.setattr(api_mod, "refresh_with_refresh_token", transient_refresh)
    with pytest.raises(CognitoError, match="service unavailable"):
        asyncio.run(api2.async_get_authorization_header())


def test_api_unary_unary_maps_unauthenticated_to_auth_failure() -> None:
    import custom_components.quilt.api as api_mod

    class Unauthenticated(grpc.RpcError):
        def code(self):  # noqa: ANN201
            return grpc.StatusCode.UNAUTHENTICATED

    class BadChannel(_FakeChannel):
        def unary_unary(
            self, method: str, request_serializer=None, response_deserializer=None
        ):  # noqa: ANN001
            del method, request_serializer, response_deserializer

            def call(req, metadata=None, timeout=None):  # noqa: ANN001
                del req, metadata, timeout
                raise Unauthenticated("expired")

            return call

    api = QuiltApi(
        QuiltApiConfig(host="example.com", id_token="tok", refresh_token="r"),
        aiohttp_session=None,
    )
    api._channel = BadChannel()  # type: ignore[attr-defined]
    api.token_expires_soon = lambda within_seconds=120: False  # type: ignore[assignment]

    try:
        asyncio.run(api.async_list_systems())
    except api_mod.ConfigEntryAuthFailed as error:  # type: ignore[name-defined]
        assert "expired" in str(error)
    else:
        raise AssertionError("expected ConfigEntryAuthFailed")

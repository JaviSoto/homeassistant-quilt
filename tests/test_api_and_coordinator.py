from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass

from custom_components.quilt.api import QuiltApi, QuiltApiConfig
from custom_components.quilt.coordinator import QuiltCoordinator
from custom_components.quilt.proto_wire import decode_message, get_first
from custom_components.quilt.quilt_parse import (
    QuiltHdsSystem,
    QuiltSpace,
    QuiltSpaceControls,
    QuiltSpaceHeader,
    QuiltSpaceSettings,
    QuiltSpaceState,
    QuiltSystemInfo,
)


def _fake_jwt(exp_unix: int) -> str:
    payload = base64.urlsafe_b64encode(f'{{"exp":{exp_unix}}}'.encode("utf-8")).decode("ascii").rstrip("=")
    return f"aaaa.{payload}.bbbb"


class _FakeUnaryUnary:
    def __init__(self, *, method: str, record: list[tuple[str, bytes, list[tuple[str, str]]]]):
        self._method = method
        self._record = record

    def __call__(self, request: bytes, metadata=None, timeout=None):  # noqa: ANN001
        self._record.append((self._method, request, list(metadata or [])))
        return b"\x00"


class _FakeChannel:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bytes, list[tuple[str, str]]]] = []

    def unary_unary(self, method: str, request_serializer=None, response_deserializer=None):  # noqa: ANN001
        return _FakeUnaryUnary(method=method, record=self.calls)

    def close(self) -> None:
        return None


def test_api_refreshes_token_and_calls_update_indoor_unit(monkeypatch) -> None:  # noqa: ANN001
    import custom_components.quilt.api as api_mod

    refreshed: list[tuple[str, str]] = []

    async def fake_refresh(session, refresh_token: str):  # noqa: ANN001
        refreshed.append((session, refresh_token))
        return type("T", (), {"id_token": _fake_jwt(9_999_999_999), "refresh_token": "new_refresh"})()

    monkeypatch.setattr(api_mod, "refresh_with_refresh_token", fake_refresh)

    ch = _FakeChannel()
    cfg = QuiltApiConfig(host="example.com", id_token=_fake_jwt(1), refresh_token="r1")
    api = QuiltApi(cfg, aiohttp_session="sess")  # type: ignore[arg-type]
    api._channel = ch  # type: ignore[attr-defined]

    # Should refresh the token.
    token = asyncio.run(api.async_get_authorization_header())
    assert "new_refresh" == api._refresh_token  # type: ignore[attr-defined]
    assert token == api._id_token  # type: ignore[attr-defined]
    assert refreshed

    # Should wrap request message under field 1.
    asyncio.run(api.async_update_indoor_unit(indoor_unit_message=b"\x12\x34"))
    method, req, meta = ch.calls[-1]
    assert method.endswith("/UpdateIndoorUnit")
    assert ("authorization", token) in meta
    decoded = decode_message(req)
    inner = get_first(decoded, number=1, wire_type=2)
    assert inner is not None and inner.value == b"\x12\x34"


def test_coordinator_wraps_update_errors(monkeypatch) -> None:  # noqa: ANN001
    system = QuiltSystemInfo(system_id="sys", name="X", timezone="UTC")

    class FakeApi:
        async def async_get_home_datastore_system(self, system_id: str):  # noqa: ANN001
            raise RuntimeError("boom")

    c = QuiltCoordinator(hass=None, api=FakeApi(), system=system)  # type: ignore[arg-type]
    try:
        asyncio.run(c._async_update_data())  # noqa: SLF001
    except Exception as e:
        assert "boom" in str(e)
    else:
        raise AssertionError("expected exception")


def test_coordinator_success(monkeypatch) -> None:  # noqa: ANN001
    system = QuiltSystemInfo(system_id="sys", name="X", timezone="UTC")

    space = QuiltSpace(
        header=QuiltSpaceHeader(space_id="s1", created=None, updated=None, system_id="sys"),
        relationships_parent_space_id="root",
        settings=QuiltSpaceSettings(name="Office", timezone="UTC"),
        controls=QuiltSpaceControls(
            hvac_mode=2,
            setpoint_c=20.0,
            cooling_setpoint_c=20.0,
            heating_setpoint_c=20.0,
            updated=None,
            unknown_field7=0,
            comfort_setting_override=None,
            comfort_setting_id=None,
        ),
        state=QuiltSpaceState(updated=None, setpoint_c=20.0, ambient_c=21.0, hvac_state=1, comfort_setting_id=None),
    )

    hds = QuiltHdsSystem(
        system_id="sys",
        spaces={"s1": space},
        indoor_units={},
        indoor_units_by_space={},
        comfort_settings={},
        comfort_settings_by_space={},
        topic_ids={"space": {"s1"}},
    )

    class FakeApi:
        async def async_get_home_datastore_system(self, system_id: str):  # noqa: ANN001
            return hds

    c = QuiltCoordinator(hass=None, api=FakeApi(), system=system)  # type: ignore[arg-type]
    data = asyncio.run(c._async_update_data())  # noqa: SLF001
    assert data.system.system_id == "sys"
    assert "s1" in data.hds.spaces

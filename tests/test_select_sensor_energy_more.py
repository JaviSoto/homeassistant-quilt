from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.quilt.energy_coordinator import QuiltEnergyCoordinator
from custom_components.quilt.quilt_parse import (
    QuiltComfortSetting,
    QuiltComfortSettingAttributes,
    QuiltComfortSettingHeader,
    QuiltComfortSettingRelationships,
    QuiltEnergyMetricBucket,
    QuiltHdsSystem,
    QuiltIndoorUnit,
    QuiltIndoorUnitControls,
    QuiltIndoorUnitHeader,
    QuiltIndoorUnitRelationships,
    QuiltSpace,
    QuiltSpaceControls,
    QuiltSpaceEnergyMetrics,
    QuiltSpaceHeader,
    QuiltSpaceSettings,
    QuiltSpaceState,
    QuiltSystemInfo,
    QuiltTimestamp,
)
from custom_components.quilt.select import QuiltLouverModeSelect, async_setup_entry as select_setup_entry
from custom_components.quilt.sensor import (
    QuiltSpaceEnergySensor,
    _is_real_space,
    _safe_zoneinfo,
)


class _FakeApi:
    def __init__(self) -> None:
        self.calls: list[bytes] = []

    async def async_update_indoor_unit(self, *, indoor_unit_message: bytes) -> bytes:  # noqa: ANN001
        self.calls.append(indoor_unit_message)
        return b""


class _FakeCoordinator:
    def __init__(self, data) -> None:  # noqa: ANN001
        self.data = data
        from homeassistant.core import HomeAssistant

        self.hass = HomeAssistant()

    async def async_request_refresh(self) -> None:
        return None


def _mk_space(
    *,
    system_id: str,
    space_id: str,
    name: str,
    root: bool = False,
    hvac_mode: int | None = 2,
    ambient_c: float | None = 21.0,
) -> QuiltSpace:
    return QuiltSpace(
        header=QuiltSpaceHeader(space_id=space_id, created=None, updated=None, system_id=system_id),
        relationships_parent_space_id=None if root else "root",
        settings=QuiltSpaceSettings(name=name, timezone="UTC"),
        controls=QuiltSpaceControls(
            hvac_mode=hvac_mode,
            setpoint_c=20.0,
            cooling_setpoint_c=23.0,
            heating_setpoint_c=20.0,
            updated=None,
            unknown_field7=0,
            comfort_setting_override=None,
            comfort_setting_id="cs-active",
        ),
        state=QuiltSpaceState(updated=None, setpoint_c=20.0, ambient_c=ambient_c, hvac_state=2, comfort_setting_id="cs-active"),
    )


def _mk_comfort(*, system_id: str, space_id: str, name: str, louver_mode: int, louver_pos: float) -> QuiltComfortSetting:
    return QuiltComfortSetting(
        header=QuiltComfortSettingHeader(comfort_setting_id=f"cs-{name.lower()}", created=None, updated=None, system_id=system_id),
        attributes=QuiltComfortSettingAttributes(
            updated=None,
            name=name,
            fan_speed_mode=1,
            fan_speed_percent=0.0,
            heating_setpoint_c=20.0,
            cooling_setpoint_c=23.0,
            comfort_setting_type=0,
            hvac_mode=2,
            louver_mode=louver_mode,
            louver_fixed_position=louver_pos,
        ),
        relationships=QuiltComfortSettingRelationships(updated=None, space_id=space_id),
    )


def _mk_select(*, iu_louver_mode: int | None, iu_louver_pos: float | None) -> tuple[QuiltLouverModeSelect, _FakeApi]:
    system_id = "sys-1"
    space_id = "space-office"
    system = QuiltSystemInfo(system_id=system_id, name="Home", timezone="UTC")
    office = _mk_space(system_id=system_id, space_id=space_id, name="Office")
    cs_active = _mk_comfort(system_id=system_id, space_id=space_id, name="Active", louver_mode=3, louver_pos=0.74)

    iu = QuiltIndoorUnit(
        header=QuiltIndoorUnitHeader(indoor_unit_id="iu-1", created=None, updated=None, system_id=system_id),
        relationships=QuiltIndoorUnitRelationships(space_id=space_id),
        controls=QuiltIndoorUnitControls(
            updated=None,
            light_color_code=None,
            light_brightness=None,
            light_animation=None,
            fan_speed_mode=None,
            fan_speed_percent=None,
            louver_mode=iu_louver_mode,
            louver_fixed_position=iu_louver_pos,
        ),
    )

    indoor_units = {} if iu_louver_mode is None else {"iu-1": iu}
    by_space = {} if iu_louver_mode is None else {space_id: [iu]}
    hds = QuiltHdsSystem(
        system_id=system_id,
        spaces={space_id: office},
        indoor_units=indoor_units,
        indoor_units_by_space=by_space,
        comfort_settings={cs_active.header.comfort_setting_id: cs_active},
        comfort_settings_by_space={space_id: [cs_active]},
        topic_ids={"space": {space_id}},
    )

    api = _FakeApi()
    coordinator = _FakeCoordinator(data=type("D", (), {"system": system, "hds": hds})())
    ent = QuiltLouverModeSelect(coordinator=coordinator, api=api, system_id=system_id, space_id=space_id, space_name="Office")
    return ent, api


def test_louver_current_option_branches_and_invalid_options() -> None:
    ent, _ = _mk_select(iu_louver_mode=2, iu_louver_pos=0.0)
    assert ent.current_option == "Sweep"

    ent2, _ = _mk_select(iu_louver_mode=1, iu_louver_pos=0.0)
    assert ent2.current_option == "Closed"

    ent3, _ = _mk_select(iu_louver_mode=3, iu_louver_pos=0.74)
    assert ent3.current_option == "Fixed 75%"

    # Fallback to comfort setting when no indoor unit exists.
    ent4, api4 = _mk_select(iu_louver_mode=None, iu_louver_pos=None)
    assert ent4.current_option == "Fixed 75%"
    asyncio.run(ent4.async_select_option("Not an option"))
    asyncio.run(ent4.async_select_option("Fixed nope%"))
    asyncio.run(ent4.async_select_option("Auto"))
    assert api4.calls == []


def test_louver_async_setup_entry_filters_non_real_spaces() -> None:
    from homeassistant.core import HomeAssistant

    hass = HomeAssistant()
    system = QuiltSystemInfo(system_id="sys-1", name="Home", timezone="UTC")
    real = _mk_space(system_id="sys-1", space_id="space-1", name="Office")
    root = _mk_space(system_id="sys-1", space_id="space-root", name="Home", root=True)
    no_name = _mk_space(system_id="sys-1", space_id="space-noname", name="", hvac_mode=None, ambient_c=None)

    hds = QuiltHdsSystem(
        system_id="sys-1",
        spaces={"space-1": real, "space-root": root, "space-noname": no_name},
        indoor_units={},
        indoor_units_by_space={},
        comfort_settings={},
        comfort_settings_by_space={},
        topic_ids={"space": {"space-1"}},
    )
    coord = _FakeCoordinator(data=type("D", (), {"system": system, "hds": hds})())
    hass.data = {
        "quilt": {
            "entry-1": {
                "api": _FakeApi(),
                "systems": [system],
                "coordinators": {"sys-1": coord},
            }
        }
    }
    entry = type("E", (), {"entry_id": "entry-1"})()
    added: list[object] = []

    def _add(entities):  # noqa: ANN001
        added.extend(entities)

    asyncio.run(select_setup_entry(hass, entry, _add))
    assert len(added) == 1


def test_energy_coordinator_update_success_and_error() -> None:
    from homeassistant.core import HomeAssistant

    class _GoodApi:
        def __init__(self) -> None:
            self.params = None

        async def async_get_energy_metrics(self, **kwargs):  # noqa: ANN003
            self.params = kwargs
            return [
                QuiltSpaceEnergyMetrics(
                    space_id="space-1",
                    bucket_time_resolution=1,
                    energy_buckets=[],
                )
            ]

    good_api = _GoodApi()
    c = QuiltEnergyCoordinator(
        HomeAssistant(),
        api=good_api,
        system=QuiltSystemInfo(system_id="sys-1", name="Home", timezone="UTC"),
        lookback_days=999,
    )
    data = asyncio.run(c._async_update_data())  # noqa: SLF001
    assert c._lookback_days == 30  # noqa: SLF001
    assert "space-1" in data.metrics_by_space_id
    assert good_api.params["preferred_time_resolution"] == 1

    class _BadApi:
        async def async_get_energy_metrics(self, **kwargs):  # noqa: ANN003
            raise RuntimeError("nope")

    c2 = QuiltEnergyCoordinator(
        HomeAssistant(),
        api=_BadApi(),
        system=QuiltSystemInfo(system_id="sys-1", name="Home", timezone="UTC"),
        lookback_days=0,
    )
    assert c2._lookback_days == 1  # noqa: SLF001
    try:
        asyncio.run(c2._async_update_data())  # noqa: SLF001
    except Exception as exc:
        assert isinstance(exc, UpdateFailed)
        assert "nope" in str(exc)
    else:
        raise AssertionError("expected UpdateFailed")


def test_sensor_helpers_and_value_paths(monkeypatch) -> None:  # noqa: ANN001
    # Invalid timezone should safely fall back to UTC.
    assert _safe_zoneinfo("Invalid/Zone").key == "UTC"

    space = _mk_space(system_id="sys-1", space_id="space-1", name="Office")
    assert _is_real_space(space, "Home")
    assert not _is_real_space(_mk_space(system_id="sys-1", space_id="space-2", name="Home", root=True), "Home")

    now = datetime(2026, 1, 25, 9, 0, 0, tzinfo=timezone.utc)

    class _DT(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001
            return now if tz is None else now.astimezone(tz)

    monkeypatch.setattr("custom_components.quilt.sensor.datetime", _DT)

    buckets = [
        QuiltEnergyMetricBucket(
            start_time=QuiltTimestamp(seconds=int((now - timedelta(hours=1)).timestamp()), nanos=0),
            status=1,
            energy_usage_kwh=0.4,
        ),
        QuiltEnergyMetricBucket(
            start_time=QuiltTimestamp(seconds=int((now - timedelta(hours=3)).timestamp()), nanos=0),
            status=1,
            energy_usage_kwh=0.6,
        ),
        QuiltEnergyMetricBucket(
            start_time=QuiltTimestamp(seconds=int((now - timedelta(days=1, hours=1)).timestamp()), nanos=0),
            status=1,
            energy_usage_kwh=1.1,
        ),
    ]
    metrics = QuiltSpaceEnergyMetrics(space_id="space-1", bucket_time_resolution=1, energy_buckets=buckets)
    coord_data = type("CD", (), {"fetched_at": now, "metrics_by_space_id": {"space-1": metrics}})()
    coord = _FakeCoordinator(coord_data)

    s7 = QuiltSpaceEnergySensor(
        coordinator=coord,
        system_id="sys-1",
        system_tz="America/Los_Angeles",
        space_id="space-1",
        space_name="Office",
        kind="last_7d",
    )
    assert s7.native_value == 2.1
    attrs = s7.extra_state_attributes
    assert attrs is not None and attrs["bucket_count"] == 3

    s24 = QuiltSpaceEnergySensor(
        coordinator=coord,
        system_id="sys-1",
        system_tz="America/Los_Angeles",
        space_id="space-1",
        space_name="Office",
        kind="last_24h",
    )
    assert s24.native_value == 1.0

    stoday = QuiltSpaceEnergySensor(
        coordinator=coord,
        system_id="sys-1",
        system_tz="UTC",
        space_id="space-1",
        space_name="Office",
        kind="today",
    )
    assert stoday.native_value == 1.0

    smissing = QuiltSpaceEnergySensor(
        coordinator=coord,
        system_id="sys-1",
        system_tz="UTC",
        space_id="missing",
        space_name="Office",
        kind="today",
    )
    assert smissing.native_value is None

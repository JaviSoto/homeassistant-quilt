from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

from custom_components.quilt import diagnostics
from custom_components.quilt.quilt_parse import (
    QuiltHdsSystem,
    QuiltIndoorUnit,
    QuiltIndoorUnitControls,
    QuiltIndoorUnitHeader,
    QuiltIndoorUnitRelationships,
    QuiltIndoorUnitState,
    QuiltSpace,
    QuiltSpaceControls,
    QuiltSpaceHeader,
    QuiltSpaceSettings,
    QuiltSpaceState,
    QuiltSystemInfo,
    QuiltTimestamp,
)


def _space(
    *,
    space_id: str,
    name: str,
    system_id: str,
    updated: QuiltTimestamp | None,
    parent_space_id: str | None = "root-private",
) -> QuiltSpace:
    return QuiltSpace(
        header=QuiltSpaceHeader(
            space_id=space_id,
            created=None,
            updated=None,
            system_id=system_id,
        ),
        relationships_parent_space_id=parent_space_id,
        settings=QuiltSpaceSettings(name=name, timezone="America/Los_Angeles"),
        controls=QuiltSpaceControls(
            hvac_mode=2,
            setpoint_c=21.0,
            cooling_setpoint_c=22.0,
            heating_setpoint_c=20.0,
            updated=QuiltTimestamp(seconds=123, nanos=0),
            unknown_field7=7,
            comfort_setting_override=1,
            comfort_setting_id="comfort-private",
        ),
        state=QuiltSpaceState(
            updated=updated,
            setpoint_c=21.0,
            ambient_c=23.0,
            hvac_state=2,
            comfort_setting_id="comfort-private",
        ),
    )


def _indoor_unit(
    *,
    unit_id: str,
    space_id: str,
    system_id: str,
    updated: QuiltTimestamp | None,
) -> QuiltIndoorUnit:
    return QuiltIndoorUnit(
        header=QuiltIndoorUnitHeader(
            indoor_unit_id=unit_id,
            created=None,
            updated=None,
            system_id=system_id,
        ),
        relationships=QuiltIndoorUnitRelationships(space_id=space_id),
        controls=QuiltIndoorUnitControls(
            updated=QuiltTimestamp(seconds=456, nanos=0),
            light_color_code=0xFFFFFF,
            light_brightness=0.5,
            light_animation=1,
            fan_speed_mode=2,
            fan_speed_percent=50.0,
            louver_mode=2,
            louver_fixed_position=0.5,
        ),
        state=QuiltIndoorUnitState(updated=updated, ambient_c=24.0),
    )


def test_config_entry_diagnostics_reports_only_room_and_unit_health(
    monkeypatch,
) -> None:  # noqa: ANN001
    now = datetime(2026, 10, 2, 17, 0, tzinfo=UTC)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001, ANN206
            return datetime(2026, 10, 2, 17, 0, tzinfo=tz or UTC)

    monkeypatch.setattr(diagnostics, "datetime", FrozenDateTime)

    system_id = "system-private"
    root_id = "root-private"
    office_id = "office-private"
    den_id = "den-private"
    office_unit_id = "office-unit-private"
    den_unit_id = "den-unit-private"

    root = _space(
        space_id=root_id,
        name="Home",
        system_id=system_id,
        updated=QuiltTimestamp(seconds=int(now.timestamp()) - 10, nanos=0),
        parent_space_id=None,
    )
    office = _space(
        space_id=office_id,
        name="Office",
        system_id=system_id,
        updated=QuiltTimestamp(seconds=int(now.timestamp()) - 4_000, nanos=0),
    )
    den = _space(
        space_id=den_id,
        name="Den",
        system_id=system_id,
        updated=QuiltTimestamp(seconds=int(now.timestamp()) - 20, nanos=250_000_000),
    )
    office_unit = _indoor_unit(
        unit_id=office_unit_id,
        space_id=office_id,
        system_id=system_id,
        updated=QuiltTimestamp(seconds=int(now.timestamp()) - 4_100, nanos=0),
    )
    den_unit = _indoor_unit(
        unit_id=den_unit_id,
        space_id=den_id,
        system_id=system_id,
        updated=QuiltTimestamp(seconds=int(now.timestamp()) - 30, nanos=500_000_000),
    )
    office_fresh_remote_sensor = SimpleNamespace(
        header=SimpleNamespace(remote_sensor_id="office-sensor-a-private"),
        state=SimpleNamespace(
            updated=QuiltTimestamp(seconds=int(now.timestamp()) - 30, nanos=0),
            rssi=-59,
        ),
    )
    office_stale_remote_sensor = SimpleNamespace(
        header=SimpleNamespace(remote_sensor_id="office-sensor-b-private"),
        state=SimpleNamespace(
            updated=QuiltTimestamp(seconds=int(now.timestamp()) - 400, nanos=0),
            rssi=-64,
        ),
    )
    den_remote_sensor = SimpleNamespace(
        header=SimpleNamespace(remote_sensor_id="den-sensor-private"),
        state=SimpleNamespace(updated=None, rssi=None),
    )
    hds = QuiltHdsSystem(
        system_id=system_id,
        spaces={root_id: root, office_id: office, den_id: den},
        indoor_units={
            office_unit_id: office_unit,
            den_unit_id: den_unit,
        },
        indoor_units_by_space={
            office_id: [office_unit],
            den_id: [den_unit],
        },
        comfort_settings={},
        comfort_settings_by_space={},
        topic_ids={},
        controller_remote_sensors={
            "office-sensor-a-private": office_fresh_remote_sensor,
            "office-sensor-b-private": office_stale_remote_sensor,
            "den-sensor-private": den_remote_sensor,
        },
        controller_remote_sensors_by_indoor_unit={
            office_unit_id: [office_stale_remote_sensor, office_fresh_remote_sensor],
            den_unit_id: [den_remote_sensor],
        },
    )
    coordinator = SimpleNamespace(
        data=SimpleNamespace(
            system=QuiltSystemInfo(
                system_id=system_id,
                name="Home",
                timezone="America/Los_Angeles",
            ),
            hds=hds,
        ),
        last_update_success=True,
    )
    hass = SimpleNamespace(
        data={
            "quilt": {
                "entry-private": {
                    "coordinators": {system_id: coordinator},
                }
            }
        }
    )
    entry = SimpleNamespace(entry_id="entry-private")

    result = asyncio.run(diagnostics.async_get_config_entry_diagnostics(hass, entry))

    assert result == {
        "systems": [
            {
                "last_update_success": True,
                "spaces": [
                    {
                        "name": "Den",
                        "state_updated_at": "2026-10-02T16:59:40.25Z",
                        "state_updated_age_seconds": 19,
                        "online_by_integration_rule": True,
                        "indoor_units": [
                            {
                                "state_updated_at": "2026-10-02T16:59:30.5Z",
                                "state_updated_age_seconds": 29,
                                "online_by_integration_rule": True,
                                "controller_remote_sensors": [
                                    {
                                        "state_updated_at": None,
                                        "state_updated_age_seconds": None,
                                        "online_by_integration_rule": False,
                                        "rssi": None,
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "name": "Office",
                        "state_updated_at": "2026-10-02T15:53:20Z",
                        "state_updated_age_seconds": 4_000,
                        "online_by_integration_rule": False,
                        "indoor_units": [
                            {
                                "state_updated_at": "2026-10-02T15:51:40Z",
                                "state_updated_age_seconds": 4_100,
                                "online_by_integration_rule": False,
                                "controller_remote_sensors": [
                                    {
                                        "state_updated_at": "2026-10-02T16:59:30Z",
                                        "state_updated_age_seconds": 30,
                                        "online_by_integration_rule": True,
                                        "rssi": -59,
                                    },
                                    {
                                        "state_updated_at": "2026-10-02T16:53:20Z",
                                        "state_updated_age_seconds": 400,
                                        "online_by_integration_rule": False,
                                        "rssi": -64,
                                    },
                                ],
                            }
                        ],
                    },
                ],
            }
        ]
    }

    serialized = repr(result).lower()
    for forbidden in (
        "private",
        "comfort",
        "setpoint",
        "temperature",
        "ambient",
        "hvac",
        "fan",
        "louver",
        "brightness",
        "color",
        "token",
        "credential",
    ):
        assert forbidden not in serialized


def test_config_entry_diagnostics_handles_missing_runtime_data() -> None:
    hass = SimpleNamespace(data={})
    entry = SimpleNamespace(entry_id="missing")

    result = asyncio.run(diagnostics.async_get_config_entry_diagnostics(hass, entry))

    assert result == {"systems": []}

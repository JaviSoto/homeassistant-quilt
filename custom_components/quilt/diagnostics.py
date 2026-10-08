from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .quilt_parse import QuiltTimestamp


def _timestamp_diagnostics(
    timestamp: QuiltTimestamp | None, *, now_seconds: float
) -> dict[str, Any]:
    """Expose only the timestamp and age used by Quilt's availability rule."""
    if timestamp is None:
        return {
            "state_updated_at": None,
            "state_updated_age_seconds": None,
        }

    seconds = timestamp.seconds + (timestamp.nanos / 1_000_000_000)
    whole_seconds = datetime.fromtimestamp(timestamp.seconds, tz=UTC)
    fractional = f"{timestamp.nanos:09d}".rstrip("0")
    updated_at = whole_seconds.strftime("%Y-%m-%dT%H:%M:%S")
    if fractional:
        updated_at += f".{fractional}"

    return {
        "state_updated_at": f"{updated_at}Z",
        "state_updated_age_seconds": int(now_seconds - seconds),
    }


def _coordinator_diagnostics(
    coordinator: Any, *, now_seconds: float
) -> dict[str, Any]:
    """Build the status-only diagnostics for one Quilt system."""
    data = coordinator.data
    if data is None:
        return {
            "last_update_success": bool(coordinator.last_update_success),
            "spaces": [],
        }

    hds = data.hds
    spaces: list[dict[str, Any]] = []
    for space_id, space in sorted(
        hds.spaces.items(), key=lambda item: (item[1].settings.name or "").casefold()
    ):
        if (
            space.relationships_parent_space_id is None
            and space.settings.name == data.system.name
        ):
            continue

        units = sorted(
            hds.indoor_units_by_space.get(space_id, []),
            key=lambda unit: unit.header.indoor_unit_id,
        )

        indoor_units: list[dict[str, Any]] = []
        for unit in units:
            unit_diagnostics = _timestamp_diagnostics(
                unit.state.updated if unit.state is not None else None,
                now_seconds=now_seconds,
            )
            unit_diagnostics["online_by_integration_rule"] = (
                hds.indoor_unit_is_online(
                    unit.header.indoor_unit_id,
                    now_seconds=now_seconds,
                )
            )
            controller_remote_sensors = (
                hds.controller_remote_sensors_by_indoor_unit.get(
                    unit.header.indoor_unit_id, []
                )
            )
            unit_diagnostics["controller_remote_sensors"] = [
                _controller_remote_sensor_diagnostics(
                    hds,
                    remote_sensor,
                    now_seconds=now_seconds,
                )
                for remote_sensor in sorted(
                    controller_remote_sensors,
                    key=lambda sensor: sensor.header.remote_sensor_id,
                )
            ]
            indoor_units.append(unit_diagnostics)

        space_diagnostics = _timestamp_diagnostics(
            space.state.updated,
            now_seconds=now_seconds,
        )
        space_diagnostics.update(
            {
                "name": space.settings.name or "Unnamed space",
                "online_by_integration_rule": hds.space_is_online(
                    space_id,
                    now_seconds=now_seconds,
                ),
                "indoor_units": indoor_units,
            }
        )
        spaces.append(space_diagnostics)

    return {
        "last_update_success": bool(coordinator.last_update_success),
        "spaces": spaces,
    }


def _controller_remote_sensor_diagnostics(
    hds: Any, remote_sensor: Any, *, now_seconds: float
) -> dict[str, Any]:
    """Expose freshness and signal strength, not sensor identity or readings."""
    state = remote_sensor.state
    diagnostics = _timestamp_diagnostics(
        state.updated if state is not None else None,
        now_seconds=now_seconds,
    )
    diagnostics.update(
        {
            "online_by_integration_rule": hds.controller_remote_sensor_is_online(
                remote_sensor.header.remote_sensor_id,
                now_seconds=now_seconds,
            ),
            "rssi": state.rssi if state is not None else None,
        }
    )
    return diagnostics


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return freshness metadata without credentials, identities, or readings."""
    runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    coordinators = runtime.get("coordinators", {})
    now_seconds = datetime.now(UTC).timestamp()
    return {
        "systems": [
            _coordinator_diagnostics(coordinator, now_seconds=now_seconds)
            for coordinator in coordinators.values()
        ]
    }

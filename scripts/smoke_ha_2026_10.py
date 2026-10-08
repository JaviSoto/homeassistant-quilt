"""Check Quilt's supported Home Assistant 2026.10 and grpcio 1.83.1 runtime."""

import asyncio
from types import SimpleNamespace

import grpc
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType

from custom_components.quilt.api import QuiltApi, QuiltApiConfig
from custom_components.quilt.config_flow import QuiltOptionsFlowHandler
from custom_components.quilt.coordinator import QuiltCoordinator
from custom_components.quilt.diagnostics import async_get_config_entry_diagnostics
from custom_components.quilt.energy_coordinator import QuiltEnergyCoordinator


async def exercise_grpc_channel() -> None:
    """Create and close a local channel without contacting Quilt."""
    api = QuiltApi(QuiltApiConfig(host="127.0.0.1:1"), aiohttp_session=None)
    await api.async_connect()
    await api.async_close()


def main() -> None:
    """Verify the Home Assistant 2026.10 and grpcio 1.83.1 contracts."""
    assert grpc.__version__ == "1.83.1"
    assert issubclass(QuiltOptionsFlowHandler, config_entries.OptionsFlowWithReload)
    assert callable(async_get_config_entry_diagnostics)

    result = QuiltOptionsFlowHandler().async_create_entry(
        data={"poll_interval_seconds": 10}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {"poll_interval_seconds": 10}

    unload_callbacks: list[object] = []
    entry = SimpleNamespace(
        async_on_unload=unload_callbacks.append,
        entry_id="smoke-entry",
        options={},
    )
    hass = SimpleNamespace()
    system = SimpleNamespace(system_id="smoke-system", name="Smoke", timezone="UTC")
    api = SimpleNamespace()
    coordinator = QuiltCoordinator(
        hass, api=api, system=system, config_entry=entry  # type: ignore[arg-type]
    )
    energy_coordinator = QuiltEnergyCoordinator(
        hass, api=api, system=system, config_entry=entry  # type: ignore[arg-type]
    )
    assert coordinator.config_entry is entry
    assert energy_coordinator.config_entry is entry
    assert len(unload_callbacks) == 2

    asyncio.run(exercise_grpc_channel())


if __name__ == "__main__":
    main()

"""Check the integration against the minimum supported Home Assistant release."""

from types import SimpleNamespace

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType

from custom_components.quilt.config_flow import QuiltOptionsFlowHandler
from custom_components.quilt.coordinator import QuiltCoordinator
from custom_components.quilt.energy_coordinator import QuiltEnergyCoordinator


def main() -> None:
    """Verify the real Home Assistant 2026.3 minimum API contracts."""
    assert issubclass(QuiltOptionsFlowHandler, config_entries.OptionsFlowWithReload)

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


if __name__ == "__main__":
    main()

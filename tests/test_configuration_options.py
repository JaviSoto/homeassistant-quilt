from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
import voluptuous as vol

from custom_components.quilt import _async_options_update_listener
from custom_components.quilt.config_flow import QuiltOptionsFlowHandler
from custom_components.quilt.const import (
    CONF_ENABLE_DEBUG_DUMPS,
    CONF_ENABLE_NOTIFIER,
    CONF_POLL_INTERVAL_SECONDS,
    DEFAULT_ENABLE_DEBUG_DUMPS,
    DEFAULT_ENABLE_NOTIFIER,
    DEFAULT_POLL_INTERVAL_SECONDS,
)
from custom_components.quilt.coordinator import QuiltCoordinator
from custom_components.quilt.quilt_parse import QuiltSystemInfo


def test_coordinator_uses_configured_poll_interval() -> None:
    system = QuiltSystemInfo(system_id="sys", name="X", timezone="UTC")

    class FakeApi:
        async def async_get_home_datastore_system(self, system_id: str):  # noqa: ANN001
            raise AssertionError(f"unexpected refresh for {system_id}")

    coordinator = QuiltCoordinator(
        hass=None,  # type: ignore[arg-type]
        api=FakeApi(),
        system=system,
        poll_interval_seconds=10,
    )

    assert coordinator.update_interval == timedelta(seconds=10)


def test_options_flow_exposes_safe_defaults_and_accepts_our_override() -> None:
    from homeassistant.config_entries import ConfigEntry

    entry = ConfigEntry()
    handler = QuiltOptionsFlowHandler(entry)
    result = asyncio.run(handler.async_step_init(None))

    schema = result["schema"]
    defaults = schema({})
    assert defaults == {
        CONF_ENABLE_NOTIFIER: DEFAULT_ENABLE_NOTIFIER,
        CONF_POLL_INTERVAL_SECONDS: DEFAULT_POLL_INTERVAL_SECONDS,
        CONF_ENABLE_DEBUG_DUMPS: DEFAULT_ENABLE_DEBUG_DUMPS,
    }

    values = schema(
        {
            CONF_ENABLE_NOTIFIER: True,
            CONF_POLL_INTERVAL_SECONDS: 10,
            CONF_ENABLE_DEBUG_DUMPS: False,
        }
    )
    assert values[CONF_POLL_INTERVAL_SECONDS] == 10

    with pytest.raises(vol.Invalid):
        schema(
            {
                CONF_ENABLE_NOTIFIER: True,
                CONF_POLL_INTERVAL_SECONDS: 9,
                CONF_ENABLE_DEBUG_DUMPS: False,
            }
        )


def test_options_update_reloads_loaded_entry() -> None:
    class FakeConfigEntries:
        def __init__(self) -> None:
            self.reloaded_entry_id: str | None = None

        async def async_reload(self, entry_id: str) -> None:
            self.reloaded_entry_id = entry_id

    class FakeHass:
        def __init__(self) -> None:
            self.config_entries = FakeConfigEntries()

    hass = FakeHass()
    entry = type("Entry", (), {"entry_id": "entry-id", "options": {}})()
    listener = _async_options_update_listener(entry)
    entry.options = {CONF_POLL_INTERVAL_SECONDS: 10}

    asyncio.run(listener(hass, entry))

    assert hass.config_entries.reloaded_entry_id == "entry-id"


def test_data_update_does_not_reload_loaded_entry() -> None:
    class FakeConfigEntries:
        def __init__(self) -> None:
            self.reload_count = 0

        async def async_reload(self, entry_id: str) -> None:
            del entry_id
            self.reload_count += 1

    class FakeEntry:
        entry_id = "entry-id"
        options: dict[str, object] = {}

    class FakeHass:
        def __init__(self) -> None:
            self.config_entries = FakeConfigEntries()

    hass = FakeHass()
    entry = FakeEntry()
    listener = _async_options_update_listener(entry)

    asyncio.run(listener(hass, entry))

    assert hass.config_entries.reload_count == 0

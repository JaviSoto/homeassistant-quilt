from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest
import voluptuous as vol

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
    from homeassistant.config_entries import ConfigEntry

    system = QuiltSystemInfo(system_id="sys", name="X", timezone="UTC")

    class FakeApi:
        async def async_get_home_datastore_system(self, system_id: str):  # noqa: ANN001
            raise AssertionError(f"unexpected refresh for {system_id}")

    coordinator = QuiltCoordinator(
        hass=None,  # type: ignore[arg-type]
        api=FakeApi(),
        system=system,
        config_entry=ConfigEntry(),
        poll_interval_seconds=10,
    )

    assert coordinator.update_interval == timedelta(seconds=10)


def test_options_flow_exposes_safe_defaults_and_accepts_our_override() -> None:
    from homeassistant.config_entries import ConfigEntry

    entry = ConfigEntry()
    handler = QuiltOptionsFlowHandler()
    handler.hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_get_entry=lambda entry_id: entry)
    )
    handler.handler = entry.entry_id
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


def test_options_flow_is_automatic_reload_handler() -> None:
    from homeassistant import config_entries

    handler = QuiltOptionsFlowHandler()

    assert isinstance(handler, config_entries.OptionsFlowWithReload)
    assert handler.automatic_reload is True

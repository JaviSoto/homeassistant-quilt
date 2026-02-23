from __future__ import annotations

import asyncio
import logging

try:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
    from homeassistant.core import HomeAssistant
    from homeassistant.exceptions import ConfigEntryNotReady
    from homeassistant.helpers.aiohttp_client import async_get_clientsession
except ModuleNotFoundError:  # pragma: no cover
    # Running outside Home Assistant (e.g., dev CLI). Keep this module importable,
    # but do not import HA-dependent code paths.
    HAS_HA = False
    HomeAssistant = object  # type: ignore[assignment]
    ConfigEntry = object  # type: ignore[assignment]
    __all__: list[str] = []
else:
    HAS_HA = True
    from .api import QuiltApi, QuiltApiConfig
    from .coordinator import QuiltCoordinator
    from .const import CONF_ENABLE_NOTIFIER, DEFAULT_ENABLE_NOTIFIER, DEFAULT_HOST, DOMAIN
    from .energy_coordinator import QuiltEnergyCoordinator
    from .notifier import QuiltNotifier

    _LOGGER = logging.getLogger(__name__)

    PLATFORMS: list[str] = [
        "climate",
        "fan",
        "light",
        "select",
        "sensor",
    ]


if HAS_HA:

    async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
        def _persist_tokens(id_token: str, refresh_token: str) -> None:
            hass.config_entries.async_update_entry(
                entry,
                data={
                    **entry.data,
                    "id_token": id_token,
                    "refresh_token": refresh_token,
                },
            )

        api = QuiltApi(
            QuiltApiConfig(
                host=DEFAULT_HOST,
                email=entry.data.get("email", ""),
                id_token=entry.data.get("id_token", ""),
                refresh_token=entry.data.get("refresh_token", ""),
                debug_dir=hass.config.path(".quilt_debug"),
            ),
            aiohttp_session=async_get_clientsession(hass),
            token_update_callback=_persist_tokens,
        )
        await api.async_connect()

        try:
            systems = await asyncio.wait_for(api.async_list_systems(), timeout=10)
        except Exception as e:
            raise ConfigEntryNotReady(f"Quilt API not ready (list systems): {e}") from e

        coordinators: dict[str, QuiltCoordinator] = {
            sysinfo.system_id: QuiltCoordinator(hass, api=api, system=sysinfo) for sysinfo in systems
        }

        # Ensure coordinators have data before we forward platform setups, so platform
        # setup doesn't block HA startup on long network calls. If we can't refresh
        # quickly, raise ConfigEntryNotReady so HA can finish starting and retry later.
        try:
            async with asyncio.timeout(20):
                for coordinator in coordinators.values():
                    await coordinator.async_config_entry_first_refresh()
        except Exception as e:
            raise ConfigEntryNotReady(f"Quilt API not ready (initial refresh): {e}") from e

        notifiers: dict[str, QuiltNotifier] = {
            system_id: QuiltNotifier(hass, api=api, coordinator=coordinator)
            for system_id, coordinator in coordinators.items()
        }

        # Energy metrics are a slower, optional poll. We don't want a temporary
        # backend issue to block the entire integration from loading.
        energy_coordinators: dict[str, QuiltEnergyCoordinator] = {
            sysinfo.system_id: QuiltEnergyCoordinator(hass, api=api, system=sysinfo) for sysinfo in systems
        }
        for system_id, energy_coordinator in energy_coordinators.items():
            try:
                async with asyncio.timeout(10):
                    await energy_coordinator.async_config_entry_first_refresh()
            except Exception as e:
                _LOGGER.warning("Quilt energy coordinator initial refresh failed for %s: %s", system_id, e)

        hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
            "api": api,
            "systems": systems,
            "coordinators": coordinators,
            "notifiers": notifiers,
            "energy_coordinators": energy_coordinators,
        }

        enable_notifier = entry.options.get(CONF_ENABLE_NOTIFIER, DEFAULT_ENABLE_NOTIFIER)

        async def _start_notifiers(_: object) -> None:
            if not enable_notifier:
                return
            for notifier in notifiers.values():
                notifier.start()

        # Avoid doing long-lived streaming work during HA startup. Some environments are
        # sensitive to startup delays, and the supervisor can kill Core if it does not
        # become "ready" quickly.
        if hass.is_running:
            await _start_notifiers(None)
        else:
            hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _start_notifiers)

        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        return True

    async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
        ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
        data = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None) or {}
        notifiers: dict[str, QuiltNotifier] = data.get("notifiers") or {}
        for notifier in notifiers.values():
            await notifier.stop()
        api: QuiltApi | None = data.get("api")
        if api is not None:
            await api.async_close()
        return ok

from __future__ import annotations

import asyncio
import logging

try:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
    from homeassistant.core import CoreState, HomeAssistant
    from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
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
    from .const import (
        CONF_ENABLE_DEBUG_DUMPS,
        CONF_ENABLE_NOTIFIER,
        CONF_POLL_INTERVAL_SECONDS,
        DEFAULT_ENABLE_DEBUG_DUMPS,
        DEFAULT_ENABLE_NOTIFIER,
        DEFAULT_HOST,
        DEFAULT_POLL_INTERVAL_SECONDS,
        DOMAIN,
    )
    from .coordinator import QuiltCoordinator
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

    async def _async_teardown_runtime(
        hass: HomeAssistant, entry: ConfigEntry, runtime: dict
    ) -> bool:
        """Release one runtime only after every owned resource is gone."""
        attempted = runtime.get("platforms_forward_attempted")
        if attempted is None:
            attempted = runtime.get("platforms_forwarded", True)
        if attempted:
            try:
                platforms_unloaded = await hass.config_entries.async_unload_platforms(
                    entry, PLATFORMS
                )
            except Exception:
                _LOGGER.exception("Failed to unload Quilt platforms")
                return False
            if not platforms_unloaded:
                _LOGGER.error("Quilt platform unload refused")
                return False
            runtime["platforms_forward_attempted"] = False
            runtime["platforms_forwarded"] = False

        unsubscribe_started = runtime.get("unsubscribe_started")
        if unsubscribe_started is not None:
            try:
                unsubscribe_started()
            except Exception:
                _LOGGER.exception("Failed to unregister Quilt startup listener")
                return False
            runtime["unsubscribe_started"] = None

        cleanup_succeeded = True
        notifiers: dict[str, QuiltNotifier] = runtime.get("notifiers") or {}
        for system_id, notifier in tuple(notifiers.items()):
            try:
                await notifier.stop()
            except Exception:
                _LOGGER.exception("Failed to stop Quilt notifier")
                cleanup_succeeded = False
            else:
                notifiers.pop(system_id, None)
        if not cleanup_succeeded:
            return False

        api: QuiltApi | None = runtime.get("api")
        if api is not None:
            try:
                await api.async_close()
            except Exception:
                _LOGGER.exception("Failed to close Quilt API")
                return False
        return True

    async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
        enable_debug_dumps = entry.options.get(
            CONF_ENABLE_DEBUG_DUMPS, DEFAULT_ENABLE_DEBUG_DUMPS
        )
        debug_dir = hass.config.path(".quilt_debug") if enable_debug_dumps else None
        poll_interval_seconds = int(
            entry.options.get(CONF_POLL_INTERVAL_SECONDS, DEFAULT_POLL_INTERVAL_SECONDS)
        )
        enable_notifier = entry.options.get(
            CONF_ENABLE_NOTIFIER, DEFAULT_ENABLE_NOTIFIER
        )

        def _persist_tokens(id_token: str, refresh_token: str) -> None:
            hass.config_entries.async_update_entry(
                entry,
                data={
                    **entry.data,
                    "id_token": id_token,
                    "refresh_token": refresh_token,
                },
            )

        existing_domain_data = hass.data.get(DOMAIN)
        if existing_domain_data is not None:
            existing_runtime = existing_domain_data.get(entry.entry_id)
            if existing_runtime is not None:
                if not await _async_teardown_runtime(hass, entry, existing_runtime):
                    raise ConfigEntryNotReady(
                        "Previous Quilt runtime is still owned after teardown failure"
                    )
                existing_domain_data.pop(entry.entry_id, None)
                if not existing_domain_data:
                    hass.data.pop(DOMAIN, None)

        api = QuiltApi(
            QuiltApiConfig(
                host=DEFAULT_HOST,
                email=entry.data.get("email", ""),
                id_token=entry.data.get("id_token", ""),
                refresh_token=entry.data.get("refresh_token", ""),
                debug_dir=debug_dir,
            ),
            aiohttp_session=async_get_clientsession(hass),
            token_update_callback=_persist_tokens,
        )
        deferred_unsubscribe = None
        domain_data = hass.data.setdefault(DOMAIN, {})
        runtime = {
            "api": api,
            "notifiers": {},
            "platforms_forward_attempted": False,
            "platforms_forwarded": False,
            "unsubscribe_started": None,
        }
        domain_data[entry.entry_id] = runtime
        setup_succeeded = False

        async def _cleanup_failed_setup() -> None:
            """Roll back all ownership acquired by a failed config-entry setup."""
            if not await _async_teardown_runtime(hass, entry, runtime):
                return
            domain_data.pop(entry.entry_id, None)
            if not domain_data:
                hass.data.pop(DOMAIN, None)

        try:
            await api.async_connect()

            try:
                systems = await asyncio.wait_for(api.async_list_systems(), timeout=10)
            except ConfigEntryAuthFailed:
                raise
            except Exception as e:
                raise ConfigEntryNotReady(
                    f"Quilt API not ready (list systems): {e}"
                ) from e

            coordinators: dict[str, QuiltCoordinator] = {
                sysinfo.system_id: QuiltCoordinator(
                    hass,
                    api=api,
                    system=sysinfo,
                    config_entry=entry,
                    poll_interval_seconds=poll_interval_seconds,
                )
                for sysinfo in systems
            }

            # Ensure coordinators have data before we forward platform setups, so platform
            # setup doesn't block HA startup on long network calls. If we can't refresh
            # quickly, raise ConfigEntryNotReady so HA can finish starting and retry later.
            try:
                async with asyncio.timeout(20):
                    for coordinator in coordinators.values():
                        await coordinator.async_config_entry_first_refresh()
            except ConfigEntryAuthFailed:
                raise
            except Exception as e:
                raise ConfigEntryNotReady(
                    f"Quilt API not ready (initial refresh): {e}"
                ) from e

            notifiers: dict[str, QuiltNotifier] = {
                system_id: QuiltNotifier(
                    hass, api=api, coordinator=coordinator, debug_dir=debug_dir
                )
                for system_id, coordinator in coordinators.items()
            }

            # Energy metrics are a slower, optional poll. We don't want a temporary
            # backend issue to block the entire integration from loading.
            energy_coordinators: dict[str, QuiltEnergyCoordinator] = {
                sysinfo.system_id: QuiltEnergyCoordinator(
                    hass, api=api, system=sysinfo, config_entry=entry
                )
                for sysinfo in systems
            }
            for system_id, energy_coordinator in energy_coordinators.items():
                try:
                    async with asyncio.timeout(10):
                        await energy_coordinator.async_config_entry_first_refresh()
                except ConfigEntryAuthFailed:
                    raise
                except Exception as e:
                    _LOGGER.warning(
                        "Quilt energy coordinator initial refresh failed for %s: %s",
                        system_id,
                        e,
                    )

            runtime.update(
                {
                    "systems": systems,
                    "coordinators": coordinators,
                    "energy_coordinators": energy_coordinators,
                }
            )

            async def _start_notifiers(_: object) -> None:
                nonlocal deferred_unsubscribe
                # The one-shot bus listener removes itself when STARTED fires. Do not
                # let entry unload attempt to remove that already-consumed listener.
                deferred_unsubscribe = None
                runtime["unsubscribe_started"] = None
                if not enable_notifier:
                    return
                for system_id, notifier in notifiers.items():
                    runtime["notifiers"][system_id] = notifier
                    notifier.start()

            def _unsubscribe_started_listener() -> None:
                nonlocal deferred_unsubscribe
                if deferred_unsubscribe is None:
                    return
                unsubscribe = deferred_unsubscribe
                unsubscribe()
                deferred_unsubscribe = None
                runtime["unsubscribe_started"] = None

            # Platforms need the runtime data above, but long-lived streaming work must
            # not start until every platform setup has succeeded.
            runtime["platforms_forward_attempted"] = True
            await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
            runtime["platforms_forwarded"] = True

            if hass.state is CoreState.running:
                await _start_notifiers(None)
            else:
                deferred_unsubscribe = hass.bus.async_listen_once(
                    EVENT_HOMEASSISTANT_STARTED, _start_notifiers
                )

                entry.async_on_unload(_unsubscribe_started_listener)
                runtime["unsubscribe_started"] = _unsubscribe_started_listener

            setup_succeeded = True
            return True
        finally:
            if not setup_succeeded:
                await _cleanup_failed_setup()

    async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
        domain_data = hass.data.get(DOMAIN, {})
        data = domain_data.get(entry.entry_id)
        if data is None:
            return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

        if not await _async_teardown_runtime(hass, entry, data):
            return False
        domain_data.pop(entry.entry_id, None)
        return True

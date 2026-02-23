from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import QuiltApi
from .const import DOMAIN
from .coordinator import QuiltCoordinator
from .hds_encode import encode_update_indoor_unit_request
from .quilt_parse import QuiltComfortSetting, QuiltIndoorUnit, QuiltSpace, QuiltSystemInfo

_LOGGER = logging.getLogger(__name__)

# Mirrors constants in climate.py (Kotlin IndoorUnitLouverMode enum order).
_LOUVER_MODE_UNSPECIFIED = 0
_LOUVER_MODE_CLOSED = 1
_LOUVER_MODE_SWEEP = 2
_LOUVER_MODE_FIXED = 3
_LOUVER_MODE_AUTOMATIC = 4

_FIXED_STEPS = (25, 50, 75, 100)

_OPT_AUTO = "Auto"
_OPT_SWEEP = "Sweep"
_OPT_CLOSED = "Closed"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    api: QuiltApi = hass.data[DOMAIN][entry.entry_id]["api"]
    systems: list[QuiltSystemInfo] = hass.data[DOMAIN][entry.entry_id]["systems"]
    coordinators: dict[str, QuiltCoordinator] = hass.data[DOMAIN][entry.entry_id]["coordinators"]

    entities: list[QuiltLouverModeSelect] = []
    for sysinfo in systems:
        coordinator = coordinators[sysinfo.system_id]
        if coordinator.data is None:
            continue

        for space in coordinator.data.hds.spaces.values():
            if not space.settings.name:
                continue
            if space.controls.hvac_mode is None and space.state.ambient_c is None:
                continue
            if space.relationships_parent_space_id is None and space.settings.name == coordinator.data.system.name:
                continue

            entities.append(
                QuiltLouverModeSelect(
                    coordinator=coordinator,
                    api=api,
                    system_id=sysinfo.system_id,
                    space_id=space.header.space_id,
                    space_name=space.settings.name,
                )
            )

    async_add_entities(entities)


class QuiltLouverModeSelect(CoordinatorEntity[QuiltCoordinator], SelectEntity):
    """Picker for Quilt louver mode/position.

    Home Assistant's climate `swing_mode` is meant for "swing on/off" semantics,
    not fixed vane positions. We keep climate swing as a simple on/off, and expose
    the richer louver controls via this select entity.
    """

    def __init__(
        self,
        *,
        coordinator: QuiltCoordinator,
        api: QuiltApi,
        system_id: str,
        space_id: str,
        space_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._api = api
        self._system_id = system_id
        self._space_id = space_id
        self._space_name = space_name

    @property
    def _space(self) -> QuiltSpace | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.hds.spaces.get(self._space_id)

    @property
    def _primary_indoor_unit(self) -> QuiltIndoorUnit | None:
        data = self.coordinator.data
        if data is None:
            return None
        ius = data.hds.indoor_units_by_space.get(self._space_id) or []
        return ius[0] if ius else None

    def _find_comfort_setting_for_space(self, *, name: str) -> QuiltComfortSetting | None:
        data = self.coordinator.data
        if data is None:
            return None
        space = data.hds.spaces.get(self._space_id)
        if space is None:
            return None
        for cs in data.hds.comfort_settings_by_space.get(space.header.space_id, []):
            if (cs.attributes.name or "").lower() == name.lower():
                return cs
        return None

    @property
    def unique_id(self) -> str:
        return f"quilt:{self._system_id}:{self._space_id}:louver_mode"

    @property
    def name(self) -> str | None:
        return f"{self._space_name} Louver"

    @property
    def device_info(self) -> DeviceInfo | None:
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._system_id}:{self._space_id}")},
            name=self._space_name or self._space_id,
            manufacturer="Quilt",
            model="Heat Pump",
            suggested_area=self._space_name or None,
        )

    @property
    def options(self) -> list[str]:
        return [_OPT_AUTO, _OPT_SWEEP, _OPT_CLOSED] + [f"Fixed {p}%" for p in _FIXED_STEPS]

    @property
    def current_option(self) -> str | None:
        # Prefer indoor unit state: this reflects the real hardware position and
        # matches what the Quilt app toggles ("fan angle auto", fixed positions, etc.).
        iu = self._primary_indoor_unit
        if iu is not None:
            lm = iu.controls.louver_mode
            pos = iu.controls.louver_fixed_position
        else:
            cs_active = self._find_comfort_setting_for_space(name="Active")
            if cs_active is None:
                return None
            lm = cs_active.attributes.louver_mode
            pos = cs_active.attributes.louver_fixed_position

        if lm in (None, _LOUVER_MODE_UNSPECIFIED, _LOUVER_MODE_AUTOMATIC):
            return _OPT_AUTO
        if lm == _LOUVER_MODE_SWEEP:
            return _OPT_SWEEP
        if lm == _LOUVER_MODE_CLOSED:
            return _OPT_CLOSED
        if lm == _LOUVER_MODE_FIXED:
            pos = pos or 0.0
            pct_int = int(round(pos * 100))
            closest = min(_FIXED_STEPS, key=lambda x: abs(x - pct_int))
            return f"Fixed {closest}%"
        return _OPT_AUTO

    async def async_select_option(self, option: str) -> None:
        iu = self._primary_indoor_unit
        if iu is None:
            return

        louver_mode: int | None = None
        louver_fixed_position: float | None = None

        if option == _OPT_AUTO:
            louver_mode = _LOUVER_MODE_AUTOMATIC
        elif option == _OPT_SWEEP:
            louver_mode = _LOUVER_MODE_SWEEP
        elif option == _OPT_CLOSED:
            louver_mode = _LOUVER_MODE_CLOSED
        elif option.startswith("Fixed "):
            # "Fixed 25%"
            try:
                pct_int = int(option.replace("Fixed", "").replace("%", "").strip())
            except ValueError:
                _LOGGER.debug("Invalid louver select option '%s' for %s", option, self._space_id)
                return
            pct_int = max(0, min(100, pct_int))
            louver_mode = _LOUVER_MODE_FIXED
            louver_fixed_position = pct_int / 100.0
        else:
            _LOGGER.debug("Invalid louver select option '%s' for %s", option, self._space_id)
            return

        req = encode_update_indoor_unit_request(
            iu,
            louver_mode=louver_mode,
            louver_fixed_position=louver_fixed_position,
        )
        await self._api.async_update_indoor_unit(indoor_unit_message=req)
        await self.coordinator.async_request_refresh()

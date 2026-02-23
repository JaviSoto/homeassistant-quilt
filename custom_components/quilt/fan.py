from __future__ import annotations

import logging

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import QuiltApi
from .const import DOMAIN
from .coordinator import QuiltCoordinator
from .hds_encode import encode_update_comfort_setting_request
from .quilt_parse import QuiltComfortSetting, QuiltSpace, QuiltSystemInfo

_LOGGER = logging.getLogger(__name__)

_FAN_SPEED_MODE_UNKNOWN = 0
_FAN_SPEED_MODE_AUTO = 1
_FAN_SPEED_MODE_SETPOINT = 2

_PRESET_AUTO = "auto"
_PRESET_SET = "set"


def _clamp_pct(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


def _find_active_comfort(hds, *, space_id: str) -> QuiltComfortSetting | None:
    for cs in hds.comfort_settings_by_space.get(space_id, []):
        if (cs.attributes.name or "").lower() == "active":
            return cs
    return None


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    api: QuiltApi = hass.data[DOMAIN][entry.entry_id]["api"]
    systems: list[QuiltSystemInfo] = hass.data[DOMAIN][entry.entry_id]["systems"]
    coordinators: dict[str, QuiltCoordinator] = hass.data[DOMAIN][entry.entry_id]["coordinators"]

    entities: list[QuiltFan] = []
    for sysinfo in systems:
        coordinator = coordinators[sysinfo.system_id]
        if coordinator.data is None:
            _LOGGER.warning("Quilt coordinator has no data for %s; skipping fan entity setup", sysinfo.system_id)
            continue

        for space in coordinator.data.hds.spaces.values():
            if not space.settings.name:
                continue
            if space.relationships_parent_space_id is None and space.settings.name == coordinator.data.system.name:
                continue
            entities.append(
                QuiltFan(
                    coordinator=coordinator,
                    api=api,
                    system_id=sysinfo.system_id,
                    space_id=space.header.space_id,
                    space_name=space.settings.name,
                )
            )

    async_add_entities(entities)


class QuiltFan(CoordinatorEntity[QuiltCoordinator], FanEntity):
    _attr_supported_features = FanEntityFeature.SET_SPEED | FanEntityFeature.PRESET_MODE
    _attr_preset_modes = [_PRESET_AUTO, _PRESET_SET]

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
    def _active(self) -> QuiltComfortSetting | None:
        if self.coordinator.data is None:
            return None
        return _find_active_comfort(self.coordinator.data.hds, space_id=self._space_id)

    @property
    def unique_id(self) -> str:
        return f"quilt:{self._system_id}:{self._space_id}:fan"

    @property
    def name(self) -> str | None:
        return f"{self._space_name} Fan"

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
    def is_on(self) -> bool | None:
        cs = self._active
        if cs is None:
            return None
        mode = cs.attributes.fan_speed_mode
        if mode is None:
            return None
        # Treat "set" mode as on, and "auto" as off (meaning: not overriding speed).
        return int(mode) == _FAN_SPEED_MODE_SETPOINT

    @property
    def percentage(self) -> int | None:
        cs = self._active
        if cs is None:
            return None
        if int(cs.attributes.fan_speed_mode or 0) != _FAN_SPEED_MODE_SETPOINT:
            return 0
        pct = (cs.attributes.fan_speed_percent or 0.0) * 100.0
        return int(round(_clamp_pct(pct)))

    @property
    def preset_mode(self) -> str | None:
        cs = self._active
        if cs is None:
            return None
        mode = int(cs.attributes.fan_speed_mode or 0)
        if mode == _FAN_SPEED_MODE_AUTO:
            return _PRESET_AUTO
        if mode == _FAN_SPEED_MODE_SETPOINT:
            return _PRESET_SET
        return None

    async def async_set_percentage(self, percentage: int) -> None:
        cs = self._active
        if cs is None:
            raise RuntimeError("Active comfort setting not available")
        a = cs.attributes
        if a.heating_setpoint_c is None or a.cooling_setpoint_c is None:
            raise RuntimeError("Active comfort setting missing setpoints")

        pct = _clamp_pct(percentage) / 100.0
        req = encode_update_comfort_setting_request(
            cs,
            heat_c=float(a.heating_setpoint_c),
            cool_c=float(a.cooling_setpoint_c),
            fan_speed_mode=_FAN_SPEED_MODE_SETPOINT,
            fan_speed_percent=float(pct),
        )
        await self._api.async_update_comfort_setting(comfort_setting_message=req)
        await self.coordinator.async_request_refresh()

    async def async_turn_on(self, percentage: int | None = None, **kwargs) -> None:
        if percentage is not None:
            await self.async_set_percentage(percentage)
            return
        # Default to 100% when turning on without a speed.
        await self.async_set_percentage(100)

    async def async_turn_off(self, **kwargs) -> None:
        cs = self._active
        if cs is None:
            raise RuntimeError("Active comfort setting not available")
        a = cs.attributes
        if a.heating_setpoint_c is None or a.cooling_setpoint_c is None:
            raise RuntimeError("Active comfort setting missing setpoints")

        req = encode_update_comfort_setting_request(
            cs,
            heat_c=float(a.heating_setpoint_c),
            cool_c=float(a.cooling_setpoint_c),
            fan_speed_mode=_FAN_SPEED_MODE_AUTO,
            fan_speed_percent=0.0,
        )
        await self._api.async_update_comfort_setting(comfort_setting_message=req)
        await self.coordinator.async_request_refresh()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        if preset_mode == _PRESET_AUTO:
            await self.async_turn_off()
            return
        if preset_mode == _PRESET_SET:
            current = self.percentage or 0
            await self.async_turn_on(percentage=current if current > 0 else 100)
            return
        raise ValueError(f"Unknown preset mode: {preset_mode}")

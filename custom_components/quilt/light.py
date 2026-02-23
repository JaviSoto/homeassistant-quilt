from __future__ import annotations

import logging

from homeassistant.components.light import ColorMode, LightEntity, LightEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import QuiltApi
from .const import DOMAIN
from .coordinator import QuiltCoordinator
from .hds_encode import encode_update_indoor_unit_request
from .quilt_parse import QuiltIndoorUnit, QuiltSpace, QuiltSystemInfo

_LOGGER = logging.getLogger(__name__)

_LIGHT_ANIMATION_TO_EFFECT: dict[int, str] = {
    0: "Unspecified",
    1: "None",
    2: "Sparkle Fade",
    3: "Twinkle Fade",
    4: "Dance",
    5: "Chase",
}

_EFFECT_TO_LIGHT_ANIMATION: dict[str, int] = {v: k for k, v in _LIGHT_ANIMATION_TO_EFFECT.items()}


def _pct_to_brightness(pct_0_1: float | None) -> int | None:
    if pct_0_1 is None:
        return None
    pct = max(0.0, min(1.0, float(pct_0_1)))
    return int(round(pct * 255.0))


def _brightness_to_pct(brightness_0_255: int | None) -> float | None:
    if brightness_0_255 is None:
        return None
    b = max(0, min(255, int(brightness_0_255)))
    return float(b) / 255.0


def _rgbw_from_color_code(code: int | None) -> tuple[int, int, int, int] | None:
    if code is None:
        return None
    # Quilt uses a packed RGBW UInt: 0xRRGGBBWW
    v = int(code) & 0xFFFFFFFF
    r = (v >> 24) & 0xFF
    g = (v >> 16) & 0xFF
    b = (v >> 8) & 0xFF
    w = v & 0xFF
    return (r, g, b, w)


def _color_code_from_rgbw(r: int, g: int, b: int, w: int) -> int:
    rr = max(0, min(255, int(r)))
    gg = max(0, min(255, int(g)))
    bb = max(0, min(255, int(b)))
    ww = max(0, min(255, int(w)))
    return (rr << 24) | (gg << 16) | (bb << 8) | ww


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    api: QuiltApi = hass.data[DOMAIN][entry.entry_id]["api"]
    systems: list[QuiltSystemInfo] = hass.data[DOMAIN][entry.entry_id]["systems"]
    coordinators: dict[str, QuiltCoordinator] = hass.data[DOMAIN][entry.entry_id]["coordinators"]

    entities: list[QuiltIndoorUnitLight] = []
    for sysinfo in systems:
        coordinator = coordinators[sysinfo.system_id]
        if coordinator.data is None:
            _LOGGER.warning("Quilt coordinator has no data for %s; skipping light entity setup", sysinfo.system_id)
            continue

        hds = coordinator.data.hds
        for space_id, indoor_units in hds.indoor_units_by_space.items():
            space = hds.spaces.get(space_id)
            if space is None or not space.settings.name:
                continue
            # Skip the synthetic root "home" space (it typically matches the system name).
            if space.relationships_parent_space_id is None and space.settings.name == coordinator.data.system.name:
                continue
            for iu in indoor_units:
                entities.append(
                    QuiltIndoorUnitLight(
                        coordinator=coordinator,
                        api=api,
                        system_id=sysinfo.system_id,
                        space_id=space_id,
                        space_name=space.settings.name,
                        indoor_unit_id=iu.header.indoor_unit_id,
                    )
                )

    async_add_entities(entities)


class QuiltIndoorUnitLight(CoordinatorEntity[QuiltCoordinator], LightEntity):
    _attr_supported_color_modes = {ColorMode.RGB}
    _attr_color_mode = ColorMode.RGB
    _attr_supported_features = LightEntityFeature.EFFECT
    _attr_effect_list = list(_EFFECT_TO_LIGHT_ANIMATION.keys())

    def __init__(
        self,
        *,
        coordinator: QuiltCoordinator,
        api: QuiltApi,
        system_id: str,
        space_id: str,
        space_name: str,
        indoor_unit_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._api = api
        self._system_id = system_id
        self._space_id = space_id
        self._space_name = space_name
        self._indoor_unit_id = indoor_unit_id

    @property
    def _space(self) -> QuiltSpace | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.hds.spaces.get(self._space_id)

    @property
    def _indoor_unit(self) -> QuiltIndoorUnit | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.hds.indoor_units.get(self._indoor_unit_id)

    @property
    def unique_id(self) -> str:
        return f"quilt:{self._system_id}:indoor_unit:{self._indoor_unit_id}:light"

    @property
    def name(self) -> str | None:
        return f"{self._space_name} Light"

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
        iu = self._indoor_unit
        if iu is None:
            return None
        b = iu.controls.light_brightness
        return bool(b and b > 0.0)

    @property
    def brightness(self) -> int | None:
        iu = self._indoor_unit
        if iu is None:
            return None
        return _pct_to_brightness(iu.controls.light_brightness)

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        iu = self._indoor_unit
        if iu is None:
            return None
        rgbw = _rgbw_from_color_code(iu.controls.light_color_code)
        if rgbw is None:
            return None
        r, g, b, _w = rgbw
        return (r, g, b)

    @property
    def effect(self) -> str | None:
        iu = self._indoor_unit
        if iu is None:
            return None
        anim = iu.controls.light_animation
        if anim is None:
            return None
        return _LIGHT_ANIMATION_TO_EFFECT.get(int(anim), f"Animation {int(anim)}")

    @property
    def extra_state_attributes(self) -> dict[str, object] | None:
        iu = self._indoor_unit
        if iu is None:
            return None
        rgbw = _rgbw_from_color_code(iu.controls.light_color_code)
        white = rgbw[3] if rgbw is not None else None
        return {
            "quilt_indoor_unit_id": iu.header.indoor_unit_id,
            "quilt_light_color_code": iu.controls.light_color_code,
            "quilt_light_animation": iu.controls.light_animation,
            "quilt_light_white_channel": white,
        }

    async def async_turn_on(self, **kwargs) -> None:
        iu = self._indoor_unit
        if iu is None:
            raise RuntimeError("Indoor unit not available (no coordinator data yet)")

        target_pct = _brightness_to_pct(kwargs.get("brightness"))
        if target_pct is None:
            # If no brightness provided, default to the last known brightness or full.
            target_pct = float(iu.controls.light_brightness) if iu.controls.light_brightness is not None else 1.0
        target_pct = max(0.0, min(1.0, target_pct))
        if target_pct == 0.0:
            target_pct = 1.0

        # Color: HA provides RGB. Quilt stores RGBW; preserve W channel if present.
        target_color_code = iu.controls.light_color_code
        rgb = kwargs.get("rgb_color")
        if rgb is not None:
            (r, g, b) = rgb
            rgbw = _rgbw_from_color_code(iu.controls.light_color_code) or (0, 0, 0, 0)
            _cr, _cg, _cb, w = rgbw
            target_color_code = _color_code_from_rgbw(r, g, b, w)

        # Effects: map HA effect names to Quilt enum values.
        target_anim = iu.controls.light_animation
        effect = kwargs.get("effect")
        if effect is not None:
            target_anim = _EFFECT_TO_LIGHT_ANIMATION.get(str(effect), target_anim)

        req = encode_update_indoor_unit_request(
            iu,
            light_brightness=target_pct,
            light_color_code=target_color_code,
            light_animation=target_anim,
        )
        await self._api.async_update_indoor_unit(indoor_unit_message=req)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        iu = self._indoor_unit
        if iu is None:
            raise RuntimeError("Indoor unit not available (no coordinator data yet)")

        req = encode_update_indoor_unit_request(
            iu,
            light_brightness=0.0,
            light_color_code=iu.controls.light_color_code,
            light_animation=iu.controls.light_animation,
        )
        await self._api.async_update_indoor_unit(indoor_unit_message=req)
        await self.coordinator.async_request_refresh()

from __future__ import annotations

import logging
import time

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    HVACAction,
    HVACMode,
    ClimateEntityFeature,
    SWING_OFF,
    SWING_ON,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util.unit_conversion import TemperatureConverter

from .api import QuiltApi
from .const import DOMAIN
from .coordinator import QuiltCoordinator
from .hds_encode import encode_space_diff, encode_update_comfort_setting_request, encode_update_indoor_unit_request
from .quilt_parse import QuiltComfortSetting, QuiltSpace, QuiltSystemInfo

_LOGGER = logging.getLogger(__name__)

_FAN_SPEED_MODE_UNKNOWN = 0
_FAN_SPEED_MODE_AUTO = 1
_FAN_SPEED_MODE_SETPOINT = 2

# Kotlin enum order from the Quilt app sources (IndoorUnitLouverMode):
# UNSPECIFIED=0, CLOSED=1, SWEEP=2, FIXED=3, AUTOMATIC=4
_LOUVER_MODE_UNSPECIFIED = 0
_LOUVER_MODE_CLOSED = 1
_LOUVER_MODE_SWEEP = 2
_LOUVER_MODE_FIXED = 3
_LOUVER_MODE_AUTOMATIC = 4

_FAN_PERCENT_STEPS = (20, 40, 60, 80, 100)
_SWING_FIXED_STEPS = (25, 50, 75, 100)

_ATTR_HVAC_MODE = "hvac_mode"
_ATTR_TEMPERATURE = "temperature"
_ATTR_TARGET_TEMP_LOW = "target_temp_low"
_ATTR_TARGET_TEMP_HIGH = "target_temp_high"


def _hvac_mode_from_quilt(value: int | None) -> HVACMode:
    # Kotlin enum order from the IPA sources:
    # UNKNOWN=0, STANDBY=1, COOL=2, HEAT=3, AUTOMATIC=4, FAN=5, FALLBACK_AUTO=6, FALLBACK_OFF=7
    if value in (None, 0, 1, 7):
        return HVACMode.OFF
    if value == 2:
        return HVACMode.COOL
    if value == 3:
        return HVACMode.HEAT
    if value in (4, 6):
        return HVACMode.HEAT_COOL
    if value == 5:
        return HVACMode.FAN_ONLY
    return HVACMode.OFF


def _quilt_mode_from_hvac_mode(mode: HVACMode) -> int:
    if mode == HVACMode.COOL:
        return 2
    if mode == HVACMode.HEAT:
        return 3
    if mode == HVACMode.HEAT_COOL:
        return 4
    if mode == HVACMode.FAN_ONLY:
        return 5
    return 1  # STANDBY


def _hvac_action_from_quilt(value: int | None) -> HVACAction | None:
    # Kotlin enum order from the IPA sources:
    # UNKNOWN=0, STANDBY=1, COOL=2, HEAT=3, DRIFT=4, FAN=5, COOL_DEFERRED=6, HEAT_DEFERRED=7,
    # FAN_DEFERRED=8, COOL_PREPARING=9, HEAT_PREPARING=10
    if value is None:
        return None
    if value in (0, 1, 4, 6, 7, 8):
        return HVACAction.IDLE
    if value in (2, 9):
        return HVACAction.COOLING
    if value in (3, 10):
        return HVACAction.HEATING
    if value == 5:
        return HVACAction.FAN
    return None


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    api: QuiltApi = hass.data[DOMAIN][entry.entry_id]["api"]
    systems: list[QuiltSystemInfo] = hass.data[DOMAIN][entry.entry_id]["systems"]
    coordinators: dict[str, QuiltCoordinator] = hass.data[DOMAIN][entry.entry_id]["coordinators"]

    # Cleanup: older versions mistakenly created a climate entity for the synthetic "home/root" space.
    ent_reg = er.async_get(hass)

    entities: list[QuiltSpaceClimate] = []
    for sysinfo in systems:
        coordinator = coordinators[sysinfo.system_id]
        if coordinator.data is None:
            _LOGGER.warning("Quilt coordinator has no data for %s; skipping entity setup", sysinfo.system_id)
            continue

        root_unique_ids: set[str] = set()
        for space in coordinator.data.hds.spaces.values():
            if space.relationships_parent_space_id is None and space.settings.name == coordinator.data.system.name:
                root_unique_ids.add(f"quilt:{coordinator.data.system.system_id}:{space.header.space_id}")
        if root_unique_ids:
            for ent in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
                if ent.domain != "climate":
                    continue
                if ent.unique_id in root_unique_ids:
                    ent_reg.async_remove(ent.entity_id)

        for space in coordinator.data.hds.spaces.values():
            # Skip spaces that don't look like controllable rooms yet.
            if not space.settings.name:
                continue
            if space.controls.hvac_mode is None and space.state.ambient_c is None:
                continue
            # Skip the synthetic root "home" space (it typically matches the system name).
            if space.relationships_parent_space_id is None and space.settings.name == coordinator.data.system.name:
                continue
            entities.append(
                QuiltSpaceClimate(
                    coordinator=coordinator,
                    api=api,
                    system_id=sysinfo.system_id,
                    space_id=space.header.space_id,
                    space_name=space.settings.name,
                )
            )

    async_add_entities(entities)


class QuiltSpaceClimate(CoordinatorEntity[QuiltCoordinator], ClimateEntity):
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
        | ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.SWING_MODE
    )
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL, HVACMode.HEAT_COOL, HVACMode.FAN_ONLY]
    _attr_fan_modes = ["auto"] + [f"{p}%" for p in _FAN_PERCENT_STEPS]
    # Model "swing" per HA climate semantics: on/off. The richer Quilt louver
    # modes (auto/closed/fixed positions) are exposed via a separate select entity.
    _attr_swing_modes = [SWING_OFF, SWING_ON]

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
    def unique_id(self) -> str:
        return f"quilt:{self._system_id}:{self._space_id}"

    @property
    def name(self) -> str | None:
        space = self._space
        return (space.settings.name if space is not None else None) or self._space_name or self._space_id

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
    def temperature_unit(self) -> str:
        # Quilt uses Celsius internally; HA may be configured for Fahrenheit.
        return self.hass.config.units.temperature_unit if self.hass else UnitOfTemperature.CELSIUS

    @property
    def min_temp(self) -> float:
        # Inferred from app fixtures (STANDBY uses 8C..40C).
        return self._to_ha_temp(8.0)

    @property
    def max_temp(self) -> float:
        return self._to_ha_temp(40.0)

    @property
    def target_temperature_step(self) -> float:
        # Quilt feels like 0.5C granularity. In Fahrenheit, HA typically uses 1.0.
        return 1.0 if self.temperature_unit == UnitOfTemperature.FAHRENHEIT else 0.5

    @property
    def current_temperature(self) -> float | None:
        space = self._space
        if space is None or space.state.ambient_c is None:
            return None
        return self._to_ha_temp(space.state.ambient_c)

    @property
    def target_temperature(self) -> float | None:
        space = self._space
        if space is None:
            return None
        # Quilt reports a primary setpoint plus explicit heat/cool setpoints.
        # For heat/cool-auto, HA prefers the range properties (low/high). In
        # practice, many HA cards/widgets will show only a single target temp if
        # we also publish `temperature`, so return None in that mode.
        if (self.hvac_mode or HVACMode.OFF) == HVACMode.HEAT_COOL:
            return None

        c = (
            space.controls.setpoint_c
            if space.controls.setpoint_c and space.controls.setpoint_c > 0
            else space.state.setpoint_c
        )
        if c is None or c <= 0:
            return None
        return self._to_ha_temp(c)

    @property
    def target_temperature_low(self) -> float | None:
        """Heat setpoint (low) for auto (heat/cool) mode."""
        space = self._space
        if space is None:
            return None
        c = space.controls.heating_setpoint_c
        if c is None or c <= 0:
            cs_active = self._find_comfort_setting_for_space(name="Active")
            c = cs_active.attributes.heating_setpoint_c if cs_active is not None else None
        if c is None or c <= 0:
            return None
        return self._to_ha_temp(float(c))

    @property
    def target_temperature_high(self) -> float | None:
        """Cool setpoint (high) for auto (heat/cool) mode."""
        space = self._space
        if space is None:
            return None
        c = space.controls.cooling_setpoint_c
        if c is None or c <= 0:
            cs_active = self._find_comfort_setting_for_space(name="Active")
            c = cs_active.attributes.cooling_setpoint_c if cs_active is not None else None
        if c is None or c <= 0:
            return None
        return self._to_ha_temp(float(c))

    @property
    def hvac_mode(self) -> HVACMode | None:
        space = self._space
        if space is None:
            return None
        return _hvac_mode_from_quilt(space.controls.hvac_mode)

    @property
    def hvac_action(self) -> HVACAction | None:
        space = self._space
        if space is None:
            return None
        return _hvac_action_from_quilt(space.state.hvac_state)

    async def async_set_temperature(self, **kwargs) -> None:
        space = self._space
        if space is None:
            return

        requested_mode = kwargs.get(_ATTR_HVAC_MODE)
        if isinstance(requested_mode, str):
            try:
                requested_mode = HVACMode(requested_mode)
            except Exception:
                requested_mode = None

        # HA can send either a single target temperature or a low/high range.
        target_low = kwargs.get(_ATTR_TARGET_TEMP_LOW)
        target_high = kwargs.get(_ATTR_TARGET_TEMP_HIGH)
        target_single = kwargs.get(_ATTR_TEMPERATURE)

        heat_c: float | None = None
        cool_c: float | None = None
        if target_low is not None or target_high is not None:
            if target_low is None or target_high is None:
                return
            heat_c = self._from_ha_temp(float(target_low))
            cool_c = self._from_ha_temp(float(target_high))
        elif target_single is not None:
            heat_c = cool_c = self._from_ha_temp(float(target_single))
        else:
            return

        # Swap if caller accidentally inverted the range.
        if heat_c is not None and cool_c is not None and heat_c > cool_c:
            heat_c, cool_c = cool_c, heat_c

        cs_active = self._find_comfort_setting_for_space(name="Active")
        cs_off = self._find_comfort_setting_for_space(name="Off")

        # HomeKit often calls climate.set_temperature with both hvac_mode and temperature.
        # If a mode is provided, treat it as authoritative (don't rely on our current
        # coordinator state being updated yet).
        if isinstance(requested_mode, HVACMode) and requested_mode != HVACMode.OFF:
            quilt_mode = _quilt_mode_from_hvac_mode(requested_mode)
            comfort_id = cs_active.header.comfort_setting_id if cs_active is not None else space.controls.comfort_setting_id
        else:
            if (self.hvac_mode or HVACMode.OFF) == HVACMode.OFF:
                # When off, only update the Active comfort setting so the next turn-on uses it.
                if cs_active is not None:
                    _LOGGER.debug(
                        "Sending UpdateComfortSetting (Active) for %s heat=%sC cool=%sC",
                        self._space_id,
                        heat_c,
                        cool_c,
                    )
                    req = encode_update_comfort_setting_request(cs_active, heat_c=float(heat_c), cool_c=float(cool_c))
                    await self._api.async_update_comfort_setting(comfort_setting_message=req)
                await self.coordinator.async_request_refresh()
                return

            quilt_mode = space.controls.hvac_mode or _quilt_mode_from_hvac_mode(self.hvac_mode or HVACMode.OFF)
            comfort_id = cs_active.header.comfort_setting_id if cs_active is not None else space.controls.comfort_setting_id

        # Update comfort setting too (matches app behavior).
        if cs_active is not None:
            req = encode_update_comfort_setting_request(cs_active, heat_c=float(heat_c), cool_c=float(cool_c))
            await self._api.async_update_comfort_setting(comfort_setting_message=req)

        # The "primary" setpoint seems redundant when heat/cool are present; use midpoint for stability.
        setpoint_c = float((heat_c + cool_c) / 2.0)
        diff = encode_space_diff(
            space,
            hvac_mode=quilt_mode,
            setpoint_c=setpoint_c,
            heat_c=float(heat_c),
            cool_c=float(cool_c),
            comfort_setting_id=comfort_id,
            comfort_setting_override=2,  # UNTIL_NEXT_SCHEDULE
        )
        _LOGGER.debug(
            "Sending UpdateSpace for %s setpoint=%sC heat=%sC cool=%sC hvac_mode=%s",
            self._space_id,
            setpoint_c,
            heat_c,
            cool_c,
            quilt_mode,
        )
        await self._api.async_update_space(space_message=diff)
        await self.coordinator.async_request_refresh()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        space = self._space
        if space is None:
            return

        cs_active = self._find_comfort_setting_for_space(name="Active")
        cs_off = self._find_comfort_setting_for_space(name="Off")

        # Choose comfort setting and setpoint behavior to match the app.
        if hvac_mode == HVACMode.OFF:
            quilt_mode = 1  # STANDBY
            comfort_id = cs_off.header.comfort_setting_id if cs_off is not None else space.controls.comfort_setting_id
            # Keep the current setpoint when turning off; the Quilt app does not
            # visibly force it down to the minimum.
            heat_c = cool_c = float(space.controls.setpoint_c or space.state.setpoint_c or 20.0)
        else:
            quilt_mode = _quilt_mode_from_hvac_mode(hvac_mode)
            comfort_id = cs_active.header.comfort_setting_id if cs_active is not None else space.controls.comfort_setting_id
            # If we have an Active comfort setting, prefer it: HomeKit often sets
            # the comfort setpoints and then immediately flips hvac_mode, before
            # we get a coordinator refresh with the new controls.* values.
            if hvac_mode == HVACMode.HEAT_COOL and cs_active is not None:
                heat_c = float(cs_active.attributes.heating_setpoint_c or 20.0)
                cool_c = float(cs_active.attributes.cooling_setpoint_c or heat_c)
                if heat_c > cool_c:
                    heat_c, cool_c = cool_c, heat_c
            elif cs_active is not None and cs_active.attributes.heating_setpoint_c is not None:
                heat_c = cool_c = float(cs_active.attributes.heating_setpoint_c)
            else:
                current_target = self.target_temperature
                heat_c = cool_c = self._from_ha_temp(float(current_target)) if current_target is not None else 20.0

            # Keep Active comfort setting in sync with the controls (matches app behavior).
            if cs_active is not None:
                req = encode_update_comfort_setting_request(cs_active, heat_c=float(heat_c), cool_c=float(cool_c))
                await self._api.async_update_comfort_setting(comfort_setting_message=req)

        setpoint_c = float((heat_c + cool_c) / 2.0)
        diff = encode_space_diff(
            space,
            hvac_mode=quilt_mode,
            setpoint_c=setpoint_c,
            heat_c=float(heat_c),
            cool_c=float(cool_c),
            comfort_setting_id=comfort_id,
            comfort_setting_override=2,  # UNTIL_NEXT_SCHEDULE (observed even for off)
        )
        _LOGGER.debug("Sending UpdateSpace for %s hvac_mode=%s", self._space_id, quilt_mode)
        await self._api.async_update_space(space_message=diff)
        await self.coordinator.async_request_refresh()

    @property
    def fan_mode(self) -> str | None:
        cs_active = self._find_comfort_setting_for_space(name="Active")
        if cs_active is None:
            return None
        a = cs_active.attributes
        if a.fan_speed_mode in (None, _FAN_SPEED_MODE_UNKNOWN, _FAN_SPEED_MODE_AUTO):
            return "auto"
        pct = a.fan_speed_percent or 0.0
        pct_int = int(round(pct * 100))
        # Snap to known steps so the UI is stable.
        closest = min(_FAN_PERCENT_STEPS, key=lambda x: abs(x - pct_int))
        return f"{closest}%"

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        cs_active = self._find_comfort_setting_for_space(name="Active")
        if cs_active is None:
            return

        a = cs_active.attributes
        heat_c = a.heating_setpoint_c
        cool_c = a.cooling_setpoint_c
        if heat_c is None or cool_c is None:
            target = self.target_temperature
            target_c = self._from_ha_temp(float(target)) if target is not None else 20.0
            heat_c = cool_c = target_c

        if fan_mode == "auto":
            mode = _FAN_SPEED_MODE_AUTO
            percent = 0.0
        else:
            try:
                percent_int = int(fan_mode.rstrip("%"))
            except ValueError:
                _LOGGER.debug("Invalid fan_mode '%s' for %s", fan_mode, self._space_id)
                return
            percent_int = max(0, min(100, percent_int))
            mode = _FAN_SPEED_MODE_SETPOINT
            percent = percent_int / 100.0

        req = encode_update_comfort_setting_request(
            cs_active,
            heat_c=float(heat_c),
            cool_c=float(cool_c),
            fan_speed_mode=mode,
            fan_speed_percent=float(percent),
        )
        await self._api.async_update_comfort_setting(comfort_setting_message=req)
        await self.coordinator.async_request_refresh()

    @property
    def swing_mode(self) -> str | None:
        data = self.coordinator.data
        if data is None:
            return None
        ius = data.hds.indoor_units_by_space.get(self._space_id) or []
        if not ius:
            return None
        lm = ius[0].controls.louver_mode
        return SWING_ON if lm == _LOUVER_MODE_SWEEP else SWING_OFF

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        data = self.coordinator.data
        if data is None:
            return
        ius = data.hds.indoor_units_by_space.get(self._space_id) or []
        if not ius:
            return
        iu = ius[0]

        louver_mode: int | None = None

        # Primary semantics: SWING_ON/OFF.
        if swing_mode == SWING_ON:
            louver_mode = _LOUVER_MODE_SWEEP
        elif swing_mode == SWING_OFF:
            louver_mode = _LOUVER_MODE_AUTOMATIC
        else:
            # Backward-compat: older versions used non-standard swing_mode values to
            # model louver positions. Keep accepting them for existing automations.
            if swing_mode == "auto":
                louver_mode = _LOUVER_MODE_AUTOMATIC
            elif swing_mode == "sweep":
                louver_mode = _LOUVER_MODE_SWEEP
            elif swing_mode == "closed":
                louver_mode = _LOUVER_MODE_CLOSED
            elif swing_mode.startswith("fixed:"):
                try:
                    pct_int = int(swing_mode.split(":", 1)[1])
                except ValueError:
                    return
                pct_int = max(0, min(100, pct_int))
                louver_mode = _LOUVER_MODE_FIXED
                louver_fixed_position = pct_int / 100.0
            else:
                _LOGGER.debug("Invalid swing_mode '%s' for %s", swing_mode, self._space_id)
                return

        req = encode_update_indoor_unit_request(iu, louver_mode=louver_mode)
        await self._api.async_update_indoor_unit(indoor_unit_message=req)
        await self.coordinator.async_request_refresh()

    def _to_ha_temp(self, celsius: float) -> float:
        return TemperatureConverter.convert(celsius, UnitOfTemperature.CELSIUS, self.temperature_unit)

    def _from_ha_temp(self, temp: float) -> float:
        return TemperatureConverter.convert(temp, self.temperature_unit, UnitOfTemperature.CELSIUS)

    def _find_comfort_setting_for_space(self, *, name: str) -> QuiltComfortSetting | None:
        data = self.coordinator.data
        if data is None:
            return None
        # We need the space ID; if the entity isn't fully initialized yet, bail.
        space = data.hds.spaces.get(self._space_id)
        if space is None:
            return None
        for cs in data.hds.comfort_settings_by_space.get(space.header.space_id, []):
            if (cs.attributes.name or "").lower() == name.lower():
                return cs
        return None

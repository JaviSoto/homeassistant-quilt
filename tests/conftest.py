from __future__ import annotations

import enum
import pathlib
import sys
import types
from dataclasses import dataclass


def _mod(name: str) -> types.ModuleType:
    m = types.ModuleType(name)
    sys.modules[name] = m
    return m


def _ensure_pkg(name: str) -> types.ModuleType:
    parts = name.split(".")
    cur = ""
    parent = None
    for p in parts:
        cur = f"{cur}.{p}" if cur else p
        if cur in sys.modules:
            parent = sys.modules[cur]
            continue
        m = _mod(cur)
        if parent is not None:
            setattr(parent, p, m)
        parent = m
    return sys.modules[name]


def pytest_sessionstart(session) -> None:  # noqa: ARG001
    """Install a minimal fake Home Assistant package for unit tests.

    This repo is a custom integration, but our CI should be able to run unit tests
    without pulling the full Home Assistant dependency tree.
    """

    repo_root = pathlib.Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))

    if "homeassistant" in sys.modules:
        return

    ha = _ensure_pkg("homeassistant")

    # --- Core types
    core = _ensure_pkg("homeassistant.core")

    class HomeAssistant:  # noqa: D401
        """Minimal stub."""

        def __init__(self) -> None:
            self.data = {}
            self.loop = None
            self.is_running = True
            self.bus = types.SimpleNamespace(async_listen_once=lambda *a, **k: None)
            self.config = types.SimpleNamespace(
                path=lambda p="": f"/tmp/{p}".rstrip("/"),
                units=types.SimpleNamespace(temperature_unit="°C"),
            )

            class _ConfigEntries:
                def async_get_entry(self, entry_id):  # noqa: ANN001
                    return None

                def async_update_entry(self, entry, data=None, options=None):  # noqa: ANN001
                    return None

                async def async_reload(self, entry_id):  # noqa: ANN001
                    return None

                async def async_forward_entry_setups(self, entry, platforms):  # noqa: ANN001
                    return None

                async def async_unload_platforms(self, entry, platforms):  # noqa: ANN001
                    return True

            self.config_entries = _ConfigEntries()

    core.HomeAssistant = HomeAssistant

    const = _ensure_pkg("homeassistant.const")
    const.UnitOfTemperature = types.SimpleNamespace(CELSIUS="°C", FAHRENHEIT="°F")
    const.UnitOfEnergy = types.SimpleNamespace(KILO_WATT_HOUR="kWh")
    const.EVENT_HOMEASSISTANT_STARTED = "homeassistant_started"

    exceptions = _ensure_pkg("homeassistant.exceptions")

    class ConfigEntryNotReady(RuntimeError):
        pass

    exceptions.ConfigEntryNotReady = ConfigEntryNotReady

    class ConfigEntryAuthFailed(RuntimeError):
        pass

    exceptions.ConfigEntryAuthFailed = ConfigEntryAuthFailed

    # --- config_entries
    config_entries = _ensure_pkg("homeassistant.config_entries")

    class ConfigEntry:  # noqa: D401
        """Minimal stub."""

        def __init__(self) -> None:
            self.data = {}
            self.options = {}
            self.entry_id = "test"

    config_entries.ConfigEntry = ConfigEntry

    class ConfigFlow:
        def __init_subclass__(cls, **kwargs):  # noqa: ANN001
            return super().__init_subclass__()

        def __init__(self) -> None:
            self.hass = HomeAssistant()
            self.context = {}

        async def async_set_unique_id(self, unique_id):  # noqa: ANN001
            self._unique_id = unique_id

        def _abort_if_unique_id_configured(self):  # noqa: D401
            """No-op for tests."""

        def async_show_form(self, *, step_id, data_schema=None, errors=None):  # noqa: ANN001
            return {"type": "form", "step_id": step_id, "errors": errors or {}, "schema": data_schema}

        def async_create_entry(self, *, title, data):  # noqa: ANN001
            return {"type": "create_entry", "title": title, "data": data}

        def async_abort(self, *, reason):  # noqa: ANN001
            return {"type": "abort", "reason": reason}

    config_entries.ConfigFlow = ConfigFlow

    class OptionsFlow:
        def __init__(self, config_entry):  # noqa: ANN001
            self._config_entry = config_entry

        def async_show_form(self, *, step_id, data_schema=None, errors=None):  # noqa: ANN001
            return {"type": "form", "step_id": step_id, "errors": errors or {}, "schema": data_schema}

        def async_create_entry(self, *, title, data):  # noqa: ANN001
            return {"type": "create_entry", "title": title, "data": data}

    config_entries.OptionsFlow = OptionsFlow

    # Also allow `from homeassistant import config_entries`.
    ha.config_entries = config_entries

    # --- data_entry_flow
    data_entry_flow = _ensure_pkg("homeassistant.data_entry_flow")
    data_entry_flow.FlowResult = dict

    # --- aiohttp client helper
    aiohttp_client = _ensure_pkg("homeassistant.helpers.aiohttp_client")

    def async_get_clientsession(hass):  # noqa: ARG001
        return None

    aiohttp_client.async_get_clientsession = async_get_clientsession

    # --- device registry
    device_registry = _ensure_pkg("homeassistant.helpers.device_registry")

    @dataclass(frozen=True)
    class DeviceInfo:
        identifiers: set[tuple[str, str]]
        name: str | None = None
        manufacturer: str | None = None
        model: str | None = None
        suggested_area: str | None = None

    device_registry.DeviceInfo = DeviceInfo

    # --- entity platform
    entity_platform = _ensure_pkg("homeassistant.helpers.entity_platform")
    entity_platform.AddEntitiesCallback = object

    # --- update coordinator
    update_coordinator = _ensure_pkg("homeassistant.helpers.update_coordinator")

    class UpdateFailed(RuntimeError):
        pass

    update_coordinator.UpdateFailed = UpdateFailed

    class DataUpdateCoordinator:  # noqa: D401
        """Minimal stub."""

        def __class_getitem__(cls, item):  # noqa: ANN001
            return cls

        def __init__(self, hass, *, logger=None, name="", update_interval=None):  # noqa: ANN001
            self.hass = hass
            self.logger = logger
            self.name = name
            self.update_interval = update_interval
            self.data = None

        async def async_request_refresh(self) -> None:
            return None

        async def async_config_entry_first_refresh(self) -> None:
            return None

        def async_add_listener(self, cb):  # noqa: ANN001
            def unsub():
                return None

            return unsub

    update_coordinator.DataUpdateCoordinator = DataUpdateCoordinator

    class CoordinatorEntity:  # noqa: D401
        """Minimal stub."""

        def __class_getitem__(cls, item):  # noqa: ANN001
            return cls

        def __init__(self, coordinator):  # noqa: ANN001
            self.coordinator = coordinator
            self.hass = getattr(coordinator, "hass", None)

    update_coordinator.CoordinatorEntity = CoordinatorEntity

    # --- entity registry (only needed for module import)
    er = _ensure_pkg("homeassistant.helpers.entity_registry")

    class _ER:
        def async_remove(self, entity_id):  # noqa: ANN001
            return None

    def async_get(hass):  # noqa: ARG001
        return _ER()

    def async_entries_for_config_entry(reg, entry_id):  # noqa: ARG001
        return []

    er.async_get = async_get
    er.async_entries_for_config_entry = async_entries_for_config_entry

    helpers = _ensure_pkg("homeassistant.helpers")
    helpers.entity_registry = er

    # --- unit conversion
    unit_conversion = _ensure_pkg("homeassistant.util.unit_conversion")

    class TemperatureConverter:
        @staticmethod
        def convert(value, from_unit, to_unit):  # noqa: ANN001
            return value

    unit_conversion.TemperatureConverter = TemperatureConverter

    # --- climate
    climate = _ensure_pkg("homeassistant.components.climate")
    climate_const = _ensure_pkg("homeassistant.components.climate.const")

    class ClimateEntity:
        @property
        def supported_features(self):  # noqa: ANN001
            return getattr(self, "_attr_supported_features", ClimateEntityFeature(0))

    climate.ClimateEntity = ClimateEntity

    class HVACMode(str, enum.Enum):
        OFF = "off"
        HEAT = "heat"
        COOL = "cool"
        HEAT_COOL = "heat_cool"
        FAN_ONLY = "fan_only"

    climate_const.HVACMode = HVACMode

    class HVACAction(str, enum.Enum):
        IDLE = "idle"
        COOLING = "cooling"
        HEATING = "heating"
        FAN = "fan"

    climate_const.HVACAction = HVACAction

    class ClimateEntityFeature(enum.IntFlag):
        TARGET_TEMPERATURE = 1
        TARGET_TEMPERATURE_RANGE = 2
        FAN_MODE = 4
        SWING_MODE = 8

    climate_const.ClimateEntityFeature = ClimateEntityFeature
    climate_const.SWING_OFF = "off"
    climate_const.SWING_ON = "on"

    # --- light
    light = _ensure_pkg("homeassistant.components.light")

    class ColorMode(str, enum.Enum):
        BRIGHTNESS = "brightness"
        RGB = "rgb"

    light.ColorMode = ColorMode

    class LightEntityFeature(enum.IntFlag):
        EFFECT = 1

    light.LightEntityFeature = LightEntityFeature

    class LightEntity:
        @property
        def effect_list(self):  # noqa: ANN001
            return getattr(self, "_attr_effect_list", None)

    light.LightEntity = LightEntity

    # --- fan
    fan = _ensure_pkg("homeassistant.components.fan")

    class FanEntityFeature(enum.IntFlag):
        SET_SPEED = 1
        PRESET_MODE = 2

    fan.FanEntityFeature = FanEntityFeature

    class FanEntity:
        @property
        def preset_modes(self):  # noqa: ANN001
            return getattr(self, "_attr_preset_modes", None)

    fan.FanEntity = FanEntity

    # --- sensor
    sensor = _ensure_pkg("homeassistant.components.sensor")

    class SensorDeviceClass(str, enum.Enum):
        ENERGY = "energy"

    sensor.SensorDeviceClass = SensorDeviceClass

    class SensorStateClass(str, enum.Enum):
        MEASUREMENT = "measurement"

    sensor.SensorStateClass = SensorStateClass

    class SensorEntity:
        pass

    sensor.SensorEntity = SensorEntity

    # --- select
    select = _ensure_pkg("homeassistant.components.select")

    class SelectEntity:
        pass

    select.SelectEntity = SelectEntity

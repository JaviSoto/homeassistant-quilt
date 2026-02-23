from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import QuiltCoordinator
from .energy_coordinator import QuiltEnergyCoordinator
from .quilt_parse import QuiltEnergyMetricBucket, QuiltSpace, QuiltSpaceEnergyMetrics, QuiltSystemInfo

_LOGGER = logging.getLogger(__name__)


def _bucket_dt_utc(b: QuiltEnergyMetricBucket) -> datetime:
    return datetime.fromtimestamp(b.start_time.seconds + (b.start_time.nanos / 1_000_000_000), tz=timezone.utc)


def _safe_zoneinfo(tz_name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name or "UTC")
    except Exception:
        return ZoneInfo("UTC")


def _is_real_space(space: QuiltSpace, system_name: str) -> bool:
    if not space.settings.name:
        return False
    if space.controls.hvac_mode is None and space.state.ambient_c is None:
        return False
    if space.relationships_parent_space_id is None and space.settings.name == system_name:
        return False
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    systems: list[QuiltSystemInfo] = hass.data[DOMAIN][entry.entry_id]["systems"]
    coordinators: dict[str, QuiltCoordinator] = hass.data[DOMAIN][entry.entry_id]["coordinators"]
    energy_coordinators: dict[str, QuiltEnergyCoordinator] = hass.data[DOMAIN][entry.entry_id].get("energy_coordinators") or {}

    entities: list[SensorEntity] = []
    for sysinfo in systems:
        coordinator = coordinators.get(sysinfo.system_id)
        energy = energy_coordinators.get(sysinfo.system_id)
        if coordinator is None or coordinator.data is None or energy is None:
            continue

        for space in coordinator.data.hds.spaces.values():
            if not _is_real_space(space, coordinator.data.system.name):
                continue
            entities.append(
                QuiltSpaceEnergySensor(
                    coordinator=energy,
                    system_id=sysinfo.system_id,
                    system_tz=sysinfo.timezone,
                    space_id=space.header.space_id,
                    space_name=space.settings.name or space.header.space_id,
                    kind="last_hour",
                )
            )
            entities.append(
                QuiltSpaceEnergySensor(
                    coordinator=energy,
                    system_id=sysinfo.system_id,
                    system_tz=sysinfo.timezone,
                    space_id=space.header.space_id,
                    space_name=space.settings.name or space.header.space_id,
                    kind="today",
                )
            )
            entities.append(
                QuiltSpaceEnergySensor(
                    coordinator=energy,
                    system_id=sysinfo.system_id,
                    system_tz=sysinfo.timezone,
                    space_id=space.header.space_id,
                    space_name=space.settings.name or space.header.space_id,
                    kind="last_24h",
                )
            )
            entities.append(
                QuiltSpaceEnergySensor(
                    coordinator=energy,
                    system_id=sysinfo.system_id,
                    system_tz=sysinfo.timezone,
                    space_id=space.header.space_id,
                    space_name=space.settings.name or space.header.space_id,
                    kind="last_7d",
                )
            )

    async_add_entities(entities)


class QuiltSpaceEnergySensor(CoordinatorEntity[QuiltEnergyCoordinator], SensorEntity):
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        *,
        coordinator: QuiltEnergyCoordinator,
        system_id: str,
        system_tz: str,
        space_id: str,
        space_name: str,
        kind: str,
    ) -> None:
        super().__init__(coordinator)
        self._system_id = system_id
        self._system_tz = system_tz
        self._space_id = space_id
        self._space_name = space_name
        self._kind = kind

    @property
    def unique_id(self) -> str:
        return f"quilt:{self._system_id}:{self._space_id}:energy:{self._kind}"

    @property
    def name(self) -> str | None:
        label = {
            "last_hour": "Energy last hour",
            "today": "Energy today",
            "last_24h": "Energy last 24h",
            "last_7d": "Energy last 7 days",
        }.get(self._kind, self._kind)
        return f"{self._space_name} {label}"

    @property
    def device_info(self) -> DeviceInfo | None:
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._system_id}:{self._space_id}")},
            name=self._space_name or self._space_id,
            manufacturer="Quilt",
            model="Heat Pump",
            suggested_area=self._space_name or None,
        )

    def _metrics(self) -> QuiltSpaceEnergyMetrics | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.metrics_by_space_id.get(self._space_id)

    @property
    def native_value(self) -> float | None:
        metrics = self._metrics()
        if metrics is None:
            return None

        now_utc = datetime.now(timezone.utc)
        buckets = [_bucket_dt_utc(b) for b in metrics.energy_buckets]
        values = [b.energy_usage_kwh for b in metrics.energy_buckets]

        if self._kind == "last_hour":
            hour_start = now_utc.replace(minute=0, second=0, microsecond=0)
            prev = hour_start - timedelta(hours=1)
            for dt, v in zip(buckets, values):
                if dt == prev:
                    return round(float(v), 6)
            return None

        if self._kind == "last_7d":
            return round(sum(values), 3)

        if self._kind == "last_24h":
            cutoff = now_utc - timedelta(hours=24)
            total = sum(v for dt, v in zip(buckets, values) if dt >= cutoff)
            return round(total, 3)

        tz = _safe_zoneinfo(self._system_tz)
        midnight_local = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
        total = sum(v for dt, v in zip(buckets, values) if dt.astimezone(tz) >= midnight_local)
        return round(total, 3)

    @property
    def extra_state_attributes(self) -> dict | None:
        if self._kind != "last_7d":
            return None
        metrics = self._metrics()
        if metrics is None:
            return None
        buckets = [
            {
                "start": _bucket_dt_utc(b).isoformat(),
                "status": b.status,
                "kwh": round(float(b.energy_usage_kwh), 6),
            }
            for b in sorted(metrics.energy_buckets, key=lambda x: (x.start_time.seconds, x.start_time.nanos))
        ]
        return {
            "bucket_time_resolution": metrics.bucket_time_resolution,
            "bucket_count": len(buckets),
            "buckets": buckets,
            "fetched_at": (self.coordinator.data.fetched_at.isoformat() if self.coordinator.data else None),
        }

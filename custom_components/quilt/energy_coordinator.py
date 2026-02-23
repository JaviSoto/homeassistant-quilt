from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import QuiltApi
from .const import DEFAULT_ENERGY_LOOKBACK_DAYS, DEFAULT_ENERGY_POLL_INTERVAL_SECONDS
from .quilt_parse import QuiltSpaceEnergyMetrics, QuiltSystemInfo


@dataclass(frozen=True)
class QuiltEnergyCoordinatorData:
    fetched_at: datetime
    metrics_by_space_id: dict[str, QuiltSpaceEnergyMetrics]


class QuiltEnergyCoordinator(DataUpdateCoordinator[QuiltEnergyCoordinatorData]):
    def __init__(
        self,
        hass: HomeAssistant,
        *,
        api: QuiltApi,
        system: QuiltSystemInfo,
        lookback_days: int = DEFAULT_ENERGY_LOOKBACK_DAYS,
    ) -> None:
        super().__init__(
            hass,
            logger=logging.getLogger(__name__),
            name=f"Quilt {system.name} energy",
            update_interval=timedelta(seconds=DEFAULT_ENERGY_POLL_INTERVAL_SECONDS),
        )
        self._api = api
        self._system = system
        self._lookback_days = max(1, min(30, int(lookback_days)))

    async def _async_update_data(self) -> QuiltEnergyCoordinatorData:
        try:
            now = datetime.now(timezone.utc)
            start = now - timedelta(days=self._lookback_days)
            metrics = await self._api.async_get_energy_metrics(
                system_id=self._system.system_id,
                start_time=start,
                end_time=now,
                preferred_time_resolution=1,  # HOURLY
            )
            return QuiltEnergyCoordinatorData(
                fetched_at=now,
                metrics_by_space_id={m.space_id: m for m in metrics},
            )
        except Exception as e:
            raise UpdateFailed(str(e)) from e

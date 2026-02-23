from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import QuiltApi
from .const import DEFAULT_POLL_INTERVAL_SECONDS
from .quilt_parse import QuiltHdsSystem, QuiltSystemInfo


@dataclass(frozen=True)
class QuiltCoordinatorData:
    system: QuiltSystemInfo
    hds: QuiltHdsSystem


class QuiltCoordinator(DataUpdateCoordinator[QuiltCoordinatorData]):
    def __init__(self, hass: HomeAssistant, *, api: QuiltApi, system: QuiltSystemInfo) -> None:
        super().__init__(
            hass,
            logger=logging.getLogger(__name__),
            name=f"Quilt {system.name}",
            update_interval=timedelta(seconds=DEFAULT_POLL_INTERVAL_SECONDS),
        )
        self._api = api
        self._system = system

    async def _async_update_data(self) -> QuiltCoordinatorData:
        try:
            hds = await self._api.async_get_home_datastore_system(self._system.system_id)
            return QuiltCoordinatorData(system=self._system, hds=hds)
        except Exception as e:
            raise UpdateFailed(str(e)) from e

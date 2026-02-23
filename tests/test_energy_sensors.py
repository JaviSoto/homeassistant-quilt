from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from custom_components.quilt.energy_coordinator import QuiltEnergyCoordinatorData
from custom_components.quilt.quilt_parse import QuiltEnergyMetricBucket, QuiltSpaceEnergyMetrics, QuiltTimestamp
from custom_components.quilt.sensor import QuiltSpaceEnergySensor


class FakeCoordinator:
    def __init__(self, data) -> None:  # noqa: ANN001
        self.data = data
        from homeassistant.core import HomeAssistant

        self.hass = HomeAssistant()


def test_energy_sensor_last_hour_matches_previous_bucket(monkeypatch) -> None:
    # Freeze time to an exact hour boundary so "last hour" is deterministic.
    now = datetime(2026, 1, 25, 8, 0, 0, tzinfo=timezone.utc)

    class _DT(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001
            return now if tz is None else now.astimezone(tz)

    monkeypatch.setattr("custom_components.quilt.sensor.datetime", _DT)

    prev = now - timedelta(hours=1)
    space_id = "space-1"

    metrics = QuiltSpaceEnergyMetrics(
        space_id=space_id,
        bucket_time_resolution=1,
        energy_buckets=[
            QuiltEnergyMetricBucket(
                start_time=QuiltTimestamp(seconds=int(prev.timestamp()), nanos=0),
                status=1,
                energy_usage_kwh=0.1234567,
            ),
        ],
    )
    data = QuiltEnergyCoordinatorData(fetched_at=now, metrics_by_space_id={space_id: metrics})

    ent = QuiltSpaceEnergySensor(
        coordinator=FakeCoordinator(data),
        system_id="sys-1",
        system_tz="America/Los_Angeles",
        space_id=space_id,
        space_name="Office",
        kind="last_hour",
    )
    assert ent.native_value == 0.123457


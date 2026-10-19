"""Raster definition."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ecu_can_daq.models import MeasurementDefinition, RasterConfig as ModelRasterConfig


@dataclass
class Raster:
    name: str
    rate_hz: float
    measurements: list[MeasurementDefinition] = field(default_factory=list)
    event_channel: int = 0
    description: str = ""
    sequence: int = 0
    last_values: dict[str, float] = field(default_factory=dict)
    overruns: int = 0

    @property
    def period_s(self) -> float:
        return 1.0 / self.rate_hz if self.rate_hz > 0 else 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "rate_hz": self.rate_hz,
            "event_channel": self.event_channel,
            "signals": [m.name for m in self.measurements],
            "sequence": self.sequence,
            "overruns": self.overruns,
        }

    @classmethod
    def from_config(
        cls, cfg: ModelRasterConfig, measurements: list[MeasurementDefinition], event_channel: int
    ) -> "Raster":
        meas_map = {m.name: m for m in measurements}
        r_meas = [meas_map[n] for n in cfg.signals if n in meas_map]
        return cls(
            name=cfg.name,
            rate_hz=cfg.rate_hz,
            measurements=r_meas,
            event_channel=event_channel,
            description=cfg.description,
        )


# Re-export for convenience
RasterConfig = ModelRasterConfig

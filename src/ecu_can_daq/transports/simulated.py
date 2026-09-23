from __future__ import annotations

import math
import time

from ecu_can_daq.models import MeasurementDefinition
from ecu_can_daq.transports.base import MeasurementTransport


class SimulatedTransport(MeasurementTransport):
    def __init__(self) -> None:
        self._start_time = 0.0

    def connect(self) -> None:
        self._start_time = time.perf_counter()

    def disconnect(self) -> None:
        return None

    def read_measurements(
        self, measurements: list[MeasurementDefinition]
    ) -> dict[str, float]:
        elapsed = time.perf_counter() - self._start_time
        values: dict[str, float] = {}
        for index, definition in enumerate(measurements):
            span = definition.upper_limit - definition.lower_limit
            midpoint = definition.lower_limit + (span / 2.0)
            amplitude = span / 2.0 if span else 1.0
            value = midpoint + amplitude * math.sin(elapsed * (1.0 + index * 0.05))
            values[definition.name] = round(value, 6)
        return values

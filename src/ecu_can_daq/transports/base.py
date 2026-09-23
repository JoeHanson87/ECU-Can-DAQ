from __future__ import annotations

from abc import ABC, abstractmethod

from ecu_can_daq.models import MeasurementDefinition


class MeasurementTransport(ABC):
    @abstractmethod
    def connect(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def disconnect(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def read_measurements(
        self, measurements: list[MeasurementDefinition]
    ) -> dict[str, float]:
        raise NotImplementedError

"""Base recorder ABC."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ecu_can_daq.models import MeasurementBatch, RecorderConfig


class BaseRecorder(ABC):
    def __init__(self, config: RecorderConfig):
        self.config = config
        self._is_open = False

    @abstractmethod
    def open(self, root_properties: dict[str, Any] | None = None) -> None:
        pass

    @abstractmethod
    def append(self, batch: MeasurementBatch) -> None:
        pass

    @abstractmethod
    def flush(self) -> None:
        pass

    @abstractmethod
    def close(self) -> None:
        pass

    @property
    def is_open(self) -> bool:
        return self._is_open

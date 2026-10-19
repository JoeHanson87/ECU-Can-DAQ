"""Global application state for web server."""
from __future__ import annotations

from typing import Any
import threading
from dataclasses import dataclass, field

from ecu_can_daq.models import DeviceConfig, TransportType, MeasurementDefinition, RasterConfig, RecorderConfig, DaqStatus


@dataclass
class AppState:
    device_config: DeviceConfig = field(default_factory=lambda: DeviceConfig())
    measurements: dict[str, MeasurementDefinition] = field(default_factory=dict)
    rasters: list[RasterConfig] = field(default_factory=list)
    recorder_config: RecorderConfig | None = None
    daq_status: DaqStatus = field(default_factory=DaqStatus)
    acquisition_service: Any | None = None
    transport: Any | None = None
    latest_values: dict[str, float] = field(default_factory=dict)
    latest_batches: dict[str, Any] = field(default_factory=dict)
    project_path: str | None = None
    a2l_path: str | None = None
    # Thread safety
    lock: threading.Lock = field(default_factory=threading.Lock)
    # WS clients
    ws_clients: set[Any] = field(default_factory=set)


# Singleton
_state: AppState | None = None
_lock = threading.Lock()


def get_state() -> AppState:
    global _state
    with _lock:
        if _state is None:
            _state = AppState()
        return _state


def set_state(state: AppState) -> None:
    global _state
    with _lock:
        _state = state

"""Transport base with extended DAQ and calibration support."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Protocol, Any

from ecu_can_daq.models import MeasurementDefinition, DeviceConfig
from ecu_can_daq.xcp.client import RequestResponseLink


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

    # Optional DAQ support – transports may override
    def supports_daq(self) -> bool:
        return False

    def configure_daq(
        self, rasters: list[tuple[int, float, list[MeasurementDefinition]]]
    ) -> Any:
        raise NotImplementedError("DAQ not supported by this transport")

    def start_daq(self) -> None:
        raise NotImplementedError

    def stop_daq(self) -> None:
        raise NotImplementedError

    def read_dto(self, timeout: float = 0.05) -> list[bytes]:
        return []

    def decode_dto(self, payloads: list[bytes]) -> dict[str, float]:
        return {}

    # Device config
    def get_device_config(self) -> DeviceConfig | None:
        return None

    def set_device_config(self, config: DeviceConfig) -> None:
        pass

    # Calibration (optional)
    def write_memory(self, address_extension: int, address: int, data: bytes) -> None:
        raise NotImplementedError

    def read_memory(self, address_extension: int, address: int, length: int) -> bytes:
        raise NotImplementedError


class CanFrameTransport(Protocol):
    """Low-level CAN that can send/receive frames."""

    def open(self) -> None: ...
    def close(self) -> None: ...
    def send(self, can_id: int, payload: bytes, extended: bool = False) -> None: ...
    def recv(self, timeout: float) -> list[tuple[int, bytes]]: ...  # list of (id, payload)


class XcpCanLink(RequestResponseLink):
    """Adapts CanFrameTransport to XCP RequestResponseLink."""

    def __init__(
        self,
        can_transport: CanFrameTransport,
        request_id: int,
        response_id: int,
        response_timeout: float = 0.25,
    ):
        self._can = can_transport
        self._req_id = request_id
        self._resp_id = response_id
        self._timeout = response_timeout

    def open(self) -> None:
        self._can.open()

    def close(self) -> None:
        self._can.close()

    def exchange(self, payload: bytes) -> bytes:
        if len(payload) > 8:
            raise ValueError(f"XCP request exceeds CAN payload: {len(payload)}")
        self._can.send(self._req_id, payload)
        deadline = self._timeout
        import time

        start = time.monotonic()
        while True:
            elapsed = time.monotonic() - start
            remaining = deadline - elapsed
            if remaining <= 0:
                raise TimeoutError(
                    f"Timed out waiting for XCP response on 0x{self._resp_id:X}"
                )
            frames = self._can.recv(remaining)
            for can_id, data in frames:
                if can_id == self._resp_id:
                    # payload_length handling
                    return data
            # continue loop
        # unreachable

    def read_dto(self, timeout: float = 0.05) -> list[bytes]:
        """Read all DTOs available (not response ID filtered as DAQ PIDs may differ)"""
        frames = self._can.recv(timeout)
        dtos: list[bytes] = []
        for can_id, data in frames:
            # If transport uses dedicated DTO IDs, DAQ PIDs share same CAN ID? Usually DAQ uses same response ID or different
            # For simplicity, treat any frame not equal to response_id as DTO if PIDs configured, else include response_id frames that are DTO (PID < 0xFC)
            # XCP DTO: first byte PID 0..0xFB (DAQ), 0xFC..0xFF are control. So if PID <=0xFB, it's DAQ.
            if not data:
                continue
            pid = data[0]
            if pid <= 0xFB:  # DAQ/STIM DTO
                dtos.append(data)
            elif can_id != self._resp_id:
                # Could still be DAQ if different CAN ID used
                dtos.append(data)
        return dtos

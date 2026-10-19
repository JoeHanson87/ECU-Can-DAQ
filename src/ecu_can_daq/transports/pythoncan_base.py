"""Base for python-can transports (SocketCAN, PCAN, Vector)."""
from __future__ import annotations

from typing import Any

from ecu_can_daq.models import MeasurementDefinition, DeviceConfig, TransportType
from ecu_can_daq.transports.base import MeasurementTransport, XcpCanLink
from ecu_can_daq.transports.pythoncan_adapter import PythonCanTransport
from ecu_can_daq.xcp.client import XcpClient


class PythonCanXcpTransport(MeasurementTransport):
    """Generic transport using python-can."""

    def __init__(
        self,
        interface: str,
        config: DeviceConfig,
        receive_own_messages: bool = False,
    ):
        self._interface = interface
        self._config = config
        self._can = PythonCanTransport(
            config=config, interface=interface, receive_own_messages=receive_own_messages
        )
        self._link = XcpCanLink(
            can_transport=self._can,
            request_id=config.request_id,
            response_id=config.response_id,
            response_timeout=config.response_timeout_ms / 1000.0,
        )
        self._xcp = XcpClient(self._link, max_payload_bytes=config.max_payload_bytes)

    def connect(self) -> None:
        self._xcp.connect()

    def disconnect(self) -> None:
        try:
            self._xcp.stop_daq()
        except Exception:
            pass
        self._xcp.disconnect()

    def read_measurements(self, measurements: list[MeasurementDefinition]) -> dict[str, float]:
        return self._xcp.read_measurements(measurements)

    def supports_daq(self) -> bool:
        return True

    def configure_daq(self, rasters):
        return self._xcp.configure_daq_lists(rasters)

    def start_daq(self) -> None:
        self._xcp.start_daq()

    def stop_daq(self) -> None:
        self._xcp.stop_daq()

    def read_dto(self, timeout: float = 0.05) -> list[bytes]:
        return self._xcp.read_dto(timeout)

    def decode_dto(self, payloads: list[bytes]) -> dict[str, float]:
        return self._xcp.decode_dto_batch(payloads)

    def get_device_config(self) -> DeviceConfig:
        return self._config

    def set_device_config(self, config: DeviceConfig) -> None:
        self._config = config
        self._can = PythonCanTransport(
            config=config, interface=self._interface, receive_own_messages=False
        )
        self._link = XcpCanLink(
            can_transport=self._can,
            request_id=config.request_id,
            response_id=config.response_id,
            response_timeout=config.response_timeout_ms / 1000.0,
        )
        self._xcp = XcpClient(self._link, max_payload_bytes=config.max_payload_bytes)

    def read_memory(self, address_extension: int, address: int, length: int) -> bytes:
        return self._xcp.read_memory(address_extension, address, length)

    def write_memory(self, address_extension: int, address: int, data: bytes) -> None:
        self._xcp.download(address_extension, address, data)

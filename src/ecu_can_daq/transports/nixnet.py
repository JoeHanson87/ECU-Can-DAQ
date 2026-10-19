"""NI-XNET transport with DAQ support."""
from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any

from ecu_can_daq.models import MeasurementDefinition, DeviceConfig, TransportType
from ecu_can_daq.transports.base import MeasurementTransport, CanFrameTransport
from ecu_can_daq.xcp.client import XcpClient
from ecu_can_daq.xcp.client import RequestResponseLink


@dataclass
class _NiXnetRuntime:
    nixnet: Any
    constants: Any
    types: Any


class NiXnetCanFrameTransport(CanFrameTransport):
    def __init__(
        self,
        channel: str,
        bitrate: int,
        can_termination: bool,
        can_fd: bool = False,
    ):
        self._channel = channel
        self._bitrate = bitrate
        self._can_termination = can_termination
        self._can_fd = can_fd
        self._runtime: _NiXnetRuntime | None = None
        self._input_session: Any | None = None
        self._output_session: Any | None = None

    def open(self) -> None:
        try:
            import nixnet
            from nixnet import constants, types
        except ImportError as exc:
            raise RuntimeError(
                "NI-XNET transport requires the optional 'nixnet' dependency on Windows"
            ) from exc
        self._runtime = _NiXnetRuntime(nixnet=nixnet, constants=constants, types=types)
        self._input_session = nixnet.FrameInStreamSession(self._channel)
        self._output_session = nixnet.FrameOutStreamSession(self._channel)
        termination = constants.CanTerm.ON if self._can_termination else constants.CanTerm.OFF
        self._input_session.intf.can_term = termination
        self._output_session.intf.can_term = termination
        self._input_session.intf.baud_rate = self._bitrate
        self._output_session.intf.baud_rate = self._bitrate
        # CAN-FD handling if supported
        if self._can_fd:
            try:
                self._input_session.intf.can_fd_baud_rate = self._bitrate
                self._output_session.intf.can_fd_baud_rate = self._bitrate
            except Exception:
                pass
        self._input_session.start()
        self._output_session.start()

    def close(self) -> None:
        if self._output_session is not None:
            try:
                self._output_session.close()
            except Exception:
                pass
            self._output_session = None
        if self._input_session is not None:
            try:
                self._input_session.close()
            except Exception:
                pass
            self._input_session = None
        self._runtime = None

    def send(self, can_id: int, payload: bytes, extended: bool = False) -> None:
        if not self._runtime or self._output_session is None:
            raise RuntimeError("NI-XNET link not open")
        if len(payload) > 8 and not self._can_fd:
            raise ValueError(f"Payload {len(payload)} exceeds classic CAN")
        frame_type = self._runtime.constants.FrameType.CAN_DATA
        # FD if needed
        if self._can_fd and len(payload) > 8:
            frame_type = getattr(self._runtime.constants.FrameType, "CAN_FD_DATA", frame_type)
        frame = self._runtime.types.CanFrame(
            self._runtime.types.CanIdentifier(can_id),
            frame_type,
            bytearray(payload),
        )
        self._output_session.frames.write([frame])

    def recv(self, timeout: float) -> list[tuple[int, bytes]]:
        if not self._runtime or self._input_session is None:
            raise RuntimeError("NI-XNET link not open")
        try:
            frames = self._input_session.frames.read(64, timeout)
        except Exception:
            return []
        result: list[tuple[int, bytes]] = []
        for received in frames:
            can_id = int(received.identifier)
            plen = getattr(received, "payload_length", len(received.payload))
            result.append((can_id, bytes(received.payload[:plen])))
        return result


class NiXnetCanLink(RequestResponseLink):
    """Backward compat wrapper + new recv for DTO."""

    def __init__(
        self,
        channel: str,
        request_id: int,
        response_id: int,
        bitrate: int,
        can_termination: bool,
        response_timeout: float = 0.25,
        can_fd: bool = False,
    ):
        self._can_transport = NiXnetCanFrameTransport(
            channel=channel,
            bitrate=bitrate,
            can_termination=can_termination,
            can_fd=can_fd,
        )
        self._request_id = request_id
        self._response_id = response_id
        self._response_timeout = response_timeout

    def open(self) -> None:
        self._can_transport.open()

    def close(self) -> None:
        self._can_transport.close()

    def exchange(self, payload: bytes) -> bytes:
        self._can_transport.send(self._request_id, payload)
        deadline = time.monotonic() + self._response_timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"Timed out waiting for XCP response on CAN ID 0x{self._response_id:X}"
                )
            frames = self._can_transport.recv(remaining)
            for can_id, data in frames:
                if can_id == self._response_id:
                    return data
            # continue

    def read_dto(self, timeout: float = 0.05) -> list[bytes]:
        frames = self._can_transport.recv(timeout)
        dtos: list[bytes] = []
        for can_id, data in frames:
            if not data:
                continue
            pid = data[0]
            if pid <= 0xFB:
                dtos.append(data)
            elif can_id != self._response_id:
                dtos.append(data)
        return dtos


class NiXnetTransport(MeasurementTransport):
    def __init__(
        self,
        channel: str = "CAN1",
        request_id: int = 0x7E0,
        response_id: int = 0x7E8,
        bitrate: int = 500_000,
        can_termination: bool = False,
        can_fd: bool = False,
        response_timeout_ms: int = 250,
        max_payload_bytes: int | None = None,
        config: DeviceConfig | None = None,
    ):
        if config is not None:
            self._config = config
        else:
            self._config = DeviceConfig(
                transport=TransportType.NIXNET,
                channel=channel,
                bitrate=bitrate,
                can_fd=can_fd,
                can_termination=can_termination,
                request_id=request_id,
                response_id=response_id,
                response_timeout_ms=response_timeout_ms,
                max_payload_bytes=max_payload_bytes,
            )
        link = NiXnetCanLink(
            channel=self._config.channel,
            request_id=self._config.request_id,
            response_id=self._config.response_id,
            bitrate=self._config.bitrate,
            can_termination=self._config.can_termination,
            response_timeout=self._config.response_timeout_ms / 1000.0,
            can_fd=self._config.can_fd,
        )
        self._xcp = XcpClient(link, max_payload_bytes=self._config.max_payload_bytes)

    def connect(self) -> None:
        self._xcp.connect()

    def disconnect(self) -> None:
        try:
            self._xcp.stop_daq()
        except Exception:
            pass
        self._xcp.disconnect()

    def read_measurements(
        self, measurements: list[MeasurementDefinition]
    ) -> dict[str, float]:
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
        # Recreate link/xcp – requires reconnect
        link = NiXnetCanLink(
            channel=self._config.channel,
            request_id=self._config.request_id,
            response_id=self._config.response_id,
            bitrate=self._config.bitrate,
            can_termination=self._config.can_termination,
            response_timeout=self._config.response_timeout_ms / 1000.0,
            can_fd=self._config.can_fd,
        )
        self._xcp = XcpClient(link, max_payload_bytes=self._config.max_payload_bytes)

    def read_memory(self, address_extension: int, address: int, length: int) -> bytes:
        return self._xcp.read_memory(address_extension, address, length)

    def write_memory(self, address_extension: int, address: int, data: bytes) -> None:
        self._xcp.download(address_extension, address, data)

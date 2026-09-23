from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any

from ecu_can_daq.models import MeasurementDefinition
from ecu_can_daq.transports.base import MeasurementTransport
from ecu_can_daq.xcp import RequestResponseLink, XcpClient


@dataclass
class _NiXnetRuntime:
    nixnet: Any
    constants: Any
    types: Any


class NiXnetCanLink(RequestResponseLink):
    def __init__(
        self,
        channel: str,
        request_id: int,
        response_id: int,
        bitrate: int,
        can_termination: bool,
        response_timeout: float = 0.25,
    ) -> None:
        self._channel = channel
        self._request_id = request_id
        self._response_id = response_id
        self._bitrate = bitrate
        self._can_termination = can_termination
        self._response_timeout = response_timeout
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
        self._input_session.start()
        self._output_session.start()

    def close(self) -> None:
        if self._output_session is not None:
            self._output_session.close()
            self._output_session = None
        if self._input_session is not None:
            self._input_session.close()
            self._input_session = None
        self._runtime = None

    def exchange(self, payload: bytes) -> bytes:
        if not self._runtime or self._input_session is None or self._output_session is None:
            raise RuntimeError("NI-XNET link is not open")
        if len(payload) > 8:
            raise ValueError(
                f"XCP request exceeds classic CAN payload size: {len(payload)} bytes"
            )

        padded_payload = payload[:8].ljust(8, b"\x00")
        frame = self._runtime.types.CanFrame(
            self._runtime.types.CanIdentifier(self._request_id),
            self._runtime.constants.FrameType.CAN_DATA,
            bytearray(padded_payload[: len(payload)]),
        )
        self._output_session.frames.write([frame])

        deadline = time.monotonic() + self._response_timeout
        while True:
            remaining_time = deadline - time.monotonic()
            if remaining_time <= 0:
                raise TimeoutError(
                    f"Timed out waiting for an XCP response on CAN ID 0x{self._response_id:X}"
                )
            frames = self._input_session.frames.read(1, remaining_time)
            if not frames:
                raise TimeoutError(
                    f"Timed out waiting for an XCP response on CAN ID 0x{self._response_id:X}"
                )
            received = frames[0]
            if int(received.identifier) != self._response_id:
                continue
            payload_length = getattr(received, "payload_length", len(received.payload))
            return bytes(received.payload[:payload_length])


class NiXnetTransport(MeasurementTransport):
    def __init__(
        self,
        channel: str,
        request_id: int,
        response_id: int,
        bitrate: int,
        can_termination: bool,
    ) -> None:
        self._xcp = XcpClient(
            NiXnetCanLink(
                channel=channel,
                request_id=request_id,
                response_id=response_id,
                bitrate=bitrate,
                can_termination=can_termination,
            )
        )

    def connect(self) -> None:
        self._xcp.connect()

    def disconnect(self) -> None:
        self._xcp.disconnect()

    def read_measurements(
        self, measurements: list[MeasurementDefinition]
    ) -> dict[str, float]:
        return self._xcp.read_measurements(measurements)

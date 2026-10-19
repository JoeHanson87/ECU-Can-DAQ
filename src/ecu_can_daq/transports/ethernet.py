"""XCP on Ethernet (TCP/UDP) transport stub."""
from __future__ import annotations

import socket
import struct
import time
from typing import Any

from ecu_can_daq.models import MeasurementDefinition, DeviceConfig, TransportType
from ecu_can_daq.transports.base import MeasurementTransport
from ecu_can_daq.xcp.client import XcpClient, RequestResponseLink
from ecu_can_daq.xcp.errors import XcpError


class XcpEthernetLink(RequestResponseLink):
    """XCP on Ethernet (TCP) link."""

    def __init__(
        self,
        host: str,
        port: int,
        protocol: str = "tcp",
        response_timeout: float = 0.5,
    ):
        self._host = host
        self._port = port
        self._protocol = protocol.lower()
        self._timeout = response_timeout
        self._sock: socket.socket | None = None

    def open(self) -> None:
        if self._protocol == "tcp":
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.settimeout(self._timeout)
            self._sock.connect((self._host, self._port))
        else:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.settimeout(self._timeout)

    def close(self) -> None:
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def exchange(self, payload: bytes) -> bytes:
        if not self._sock:
            raise RuntimeError("Ethernet link not open")
        if self._protocol == "tcp":
            # XCP over Ethernet header: 2 bytes length + 2 bytes counter
            # Simplified: send length + payload
            header = struct.pack("<HH", len(payload), 0)
            self._sock.sendall(header + payload)
            # Read header
            hdr = self._sock.recv(4)
            if len(hdr) < 4:
                raise XcpError("Truncated Ethernet header")
            resp_len = struct.unpack("<H", hdr[:2])[0]
            # Read payload
            data = b""
            while len(data) < resp_len:
                chunk = self._sock.recv(resp_len - len(data))
                if not chunk:
                    break
                data += chunk
            return data
        else:
            self._sock.sendto(payload, (self._host, self._port))
            data, _ = self._sock.recvfrom(8192)
            return data

    def read_dto(self, timeout: float = 0.05) -> list[bytes]:
        # Ethernet DTO reading not yet implemented – would need async listener
        return []


class EthernetTransport(MeasurementTransport):
    def __init__(self, config: DeviceConfig | None = None):
        cfg = config or DeviceConfig(
            transport=TransportType.ETHERNET, host="127.0.0.1", port=5555
        )
        self._config = cfg
        self._link = XcpEthernetLink(
            host=cfg.host,
            port=cfg.port,
            response_timeout=cfg.response_timeout_ms / 1000.0,
        )
        self._xcp = XcpClient(self._link, max_payload_bytes=cfg.max_payload_bytes or 512)

    def connect(self) -> None:
        self._xcp.connect()

    def disconnect(self) -> None:
        self._xcp.disconnect()

    def read_measurements(self, measurements: list[MeasurementDefinition]) -> dict[str, float]:
        return self._xcp.read_measurements(measurements)

    def supports_daq(self) -> bool:
        # Could support but not yet
        return False

    def get_device_config(self) -> DeviceConfig:
        return self._config

    def set_device_config(self, config: DeviceConfig) -> None:
        self._config = config
        self._link = XcpEthernetLink(
            host=config.host, port=config.port, response_timeout=config.response_timeout_ms / 1000.0
        )
        self._xcp = XcpClient(self._link, max_payload_bytes=config.max_payload_bytes or 512)

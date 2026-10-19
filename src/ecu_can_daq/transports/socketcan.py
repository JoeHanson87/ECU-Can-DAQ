"""SocketCAN transport."""
from __future__ import annotations

from ecu_can_daq.models import DeviceConfig, TransportType
from ecu_can_daq.transports.pythoncan_base import PythonCanXcpTransport


class SocketCanTransport(PythonCanXcpTransport):
    def __init__(self, config: DeviceConfig | None = None):
        cfg = config or DeviceConfig(
            transport=TransportType.SOCKETCAN,
            channel="can0",
            bitrate=500_000,
        )
        # For testing we support virtual interface fallback
        # If channel is virtual or vcan, use virtual interface
        interface = "socketcan"
        if cfg.channel in ("virtual", "vcan0", "vcan1") or "virtual" in cfg.channel:
            interface = "virtual"
        super().__init__(interface=interface, config=cfg)

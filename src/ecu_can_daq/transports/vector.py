"""Vector XL transport (mocked for now)."""
from __future__ import annotations

import logging

from ecu_can_daq.models import DeviceConfig, TransportType
from ecu_can_daq.transports.pythoncan_base import PythonCanXcpTransport

log = logging.getLogger(__name__)


class VectorTransport(PythonCanXcpTransport):
    def __init__(self, config: DeviceConfig | None = None, mock: bool = False):
        from dataclasses import replace

        cfg = config or DeviceConfig(
            transport=TransportType.VECTOR,
            channel="0",
            bitrate=500_000,
        )
        if cfg.transport != TransportType.VECTOR:
            cfg = replace(cfg, transport=TransportType.VECTOR)
        interface = "vector"
        if mock:
            interface = "virtual"
            if cfg.channel != "virtual":
                cfg = replace(cfg, channel="virtual")
            log.info("Vector transport in mock mode using virtual bus")
        super().__init__(interface=interface, config=cfg)

    @staticmethod
    def check_driver() -> bool:
        try:
            import can.interfaces.vector  # noqa: F401
            return True
        except Exception:
            return False

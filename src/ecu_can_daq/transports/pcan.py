"""PEAK PCAN transport (mocked for now, uses virtual when no hardware)."""
from __future__ import annotations

import logging

from ecu_can_daq.models import DeviceConfig, TransportType
from ecu_can_daq.transports.pythoncan_base import PythonCanXcpTransport

log = logging.getLogger(__name__)


class PcanTransport(PythonCanXcpTransport):
    def __init__(self, config: DeviceConfig | None = None, mock: bool = False):
        from dataclasses import replace

        cfg = config or DeviceConfig(
            transport=TransportType.PCAN,
            channel="PCAN_USBBUS1",
            bitrate=500_000,
        )
        # Ensure transport type is PCAN (handle frozen dataclass)
        if cfg.transport != TransportType.PCAN:
            cfg = replace(cfg, transport=TransportType.PCAN)
        interface = "pcan"
        if mock:
            interface = "virtual"
            if cfg.channel != "virtual":
                cfg = replace(cfg, channel="virtual")
            log.info("PCAN transport in mock mode using virtual bus")
        super().__init__(interface=interface, config=cfg)

    @staticmethod
    def check_driver() -> bool:
        try:
            import can.interfaces.pcan  # noqa: F401
            return True
        except Exception:
            return False

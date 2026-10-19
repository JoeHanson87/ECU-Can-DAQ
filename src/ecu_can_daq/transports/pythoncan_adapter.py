"""python-can based CAN adapter."""
from __future__ import annotations

import time
from typing import Any

try:
    import can
    HAS_PYTHON_CAN = True
except ImportError:
    HAS_PYTHON_CAN = False
    can = None  # type: ignore

from ecu_can_daq.models import DeviceConfig


class PythonCanTransport:
    """Wraps python-can Bus to CanFrameTransport interface."""

    def __init__(
        self,
        config: DeviceConfig,
        interface: str = "socketcan",
        receive_own_messages: bool = False,
    ):
        if not HAS_PYTHON_CAN:
            raise RuntimeError("python-can not installed")
        self._config = config
        self._interface = interface
        self._bus: Any | None = None
        self._receive_own = receive_own_messages

    def open(self) -> None:
        if not HAS_PYTHON_CAN:
            raise RuntimeError("python-can not installed")
        # Map DeviceConfig to python-can params
        kwargs: dict[str, Any] = {
            "interface": self._interface,
            "channel": self._config.channel,
            "receive_own_messages": self._receive_own,
        }
        if self._interface in ("socketcan", "kvaser", "pcan", "vector"):
            # bitrate
            kwargs["bitrate"] = self._config.bitrate
            if self._config.can_fd:
                kwargs["fd"] = True
        # For virtual bus (testing)
        if self._interface == "virtual":
            kwargs["receive_own_messages"] = True
        self._bus = can.Bus(**kwargs)  # type: ignore
        # Flush
        try:
            while self._bus.recv(timeout=0.0):  # type: ignore
                pass
        except Exception:
            pass

    def close(self) -> None:
        if self._bus is not None:
            try:
                self._bus.shutdown()  # type: ignore
            except Exception:
                pass
            self._bus = None

    def send(self, can_id: int, payload: bytes, extended: bool = False) -> None:
        if self._bus is None:
            raise RuntimeError("CAN bus not open")
        is_extended = extended or can_id > 0x7FF
        msg = can.Message(  # type: ignore
            arbitration_id=can_id,
            data=payload,
            is_extended_id=is_extended,
            is_fd=self._config.can_fd,
        )
        self._bus.send(msg)  # type: ignore

    def recv(self, timeout: float) -> list[tuple[int, bytes]]:
        if self._bus is None:
            raise RuntimeError("CAN bus not open")
        deadline = time.monotonic() + timeout
        frames: list[tuple[int, bytes]] = []
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                msg = self._bus.recv(timeout=remaining)  # type: ignore
            except Exception:
                break
            if msg is None:
                break
            # Filter: keep all for now; upper layer filters by ID
            data = bytes(msg.data)[: msg.dlc if hasattr(msg, "dlc") else len(msg.data)]
            frames.append((int(msg.arbitration_id), data))
            # For efficiency, if we got one frame and timeout small, continue to drain?
            # We'll drain up to 32 frames as before
            if len(frames) >= 32:
                break
            # If timeout is very small, break after first batch
            if timeout < 0.01 and frames:
                # try one more non-blocking
                try:
                    while True:
                        extra = self._bus.recv(timeout=0.0)  # type: ignore
                        if extra is None:
                            break
                        frames.append((int(extra.arbitration_id), bytes(extra.data)))
                        if len(frames) >= 64:
                            break
                except Exception:
                    pass
                break
        return frames

    @staticmethod
    def list_interfaces(interface: str = "socketcan") -> list[str]:
        """List available interfaces – best effort."""
        # This is platform dependent; return common guesses
        if interface == "socketcan":
            import glob

            return glob.glob("/sys/class/net/can*")
        return []

    @staticmethod
    def detect_channels() -> list[str]:
        # Best effort: for virtual, return ["virtual"]
        # For socketcan, list net devices? For PCAN/Vector, need driver.
        chans = []
        try:
            import can.interfaces
        except Exception:
            pass
        # Common defaults
        return ["CAN1", "CAN2", "vcan0", "can0"]

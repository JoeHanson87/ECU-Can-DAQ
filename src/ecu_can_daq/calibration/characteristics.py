"""Calibration characteristics management (scaffold for full calibration)."""
from __future__ import annotations

from typing import Any

from ecu_can_daq.models import CharacteristicDefinition
from ecu_can_daq.transports.base import MeasurementTransport


class CalibrationManager:
    """Manages CHARACTERISTIC read/write via XCP DOWNLOAD."""

    def __init__(self, transport: MeasurementTransport, characteristics: dict[str, CharacteristicDefinition]):
        self._transport = transport
        self._chars = characteristics

    def list_characteristics(self) -> list[str]:
        return list(self._chars.keys())

    def read_characteristic(self, name: str) -> Any:
        if name not in self._chars:
            raise KeyError(f"Characteristic {name} not found")
        char = self._chars[name]
        # Use transport read_memory if available
        if hasattr(self._transport, "read_memory"):
            data = self._transport.read_memory(char.address_extension, char.ecu_address, char.size_bytes)  # type: ignore
            # Decode first value
            try:
                raw = char.data_type.decode(data[: char.data_type.size_bytes], char.byte_order)
                if char.compu_method:
                    return char.compu_method.raw_to_phys(raw)
                return raw
            except Exception:
                return data.hex()
        raise NotImplementedError("Transport does not support memory read")

    def write_characteristic(self, name: str, value: Any) -> None:
        if name not in self._chars:
            raise KeyError(f"Characteristic {name} not found")
        char = self._chars[name]
        # Encode value
        phys = float(value)
        raw = char.compu_method.phys_to_raw(phys) if char.compu_method else phys
        payload = char.data_type.encode(raw, char.byte_order)
        if hasattr(self._transport, "write_memory"):
            self._transport.write_memory(char.address_extension, char.ecu_address, payload)  # type: ignore
        else:
            raise NotImplementedError("Transport does not support memory write")

    def to_dict(self) -> dict[str, Any]:
        return {name: char.to_dict() for name, char in self._chars.items()}

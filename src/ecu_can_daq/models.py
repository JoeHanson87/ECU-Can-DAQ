from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import struct
from typing import Any


class ByteOrder(str, Enum):
    LITTLE = "little"
    BIG = "big"


class DataType(str, Enum):
    UBYTE = "UBYTE"
    SBYTE = "SBYTE"
    UWORD = "UWORD"
    SWORD = "SWORD"
    ULONG = "ULONG"
    SLONG = "SLONG"
    A_UINT64 = "A_UINT64"
    A_INT64 = "A_INT64"
    FLOAT32_IEEE = "FLOAT32_IEEE"
    FLOAT64_IEEE = "FLOAT64_IEEE"

    @property
    def size_bytes(self) -> int:
        return {
            DataType.UBYTE: 1,
            DataType.SBYTE: 1,
            DataType.UWORD: 2,
            DataType.SWORD: 2,
            DataType.ULONG: 4,
            DataType.SLONG: 4,
            DataType.A_UINT64: 8,
            DataType.A_INT64: 8,
            DataType.FLOAT32_IEEE: 4,
            DataType.FLOAT64_IEEE: 8,
        }[self]

    def decode(self, payload: bytes, byte_order: ByteOrder) -> float:
        prefix = "<" if byte_order is ByteOrder.LITTLE else ">"
        formats = {
            DataType.UBYTE: "B",
            DataType.SBYTE: "b",
            DataType.UWORD: "H",
            DataType.SWORD: "h",
            DataType.ULONG: "I",
            DataType.SLONG: "i",
            DataType.A_UINT64: "Q",
            DataType.A_INT64: "q",
            DataType.FLOAT32_IEEE: "f",
            DataType.FLOAT64_IEEE: "d",
        }
        return float(struct.unpack(prefix + formats[self], payload)[0])

    @classmethod
    def from_a2l(cls, value: str) -> "DataType":
        try:
            return cls(value.upper())
        except ValueError as exc:
            raise ValueError(f"Unsupported A2L datatype: {value}") from exc


@dataclass(frozen=True)
class MeasurementDefinition:
    name: str
    data_type: DataType
    ecu_address: int
    lower_limit: float
    upper_limit: float
    address_extension: int = 0
    byte_order: ByteOrder = ByteOrder.LITTLE

    @property
    def size_bytes(self) -> int:
        return self.data_type.size_bytes

    def decode(self, payload: bytes) -> float:
        if len(payload) != self.size_bytes:
            raise ValueError(
                f"Measurement {self.name} expected {self.size_bytes} bytes, got {len(payload)}"
            )
        return self.data_type.decode(payload, self.byte_order)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "data_type": self.data_type.value,
            "ecu_address": hex(self.ecu_address),
            "address_extension": self.address_extension,
            "lower_limit": self.lower_limit,
            "upper_limit": self.upper_limit,
            "byte_order": self.byte_order.value,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True)
class MeasurementBatch:
    sequence: int
    timestamp_ns: int
    rate_hz: float
    values: dict[str, float]

    def to_message(self) -> dict[str, Any]:
        return {
            "type": "sample_batch",
            "sequence": self.sequence,
            "timestamp_ns": self.timestamp_ns,
            "rate_hz": self.rate_hz,
            "values": self.values,
        }

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import struct
from typing import Any, Callable
import math


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

    def encode(self, value: float, byte_order: ByteOrder) -> bytes:
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
        fmt = formats[self]
        # Clamp integer types
        if fmt in ("B", "b", "H", "h", "I", "i", "Q", "q"):
            ival = int(round(value))
            return struct.pack(prefix + fmt, ival)
        return struct.pack(prefix + fmt, float(value))

    @classmethod
    def from_a2l(cls, value: str) -> "DataType":
        try:
            return cls(value.upper())
        except ValueError as exc:
            raise ValueError(f"Unsupported A2L datatype: {value}") from exc


class CompuMethodType(str, Enum):
    IDENTICAL = "IDENTICAL"
    LINEAR = "LINEAR"
    RAT_FUNC = "RAT_FUNC"
    TAB_INTP = "TAB_INTP"
    TAB_NOINTP = "TAB_NOINTP"
    TAB_VERB = "TAB_VERB"
    FORM = "FORM"


@dataclass(frozen=True)
class CompuMethod:
    name: str
    compu_type: CompuMethodType = CompuMethodType.IDENTICAL
    unit: str = ""
    format_str: str = "%7.3"
    coeffs: tuple[float, ...] = field(default_factory=lambda: (0.0, 1.0, 0.0, 0.0, 0.0, 1.0))
    # For TAB_* : mapping raw->phys or verb
    table: dict[float, float | str] | None = None
    # Optional formula string (FORM)
    formula: str | None = None
    description: str = ""

    def raw_to_phys(self, raw: float) -> float:
        """Convert raw ECU value to physical using ASAP2 COMPU_METHOD"""
        if self.compu_type == CompuMethodType.IDENTICAL:
            return raw
        if self.compu_type == CompuMethodType.LINEAR:
            # COMPU_METHOD LINEAR: coeffs[0] + coeffs[1]*raw etc, but often a,b
            # ASAP2: f(x) = (a*x + b) approximated by coeffs.
            # We support up to 6 coeffs: (a*x^2 + b*x + c)/(d*x^2 + e*x + f) is RAT_FUNC
            # For LINEAR we interpret as coeffs[1]*raw + coeffs[0] if 2 coeffs
            if len(self.coeffs) >= 2:
                return self.coeffs[0] + self.coeffs[1] * raw
            return raw
        if self.compu_type == CompuMethodType.RAT_FUNC:
            # f(x) = (a*x^2 + b*x + c)/(d*x^2 + e*x + f) with coeffs = (a,b,c,d,e,f)
            if len(self.coeffs) < 6:
                return raw
            a, b, c, d, e, f = self.coeffs[:6]
            num = a * raw * raw + b * raw + c
            den = d * raw * raw + e * raw + f
            if abs(den) < 1e-12:
                return num
            return num / den
        if self.compu_type in (CompuMethodType.TAB_INTP, CompuMethodType.TAB_NOINTP):
            if not self.table:
                return raw
            # Interpolate if TAB_INTP, stepwise if TAB_NOINTP
            sorted_keys = sorted(self.table.keys())
            if raw in self.table:
                v = self.table[raw]
                return float(v) if isinstance(v, (int, float)) else raw
            if self.compu_type == CompuMethodType.TAB_NOINTP:
                # Previous value
                for k in reversed(sorted_keys):
                    if raw >= k:
                        v = self.table[k]
                        return float(v) if isinstance(v, (int, float)) else raw
                return float(sorted_keys[0]) if sorted_keys else raw
            # Linear interpolation
            lower = None
            upper = None
            for k in sorted_keys:
                if k <= raw:
                    lower = k
                if k >= raw and upper is None:
                    upper = k
            if lower is None:
                lower = sorted_keys[0]
            if upper is None:
                upper = sorted_keys[-1]
            if lower == upper:
                v = self.table[lower]
                return float(v) if isinstance(v, (int, float)) else raw
            lv = self.table[lower]
            uv = self.table[upper]
            if not isinstance(lv, (int, float)) or not isinstance(uv, (int, float)):
                return raw
            t = (raw - lower) / (upper - lower) if upper != lower else 0.0
            return float(lv) + t * (float(uv) - float(lv))
        # FORM and TAB_VERB fallback to raw for now
        return raw

    def phys_to_raw(self, phys: float) -> float:
        """Inverse conversion. Only implemented for LINEAR and IDENTICAL, others approximate."""
        if self.compu_type == CompuMethodType.IDENTICAL:
            return phys
        if self.compu_type == CompuMethodType.LINEAR:
            if len(self.coeffs) >= 2 and abs(self.coeffs[1]) > 1e-12:
                return (phys - self.coeffs[0]) / self.coeffs[1]
            return phys
        if self.compu_type == CompuMethodType.RAT_FUNC and len(self.coeffs) >= 6:
            # Inverse not trivial, use iterative Newton for demo
            raw = phys
            for _ in range(10):
                f = self.raw_to_phys(raw) - phys
                # numerical derivative
                h = 1e-6
                df = (self.raw_to_phys(raw + h) - self.raw_to_phys(raw - h)) / (2 * h)
                if abs(df) < 1e-12:
                    break
                raw -= f / df
            return raw
        return phys


class CharacteristicType(str, Enum):
    VALUE = "VALUE"
    CURVE = "CURVE"
    MAP = "MAP"
    CUBOID = "CUBOID"
    CUBE_4 = "CUBE_4"
    CUBE_5 = "CUBE_5"
    VAL_BLK = "VAL_BLK"
    ASCII = "ASCII"
    AXIS_PTS = "AXIS_PTS"


@dataclass(frozen=True)
class AxisDefinition:
    name: str
    data_type: DataType
    size: int
    ecu_address: int
    address_extension: int = 0
    byte_order: ByteOrder = ByteOrder.LITTLE
    compu_method: CompuMethod | None = None
    unit: str = ""
    lower_limit: float = 0.0
    upper_limit: float = 0.0


@dataclass(frozen=True)
class CharacteristicDefinition:
    name: str
    char_type: CharacteristicType
    data_type: DataType
    ecu_address: int
    lower_limit: float
    upper_limit: float
    address_extension: int = 0
    byte_order: ByteOrder = ByteOrder.LITTLE
    compu_method: CompuMethod | None = None
    unit: str = ""
    description: str = ""
    axes: tuple[AxisDefinition, ...] = field(default_factory=tuple)
    dimensions: tuple[int, ...] = field(default_factory=tuple)
    # MATRIX_DIM etc.

    @property
    def size_bytes(self) -> int:
        # Simplified
        base = self.data_type.size_bytes
        if not self.dimensions:
            return base
        prod = 1
        for d in self.dimensions:
            prod *= d
        return base * prod

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.char_type.value,
            "data_type": self.data_type.value,
            "ecu_address": hex(self.ecu_address),
            "address_extension": self.address_extension,
            "lower_limit": self.lower_limit,
            "upper_limit": self.upper_limit,
            "byte_order": self.byte_order.value,
            "unit": self.unit,
            "dimensions": list(self.dimensions),
            "description": self.description,
        }


@dataclass(frozen=True)
class MeasurementDefinition:
    name: str
    data_type: DataType
    ecu_address: int
    lower_limit: float
    upper_limit: float
    address_extension: int = 0
    byte_order: ByteOrder = ByteOrder.LITTLE
    compu_method: CompuMethod | None = None
    unit: str = ""
    description: str = ""
    max_refresh_rate_hz: float | None = None

    @property
    def size_bytes(self) -> int:
        return self.data_type.size_bytes

    def decode(self, payload: bytes) -> float:
        if len(payload) != self.size_bytes:
            raise ValueError(
                f"Measurement {self.name} expected {self.size_bytes} bytes, got {len(payload)}"
            )
        raw = self.data_type.decode(payload, self.byte_order)
        if self.compu_method:
            return self.compu_method.raw_to_phys(raw)
        return raw

    def decode_raw(self, payload: bytes) -> float:
        if len(payload) != self.size_bytes:
            raise ValueError(
                f"Measurement {self.name} expected {self.size_bytes} bytes, got {len(payload)}"
            )
        return self.data_type.decode(payload, self.byte_order)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "name": self.name,
            "data_type": self.data_type.value,
            "ecu_address": hex(self.ecu_address),
            "address_extension": self.address_extension,
            "lower_limit": self.lower_limit,
            "upper_limit": self.upper_limit,
            "byte_order": self.byte_order.value,
            "size_bytes": self.size_bytes,
            "unit": self.unit,
        }
        if self.description:
            d["description"] = self.description
        if self.compu_method:
            d["compu_method"] = {
                "name": self.compu_method.name,
                "type": self.compu_method.compu_type.value,
                "unit": self.compu_method.unit,
                "coeffs": list(self.compu_method.coeffs),
            }
        return d


@dataclass(frozen=True)
class MemorySegment:
    name: str
    address: int
    size: int
    address_extension: int = 0
    description: str = ""


class TransportType(str, Enum):
    SIMULATED = "simulated"
    SOCKETCAN = "socketcan"
    NIXNET = "nixnet"
    PCAN = "pcan"
    VECTOR = "vector"
    ETHERNET = "ethernet"


@dataclass(frozen=True)
class DeviceConfig:
    transport: TransportType = TransportType.SIMULATED
    channel: str = "CAN1"
    bitrate: int = 500_000
    can_fd: bool = False
    can_termination: bool = False
    request_id: int = 0x7E0
    response_id: int = 0x7E8
    response_timeout_ms: int = 250
    max_payload_bytes: int | None = None
    host: str = "127.0.0.1"
    port: int = 0
    description: str = ""

    def validate(self) -> None:
        if self.request_id == self.response_id:
            raise ValueError("request_id and response_id must differ")
        if not (0 <= self.request_id <= 0x1FFFFFFF):
            raise ValueError("request_id out of range")
        if not (0 <= self.response_id <= 0x1FFFFFFF):
            raise ValueError("response_id out of range")
        if self.bitrate <= 0:
            raise ValueError("bitrate must be >0")
        if self.response_timeout_ms <= 0:
            raise ValueError("response_timeout must be >0")

    def to_dict(self) -> dict[str, Any]:
        return {
            "transport": self.transport.value,
            "channel": self.channel,
            "bitrate": self.bitrate,
            "can_fd": self.can_fd,
            "can_termination": self.can_termination,
            "request_id": hex(self.request_id),
            "response_id": hex(self.response_id),
            "response_timeout_ms": self.response_timeout_ms,
            "max_payload_bytes": self.max_payload_bytes,
            "host": self.host,
            "port": self.port,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "DeviceConfig":
        def _parse_can_id(v: Any) -> int:
            if isinstance(v, int):
                return v
            if isinstance(v, str):
                return int(v, 0)
            raise ValueError(f"Invalid CAN ID {v}")

        transport = TransportType(d.get("transport", "simulated"))
        return cls(
            transport=transport,
            channel=d.get("channel", "CAN1"),
            bitrate=int(d.get("bitrate", 500_000)),
            can_fd=bool(d.get("can_fd", False)),
            can_termination=bool(d.get("can_termination", False)),
            request_id=_parse_can_id(d.get("request_id", 0x7E0)),
            response_id=_parse_can_id(d.get("response_id", 0x7E8)),
            response_timeout_ms=int(d.get("response_timeout_ms", 250)),
            max_payload_bytes=d.get("max_payload_bytes"),
            host=d.get("host", "127.0.0.1"),
            port=int(d.get("port", 0)),
            description=d.get("description", ""),
        )


@dataclass(frozen=True)
class RasterConfig:
    name: str
    rate_hz: float
    signals: tuple[str, ...] = field(default_factory=tuple)
    description: str = ""

    def __post_init__(self):
        if self.rate_hz <= 0:
            raise ValueError("rate_hz must be >0")


@dataclass(frozen=True)
class MeasurementBatch:
    sequence: int
    timestamp_ns: int
    rate_hz: float
    values: dict[str, float]
    raster_name: str = "default"
    raw_values: dict[str, float] | None = None

    def to_message(self) -> dict[str, Any]:
        msg: dict[str, Any] = {
            "type": "sample_batch",
            "sequence": self.sequence,
            "timestamp_ns": self.timestamp_ns,
            "rate_hz": self.rate_hz,
            "raster": self.raster_name,
            "values": self.values,
        }
        if self.raw_values:
            msg["raw_values"] = self.raw_values
        return msg


@dataclass(frozen=True)
class RecorderConfig:
    file_path: str
    format: str = "tdms"  # tdms or csv or both
    enable_csv_preview: bool = True
    csv_decimation_hz: float = 50.0
    flush_interval_seconds: float = 1.0
    root_properties: dict[str, Any] = field(default_factory=dict)


@dataclass
class DaqStatus:
    running: bool = False
    recording: bool = False
    rasters: dict[str, dict[str, Any]] = field(default_factory=dict)
    overruns: int = 0
    batches_sent: int = 0
    recording_file: str | None = None
    device_connected: bool = False
    last_error: str | None = None

"""XCP DAQ list allocator and DTO decoder."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ecu_can_daq.models import ByteOrder, MeasurementDefinition


@dataclass(frozen=True)
class OdtEntry:
    measurement: MeasurementDefinition
    offset_in_odt: int = 0  # byte offset inside DTO payload

    @property
    def size(self) -> int:
        return self.measurement.size_bytes


@dataclass
class Odt:
    entries: list[OdtEntry] = field(default_factory=list)
    max_size: int = 7  # max DTO payload (can be larger with CAN-FD)

    @property
    def used_bytes(self) -> int:
        return sum(e.size for e in self.entries)

    def can_fit(self, measurement: MeasurementDefinition) -> bool:
        return self.used_bytes + measurement.size_bytes <= self.max_size

    def add(self, measurement: MeasurementDefinition) -> OdtEntry:
        entry = OdtEntry(measurement=measurement, offset_in_odt=self.used_bytes)
        self.entries.append(entry)
        return entry


@dataclass
class DaqListConfig:
    daq_list_number: int
    event_channel: int  # raster: 0,1, etc.
    rate_hz: float
    odts: list[Odt] = field(default_factory=list)
    is_stim: bool = False
    prescaler: int = 1

    @property
    def num_odts(self) -> int:
        return len(self.odts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "daq_list_number": self.daq_list_number,
            "event_channel": self.event_channel,
            "rate_hz": self.rate_hz,
            "num_odts": self.num_odts,
            "signals": [
                e.measurement.name
                for odt in self.odts
                for e in odt.entries
            ],
        }


class DaqListAllocator:
    """Pack measurements into DAQ lists by raster."""

    def __init__(self, max_dto_payload: int = 7, max_odt_per_daq: int = 16):
        self.max_dto_payload = max_dto_payload
        self.max_odt_per_daq = max_odt_per_daq

    def allocate(
        self,
        rasters: list[tuple[int, float, list[MeasurementDefinition]]],
    ) -> list[DaqListConfig]:
        """
        rasters: list of (event_channel, rate_hz, measurements)
        Returns list of DaqListConfig
        """
        result: list[DaqListConfig] = []
        daq_num = 0
        for event_channel, rate_hz, measurements in rasters:
            # Sort by address for better cache? But DAQ grouping cares about size packing
            # Keep original order for determinism
            current_daq = DaqListConfig(
                daq_list_number=daq_num,
                event_channel=event_channel,
                rate_hz=rate_hz,
                odts=[Odt(max_size=self.max_dto_payload)],
            )
            for meas in measurements:
                # If measurement too large for DTO, need CAN-FD (not supported yet) -> split?
                # For classic CAN, max_dto_payload >=7, largest type is 8 bytes (float64)
                # That would not fit; we allow 8 bytes if max_dto allows, else error for now
                if meas.size_bytes > self.max_dto_payload:
                    # Need to allocate dedicated ODT with larger size or error
                    # We allow up to 8 if max_dto is 7 -> will raise to advise FD
                    raise ValueError(
                        f"Signal {meas.name} size {meas.size_bytes} exceeds max DTO payload {self.max_dto_payload}. "
                        "Use CAN-FD or reduce size."
                    )
                last_odt = current_daq.odts[-1]
                if not last_odt.can_fit(meas):
                    # Need new ODT
                    if len(current_daq.odts) >= self.max_odt_per_daq:
                        # Need new DAQ list for same event
                        result.append(current_daq)
                        daq_num += 1
                        current_daq = DaqListConfig(
                            daq_list_number=daq_num,
                            event_channel=event_channel,
                            rate_hz=rate_hz,
                            odts=[Odt(max_size=self.max_dto_payload)],
                        )
                        last_odt = current_daq.odts[-1]
                    else:
                        new_odt = Odt(max_size=self.max_dto_payload)
                        current_daq.odts.append(new_odt)
                        last_odt = new_odt
                last_odt.add(meas)

            if current_daq.odts and any(odt.entries for odt in current_daq.odts):
                result.append(current_daq)
                daq_num += 1

        return result


class DtoDecoder:
    """Decode DTO packets into measurement values."""

    def __init__(self, daq_lists: list[DaqListConfig]):
        self._pid_to_daq: dict[int, tuple[DaqListConfig, Odt]] = {}
        pid = 0
        for daq in daq_lists:
            for odt in daq.odts:
                self._pid_to_daq[pid] = (daq, odt)
                pid += 1

    @property
    def num_pids(self) -> int:
        return len(self._pid_to_daq)

    def decode_dto(self, payload: bytes) -> dict[str, float] | None:
        """
        DTO format: [PID][data...]
        Returns dict of measurement name -> phys value
        """
        if not payload:
            return None
        pid = payload[0]
        mapping = self._pid_to_daq.get(pid)
        if mapping is None:
            return None
        _, odt = mapping
        data = payload[1:]
        if len(data) < odt.used_bytes:
            # truncated
            return None
        result: dict[str, float] = {}
        offset = 0
        for entry in odt.entries:
            raw_bytes = data[offset : offset + entry.size]
            if len(raw_bytes) != entry.size:
                break
            try:
                result[entry.measurement.name] = entry.measurement.decode(raw_bytes)
            except Exception:
                # Keep raw if decode fails
                pass
            offset += entry.size
        return result

    def decode_dto_raw(self, payload: bytes) -> tuple[int, dict[str, float]] | None:
        """Return (pid, values) or None"""
        if not payload:
            return None
        pid = payload[0]
        mapping = self._pid_to_daq.get(pid)
        if not mapping:
            return None
        daq, odt = mapping
        data = payload[1:]
        result = {}
        offset = 0
        for entry in odt.entries:
            raw_bytes = data[offset : offset + entry.size]
            if len(raw_bytes) != entry.size:
                break
            result[entry.measurement.name] = entry.measurement.decode_raw(raw_bytes)
            offset += entry.size
        return pid, result

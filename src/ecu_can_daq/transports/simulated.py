"""Simulated transport with polling + DAQ DTO simulation."""
from __future__ import annotations

import math
import time
import random
from collections import deque

from ecu_can_daq.models import MeasurementDefinition, DeviceConfig, TransportType, DataType, ByteOrder
from ecu_can_daq.transports.base import MeasurementTransport
from ecu_can_daq.xcp.daq import DaqListConfig, DtoDecoder, Odt


class SimulatedTransport(MeasurementTransport):
    def __init__(self, config: DeviceConfig | None = None, jitter_ms: float = 1.0):
        self._config = config or DeviceConfig(transport=TransportType.SIMULATED)
        self._start_time = 0.0
        self._connected = False
        self._daq_configs: list[DaqListConfig] = []
        self._decoder: DtoDecoder | None = None
        self._dto_sequence = 0
        self._jitter = jitter_ms / 1000.0
        self._values: dict[str, float] = {}
        self._last_daq_time: dict[int, float] = {}

    def connect(self) -> None:
        self._start_time = time.perf_counter()
        self._connected = True
        self._dto_sequence = 0

    def disconnect(self) -> None:
        self._connected = False
        self._daq_configs.clear()
        self._decoder = None

    def read_measurements(
        self, measurements: list[MeasurementDefinition]
    ) -> dict[str, float]:
        elapsed = time.perf_counter() - self._start_time
        values: dict[str, float] = {}
        for index, definition in enumerate(measurements):
            span = definition.upper_limit - definition.lower_limit
            midpoint = definition.lower_limit + (span / 2.0)
            amplitude = span / 2.0 if span else 1.0
            # Use freq based on hash of name
            freq = 1.0 + (hash(definition.name) % 100) / 100.0 * 2.0
            value = midpoint + amplitude * math.sin(elapsed * freq + index * 0.05)
            # Add noise
            value += random.uniform(-span * 0.01, span * 0.01)
            values[definition.name] = round(value, 6)
        self._values.update(values)
        return values

    def supports_daq(self) -> bool:
        return True

    def configure_daq(self, rasters):
        # rasters: list of (event_channel, rate_hz, measurements)
        from ecu_can_daq.xcp.daq import DaqListAllocator

        allocator = DaqListAllocator(max_dto_payload=7)
        daq_configs = allocator.allocate(rasters)
        self._daq_configs = daq_configs
        self._decoder = DtoDecoder(daq_configs)
        self._last_daq_time = {daq.daq_list_number: 0.0 for daq in daq_configs}
        return daq_configs

    def start_daq(self) -> None:
        # Reset timers
        now = time.perf_counter()
        for daq in self._daq_configs:
            self._last_daq_time[daq.daq_list_number] = now

    def stop_daq(self) -> None:
        pass

    def read_dto(self, timeout: float = 0.05) -> list[bytes]:
        # Simulate DAQ DTOs based on elapsed time and raster rates
        if not self._daq_configs or not self._connected:
            time.sleep(min(timeout, 0.002))
            return []
        now = time.perf_counter()
        dtos: list[bytes] = []
        pid = 0
        # Update values first
        all_measurements: list[MeasurementDefinition] = []
        for daq in self._daq_configs:
            for odt in daq.odts:
                for entry in odt.entries:
                    if entry.measurement not in all_measurements:
                        all_measurements.append(entry.measurement)
        self.read_measurements(all_measurements)

        for daq in self._daq_configs:
            period = 1.0 / daq.rate_hz if daq.rate_hz > 0 else 1.0
            last = self._last_daq_time.get(daq.daq_list_number, 0)
            # How many periods elapsed?
            elapsed_since = now - last
            num_fires = int(elapsed_since / period) if period > 0 else 0
            if num_fires <= 0:
                continue
            # For simplicity fire once per call if elapsed
            # Update last time
            self._last_daq_time[daq.daq_list_number] = now

            # Generate DTOs for this DAQ (one per ODT)
            for odt_idx, odt in enumerate(daq.odts):
                # PID mapping: sequential across all DAQs
                # Find PID for this odt
                # Compute PID by iterating daq lists in order
                # Quick: compute from our allocation order
                current_pid = self._compute_pid(daq.daq_list_number, odt_idx)
                # Build payload: [PID][data...]
                payload = bytearray([current_pid])
                for entry in odt.entries:
                    val = self._values.get(entry.measurement.name, 0.0)
                    # Encode as raw then if compu method phys->raw inverse? For sim we just encode phys as raw for simplicity
                    raw = entry.measurement.compu_method.phys_to_raw(val) if entry.measurement.compu_method else val
                    # Encode according to data_type and byte_order
                    try:
                        encoded = entry.measurement.data_type.encode(raw, entry.measurement.byte_order)
                    except Exception:
                        # fallback to 0
                        encoded = entry.measurement.data_type.encode(0.0, entry.measurement.byte_order)
                    # For simulation, if entry size mismatched, pad
                    if len(encoded) != entry.measurement.size_bytes:
                        encoded = encoded[: entry.measurement.size_bytes].ljust(entry.measurement.size_bytes, b"\x00")
                    payload.extend(encoded)
                dtos.append(bytes(payload))
                self._dto_sequence += 1

        # Optional jitter sleep
        if self._jitter > 0:
            time.sleep(random.uniform(0, self._jitter))
        return dtos

    def _compute_pid(self, daq_list_number: int, odt_idx: int) -> int:
        pid = 0
        for daq in self._daq_configs:
            if daq.daq_list_number == daq_list_number:
                return pid + odt_idx
            pid += len(daq.odts)
        return pid

    def decode_dto(self, payloads: list[bytes]) -> dict[str, float]:
        if not self._decoder:
            return {}
        merged: dict[str, float] = {}
        for p in payloads:
            vals = self._decoder.decode_dto(p)
            if vals:
                merged.update(vals)
        return merged

    def get_device_config(self) -> DeviceConfig:
        return self._config

    def set_device_config(self, config: DeviceConfig) -> None:
        self._config = config

    def read_memory(self, address_extension: int, address: int, length: int) -> bytes:
        # Return synthetic memory based on address
        return bytes([((address + i) & 0xFF) for i in range(length)])

    def write_memory(self, address_extension: int, address: int, data: bytes) -> None:
        # Simulated write – no-op
        pass

"""Multi-raster DAQ engine supporting polling and DAQ DTO."""
from __future__ import annotations

import asyncio
import time
import logging
import inspect
from dataclasses import dataclass, field
from typing import Any, Callable

from ecu_can_daq.models import MeasurementDefinition, MeasurementBatch, RasterConfig
from ecu_can_daq.transports.base import MeasurementTransport
from ecu_can_daq.daq.raster import Raster
from ecu_can_daq.daq.ringbuffer import RingBuffer

log = logging.getLogger(__name__)


@dataclass
class DaqEngineConfig:
    rasters: list[RasterConfig] = field(default_factory=list)
    ring_capacity: int = 10000
    use_daq_if_available: bool = True
    enable_soft_raster_sync: bool = True


class DaqEngine:
    """Multi-raster DAQ engine.

    Supports:
    - Polling fallback per raster
    - DAQ lists when transport supports it
    """

    def __init__(
        self,
        transport: MeasurementTransport,
        measurements: list[MeasurementDefinition],
        config: DaqEngineConfig,
    ):
        self._transport = transport
        self._all_measurements = {m.name: m for m in measurements}
        self._config = config
        self._rasters: list[Raster] = []
        self._buffers: dict[str, RingBuffer] = {}
        self._running = False
        self._overruns = 0
        self._batches_sent = 0

        # Build rasters
        for idx, rc in enumerate(config.rasters):
            r_meas = [self._all_measurements[n] for n in rc.signals if n in self._all_measurements]
            if not r_meas:
                log.warning(f"Raster {rc.name} has no resolved measurements")
                continue
            raster = Raster(
                name=rc.name,
                rate_hz=rc.rate_hz,
                measurements=r_meas,
                event_channel=idx,
                description=rc.description,
            )
            self._rasters.append(raster)
            self._buffers[raster.name] = RingBuffer(capacity=config.ring_capacity)

        # If no raster defined, create default from all measurements at 100Hz
        if not self._rasters and measurements:
            raster = Raster(
                name="default",
                rate_hz=100.0,
                measurements=measurements,
                event_channel=0,
            )
            self._rasters.append(raster)
            self._buffers[raster.name] = RingBuffer(capacity=config.ring_capacity)

        self._daq_mode = False

    @property
    def rasters(self) -> list[Raster]:
        return list(self._rasters)

    @property
    def status(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "daq_mode": self._daq_mode,
            "rasters": [r.to_dict() for r in self._rasters],
            "overruns": self._overruns,
            "batches_sent": self._batches_sent,
        }

    async def connect(self) -> None:
        await asyncio.to_thread(self._transport.connect)
        # If transport supports DAQ and config wants it, configure
        if self._config.use_daq_if_available and self._transport.supports_daq():
            try:
                rasters_tuple = [
                    (r.event_channel, r.rate_hz, r.measurements) for r in self._rasters
                ]
                await asyncio.to_thread(self._transport.configure_daq, rasters_tuple)
                self._daq_mode = True
                log.info("DAQ mode enabled")
            except Exception as e:
                log.warning(f"Failed to configure DAQ, falling back to polling: {e}")
                self._daq_mode = False

    async def disconnect(self) -> None:
        if self._daq_mode:
            try:
                await asyncio.to_thread(self._transport.stop_daq)
            except Exception:
                pass
        await asyncio.to_thread(self._transport.disconnect)

    async def start(self) -> None:
        if self._daq_mode:
            await asyncio.to_thread(self._transport.start_daq)

    async def stop(self) -> None:
        if self._daq_mode:
            await asyncio.to_thread(self._transport.stop_daq)

    def get_latest(self, raster_name: str | None = None) -> dict[str, float]:
        if raster_name:
            buf = self._buffers.get(raster_name)
            if buf:
                latest = buf.latest()
                if latest:
                    return latest.get("values", {})
            return {}
        # Merge latest from all rasters
        merged: dict[str, float] = {}
        for r in self._rasters:
            buf = self._buffers.get(r.name)
            if buf and buf.latest():
                merged.update(buf.latest().get("values", {}))
        return merged

    async def run_raster_loop(
        self,
        raster: Raster,
        batch_callback: Callable[[MeasurementBatch], Any],
    ) -> None:
        """Run polling loop for single raster."""
        period = raster.period_s
        seq = 0
        while self._running:
            cycle_start = time.perf_counter()
            try:
                values = await asyncio.to_thread(
                    self._transport.read_measurements, raster.measurements
                )
                batch = MeasurementBatch(
                    sequence=seq,
                    timestamp_ns=time.time_ns(),
                    rate_hz=raster.rate_hz,
                    values=values,
                    raster_name=raster.name,
                )
                self._buffers[raster.name].append(
                    {"timestamp_ns": batch.timestamp_ns, "values": values, "sequence": seq}
                )
                raster.last_values = values
                raster.sequence = seq
                # Callback can be async or sync
                if inspect.iscoroutinefunction(batch_callback):
                    await batch_callback(batch)  # type: ignore
                else:
                    batch_callback(batch)
                seq += 1
                self._batches_sent += 1
            except Exception as e:
                log.error(f"Raster {raster.name} read error: {e}")
                raster.overruns += 1
                self._overruns += 1

            elapsed = time.perf_counter() - cycle_start
            await asyncio.sleep(max(0.0, period - elapsed))

    async def run_daq_loop(
        self,
        batch_callback: Callable[[MeasurementBatch], Any],
    ) -> None:
        """Run DAQ DTO reading loop."""
        # DAQ loop aggregates DTOs and emits batches per raster?
        # For simplicity, we emit batches as values arrive
        raster_map: dict[int, Raster] = {r.event_channel: r for r in self._rasters}
        # Need mapping DAQ list -> raster: we have event_channel == raster event_channel
        seq_per_raster: dict[str, int] = {r.name: 0 for r in self._rasters}
        while self._running:
            try:
                dtos = await asyncio.to_thread(self._transport.read_dto, 0.05)
                if not dtos:
                    await asyncio.sleep(0.001)
                    continue
                values = await asyncio.to_thread(self._transport.decode_dto, dtos)
                if not values:
                    continue
                # Group values by raster? For now we attribute to all rasters that contain signal
                # Emit per raster if any of its signals present
                for raster in self._rasters:
                    raster_values = {k: v for k, v in values.items() if k in [m.name for m in raster.measurements]}
                    if not raster_values:
                        continue
                    batch = MeasurementBatch(
                        sequence=seq_per_raster[raster.name],
                        timestamp_ns=time.time_ns(),
                        rate_hz=raster.rate_hz,
                        values=raster_values,
                        raster_name=raster.name,
                    )
                    self._buffers[raster.name].append(
                        {
                            "timestamp_ns": batch.timestamp_ns,
                            "values": raster_values,
                            "sequence": seq_per_raster[raster.name],
                        }
                    )
                    raster.last_values.update(raster_values)
                    raster.sequence = seq_per_raster[raster.name]
                    seq_per_raster[raster.name] += 1
                    if inspect.iscoroutinefunction(batch_callback):
                        await batch_callback(batch)  # type: ignore
                    else:
                        batch_callback(batch)
                    self._batches_sent += 1
            except Exception as e:
                log.error(f"DAQ loop error: {e}")
                await asyncio.sleep(0.05)

    async def run(
        self,
        batch_callback: Callable[[MeasurementBatch], Any],
        duration_s: float | None = None,
    ) -> None:
        self._running = True
        start = time.perf_counter()
        try:
            await self.connect()
            await self.start()

            if self._daq_mode:
                # Single DAQ loop
                try:
                    if duration_s is not None:
                        await asyncio.wait_for(
                            self.run_daq_loop(batch_callback), timeout=duration_s
                        )
                    else:
                        await self.run_daq_loop(batch_callback)
                except asyncio.TimeoutError:
                    pass
            else:
                # Multi-raster polling loops
                tasks = [
                    asyncio.create_task(self.run_raster_loop(raster, batch_callback))
                    for raster in self._rasters
                ]
                try:
                    if duration_s is not None:
                        await asyncio.wait_for(
                            asyncio.gather(*tasks), timeout=duration_s
                        )
                    else:
                        await asyncio.gather(*tasks)
                except asyncio.TimeoutError:
                    pass
                finally:
                    for t in tasks:
                        t.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            self._running = False
            try:
                await self.stop()
            except Exception:
                pass
            await self.disconnect()

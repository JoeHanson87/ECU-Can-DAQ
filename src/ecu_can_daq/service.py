"""Acquisition services: legacy + enhanced multi-raster."""
from __future__ import annotations

import asyncio
import time
import inspect
from typing import Callable, Any

from ecu_can_daq.models import MeasurementBatch, MeasurementDefinition, RasterConfig, RecorderConfig, DeviceConfig
from ecu_can_daq.streaming import TcpJsonStreamer
from ecu_can_daq.transports.base import MeasurementTransport
from ecu_can_daq.daq.engine import DaqEngine, DaqEngineConfig
from ecu_can_daq.recorder.manager import RecorderManager
from ecu_can_daq.models import DaqStatus


class AcquisitionService:
    """Legacy single-raster service kept for backward compat."""

    def __init__(
        self,
        transport: MeasurementTransport,
        streamer: TcpJsonStreamer,
        measurements: list[MeasurementDefinition],
        rate_hz: float,
    ) -> None:
        if rate_hz <= 0:
            raise ValueError("rate_hz must be greater than zero")
        self._transport = transport
        self._streamer = streamer
        self._measurements = measurements
        self._rate_hz = rate_hz

    async def run(self, duration_seconds: float | None = None) -> None:
        period_seconds = 1.0 / self._rate_hz
        started_at = time.perf_counter()
        sequence = 0
        try:
            await self._streamer.start()
            await asyncio.to_thread(self._transport.connect)
            while True:
                cycle_started_at = time.perf_counter()
                values = await asyncio.to_thread(
                    self._transport.read_measurements, self._measurements
                )
                batch = MeasurementBatch(
                    sequence=sequence,
                    timestamp_ns=time.time_ns(),
                    rate_hz=self._rate_hz,
                    values=values,
                )
                await self._streamer.broadcast(batch)
                sequence += 1

                if (
                    duration_seconds is not None
                    and time.perf_counter() - started_at >= duration_seconds
                ):
                    return

                elapsed = time.perf_counter() - cycle_started_at
                await asyncio.sleep(max(0.0, period_seconds - elapsed))
        finally:
            await asyncio.to_thread(self._transport.disconnect)
            await self._streamer.stop()


class EnhancedAcquisitionService:
    """Full-featured multi-raster service with DAQ, recording, streaming."""

    def __init__(
        self,
        transport: MeasurementTransport,
        measurements: list[MeasurementDefinition],
        rasters: list[RasterConfig],
        streamer: TcpJsonStreamer | None = None,
        recorder_config: RecorderConfig | None = None,
        device_config: DeviceConfig | None = None,
        broadcast_callback: Callable[[MeasurementBatch], Any] | None = None,
    ) -> None:
        self._transport = transport
        self._measurements = measurements
        self._rasters = rasters
        self._streamer = streamer
        self._recorder_config = recorder_config
        self._device_config = device_config
        self._broadcast_callback = broadcast_callback

        self._daq_engine = DaqEngine(
            transport=transport,
            measurements=measurements,
            config=DaqEngineConfig(rasters=rasters, use_daq_if_available=True),
        )
        self._recorder: RecorderManager | None = None
        if recorder_config:
            self._recorder = RecorderManager(recorder_config)

        self._status = DaqStatus()
        self._running = False

    @property
    def status(self) -> DaqStatus:
        # Update from engine
        engine_status = self._daq_engine.status
        self._status.rasters = {r.name: r.to_dict() for r in self._daq_engine.rasters}
        self._status.overruns = engine_status.get("overruns", 0)
        self._status.batches_sent = engine_status.get("batches_sent", 0)
        self._status.device_connected = self._status.device_connected
        return self._status

    async def run(self, duration_seconds: float | None = None) -> None:
        self._running = True
        self._status.running = True
        # Setup streamer
        if self._streamer:
            await self._streamer.start()
            # configure measurements for legacy streamer
            if hasattr(self._streamer, "configure_measurements"):
                self._streamer.configure_measurements(self._measurements)

        # Setup recorder
        if self._recorder:
            root_props = {
                "project": "ECU-Can-DAQ",
                "device": self._device_config.to_dict() if self._device_config else {},
                "rasters": [r.name for r in self._rasters],
            }
            self._recorder.open(root_properties=root_props)
            self._status.recording = True
            if self._recorder_config:
                self._status.recording_file = self._recorder_config.file_path

        async def handle_batch(batch: MeasurementBatch):
            # Broadcast to legacy streamer
            if self._streamer:
                try:
                    await self._streamer.broadcast(batch)
                except Exception:
                    pass
            # Broadcast via callback (for WS)
            if self._broadcast_callback:
                try:
                    if inspect.iscoroutinefunction(self._broadcast_callback):
                        await self._broadcast_callback(batch)  # type: ignore
                    else:
                        self._broadcast_callback(batch)
                except Exception:
                    pass
            # Record
            if self._recorder:
                try:
                    self._recorder.append(batch)
                except Exception:
                    pass

        try:
            await self._daq_engine.run(handle_batch, duration_s=duration_seconds)
        finally:
            self._running = False
            self._status.running = False
            self._status.recording = False
            if self._recorder:
                try:
                    self._recorder.close()
                except Exception:
                    pass
            if self._streamer:
                try:
                    await self._streamer.stop()
                except Exception:
                    pass

    async def stop(self) -> None:
        self._running = False
        self._status.running = False

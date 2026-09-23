from __future__ import annotations

import asyncio
import time

from ecu_can_daq.models import MeasurementBatch, MeasurementDefinition
from ecu_can_daq.streaming import TcpJsonStreamer
from ecu_can_daq.transports.base import MeasurementTransport


class AcquisitionService:
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
        await self._streamer.start()
        await asyncio.to_thread(self._transport.connect)
        try:
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

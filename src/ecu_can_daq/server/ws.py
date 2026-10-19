"""WebSocket manager for live streaming."""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from ecu_can_daq.models import MeasurementBatch
from ecu_can_daq.server.state import get_state


class WebSocketManager:
    def __init__(self):
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self._decimation_hz = 30.0
        self._last_sent: dict[int, float] = {}

    async def connect(self, ws: WebSocket):
        await ws.accept()
        async with self._lock:
            self._clients.add(ws)
        # Send metadata on connect
        state = get_state()
        with state.lock:
            meas = list(state.measurements.values())
            rasters = state.rasters
        if meas:
            await ws.send_text(
                json.dumps(
                    {
                        "type": "metadata",
                        "measurements": [m.to_dict() for m in meas],
                        "rasters": [{"name": r.name, "rate_hz": r.rate_hz, "signals": list(r.signals)} for r in rasters],
                    }
                )
            )

    async def disconnect(self, ws: WebSocket):
        async with self._lock:
            self._clients.discard(ws)
            self._last_sent.pop(id(ws), None)

    async def broadcast_batch(self, batch: MeasurementBatch):
        state = get_state()
        # Update global latest
        with state.lock:
            state.latest_values.update(batch.values)
            state.latest_batches[batch.raster_name] = batch.to_message()

        # Decimation per client
        now = time.monotonic()
        dead: list[WebSocket] = []
        async with self._lock:
            clients = list(self._clients)
        for ws in clients:
            last = self._last_sent.get(id(ws), 0)
            period = 1.0 / self._decimation_hz if self._decimation_hz > 0 else 0.0
            if now - last < period:
                continue
            self._last_sent[id(ws)] = now
            try:
                await ws.send_text(json.dumps(batch.to_message()))
            except (WebSocketDisconnect, RuntimeError, Exception):
                dead.append(ws)

        for ws in dead:
            await self.disconnect(ws)

    async def broadcast_status(self, status: dict[str, Any]):
        async with self._lock:
            clients = list(self._clients)
        dead = []
        msg = json.dumps({"type": "status", **status})
        for ws in clients:
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.disconnect(ws)

    def set_decimation(self, hz: float):
        self._decimation_hz = hz


# Singleton manager
ws_manager = WebSocketManager()


async def ws_batch_callback(batch: MeasurementBatch):
    await ws_manager.broadcast_batch(batch)

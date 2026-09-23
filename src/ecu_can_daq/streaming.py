from __future__ import annotations

import asyncio
import json
from typing import Iterable

from ecu_can_daq.models import MeasurementBatch, MeasurementDefinition


class TcpJsonStreamer:
    def __init__(self, host: str, port: int) -> None:
        self._host = host
        self._port = port
        self._server: asyncio.base_events.Server | None = None
        self._clients: set[asyncio.StreamWriter] = set()
        self._measurement_definitions: list[MeasurementDefinition] = []

    def configure_measurements(
        self, definitions: Iterable[MeasurementDefinition]
    ) -> None:
        self._measurement_definitions = list(definitions)

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle_client, self._host, self._port
        )

    async def stop(self) -> None:
        for client in list(self._clients):
            client.close()
            await client.wait_closed()
        self._clients.clear()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def broadcast(self, batch: MeasurementBatch) -> None:
        payload = json.dumps(batch.to_message(), separators=(",", ":")).encode("utf-8") + b"\n"
        dead_clients: list[asyncio.StreamWriter] = []
        for client in self._clients:
            try:
                client.write(payload)
                await client.drain()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                dead_clients.append(client)
        for client in dead_clients:
            self._clients.discard(client)
            client.close()
            await client.wait_closed()

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self._clients.add(writer)
        try:
            metadata = {
                "type": "metadata",
                "measurements": [
                    definition.to_dict() for definition in self._measurement_definitions
                ],
            }
            writer.write(
                json.dumps(metadata, separators=(",", ":")).encode("utf-8") + b"\n"
            )
            await writer.drain()
            await reader.read()
        finally:
            self._clients.discard(writer)
            writer.close()
            await writer.wait_closed()

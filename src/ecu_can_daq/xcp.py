from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ecu_can_daq.models import MeasurementDefinition


POSITIVE_RESPONSE = 0xFF
NEGATIVE_RESPONSE = 0xFE
CONNECT = 0xFF
DISCONNECT = 0xFE
SET_MTA = 0xF6
UPLOAD = 0xF5


class RequestResponseLink(Protocol):
    def open(self) -> None: ...
    def close(self) -> None: ...
    def exchange(self, payload: bytes) -> bytes: ...


class XcpError(RuntimeError):
    pass


@dataclass(frozen=True)
class MemoryBlock:
    address_extension: int
    start_address: int
    length: int


def _group_measurements(definitions: list[MeasurementDefinition]) -> list[MemoryBlock]:
    ordered = sorted(definitions, key=lambda item: (item.address_extension, item.ecu_address))
    blocks: list[MemoryBlock] = []
    for definition in ordered:
        if not blocks:
            blocks.append(
                MemoryBlock(
                    address_extension=definition.address_extension,
                    start_address=definition.ecu_address,
                    length=definition.size_bytes,
                )
            )
            continue

        last = blocks[-1]
        last_end = last.start_address + last.length
        if (
            last.address_extension == definition.address_extension
            and last_end == definition.ecu_address
        ):
            blocks[-1] = MemoryBlock(
                address_extension=last.address_extension,
                start_address=last.start_address,
                length=last.length + definition.size_bytes,
            )
        else:
            blocks.append(
                MemoryBlock(
                    address_extension=definition.address_extension,
                    start_address=definition.ecu_address,
                    length=definition.size_bytes,
                )
            )
    return blocks


class XcpClient:
    def __init__(
        self, link: RequestResponseLink, max_payload_bytes: int | None = None
    ) -> None:
        self._link = link
        self._max_payload_bytes = max_payload_bytes
        self._connected = False

    def connect(self) -> None:
        self._link.open()
        response = self._exchange(bytes([CONNECT, 0x00]))
        if response[0] != POSITIVE_RESPONSE:
            raise XcpError("ECU rejected XCP CONNECT")
        if self._max_payload_bytes is None:
            max_dto = response[4] if len(response) > 4 else 8
            self._max_payload_bytes = max(1, min(7, max_dto - 1))
        self._connected = True

    def disconnect(self) -> None:
        if not self._connected:
            self._link.close()
            return
        try:
            self._exchange(bytes([DISCONNECT]))
        except (XcpError, TimeoutError, RuntimeError):
            pass
        finally:
            self._connected = False
            self._link.close()

    def read_measurements(
        self, definitions: list[MeasurementDefinition]
    ) -> dict[str, float]:
        memory: dict[tuple[int, int], bytes] = {}
        for block in _group_measurements(definitions):
            payload = self.read_memory(
                block.address_extension, block.start_address, block.length
            )
            for offset, byte in enumerate(payload):
                memory[(block.address_extension, block.start_address + offset)] = bytes([byte])

        result: dict[str, float] = {}
        for definition in definitions:
            raw_bytes = b"".join(
                memory[(definition.address_extension, definition.ecu_address + offset)]
                for offset in range(definition.size_bytes)
            )
            result[definition.name] = definition.decode(raw_bytes)
        return result

    def read_memory(self, address_extension: int, address: int, length: int) -> bytes:
        self._exchange(
            bytes([SET_MTA, 0x00, address_extension & 0xFF])
            + address.to_bytes(4, byteorder="little", signed=False)
        )
        data = bytearray()
        remaining = length
        max_payload_bytes = self._max_payload_bytes or 7
        while remaining > 0:
            chunk_size = min(max_payload_bytes, remaining)
            response = self._exchange(bytes([UPLOAD, chunk_size]))
            if response[0] != POSITIVE_RESPONSE:
                raise XcpError("ECU returned an invalid XCP upload response")
            if len(response) < 1 + chunk_size:
                raise XcpError(
                    f"ECU returned a truncated XCP upload response for {chunk_size} bytes"
                )
            data.extend(response[1 : 1 + chunk_size])
            remaining -= chunk_size
        return bytes(data)

    def _exchange(self, payload: bytes) -> bytes:
        response = self._link.exchange(payload)
        if not response:
            raise XcpError("Received an empty XCP response")
        if response[0] == NEGATIVE_RESPONSE:
            error_code = response[1] if len(response) > 1 else None
            raise XcpError(f"XCP negative response: {error_code}")
        return response

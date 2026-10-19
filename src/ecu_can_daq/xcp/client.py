"""Extended XCP client with DAQ lists, DOWNLOAD, polling fallback."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Any
import struct
import time
import logging

from ecu_can_daq.models import MeasurementDefinition, CharacteristicDefinition
from ecu_can_daq.xcp.errors import XcpError, XcpNegativeResponseError
from ecu_can_daq.xcp.protocol import XcpCommands as CMD
from ecu_can_daq.xcp.daq import DaqListConfig, DaqListAllocator, DtoDecoder

log = logging.getLogger(__name__)

POSITIVE_RESPONSE = CMD.POSITIVE_RESPONSE
NEGATIVE_RESPONSE = CMD.NEGATIVE_RESPONSE
CONNECT = CMD.CONNECT
DISCONNECT = CMD.DISCONNECT
SET_MTA = CMD.SET_MTA
UPLOAD = CMD.UPLOAD


class RequestResponseLink(Protocol):
    def open(self) -> None: ...
    def close(self) -> None: ...
    def exchange(self, payload: bytes) -> bytes: ...
    def read_dto(self, timeout: float = 0.1) -> list[bytes]: ...  # optional for DAQ


@dataclass(frozen=True)
class MemoryBlock:
    address_extension: int
    start_address: int
    length: int


def _group_measurements(definitions: list[MeasurementDefinition]) -> list[MemoryBlock]:
    ordered = sorted(definitions, key=lambda item: (item.address_extension, item.ecu_address))
    blocks: list[MemoryBlock] = []
    for definition in ordered:
        definition_end = definition.ecu_address + definition.size_bytes
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
            and definition.ecu_address <= last_end
        ):
            blocks[-1] = MemoryBlock(
                address_extension=last.address_extension,
                start_address=last.start_address,
                length=max(last_end, definition_end) - last.start_address,
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
    """Extended XCP client supporting DAQ and calibration."""

    def __init__(
        self,
        link: RequestResponseLink,
        max_payload_bytes: int | None = None,
        max_dto_payload: int = 7,
    ) -> None:
        self._link = link
        self._max_payload_bytes = max_payload_bytes
        self._max_dto_payload = max_dto_payload
        self._connected = False
        self._daq_lists: list[DaqListConfig] = []
        self._decoder: DtoDecoder | None = None
        self._protection_status: int = 0

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def daq_lists(self) -> list[DaqListConfig]:
        return list(self._daq_lists)

    # === Basic connection ===

    def connect(self) -> None:
        self._link.open()
        try:
            response = self._exchange(bytes([CMD.CONNECT, 0x00]))
            self._assert_positive(response, "CONNECT")
            # response: [0xFF, resource, comm_mode_basic, max_cto, max_dto LSB, max_dto MSB, ...]
            if len(response) >= 6:
                max_cto = response[3]
                max_dto = response[4] | (response[5] << 8) if len(response) > 5 else response[4]
                if self._max_payload_bytes is None:
                    # CTO payload = max_cto -1 (PID)
                    self._max_payload_bytes = max(1, min(7, max_cto - 1))
                self._max_dto_payload = max(1, min(7, max_dto - 1)) if max_dto else self._max_dto_payload
            self._connected = True
            log.info(f"XCP connected, max_cto_payload={self._max_payload_bytes}, max_dto_payload={self._max_dto_payload}")
        except Exception:
            self._link.close()
            self._connected = False
            raise

    def disconnect(self) -> None:
        if not self._connected:
            try:
                self._link.close()
            except Exception:
                pass
            return
        try:
            self._exchange(bytes([CMD.DISCONNECT]))
        except (XcpError, TimeoutError, RuntimeError):
            pass
        finally:
            self._connected = False
            self._link.close()

    def get_status(self) -> dict[str, Any]:
        resp = self._exchange(bytes([CMD.GET_STATUS]))
        self._assert_positive(resp, "GET_STATUS")
        # Basic parse
        status = {
            "session_status": resp[1] if len(resp) > 1 else 0,
            "protection_status": resp[2] if len(resp) > 2 else 0,
            "raw": resp.hex(),
        }
        self._protection_status = status["protection_status"]
        return status

    def get_comm_mode_info(self) -> dict[str, Any]:
        resp = self._exchange(bytes([CMD.GET_COMM_MODE_INFO]))
        self._assert_positive(resp, "GET_COMM_MODE_INFO")
        # [0xFF, reserved, comm_mode_optional, max_bs, min_st, queue_size, xcp_driver_version]
        info = {
            "comm_mode_optional": resp[2] if len(resp) > 2 else 0,
            "max_bs": resp[3] if len(resp) > 3 else 0,
            "min_st": resp[4] if len(resp) > 4 else 0,
            "queue_size": resp[5] if len(resp) > 5 else 0,
        }
        return info

    # === Polling read (legacy) ===

    def read_measurements(
        self, definitions: list[MeasurementDefinition]
    ) -> dict[str, float]:
        if self._daq_lists and self._decoder:
            # If DAQ active, try reading DAQ first? Fallback polling still valid for mixed mode.
            pass
        memory: dict[tuple[int, int], bytes] = {}
        for block in _group_measurements(definitions):
            payload = self.read_memory(
                block.address_extension, block.start_address, block.length
            )
            for offset, byte in enumerate(payload):
                memory[(block.address_extension, block.start_address + offset)] = bytes([byte])

        result: dict[str, float] = {}
        for definition in definitions:
            try:
                raw_bytes = b"".join(
                    memory[(definition.address_extension, definition.ecu_address + offset)]
                    for offset in range(definition.size_bytes)
                )
            except KeyError as e:
                raise XcpError(f"Missing memory for {definition.name}: {e}") from e
            result[definition.name] = definition.decode(raw_bytes)
        return result

    def read_memory(self, address_extension: int, address: int, length: int) -> bytes:
        self._exchange(
            bytes([CMD.SET_MTA, 0x00, address_extension & 0xFF])
            + address.to_bytes(4, byteorder="little", signed=False)
        )
        data = bytearray()
        remaining = length
        max_payload = self._max_payload_bytes or 7
        while remaining > 0:
            chunk_size = min(max_payload, remaining)
            response = self._exchange(bytes([CMD.UPLOAD, chunk_size]))
            self._assert_positive(response, "UPLOAD")
            if len(response) < 1 + chunk_size:
                raise XcpError(
                    f"ECU returned truncated UPLOAD response for {chunk_size} bytes: got {len(response)-1}"
                )
            data.extend(response[1 : 1 + chunk_size])
            remaining -= chunk_size
        return bytes(data)

    def short_upload(self, address_extension: int, address: int, length: int) -> bytes:
        # SHORT_UPLOAD: [0xF4, length, ext, addr(4)]
        payload = (
            bytes([CMD.SHORT_UPLOAD, length & 0xFF, address_extension & 0xFF])
            + address.to_bytes(4, byteorder="little")
        )
        resp = self._exchange(payload)
        self._assert_positive(resp, "SHORT_UPLOAD")
        if len(resp) < 1 + length:
            raise XcpError("Truncated SHORT_UPLOAD")
        return resp[1 : 1 + length]

    # === Calibration: DOWNLOAD ===

    def download(self, address_extension: int, address: int, data: bytes) -> None:
        self._exchange(
            bytes([CMD.SET_MTA, 0x00, address_extension & 0xFF])
            + address.to_bytes(4, byteorder="little")
        )
        remaining = data
        first = True
        while remaining:
            max_payload = self._max_payload_bytes or 6
            chunk = remaining[:max_payload]
            remaining = remaining[max_payload:]
            if first:
                # DOWNLOAD: [0xF0, length, data...]
                resp = self._exchange(bytes([CMD.DOWNLOAD, len(chunk)]) + chunk)
                first = False
            else:
                resp = self._exchange(bytes([CMD.DOWNLOAD_NEXT, len(chunk)]) + chunk)
            self._assert_positive(resp, "DOWNLOAD")

    def short_download(self, address_extension: int, address: int, data: bytes) -> None:
        if len(data) > 7 - 4 - 1:
            # Need to use DOWNLOAD for larger
            return self.download(address_extension, address, data)
        # SHORT_DOWNLOAD: [0xED, ext, addr(4), length?, data]
        # Format: [CMD, addr_ext, addr(4), data_len?, data...] per spec variant; we use simple
        # We'll construct as [0xED, len, ext, addr(4), data]
        # but many ECUs accept [0xED, ext, addr... + data]
        # Implement generic: payload = [SHORT_DOWNLOAD, ext, addr(4), data]
        payload = (
            bytes([CMD.SHORT_DOWNLOAD, address_extension & 0xFF])
            + address.to_bytes(4, byteorder="little")
            + bytes([len(data)])
            + data
        )
        # Truncate to 8 byte CAN
        if len(payload) > 8:
            # fallback
            return self.download(address_extension, address, data)
        resp = self._exchange(payload)
        self._assert_positive(resp, "SHORT_DOWNLOAD")

    def write_characteristic(self, char: CharacteristicDefinition, value: Any) -> None:
        """Write VALUE characteristic (single)."""
        if char.char_type != char.char_type.__class__.VALUE:
            # still allow for VALUE
            pass
        # Encode via data_type and compu_method inverse
        phys = float(value) if not isinstance(value, (bytes, bytearray)) else 0.0
        raw = phys
        if char.compu_method:
            raw = char.compu_method.phys_to_raw(phys)
        payload = char.data_type.encode(raw, char.byte_order)
        self.download(char.address_extension, char.ecu_address, payload)

    # === DAQ ===

    def get_daq_processor_info(self) -> dict[str, Any]:
        resp = self._exchange(bytes([CMD.GET_DAQ_PROCESSOR_INFO]))
        self._assert_positive(resp, "GET_DAQ_PROCESSOR_INFO")
        # [0xFF, min_daq, max_daq, max_event_channel, ...]
        return {
            "min_daq": resp[1] if len(resp) > 1 else 0,
            "max_daq": resp[2] | (resp[3] << 8) if len(resp) > 3 else 0,
            "max_event_channel": resp[4] | (resp[5] << 8) if len(resp) > 5 else 0,
            "raw": resp.hex(),
        }

    def get_daq_resolution_info(self) -> dict[str, Any]:
        resp = self._exchange(bytes([CMD.GET_DAQ_RESOLUTION_INFO]))
        self._assert_positive(resp, "GET_DAQ_RESOLUTION_INFO")
        return {"raw": resp.hex(), "granularity_odt_entry": resp[1] if len(resp) > 1 else 0}

    def alloc_daq(self, num_daq_lists: int) -> None:
        resp = self._exchange(
            bytes([CMD.ALLOC_DAQ, num_daq_lists & 0xFF, (num_daq_lists >> 8) & 0xFF])
        )
        self._assert_positive(resp, "ALLOC_DAQ")

    def alloc_odt(self, daq_list_number: int, num_odts: int) -> None:
        resp = self._exchange(
            bytes(
                [
                    CMD.ALLOC_ODT,
                    daq_list_number & 0xFF,
                    (daq_list_number >> 8) & 0xFF,
                    num_odts & 0xFF,
                    (num_odts >> 8) & 0xFF,
                ]
            )
        )
        self._assert_positive(resp, "ALLOC_ODT")

    def alloc_odt_entry(self, daq_list_number: int, odt_number: int, num_entries: int) -> None:
        resp = self._exchange(
            bytes(
                [
                    CMD.ALLOC_ODT_ENTRY,
                    daq_list_number & 0xFF,
                    (daq_list_number >> 8) & 0xFF,
                    odt_number & 0xFF,
                    (odt_number >> 8) & 0xFF,
                    num_entries & 0xFF,
                    (num_entries >> 8) & 0xFF,
                ]
            )
        )
        self._assert_positive(resp, "ALLOC_ODT_ENTRY")

    def set_daq_ptr(self, daq_list_number: int, odt_number: int, entry_number: int) -> None:
        resp = self._exchange(
            bytes(
                [
                    CMD.SET_DAQ_PTR,
                    0x00,
                    daq_list_number & 0xFF,
                    (daq_list_number >> 8) & 0xFF,
                    odt_number & 0xFF,
                    (odt_number >> 8) & 0xFF,
                    entry_number & 0xFF,
                    (entry_number >> 8) & 0xFF,
                ]
            )[:8]
        )
        self._assert_positive(resp, "SET_DAQ_PTR")

    def write_daq_entry(
        self,
        bit_offset: int,
        size: int,
        address_extension: int,
        address: int,
    ) -> None:
        # WRITE_DAQ: [0xE1, bit_offset, size, ext, addr(4)] -> 8 bytes
        payload = (
            bytes([CMD.WRITE_DAQ, 0xFF, bit_offset & 0xFF, size & 0xFF, address_extension & 0xFF])
            + address.to_bytes(4, byteorder="little")
        )
        # Ensure 8 bytes
        payload = payload[:8].ljust(8, b"\x00")
        resp = self._exchange(payload)
        self._assert_positive(resp, "WRITE_DAQ")

    def set_daq_list_mode(
        self, daq_list_number: int, mode: int, event_channel: int, prescaler: int = 1, priority: int = 0
    ) -> None:
        payload = bytes(
            [
                CMD.SET_DAQ_LIST_MODE,
                mode & 0xFF,
                daq_list_number & 0xFF,
                (daq_list_number >> 8) & 0xFF,
                event_channel & 0xFF,
                (event_channel >> 8) & 0xFF,
                prescaler & 0xFF,
                priority & 0xFF,
            ]
        )
        resp = self._exchange(payload)
        self._assert_positive(resp, "SET_DAQ_LIST_MODE")

    def start_stop_daq_list(self, daq_list_number: int, start: bool) -> None:
        payload = bytes(
            [
                CMD.START_STOP_DAQ_LIST,
                0x02 if start else 0x00,
                daq_list_number & 0xFF,
                (daq_list_number >> 8) & 0xFF,
            ]
        )
        resp = self._exchange(payload)
        self._assert_positive(resp, "START_STOP_DAQ_LIST")

    def start_stop_synch(self, start: bool) -> None:
        payload = bytes([CMD.START_STOP_SYNCH, 0x02 if start else 0x00])
        resp = self._exchange(payload)
        self._assert_positive(resp, "START_STOP_SYNCH")

    def get_daq_clock(self) -> int:
        resp = self._exchange(bytes([CMD.GET_DAQ_CLOCK]))
        self._assert_positive(resp, "GET_DAQ_CLOCK")
        if len(resp) >= 9:
            # [0xFF, 0-3 reserved, timestamp 4 bytes, ...]
            ts = struct.unpack("<I", resp[4:8])[0]
            # There may also be 64-bit
            if len(resp) >= 12:
                # extended?
                pass
            return ts
        return 0

    # High-level DAQ setup

    def configure_daq_lists(
        self,
        rasters: list[tuple[int, float, list[MeasurementDefinition]]],
        max_dto_payload: int | None = None,
    ) -> list[DaqListConfig]:
        max_dto = max_dto_payload or self._max_dto_payload or 7
        allocator = DaqListAllocator(max_dto_payload=max_dto)
        daq_configs = allocator.allocate(rasters)

        # Free previous
        try:
            self._exchange(bytes([CMD.FREE_DAQ]))
        except Exception:
            pass

        self.alloc_daq(len(daq_configs))
        for daq in daq_configs:
            self.alloc_odt(daq.daq_list_number, len(daq.odts))
            for odt_idx, odt in enumerate(daq.odts):
                self.alloc_odt_entry(daq.daq_list_number, odt_idx, len(odt.entries))
                for entry_idx, entry in enumerate(odt.entries):
                    self.set_daq_ptr(daq.daq_list_number, odt_idx, entry_idx)
                    # WRITE_DAQ: bit_offset 0xFF = byte aligned
                    self.write_daq_entry(
                        bit_offset=0xFF,
                        size=entry.size,
                        address_extension=entry.measurement.address_extension,
                        address=entry.measurement.ecu_address,
                    )
            # Mode: 0x10 = enable, direction, timestamp, no pid? Use default mode
            # Mode byte: bit0=selected, bit4=direction(0=DAQ), bit5=timestamp, etc.
            # For simplicity: mode = 0x10 (DAQ, DTO PID, etc). Common default 0x10
            self.set_daq_list_mode(
                daq_list_number=daq.daq_list_number,
                mode=0x10,
                event_channel=daq.event_channel,
                prescaler=1,
            )

        self._daq_lists = daq_configs
        self._decoder = DtoDecoder(daq_configs)
        log.info(f"Configured {len(daq_configs)} DAQ lists, {self._decoder.num_pids} PIDs")
        return daq_configs

    def start_daq(self) -> None:
        for daq in self._daq_lists:
            self.start_stop_daq_list(daq.daq_list_number, True)
        self.start_stop_synch(True)
        log.info("DAQ started")

    def stop_daq(self) -> None:
        try:
            self.start_stop_synch(False)
        except Exception:
            pass
        for daq in self._daq_lists:
            try:
                self.start_stop_daq_list(daq.daq_list_number, False)
            except Exception:
                pass
        log.info("DAQ stopped")

    def read_dto(self, timeout: float = 0.05) -> list[bytes]:
        if hasattr(self._link, "read_dto"):
            try:
                return self._link.read_dto(timeout)  # type: ignore
            except Exception:
                return []
        return []

    def decode_dto_batch(self, payloads: list[bytes]) -> dict[str, float]:
        if not self._decoder:
            return {}
        merged: dict[str, float] = {}
        for p in payloads:
            vals = self._decoder.decode_dto(p)
            if vals:
                merged.update(vals)
        return merged

    # Helpers

    def _exchange(self, payload: bytes) -> bytes:
        response = self._link.exchange(payload)
        if not response:
            raise XcpError("Received empty XCP response")
        if response[0] == NEGATIVE_RESPONSE:
            code = response[1] if len(response) > 1 else None
            raise XcpNegativeResponseError(code)
        return response

    def _assert_positive(self, resp: bytes, cmd_name: str) -> None:
        if not resp or resp[0] != POSITIVE_RESPONSE:
            raise XcpError(f"{cmd_name} failed: {resp.hex() if resp else 'empty'}")

"""XCP package: extended client with DAQ support."""
from __future__ import annotations

# Backward compat from legacy
from ecu_can_daq.legacy_xcp import (
    POSITIVE_RESPONSE,
    NEGATIVE_RESPONSE,
    CONNECT,
    DISCONNECT,
    SET_MTA,
    UPLOAD,
    RequestResponseLink,
    XcpError,
    MemoryBlock,
    XcpClient as LegacyXcpClient,
)

# New extended
from ecu_can_daq.xcp.errors import XcpError as NewXcpError  # noqa: F401, will be same
from ecu_can_daq.xcp.client import XcpClient  # extended version replaces legacy
from ecu_can_daq.xcp.daq import DaqListConfig, OdtEntry, DaqListAllocator, DtoDecoder
from ecu_can_daq.xcp.protocol import XcpCommands

__all__ = [
    "POSITIVE_RESPONSE",
    "NEGATIVE_RESPONSE",
    "CONNECT",
    "DISCONNECT",
    "SET_MTA",
    "UPLOAD",
    "RequestResponseLink",
    "XcpError",
    "MemoryBlock",
    "XcpClient",
    "LegacyXcpClient",
    "DaqListConfig",
    "OdtEntry",
    "DaqListAllocator",
    "DtoDecoder",
    "XcpCommands",
]

from __future__ import annotations

import re
import shlex
from pathlib import Path

from ecu_can_daq.models import ByteOrder, DataType, MeasurementDefinition


MEASUREMENT_BEGIN_RE = re.compile(r"^/begin\s+MEASUREMENT\b", re.IGNORECASE)
MEASUREMENT_END_RE = re.compile(r"^/end\s+MEASUREMENT\b", re.IGNORECASE)
ECU_ADDRESS_RE = re.compile(r"\bECU_ADDRESS\s+(0x[0-9A-Fa-f]+|\d+)")
ECU_ADDRESS_EXTENSION_RE = re.compile(
    r"\bECU_ADDRESS_EXTENSION\s+(0x[0-9A-Fa-f]+|\d+)"
)
BYTE_ORDER_RE = re.compile(r"\bBYTE_ORDER\s+(MSB_FIRST|MSB_LAST)\b", re.IGNORECASE)


class A2LParseError(ValueError):
    pass


def _parse_number(token: str) -> float:
    return float(token)


def _parse_int(token: str) -> int:
    return int(token, 0)


def _parse_measurement_block(lines: list[str]) -> MeasurementDefinition:
    header_tokens = shlex.split(lines[0], posix=True)
    if len(header_tokens) < 9:
        raise A2LParseError(f"Incomplete measurement header: {lines[0]}")

    name = header_tokens[2]
    data_type = DataType.from_a2l(header_tokens[4])
    lower_limit = _parse_number(header_tokens[-2])
    upper_limit = _parse_number(header_tokens[-1])

    block_text = "\n".join(lines)
    address_match = ECU_ADDRESS_RE.search(block_text)
    if address_match is None:
        raise A2LParseError(f"Measurement {name} does not define ECU_ADDRESS")

    address_extension_match = ECU_ADDRESS_EXTENSION_RE.search(block_text)
    byte_order_match = BYTE_ORDER_RE.search(block_text)

    return MeasurementDefinition(
        name=name,
        data_type=data_type,
        ecu_address=_parse_int(address_match.group(1)),
        lower_limit=lower_limit,
        upper_limit=upper_limit,
        address_extension=_parse_int(address_extension_match.group(1)) if address_extension_match else 0,
        byte_order=ByteOrder.BIG if byte_order_match and byte_order_match.group(1).upper() == "MSB_FIRST" else ByteOrder.LITTLE,
    )


def load_measurements(a2l_path: str | Path) -> dict[str, MeasurementDefinition]:
    path = Path(a2l_path)
    definitions: dict[str, MeasurementDefinition] = {}
    current_block: list[str] = []

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if MEASUREMENT_BEGIN_RE.match(line):
            current_block = [line]
            continue
        if current_block:
            current_block.append(line)
            if MEASUREMENT_END_RE.match(line):
                definition = _parse_measurement_block(current_block)
                definitions[definition.name] = definition
                current_block = []

    return definitions


def resolve_measurements(
    definitions: dict[str, MeasurementDefinition],
    names: list[str],
) -> list[MeasurementDefinition]:
    resolved: list[MeasurementDefinition] = []
    missing = [name for name in names if name not in definitions]
    if missing:
        raise KeyError(f"Measurements not found in A2L file: {', '.join(sorted(missing))}")
    for name in names:
        resolved.append(definitions[name])
    return resolved

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from ecu_can_daq.a2l import load_measurements, resolve_measurements
from ecu_can_daq.service import AcquisitionService
from ecu_can_daq.streaming import TcpJsonStreamer
from ecu_can_daq.transports.nixnet import NiXnetTransport
from ecu_can_daq.transports.simulated import SimulatedTransport


def _load_measurement_names(path: Path) -> list[str]:
    names = []
    for line in path.read_text(encoding="utf-8").splitlines():
        entry = line.strip()
        if entry and not entry.startswith("#"):
            names.append(entry)
    return names


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Acquire ECU measurements from an A2L file and stream them to LabVIEW"
    )
    parser.add_argument("--a2l", required=True, help="Path to the source A2L file")
    parser.add_argument(
        "--measurements",
        required=True,
        help="Text file with one requested measurement name per line",
    )
    parser.add_argument(
        "--transport",
        choices=["simulated", "nixnet"],
        default="simulated",
        help="Measurement transport backend",
    )
    parser.add_argument("--host", default="127.0.0.1", help="TCP bind host")
    parser.add_argument("--port", type=int, default=9500, help="TCP bind port")
    parser.add_argument("--rate-hz", type=float, default=100.0, help="Sample rate in hertz")
    parser.add_argument(
        "--duration",
        type=float,
        default=None,
        help="Optional runtime limit in seconds",
    )
    parser.add_argument("--channel", default="CAN1", help="NI-XNET interface name")
    parser.add_argument(
        "--request-id",
        type=lambda value: int(value, 0),
        default=0x7E0,
        help="XCP CAN identifier used for master requests",
    )
    parser.add_argument(
        "--response-id",
        type=lambda value: int(value, 0),
        default=0x7E8,
        help="XCP CAN identifier used for ECU responses",
    )
    parser.add_argument(
        "--bitrate",
        type=int,
        default=500_000,
        help="CAN bus bitrate in bits per second",
    )
    parser.add_argument(
        "--can-termination",
        action="store_true",
        help="Enable NI-XNET CAN termination on the interface",
    )
    return parser


async def _run(args: argparse.Namespace) -> int:
    definitions = load_measurements(args.a2l)
    measurement_names = _load_measurement_names(Path(args.measurements))
    measurements = resolve_measurements(definitions, measurement_names)
    if not measurements:
        raise ValueError("At least one measurement must be requested")
    if len(measurements) > 200:
        raise ValueError("At most 200 measurements may be requested")

    streamer = TcpJsonStreamer(args.host, args.port)
    streamer.configure_measurements(measurements)

    if args.transport == "simulated":
        transport = SimulatedTransport()
    else:
        transport = NiXnetTransport(
            channel=args.channel,
            request_id=args.request_id,
            response_id=args.response_id,
            bitrate=args.bitrate,
            can_termination=args.can_termination,
        )

    service = AcquisitionService(
        transport=transport,
        streamer=streamer,
        measurements=measurements,
        rate_hz=args.rate_hz,
    )
    await service.run(duration_seconds=args.duration)
    return 0


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    return asyncio.run(_run(args))

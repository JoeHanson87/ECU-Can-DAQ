"""CLI with subcommands: run, serve, inspect, record, init, validate, project."""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
import logging

from ecu_can_daq.a2l import load_measurements, resolve_measurements
from ecu_can_daq.a2l.facade import load_a2l_project
from ecu_can_daq.service import AcquisitionService, EnhancedAcquisitionService
from ecu_can_daq.streaming import TcpJsonStreamer
from ecu_can_daq.transports.nixnet import NiXnetTransport
from ecu_can_daq.transports.simulated import SimulatedTransport
from ecu_can_daq.transports.socketcan import SocketCanTransport
from ecu_can_daq.transports.pcan import PcanTransport
from ecu_can_daq.transports.vector import VectorTransport
from ecu_can_daq.transports.ethernet import EthernetTransport
from ecu_can_daq.models import DeviceConfig, TransportType, RasterConfig, RecorderConfig
from ecu_can_daq.project import Project
from ecu_can_daq.config.schema import ProjectSchema, DeviceConfigSchema, RasterSchema, RecorderSchema

log = logging.getLogger(__name__)


def _load_measurement_names(path: Path) -> list[str]:
    names = []
    for line in path.read_text(encoding="utf-8").splitlines():
        entry = line.strip()
        if entry and not entry.startswith("#"):
            names.append(entry)
    return names


def _build_transport_from_args(args: argparse.Namespace, device_cfg: DeviceConfig | None = None) -> tuple[DeviceConfig, object]:
    """Build transport from args or DeviceConfig."""
    if device_cfg:
        # Use device config
        t = device_cfg.transport
        if t == TransportType.SIMULATED:
            return device_cfg, SimulatedTransport(config=device_cfg)
        if t == TransportType.NIXNET:
            return device_cfg, NiXnetTransport(config=device_cfg)
        if t == TransportType.SOCKETCAN:
            return device_cfg, SocketCanTransport(config=device_cfg)
        if t == TransportType.PCAN:
            return device_cfg, PcanTransport(config=device_cfg, mock=True)
        if t == TransportType.VECTOR:
            return device_cfg, VectorTransport(config=device_cfg, mock=True)
        if t == TransportType.ETHERNET:
            return device_cfg, EthernetTransport(config=device_cfg)
        return device_cfg, SimulatedTransport(config=device_cfg)

    # Legacy args path
    transport_type = getattr(args, "transport", "simulated")
    channel = getattr(args, "channel", "CAN1")
    request_id = getattr(args, "request_id", 0x7E0)
    response_id = getattr(args, "response_id", 0x7E8)
    bitrate = getattr(args, "bitrate", 500_000)
    can_term = getattr(args, "can_termination", False)
    can_fd = getattr(args, "can_fd", False)
    timeout = getattr(args, "response_timeout_ms", 250)

    cfg = DeviceConfig(
        transport=TransportType(transport_type),
        channel=channel,
        bitrate=bitrate,
        can_fd=can_fd,
        can_termination=can_term,
        request_id=request_id,
        response_id=response_id,
        response_timeout_ms=timeout,
    )
    if transport_type == "simulated":
        trans = SimulatedTransport(config=cfg)
    elif transport_type == "nixnet":
        trans = NiXnetTransport(config=cfg)
    elif transport_type == "socketcan":
        trans = SocketCanTransport(config=cfg)
    elif transport_type == "pcan":
        trans = PcanTransport(config=cfg, mock=True)
    elif transport_type == "vector":
        trans = VectorTransport(config=cfg, mock=True)
    elif transport_type == "ethernet":
        trans = EthernetTransport(config=cfg)
    else:
        trans = SimulatedTransport(config=cfg)
    return cfg, trans


def _parse_raster_spec(spec: str) -> tuple[str, float, str]:
    """
    Parse raster spec like "EngineSpeed@100Hz" or "EngineSpeed@100" or "EngineSpeed:100Hz" or "100Hz:EngineSpeed,Throttle"
    Returns (signal_name, rate_hz, raster_name)
    For simplicity we support two formats:
    - "Signal@100" -> signal EngineSpeed at 100Hz in raster "100Hz"
    - "100Hz:Sig1,Sig2" not implemented; we have separate arg --rasters yaml
    We'll handle simple.
    """
    # Placeholder
    return ("", 0.0, "")


async def _run(args: argparse.Namespace) -> int:
    """Legacy run path used by tests."""
    definitions = load_measurements(args.a2l)
    measurement_names = _load_measurement_names(Path(args.measurements))
    measurements = resolve_measurements(definitions, measurement_names)
    if not measurements:
        raise ValueError("At least one measurement must be requested")
    if len(measurements) > 200:
        raise ValueError("At most 200 measurements may be requested")

    streamer = TcpJsonStreamer(args.host, args.port)
    streamer.configure_measurements(measurements)

    _, transport = _build_transport_from_args(args)

    service = AcquisitionService(
        transport=transport,
        streamer=streamer,
        measurements=measurements,
        rate_hz=args.rate_hz,
    )
    await service.run(duration_seconds=args.duration)
    return 0


# New command handlers

async def _run_project(args: argparse.Namespace) -> int:
    project_path = Path(args.project)
    project = Project.load(project_path)

    # Load A2L via facade
    a2l_proj = load_a2l_project(project.a2l_file)
    measurements_dict = a2l_proj.measurements
    # If measurements_file exists, use it to filter, else use rasters signals
    requested_names: list[str] = []
    if project.measurements_file and project.measurements_file.exists():
        requested_names = _load_measurement_names(project.measurements_file)
    else:
        for raster in project.rasters:
            requested_names.extend(raster.signals)
    requested_names = list(dict.fromkeys(requested_names))  # dedup preserve order

    if not requested_names:
        # Use all measurements up to 200 for backward compat
        requested_names = list(measurements_dict.keys())[:200]

    resolved = resolve_measurements(measurements_dict, requested_names)

    # Build transport
    device_cfg, transport = _build_transport_from_args(argparse.Namespace(), device_cfg=project.device)

    # Build rasters – if none in project, create default
    if not project.rasters:
        project.rasters = [RasterConfig(name="default", rate_hz=100.0, signals=tuple(requested_names))]

    # Streamer
    streamer = TcpJsonStreamer(project.host, project.port)
    streamer.configure_measurements(resolved)

    # Recorder
    recorder_cfg = project.recorder
    if args.record:
        recorder_cfg = RecorderConfig(file_path=args.record, format="both", enable_csv_preview=True)
    elif project.recorder is None and args.record is None:
        # No recording unless requested
        recorder_cfg = None

    # If web server requested, run both DAQ and web server
    if args.web:
        from ecu_can_daq.server.app import create_app
        from ecu_can_daq.server.state import get_state
        from ecu_can_daq.server.ws import ws_manager

        state = get_state()
        with state.lock:
            state.device_config = device_cfg
            state.measurements = measurements_dict
            state.rasters = project.rasters
            state.recorder_config = recorder_cfg
            state.project_path = str(project_path)
            state.a2l_path = str(project.a2l_file)
            state.transport = transport

        # Broadcast callback for WS
        async def ws_callback(batch):
            await ws_manager.broadcast_batch(batch)

        service = EnhancedAcquisitionService(
            transport=transport,
            measurements=resolved,
            rasters=project.rasters,
            streamer=streamer,
            recorder_config=recorder_cfg,
            device_config=device_cfg,
            broadcast_callback=ws_callback,
        )
        with state.lock:
            state.acquisition_service = service

        # Run DAQ + web server concurrently
        import uvicorn

        app = create_app()

        async def run_daq():
            await service.run(duration_seconds=args.duration)

        async def run_web():
            config = uvicorn.Config(app, host=project.web_host, port=project.web_port, log_level="info")
            server = uvicorn.Server(config)
            await server.serve()

        await asyncio.gather(run_daq(), run_web())
    else:
        # Just DAQ without web, but with optional recorder
        service = EnhancedAcquisitionService(
            transport=transport,
            measurements=resolved,
            rasters=project.rasters,
            streamer=streamer,
            recorder_config=recorder_cfg,
            device_config=device_cfg,
        )
        await service.run(duration_seconds=args.duration)

    return 0


async def _run_run(args: argparse.Namespace) -> int:
    # Handles `ecu-can-daq run --a2l ... --measurements ...` or --project
    if args.project:
        # delegate to project runner
        class Temp:
            pass
        t = Temp()
        t.project = args.project
        t.record = args.record
        t.web = args.web
        t.duration = args.duration
        return await _run_project(t)  # type: ignore

    # Legacy style but with new engine
    a2l_path = Path(args.a2l)
    measurements_path = Path(args.measurements) if args.measurements else None

    a2l_proj = load_a2l_project(a2l_path)
    definitions = a2l_proj.measurements

    if measurements_path:
        names = _load_measurement_names(measurements_path)
    else:
        # If rasters spec provided
        names = []
        if args.raster:
            # parse raster spec like "EngineSpeed@100Hz,Throttle@10Hz"
            # We'll split by comma
            for part in args.raster.split(","):
                part = part.strip()
                if "@" in part:
                    sig, rate_part = part.split("@", 1)
                    names.append(sig.strip())
                elif ":" in part:
                    # raster:signals
                    _, sigs = part.split(":", 1)
                    names.extend([s.strip() for s in sigs.split(",") if s.strip()])
                else:
                    names.append(part)
        if not names:
            names = list(definitions.keys())[:50]

    measurements = resolve_measurements(definitions, names)

    device_cfg, transport = _build_transport_from_args(args)

    # Rasters
    rasters: list[RasterConfig] = []
    if args.raster:
        # Group signals by rate
        rate_to_sigs: dict[float, list[str]] = {}
        for part in args.raster.split(","):
            part = part.strip()
            if not part:
                continue
            if "@" in part:
                sig, rate_str = part.split("@", 1)
                rate_str = rate_str.lower().replace("hz", "")
                try:
                    rate = float(rate_str)
                except ValueError:
                    rate = 100.0
                rate_to_sigs.setdefault(rate, []).append(sig.strip())
            else:
                rate_to_sigs.setdefault(100.0, []).append(part.strip())
        for rate, sigs in rate_to_sigs.items():
            rasters.append(RasterConfig(name=f"{int(rate)}Hz", rate_hz=rate, signals=tuple(sigs)))
    else:
        rasters = [RasterConfig(name="default", rate_hz=args.rate_hz, signals=tuple(m.name for m in measurements))]

    streamer = TcpJsonStreamer(args.host, args.port)
    streamer.configure_measurements(measurements)

    recorder_cfg = None
    if args.record:
        recorder_cfg = RecorderConfig(file_path=args.record, format="both", enable_csv_preview=True)

    if args.web:
        from ecu_can_daq.server.app import create_app
        from ecu_can_daq.server.state import get_state
        from ecu_can_daq.server.ws import ws_manager, ws_batch_callback

        state = get_state()
        with state.lock:
            state.device_config = device_cfg
            state.measurements = definitions
            state.rasters = rasters
            state.recorder_config = recorder_cfg
            state.a2l_path = str(a2l_path)

        async def ws_cb(batch):
            await ws_manager.broadcast_batch(batch)

        service = EnhancedAcquisitionService(
            transport=transport,
            measurements=measurements,
            rasters=rasters,
            streamer=streamer,
            recorder_config=recorder_cfg,
            device_config=device_cfg,
            broadcast_callback=ws_cb,
        )
        with state.lock:
            state.acquisition_service = service

        import uvicorn
        app = create_app()

        async def run_daq_inner():
            await service.run(duration_seconds=args.duration)

        async def run_web_inner():
            config = uvicorn.Config(app, host=args.web_host, port=args.web_port, log_level="info")
            server = uvicorn.Server(config)
            await server.serve()

        await asyncio.gather(run_daq_inner(), run_web_inner())
    else:
        service = EnhancedAcquisitionService(
            transport=transport,
            measurements=measurements,
            rasters=rasters,
            streamer=streamer,
            recorder_config=recorder_cfg,
            device_config=device_cfg,
        )
        await service.run(duration_seconds=args.duration)

    return 0


async def _run_serve(args: argparse.Namespace) -> int:
    # Serve dashboard without DAQ running initially, but with loaded A2L if provided
    from ecu_can_daq.server.app import create_app
    from ecu_can_daq.server.state import get_state
    import uvicorn

    state = get_state()
    if args.a2l:
        try:
            a2l_proj = load_a2l_project(args.a2l)
            with state.lock:
                state.measurements = a2l_proj.measurements
                state.a2l_path = str(args.a2l)
                if args.project:
                    state.project_path = str(args.project)
        except Exception as e:
            log.warning(f"Failed to load A2L for serve: {e}")

    if args.project:
        try:
            proj = Project.load(args.project)
            with state.lock:
                state.device_config = proj.device
                state.rasters = proj.rasters
                state.a2l_path = str(proj.a2l_file)
                state.project_path = str(args.project)
        except Exception as e:
            log.warning(f"Failed to load project: {e}")

    app = create_app()
    config = uvicorn.Config(app, host=args.host, port=args.port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()
    return 0


def _cmd_inspect(args: argparse.Namespace) -> int:
    a2l_path = Path(args.a2l)
    proj = load_a2l_project(a2l_path)
    print(f"A2L: {a2l_path}")
    print(f"Modules: {proj.modules}")
    print(f"Measurements: {len(proj.measurements)}")
    for name, m in list(proj.measurements.items())[: args.limit]:
        print(f"  {name}: {m.data_type.value} @ {hex(m.ecu_address)} [{m.lower_limit}..{m.upper_limit}] unit={m.unit} compu={m.compu_method.compu_type.value if m.compu_method else 'none'}")
    if len(proj.measurements) > args.limit:
        print(f"  ... and {len(proj.measurements)-args.limit} more")
    print(f"Characteristics: {len(proj.characteristics)}")
    for name, c in list(proj.characteristics.items())[: args.limit]:
        print(f"  {name}: {c.char_type.value} {c.data_type.value} @ {hex(c.ecu_address)}")
    print(f"CompuMethods: {len(proj.compu_methods)}")
    for name in list(proj.compu_methods.keys())[: args.limit]:
        print(f"  {name}: {proj.compu_methods[name].compu_type.value}")
    return 0


def _cmd_init(args: argparse.Namespace) -> int:
    # Create sample project
    from ecu_can_daq.models import DeviceConfig, TransportType, RasterConfig, RecorderConfig
    import yaml

    a2l_file = args.a2l or "examples/sample.a2l"
    # Try to infer rasters from measurements file if given
    signals = []
    if args.measurements:
        try:
            signals = _load_measurement_names(Path(args.measurements))
        except Exception:
            pass
    if not signals:
        signals = ["EngineSpeed", "Throttle", "CoolantTemp"]

    rasters = [
        RasterConfig(name="100Hz", rate_hz=100.0, signals=tuple(signals)),
        RasterConfig(name="10Hz", rate_hz=10.0, signals=tuple(signals[:1])),
    ]

    device = DeviceConfig(
        transport=TransportType(args.transport),
        channel=args.channel,
        bitrate=args.bitrate,
        request_id=args.request_id,
        response_id=args.response_id,
        can_termination=args.can_termination,
    )

    recorder = RecorderConfig(file_path="recording.tdms", format="both", enable_csv_preview=True)

    project_dict = {
        "name": "ECU DAQ Project",
        "a2l_file": str(a2l_file),
        "device": {
            "transport": device.transport.value,
            "channel": device.channel,
            "bitrate": device.bitrate,
            "can_termination": device.can_termination,
            "request_id": hex(device.request_id),
            "response_id": hex(device.response_id),
        },
        "rasters": [{"name": r.name, "rate_hz": r.rate_hz, "signals": list(r.signals)} for r in rasters],
        "recorder": {"file_path": recorder.file_path, "format": recorder.format, "enable_csv_preview": True},
        "host": "127.0.0.1",
        "port": 9500,
        "web_host": "127.0.0.1",
        "web_port": 8000,
    }

    out_path = Path(args.output)
    if out_path.suffix.lower() in (".yaml", ".yml"):
        out_path.write_text(yaml.safe_dump(project_dict, sort_keys=False), encoding="utf-8")
    else:
        import json

        out_path.write_text(json.dumps(project_dict, indent=2), encoding="utf-8")
    print(f"Project created at {out_path}")
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    path = Path(args.project)
    try:
        proj = Project.load(path)
        print(f"Project {path} valid")
        print(f"  A2L: {proj.a2l_file} exists={proj.a2l_file.exists()}")
        print(f"  Device: {proj.device.transport.value} {proj.device.channel}")
        print(f"  Rasters: {len(proj.rasters)}")
        for r in proj.rasters:
            print(f"    {r.name} {r.rate_hz}Hz {len(r.signals)} signals")
        # Try load A2L
        a2l_proj = load_a2l_project(proj.a2l_file)
        missing = []
        all_sigs = set()
        for r in proj.rasters:
            all_sigs.update(r.signals)
        for sig in all_sigs:
            if sig not in a2l_proj.measurements:
                missing.append(sig)
        if missing:
            print(f"  Missing signals in A2L: {missing}")
            return 1
        print("  All signals found in A2L")
        return 0
    except Exception as e:
        print(f"Validation failed: {e}", file=sys.stderr)
        return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="ECU-Can-DAQ - CANape-class DAQ and calibration tool",
        prog="ecu-can-daq",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")

    # Backward compat: if no subcommand, parse legacy args
    # We will add legacy args as optional top-level to detect
    parser.add_argument("--a2l", help="Path to A2L file (legacy or for subcommands)")
    parser.add_argument(
        "--measurements",
        help="Text file with measurement names (legacy)",
    )
    parser.add_argument(
        "--transport",
        choices=["simulated", "nixnet", "socketcan", "pcan", "vector", "ethernet"],
        default="simulated",
        help="Transport backend",
    )
    parser.add_argument("--host", default="127.0.0.1", help="TCP bind host")
    parser.add_argument("--port", type=int, default=9500, help="TCP bind port")
    parser.add_argument("--rate-hz", type=float, default=100.0, help="Sample rate")
    parser.add_argument("--duration", type=float, default=None, help="Duration seconds")
    parser.add_argument("--channel", default="CAN1", help="CAN channel")
    parser.add_argument(
        "--request-id",
        type=lambda v: int(v, 0),
        default=0x7E0,
        help="XCP request ID",
    )
    parser.add_argument(
        "--response-id",
        type=lambda v: int(v, 0),
        default=0x7E8,
        help="XCP response ID",
    )
    parser.add_argument("--bitrate", type=int, default=500_000, help="CAN bitrate")
    parser.add_argument("--can-termination", action="store_true", help="CAN termination")
    parser.add_argument("--can-fd", action="store_true", help="Enable CAN-FD")
    parser.add_argument("--response-timeout-ms", type=int, default=250)
    parser.add_argument("--raster", help='Raster spec like "EngineSpeed@100Hz,Throttle@10Hz"')
    parser.add_argument("--record", help="Record to TDMS file (e.g. out.tdms)")
    parser.add_argument("--web", action="store_true", help="Enable web dashboard")

    subparsers = parser.add_subparsers(dest="command", help="Subcommands")

    # run
    run_parser = subparsers.add_parser("run", help="Run DAQ acquisition")
    run_parser.add_argument("--a2l", help="A2L file")
    run_parser.add_argument("--measurements", help="Measurements txt")
    run_parser.add_argument("--project", help="Project YAML/JSON")
    run_parser.add_argument("--transport", choices=["simulated", "nixnet", "socketcan", "pcan", "vector", "ethernet"], default="simulated")
    run_parser.add_argument("--channel", default="CAN1")
    run_parser.add_argument("--request-id", type=lambda v: int(v, 0), default=0x7E0)
    run_parser.add_argument("--response-id", type=lambda v: int(v, 0), default=0x7E8)
    run_parser.add_argument("--bitrate", type=int, default=500_000)
    run_parser.add_argument("--can-termination", action="store_true")
    run_parser.add_argument("--can-fd", action="store_true")
    run_parser.add_argument("--host", default="127.0.0.1")
    run_parser.add_argument("--port", type=int, default=9500)
    run_parser.add_argument("--web-host", default="127.0.0.1")
    run_parser.add_argument("--web-port", type=int, default=8000)
    run_parser.add_argument("--rate-hz", type=float, default=100.0)
    run_parser.add_argument("--raster", help='Raster spec like "EngineSpeed@100Hz"')
    run_parser.add_argument("--record", help="TDMS file")
    run_parser.add_argument("--duration", type=float, default=None)
    run_parser.add_argument("--web", action="store_true", help="Enable web dashboard")
    run_parser.add_argument("--response-timeout-ms", type=int, default=250)

    # serve
    serve_parser = subparsers.add_parser("serve", help="Serve web dashboard")
    serve_parser.add_argument("--a2l", help="Optional A2L for signal list")
    serve_parser.add_argument("--project", help="Optional project file")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8000)

    # inspect
    inspect_parser = subparsers.add_parser("inspect", help="Inspect A2L file")
    inspect_parser.add_argument("--a2l", required=True)
    inspect_parser.add_argument("--limit", type=int, default=20, help="Limit per category")

    # init
    init_parser = subparsers.add_parser("init", help="Init new project")
    init_parser.add_argument("--a2l", help="A2L file")
    init_parser.add_argument("--measurements", help="Measurements txt")
    init_parser.add_argument("--transport", default="simulated", choices=["simulated", "nixnet", "socketcan", "pcan", "vector", "ethernet"])
    init_parser.add_argument("--channel", default="CAN1")
    init_parser.add_argument("--request-id", type=lambda v: int(v, 0), default=0x7E0)
    init_parser.add_argument("--response-id", type=lambda v: int(v, 0), default=0x7E8)
    init_parser.add_argument("--bitrate", type=int, default=500_000)
    init_parser.add_argument("--can-termination", action="store_true")
    init_parser.add_argument("--output", "-o", default="project.yaml", help="Output project file")

    # validate
    validate_parser = subparsers.add_parser("validate", help="Validate project")
    validate_parser.add_argument("--project", required=True)

    # record (shortcut)
    record_parser = subparsers.add_parser("record", help="Record DAQ to TDMS")
    record_parser.add_argument("--a2l", required=True)
    record_parser.add_argument("--measurements", help="Measurements file")
    record_parser.add_argument("--project", help="Project file")
    record_parser.add_argument("--transport", default="simulated", choices=["simulated", "nixnet", "socketcan", "pcan", "vector", "ethernet"])
    record_parser.add_argument("--channel", default="CAN1")
    record_parser.add_argument("--request-id", type=lambda v: int(v, 0), default=0x7E0)
    record_parser.add_argument("--response-id", type=lambda v: int(v, 0), default=0x7E8)
    record_parser.add_argument("--bitrate", type=int, default=500_000)
    record_parser.add_argument("--can-termination", action="store_true")
    record_parser.add_argument("--rate-hz", type=float, default=100.0)
    record_parser.add_argument("--raster", help="Raster spec")
    record_parser.add_argument("--output", "-o", required=True, help="Output TDMS file")
    record_parser.add_argument("--duration", type=float, default=None)
    record_parser.add_argument("--host", default="127.0.0.1")
    record_parser.add_argument("--port", type=int, default=9500)

    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    # If subcommand present, dispatch
    if args.command == "run":
        return asyncio.run(_run_run(args))
    if args.command == "serve":
        return asyncio.run(_run_serve(args))
    if args.command == "inspect":
        return _cmd_inspect(args)
    if args.command == "init":
        return _cmd_init(args)
    if args.command == "validate":
        return _cmd_validate(args)
    if args.command == "record":
        # Map to run with record
        class RArgs:
            pass
        r = RArgs()
        r.a2l = args.a2l
        r.measurements = args.measurements
        r.project = args.project
        r.transport = args.transport
        r.channel = args.channel
        r.request_id = args.request_id
        r.response_id = args.response_id
        r.bitrate = args.bitrate
        r.can_termination = args.can_termination
        r.can_fd = False
        r.host = args.host
        r.port = args.port
        r.web_host = "127.0.0.1"
        r.web_port = 8000
        r.rate_hz = args.rate_hz
        r.raster = args.raster
        r.record = args.output
        r.duration = args.duration
        r.web = False
        r.response_timeout_ms = 250
        return asyncio.run(_run_run(r))

    # No subcommand: legacy path or infer run
    # If legacy required args present, use _run
    if args.a2l and args.measurements and not args.command:
        # Backward compat for tests
        return asyncio.run(_run(args))

    # If a2l provided without measurements but with other flags, treat as run
    if args.a2l and not args.command:
        return asyncio.run(_run_run(args))

    # If project file provided as positional? Check if file exists as first arg leftover?
    # Show help
    parser.print_help()
    return 0

"""REST API endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Any
import logging
from pathlib import Path

from ecu_can_daq.server.state import get_state
from ecu_can_daq.config.schema import DeviceConfigSchema, RasterSchema, RecorderSchema
from ecu_can_daq.models import RasterConfig, RecorderConfig

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


class StatusResponse(BaseModel):
    running: bool
    recording: bool
    recording_file: str | None = None
    device_connected: bool
    rasters: dict[str, Any] = {}
    overruns: int = 0
    batches_sent: int = 0


@router.get("/status")
async def get_status():
    state = get_state()
    with state.lock:
        status = state.daq_status
        return {
            "running": status.running,
            "recording": status.recording,
            "recording_file": status.recording_file,
            "device_connected": status.device_connected,
            "rasters": status.rasters,
            "overruns": status.overruns,
            "batches_sent": status.batches_sent,
            "latest_values": dict(state.latest_values),
            "a2l_path": state.a2l_path,
            "project_path": state.project_path,
        }


@router.get("/signals")
async def get_signals():
    state = get_state()
    with state.lock:
        signals = [m.to_dict() for m in state.measurements.values()]
        return {"signals": signals}


@router.get("/rasters")
async def get_rasters():
    state = get_state()
    with state.lock:
        return {"rasters": [{"name": r.name, "rate_hz": r.rate_hz, "signals": list(r.signals)} for r in state.rasters]}


@router.post("/rasters")
async def set_rasters(rasters: list[RasterSchema]):
    state = get_state()
    # Validate signals exist?
    with state.lock:
        new_rasters = [r.to_raster_config() for r in rasters]
        state.rasters = new_rasters
        state.daq_status.rasters = {r.name: {"name": r.name, "rate_hz": r.rate_hz, "signals": list(r.signals)} for r in new_rasters}
    return {"rasters": [r.name for r in new_rasters]}


@router.get("/devices")
async def get_devices():
    return {
        "transports": ["simulated", "nixnet", "socketcan", "pcan", "vector", "ethernet"],
        "available": {
            "simulated": True,
            "nixnet": False,  # check later
            "socketcan": True,
            "pcan": False,
            "vector": False,
            "ethernet": True,
        },
    }


@router.get("/devices/ni/interfaces")
async def get_ni_interfaces():
    # Try to list NI interfaces if driver available
    interfaces = ["CAN1", "CAN2"]
    try:
        import nixnet

        # Attempt to get system interfaces – best effort mock
        # nixnet does not have direct list API; return defaults
        interfaces = ["CAN1", "CAN2", "CAN3", "CAN4"]
    except Exception:
        pass
    return {"interfaces": interfaces}


@router.get("/device/config")
async def get_device_config():
    state = get_state()
    with state.lock:
        return state.device_config.to_dict()


@router.put("/device/config")
async def set_device_config(cfg: DeviceConfigSchema):
    state = get_state()
    # Check if running
    with state.lock:
        if state.daq_status.running:
            raise HTTPException(status_code=409, detail="Cannot change device config while acquiring")
    try:
        device = cfg.to_device_config()
        device.validate()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    with state.lock:
        state.device_config = device
        if state.transport and hasattr(state.transport, "set_device_config"):
            try:
                state.transport.set_device_config(device)
            except Exception as e:
                log.warning(f"Failed to apply device config to transport: {e}")

    return device.to_dict()


class TestConnectionResponse(BaseModel):
    success: bool
    message: str
    details: dict[str, Any] = {}


@router.post("/device/test", response_model=TestConnectionResponse)
async def test_device_connection():
    state = get_state()
    with state.lock:
        transport = state.transport
        cfg = state.device_config

    if transport is None:
        # Build temporary transport based on config
        from ecu_can_daq.transports.simulated import SimulatedTransport
        from ecu_can_daq.transports.nixnet import NiXnetTransport
        from ecu_can_daq.transports.socketcan import SocketCanTransport
        from ecu_can_daq.transports.pcan import PcanTransport
        from ecu_can_daq.transports.vector import VectorTransport
        from ecu_can_daq.transports.ethernet import EthernetTransport
        from ecu_can_daq.models import TransportType

        try:
            if cfg.transport == TransportType.SIMULATED:
                transport = SimulatedTransport(config=cfg)
            elif cfg.transport == TransportType.NIXNET:
                transport = NiXnetTransport(config=cfg)
            elif cfg.transport == TransportType.SOCKETCAN:
                transport = SocketCanTransport(config=cfg)
            elif cfg.transport == TransportType.PCAN:
                transport = PcanTransport(config=cfg, mock=True)
            elif cfg.transport == TransportType.VECTOR:
                transport = VectorTransport(config=cfg, mock=True)
            elif cfg.transport == TransportType.ETHERNET:
                transport = EthernetTransport(config=cfg)
            else:
                transport = SimulatedTransport(config=cfg)
        except Exception as e:
            return TestConnectionResponse(success=False, message=f"Failed to create transport: {e}")

    try:
        import asyncio

        await asyncio.to_thread(transport.connect)
        # Try get status if XCP
        details = {}
        try:
            from ecu_can_daq.transports.base import MeasurementTransport

            # If transport has _xcp attribute
            xcp = getattr(transport, "_xcp", None)
            if xcp and hasattr(xcp, "get_status"):
                details = await asyncio.to_thread(xcp.get_status)
        except Exception as e:
            details["status_error"] = str(e)

        await asyncio.to_thread(transport.disconnect)
        return TestConnectionResponse(success=True, message="Connection successful", details=details)
    except Exception as e:
        try:
            await asyncio.to_thread(transport.disconnect)
        except Exception:
            pass
        return TestConnectionResponse(success=False, message=str(e), details={})


@router.get("/project")
async def get_project():
    state = get_state()
    with state.lock:
        return {
            "project_path": state.project_path,
            "a2l_path": state.a2l_path,
            "device": state.device_config.to_dict(),
            "rasters": [{"name": r.name, "rate_hz": r.rate_hz, "signals": list(r.signals)} for r in state.rasters],
        }


class RecordRequest(BaseModel):
    file_path: str = "recording.tdms"
    format: str = "both"
    enable_csv_preview: bool = True
    csv_decimation_hz: float = 50.0


@router.post("/daq/start")
async def start_daq(req: RecordRequest | None = None):
    state = get_state()
    with state.lock:
        if state.daq_status.running:
            raise HTTPException(status_code=409, detail="Already running")
        if not state.measurements:
            raise HTTPException(status_code=400, detail="No measurements loaded")
        # Set recorder if requested
        if req:
            from ecu_can_daq.models import RecorderConfig

            state.recorder_config = RecorderConfig(
                file_path=req.file_path,
                format=req.format,
                enable_csv_preview=req.enable_csv_preview,
                csv_decimation_hz=req.csv_decimation_hz,
            )
    # This endpoint would trigger acquisition via background task – scaffold
    return {"status": "start requested, use CLI or manage via service lifecycle"}


@router.post("/daq/stop")
async def stop_daq():
    state = get_state()
    with state.lock:
        if not state.daq_status.running:
            return {"status": "not running"}
    # Signal stop
    if state.acquisition_service:
        try:
            import asyncio

            await state.acquisition_service.stop()
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    return {"status": "stop requested"}

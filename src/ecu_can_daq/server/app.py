"""FastAPI app factory for CANape-style dashboard."""
from __future__ import annotations

import asyncio
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import logging

from ecu_can_daq.server.rest import router as rest_router
from ecu_can_daq.server.ws import ws_manager
from ecu_can_daq.server.state import get_state

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("ECU-Can-DAQ web server startup")
    yield
    log.info("ECU-Can-DAQ web server shutdown")
    state = get_state()
    if state.acquisition_service:
        try:
            await state.acquisition_service.stop()
        except Exception:
            pass


def create_app(static_dir: Path | None = None) -> FastAPI:
    app = FastAPI(
        title="ECU-Can-DAQ",
        description="CANape-class ECU DAQ and calibration tool",
        version="0.2.0",
        lifespan=lifespan,
    )

    app.include_router(rest_router)

    @app.websocket("/ws/live")
    async def ws_live(websocket: WebSocket):
        await ws_manager.connect(websocket)
        try:
            while True:
                # Keep alive and handle incoming commands
                data = await websocket.receive_text()
                try:
                    import json

                    msg = json.loads(data)
                    if msg.get("type") == "set_decimation":
                        hz = float(msg.get("hz", 30))
                        ws_manager.set_decimation(hz)
                except Exception:
                    pass
        except WebSocketDisconnect:
            await ws_manager.disconnect(websocket)

    @app.get("/")
    async def root():
        if static_dir and (static_dir / "index.html").exists():
            return FileResponse(str(static_dir / "index.html"))
        pkg_web = Path(__file__).parent / "web" / "index.html"
        if pkg_web.exists():
            return FileResponse(str(pkg_web))
        return {"message": "ECU-Can-DAQ running, web UI not built yet", "ws": "/ws/live", "api": "/api/status"}

    web_dir = static_dir or (Path(__file__).parent / "web")
    if web_dir.exists():
        app.mount("/static", StaticFiles(directory=str(web_dir)), name="static")

    return app


_app: FastAPI | None = None


def get_app() -> FastAPI:
    global _app
    if _app is None:
        _app = create_app()
    return _app


app = get_app()

"""Project loading/saving."""
from __future__ import annotations

from pathlib import Path
from typing import Any
import yaml
import json

from ecu_can_daq.config.schema import ProjectSchema
from ecu_can_daq.models import DeviceConfig, RasterConfig, RecorderConfig


class Project:
    def __init__(
        self,
        name: str,
        a2l_file: Path,
        device: DeviceConfig,
        rasters: list[RasterConfig],
        recorder: RecorderConfig | None = None,
        measurements_file: Path | None = None,
        host: str = "127.0.0.1",
        port: int = 9500,
        web_host: str = "127.0.0.1",
        web_port: int = 8000,
        description: str = "",
        path: Path | None = None,
    ):
        self.name = name
        self.a2l_file = a2l_file
        self.device = device
        self.rasters = rasters
        self.recorder = recorder
        self.measurements_file = measurements_file
        self.host = host
        self.port = port
        self.web_host = web_host
        self.web_port = web_port
        self.description = description
        self.path = path

    @classmethod
    def load(cls, path: Path | str) -> "Project":
        p = Path(path)
        text = p.read_text(encoding="utf-8")
        if p.suffix.lower() in (".yaml", ".yml"):
            data = yaml.safe_load(text)
        else:
            data = json.loads(text)
        schema = ProjectSchema.model_validate(data)
        a2l_file = Path(schema.a2l_file)
        if not a2l_file.is_absolute():
            a2l_file = (p.parent / a2l_file).resolve()
        meas_file = None
        if schema.measurements_file:
            mf = Path(schema.measurements_file)
            if not mf.is_absolute():
                mf = (p.parent / mf).resolve()
            meas_file = mf
        models = schema.to_models()
        return cls(
            name=schema.name,
            a2l_file=a2l_file,
            device=models["device"],
            rasters=models["rasters"],
            recorder=models["recorder"],
            measurements_file=meas_file,
            host=schema.host,
            port=schema.port,
            web_host=schema.web_host,
            web_port=schema.web_port,
            description=schema.description,
            path=p,
        )

    def save(self, path: Path | str | None = None) -> Path:
        target = Path(path) if path else self.path
        if target is None:
            raise ValueError("No path to save project")
        # Build dict
        # Make a2l_file relative if inside target parent
        try:
            a2l_rel = str(self.a2l_file.relative_to(target.parent))
        except Exception:
            a2l_rel = str(self.a2l_file)
        data: dict[str, Any] = {
            "name": self.name,
            "a2l_file": a2l_rel,
            "device": {
                "transport": self.device.transport.value,
                "channel": self.device.channel,
                "bitrate": self.device.bitrate,
                "can_fd": self.device.can_fd,
                "can_termination": self.device.can_termination,
                "request_id": hex(self.device.request_id),
                "response_id": hex(self.device.response_id),
                "response_timeout_ms": self.device.response_timeout_ms,
                "max_payload_bytes": self.device.max_payload_bytes,
                "host": self.device.host,
                "port": self.device.port,
            },
            "rasters": [
                {"name": r.name, "rate_hz": r.rate_hz, "signals": list(r.signals), "description": r.description}
                for r in self.rasters
            ],
            "host": self.host,
            "port": self.port,
            "web_host": self.web_host,
            "web_port": self.web_port,
            "description": self.description,
        }
        if self.measurements_file:
            try:
                mf_rel = str(self.measurements_file.relative_to(target.parent))
            except Exception:
                mf_rel = str(self.measurements_file)
            data["measurements_file"] = mf_rel
        if self.recorder:
            data["recorder"] = {
                "file_path": self.recorder.file_path,
                "format": self.recorder.format,
                "enable_csv_preview": self.recorder.enable_csv_preview,
                "csv_decimation_hz": self.recorder.csv_decimation_hz,
                "flush_interval_seconds": self.recorder.flush_interval_seconds,
                "root_properties": self.recorder.root_properties,
            }

        if target.suffix.lower() in (".yaml", ".yml"):
            target.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        else:
            target.write_text(json.dumps(data, indent=2), encoding="utf-8")
        self.path = target
        return target

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "a2l_file": str(self.a2l_file),
            "device": self.device.to_dict(),
            "rasters": [{"name": r.name, "rate_hz": r.rate_hz, "signals": list(r.signals)} for r in self.rasters],
            "recorder": {
                "file_path": self.recorder.file_path,
                "format": self.recorder.format,
            }
            if self.recorder
            else None,
            "host": self.host,
            "port": self.port,
            "web_host": self.web_host,
            "web_port": self.web_port,
        }

"""Pydantic schemas for project config."""
from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field, field_validator, model_validator


class DeviceConfigSchema(BaseModel):
    transport: str = Field(default="simulated", description="Transport type: simulated, nixnet, socketcan, pcan, vector, ethernet")
    channel: str = Field(default="CAN1")
    bitrate: int = Field(default=500_000, gt=0)
    can_fd: bool = Field(default=False)
    can_termination: bool = Field(default=False)
    request_id: int | str = Field(default=0x7E0)
    response_id: int | str = Field(default=0x7E8)
    response_timeout_ms: int = Field(default=250, gt=0)
    max_payload_bytes: int | None = Field(default=None)
    host: str = Field(default="127.0.0.1")
    port: int = Field(default=0)
    description: str = Field(default="")

    @field_validator("request_id", "response_id", mode="before")
    @classmethod
    def parse_can_id(cls, v: Any) -> int:
        if isinstance(v, int):
            return v
        if isinstance(v, str):
            return int(v, 0)
        raise ValueError("Invalid CAN ID")

    @model_validator(mode="after")
    def check_ids(self):
        if self.request_id == self.response_id:
            raise ValueError("request_id and response_id must differ")
        return self

    def to_device_config(self):
        from ecu_can_daq.models import DeviceConfig, TransportType

        return DeviceConfig(
            transport=TransportType(self.transport),
            channel=self.channel,
            bitrate=self.bitrate,
            can_fd=self.can_fd,
            can_termination=self.can_termination,
            request_id=int(self.request_id),
            response_id=int(self.response_id),
            response_timeout_ms=self.response_timeout_ms,
            max_payload_bytes=self.max_payload_bytes,
            host=self.host,
            port=self.port,
            description=self.description,
        )


class RasterSchema(BaseModel):
    name: str = Field(description="Raster name, e.g. 100Hz")
    rate_hz: float = Field(gt=0)
    signals: list[str] = Field(default_factory=list)
    description: str = Field(default="")

    def to_raster_config(self):
        from ecu_can_daq.models import RasterConfig

        return RasterConfig(
            name=self.name, rate_hz=self.rate_hz, signals=tuple(self.signals), description=self.description
        )


class RecorderSchema(BaseModel):
    file_path: str = Field(default="recording.tdms")
    format: str = Field(default="both", description="tdms, csv, both")
    enable_csv_preview: bool = Field(default=True)
    csv_decimation_hz: float = Field(default=50.0, gt=0)
    flush_interval_seconds: float = Field(default=1.0, gt=0)
    root_properties: dict[str, Any] = Field(default_factory=dict)

    def to_recorder_config(self):
        from ecu_can_daq.models import RecorderConfig

        return RecorderConfig(
            file_path=self.file_path,
            format=self.format,
            enable_csv_preview=self.enable_csv_preview,
            csv_decimation_hz=self.csv_decimation_hz,
            flush_interval_seconds=self.flush_interval_seconds,
            root_properties=self.root_properties,
        )


class ProjectSchema(BaseModel):
    name: str = Field(default="ECU DAQ Project")
    a2l_file: str = Field(description="Path to A2L file")
    measurements_file: str | None = Field(default=None, description="Optional measurements txt")
    device: DeviceConfigSchema = Field(default_factory=DeviceConfigSchema)
    rasters: list[RasterSchema] = Field(default_factory=list)
    recorder: RecorderSchema | None = Field(default=None)
    host: str = Field(default="127.0.0.1")
    port: int = Field(default=9500, description="Legacy TCP JSON streaming port")
    web_host: str = Field(default="127.0.0.1")
    web_port: int = Field(default=8000)
    description: str = Field(default="")

    def to_models(self):
        rasters = [r.to_raster_config() for r in self.rasters]
        device = self.device.to_device_config()
        recorder = self.recorder.to_recorder_config() if self.recorder else None
        return {
            "rasters": rasters,
            "device": device,
            "recorder": recorder,
        }

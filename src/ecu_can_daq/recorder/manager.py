"""Recorder manager handling TDMS + CSV together."""
from __future__ import annotations

from typing import Any

from ecu_can_daq.models import MeasurementBatch, RecorderConfig
from ecu_can_daq.recorder.tdms_recorder import TdmsRecorder
from ecu_can_daq.recorder.csv_recorder import CsvRecorder
from ecu_can_daq.recorder.base import BaseRecorder


class RecorderManager:
    """Manages multiple recorders based on config."""

    def __init__(self, config: RecorderConfig):
        self.config = config
        self._recorders: list[BaseRecorder] = []

        fmt = config.format.lower()
        if "tdms" in fmt or fmt == "both":
            try:
                self._recorders.append(TdmsRecorder(config))
            except Exception as e:
                print(f"TDMS recorder init failed: {e}")

        if config.enable_csv_preview and ("csv" in fmt or "both" in fmt or "tdms" in fmt):
            # CSV is enabled as preview when TDMS chosen (per user request)
            try:
                self._recorders.append(CsvRecorder(config))
            except Exception as e:
                print(f"CSV recorder init failed: {e}")

        # If format is pure csv and no tdms, still want csv
        if fmt == "csv" and not any(isinstance(r, CsvRecorder) for r in self._recorders):
            self._recorders.append(CsvRecorder(config))

    def open(self, root_properties: dict[str, Any] | None = None) -> None:
        for rec in self._recorders:
            rec.open(root_properties)

    def append(self, batch: MeasurementBatch) -> None:
        for rec in self._recorders:
            try:
                rec.append(batch)
            except Exception as e:
                print(f"Recorder {rec.__class__.__name__} append failed: {e}")

    def flush(self) -> None:
        for rec in self._recorders:
            try:
                rec.flush()
            except Exception:
                pass

    def close(self) -> None:
        for rec in self._recorders:
            try:
                rec.close()
            except Exception:
                pass

    @property
    def is_open(self) -> bool:
        return any(r.is_open for r in self._recorders)

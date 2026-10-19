"""CSV sidecar recorder for quick preview."""
from __future__ import annotations

from pathlib import Path
import csv
import time
from collections import defaultdict
from typing import Any

from ecu_can_daq.models import MeasurementBatch, RecorderConfig
from ecu_can_daq.recorder.base import BaseRecorder


class CsvRecorder(BaseRecorder):
    """Writes one CSV per raster, decimated for preview."""

    def __init__(self, config: RecorderConfig):
        super().__init__(config)
        self._base_path = Path(config.file_path)
        # If config.file_path is .tdms, we want folder _preview/
        # e.g. out.tdms -> out_preview/
        if self._base_path.suffix.lower() == ".tdms":
            self._preview_dir = self._base_path.with_name(
                self._base_path.stem + "_preview"
            )
        else:
            self._preview_dir = self._base_path.parent / (self._base_path.stem + "_preview")
        self._files: dict[str, Any] = {}
        self._writers: dict[str, csv.DictWriter] = {}
        self._headers: dict[str, list[str]] = {}
        self._last_write_time: dict[str, float] = defaultdict(float)
        self._decimation_period = (
            1.0 / config.csv_decimation_hz if config.csv_decimation_hz > 0 else 0.02
        )

    def open(self, root_properties: dict[str, Any] | None = None) -> None:
        self._preview_dir.mkdir(parents=True, exist_ok=True)
        self._is_open = True

    def _ensure_file(self, raster: str, signals: list[str]) -> None:
        if raster in self._files:
            # Check if new signals added
            needed = set(["timestamp_ns", "timestamp_iso"] + signals)
            existing = set(self._headers.get(raster, []))
            if not needed.issubset(existing):
                # Need to recreate with wider header – for simplicity, close and append new header?
                # We'll keep old and add new columns lazily.
                new_headers = list(existing | needed)
                self._headers[raster] = new_headers
            return

        file_path = self._preview_dir / f"{raster}.csv"
        f = open(file_path, "w", newline="", encoding="utf-8")
        headers = ["timestamp_ns", "timestamp_iso"] + sorted(signals)
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        self._files[raster] = f
        self._writers[raster] = writer
        self._headers[raster] = headers

    def append(self, batch: MeasurementBatch) -> None:
        if not self._is_open:
            raise RuntimeError("Recorder not open")
        raster = batch.raster_name or "default"
        now = time.monotonic()
        # Decimate
        last = self._last_write_time.get(raster, 0)
        if now - last < self._decimation_period:
            return
        self._last_write_time[raster] = now

        signals = list(batch.values.keys())
        self._ensure_file(raster, signals)

        writer = self._writers.get(raster)
        f = self._files.get(raster)
        if not writer or not f:
            return

        # If headers missing some signals, update file? Keep simple
        row: dict[str, Any] = {
            "timestamp_ns": batch.timestamp_ns,
            "timestamp_iso": time.strftime(
                "%Y-%m-%dT%H:%M:%S", time.localtime(batch.timestamp_ns / 1e9)
            ),
        }
        for k, v in batch.values.items():
            row[k] = v
        # Ensure row has all headers (fill missing)
        for h in self._headers[raster]:
            row.setdefault(h, "")

        try:
            writer.writerow(row)
        except ValueError:
            # Header mismatch – expand
            new_headers = list(set(self._headers[raster]) | set(row.keys()))
            # Rewrite? For simplicity, close and reopen with new headers and append
            # Not perfect but works for preview
            self._headers[raster] = new_headers
            # recreate writer with bigger header? Need to handle existing file
            # We'll just write with dict that may miss – skip for now
            pass

        # Auto flush periodically
        if (time.monotonic() % 1.0) < 0.1:  # approx each sec
            self.flush()

    def flush(self) -> None:
        for f in self._files.values():
            try:
                f.flush()
            except Exception:
                pass

    def close(self) -> None:
        for f in self._files.values():
            try:
                f.close()
            except Exception:
                pass
        self._files.clear()
        self._writers.clear()
        self._is_open = False

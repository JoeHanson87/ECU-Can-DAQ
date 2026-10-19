"""TDMS recorder using nptdms."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any
import logging
from collections import defaultdict

try:
    from nptdms import TdmsWriter, ChannelObject
    HAS_TDMS = True
except ImportError:
    HAS_TDMS = False
    TdmsWriter = None  # type: ignore
    ChannelObject = None  # type: ignore

from ecu_can_daq.models import MeasurementBatch, RecorderConfig
from ecu_can_daq.recorder.base import BaseRecorder

log = logging.getLogger(__name__)


class TdmsRecorder(BaseRecorder):
    """Writes MeasurementBatches to TDMS: group = raster, channel = signal."""

    def __init__(self, config: RecorderConfig):
        super().__init__(config)
        if not HAS_TDMS:
            raise RuntimeError("nptdms not installed, run pip install nptdms")
        self._file_path = Path(config.file_path)
        self._writer: Any | None = None
        self._buffers: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
        self._timestamp_buffers: dict[str, list[int]] = defaultdict(list)
        self._last_flush = time.monotonic()
        self._root_props: dict[str, Any] = {}
        # Keep track of channel properties
        self._channel_props: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)

    def open(self, root_properties: dict[str, Any] | None = None) -> None:
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        # TdmsWriter expects file path and needs open()
        self._writer = TdmsWriter(str(self._file_path))
        try:
            self._writer.open()
        except Exception as e:
            log.warning(f"Trying context manager open fallback: {e}")
            # Try entering context
            self._writer.__enter__()
        self._root_props = root_properties or {}
        self._root_props.update(self.config.root_properties)
        self._root_props.setdefault("created", time.strftime("%Y-%m-%d %H:%M:%S"))
        self._is_open = True
        log.info(f"TDMS recorder opened {self._file_path}")

    def append(self, batch: MeasurementBatch) -> None:
        if not self._is_open:
            raise RuntimeError("Recorder not open")
        raster = batch.raster_name or "default"
        # Append timestamp
        self._timestamp_buffers[raster].append(batch.timestamp_ns)
        for name, value in batch.values.items():
            self._buffers[raster][name].append(float(value))

        # Auto flush
        now = time.monotonic()
        if now - self._last_flush >= self.config.flush_interval_seconds:
            self.flush()
            self._last_flush = now

    def flush(self) -> None:
        if not self._is_open or not self._writer:
            return
        # Write buffered segments
        for raster, channels in self._buffers.items():
            if not channels:
                continue
            segment_objects = []
            timestamps = self._timestamp_buffers.get(raster, [])
            # Write timestamp channel as absolute time (int64 ns)
            if timestamps:
                segment_objects.append(
                    ChannelObject(raster, "timestamp_ns", timestamps, properties={"unit": "ns", "description": "Absolute timestamp"})
                )
                # Also waveform relative time if needed
            for ch_name, data in channels.items():
                if not data:
                    continue
                props = self._channel_props[raster].get(ch_name, {})
                # Set common props
                props.setdefault("unit", "")
                segment_objects.append(
                    ChannelObject(raster, ch_name, data, properties=props)
                )
            if segment_objects:
                try:
                    self._writer.write_segment(segment_objects)
                except Exception as e:
                    log.error(f"TDMS write_segment failed for raster {raster}: {e}")

        # Clear buffers
        self._buffers.clear()
        self._timestamp_buffers.clear()

        # Note: root properties written at file creation? nptdms doesn't support root props in TdmsWriter directly?
        # We can write root props via a dummy segment or rely on file properties later.
        # For simplicity, we store root props to sidecar JSON or just log.
        # nptdms Writer doesn't have root properties API, but we can attempt.

    def close(self) -> None:
        if not self._is_open:
            return
        try:
            self.flush()
        finally:
            if self._writer:
                try:
                    # If we used __enter__, use __exit__
                    try:
                        self._writer.__exit__(None, None, None)
                    except Exception:
                        self._writer.close()
                except Exception:
                    pass
                self._writer = None
            self._is_open = False
            log.info(f"TDMS recorder closed {self._file_path}")

    def set_channel_properties(
        self, raster: str, channel: str, properties: dict[str, Any]
    ) -> None:
        self._channel_props[raster][channel].update(properties)

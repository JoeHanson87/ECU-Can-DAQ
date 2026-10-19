# ECU-Can-DAQ — CANape-class DAQ & Calibration

Full-featured ECU CAN DAQ and calibration tool inspired by Vector CANape. Supports **A2L-driven DAQ, multi-raster, XCP DAQ lists, TDMS+CSV recording, Web dashboard with Plotly dark theme, and NI hardware configurable in UI**.

## What's New in v0.2.0

- **A2L via pya2l**: Full ASAP2 parser with fallback — COMPU_METHOD (LINEAR/RAT_FUNC/TAB), CHARACTERISTIC, AXIS, MEMORY_SEGMENT, MOD_PAR, IF_DATA handling. Phys = compu_method(raw) conversion.
- **XCP full**: CONNECT, GET_STATUS, GET_COMM_MODE_INFO, SET_MTA, UPLOAD/SHORT_UPLOAD, DOWNLOAD/SHORT_DOWNLOAD, DAQ list alloc: ALLOC_DAQ/ODT/ODT_ENTRY, SET_DAQ_PTR, WRITE_DAQ, SET_DAQ_LIST_MODE, START_STOP_DAQ_LIST/SYNCH, GET_DAQ_CLOCK/PROCESSOR_INFO. Polling fallback retained.
- **Multi-raster DAQ engine**: e.g. `EngineSpeed@100Hz, CoolantTemp@10Hz`, per-raster sequence, ringbuffer 60s, overrun counters. Simulated transport now emits DTO-rate synthetic data with jitter.
- **Transports via python-can abstraction**: Simulated (DTO), SocketCAN/virtual (dev/CI), NI-XNET (real, FD-aware), PEAK PCAN (mocked with virtual), Vector XL (mocked), XCP-Ethernet TCP/UDP (stub). PCAN/Vector need no HW for tests (`pytest -m mock_hw`).
- **Recorders**: TDMS (nptdms, LabVIEW/DIAdem native) as primary + CSV preview sidecar. Layout: Group=raster (e.g. "100Hz"), Channel=signal, props: unit, ecu_address, data_type. Writer buffered flush ~1s.
- **Web dashboard**: FastAPI + WebSocket live (decimated 30Hz), REST: `/api/signals`, `/api/rasters`, `/api/status`, `/api/devices`, `/api/device/config`, `/api/device/test`, `/api/project`, `/api/daq/{start,stop}`. Frontend: single-file `index.html` + `app.js` with Plotly.js via CDN (offline fallback planned), dark CANape theme `#0e1116` bg, trace palette, crosshair, synced zoom, signal explorer + numerics + time plot + control bar.
- **NI hardware configurable in UI**: Devices panel → channel (select via `/api/devices/ni/interfaces`), bitrate (125k/250k/500k/1M+custom), CAN-FD, termination, request/response IDs (hex), timeout, host/port for Ethernet. Test Connection button does CONNECT→GET_STATUS. Apply validates (request≠response) and re-inits link when idle; blocked if acquiring.
- **Project files**: `*.yaml/*.json` experiment: a2l_file, measurements_file, device, rasters, recorder, host/port, web_host/port. YAML example in `examples/sample_project.yaml`.
- **Calibration scaffold**: CHARACTERISTIC read/write via DOWNLOAD, PageManager stub (REFERENCE/WORKING), PAR/DCM import/export.

## Architecture

```
A2L (pya2l facade) → Internal Model (Measurement/Characteristic/CompuMethod raw↔phys)
→ DAQ Engine (multi-raster: XCP DAQ lists primary, UPLOAD polling fallback)
  ├─ Recorder: TDMS + CSV preview
  ├─ Streamers: TCP-JSON (LabVIEW compat) + WebSocket (dashboard, decimated)
  └─ Web UI (FastAPI + Plotly dark)
Transports: Simulated | SocketCAN | NI-XNET | PCAN (mock) | Vector (mock) | Ethernet
Project: *.ecuproj.yaml
Calibration (scaffold): VALUE/CURVE/MAP, pages, PAR/DCM
```

## Quick Start

### Install
```bash
pip install -e .   # base deps: pya2l, fastapi, uvicorn, python-can, nptdms, numpy, pyyaml
# optional:
pip install -e .[nixnet]  # Windows NI-XNET
pip install -e .[pcan]    # PEAK
```

### 1) Inspect A2L
```bash
ecu-can-daq inspect --a2l examples/sample.a2l
```

### 2) Init project
```bash
ecu-can-daq init --a2l examples/sample.a2l --measurements examples/sample_measurements.txt -o project.yaml
cat project.yaml
```

### 3) Validate project
```bash
ecu-can-daq validate --project examples/sample_project.yaml
```

### 4) Run DAQ with TDMS+CSV recording (simulated)
```bash
ecu-can-daq run --project examples/sample_project.yaml --record recording.tdms --duration 5
# Produces recording.tdms + recording_preview/*.csv
# Open .tdms in LabVIEW DIAdem or:
python -c "from nptdms import TdmsFile; print(TdmsFile.read('recording.tdms').groups())"
```

### 5) Run with web dashboard (DAQ-first)
```bash
ecu-can-daq run --project examples/sample_project.yaml --record out.tdms --web --duration 10
# Open http://127.0.0.1:8000
# - Signal explorer left: search, select, assign to raster
# - Center: Plotly live plot (decimated), numeric cards
# - Right: Device Config — NI-XNET channel/bitrate/IDs/termination/FD configurable, Test Connection
```

### 6) Serve dashboard only (no DAQ)
```bash
ecu-can-daq serve --a2l examples/sample.a2l --port 8000
```

### 7) Legacy LabVIEW TCP-JSON streaming (backward compat)
```bash
python -m ecu_can_daq --a2l examples/sample.a2l --measurements examples/sample_measurements.txt --transport simulated --rate-hz 100
# Connect LabVIEW to 127.0.0.1:9500 – receives metadata + sample_batch newline JSON
```

Example batch:
```json
{"type":"sample_batch","sequence":12,"timestamp_ns":1727085625000000000,"rate_hz":100.0,"raster":"100Hz","values":{"EngineSpeed":3210.0,"Throttle":18.5}}
```

### NI-XNET real hardware
```bash
# Edit device in UI or project.yaml:
# transport: nixnet, channel: CAN1, bitrate: 500000, request_id: 0x7E0, response_id: 0x7E8, can_termination: true
ecu-can-daq run --a2l C:\\data\\ecu.a2l --measurements C:\\data\\measurements.txt --transport nixnet --channel CAN1 --record run.tdms --web
```

## Web Dashboard Details

- **Signal Explorer**: checkbox list, search persists to localStorage, All/None, assign to raster dropdown → POST /api/rasters.
- **Live Numerics**: up to 12 selected signals, formatted 3 decimals.
- **Plotly Dark**: `plotly_dark` customized, bg `#0e1116`, grid `#21262d`, CANape trace palette, hover x unified, crosshair.
- **Controls**: Connect & Start, Stop, Record file input, CSV preview toggle, status text, overruns badge.
- **Device Panel**: Transport select (simulated/nixnet/socketcan/pcan/vector/ethernet), channel text + available interfaces hint, bitrate select + custom, timeout ms, request/response hex, termination/FD toggles, host/port for Ethernet, Apply Config (PUT /api/device/config, validates IDs differ), Test (POST /api/device/test runs CONNECT/GET_STATUS), Save Project stub (CLI `ecu-can-daq init`).
- **Recorder table**: shows recording file status.

Decimation: WS decimated to 30Hz (configurable via `set_decimation` message), full rate to TDMS, CSV preview decimated to 50Hz default.

## TDMS + CSV Recording

- **TDMS**: Group = raster, Channel = signal, plus `timestamp_ns` channel per raster. Props: unit, description. Root props: project, device, created. Flush every 1s. Open directly in LabVIEW/DIAdem.
- **CSV preview**: folder `out_preview/` with `100Hz.csv`, `10Hz.csv` etc. Columns: timestamp_ns, timestamp_iso, signals..., decimated to 50Hz for quick pandas: `pd.read_csv("out_preview/100Hz.csv")`.

Example validation:
```bash
ecu-can-daq record --a2l examples/sample.a2l --measurements examples/sample_measurements.txt --output test.tdms --duration 2
```

## Project YAML Format

```yaml
name: Sample
a2l_file: sample.a2l
measurements_file: sample_measurements.txt
device:
  transport: nixnet
  channel: CAN1
  bitrate: 500000
  can_termination: false
  can_fd: false
  request_id: 0x7E0
  response_id: 0x7E8
rasters:
  - name: 100Hz
    rate_hz: 100.0
    signals: [EngineSpeed, Throttle]
  - name: 10Hz
    rate_hz: 10.0
    signals: [CoolantTemp]
recorder:
  file_path: recording.tdms
  format: both
  enable_csv_preview: true
  csv_decimation_hz: 50.0
host: 127.0.0.1
port: 9500
web_host: 127.0.0.1
web_port: 8000
```

## CLI Reference

```
ecu-can-daq run --a2l FILE --measurements FILE --transport {simulated,nixnet,socketcan,pcan,vector,ethernet} --channel CAN1 --bitrate 500000 --raster "EngineSpeed@100Hz,CoolantTemp@10Hz" --record out.tdms --web --duration SECS
ecu-can-daq run --project project.yaml --record out.tdms --web
ecu-can-daq serve --a2l FILE --project FILE --host 127.0.0.1 --port 8000
ecu-can-daq inspect --a2l FILE --limit 20
ecu-can-daq init --a2l FILE --measurements FILE --transport simulated -o project.yaml
ecu-can-daq validate --project project.yaml
ecu-can-daq record --a2l FILE --output out.tdms --duration 10
```

Legacy compat: `python -m ecu_can_daq --a2l ... --measurements ...` still works (single raster, up to 200 signals).

## XCP Details

- **Polling**: SET_MTA + UPLOAD, grouping contiguous address blocks.
- **DAQ**: DAQ list allocator packs ODT entries by raster, respects MAX_DTO (7 classic CAN, larger if FD), decodes DTO by PID. START_STOP_SYNCH for synchronous start.
- **Calibration**: DOWNLOAD/SHORT_DOWNLOAD scaffold, phys↔raw via CompuMethod inverse (LINEAR exact, RAT_FUNC iterative Newton).

## Testing

```bash
pytest -q
pytest tests/test_new_features.py -v
# NI hardware (if connected):
pytest -m ni_hw
# Mocked PCAN/Vector:
pytest -m mock_hw
```

22 tests (8 legacy + 14 new) should pass.

## Notes & Limitations

- Real NI-XNET requires Windows + NI driver + `pip install .[nixnet]`.
- PCAN/Vector are mocked via `virtual` bus for CI; real driver support via python-can.
- CAN-FD payload >8 not fully tested on NI; python-can FD path exists.
- XCP Ethernet DTO streaming not yet implemented (stub).
- Calibration map/curve 3D editor deferred after DAQ milestone.

## Roadmap

- P6 Calibration full editor (MAP/CURVE UI, PAGE switching, HEX/ELF integration)
- MDF4 recorder optional (`asammdf`)
- Trigger (start/stop on signal threshold/time)
- Seed&Key unlock, A2L IF_DATA handling, CCP support
- Offline bundled Plotly for air-gapped hosts

# ECU-Can-DAQ

ECU-Can-DAQ is a Python application that loads measurement definitions from an A2L file, samples up to 200 ECU parameters, and streams the captured data to LabVIEW over TCP as newline-delimited JSON.

## Features

- Loads measurement metadata from an A2L file.
- Validates and schedules up to 200 requested parameters.
- Streams measurement batches to one or more TCP clients.
- Includes a simulated transport for development and testing.
- Includes an NI-XNET transport that uses XCP-on-CAN for ECU memory uploads.

## Stream format

Each LabVIEW client receives newline-delimited JSON messages:

1. A `metadata` message with the measurement definitions.
2. Repeating `sample_batch` messages with the latest values.

Example sample batch:

```json
{"type":"sample_batch","sequence":12,"timestamp_ns":1727085625000000000,"rate_hz":100.0,"values":{"EngineSpeed":3210.0,"Throttle":18.5}}
```

## Quick start

### Simulated transport

```bash
python -m ecu_can_daq \
  --a2l /home/runner/work/ECU-Can-DAQ/ECU-Can-DAQ/examples/sample.a2l \
  --measurements /home/runner/work/ECU-Can-DAQ/ECU-Can-DAQ/examples/sample_measurements.txt \
  --transport simulated \
  --rate-hz 100
```

Then connect LabVIEW or another TCP client to `127.0.0.1:9500`.

### NI-XNET transport

Install the optional dependency on a Windows machine that already has the NI-XNET driver/runtime installed:

```bash
python -m pip install .[nixnet]
```

Run the application with the XCP request/response CAN identifiers for the ECU:

```bash
python -m ecu_can_daq \
  --a2l C:\\data\\ecu.a2l \
  --measurements C:\\data\\measurements.txt \
  --transport nixnet \
  --channel CAN1 \
  --request-id 0x7E0 \
  --response-id 0x7E8 \
  --bitrate 500000 \
  --rate-hz 100
```

## Notes

- The NI-XNET path uses XCP `CONNECT`, `SET_MTA`, and `UPLOAD` commands to read ECU memory described by the A2L file.
- Measurements are grouped into contiguous address blocks before upload requests are issued to reduce CAN traffic.
- Integer and IEEE floating-point A2L datatypes are supported.

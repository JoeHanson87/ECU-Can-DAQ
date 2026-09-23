import unittest

from ecu_can_daq.a2l import load_measurements, resolve_measurements
from ecu_can_daq.service import AcquisitionService
from ecu_can_daq.streaming import TcpJsonStreamer
from ecu_can_daq.transports.simulated import SimulatedTransport


class _RecordingStreamer(TcpJsonStreamer):
    def __init__(self) -> None:
        super().__init__("127.0.0.1", 0)
        self.batches = []

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def broadcast(self, batch):
        self.batches.append(batch)


class AcquisitionServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_publishes_batches(self) -> None:
        definitions = load_measurements(
            "/home/runner/work/ECU-Can-DAQ/ECU-Can-DAQ/examples/sample.a2l"
        )
        measurements = resolve_measurements(definitions, ["EngineSpeed", "Throttle"])
        streamer = _RecordingStreamer()
        streamer.configure_measurements(measurements)
        service = AcquisitionService(
            transport=SimulatedTransport(),
            streamer=streamer,
            measurements=measurements,
            rate_hz=20.0,
        )

        await service.run(duration_seconds=0.11)

        self.assertGreaterEqual(len(streamer.batches), 2)
        self.assertEqual(set(streamer.batches[0].values), {"EngineSpeed", "Throttle"})


if __name__ == "__main__":
    unittest.main()

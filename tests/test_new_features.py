import unittest
import tempfile
from pathlib import Path
import time
import json

from ecu_can_daq.models import MeasurementDefinition, DataType, ByteOrder, DeviceConfig, TransportType, RasterConfig, RecorderConfig
from ecu_can_daq.xcp.daq import DaqListAllocator, DtoDecoder
from ecu_can_daq.daq.engine import DaqEngine, DaqEngineConfig
from ecu_can_daq.transports.simulated import SimulatedTransport
from ecu_can_daq.a2l.facade import load_a2l_project
from ecu_can_daq.recorder.manager import RecorderManager
from ecu_can_daq.config.schema import DeviceConfigSchema

EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples"


class CompuMethodTests(unittest.TestCase):
    def test_linear_conversion(self):
        from ecu_can_daq.models import CompuMethod, CompuMethodType
        cm = CompuMethod(name="Test", compu_type=CompuMethodType.LINEAR, coeffs=(0.0, 0.1, 0,0,0,1.0))
        self.assertAlmostEqual(cm.raw_to_phys(1000), 100.0)
        self.assertAlmostEqual(cm.phys_to_raw(100.0), 1000.0)

    def test_identical(self):
        from ecu_can_daq.models import CompuMethod
        cm = CompuMethod(name="Id")
        self.assertEqual(cm.raw_to_phys(42), 42)


class A2LFacadeTests(unittest.TestCase):
    def test_load_sample(self):
        proj = load_a2l_project(EXAMPLES_DIR / "sample.a2l")
        self.assertIn("EngineSpeed", proj.measurements)
        self.assertEqual(len(proj.modules), 1)
        self.assertEqual(proj.measurements["EngineSpeed"].ecu_address, 0x1000)


class DaqAllocatorTests(unittest.TestCase):
    def test_allocate_simple(self):
        measurements = [
            MeasurementDefinition(name=f"Sig{i}", data_type=DataType.UBYTE, ecu_address=0x1000+i, lower_limit=0, upper_limit=100)
            for i in range(5)
        ]
        allocator = DaqListAllocator(max_dto_payload=7)
        rasters = [(0, 100.0, measurements[:3]), (1, 10.0, measurements[3:])]
        configs = allocator.allocate(rasters)
        self.assertTrue(len(configs) >= 2)
        # First raster should have at least 1 ODT
        self.assertGreaterEqual(configs[0].num_odts, 1)

    def test_decode_dto(self):
        measurements = [
            MeasurementDefinition(name="Sig0", data_type=DataType.UBYTE, ecu_address=0x1000, lower_limit=0, upper_limit=255),
            MeasurementDefinition(name="Sig1", data_type=DataType.UBYTE, ecu_address=0x1001, lower_limit=0, upper_limit=255),
        ]
        allocator = DaqListAllocator(max_dto_payload=7)
        configs = allocator.allocate([(0, 100.0, measurements)])
        decoder = DtoDecoder(configs)
        # Build DTO: PID 0 + 2 bytes
        payload = bytes([0, 42, 84])
        vals = decoder.decode_dto(payload)
        self.assertEqual(vals["Sig0"], 42.0)
        self.assertEqual(vals["Sig1"], 84.0)


class SimulatedDaqTests(unittest.IsolatedAsyncioTestCase):
    async def test_simulated_daq_loop(self):
        meas = [
            MeasurementDefinition(name="EngineSpeed", data_type=DataType.ULONG, ecu_address=0x1000, lower_limit=0, upper_limit=8000),
            MeasurementDefinition(name="Throttle", data_type=DataType.UBYTE, ecu_address=0x1004, lower_limit=0, upper_limit=100),
        ]
        transport = SimulatedTransport()
        from ecu_can_daq.models import RasterConfig
        rasters = [RasterConfig(name="100Hz", rate_hz=20.0, signals=("EngineSpeed","Throttle"))]
        engine = DaqEngine(transport=transport, measurements=meas, config=DaqEngineConfig(rasters=rasters, use_daq_if_available=True))
        batches = []
        def cb(b):
            batches.append(b)
        await engine.run(cb, duration_s=0.3)
        self.assertGreaterEqual(len(batches), 2)


class RecorderTests(unittest.TestCase):
    def test_tdms_and_csv(self):
        import pathlib
        with tempfile.TemporaryDirectory() as tmp:
            tdms_path = Path(tmp) / "test.tdms"
            cfg = RecorderConfig(file_path=str(tdms_path), format="both", enable_csv_preview=True, csv_decimation_hz=100.0, flush_interval_seconds=0.1)
            mgr = RecorderManager(cfg)
            mgr.open({"test":"yes"})
            from ecu_can_daq.models import MeasurementBatch
            for i in range(10):
                batch = MeasurementBatch(sequence=i, timestamp_ns=time.time_ns(), rate_hz=10.0, values={"EngineSpeed": 1000+i}, raster_name="100Hz")
                mgr.append(batch)
            mgr.close()
            # Check TDMS exists
            self.assertTrue(tdms_path.exists())
            # Read back
            from nptdms import TdmsFile
            tdms = TdmsFile.read(str(tdms_path))
            groups = tdms.groups()
            self.assertEqual(len(groups), 1)
            self.assertEqual(groups[0].name, "100Hz")
            # Check CSV
            csv_dir = Path(tmp) / "test_preview"
            csv_file = csv_dir / "100Hz.csv"
            self.assertTrue(csv_file.exists())
            lines = csv_file.read_text().splitlines()
            self.assertGreaterEqual(len(lines), 2)  # header + data


class DeviceConfigTests(unittest.TestCase):
    def test_device_schema_validation(self):
        cfg = DeviceConfigSchema(transport="nixnet", channel="CAN1", bitrate=500000, request_id="0x7E0", response_id="0x7E8")
        device = cfg.to_device_config()
        device.validate()
        self.assertEqual(device.request_id, 0x7E0)

    def test_device_config_reject_same_ids(self):
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            DeviceConfigSchema(transport="simulated", request_id=0x7E0, response_id=0x7E0)


class TransportMockTests(unittest.TestCase):
    def test_pcan_mock(self):
        from ecu_can_daq.transports.pcan import PcanTransport
        cfg = DeviceConfig(transport=TransportType.PCAN, channel="virtual", bitrate=500000)
        trans = PcanTransport(config=cfg, mock=True)
        # Should not raise on create
        self.assertEqual(trans.get_device_config().transport, TransportType.PCAN)

    def test_vector_mock(self):
        from ecu_can_daq.transports.vector import VectorTransport
        cfg = DeviceConfig(transport=TransportType.VECTOR, channel="virtual", bitrate=500000)
        trans = VectorTransport(config=cfg, mock=True)
        self.assertEqual(trans.get_device_config().transport, TransportType.VECTOR)


class ProjectTests(unittest.TestCase):
    def test_project_load(self):
        from ecu_can_daq.project import Project
        proj = Project.load(EXAMPLES_DIR / "sample_project.yaml")
        self.assertEqual(proj.name, "Sample CANape-class Project")
        self.assertEqual(len(proj.rasters), 2)
        self.assertEqual(proj.device.transport, TransportType.SIMULATED)

    def test_project_save_load(self):
        from ecu_can_daq.project import Project
        from ecu_can_daq.models import DeviceConfig, TransportType, RasterConfig, RecorderConfig
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.yaml"
            device = DeviceConfig(transport=TransportType.SIMULATED, channel="CAN1")
            rasters = [RasterConfig(name="100Hz", rate_hz=100.0, signals=("EngineSpeed",))]
            proj = Project(name="Test", a2l_file=EXAMPLES_DIR / "sample.a2l", device=device, rasters=rasters, path=path)
            saved = proj.save(path)
            self.assertTrue(saved.exists())
            loaded = Project.load(saved)
            self.assertEqual(loaded.name, "Test")


class ServerApiTests(unittest.TestCase):
    def test_device_api(self):
        from fastapi.testclient import TestClient
        from ecu_can_daq.server.app import create_app
        app = create_app()
        client = TestClient(app)
        r = client.get("/api/devices")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("transports", data)

        r = client.get("/api/device/config")
        self.assertEqual(r.status_code, 200)

        r = client.get("/api/status")
        self.assertEqual(r.status_code, 200)


if __name__ == "__main__":
    unittest.main()

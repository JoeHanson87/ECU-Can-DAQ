from pathlib import Path
import unittest

from ecu_can_daq.a2l import load_measurements, resolve_measurements
from ecu_can_daq.models import ByteOrder, DataType


class A2LParserTests(unittest.TestCase):
    def test_load_measurements_extracts_header_and_block_fields(self) -> None:
        definitions = load_measurements(
            Path("/home/runner/work/ECU-Can-DAQ/ECU-Can-DAQ/examples/sample.a2l")
        )

        engine_speed = definitions["EngineSpeed"]
        self.assertEqual(engine_speed.data_type, DataType.ULONG)
        self.assertEqual(engine_speed.ecu_address, 0x1000)
        self.assertEqual(engine_speed.address_extension, 0)
        self.assertEqual(engine_speed.byte_order, ByteOrder.LITTLE)

        coolant = definitions["CoolantTemp"]
        self.assertEqual(coolant.data_type, DataType.SWORD)
        self.assertEqual(coolant.byte_order, ByteOrder.BIG)

    def test_resolve_measurements_preserves_requested_order(self) -> None:
        definitions = load_measurements(
            Path("/home/runner/work/ECU-Can-DAQ/ECU-Can-DAQ/examples/sample.a2l")
        )
        resolved = resolve_measurements(definitions, ["Throttle", "EngineSpeed"])
        self.assertEqual([item.name for item in resolved], ["Throttle", "EngineSpeed"])


if __name__ == "__main__":
    unittest.main()

import unittest

from ecu_can_daq.models import ByteOrder, DataType, MeasurementDefinition
from ecu_can_daq.xcp import CONNECT, DISCONNECT, POSITIVE_RESPONSE, SET_MTA, UPLOAD, XcpClient


class _FakeLink:
    def __init__(self) -> None:
        self.memory = {
            (0, 0x1000): 0x39,
            (0, 0x1001): 0x30,
            (0, 0x1002): 0x00,
        }
        self.current_address = 0
        self.current_extension = 0
        self.open_called = False
        self.close_called = False
        self.exchanges: list[bytes] = []

    def open(self) -> None:
        self.open_called = True

    def close(self) -> None:
        self.close_called = True

    def exchange(self, payload: bytes) -> bytes:
        self.exchanges.append(payload)
        command = payload[0]
        if command == CONNECT:
            return bytes([POSITIVE_RESPONSE, 0, 0, 8, 8, 1, 1])
        if command == DISCONNECT:
            return bytes([POSITIVE_RESPONSE])
        if command == SET_MTA:
            self.current_extension = payload[2]
            self.current_address = int.from_bytes(payload[3:7], byteorder="little")
            return bytes([POSITIVE_RESPONSE])
        if command == UPLOAD:
            length = payload[1]
            data = bytes(
                self.memory[(self.current_extension, self.current_address + offset)]
                for offset in range(length)
            )
            self.current_address += length
            return bytes([POSITIVE_RESPONSE]) + data
        raise AssertionError(f"Unexpected XCP command: {command}")


class XcpClientTests(unittest.TestCase):
    def test_read_measurements_groups_contiguous_uploads(self) -> None:
        definitions = [
            MeasurementDefinition(
                name="Throttle",
                data_type=DataType.UBYTE,
                ecu_address=0x1000,
                lower_limit=0,
                upper_limit=100,
            ),
            MeasurementDefinition(
                name="EngineSpeed",
                data_type=DataType.UWORD,
                ecu_address=0x1001,
                lower_limit=0,
                upper_limit=8000,
                byte_order=ByteOrder.LITTLE,
            ),
        ]
        link = _FakeLink()
        client = XcpClient(link)

        client.connect()
        values = client.read_measurements(definitions)
        client.disconnect()

        self.assertTrue(link.open_called)
        self.assertTrue(link.close_called)
        self.assertEqual(values["Throttle"], 57.0)
        self.assertEqual(values["EngineSpeed"], 48.0)
        self.assertEqual(sum(1 for payload in link.exchanges if payload[0] == SET_MTA), 1)
        self.assertEqual(sum(1 for payload in link.exchanges if payload[0] == UPLOAD), 1)

    def test_read_measurements_splits_large_uploads(self) -> None:
        definitions = []
        link = _FakeLink()
        for offset in range(8):
            link.memory[(0, 0x2000 + offset)] = offset + 1
            definitions.append(
                MeasurementDefinition(
                    name=f"Signal{offset}",
                    data_type=DataType.UBYTE,
                    ecu_address=0x2000 + offset,
                    lower_limit=0,
                    upper_limit=255,
                )
            )
        client = XcpClient(link)

        client.connect()
        values = client.read_measurements(definitions)
        client.disconnect()

        self.assertEqual(values["Signal0"], 1.0)
        self.assertEqual(values["Signal7"], 8.0)
        upload_payloads = [payload for payload in link.exchanges if payload[0] == UPLOAD]
        self.assertEqual([payload[1] for payload in upload_payloads], [7, 1])

    def test_read_memory_respects_configured_max_payload_bytes(self) -> None:
        link = _FakeLink()
        for offset in range(5):
            link.memory[(0, 0x3000 + offset)] = 0x20 + offset
        client = XcpClient(link, max_payload_bytes=3)

        client.connect()
        data = client.read_memory(0, 0x3000, 5)
        client.disconnect()

        self.assertEqual(data, bytes([0x20, 0x21, 0x22, 0x23, 0x24]))
        upload_payloads = [payload for payload in link.exchanges if payload[0] == UPLOAD]
        self.assertEqual([payload[1] for payload in upload_payloads], [3, 2])


if __name__ == "__main__":
    unittest.main()

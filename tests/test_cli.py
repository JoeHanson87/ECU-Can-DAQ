import argparse
import asyncio
from pathlib import Path
from tempfile import NamedTemporaryFile
import unittest

from ecu_can_daq.cli import _run

EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples"


class CliValidationTests(unittest.TestCase):
    def _build_args(self, measurements_path: str) -> argparse.Namespace:
        return argparse.Namespace(
            a2l=str(EXAMPLES_DIR / "sample.a2l"),
            measurements=measurements_path,
            transport="simulated",
            host="127.0.0.1",
            port=9500,
            rate_hz=20.0,
            duration=0.01,
            channel="CAN1",
            request_id=0x7E0,
            response_id=0x7E8,
            bitrate=500000,
            can_termination=False,
        )

    def test_run_rejects_empty_measurement_list(self) -> None:
        with NamedTemporaryFile("w", encoding="utf-8") as handle:
            with self.assertRaisesRegex(
                ValueError, "At least one measurement must be requested"
            ):
                asyncio.run(_run(self._build_args(handle.name)))

    def test_run_rejects_more_than_200_measurements(self) -> None:
        with NamedTemporaryFile("w", encoding="utf-8") as handle:
            handle.write("\n".join(["EngineSpeed"] * 201))
            handle.flush()
            with self.assertRaisesRegex(
                ValueError, "At most 200 measurements may be requested"
            ):
                asyncio.run(_run(self._build_args(handle.name)))


if __name__ == "__main__":
    unittest.main()

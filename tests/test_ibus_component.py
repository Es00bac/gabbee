from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gabbee.ibus_component import COMPONENT_NAME, ENGINE_NAME, render_component_xml, write_user_component_file


class IBusComponentTests(unittest.TestCase):
    def test_render_component_xml_contains_engine_metadata(self) -> None:
        xml = render_component_xml("/tmp/gabbee-engine")
        self.assertIn(f"<name>{COMPONENT_NAME}</name>", xml)
        self.assertIn(f"<name>{ENGINE_NAME}</name>", xml)
        self.assertIn("<icon>gabbee</icon>", xml)
        self.assertIn("<exec>/tmp/gabbee-engine --ibus</exec>", xml)

    def test_write_user_component_file_writes_destination(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "gabbee.xml"
            path = write_user_component_file("/tmp/gabbee-engine", destination=destination)
            self.assertEqual(path, destination)
            self.assertTrue(destination.exists())
            self.assertIn("/tmp/gabbee-engine --ibus", destination.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

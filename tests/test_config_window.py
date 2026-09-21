from __future__ import annotations

from pathlib import Path
import os
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PyQt6.QtWidgets import QApplication

from gabbee.app_paths import AppPaths
from gabbee.config import AppConfig
from gabbee.ui.config_window import ConfigWindow


def make_config(root: Path) -> AppConfig:
    return AppConfig(
        paths=AppPaths(root / "config", root / "state", root / "cache", root / "runtime"),
        env_file=root / ".env",
        env_values={},
        stt_provider="mock",
        language_code="en",
        elevenlabs_model_id="scribe_v2",
        elevenlabs_base_url="https://api.elevenlabs.io/v1",
        audio_source=None,
        sample_rate=16000,
        fallback_sink="clipboard",
        ui_title="Gabbee",
    )


class ConfigWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_has_command_studio_button(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            window = ConfigWindow(make_config(Path(tmp)))

            self.assertEqual(window.command_studio_button.text(), "Command Studio...")
            window.close()


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

from pathlib import Path
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gabbee.app_paths import AppPaths
from gabbee.config import load_config


class ConfigTests(unittest.TestCase):
    def test_loads_env_file_without_hardcoding_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_file = root / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "ELEVENLABS_API_KEY=test-key",
                        "GABBEE_LANGUAGE_CODE=en",
                        "GABBEE_STT_PROVIDER=elevenlabs",
                    ]
                ),
                encoding="utf-8",
            )
            paths = AppPaths(
                config_dir=root / "config",
                state_dir=root / "state",
                cache_dir=root / "cache",
                runtime_dir=root / "runtime",
            )
            with patch.dict(os.environ, {"GABBEE_ENV_FILE": str(env_file)}, clear=False):
                config = load_config(paths)

            self.assertEqual(config.env_file, env_file)
            self.assertEqual(config.stt_provider, "elevenlabs")
            self.assertEqual(config.language_code, "en")
            self.assertEqual(config.elevenlabs_api_key(), "test-key")

    def test_defaults_to_mock_without_api_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = AppPaths(
                config_dir=root / "config",
                state_dir=root / "state",
                cache_dir=root / "cache",
                runtime_dir=root / "runtime",
            )
            with patch.dict(os.environ, {"GABBEE_ENV_FILE": str(root / "missing.env")}, clear=True):
                config = load_config(paths)

            self.assertEqual(config.stt_provider, "mock")

    def test_process_environment_overrides_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_file = root / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "ELEVENLABS_API_KEY=file-key",
                        "GABBEE_STT_PROVIDER=mock",
                    ]
                ),
                encoding="utf-8",
            )
            paths = AppPaths(
                config_dir=root / "config",
                state_dir=root / "state",
                cache_dir=root / "cache",
                runtime_dir=root / "runtime",
            )
            with patch.dict(
                os.environ,
                {
                    "GABBEE_ENV_FILE": str(env_file),
                    "ELEVENLABS_API_KEY": "process-key",
                    "GABBEE_STT_PROVIDER": "elevenlabs",
                },
                clear=False,
            ):
                config = load_config(paths)

            self.assertEqual(config.stt_provider, "elevenlabs")
            self.assertEqual(config.elevenlabs_api_key(), "process-key")

    def test_reads_local_whisper_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_file = root / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "GABBEE_STT_PROVIDER=whisper_local",
                        "GABBEE_WHISPER_LOCAL_MODEL=tiny",
                        "GABBEE_WHISPER_LOCAL_DEVICE=cpu",
                        "GABBEE_WHISPER_LOCAL_COMPUTE_TYPE=default",
                    ]
                ),
                encoding="utf-8",
            )
            paths = AppPaths(
                config_dir=root / "config",
                state_dir=root / "state",
                cache_dir=root / "cache",
                runtime_dir=root / "runtime",
            )
            with patch.dict(os.environ, {"GABBEE_ENV_FILE": str(env_file)}, clear=False):
                config = load_config(paths)

            self.assertEqual(config.stt_provider, "whisper_local")
            self.assertEqual(config.whisper_local_model, "tiny")
            self.assertEqual(config.whisper_local_device, "cpu")
            self.assertEqual(config.whisper_local_compute_type, "default")
            self.assertEqual(config.provider_label(), "Whisper (local)")

    def test_defaults_toggle_shortcut_to_f5(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = AppPaths(
                config_dir=root / "config",
                state_dir=root / "state",
                cache_dir=root / "cache",
                runtime_dir=root / "runtime",
            )
            with patch.dict(os.environ, {"GABBEE_ENV_FILE": str(root / "missing.env")}, clear=True):
                config = load_config(paths)

            self.assertEqual(config.toggle_shortcut, "F5")

    def test_reads_toggle_shortcut_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_file = root / ".env"
            env_file.write_text("GABBEE_TOGGLE_SHORTCUT=F6\n", encoding="utf-8")
            paths = AppPaths(
                config_dir=root / "config",
                state_dir=root / "state",
                cache_dir=root / "cache",
                runtime_dir=root / "runtime",
            )
            with patch.dict(os.environ, {"GABBEE_ENV_FILE": str(env_file)}, clear=False):
                config = load_config(paths)

            self.assertEqual(config.toggle_shortcut, "F6")


if __name__ == "__main__":
    unittest.main()

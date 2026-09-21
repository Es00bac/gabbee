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

    def test_wallet_key_is_used_when_no_explicit_key_is_configured(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = AppPaths(
                config_dir=root / "config",
                state_dir=root / "state",
                cache_dir=root / "cache",
                runtime_dir=root / "runtime",
            )
            with patch.dict(os.environ, {"GABBEE_ENV_FILE": str(root / "missing.env")}, clear=True), patch(
                "gabbee.secret_store.load_api_key", return_value="wallet-key"
            ):
                config = load_config(paths)

            self.assertEqual(config.elevenlabs_api_key(), "wallet-key")
            self.assertEqual(config.stt_provider, "elevenlabs")

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

    def test_reads_local_whisper_rocm_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_file = root / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "GABBEE_STT_PROVIDER=whisper_local",
                        "GABBEE_WHISPER_LOCAL_MODEL=small",
                        "GABBEE_WHISPER_LOCAL_DEVICE=rocm",
                        "GABBEE_WHISPER_LOCAL_COMPUTE_TYPE=float16",
                        "GABBEE_WHISPER_LOCAL_ROCM_GFX_VERSION=10.3.0",
                        "GABBEE_WHISPER_LOCAL_FALLBACK_DEVICE=cpu",
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
            self.assertEqual(config.whisper_local_model, "small")
            self.assertEqual(config.whisper_local_device, "rocm")
            self.assertEqual(config.whisper_local_compute_type, "float16")
            self.assertEqual(config.whisper_local_rocm_gfx_version, "10.3.0")
            self.assertEqual(config.whisper_local_fallback_device, "cpu")

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
            self.assertEqual(config.command_shortcut, "F6")

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

    def test_reads_command_shortcut_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_file = root / ".env"
            env_file.write_text("GABBEE_COMMAND_SHORTCUT=Ctrl+F6\n", encoding="utf-8")
            paths = AppPaths(
                config_dir=root / "config",
                state_dir=root / "state",
                cache_dir=root / "cache",
                runtime_dir=root / "runtime",
            )
            with patch.dict(os.environ, {"GABBEE_ENV_FILE": str(env_file)}, clear=False):
                config = load_config(paths)

            self.assertEqual(config.command_shortcut, "Ctrl+F6")

    def test_gemini_default_model_is_consistent(self) -> None:
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

            self.assertEqual(config.gemini_model, "gemini-2.5-flash")

    def test_reads_vertex_gemini_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_file = root / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "GABBEE_STT_PROVIDER=gemini",
                        "GABBEE_VERTEX_PROJECT=project-38890c01-de5b-44c9-be4",
                        "GABBEE_VERTEX_LOCATION=us-west1",
                        "GABBEE_GEMINI_MODEL=gemini-3.1-flash",
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

            self.assertEqual(config.stt_provider, "gemini")
            self.assertEqual(config.vertex_project, "project-38890c01-de5b-44c9-be4")
            self.assertEqual(config.vertex_location, "us-west1")
            self.assertEqual(config.gemini_model, "gemini-3.1-flash")
            self.assertEqual(config.provider_label(), "Gemini (Vertex)")


if __name__ == "__main__":
    unittest.main()

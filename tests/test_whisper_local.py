from __future__ import annotations

from pathlib import Path
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gabbee.app_paths import AppPaths
from gabbee.config import load_config
from gabbee.stt.whisper_local import WhisperLocalSpeechToText


class WhisperLocalTests(unittest.TestCase):
    def test_rocm_device_sets_hsa_override_and_falls_back_to_cpu(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_file = root / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "GABBEE_STT_PROVIDER=whisper_local",
                        "GABBEE_WHISPER_LOCAL_MODEL=tiny",
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

        calls: list[dict[str, str]] = []

        class FakeWhisperModel:
            def __init__(self, model_name: str, *, device: str, compute_type: str) -> None:
                calls.append(
                    {
                        "model_name": model_name,
                        "device": device,
                        "compute_type": compute_type,
                    }
                )
                if device == "cuda":
                    raise RuntimeError("GPU unavailable")

        fake_module = types.SimpleNamespace(WhisperModel=FakeWhisperModel)

        with patch.dict(sys.modules, {"faster_whisper": fake_module}), patch.dict(os.environ, {}, clear=True):
            provider = WhisperLocalSpeechToText(config)
            hsa_override = os.environ.get("HSA_OVERRIDE_GFX_VERSION")

        self.assertEqual(hsa_override, "10.3.0")
        self.assertEqual(calls[0]["device"], "cuda")
        self.assertEqual(calls[0]["compute_type"], "float16")
        self.assertEqual(calls[1]["device"], "cpu")
        self.assertEqual(calls[1]["compute_type"], "default")
        self.assertEqual(provider.device, "cpu")
        self.assertIn("GPU unavailable", provider.fallback_reason)


if __name__ == "__main__":
    unittest.main()

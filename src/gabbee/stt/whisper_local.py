from __future__ import annotations

import os
from pathlib import Path

from ..config import AppConfig
from ..models import TranscriptionResult


_ROCM_DEVICE_ALIASES = {
    "amd",
    "hip",
    "rocm",
    "rx5700xt",
    "rx-5700-xt",
    "rx_5700_xt",
}


def _resolve_faster_whisper_device(device: str) -> str:
    normalized = (device or "cpu").strip().lower()
    if normalized in _ROCM_DEVICE_ALIASES:
        return "cuda"
    return normalized


def _fallback_compute_type(compute_type: str, device: str) -> str:
    if device == "cpu" and compute_type.strip().lower() in {"float16", "bfloat16"}:
        return "default"
    return compute_type


class WhisperLocalSpeechToText:
    provider_name = "whisper_local"

    def __init__(self, config: AppConfig) -> None:
        self.model_name = config.whisper_local_model
        self.requested_device = (config.whisper_local_device or "cpu").strip().lower()
        self.device = _resolve_faster_whisper_device(self.requested_device)
        self.compute_type = config.whisper_local_compute_type
        self.language_code = config.language_code
        self.fallback_reason = ""

        if self.requested_device in _ROCM_DEVICE_ALIASES:
            rocm_gfx_version = config.whisper_local_rocm_gfx_version or "10.3.0"
            os.environ.setdefault("HSA_OVERRIDE_GFX_VERSION", rocm_gfx_version)

        try:
            from faster_whisper import WhisperModel
        except Exception as exc:  # pragma: no cover - optional dependency path
            raise RuntimeError(
                "whisper_local provider requires faster-whisper. Install with `pip install -e .[whisper_local]` or `pip install faster-whisper`."
            ) from exc

        try:
            self._model = WhisperModel(
                self.model_name,
                device=self.device,
                compute_type=self.compute_type,
            )
        except Exception as exc:
            fallback_device = _resolve_faster_whisper_device(config.whisper_local_fallback_device)
            if not fallback_device or fallback_device == self.device:
                raise
            fallback_compute_type = _fallback_compute_type(self.compute_type, fallback_device)
            self._model = WhisperModel(
                self.model_name,
                device=fallback_device,
                compute_type=fallback_compute_type,
            )
            self.fallback_reason = f"{self.requested_device} initialization failed: {exc}"
            self.device = fallback_device
            self.compute_type = fallback_compute_type

    def transcribe(self, audio_path: Path) -> TranscriptionResult:
        segments, _ = self._model.transcribe(
            str(audio_path),
            language=self.language_code or None,
        )
        text_parts: list[str] = []
        for segment in segments:
            text = (segment.text or "").strip()
            if text:
                text_parts.append(text)
        text = " ".join(text_parts).strip()
        if not text:
            raise RuntimeError("Whisper local returned an empty transcript.")
        return TranscriptionResult(
            text=text,
            provider=self.provider_name,
            language_code=self.language_code,
        )

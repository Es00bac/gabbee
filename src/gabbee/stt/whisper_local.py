from __future__ import annotations

from pathlib import Path

from ..config import AppConfig
from ..models import TranscriptionResult


class WhisperLocalSpeechToText:
    provider_name = "whisper_local"

    def __init__(self, config: AppConfig) -> None:
        self.model_name = config.whisper_local_model
        self.device = config.whisper_local_device
        self.compute_type = config.whisper_local_compute_type
        self.language_code = config.language_code

        try:
            from faster_whisper import WhisperModel
        except Exception as exc:  # pragma: no cover - optional dependency path
            raise RuntimeError(
                "whisper_local provider requires faster-whisper. Install with `pip install -e .[whisper_local]` or `pip install faster-whisper`."
            ) from exc

        self._model = WhisperModel(
            self.model_name,
            device=self.device,
            compute_type=self.compute_type,
        )

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

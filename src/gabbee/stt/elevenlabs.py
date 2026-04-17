from __future__ import annotations

from pathlib import Path
import requests

from ..config import AppConfig
from ..models import TranscriptionResult


class ElevenLabsSpeechToText:
    provider_name = "elevenlabs"

    def __init__(self, config: AppConfig) -> None:
        self.base_url = config.elevenlabs_base_url.rstrip("/")
        self.model_id = config.elevenlabs_model_id
        self.language_code = config.language_code
        self.api_key = config.elevenlabs_api_key()
        if not self.api_key:
            raise RuntimeError("ELEVENLABS_API_KEY is missing from the configured environment file.")

    def transcribe(self, audio_path: Path) -> TranscriptionResult:
        with audio_path.open("rb") as handle:
            response = requests.post(
                f"{self.base_url}/speech-to-text",
                headers={"xi-api-key": self.api_key},
                data={
                    "model_id": self.model_id,
                    "language_code": self.language_code,
                    "timestamps_granularity": "none",
                    "tag_audio_events": "false",
                    "diarize": "false",
                },
                files={"file": (audio_path.name, handle, "audio/wav")},
                timeout=120,
            )
        response.raise_for_status()
        body = response.json()
        text = str(body.get("text", "")).strip()
        if not text:
            raise RuntimeError("ElevenLabs returned an empty transcript.")
        return TranscriptionResult(
            text=text,
            provider=self.provider_name,
            language_code=str(body.get("language_code", self.language_code)),
        )

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ..models import TranscriptionResult


class SpeechToTextProvider(Protocol):
    provider_name: str

    def transcribe(self, audio_path: Path) -> TranscriptionResult:
        ...

from __future__ import annotations

from pathlib import Path

from ..models import TranscriptionResult


class MockSpeechToText:
    provider_name = "mock"

    def transcribe(self, audio_path: Path) -> TranscriptionResult:
        return TranscriptionResult(
            text=f"[mock transcript from {audio_path.name}]",
            provider=self.provider_name,
            language_code="en",
        )

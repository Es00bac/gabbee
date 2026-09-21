from __future__ import annotations

from pathlib import Path
from typing import Callable, Protocol

from ..models import TranscriptionResult


class SpeechToTextProvider(Protocol):
    provider_name: str

    def transcribe(self, audio_path: Path) -> TranscriptionResult:
        ...


PartialCallback = Callable[[str], None]
CommittedCallback = Callable[[TranscriptionResult], None]
ErrorCallback = Callable[[Exception], None]


class RealtimeSession(Protocol):
    def push_audio(self, pcm: bytes) -> None:
        ...

    def commit(self, timeout: float | None = None) -> TranscriptionResult:
        ...

    def cancel(self) -> None:
        ...


class StreamingSpeechToTextProvider(SpeechToTextProvider, Protocol):
    def open_session(
        self,
        *,
        previous_text: str = "",
        keyterms: list[str] | None = None,
        entity_detection: list[str] | None = None,
        on_partial: PartialCallback | None = None,
        on_committed: CommittedCallback | None = None,
        on_error: ErrorCallback | None = None,
    ) -> RealtimeSession:
        ...

    def push_audio(self, pcm: bytes) -> None:
        ...

    def commit(self, timeout: float | None = None) -> TranscriptionResult:
        ...

    def cancel(self) -> None:
        ...

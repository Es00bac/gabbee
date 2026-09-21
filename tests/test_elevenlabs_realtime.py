from __future__ import annotations

import json
from pathlib import Path
from queue import Empty, Queue
import time
from urllib.parse import parse_qs, urlsplit

import pytest

from gabbee.app_paths import AppPaths
from gabbee.config import AppConfig
from gabbee.controller import GabbeeController
from gabbee.models import DeliveryResult, TranscriptionResult
from gabbee.stt.elevenlabs import (
    ElevenLabsRealtimeError,
    ElevenLabsRealtimeSession,
    ElevenLabsSpeechToText,
)


class FakeTransport:
    def __init__(self, events=(), *, on_send=None) -> None:
        self.events: Queue[object] = Queue()
        for event in events:
            self.events.put(event)
        self.on_send = on_send
        self.sent: list[dict] = []
        self.closed = False

    def send(self, message: str) -> None:
        decoded = json.loads(message)
        self.sent.append(decoded)
        if self.on_send:
            self.on_send(decoded, self)

    def recv(self, timeout=None):
        try:
            event = self.events.get(timeout=timeout)
        except Empty as exc:
            raise TimeoutError from exc
        if isinstance(event, Exception):
            raise event
        return json.dumps(event)

    def close(self, code=1000, reason="") -> None:
        self.closed = True


def started_event():
    return {
        "message_type": "session_started",
        "session_id": "session-1",
        "config": {"language_code": "en"},
    }


def test_partial_manual_commit_companion_events_and_previous_text() -> None:
    partials = []
    committed_callbacks = []

    def on_send(message, transport):
        if message.get("commit"):
            transport.events.put({"message_type": "committed_transcript", "text": "call 303"})
            transport.events.put(
                {
                    "message_type": "committed_transcript_with_timestamps",
                    "language_code": "en",
                    "words": [{"text": "call", "start": 0.0, "end": 0.2}],
                }
            )
            transport.events.put(
                {
                    "message_type": "committed_transcript_entities",
                    "text": "call 303",
                    "entities": [
                        {"type": "phone", "text": "303", "start_character": 5, "end_character": 8}
                    ],
                }
            )

    transport = FakeTransport(
        [started_event(), {"message_type": "partial_transcript", "text": "call three"}],
        on_send=on_send,
    )
    session = ElevenLabsRealtimeSession(
        transport,
        language_code="en",
        previous_text="x" * 60,
        on_partial=partials.append,
        on_committed=committed_callbacks.append,
        expect_timestamps=True,
        expect_entities=True,
    )
    session.push_audio(b"\x01\x02")
    result = session.commit(timeout=1)

    assert partials == ["call three"]
    assert len(committed_callbacks) == 1
    assert result.text == "call 303"
    assert result.session_id == "session-1"
    assert [word.text for word in result.words] == ["call"]
    assert result.entities[0].text == "303"
    assert transport.sent[0]["previous_text"] == "x" * 50
    assert "previous_text" not in transport.sent[1]
    assert transport.sent[1]["commit"] is True
    assert transport.closed


@pytest.mark.parametrize("event_type", ["auth_error", "quota_exceeded", "rate_limited"])
def test_provider_failures_are_categorized(event_type: str) -> None:
    transport = FakeTransport([{"message_type": event_type, "error": "denied"}])
    with pytest.raises(ElevenLabsRealtimeError) as caught:
        ElevenLabsRealtimeSession(transport, language_code="en", start_timeout=0.2)
    assert caught.value.category == event_type


def test_cancel_prevents_commit() -> None:
    session = ElevenLabsRealtimeSession(FakeTransport([started_event()]), language_code="en")
    session.cancel()
    with pytest.raises(ElevenLabsRealtimeError, match="cancelled"):
        session.commit(timeout=0.01)


def make_config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        paths=AppPaths(
            config_dir=tmp_path / "config",
            state_dir=tmp_path / "state",
            cache_dir=tmp_path / "cache",
            runtime_dir=tmp_path / "runtime",
        ),
        env_file=tmp_path / ".env",
        env_values={"ELEVENLABS_API_KEY": "secret"},
        stt_provider="elevenlabs",
        language_code="en",
        elevenlabs_model_id="scribe_v2",
        elevenlabs_base_url="https://api.elevenlabs.io/v1",
        audio_source=None,
        sample_rate=16000,
        fallback_sink="clipboard",
        ui_title="Gabbee",
    )


def test_provider_url_caps_realtime_keyterms_and_enables_selected_entities(tmp_path) -> None:
    captured = {}

    def connector(url, headers):
        captured.update(url=url, headers=headers)
        return FakeTransport([started_event()])

    provider = ElevenLabsSpeechToText(make_config(tmp_path), connector=connector)
    session = provider.open_session(
        keyterms=[f"term-{index}" for index in range(80)],
        entity_detection=["phone", "date"],
    )
    query = parse_qs(urlsplit(captured["url"]).query)
    assert query["model_id"] == ["scribe_v2_realtime"]
    assert query["commit_strategy"] == ["manual"]
    assert len(query["keyterms"]) == 50
    assert query["entity_detection"] == ["phone_number", "date"]
    assert captured["headers"] == {"xi-api-key": "secret"}
    session.cancel()


def test_provider_retries_one_initial_connection_failure(tmp_path) -> None:
    calls = []

    def connector(url, headers):
        calls.append(url)
        if len(calls) == 1:
            raise OSError("temporary network failure")
        return FakeTransport([started_event()])

    provider = ElevenLabsSpeechToText(make_config(tmp_path), connector=connector)
    session = provider.open_session()
    assert len(calls) == 2
    session.cancel()


class StreamingRecorder:
    def __init__(self) -> None:
        self.is_recording = False
        self.path = None

    def start_streaming(self, path, source_name, on_audio):
        self.is_recording = True
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"recovery wav")
        on_audio(b"pcm")

    def start(self, path, source_name=None):
        self.start_streaming(path, source_name, lambda _chunk: None)

    def stop(self):
        self.is_recording = False
        return self.path

    def cancel(self):
        self.is_recording = False


class FailingSession:
    def __init__(self, on_partial) -> None:
        self.on_partial = on_partial
        self.cancelled = False

    def push_audio(self, chunk):
        self.on_partial("live partial")

    def commit(self):
        raise ElevenLabsRealtimeError("connection", "network lost")

    def cancel(self):
        self.cancelled = True


class RecoveringProvider:
    provider_name = "elevenlabs"

    def __init__(self) -> None:
        self.batch_calls = 0
        self.session = None

    def open_session(self, **kwargs):
        self.session = FailingSession(kwargs["on_partial"])
        return self.session

    def transcribe(self, path, **kwargs):
        self.batch_calls += 1
        return TranscriptionResult("recovered once", "elevenlabs", "en")


class Sink:
    def __init__(self) -> None:
        self.values = []

    def deliver(self, text):
        self.values.append(text)
        return DeliveryResult(True, "fake")

    def deliver_key(self, key):
        return DeliveryResult(True, "fake")


def test_controller_uses_batch_recovery_and_delivers_exactly_once(tmp_path) -> None:
    provider, sink = RecoveringProvider(), Sink()
    controller = GabbeeController(
        make_config(tmp_path),
        recorder=StreamingRecorder(),
        transcriber=provider,
        sink=sink,
    )
    controller.start()
    deadline = time.monotonic() + 1
    while not controller.snapshot().partial_text and time.monotonic() < deadline:
        time.sleep(0.01)
    assert controller.snapshot().partial_text == "live partial"
    assert sink.values == []
    controller.stop()
    controller.wait_for_background(2)
    assert provider.batch_calls == 1
    assert sink.values == ["recovered once"]
    assert controller.last_dictation is not None and controller.last_dictation.delivered
    assert not list((tmp_path / "cache").glob("recording-*.wav"))

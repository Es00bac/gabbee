from __future__ import annotations

import base64
from collections.abc import Callable
import json
from pathlib import Path
import threading
import time
from typing import Any, Protocol
from urllib.parse import urlencode, urlsplit, urlunsplit

import requests

from ..config import AppConfig
from ..models import SemanticSpan, TranscriptionResult, WordTiming
from .base import CommittedCallback, ErrorCallback, PartialCallback


REALTIME_MODEL_ID = "scribe_v2_realtime"
ENTITY_HINT_TO_PROVIDER = {
    "phone": "phone_number",
    "zip": "location_zip",
    "date": "date",
    "time": "time",
    "money": "money",
    "measurement": "measurement",
    "id": "generic_id",
    "url": "url",
    "filename": "filename",
    "ip": "ip_address",
}
ERROR_EVENTS = {
    "error",
    "auth_error",
    "quota_exceeded",
    "transcriber_error",
    "input_error",
    "invalid_request",
    "commit_throttled",
    "unaccepted_terms",
    "rate_limited",
    "queue_overflow",
    "resource_exhausted",
    "session_time_limit_exceeded",
    "chunk_size_exceeded",
    "insufficient_audio_activity",
}


class ElevenLabsRealtimeError(RuntimeError):
    def __init__(self, category: str, detail: str) -> None:
        super().__init__(detail)
        self.category = category


class WebSocketTransport(Protocol):
    def send(self, message: str) -> None:
        ...

    def recv(self, timeout: float | None = None) -> str | bytes:
        ...

    def close(self, code: int = 1000, reason: str = "") -> None:
        ...


Connector = Callable[[str, dict[str, str]], WebSocketTransport]


def _default_connector(url: str, headers: dict[str, str]) -> WebSocketTransport:
    try:
        from websockets.sync.client import connect
    except ImportError as exc:  # pragma: no cover - packaging/runtime guard
        raise RuntimeError(
            "Realtime ElevenLabs dictation requires the 'websockets' package."
        ) from exc
    return connect(
        url,
        additional_headers=headers,
        open_timeout=10,
        close_timeout=3,
        max_size=4 * 1024 * 1024,
    )


class ElevenLabsRealtimeSession:
    def __init__(
        self,
        transport: WebSocketTransport,
        *,
        language_code: str,
        previous_text: str = "",
        on_partial: PartialCallback | None = None,
        on_committed: CommittedCallback | None = None,
        on_error: ErrorCallback | None = None,
        start_timeout: float = 10.0,
        expect_timestamps: bool = False,
        expect_entities: bool = False,
        companion_timeout: float = 0.35,
    ) -> None:
        self.transport = transport
        self.language_code = language_code
        # The API only accepts previous_text on the first chunk and recommends
        # fewer than 50 characters. Never derive this from screen contents.
        self.previous_text = previous_text[-50:]
        self.on_partial = on_partial or (lambda _text: None)
        self.on_committed = on_committed or (lambda _result: None)
        self.on_error = on_error or (lambda _error: None)
        self.expect_timestamps = expect_timestamps
        self.expect_entities = expect_entities
        self.companion_timeout = max(0.0, companion_timeout)
        self.session_id = ""
        self.partial_text = ""
        self._committed_parts: list[str] = []
        self._words: list[WordTiming] = []
        self._entities: list[SemanticSpan] = []
        self._error: Exception | None = None
        self._first_chunk = True
        self._closed = False
        self._cancelled = False
        self._lock = threading.Lock()
        self._started = threading.Event()
        self._commit_received = threading.Event()
        self._timestamps_received = threading.Event()
        self._entities_received = threading.Event()
        self._companion_received = threading.Event()
        self._receiver = threading.Thread(
            target=self._receive_loop,
            daemon=True,
            name="gabbee-elevenlabs-realtime",
        )
        self._receiver.start()
        if not self._started.wait(timeout=start_timeout):
            self.cancel()
            raise ElevenLabsRealtimeError("connection_timeout", "ElevenLabs realtime session did not start in time.")
        if self._error is not None:
            self._close(reason="startup error")
            raise self._error

    @property
    def failed(self) -> bool:
        return self._error is not None

    def push_audio(self, pcm: bytes) -> None:
        if not pcm:
            return
        with self._lock:
            self._raise_if_unusable()
            message: dict[str, Any] = {
                "message_type": "input_audio_chunk",
                "audio_base_64": base64.b64encode(pcm).decode("ascii"),
                "sample_rate": 16000,
            }
            if self._first_chunk:
                if self.previous_text:
                    message["previous_text"] = self.previous_text
                self._first_chunk = False
            self.transport.send(json.dumps(message, separators=(",", ":")))

    def commit(self, timeout: float | None = None) -> TranscriptionResult:
        timeout = 20.0 if timeout is None else timeout
        with self._lock:
            self._raise_if_unusable()
            self._commit_received.clear()
            message: dict[str, Any] = {
                "message_type": "input_audio_chunk",
                "audio_base_64": "",
                "sample_rate": 16000,
                "commit": True,
            }
            if self._first_chunk:
                if self.previous_text:
                    message["previous_text"] = self.previous_text
                self._first_chunk = False
            self.transport.send(json.dumps(message, separators=(",", ":")))
        if not self._commit_received.wait(timeout=max(0.01, timeout)):
            if self._error is not None:
                raise self._error
            raise ElevenLabsRealtimeError("commit_timeout", "ElevenLabs did not return a committed transcript in time.")
        if self._error is not None:
            raise self._error
        self._wait_for_companions()
        if self._error is not None:
            raise self._error
        result = self.result()
        if not result.text:
            raise ElevenLabsRealtimeError("empty_transcript", "ElevenLabs returned an empty realtime transcript.")
        self._close()
        return result

    def _wait_for_companions(self) -> None:
        if not self.expect_timestamps and not self.expect_entities:
            return
        deadline = time.monotonic() + self.companion_timeout
        while time.monotonic() < deadline:
            self._companion_received.clear()
            timestamps_done = not self.expect_timestamps or self._timestamps_received.is_set()
            entities_done = not self.expect_entities or self._entities_received.is_set()
            if timestamps_done and entities_done:
                return
            self._companion_received.wait(max(0.0, deadline - time.monotonic()))

    def result(self) -> TranscriptionResult:
        with self._lock:
            text = " ".join(part.strip() for part in self._committed_parts if part.strip()).strip()
            return TranscriptionResult(
                text=text,
                provider="elevenlabs",
                language_code=self.language_code,
                words=list(self._words),
                entities=list(self._entities),
                session_id=self.session_id,
            )

    def cancel(self) -> None:
        self._cancelled = True
        self._close(reason="cancelled")
        self._commit_received.set()
        self._started.set()

    def _close(self, reason: str = "complete") -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.transport.close(1000, reason)
        except Exception:
            pass

    def _raise_if_unusable(self) -> None:
        if self._cancelled:
            raise ElevenLabsRealtimeError("cancelled", "Realtime transcription was cancelled.")
        if self._error is not None:
            raise self._error
        if self._closed:
            raise ElevenLabsRealtimeError("closed", "Realtime transcription session is closed.")

    def _receive_loop(self) -> None:
        try:
            while not self._closed:
                try:
                    raw = self.transport.recv(timeout=1.0)
                except TimeoutError:
                    continue
                except Exception as exc:
                    if self._closed or self._cancelled:
                        break
                    if self._commit_received.is_set():
                        self._companion_received.set()
                        break
                    self._fail(ElevenLabsRealtimeError("connection", f"ElevenLabs realtime connection failed: {exc}"))
                    break
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                try:
                    event = json.loads(raw)
                except (json.JSONDecodeError, TypeError) as exc:
                    self._fail(ElevenLabsRealtimeError("protocol", f"Invalid ElevenLabs realtime event: {exc}"))
                    break
                self._handle_event(event)
        finally:
            self._started.set()

    def _handle_event(self, event: object) -> None:
        if not isinstance(event, dict):
            self._fail(ElevenLabsRealtimeError("protocol", "ElevenLabs returned a non-object event."))
            return
        kind = str(event.get("message_type") or event.get("type") or "")
        if kind == "session_started":
            self.session_id = str(event.get("session_id") or "")
            config = event.get("config")
            if isinstance(config, dict) and config.get("language_code"):
                self.language_code = str(config["language_code"])
            self._started.set()
            return
        if kind == "partial_transcript":
            self.partial_text = str(event.get("text") or "")
            self.on_partial(self.partial_text)
            return
        if kind == "committed_transcript":
            text = str(event.get("text") or "").strip()
            if text:
                with self._lock:
                    self._committed_parts.append(text)
                self.on_committed(self.result())
            self.partial_text = ""
            self._commit_received.set()
            return
        if kind == "committed_transcript_with_timestamps":
            self._consume_timestamps(event)
            return
        if kind == "committed_transcript_entities":
            self._consume_entities(event)
            return
        if kind in ERROR_EVENTS or (kind.endswith("_error") and kind != "warning"):
            detail = str(event.get("error") or event.get("message") or event.get("detail") or kind)
            self._fail(ElevenLabsRealtimeError(kind or "error", detail))
            return
        # session warnings are intentionally non-fatal.

    def _consume_timestamps(self, event: dict[str, Any]) -> None:
        words = event.get("words") or event.get("word_timestamps") or []
        if not isinstance(words, list):
            return
        parsed: list[WordTiming] = []
        for item in words:
            if not isinstance(item, dict):
                continue
            try:
                parsed.append(
                    WordTiming(
                        text=str(item.get("text") or item.get("word") or item.get("character") or ""),
                        start=float(item.get("start") if item.get("start") is not None else item.get("start_time", 0)),
                        end=float(item.get("end") if item.get("end") is not None else item.get("end_time", 0)),
                        kind=str(item.get("type") or "word"),
                        speaker_id=str(item["speaker_id"]) if item.get("speaker_id") is not None else None,
                    )
                )
            except (TypeError, ValueError):
                continue
        with self._lock:
            self._words.extend(parsed)
            language = event.get("language_code")
            if language:
                self.language_code = str(language)
        self._timestamps_received.set()
        self._companion_received.set()

    def _consume_entities(self, event: dict[str, Any]) -> None:
        entities = event.get("entities") or []
        if not isinstance(entities, list):
            return
        # Entity offsets are per committed segment. Offset them into the joined
        # result while retaining strict source validation in the formatter.
        segment_text = str(event.get("text") or "")
        full = self.result().text
        base = max(0, full.rfind(segment_text)) if segment_text else 0
        parsed: list[SemanticSpan] = []
        for item in entities:
            if not isinstance(item, dict):
                continue
            try:
                start = int(item.get("start_character", item.get("start_char", item.get("start", 0)))) + base
                end = int(item.get("end_character", item.get("end_char", item.get("end", 0)))) + base
            except (TypeError, ValueError):
                continue
            entity_text = str(item.get("text") or full[start:end])
            parsed.append(
                SemanticSpan(
                    start=start,
                    end=end,
                    kind=str(item.get("type") or item.get("entity_type") or "entity"),
                    text=entity_text,
                    value=item.get("value"),
                    source="provider",
                    confidence=float(item["confidence"]) if isinstance(item.get("confidence"), (int, float)) else None,
                )
            )
        with self._lock:
            self._entities.extend(parsed)
        self._entities_received.set()
        self._companion_received.set()

    def _fail(self, error: Exception) -> None:
        if self._error is None:
            self._error = error
            self.on_error(error)
        self._started.set()
        self._commit_received.set()
        self._companion_received.set()


class ElevenLabsSpeechToText:
    provider_name = "elevenlabs"

    def __init__(self, config: AppConfig, *, connector: Connector | None = None) -> None:
        self.base_url = config.elevenlabs_base_url.rstrip("/")
        self.model_id = config.elevenlabs_model_id
        self.realtime_model_id = getattr(config, "elevenlabs_realtime_model_id", REALTIME_MODEL_ID)
        self.language_code = config.language_code
        self.config = config
        self.connector = connector or _default_connector
        self._active_session: ElevenLabsRealtimeSession | None = None

    def transcribe(
        self,
        audio_path: Path,
        *,
        keyterms: list[str] | None = None,
        entity_detection: list[str] | None = None,
    ) -> TranscriptionResult:
        api_key = self.config.elevenlabs_api_key()
        if not api_key:
            raise RuntimeError("ELEVENLABS_API_KEY is missing from the configured environment file.")
        data: list[tuple[str, str]] = [
            ("model_id", self.model_id),
            ("language_code", self.language_code),
            ("timestamps_granularity", "word"),
            ("tag_audio_events", "false"),
            ("diarize", "false"),
        ]
        for term in (keyterms or [])[:50]:
            data.append(("keyterms", term))
        for entity in _provider_entity_hints(entity_detection):
            data.append(("entity_detection", entity))
        with audio_path.open("rb") as handle:
            response = requests.post(
                f"{self.base_url}/speech-to-text",
                headers={"xi-api-key": api_key},
                data=data,
                files={"file": (audio_path.name, handle, "audio/wav")},
                timeout=120,
            )
        if response.status_code in {401, 403}:
            raise ElevenLabsRealtimeError("auth_error", "ElevenLabs rejected the configured API key.")
        if response.status_code == 402:
            raise ElevenLabsRealtimeError("quota_exceeded", "ElevenLabs transcription quota is exhausted.")
        if response.status_code == 429:
            raise ElevenLabsRealtimeError("rate_limited", "ElevenLabs transcription rate limit was reached.")
        response.raise_for_status()
        body = response.json()
        text = str(body.get("text", "")).strip()
        if not text:
            raise RuntimeError("ElevenLabs returned an empty transcript.")
        return TranscriptionResult(
            text=text,
            provider=self.provider_name,
            language_code=str(body.get("language_code", self.language_code)),
            words=_parse_batch_words(body.get("words")),
            entities=_parse_batch_entities(body.get("entities"), text),
        )

    def open_session(
        self,
        *,
        previous_text: str = "",
        keyterms: list[str] | None = None,
        entity_detection: list[str] | None = None,
        on_partial: PartialCallback | None = None,
        on_committed: CommittedCallback | None = None,
        on_error: ErrorCallback | None = None,
    ) -> ElevenLabsRealtimeSession:
        api_key = self.config.elevenlabs_api_key()
        if not api_key:
            raise RuntimeError("ELEVENLABS_API_KEY is missing from the configured environment file.")
        params: list[tuple[str, str]] = [
            ("model_id", self.realtime_model_id),
            ("audio_format", "pcm_16000"),
            ("language_code", self.language_code),
            ("commit_strategy", "manual"),
            ("include_timestamps", "true"),
        ]
        for term in (keyterms or [])[:50]:
            params.append(("keyterms", term))
        mapped_entities = _provider_entity_hints(entity_detection)
        for entity in mapped_entities:
            params.append(("entity_detection", entity))
        url = self._realtime_url(params)
        session = None
        last_error: Exception | None = None
        for attempt in range(2):
            transport = None
            try:
                transport = self.connector(url, {"xi-api-key": api_key})
                session = ElevenLabsRealtimeSession(
                    transport,
                    language_code=self.language_code,
                    previous_text=previous_text,
                    on_partial=on_partial,
                    on_committed=on_committed,
                    # Startup errors are raised to the caller. Install the
                    # runtime callback only after a successful retry so a
                    # recovered first connection does not arm batch fallback.
                    on_error=None,
                    expect_timestamps=True,
                    expect_entities=bool(mapped_entities),
                )
                break
            except Exception as exc:
                last_error = exc
                if transport is not None:
                    try:
                        transport.close(1000, "reconnect")
                    except Exception:
                        pass
                category = getattr(exc, "category", "connection")
                if attempt or category in {"auth_error", "quota_exceeded", "rate_limited", "unaccepted_terms"}:
                    raise
        if session is None:
            raise last_error or RuntimeError("Could not open ElevenLabs realtime session.")
        session.on_error = on_error or (lambda _error: None)
        self._active_session = session
        return session

    def push_audio(self, pcm: bytes) -> None:
        if self._active_session is None:
            raise RuntimeError("No ElevenLabs realtime session is open.")
        self._active_session.push_audio(pcm)

    def commit(self, timeout: float | None = None) -> TranscriptionResult:
        if self._active_session is None:
            raise RuntimeError("No ElevenLabs realtime session is open.")
        try:
            return self._active_session.commit(timeout)
        finally:
            self._active_session = None

    def cancel(self) -> None:
        if self._active_session is not None:
            self._active_session.cancel()
            self._active_session = None

    def _realtime_url(self, params: list[tuple[str, str]]) -> str:
        split = urlsplit(self.base_url)
        scheme = "wss" if split.scheme == "https" else "ws"
        path = split.path.rstrip("/") + "/speech-to-text/realtime"
        return urlunsplit((scheme, split.netloc, path, urlencode(params, doseq=True), ""))


def _parse_batch_words(raw: object) -> list[WordTiming]:
    if not isinstance(raw, list):
        return []
    result: list[WordTiming] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            result.append(
                WordTiming(
                    text=str(item.get("text") or item.get("word") or ""),
                    start=float(item.get("start", 0)),
                    end=float(item.get("end", 0)),
                    kind=str(item.get("type") or "word"),
                    speaker_id=str(item["speaker_id"]) if item.get("speaker_id") is not None else None,
                )
            )
        except (TypeError, ValueError):
            continue
    return result


def _provider_entity_hints(values: list[str] | tuple[str, ...] | None) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        mapped = ENTITY_HINT_TO_PROVIDER.get(value.casefold().strip())
        if mapped and mapped not in seen:
            result.append(mapped)
            seen.add(mapped)
    return result


def _parse_batch_entities(raw: object, text: str) -> list[SemanticSpan]:
    if not isinstance(raw, list):
        return []
    result: list[SemanticSpan] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            start = int(item.get("start_character", item.get("start_char", item.get("start", 0))))
            end = int(item.get("end_character", item.get("end_char", item.get("end", 0))))
        except (TypeError, ValueError):
            continue
        if start < 0 or end <= start or end > len(text):
            continue
        result.append(
            SemanticSpan(
                start,
                end,
                str(item.get("type") or item.get("entity_type") or "entity"),
                str(item.get("text") or text[start:end]),
                item.get("value"),
                "provider",
                float(item["confidence"]) if isinstance(item.get("confidence"), (int, float)) else None,
            )
        )
    return result

from __future__ import annotations

from pathlib import Path
import threading
from typing import Callable, Protocol

from .audio import PipeWireRecorder
from .config import AppConfig
from .models import ControllerSnapshot, ControllerState
from .output import ActiveWindowTextSink, ClipboardTextSink, FallbackTextSink, IBusTextSink, MirroringTextSink, TextSink
from .stt.elevenlabs import ElevenLabsSpeechToText
from .stt.mock import MockSpeechToText
from .stt.whisper_local import WhisperLocalSpeechToText
from .text_processor import TextProcessor


class RecorderProtocol(Protocol):
    @property
    def is_recording(self) -> bool:
        ...

    def start(self, output_path: Path, source_name: str | None = None) -> None:
        ...

    def stop(self) -> Path:
        ...

    def cancel(self) -> None:
        ...


Listener = Callable[[ControllerSnapshot], None]


def build_transcriber(config: AppConfig):
    if config.stt_provider == "elevenlabs":
        return ElevenLabsSpeechToText(config)
    if config.stt_provider == "whisper_local":
        return WhisperLocalSpeechToText(config)
    if config.stt_provider == "mock":
        return MockSpeechToText()
    raise RuntimeError(f"Unsupported STT provider: {config.stt_provider}")


def build_sink(config: AppConfig) -> TextSink:
    primary = FallbackTextSink(
        ActiveWindowTextSink(),
        IBusTextSink(config.paths.engine_socket),
    )
    if config.fallback_sink == "clipboard":
        return MirroringTextSink(
            FallbackTextSink(primary, ClipboardTextSink()),
            ClipboardTextSink(),
        )
    return primary


class GabbeeController:
    def __init__(
        self,
        config: AppConfig,
        recorder: RecorderProtocol | None = None,
        transcriber=None,
        sink: TextSink | None = None,
    ) -> None:
        self.config = config
        self.recorder = recorder or PipeWireRecorder(sample_rate=config.sample_rate)
        self.transcriber = transcriber or build_transcriber(config)
        self.sink = sink or build_sink(config)
        self.processor = TextProcessor(config.keyword_map)
        self.state = ControllerState.IDLE
        self.last_text = ""
        self.error_message = ""
        self._listeners: list[Listener] = []
        self._worker: threading.Thread | None = None
        self._lock = threading.Lock()

    def add_listener(self, listener: Listener) -> None:
        self._listeners.append(listener)
        listener(self.snapshot())

    def snapshot(self) -> ControllerSnapshot:
        return ControllerSnapshot(
            state=self.state,
            provider=getattr(self.transcriber, "provider_name", "unknown"),
            delivery_method=getattr(self.sink, "__class__", type(self.sink)).__name__,
            last_text=self.last_text,
            error_message=self.error_message,
        )

    def _notify(self) -> None:
        snapshot = self.snapshot()
        for listener in list(self._listeners):
            listener(snapshot)

    def _set_state(self, state: ControllerState, *, error: str = "") -> None:
        with self._lock:
            self.state = state
            self.error_message = error
        self._notify()

    def start(self) -> None:
        if self.state not in (ControllerState.IDLE, ControllerState.ERROR):
            return
        self.config.paths.ensure()
        self.recorder.start(self.config.paths.recording_path, self.config.audio_source)
        self.last_text = ""
        self._set_state(ControllerState.RECORDING)

    def stop(self) -> None:
        if self.state != ControllerState.RECORDING:
            return
        audio_path = self.recorder.stop()
        self._set_state(ControllerState.TRANSCRIBING)
        self._worker = threading.Thread(
            target=self._transcribe_and_deliver,
            args=(audio_path,),
            daemon=True,
        )
        self._worker.start()

    def toggle(self) -> None:
        if self.state == ControllerState.RECORDING:
            self.stop()
        elif self.state in (ControllerState.IDLE, ControllerState.ERROR):
            self.start()

    def cancel(self) -> None:
        if self.state == ControllerState.RECORDING:
            self.recorder.cancel()
            self._set_state(ControllerState.IDLE)

    def wait_for_background(self, timeout: float | None = None) -> None:
        worker = self._worker
        if worker:
            worker.join(timeout=timeout)

    def _transcribe_and_deliver(self, audio_path: Path) -> None:
        try:
            result = self.transcriber.transcribe(audio_path)
            actions = self.processor.process_to_actions(result.text)
            self.last_text = result.text # Show original or processed? User might want to see what happened.
            # Let's show a summary of what's being delivered
            display_text = " ".join([v if t == 'text' else f"[{v}]" for t, v in actions])
            self.last_text = display_text
            
            self._set_state(ControllerState.DELIVERING)
            for action_type, action_value in actions:
                if action_type == "text":
                    delivery = self.sink.deliver(action_value)
                else:
                    delivery = self.sink.deliver_key(action_value)
                
                if not delivery.ok:
                    # Log error but maybe continue?
                    print(f"Delivery failed: {delivery.detail}")
            
            self._set_state(ControllerState.IDLE)
        except Exception as exc:
            self._set_state(ControllerState.ERROR, error=str(exc))

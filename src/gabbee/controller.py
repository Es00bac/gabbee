from __future__ import annotations

from pathlib import Path
import inspect
import os
import threading
import time
from typing import Callable, Protocol

from .advanced_config import AdvancedConfig, VocabularyEntry, load_advanced_config, save_advanced_config
from .audio import BufferedAudioRelay, PipeWireRecorder
from .config import AppConfig
from .desktop import (
    AppContextService,
    AtSpiAccessibilityBackend,
    DotoolPointerBackend,
    KWinWindowBackend,
    NullAccessibilityBackend,
    NullPointerBackend,
    NullWindowBackend,
    qt_virtual_screen_rect,
)
from .desktop_actions import BuiltinDesktopCommands, DesktopExecutor
from .diagnostics import DiagnosticsLog
from .macro_runtime import AmbiguousCommandError, CommandDispatcher, MacroResult, MacroRunner
from .models import (
    Action,
    AppContext,
    ClickAction,
    ControllerSnapshot,
    ControllerState,
    DeliveryResult,
    LastDictation,
    ProcessingContext,
    Rect,
    SemanticSpan,
    TranscriptionResult,
    UiTarget,
)
from .agent_input import AgentInputTextSink
from .output import (
    ActiveWindowTextSink,
    ClipboardPasteSink,
    ClipboardTextSink,
    FallbackTextSink,
    IBusTextSink,
    MirroringTextSink,
    TextDeliveryRouter,
    TextSink,
)
from .profiles import ProfileManager
from .stt.elevenlabs import ElevenLabsSpeechToText
from .stt.gemini import GeminiSpeechToText
from .stt.mock import MockSpeechToText
from .stt.whisper_local import WhisperLocalSpeechToText
from .text_processor import TextProcessor
from .ui.target_overlay import NumberedTargetOverlay, RecursiveScreenGrid
from .ui.sounds import NullFeedbackSounds
from .utterance_queue import UtteranceJob, group_dictation_chains


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
    if config.stt_provider == "gemini":
        return GeminiSpeechToText(config)
    if config.stt_provider == "whisper_local":
        return WhisperLocalSpeechToText(config)
    if config.stt_provider == "mock":
        return MockSpeechToText()
    raise RuntimeError(f"Unsupported STT provider: {config.stt_provider}")


def build_sink(config: AppConfig) -> TextSink:
    """Legacy sink composition retained for third-party callers.

    New controller instances use :func:`build_delivery_router`; keeping this
    helper avoids breaking integrations built against Gabbee 0.1.
    """

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


def build_delivery_router(
    config: AppConfig,
    *,
    window_backend=None,
    accessibility_backend=None,
) -> TextDeliveryRouter:
    window_backend = window_backend or KWinWindowBackend()
    accessibility_backend = accessibility_backend or AtSpiAccessibilityBackend()
    context_service = AppContextService(window_backend, accessibility_backend)
    typing = ActiveWindowTextSink()

    def activate_captured(target: AppContext) -> bool:
        if not window_backend.activate_window(target):
            return False
        if target.focused_selector:
            restore = getattr(accessibility_backend, "restore_focus", None)
            return bool(callable(restore) and restore(target))
        return True

    configured_sink = config.env("GABBEE_FALLBACK_SINK")
    qindaqt_default = (
        configured_sink is None
        and os.environ.get("XDG_CURRENT_DESKTOP", "").casefold() == "qindaqt"
    )
    router = TextDeliveryRouter(
        IBusTextSink(config.paths.engine_socket),
        accessibility_backend,
        ClipboardPasteSink(typing),
        typing,
        current_context=context_service.current,
        activate_target=activate_captured,
        clipboard_mirror=ClipboardTextSink() if config.fallback_sink == "clipboard" else None,
        agent_input=(AgentInputTextSink.from_environment()
                     if configured_sink == "agent-input" or qindaqt_default else None),
    )
    # The controller needs the same service/backends that the router verifies.
    router.context_service = context_service
    router.window_backend = window_backend
    router.accessibility_backend = accessibility_backend
    return router


def build_processor(config: AppConfig, advanced_config: AdvancedConfig | None = None) -> TextProcessor:
    return TextProcessor(
        config.keyword_map,
        config.paths.vocabulary_file,
        advanced_config or load_advanced_config(config.paths.advanced_config_file),
    )


class GabbeeController:
    def __init__(
        self,
        config: AppConfig,
        recorder: RecorderProtocol | None = None,
        transcriber=None,
        sink: TextSink | None = None,
        *,
        context_service: AppContextService | None = None,
        macro_runner: MacroRunner | None = None,
        command_dispatcher: CommandDispatcher | None = None,
        target_overlay: NumberedTargetOverlay | None = None,
        diagnostics: DiagnosticsLog | None = None,
        sounds=None,
    ) -> None:
        self.config = config
        self.diagnostics = diagnostics or DiagnosticsLog(config.paths.diagnostics_log)
        self.sounds = sounds or NullFeedbackSounds()
        self.recorder = recorder or PipeWireRecorder(sample_rate=config.sample_rate)
        self.transcriber = transcriber or build_transcriber(config)
        if sink is None:
            router = build_delivery_router(config)
            self.sink = router
            self.context_service = context_service or router.context_service
            self.window_backend = router.window_backend
            self.accessibility_backend = router.accessibility_backend
        else:
            self.sink = sink
            self.context_service = context_service
            self.window_backend = context_service.window_backend if context_service else NullWindowBackend()
            self.accessibility_backend = context_service.accessibility_backend if context_service else NullAccessibilityBackend()

        self.advanced_config = load_advanced_config(config.paths.advanced_config_file)
        self.profile_manager = ProfileManager(self.advanced_config)
        self.processor = build_processor(config, self.advanced_config)
        self.target_overlay = target_overlay or NumberedTargetOverlay()
        desktop_executor: DesktopExecutor | None = None
        if macro_runner is None:
            pointer = DotoolPointerBackend(qt_virtual_screen_rect()) if isinstance(self.sink, TextDeliveryRouter) else NullPointerBackend()
            desktop_executor = DesktopExecutor(
                self.sink,  # type: ignore[arg-type]
                self.window_backend,
                self.accessibility_backend,
                pointer,
                app_context=lambda: self._delivery_target,
            )
            macro_runner = MacroRunner(desktop_executor, on_progress=self._on_macro_progress)
        self.macro_runner = macro_runner
        candidate_executor = getattr(macro_runner, "executor", None)
        self.desktop_executor = candidate_executor if isinstance(candidate_executor, DesktopExecutor) else desktop_executor
        self.command_dispatcher = command_dispatcher or CommandDispatcher(self.advanced_config, self.macro_runner)
        self.builtin_desktop_commands = BuiltinDesktopCommands()

        self.state = ControllerState.IDLE
        # Armed by default. A consumer such as the QindaQt Voice1 provider may
        # disarm it; start() then refuses regardless of which shortcut backend
        # or D-Bus caller asked, which is what makes a "voice input off" switch
        # in the desktop mean what it says.
        self.input_enabled = True
        self.last_text = ""
        self.partial_text = ""
        self.error_message = ""
        self.provider_status = "ready"
        self.delivery_method = ""
        self.macro_progress = ""
        self.last_dictation: LastDictation | None = None
        self._command_mode = False
        self._listeners: list[Listener] = []
        self._worker: threading.Thread | None = None
        self._lock = threading.Lock()
        self._queue_condition = threading.Condition(self._lock)
        self._pending_jobs: list[UtteranceJob] = []
        self._next_sequence = 1
        self._current_sequence = 0
        self._current_recording_path: Path | None = None
        self._current_session = None
        self._current_target: AppContext | None = None
        self._delivery_target: AppContext | None = None
        self._current_profile_name = ""
        self._current_keyterms: tuple[str, ...] = ()
        self._current_entity_detection: tuple[str, ...] = ()
        self._stream_errors: dict[int, Exception] = {}
        self._failed_chain: list[UtteranceJob] = []
        self._delivered_sequences: set[int] = set()
        self._transcript_tail = ""
        self._continuation_seconds = 3.0
        self._chain_settle_seconds = 0.05
        self._shutting_down = False
        self._screen_grid: RecursiveScreenGrid | None = None
        self._pending_selection_action: Action | None = None
        self._numbered_windows: dict[int, AppContext] = {}

    def reload_transcriber(self) -> None:
        with self._lock:
            manual_profile = self.profile_manager.manual_override
            self.transcriber = build_transcriber(self.config)
            self.advanced_config = load_advanced_config(self.config.paths.advanced_config_file)
            self.profile_manager = ProfileManager(self.advanced_config)
            if manual_profile and self.profile_manager.by_name(manual_profile) is not None:
                self.profile_manager.set_manual_override(manual_profile)
            self.processor = build_processor(self.config, self.advanced_config)
            self.command_dispatcher = CommandDispatcher(self.advanced_config, self.macro_runner)
        self._notify()

    def add_listener(self, listener: Listener) -> None:
        self._listeners.append(listener)
        listener(self.snapshot())

    def snapshot(self) -> ControllerSnapshot:
        with self._lock:
            target = self._current_target or self._delivery_target
            return ControllerSnapshot(
                state=self.state,
                provider=getattr(self.transcriber, "provider_name", "unknown"),
                delivery_method=self.delivery_method or type(self.sink).__name__,
                last_text=self.last_text,
                error_message=self.error_message,
                queue_depth=len(self._pending_jobs),
                active_recording=self.recorder.is_recording,
                partial_text=self.partial_text,
                provider_status=self.provider_status,
                target_app=(target.title or target.desktop_file_id) if target else "",
                active_profile=self._current_profile_name,
                macro_progress=self.macro_progress,
                command_mode=self._command_mode,
                retained_audio=bool(self.last_dictation and self.last_dictation.audio_path and self.last_dictation.audio_path.exists()),
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
        if state is ControllerState.ERROR:
            self.sounds.error()

    def _recording_path_for(self, sequence: int) -> Path:
        return self.config.paths.recording_path.with_name(f"recording-{sequence}.wav")

    def set_input_enabled(self, enabled: bool) -> None:
        """Arm or disarm every path that can open the microphone."""

        enabled = bool(enabled)
        if self.input_enabled == enabled:
            return
        self.input_enabled = enabled
        self._notify()

    def start(self, *, command_mode: bool = False) -> None:
        if self._shutting_down:
            return
        if not self.input_enabled:
            return
        if command_mode and self.macro_runner.is_running:
            self.macro_runner.cancel()
            with self._lock:
                self.macro_progress = "Cancelling macro…"
            self._notify()
            return
        if not command_mode:
            self.target_overlay.hide()
            self._screen_grid = None
            self._numbered_windows = {}
            self._pending_selection_action = None
        with self._lock:
            if self.recorder.is_recording:
                return
            sequence = self._next_sequence
            self._next_sequence += 1
            audio_path = self._recording_path_for(sequence)
            self._current_sequence = sequence
            self._current_recording_path = audio_path
            self._command_mode = command_mode
            self.state = ControllerState.CONNECTING
            self.error_message = ""
            self.partial_text = ""
            self.macro_progress = ""
            self.delivery_method = ""
            self.provider_status = "connecting" if self._can_stream() else "ready"

        self._notify()

        target = self.context_service.capture() if self.context_service else None
        profile = self.profile_manager.select(target)
        keyterms = (
            self.profile_manager.keyterms_for(profile)
            if getattr(self.config, "elevenlabs_keyterms_enabled", False)
            else []
        )
        profile_entities = self.profile_manager.entity_hints_for(profile)
        entities = profile_entities or list(getattr(self.config, "elevenlabs_entity_detection", []))
        with self._lock:
            self._current_target = target
            self._current_profile_name = profile.name if profile else ""
            self._current_keyterms = tuple(keyterms[:50])
            self._current_entity_detection = tuple(entities)

        self.config.paths.ensure()
        streaming = self._can_stream()
        relay = (
            BufferedAudioRelay(sample_rate=self.config.sample_rate)
            if streaming
            else None
        )
        session = None
        try:
            start_streaming = getattr(self.recorder, "start_streaming", None)
            if relay is not None and callable(start_streaming):
                # Begin capturing before the network handshake.  Early frames
                # stay in relay until ElevenLabs confirms the session.
                start_streaming(audio_path, self.config.audio_source, relay)
            else:
                self.recorder.start(audio_path, self.config.audio_source)
        except Exception as exc:
            self._stream_errors.pop(sequence, None)
            if session is not None:
                session.cancel()
            if self.recorder.is_recording:
                try:
                    self.recorder.cancel()
                except Exception:
                    pass
            with self._lock:
                self._current_session = None
                self._current_sequence = 0
                self._current_recording_path = None
                self._current_target = None
                self._command_mode = False
                self.state = ControllerState.ERROR
                self.error_message = str(exc)
            self._diagnose(
                "recording_start",
                success=False,
                failure_category=type(exc).__name__,
            )
            self.sounds.error()
            self._notify()
            return
        with self._lock:
            self._current_session = None
            self.last_text = ""
            self.state = ControllerState.RECORDING
            self.error_message = ""
            self.provider_status = "realtime connecting" if streaming else "ready"
        self._diagnose("recording_start", success=True, detail="command" if command_mode else "dictation")
        self.sounds.start(command_mode)
        self._notify()

        if not streaming:
            return

        self._start_realtime_connection(sequence, relay)

    def _start_realtime_connection(self, sequence: int, relay: BufferedAudioRelay | None) -> None:
        threading.Thread(
            target=self._connect_realtime,
            args=(sequence, relay),
            daemon=True,
            name="gabbee-elevenlabs-connect",
        ).start()

    def _connect_realtime(self, sequence: int, relay: BufferedAudioRelay | None) -> None:
        realtime_started = time.monotonic()
        session = None
        try:
            session = self.transcriber.open_session(
                previous_text=self._transcript_tail[-50:],
                keyterms=list(self._current_keyterms),
                entity_detection=list(self._current_entity_detection),
                on_partial=lambda value: self._on_partial(sequence, value),
                on_error=lambda error: self._on_stream_error(sequence, error),
            )
            if relay is not None:
                relay.attach(session.push_audio)
            stale = False
            with self._lock:
                if self._current_sequence != sequence or not self.recorder.is_recording:
                    stale = True
                else:
                    self._current_session = session
                    self.provider_status = "realtime connected"
            if stale:
                session.cancel()
                if relay is not None:
                    relay.discard()
                return
            self._diagnose(
                "provider_connection",
                success=True,
                duration_ms=round((time.monotonic() - realtime_started) * 1000),
                detail="realtime",
            )
        except Exception as exc:
            if session is not None:
                try:
                    session.cancel()
                except Exception:
                    pass
            if relay is not None:
                relay.discard()
            self._stream_errors[sequence] = exc
            with self._lock:
                if self._current_sequence == sequence and self.recorder.is_recording:
                    self.provider_status = "realtime unavailable; batch recovery armed"
            self._diagnose(
                "provider_connection",
                success=False,
                duration_ms=round((time.monotonic() - realtime_started) * 1000),
                failure_category=getattr(exc, "category", type(exc).__name__),
                detail="batch recovery armed",
            )
        finally:
            self._notify()

    def _can_stream(self) -> bool:
        return (
            self.config.stt_provider == "elevenlabs"
            and getattr(self.config, "elevenlabs_realtime_enabled", True)
            and callable(getattr(self.transcriber, "open_session", None))
        )

    def start_command(self) -> None:
        self.start(command_mode=True)

    def stop(self) -> None:
        with self._lock:
            if not self.recorder.is_recording:
                return
            sequence = self._current_sequence
            command_mode = self._command_mode
            created_at = time.monotonic()
            session = self._current_session
            target = self._current_target
            profile_name = self._current_profile_name
            keyterms = self._current_keyterms
            entities = self._current_entity_detection

        try:
            audio_path = self.recorder.stop()
        except Exception as exc:
            self._stream_errors.pop(sequence, None)
            try:
                self.recorder.cancel()
            except Exception:
                pass
            if session is not None:
                try:
                    session.cancel()
                except Exception:
                    pass
            with self._lock:
                self._current_session = None
                self._current_sequence = 0
                self._current_recording_path = None
                self._current_target = None
                self._command_mode = False
            self._diagnose(
                "recording_stop",
                success=False,
                failure_category=type(exc).__name__,
            )
            self._set_state(ControllerState.ERROR, error=str(exc))
            return
        self._diagnose("recording_stop", success=True, detail="command" if command_mode else "dictation")
        self.sounds.stop(command_mode)
        stream_error = self._stream_errors.pop(sequence, None) or getattr(self.recorder, "stream_error", None)
        if isinstance(self.sink, TextDeliveryRouter):
            self.sink.clear_preedit()
        if stream_error is not None and session is not None:
            session.cancel()
            session = None
        with self._queue_condition:
            self._command_mode = False
            self._current_sequence = 0
            self._current_recording_path = None
            self._current_session = None
            self._current_target = None
            self.partial_text = ""
            self._pending_jobs.append(
                UtteranceJob(
                    sequence=sequence,
                    audio_path=audio_path,
                    command_mode=command_mode,
                    created_at=created_at,
                    app_context=target,
                    profile_name=profile_name,
                    realtime_session=session,
                    keyterms=keyterms,
                    entity_detection=entities,
                )
            )
            self.state = ControllerState.TRANSCRIBING
            self.provider_status = "batch recovery" if stream_error is not None else self.provider_status
            self.error_message = ""
            self._start_queue_worker_locked()
            self._queue_condition.notify_all()
        self._notify()

    def _start_queue_worker_locked(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        self._worker = threading.Thread(target=self._drain_queue, daemon=True, name="gabbee-queue")
        self._worker.start()

    def toggle(self) -> None:
        if self.recorder.is_recording:
            self.stop()
        else:
            self.start()

    def cancel(self) -> None:
        self.target_overlay.hide()
        self._screen_grid = None
        self._numbered_windows = {}
        self._pending_selection_action = None
        if self.macro_runner.is_running:
            self.macro_runner.cancel()
        if self.recorder.is_recording:
            sequence = self._current_sequence
            session = self._current_session
            try:
                self.recorder.cancel()
            except Exception as exc:
                self._diagnose(
                    "recording_cancel",
                    success=False,
                    failure_category=type(exc).__name__,
                )
            if session is not None:
                try:
                    session.cancel()
                except Exception:
                    pass
            if isinstance(self.sink, TextDeliveryRouter):
                self.sink.clear_preedit()
            with self._lock:
                self._stream_errors.pop(sequence, None)
                self._command_mode = False
                self._current_sequence = 0
                self._current_recording_path = None
                self._current_session = None
                self._current_target = None
                self.partial_text = ""
                self.state = ControllerState.IDLE if not self._pending_jobs else ControllerState.TRANSCRIBING
                self.error_message = ""
            self._notify()

    def cancel_macro(self) -> None:
        self.macro_runner.cancel()

    def shutdown(self) -> None:
        """Release recorder/session/overlay resources during application exit."""

        with self._queue_condition:
            self._shutting_down = True
            self._queue_condition.notify_all()
        self.cancel()
        session = self._current_session
        if session is not None:
            try:
                session.cancel()
            except Exception:
                pass

    def capture_current_app(self) -> AppContext | None:
        return self.context_service.capture() if self.context_service else None

    def capture_current_control(self, app: AppContext | None = None) -> UiTarget | None:
        app = app or self.capture_current_app()
        if app is None:
            return None
        targets = self.accessibility_backend.discover_targets(app)
        if app.focused_selector:
            selected = next(
                (item for item in targets if (item.selector or item.id) == app.focused_selector),
                None,
            )
            if selected is not None:
                return selected
        if app.focused_name and self.desktop_executor is not None:
            matches = self.desktop_executor.resolver.resolve(app.focused_name, targets)
            return matches[0] if len(matches) == 1 else None
        return None

    def available_profiles(self) -> list[str]:
        return [item.name for item in self.advanced_config.profiles if item.enabled]

    def set_profile_override(self, profile_name: str | None) -> None:
        self.profile_manager.set_manual_override(profile_name)
        with self._lock:
            self._current_profile_name = self.profile_manager.manual_override or ""
        self._notify()

    def wait_for_background(self, timeout: float | None = None) -> None:
        worker = self._worker
        if worker:
            worker.join(timeout=timeout)

    def _drain_queue(self) -> None:
        had_error = False
        while True:
            with self._queue_condition:
                if self._shutting_down:
                    self.state = ControllerState.IDLE
                    self._queue_condition.notify_all()
                    break
                if not self._pending_jobs:
                    if not had_error:
                        self.state = ControllerState.IDLE
                        self.error_message = ""
                    self._queue_condition.notify_all()
                    break
                if self._pending_jobs[0].command_mode:
                    chain = [self._pending_jobs.pop(0)]
                else:
                    self._queue_condition.wait(timeout=self._chain_settle_seconds)
                    chains = group_dictation_chains(
                        self._pending_jobs,
                        continuation_seconds=self._continuation_seconds,
                    )
                    chain = chains[0]
                    del self._pending_jobs[: len(chain)]
                self.state = ControllerState.TRANSCRIBING
                self.error_message = ""
            self._notify()
            try:
                self._process_chain(chain)
            except Exception as exc:
                had_error = True
                self._failed_chain = chain
                self._delivery_target = None
                current_paths = {job.audio_path for job in chain}
                if self.last_dictation is None or self.last_dictation.audio_path not in current_paths:
                    self.last_dictation = LastDictation(
                        "", "", chain[0].app_context, chain[0].audio_path, delivered=False
                    )
                self._diagnose(
                    "utterance",
                    success=False,
                    failure_category=getattr(exc, "category", type(exc).__name__),
                )
                self._set_state(ControllerState.ERROR, error=str(exc))
        self._notify()

    def _process_chain(self, chain: list[UtteranceJob]) -> None:
        started = time.monotonic()
        results = [self._transcribe_job(job) for job in chain]
        if self._shutting_down:
            raise RuntimeError("Gabbee is shutting down; retained audio was not delivered.")
        raw_text, entity_spans = self._join_results(results)
        command_mode = len(chain) == 1 and chain[0].command_mode
        target = chain[0].app_context
        self._delivery_target = target
        self.last_dictation = LastDictation(
            raw_text=raw_text,
            formatted_text="",
            app=target,
            audio_path=chain[0].audio_path,
            delivered=False,
        )
        profile = self.profile_manager.by_name(chain[0].profile_name) if chain[0].profile_name else self.profile_manager.select(target)
        context = ProcessingContext(
            previous_text=self._transcript_tail,
            surrounding_before=target.surrounding_before if target else "",
            surrounding_after=target.surrounding_after if target else "",
            app=target,
            profile_name=profile.name if profile else "",
            prose=bool(self.advanced_config.behavior.get("prose", True)),
            terminal_mode=bool(self.advanced_config.behavior.get("terminal_mode", False))
            or (profile.terminal_mode if profile else False),
            code_mode=bool(self.advanced_config.behavior.get("code_mode", False))
            or (profile.code_mode if profile else False),
            entity_spans=entity_spans,
        )

        if command_mode and self._dispatch_command(raw_text, context):
            display_text = raw_text
            delivery_method = "desktop-action"
        else:
            actions = self.processor.process_to_actions(
                raw_text,
                enable_commands=command_mode,
                context=context,
                entity_spans=entity_spans,
            )
            display_text = " ".join(
                value if action_type == "text" else f"[{value}]"
                for action_type, value in actions
            )
            self.last_dictation.formatted_text = display_text
            with self._lock:
                self.last_text = display_text
                self.state = ControllerState.DELIVERING
                self.error_message = ""
            self._notify()
            delivery_method = self._deliver_actions(actions, target)

        self.last_dictation = LastDictation(
            raw_text=raw_text,
            formatted_text=display_text,
            app=target,
            audio_path=None,
            delivery_method=delivery_method,
            delivered=True,
        )
        if not command_mode:
            self._transcript_tail = display_text[-500:]
        with self._lock:
            self.last_text = display_text
            self.delivery_method = delivery_method
            self.provider_status = "ready"
            self._current_profile_name = context.profile_name
        for job in chain:
            self._delivered_sequences.add(job.sequence)
            try:
                job.audio_path.unlink(missing_ok=True)
            except OSError:
                pass
        self._failed_chain = []
        self._delivery_target = None
        self._diagnose(
            "utterance",
            success=True,
            duration_ms=round((time.monotonic() - started) * 1000),
            detail=delivery_method,
        )

    def _transcribe_job(self, job: UtteranceJob) -> TranscriptionResult:
        if job.sequence in self._delivered_sequences:
            raise RuntimeError("This utterance has already been delivered.")
        if job.realtime_session is not None:
            started = time.monotonic()
            try:
                result = job.realtime_session.commit()
                with self._lock:
                    self.provider_status = "realtime committed"
                self._diagnose(
                    "transcription",
                    success=True,
                    duration_ms=round((time.monotonic() - started) * 1000),
                    detail="realtime",
                )
                return result
            except Exception as exc:
                try:
                    job.realtime_session.cancel()
                except Exception:
                    pass
                with self._lock:
                    self.provider_status = "realtime failed; using batch recovery"
                self._diagnose(
                    "transcription",
                    success=False,
                    duration_ms=round((time.monotonic() - started) * 1000),
                    failure_category=getattr(exc, "category", type(exc).__name__),
                    detail="realtime; batch recovery",
                )
        batch_started = time.monotonic()
        try:
            parameters = inspect.signature(self.transcriber.transcribe).parameters
            supports_options = "keyterms" in parameters or any(
                item.kind is inspect.Parameter.VAR_KEYWORD for item in parameters.values()
            )
        except (TypeError, ValueError):
            supports_options = False
        if supports_options:
            result = self.transcriber.transcribe(
                job.audio_path,
                keyterms=list(job.keyterms),
                entity_detection=list(job.entity_detection),
            )
            self._diagnose(
                "transcription",
                success=True,
                duration_ms=round((time.monotonic() - batch_started) * 1000),
                detail="batch",
            )
            return result
        # Legacy/non-ElevenLabs providers preserve the batch contract.
        result = self.transcriber.transcribe(job.audio_path)
        self._diagnose(
            "transcription",
            success=True,
            duration_ms=round((time.monotonic() - batch_started) * 1000),
            detail="batch",
        )
        return result

    @staticmethod
    def _join_results(results: list[TranscriptionResult]) -> tuple[str, list[SemanticSpan]]:
        parts: list[str] = []
        entities: list[SemanticSpan] = []
        offset = 0
        for result in results:
            part = result.text.strip()
            if not part:
                continue
            if parts:
                offset += 1
            parts.append(part)
            for entity in result.entities:
                if entity.is_valid_for(result.text):
                    leading = len(result.text) - len(result.text.lstrip())
                    entities.append(
                        SemanticSpan(
                            offset + entity.start - leading,
                            offset + entity.end - leading,
                            entity.kind,
                            entity.text,
                            entity.value,
                            entity.source,
                            entity.confidence,
                        )
                    )
            offset += len(part)
        return " ".join(parts), entities

    def _dispatch_command(self, text: str, context: ProcessingContext) -> bool:
        try:
            resolved = self.command_dispatcher.resolve(text, context.profile_name)
        except AmbiguousCommandError as exc:
            raise RuntimeError(f"Ambiguous command: {len(exc.matches)} saved patterns match; choose one.") from exc
        active_profile = self.profile_manager.by_name(context.profile_name) if context.profile_name else None
        if resolved is not None:
            entry = resolved.entry
            if hasattr(entry, "name"):
                available = self.profile_manager.macro_available(str(getattr(entry, "name")), active_profile)
            else:
                available = self.profile_manager.command_available(
                    str(getattr(entry, "spoken", "")), active_profile
                )
            if not available:
                resolved = None
        if resolved is not None:
            with self._lock:
                self.state = ControllerState.RUNNING_MACRO
            result = self.command_dispatcher.dispatch(text, context.profile_name)
            if isinstance(result, MacroResult) and not result.ok:
                raise RuntimeError(result.detail or "Macro failed.")
            return True

        builtin = self.builtin_desktop_commands.parse(text)
        if builtin is None:
            return False
        if isinstance(builtin, tuple):
            kind, value = builtin
            if kind == "previous_app":
                previous = getattr(self.window_backend, "previous_app", None)
                if not callable(previous) or not previous():
                    raise RuntimeError("No previous app is available.")
                return True
            if kind in {"next_window", "previous_window"}:
                activate = getattr(self.window_backend, "activate_named", None)
                matches = activate(value, 1 if kind == "next_window" else -1) if callable(activate) else []
                if len(matches) != 1:
                    raise RuntimeError("Window target is missing or ambiguous.")
                return True
            if kind == "show_numbers":
                self._show_numbers(context.app)
                return True
            if kind in {"click", "move to", "narrow", "zoom"}:
                self._select_number(int(value), kind)
                return True
            raise RuntimeError(f"Unsupported desktop selection: {kind} {value}".strip())
        result = self.macro_runner.run([builtin])
        if not result.ok:
            if self.desktop_executor is not None and self.desktop_executor.last_ambiguity:
                self._pending_selection_action = self.desktop_executor.last_ambiguous_action or builtin
                self._show_target_choices(self.desktop_executor.last_ambiguity)
                return True
            if self.desktop_executor is not None and self.desktop_executor.last_window_ambiguity:
                self._show_window_choices(self.desktop_executor.last_window_ambiguity)
                return True
            raise RuntimeError(result.detail or "Desktop command failed.")
        return True

    def _show_numbers(self, app: AppContext | None) -> None:
        if self.desktop_executor is None:
            raise RuntimeError("Desktop targeting is unavailable.")
        app = app or self._delivery_target
        targets = (
            self.desktop_executor.resolver.filter_targets(self.accessibility_backend.discover_targets(app))
            if app is not None
            else []
        )
        self._pending_selection_action = None
        if targets:
            self._screen_grid = None
            self._show_target_choices(targets)
            return
        self._screen_grid = RecursiveScreenGrid(qt_virtual_screen_rect())
        self._show_grid_cells()

    def _show_target_choices(self, targets: list[UiTarget]) -> None:
        self._numbered_windows = {}
        self.target_overlay.show_targets(targets)
        with self._lock:
            self.macro_progress = f"Choose target 1–{len(targets)}"
        self._notify()

    def _show_window_choices(self, windows: list[AppContext]) -> None:
        desktop = qt_virtual_screen_rect()
        targets: list[UiTarget] = []
        self._numbered_windows = {}
        for number, window in enumerate(windows, start=1):
            rect = window.rect
            if rect is None or rect.width <= 0 or rect.height <= 0:
                rect = Rect(desktop.x + 40, desktop.y + 40 + (number - 1) * 36, 1, 1)
            targets.append(
                UiTarget(
                    id=f"gabbee-window:{window.window_id}",
                    name=window.title or window.desktop_file_id or f"Window {number}",
                    role="window",
                    rect=rect,
                )
            )
            self._numbered_windows[number] = window
        self.target_overlay.show_targets(targets)
        with self._lock:
            self.macro_progress = f"Choose window 1–{len(targets)}"
        self._notify()

    def _show_grid_cells(self) -> None:
        if self._screen_grid is None:
            return
        targets = [
            UiTarget(
                id=f"gabbee-grid:{self._screen_grid.depth}:{cell.number}",
                name=f"Grid {cell.number}",
                role="screen grid",
                rect=cell.rect,
            )
            for cell in self._screen_grid.cells()
        ]
        self.target_overlay.show_targets(targets)
        with self._lock:
            self.macro_progress = "Screen grid: say narrow 1–9, click N, or move to N"
        self._notify()

    def _select_number(self, number: int, operation: str) -> None:
        if self.desktop_executor is None:
            raise RuntimeError("Desktop targeting is unavailable.")
        target = self.target_overlay.targets.get(number)
        if target is None:
            raise RuntimeError(f"Target number {number} is not currently shown.")
        selected_window = self._numbered_windows.get(number)
        if selected_window is not None:
            self.target_overlay.choose(number)
            self._numbered_windows = {}
            if not self.window_backend.activate_window(selected_window):
                raise RuntimeError("Window activation failed.")
            with self._lock:
                self.delivery_method = "window"
                self.macro_progress = f"Activated {selected_window.title or selected_window.desktop_file_id}."
            return
        if operation in {"narrow", "zoom"}:
            if self._screen_grid is None:
                raise RuntimeError("Narrow is only available for the screen grid.")
            self._screen_grid.descend(number)
            self._show_grid_cells()
            return

        target = self.target_overlay.choose(number)
        if target is None:
            raise RuntimeError(f"Target number {number} is no longer available.")
        selected_action = self._pending_selection_action
        self._pending_selection_action = None
        result = self.desktop_executor.execute_selected(
            target,
            selected_action,
            operation="move" if operation == "move to" else "click",
        )
        self._screen_grid = None
        if not result.ok:
            raise RuntimeError(result.detail or "Numbered target action failed.")
        with self._lock:
            self.delivery_method = result.method
            self.macro_progress = result.detail

    def _deliver_actions(self, actions: list[tuple[str, str]], target: AppContext | None) -> str:
        method = ""
        for action_type, action_value in actions:
            if action_type == "text":
                result = self._sink_deliver(action_value, target)
            elif action_type == "key":
                result = self._sink_deliver_key(action_value, target)
            else:
                raise RuntimeError(f"Unsupported delivery action: {action_type}")
            if not result.ok:
                raise RuntimeError(result.detail or f"{action_type} delivery failed.")
            method = result.method
        return method

    def _sink_deliver(self, text: str, target: AppContext | None) -> DeliveryResult:
        try:
            return self.sink.deliver(text, target)  # type: ignore[call-arg]
        except TypeError:
            return self.sink.deliver(text)

    def _sink_deliver_key(self, key: str, target: AppContext | None) -> DeliveryResult:
        try:
            return self.sink.deliver_key(key, target)  # type: ignore[call-arg]
        except TypeError:
            return self.sink.deliver_key(key)

    def _on_partial(self, sequence: int, text: str) -> None:
        with self._lock:
            if sequence != self._current_sequence or self.state not in {
                ControllerState.CONNECTING,
                ControllerState.RECORDING,
            }:
                return
            self.partial_text = text
            target = self._current_target
        if isinstance(self.sink, TextDeliveryRouter):
            self.sink.update_preedit(text, target)
        self._notify()

    def _on_stream_error(self, sequence: int, error: Exception) -> None:
        self._stream_errors[sequence] = error
        with self._lock:
            if sequence == self._current_sequence:
                self.provider_status = "realtime error; recording retained for batch recovery"
        self._notify()
        self._diagnose(
            "realtime_stream",
            success=False,
            failure_category=getattr(error, "category", type(error).__name__),
            detail="batch recovery armed",
        )

    def _on_macro_progress(self, completed: int, total: int, label: str) -> None:
        with self._lock:
            self.macro_progress = f"{completed}/{total} {label}".strip()
        self._notify()

    def _diagnose(self, category: str, **values) -> None:
        try:
            self.diagnostics.record(category, **values)
        except Exception:
            # Diagnostics must never interrupt dictation or desktop control.
            pass

    # Last Dictation actions -------------------------------------------------
    def undo_last(self) -> DeliveryResult:
        if self.last_dictation is None or not self.last_dictation.delivered:
            return DeliveryResult(False, "undo", "There is no delivered dictation to undo.")
        return self._sink_deliver_key("Control+z", self.last_dictation.app)

    def edit_and_replace(self, corrected_text: str) -> DeliveryResult:
        if self.last_dictation is None:
            return DeliveryResult(False, "replace", "There is no dictation to replace.")
        if self.last_dictation.delivered:
            undone = self.undo_last()
            if not undone.ok:
                return undone
        delivered = self._sink_deliver(corrected_text, self.last_dictation.app)
        if delivered.ok:
            self.last_dictation.formatted_text = corrected_text
            self.last_dictation.delivery_method = delivered.method
            self.last_dictation.delivered = True
            self.last_text = corrected_text
            self._notify()
        return delivered

    def retry_last(self) -> bool:
        if not self._failed_chain:
            return False
        with self._queue_condition:
            self._pending_jobs[0:0] = self._failed_chain
            self._failed_chain = []
            self.state = ControllerState.TRANSCRIBING
            self.error_message = ""
            self._start_queue_worker_locked()
            self._queue_condition.notify_all()
        self._notify()
        return True

    def copy_raw(self) -> DeliveryResult:
        if self.last_dictation is None:
            return DeliveryResult(False, "clipboard", "There is no dictation to copy.")
        return ClipboardTextSink().deliver(self.last_dictation.raw_text)

    def copy_formatted(self) -> DeliveryResult:
        if self.last_dictation is None:
            return DeliveryResult(False, "clipboard", "There is no dictation to copy.")
        return ClipboardTextSink().deliver(self.last_dictation.formatted_text)

    def correction_suggestion(self, corrected_text: str) -> VocabularyEntry | None:
        if self.last_dictation is None:
            return None
        raw = self.last_dictation.raw_text.strip()
        corrected = corrected_text.strip()
        if not raw or not corrected or raw == corrected:
            return None
        return VocabularyEntry(raw, corrected, priority=100)

    def save_correction_rule(self, spoken: str, written: str) -> None:
        entry = VocabularyEntry(spoken.strip(), written.strip(), priority=100)
        if not entry.spoken or not entry.written:
            raise ValueError("Both spoken and written forms are required.")
        self.advanced_config.vocabulary.append(entry)
        profile = self.profile_manager.by_name(self._current_profile_name) if self._current_profile_name else None
        if profile is not None and entry.spoken not in profile.vocabulary_spoken:
            profile.vocabulary_spoken.append(entry.spoken)
        save_advanced_config(self.config.paths.advanced_config_file, self.advanced_config)
        self.processor = build_processor(self.config, self.advanced_config)

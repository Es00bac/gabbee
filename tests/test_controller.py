from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gabbee.app_paths import AppPaths
from gabbee.config import AppConfig
from gabbee.controller import GabbeeController, build_sink
from gabbee.models import ControllerState, DeliveryResult, TranscriptionResult


class FakeRecorder:
    def __init__(self) -> None:
        self.is_recording = False
        self.output_path: Path | None = None

    def start(self, output_path: Path, source_name: str | None = None) -> None:
        self.is_recording = True
        self.output_path = output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"fake-wav")

    def stop(self) -> Path:
        self.is_recording = False
        if self.output_path is None:
            raise RuntimeError("No output path.")
        return self.output_path

    def cancel(self) -> None:
        self.is_recording = False
        if self.output_path and self.output_path.exists():
            self.output_path.unlink()


class FakeTranscriber:
    provider_name = "fake"

    def transcribe(self, audio_path: Path) -> TranscriptionResult:
        return TranscriptionResult(text="hello world", provider="fake", language_code="en")


class FakeSink:
    def __init__(self) -> None:
        self.received: list[str] = []
        self.key_strokes: list[str] = []

    def deliver(self, text: str) -> DeliveryResult:
        self.received.append(text)
        return DeliveryResult(ok=True, method="fake-sink", detail="ok")

    def deliver_key(self, key_stroke: str) -> DeliveryResult:
        self.key_strokes.append(key_stroke)
        return DeliveryResult(ok=True, method="fake-key-sink", detail="ok")


class QueueTranscriber:
    provider_name = "fake"

    def __init__(self, texts: list[str]) -> None:
        self.texts = texts
        self.paths: list[Path] = []

    def transcribe(self, audio_path: Path) -> TranscriptionResult:
        self.paths.append(audio_path)
        return TranscriptionResult(text=self.texts.pop(0), provider="fake", language_code="en")


class ControllerTests(unittest.TestCase):
    def test_start_stop_transcribe_and_deliver(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = AppConfig(
                paths=AppPaths(
                    config_dir=root / "config",
                    state_dir=root / "state",
                    cache_dir=root / "cache",
                    runtime_dir=root / "runtime",
                ),
                env_file=root / ".env",
                env_values={},
                stt_provider="mock",
                language_code="en",
                elevenlabs_model_id="scribe_v2",
                elevenlabs_base_url="https://api.elevenlabs.io/v1",
                audio_source=None,
                sample_rate=16000,
                fallback_sink="clipboard",
                ui_title="Gabbee",
            )
            sink = FakeSink()
            controller = GabbeeController(
                config=config,
                recorder=FakeRecorder(),
                transcriber=FakeTranscriber(),
                sink=sink,
            )

            controller.start()
            self.assertEqual(controller.state, ControllerState.RECORDING)

            controller.stop()
            controller.wait_for_background(timeout=2)

            self.assertEqual(controller.state, ControllerState.IDLE)
            self.assertEqual(controller.last_text, "hello world")
            self.assertEqual(sink.received, ["hello world"])
            self.assertEqual(sink.key_strokes, [])

    def test_dictation_delivers_editing_words_as_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = AppConfig(
                paths=AppPaths(
                    config_dir=root / "config",
                    state_dir=root / "state",
                    cache_dir=root / "cache",
                    runtime_dir=root / "runtime",
                ),
                env_file=root / ".env",
                env_values={},
                stt_provider="mock",
                language_code="en",
                elevenlabs_model_id="scribe_v2",
                elevenlabs_base_url="https://api.elevenlabs.io/v1",
                audio_source=None,
                sample_rate=16000,
                fallback_sink="clipboard",
                ui_title="Gabbee",
            )

            class EditingWordTranscriber:
                provider_name = "fake"

                def transcribe(self, audio_path: Path) -> TranscriptionResult:
                    return TranscriptionResult(
                        text="please delete copy that and undo this",
                        provider="fake",
                        language_code="en",
                    )

            sink = FakeSink()
            controller = GabbeeController(
                config=config,
                recorder=FakeRecorder(),
                transcriber=EditingWordTranscriber(),
                sink=sink,
            )

            controller.start()
            controller.stop()
            controller.wait_for_background(timeout=2)

            self.assertEqual(sink.received, ["please delete copy that and undo this"])
            self.assertEqual(sink.key_strokes, [])

    def test_command_recording_can_deliver_keyword_actions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = AppConfig(
                paths=AppPaths(
                    config_dir=root / "config",
                    state_dir=root / "state",
                    cache_dir=root / "cache",
                    runtime_dir=root / "runtime",
                ),
                env_file=root / ".env",
                env_values={},
                stt_provider="mock",
                language_code="en",
                elevenlabs_model_id="scribe_v2",
                elevenlabs_base_url="https://api.elevenlabs.io/v1",
                audio_source=None,
                sample_rate=16000,
                fallback_sink="clipboard",
                ui_title="Gabbee",
            )

            class CommandTranscriber:
                provider_name = "fake"

                def transcribe(self, audio_path: Path) -> TranscriptionResult:
                    return TranscriptionResult(text="delete word", provider="fake", language_code="en")

            sink = FakeSink()
            controller = GabbeeController(
                config=config,
                recorder=FakeRecorder(),
                transcriber=CommandTranscriber(),
                sink=sink,
            )

            controller.start_command()
            controller.stop()
            controller.wait_for_background(timeout=2)

            self.assertEqual(sink.received, [])
            self.assertEqual(sink.key_strokes, ["Control+BackSpace"])

    def test_controller_processes_queued_utterances_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = AppConfig(
                paths=AppPaths(
                    config_dir=root / "config",
                    state_dir=root / "state",
                    cache_dir=root / "cache",
                    runtime_dir=root / "runtime",
                ),
                env_file=root / ".env",
                env_values={},
                stt_provider="mock",
                language_code="en",
                elevenlabs_model_id="scribe_v2",
                elevenlabs_base_url="https://api.elevenlabs.io/v1",
                audio_source=None,
                sample_rate=16000,
                fallback_sink="clipboard",
                ui_title="Gabbee",
            )
            sink = FakeSink()
            controller = GabbeeController(
                config=config,
                recorder=FakeRecorder(),
                transcriber=QueueTranscriber(["first", "second"]),
                sink=sink,
            )

            controller.start()
            controller.stop()
            controller.start()
            controller.stop()
            controller.wait_for_background(timeout=2)

            self.assertEqual(sink.received, ["first second"])
            self.assertEqual(controller.snapshot().queue_depth, 0)

    def test_controller_loads_advanced_config(self) -> None:
        from gabbee.advanced_config import AdvancedConfig, VocabularyEntry, save_advanced_config

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = AppConfig(
                paths=AppPaths(
                    config_dir=root / "config",
                    state_dir=root / "state",
                    cache_dir=root / "cache",
                    runtime_dir=root / "runtime",
                ),
                env_file=root / ".env",
                env_values={},
                stt_provider="mock",
                language_code="en",
                elevenlabs_model_id="scribe_v2",
                elevenlabs_base_url="https://api.elevenlabs.io/v1",
                audio_source=None,
                sample_rate=16000,
                fallback_sink="clipboard",
                ui_title="Gabbee",
            )
            save_advanced_config(
                config.paths.advanced_config_file,
                AdvancedConfig(vocabulary=[VocabularyEntry(spoken="gabby", written="Gabbee")]),
            )

            controller = GabbeeController(
                config=config,
                recorder=FakeRecorder(),
                transcriber=FakeTranscriber(),
                sink=FakeSink(),
            )

            self.assertEqual(controller.processor.process_to_actions("hello gabby"), [("text", "hello Gabbee")])

    def test_build_sink_prefers_active_window_typing_before_ibus(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = AppConfig(
                paths=AppPaths(
                    config_dir=root / "config",
                    state_dir=root / "state",
                    cache_dir=root / "cache",
                    runtime_dir=root / "runtime",
                ),
                env_file=root / ".env",
                env_values={},
                stt_provider="mock",
                language_code="en",
                elevenlabs_model_id="scribe_v2",
                elevenlabs_base_url="https://api.elevenlabs.io/v1",
                audio_source=None,
                sample_rate=16000,
                fallback_sink="clipboard",
                ui_title="Gabbee",
            )

            class RecordingSink:
                def __init__(self, result: DeliveryResult) -> None:
                    self.result = result
                    self.received: list[str] = []

                def deliver(self, text: str) -> DeliveryResult:
                    self.received.append(text)
                    return self.result

            type_sink = RecordingSink(DeliveryResult(ok=True, method="type", detail="typed"))
            ibus_sink = RecordingSink(DeliveryResult(ok=True, method="ibus", detail="ibus"))
            clipboard_sink = RecordingSink(DeliveryResult(ok=True, method="clipboard", detail="clip"))

            with patch("gabbee.controller.ActiveWindowTextSink", return_value=type_sink), patch(
                "gabbee.controller.IBusTextSink", return_value=ibus_sink
            ), patch("gabbee.controller.ClipboardTextSink", return_value=clipboard_sink):
                sink = build_sink(config)
                result = sink.deliver("hello")

            self.assertTrue(result.ok)
            self.assertEqual(type_sink.received, ["hello"])
            self.assertEqual(ibus_sink.received, [])
            self.assertEqual(clipboard_sink.received, ["hello"])


if __name__ == "__main__":
    unittest.main()

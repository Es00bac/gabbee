# Voice Workflow Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the approved bundled Gabbee voice workflow polish release: queued utterances, dictation chaining, smarter number handling, Gemini fixes, macro/profile expansion, import/export, setup checks, UI/status polish, tests, and docs.

**Architecture:** Add small focused units around the existing controller instead of making `GabbeeController` own every detail. The controller will orchestrate recording jobs through a FIFO queue, `TextProcessor` will delegate number work to a new normalizer, advanced voice behavior will use explicit schema types, and setup checks/import-export will live in testable service modules.

**Tech Stack:** Python 3.11+, PyQt6, pytest/unittest, requests, PipeWire `pw-record`, KDE/IBus integration.

---

## File structure

- Create `src/gabbee/utterance_queue.py` — utterance job dataclasses, dictation-chain grouping, FIFO queue helpers.
- Modify `src/gabbee/models.py` — add queue-aware snapshot fields if needed by tests/UI.
- Modify `src/gabbee/controller.py` — replace one-worker flow with queued job processing.
- Create `src/gabbee/number_normalizer.py` — deterministic context-aware number parsing and formatting.
- Modify `src/gabbee/text_processor.py` — delegate number conversion to `NumberNormalizer` and support new macro actions.
- Modify `src/gabbee/stt/gemini.py` — isolate request payload building and improve API errors.
- Modify `src/gabbee/config.py` — align Gemini defaults and save/update behavior.
- Modify `src/gabbee/advanced_config.py` — add macro/profile schema, load/save/import/export validation.
- Modify `src/gabbee/ui/command_studio.py` — replace macro/profile placeholders with editors and import/export buttons.
- Create `src/gabbee/setup_checks.py` — non-mutating environment and integration checks.
- Modify `src/gabbee/ui/config_window.py` — expose setup checks and clarify Gemini setup.
- Modify `src/gabbee/ui/bar.py` — queue-aware status/button behavior.
- Modify `src/gabbee/plasma_dbus_service.py` — expose queue-aware state and remove debug output.
- Modify `src/gabbee/main_bar.py` — remove debug output and reload advanced config correctly.
- Modify `README.md` — document new workflow and remove completed next-step placeholders.
- Add/update tests in `tests/` for each unit.

---

## Task 1: Add utterance queue primitives

**Files:**
- Create: `src/gabbee/utterance_queue.py`
- Test: `tests/test_utterance_queue.py`

- [ ] **Step 1: Write failing queue primitive tests**

```python
from __future__ import annotations

from pathlib import Path

from gabbee.utterance_queue import UtteranceJob, group_dictation_chains


def test_group_dictation_chains_merges_close_dictation_jobs() -> None:
    jobs = [
        UtteranceJob(sequence=1, audio_path=Path("one.wav"), command_mode=False, created_at=10.0),
        UtteranceJob(sequence=2, audio_path=Path("two.wav"), command_mode=False, created_at=11.5),
    ]

    chains = group_dictation_chains(jobs, continuation_seconds=3.0)

    assert [[job.sequence for job in chain] for chain in chains] == [[1, 2]]


def test_group_dictation_chains_separates_old_dictation_jobs() -> None:
    jobs = [
        UtteranceJob(sequence=1, audio_path=Path("one.wav"), command_mode=False, created_at=10.0),
        UtteranceJob(sequence=2, audio_path=Path("two.wav"), command_mode=False, created_at=20.0),
    ]

    chains = group_dictation_chains(jobs, continuation_seconds=3.0)

    assert [[job.sequence for job in chain] for chain in chains] == [[1], [2]]


def test_group_dictation_chains_keeps_commands_separate() -> None:
    jobs = [
        UtteranceJob(sequence=1, audio_path=Path("one.wav"), command_mode=True, created_at=10.0),
        UtteranceJob(sequence=2, audio_path=Path("two.wav"), command_mode=True, created_at=11.0),
    ]

    chains = group_dictation_chains(jobs, continuation_seconds=3.0)

    assert [[job.sequence for job in chain] for chain in chains] == [[1], [2]]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_utterance_queue.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'gabbee.utterance_queue'`.

- [ ] **Step 3: Implement queue primitives**

```python
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic


@dataclass(slots=True, frozen=True)
class UtteranceJob:
    sequence: int
    audio_path: Path
    command_mode: bool
    created_at: float = field(default_factory=monotonic)


def group_dictation_chains(
    jobs: list[UtteranceJob],
    *,
    continuation_seconds: float,
) -> list[list[UtteranceJob]]:
    chains: list[list[UtteranceJob]] = []
    for job in sorted(jobs, key=lambda item: item.sequence):
        if job.command_mode or not chains:
            chains.append([job])
            continue

        previous = chains[-1][-1]
        if previous.command_mode:
            chains.append([job])
            continue

        if job.created_at - previous.created_at <= continuation_seconds:
            chains[-1].append(job)
        else:
            chains.append([job])
    return chains
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_utterance_queue.py -v`

Expected: PASS.

- [ ] **Step 5: Checkpoint**

Stage only `src/gabbee/utterance_queue.py` and `tests/test_utterance_queue.py` if commits are requested for this session.

---

## Task 2: Make controller accept recordings while queued work is processing

**Files:**
- Modify: `src/gabbee/controller.py`
- Modify: `src/gabbee/models.py`
- Test: `tests/test_controller.py`

- [ ] **Step 1: Write failing FIFO queue controller tests**

Add tests using fake recorder/transcriber/sink objects:

```python
from __future__ import annotations

from pathlib import Path

from gabbee.controller import GabbeeController
from gabbee.models import DeliveryResult, TranscriptionResult


class QueueRecorder:
    def __init__(self) -> None:
        self.paths: list[Path] = []
        self.is_recording = False

    def start(self, output_path: Path, source_name: str | None = None) -> None:
        self.is_recording = True
        self.paths.append(output_path)

    def stop(self) -> Path:
        self.is_recording = False
        return self.paths[-1]

    def cancel(self) -> None:
        self.is_recording = False


class QueueTranscriber:
    provider_name = "fake"

    def __init__(self, texts: list[str]) -> None:
        self.texts = texts
        self.paths: list[Path] = []

    def transcribe(self, audio_path: Path) -> TranscriptionResult:
        self.paths.append(audio_path)
        return TranscriptionResult(text=self.texts.pop(0), provider="fake", language_code="en")


class QueueSink:
    def __init__(self) -> None:
        self.delivered: list[str] = []
        self.keys: list[str] = []

    def deliver(self, text: str) -> DeliveryResult:
        self.delivered.append(text)
        return DeliveryResult(ok=True, method="fake", detail="ok")

    def deliver_key(self, key_stroke: str) -> DeliveryResult:
        self.keys.append(key_stroke)
        return DeliveryResult(ok=True, method="fake", detail="ok")


def test_controller_processes_queued_utterances_in_order(config) -> None:
    recorder = QueueRecorder()
    transcriber = QueueTranscriber(["first", "second"])
    sink = QueueSink()
    controller = GabbeeController(config, recorder=recorder, transcriber=transcriber, sink=sink)

    controller.start()
    controller.stop()
    controller.start()
    controller.stop()
    controller.wait_for_background(timeout=2)

    assert sink.delivered == ["first second"]
    assert controller.snapshot().queue_depth == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_controller.py::test_controller_processes_queued_utterances_in_order -v`

Expected: FAIL because the second `start()` is ignored while state is transcribing or snapshot has no `queue_depth`.

- [ ] **Step 3: Add queue-aware snapshot fields**

Update `src/gabbee/models.py` so `ControllerSnapshot` includes:

```python
queue_depth: int = 0
active_recording: bool = False
```

- [ ] **Step 4: Implement controller queue drain**

Change `GabbeeController` to maintain `_pending_jobs`, `_next_sequence`, `_queue_worker`, and `_continuation_seconds = 3.0`. `stop()` should append an `UtteranceJob` and start one queue worker if none is alive. `start()` should allow recording when `state` is `IDLE`, `ERROR`, `TRANSCRIBING`, or `DELIVERING`, as long as the recorder is not already recording.

- [ ] **Step 5: Run controller tests**

Run: `pytest tests/test_controller.py -v`

Expected: PASS after updating tests that assert old single-worker behavior.

- [ ] **Step 6: Checkpoint**

Stage only controller/model/test changes if commits are requested.

---

## Task 3: Add context-aware number normalizer

**Files:**
- Create: `src/gabbee/number_normalizer.py`
- Modify: `src/gabbee/text_processor.py`
- Test: `tests/test_text_processor.py`
- Test: `tests/test_number_normalizer.py`

- [ ] **Step 1: Write failing motivating tests**

```python
from gabbee.number_normalizer import NumberNormalizer


def normalize(text: str) -> str:
    return NumberNormalizer().normalize(text)


def test_casual_money_range_stays_words() -> None:
    assert normalize("I gave Bill one or two dollars") == "I gave Bill one or two dollars"


def test_coordinate_decimal_sequences_become_single_numbers() -> None:
    assert normalize(
        "The coordinates are nine point two seven one eight three north by four point one two three four five six seven east"
    ) == "The coordinates are 9.27183 north by 4.1234567 east"


def test_scientific_mixed_artifact_repairs_to_measurement_number() -> None:
    assert normalize("The speed of light is 100 eighty-six 1000 miles per second") == "The speed of light is 186,000 miles per second"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_number_normalizer.py -v`

Expected: FAIL with module not found.

- [ ] **Step 3: Implement deterministic normalizer**

Implement `NumberNormalizer.normalize(text: str) -> str` with helpers for:

- decimal phrases: `nine point two seven` -> `9.27`
- coordinate context around `coordinates`, `north`, `south`, `east`, `west`, `latitude`, `longitude`
- prose ranges: `one or two dollars` remains words
- measurement/science cues: `miles per second`, `meters`, `percent`, `degrees`, `kilometers`, `seconds`, `minutes`, `hours`
- mixed artifact repair: `100 eighty-six 1000` -> `186,000`

- [ ] **Step 4: Wire into TextProcessor**

In `src/gabbee/text_processor.py`, import and instantiate `NumberNormalizer`, then replace the existing `_convert_numbers` call with the new normalizer. Leave legacy helper methods only if tests still need them; otherwise remove them after tests pass.

- [ ] **Step 5: Run text processor tests**

Run: `pytest tests/test_number_normalizer.py tests/test_text_processor.py -v`

Expected: PASS.

- [ ] **Step 6: Checkpoint**

Stage only number normalizer and text processor tests if commits are requested.

---

## Task 4: Fix Gemini config and provider errors

**Files:**
- Modify: `src/gabbee/config.py`
- Modify: `src/gabbee/stt/gemini.py`
- Modify: `src/gabbee/ui/config_window.py`
- Test: `tests/test_config.py`
- Test: `tests/test_gemini.py`

- [ ] **Step 1: Write failing Gemini tests**

Add tests that assert:

```python
def test_gemini_default_model_is_consistent(config_paths) -> None:
    config = load_config(config_paths)
    assert config.gemini_model == "gemini-2.5-flash"


def test_gemini_payload_contains_audio_and_prompt(tmp_path, config) -> None:
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"RIFFfake")
    provider = GeminiSpeechToText(config)
    payload = provider._build_payload(audio)
    assert payload["contents"][0]["parts"][1]["inline_data"]["mime_type"] == "audio/wav"
    assert "Output ONLY" in payload["contents"][0]["parts"][0]["text"]
```

- [ ] **Step 2: Run Gemini tests to verify failure**

Run: `pytest tests/test_config.py tests/test_gemini.py -v`

Expected: FAIL because defaults differ and `_build_payload` does not exist.

- [ ] **Step 3: Align config default**

Set both `AppConfig.gemini_model` and `load_config(... GABBEE_GEMINI_MODEL ...)` default to `gemini-2.5-flash`.

- [ ] **Step 4: Isolate Gemini payload building**

Add `_build_payload(audio_path: Path) -> dict[str, object]` to `GeminiSpeechToText` and call it from `transcribe()`.

- [ ] **Step 5: Improve API error messages**

Ensure non-200 responses raise `RuntimeError(f"Gemini API error ({response.status_code}): {message}")`, preserving key/model failure details without printing the API key.

- [ ] **Step 6: Run Gemini/config tests**

Run: `pytest tests/test_config.py tests/test_gemini.py -v`

Expected: PASS.

---

## Task 5: Expand advanced config schema for macros and profiles

**Files:**
- Modify: `src/gabbee/advanced_config.py`
- Test: `tests/test_advanced_config.py`

- [ ] **Step 1: Write failing schema tests**

Add tests for loading/saving:

```python
def test_loads_macro_sequence_and_profile(tmp_path) -> None:
    path = tmp_path / "advanced.json"
    path.write_text('''{
      "metadata": {"version": 2},
      "vocabulary": [],
      "commands": [],
      "macros": [{
        "name": "test macro",
        "spoken": "run tests",
        "enabled": true,
        "steps": [
          {"type": "type_cli", "text": "pytest"},
          {"type": "wait", "seconds": 0.2},
          {"type": "press_key", "key": "Control+l"}
        ]
      }],
      "profiles": [{"name": "default", "enabled": true, "macro_names": ["test macro"]}]
    }''', encoding="utf-8")

    config = load_advanced_config(path)

    assert config.macros[0].spoken == "run tests"
    assert config.macros[0].steps[0].type == "type_cli"
    assert config.profiles[0].name == "default"
```

- [ ] **Step 2: Run test to verify failure**

Run: `pytest tests/test_advanced_config.py -v`

Expected: FAIL because macros/profiles are not defined.

- [ ] **Step 3: Add schema dataclasses**

Add:

```python
@dataclass(slots=True)
class MacroStep:
    type: str
    text: str = ""
    key: str = ""
    seconds: float = 0.0

@dataclass(slots=True)
class MacroEntry:
    name: str
    spoken: str
    steps: list[MacroStep]
    enabled: bool = True

@dataclass(slots=True)
class ProfileEntry:
    name: str
    enabled: bool = True
    vocabulary_spoken: list[str] = field(default_factory=list)
    command_spoken: list[str] = field(default_factory=list)
    macro_names: list[str] = field(default_factory=list)
```

Update `AdvancedConfig` with `macros` and `profiles` lists.

- [ ] **Step 4: Add load/save support**

Support action types: `type_text`, `type_cli`, `press_key`, `wait`. Reject malformed entries by skipping them and preserving a clear `load_error` only for unreadable files or invalid root objects.

- [ ] **Step 5: Run advanced config tests**

Run: `pytest tests/test_advanced_config.py -v`

Expected: PASS.

---

## Task 6: Execute macro sequences from command mode

**Files:**
- Modify: `src/gabbee/text_processor.py`
- Modify: `src/gabbee/controller.py`
- Test: `tests/test_text_processor.py`
- Test: `tests/test_controller.py`

- [ ] **Step 1: Write failing macro processing tests**

```python
def test_command_mode_macro_sequence_returns_actions() -> None:
    config = AdvancedConfig(
        macros=[
            MacroEntry(
                name="tests",
                spoken="run tests",
                steps=[MacroStep(type="type_cli", text="pytest"), MacroStep(type="wait", seconds=0.2)],
            )
        ]
    )
    processor = TextProcessor(advanced_config=config)

    assert processor.process_to_actions("run tests", enable_commands=True) == [
        ("text", "pytest"),
        ("wait", "0.2"),
    ]
```

- [ ] **Step 2: Run test to verify failure**

Run: `pytest tests/test_text_processor.py::test_command_mode_macro_sequence_returns_actions -v`

Expected: FAIL because macros are ignored.

- [ ] **Step 3: Implement macro lookup**

Add `_custom_macro_for(text: str)` to `TextProcessor`. When command mode is enabled and a macro matches, convert steps to actions:

- `type_text` and `type_cli` -> `("text", step.text)`
- `press_key` -> `("key", step.key)`
- `wait` -> `("wait", str(step.seconds))`

- [ ] **Step 4: Implement controller wait action**

In controller delivery loop, support `action_type == "wait"` by sleeping for the parsed duration with an upper bound of 10 seconds.

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_text_processor.py tests/test_controller.py -v`

Expected: PASS.

---

## Task 7: Add import/export helpers

**Files:**
- Modify: `src/gabbee/advanced_config.py`
- Test: `tests/test_advanced_config.py`

- [ ] **Step 1: Write failing import/export tests**

```python
def test_export_excludes_provider_secrets(tmp_path) -> None:
    source = AdvancedConfig(vocabulary=[VocabularyEntry(spoken="gabby", written="Gabbee")])
    export_path = tmp_path / "export.json"

    export_advanced_config(export_path, source)

    text = export_path.read_text(encoding="utf-8")
    assert "GEMINI_API_KEY" not in text
    assert "ELEVENLABS_API_KEY" not in text
    assert "gabby" in text


def test_import_rejects_non_object_root(tmp_path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("[]", encoding="utf-8")

    imported = import_advanced_config(path)

    assert imported.load_error.endswith("expected JSON object")
```

- [ ] **Step 2: Run test to verify failure**

Run: `pytest tests/test_advanced_config.py -v`

Expected: FAIL because import/export functions do not exist.

- [ ] **Step 3: Implement helpers**

Add:

```python
def export_advanced_config(path: Path, config: AdvancedConfig) -> None:
    save_advanced_config(path, config)


def import_advanced_config(path: Path) -> AdvancedConfig:
    return load_advanced_config(path)
```

Ensure `save_advanced_config` serializes only advanced config fields and metadata.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_advanced_config.py -v`

Expected: PASS.

---

## Task 8: Replace Command Studio macro/profile placeholders

**Files:**
- Modify: `src/gabbee/ui/command_studio.py`
- Test: `tests/test_command_studio.py`

- [ ] **Step 1: Write failing UI tests**

Assert sidebar labels are real:

```python
def test_command_studio_has_macro_and_profile_pages(qt_app, tmp_path) -> None:
    window = CommandStudioWindow(tmp_path / "advanced.json")

    labels = [window.sidebar.item(index).text() for index in range(window.sidebar.count())]

    assert "Macros" in labels
    assert "Profiles" in labels
    assert "Macros (later)" not in labels
    assert "Profiles (later)" not in labels
```

- [ ] **Step 2: Run test to verify failure**

Run: `pytest tests/test_command_studio.py -v`

Expected: FAIL because placeholder labels exist.

- [ ] **Step 3: Add macro/profile pages**

Replace placeholder labels with `Macros` and `Profiles`. Add basic tables and add/delete editors using the new schema. Keep UI simple: one macro row can store name, spoken phrase, enabled, and a plain multi-line steps text representation.

- [ ] **Step 4: Add import/export buttons**

Add buttons that call advanced config import/export helpers using file dialogs. Tests can directly exercise helper methods without opening native dialogs.

- [ ] **Step 5: Run UI tests**

Run: `pytest tests/test_command_studio.py -v`

Expected: PASS.

---

## Task 9: Add non-mutating setup checks

**Files:**
- Create: `src/gabbee/setup_checks.py`
- Modify: `src/gabbee/ui/config_window.py`
- Test: `tests/test_setup_checks.py`
- Test: `tests/test_config_window.py`

- [ ] **Step 1: Write failing setup checks tests**

```python
from gabbee.setup_checks import run_setup_checks


def test_setup_checks_report_missing_provider_key(config) -> None:
    config.stt_provider = "gemini"
    config.gemini_api_key = None

    checks = run_setup_checks(config, command_exists=lambda command: False)

    missing = {check.name: check for check in checks}
    assert missing["Gemini API key"].ok is False
    assert "GEMINI_API_KEY" in missing["Gemini API key"].detail
```

- [ ] **Step 2: Run test to verify failure**

Run: `pytest tests/test_setup_checks.py -v`

Expected: FAIL because module does not exist.

- [ ] **Step 3: Implement setup checks**

Add `SetupCheck` dataclass and `run_setup_checks(config, command_exists=shutil.which)` returning checks for env file, provider key, `pw-record`, clipboard tool, IBus component path, and portal shortcut availability as best-effort non-mutating checks.

- [ ] **Step 4: Add config window entry point**

Add a `Setup Checks...` button that opens a simple dialog listing check names and details. Tests should assert the button exists.

- [ ] **Step 5: Run setup/config window tests**

Run: `pytest tests/test_setup_checks.py tests/test_config_window.py -v`

Expected: PASS.

---

## Task 10: Queue-aware bar, DBus, and debug cleanup

**Files:**
- Modify: `src/gabbee/ui/bar.py`
- Modify: `src/gabbee/plasma_dbus_service.py`
- Modify: `src/gabbee/main_bar.py`
- Test: `tests/test_ui_bar.py`
- Test: `tests/test_plasmoid_package.py`

- [ ] **Step 1: Write failing UI state tests**

Add a snapshot test asserting queue depth appears in status text when nonzero.

```python
def test_bar_shows_queue_depth(qt_app, controller) -> None:
    bar = FloatingBar(qt_app, controller, global_shortcut_factory=None)
    snapshot = controller.snapshot()
    snapshot.queue_depth = 2

    bar._apply_snapshot(snapshot)

    assert "2" in bar.status_chip.text() or "2" in bar.hint_label.text()
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/test_ui_bar.py -v`

Expected: FAIL until snapshot/status code supports queue depth.

- [ ] **Step 3: Update status rendering**

Show `Transcribing (2 queued)` or similar when queue depth is nonzero. Enable Start when not actively recording even if queued work is processing. Enable Stop/Cancel only during active recording.

- [ ] **Step 4: Remove debug prints**

Remove startup debug prints from `src/gabbee/main_bar.py` and HTTP server debug prints from `src/gabbee/plasma_dbus_service.py`. Keep meaningful exception messages returned to the UI or process exit codes.

- [ ] **Step 5: Fix advanced config reload**

Change `controller.reload_transcriber()` to rebuild processor through `build_processor(config)` so Command Studio saves are picked up.

- [ ] **Step 6: Run UI/plasma tests**

Run: `pytest tests/test_ui_bar.py tests/test_plasmoid_package.py tests/test_controller.py -v`

Expected: PASS.

---

## Task 11: Documentation and release checks

**Files:**
- Modify: `README.md`
- Test: existing suite

- [ ] **Step 1: Update README sections**

Document:

- queued utterance behavior
- continuation chaining
- Gemini setup keys and default model
- number normalization expectations
- macro safety model
- import/export
- setup checks

- [ ] **Step 2: Update Next steps**

Remove completed entries for macros/profiles/import/export/setup checks. Keep real future work such as provider-backed streaming/preedit if not implemented.

- [ ] **Step 3: Run full test suite**

Run: `pytest -q`

Expected: PASS.

- [ ] **Step 4: Run lint**

Run: `ruff check .`

Expected: PASS or only pre-existing issues that are documented before finishing.

- [ ] **Step 5: Manual UI verification**

Run: `gabbee-bar` from the project venv and verify:

- bar launches
- Start/Stop queues at least one recording
- second Start is possible while first utterance is processing
- config window opens
- Command Studio macro/profile pages open
- setup checks open

---

## Self-review

Spec coverage:

- queued utterances: Tasks 1, 2, 10
- dictation chaining: Tasks 1, 2, 3
- number normalization: Task 3
- Gemini: Task 4
- macros/profiles: Tasks 5, 6, 8
- import/export: Task 7
- setup checks: Task 9
- UI/status polish: Task 10
- docs/tests: Task 11

Placeholder scan: no `TBD`, `TODO`, or unspecified edge-case steps remain. Large implementation steps identify exact files, tests, and expected commands.

Type consistency: plan uses `UtteranceJob`, `ControllerSnapshot.queue_depth`, `NumberNormalizer`, `MacroStep`, `MacroEntry`, `ProfileEntry`, `export_advanced_config`, `import_advanced_config`, and `SetupCheck` consistently across tasks.

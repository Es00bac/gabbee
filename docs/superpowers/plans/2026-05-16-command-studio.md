# Command Studio Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Command Studio: an advanced configuration UI for custom vocabulary, custom secondary-binding commands, and safe previewing.

**Architecture:** Add a small JSON-backed advanced configuration model, pass it into `TextProcessor`, and expose it through a separate PyQt6 `CommandStudioWindow`. Primary dictation remains text-only; custom commands are only evaluated when `enable_commands=True`, which is already controlled by the secondary command shortcut.

**Tech Stack:** Python, PyQt6, pytest/unittest, JSON config under `AppPaths.config_dir`, existing `TextProcessor` action tuples.

---

## File structure

- Create `src/gabbee/advanced_config.py`
  - Dataclasses for vocabulary entries, command entries, command actions, and the full advanced config.
  - JSON load/save helpers.
  - Preview helper data for UI warnings.
- Modify `src/gabbee/app_paths.py`
  - Add `advanced_config_file` path under the existing config directory.
- Modify `src/gabbee/text_processor.py`
  - Accept advanced config.
  - Apply enabled vocabulary before dot/number normalization.
  - In command mode, exact-match enabled custom commands before built-ins.
  - Treat `type_cli` as text output, not key submission.
- Modify `src/gabbee/controller.py`
  - Load advanced config into `TextProcessor` during initialization and reload.
- Create `src/gabbee/ui/command_studio.py`
  - PyQt6 dialog with Vocabulary, Commands, and Test / Preview pages.
- Modify `src/gabbee/ui/config_window.py`
  - Add an `Advanced...` button that opens Command Studio without saving basic settings.
- Modify `src/gabbee/ui/tray.py`
  - Add `Command Studio...` tray action.
- Modify `src/gabbee/main_bar.py`
  - Wire tray/config entry points to Command Studio and reload the controller processor after saves.
- Create `tests/test_advanced_config.py`
  - JSON load/save and invalid-file behavior.
- Modify `tests/test_text_processor.py`
  - Advanced vocabulary/custom command behavior.
- Create `tests/test_command_studio.py`
  - Offscreen Qt UI add/edit/delete/preview behavior.
- Modify `tests/test_tray.py`
  - Tray action existence.
- Modify `README.md`
  - Document Command Studio basics and secondary-binding safety.

---

### Task 1: Add JSON-backed advanced configuration

**Files:**
- Create: `src/gabbee/advanced_config.py`
- Modify: `src/gabbee/app_paths.py:38-41`
- Test: `tests/test_advanced_config.py`

- [ ] **Step 1: Write failing tests for defaults, save/load, and invalid JSON preservation**

Create `tests/test_advanced_config.py`:

```python
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from gabbee.advanced_config import (
    AdvancedConfig,
    CommandAction,
    CommandEntry,
    VocabularyEntry,
    load_advanced_config,
    save_advanced_config,
)
from gabbee.app_paths import AppPaths


class AdvancedConfigTests(unittest.TestCase):
    def test_missing_file_loads_empty_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "advanced.json"

            config = load_advanced_config(path)

            self.assertEqual(config.vocabulary, [])
            self.assertEqual(config.commands, [])
            self.assertEqual(config.metadata["version"], 1)
            self.assertEqual(config.load_error, "")

    def test_saves_and_loads_vocabulary_and_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "advanced.json"
            config = AdvancedConfig(
                vocabulary=[VocabularyEntry(spoken="gabby", written="Gabbee", enabled=True)],
                commands=[
                    CommandEntry(
                        spoken="run tests",
                        action=CommandAction(type="type_cli", text="pytest"),
                        enabled=True,
                    )
                ],
            )

            save_advanced_config(path, config)
            loaded = load_advanced_config(path)

            self.assertEqual(loaded.vocabulary[0].spoken, "gabby")
            self.assertEqual(loaded.vocabulary[0].written, "Gabbee")
            self.assertEqual(loaded.commands[0].spoken, "run tests")
            self.assertEqual(loaded.commands[0].action.type, "type_cli")
            self.assertEqual(loaded.commands[0].action.text, "pytest")
            self.assertEqual(loaded.load_error, "")

    def test_invalid_json_returns_empty_config_with_error_without_rewriting_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "advanced.json"
            path.write_text("{not json", encoding="utf-8")

            config = load_advanced_config(path)

            self.assertEqual(config.vocabulary, [])
            self.assertEqual(config.commands, [])
            self.assertIn("advanced.json", config.load_error)
            self.assertEqual(path.read_text(encoding="utf-8"), "{not json")

    def test_app_paths_exposes_advanced_config_file(self) -> None:
        root = Path("/tmp/gabbee-test")
        paths = AppPaths(
            config_dir=root / "config",
            state_dir=root / "state",
            cache_dir=root / "cache",
            runtime_dir=root / "runtime",
        )

        self.assertEqual(paths.advanced_config_file, root / "config" / "advanced.json")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
python -m pytest tests/test_advanced_config.py -v
```

Expected: FAIL during import with `ModuleNotFoundError: No module named 'gabbee.advanced_config'`.

- [ ] **Step 3: Implement minimal advanced config model**

Create `src/gabbee/advanced_config.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class VocabularyEntry:
    spoken: str
    written: str
    enabled: bool = True


@dataclass(slots=True)
class CommandAction:
    type: str
    text: str = ""
    key: str = ""


@dataclass(slots=True)
class CommandEntry:
    spoken: str
    action: CommandAction
    enabled: bool = True


@dataclass(slots=True)
class AdvancedConfig:
    vocabulary: list[VocabularyEntry] = field(default_factory=list)
    commands: list[CommandEntry] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=lambda: {"version": 1})
    load_error: str = ""


def _as_bool(value: object, default: bool = True) -> bool:
    return value if isinstance(value, bool) else default


def _as_str(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _load_vocabulary(raw_items: object) -> list[VocabularyEntry]:
    entries: list[VocabularyEntry] = []
    if not isinstance(raw_items, list):
        return entries
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        spoken = _as_str(item.get("spoken"))
        written = _as_str(item.get("written"))
        if spoken and written:
            entries.append(VocabularyEntry(spoken=spoken, written=written, enabled=_as_bool(item.get("enabled"))))
    return entries


def _load_commands(raw_items: object) -> list[CommandEntry]:
    entries: list[CommandEntry] = []
    if not isinstance(raw_items, list):
        return entries
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        spoken = _as_str(item.get("spoken"))
        raw_action = item.get("action")
        if not spoken or not isinstance(raw_action, dict):
            continue
        action_type = _as_str(raw_action.get("type"))
        if action_type not in {"type_text", "type_cli", "press_key"}:
            continue
        action = CommandAction(
            type=action_type,
            text=_as_str(raw_action.get("text")),
            key=_as_str(raw_action.get("key")),
        )
        entries.append(CommandEntry(spoken=spoken, action=action, enabled=_as_bool(item.get("enabled"))))
    return entries


def load_advanced_config(path: Path) -> AdvancedConfig:
    if not path.exists():
        return AdvancedConfig()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return AdvancedConfig(load_error=f"{path}: {exc}")
    if not isinstance(raw, dict):
        return AdvancedConfig(load_error=f"{path}: expected JSON object")
    metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {"version": 1}
    metadata.setdefault("version", 1)
    return AdvancedConfig(
        vocabulary=_load_vocabulary(raw.get("vocabulary")),
        commands=_load_commands(raw.get("commands")),
        metadata=metadata,
    )


def save_advanced_config(path: Path, config: AdvancedConfig) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "metadata": {"version": int(config.metadata.get("version", 1))},
        "vocabulary": [
            {"spoken": entry.spoken, "written": entry.written, "enabled": entry.enabled}
            for entry in config.vocabulary
        ],
        "commands": [
            {
                "spoken": entry.spoken,
                "action": {
                    "type": entry.action.type,
                    "text": entry.action.text,
                    "key": entry.action.key,
                },
                "enabled": entry.enabled,
            }
            for entry in config.commands
        ],
    }
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
```

Modify `src/gabbee/app_paths.py` by adding this property after `vocabulary_file`:

```python
    @property
    def advanced_config_file(self) -> Path:
        return self.config_dir / "advanced.json"
```

- [ ] **Step 4: Run tests to verify Task 1 passes**

Run:

```bash
python -m pytest tests/test_advanced_config.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit Task 1 if commits are requested**

```bash
git add src/gabbee/advanced_config.py src/gabbee/app_paths.py tests/test_advanced_config.py
git commit -m "Add advanced configuration model"
```

---

### Task 2: Integrate advanced vocabulary and custom commands into TextProcessor

**Files:**
- Modify: `src/gabbee/text_processor.py`
- Test: `tests/test_text_processor.py`

- [ ] **Step 1: Write failing TextProcessor tests**

Append these tests to `TextProcessorTests` in `tests/test_text_processor.py`:

```python
    def test_advanced_vocabulary_applies_in_dictation_mode(self) -> None:
        from gabbee.advanced_config import AdvancedConfig, VocabularyEntry

        processor = TextProcessor(
            advanced_config=AdvancedConfig(
                vocabulary=[VocabularyEntry(spoken="gabby", written="Gabbee", enabled=True)]
            )
        )

        actions = processor.process_to_actions("open gabby")

        self.assertEqual(actions, [("text", "open Gabbee")])

    def test_disabled_advanced_vocabulary_is_ignored(self) -> None:
        from gabbee.advanced_config import AdvancedConfig, VocabularyEntry

        processor = TextProcessor(
            advanced_config=AdvancedConfig(
                vocabulary=[VocabularyEntry(spoken="gabby", written="Gabbee", enabled=False)]
            )
        )

        actions = processor.process_to_actions("open gabby")

        self.assertEqual(actions, [("text", "open gabby")])

    def test_custom_command_exact_match_takes_priority_over_builtin(self) -> None:
        from gabbee.advanced_config import AdvancedConfig, CommandAction, CommandEntry

        processor = TextProcessor(
            advanced_config=AdvancedConfig(
                commands=[
                    CommandEntry(
                        spoken="delete",
                        action=CommandAction(type="type_text", text="rm -i"),
                        enabled=True,
                    )
                ]
            )
        )

        actions = processor.process_to_actions("delete", enable_commands=True)

        self.assertEqual(actions, [("text", "rm -i")])

    def test_custom_commands_do_not_run_in_dictation_mode(self) -> None:
        from gabbee.advanced_config import AdvancedConfig, CommandAction, CommandEntry

        processor = TextProcessor(
            advanced_config=AdvancedConfig(
                commands=[
                    CommandEntry(
                        spoken="run tests",
                        action=CommandAction(type="type_cli", text="pytest"),
                        enabled=True,
                    )
                ]
            )
        )

        actions = processor.process_to_actions("run tests", enable_commands=False)

        self.assertEqual(actions, [("text", "run tests")])

    def test_type_cli_command_types_text_without_enter(self) -> None:
        from gabbee.advanced_config import AdvancedConfig, CommandAction, CommandEntry

        processor = TextProcessor(
            advanced_config=AdvancedConfig(
                commands=[
                    CommandEntry(
                        spoken="run tests",
                        action=CommandAction(type="type_cli", text="pytest"),
                        enabled=True,
                    )
                ]
            )
        )

        actions = processor.process_to_actions("run tests", enable_commands=True)

        self.assertEqual(actions, [("text", "pytest")])

    def test_press_key_custom_command_returns_key_action(self) -> None:
        from gabbee.advanced_config import AdvancedConfig, CommandAction, CommandEntry

        processor = TextProcessor(
            advanced_config=AdvancedConfig(
                commands=[
                    CommandEntry(
                        spoken="cancel that",
                        action=CommandAction(type="press_key", key="Escape"),
                        enabled=True,
                    )
                ]
            )
        )

        actions = processor.process_to_actions("cancel that", enable_commands=True)

        self.assertEqual(actions, [("key", "Escape")])
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
python -m pytest tests/test_text_processor.py -v
```

Expected: FAIL with `TypeError: TextProcessor.__init__() got an unexpected keyword argument 'advanced_config'`.

- [ ] **Step 3: Implement TextProcessor integration**

Modify `src/gabbee/text_processor.py` imports:

```python
from .advanced_config import AdvancedConfig, CommandEntry
```

Change the constructor signature and setup:

```python
class TextProcessor:
    def __init__(
        self,
        keyword_map: dict[str, str] | None = None,
        vocabulary_path: Path | None = None,
        advanced_config: AdvancedConfig | None = None,
    ) -> None:
        self.vocabulary_path = vocabulary_path
        self.advanced_config = advanced_config or AdvancedConfig()
```

Keep the existing built-in `keyword_map` setup. After `_load_vocabulary()` runs, merge enabled advanced vocabulary:

```python
        self.custom_vocabulary: dict[str, str] = {}
        self._load_vocabulary()
        self.custom_vocabulary.update(
            {
                entry.spoken.lower().strip(): entry.written.strip()
                for entry in self.advanced_config.vocabulary
                if entry.enabled and entry.spoken.strip() and entry.written.strip()
            }
        )
```

Add this helper method before `_apply_vocabulary`:

```python
    def _custom_command_for(self, text: str) -> CommandEntry | None:
        normalized = text.strip().lower()
        for command in self.advanced_config.commands:
            if command.enabled and command.spoken.strip().lower() == normalized:
                return command
        return None
```

In `process_to_actions`, after dot/number conversion and before built-in keyword parsing, add:

```python
        custom_command = self._custom_command_for(text)
        if custom_command is not None:
            action = custom_command.action
            if action.type in {"type_text", "type_cli"}:
                return [("text", action.text)] if action.text else []
            if action.type == "press_key":
                return [("key", action.key)] if action.key else []
```

- [ ] **Step 4: Run tests to verify Task 2 passes**

Run:

```bash
python -m pytest tests/test_text_processor.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit Task 2 if commits are requested**

```bash
git add src/gabbee/text_processor.py tests/test_text_processor.py
git commit -m "Apply advanced vocabulary and commands"
```

---

### Task 3: Load advanced config through the controller

**Files:**
- Modify: `src/gabbee/controller.py`
- Test: `tests/test_controller.py`

- [ ] **Step 1: Write failing controller reload test**

Add this test to `tests/test_controller.py` using the existing fake recorder/transcriber/sink patterns in that file:

```python
    def test_reload_transcriber_reloads_advanced_config(self) -> None:
        from gabbee.advanced_config import AdvancedConfig, VocabularyEntry, save_advanced_config

        config = make_config()
        save_advanced_config(
            config.paths.advanced_config_file,
            AdvancedConfig(vocabulary=[VocabularyEntry(spoken="gabby", written="Gabbee")]),
        )
        controller = GabbeeController(
            config,
            recorder=FakeRecorder(),
            transcriber=FakeTranscriber("hello gabby"),
            sink=FakeSink(),
        )

        actions = controller.processor.process_to_actions("hello gabby")

        self.assertEqual(actions, [("text", "hello Gabbee")])
```

If `tests/test_controller.py` uses different helper names, keep its existing helper style and only change the assertion body above.

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python -m pytest tests/test_controller.py::ControllerTests::test_reload_transcriber_reloads_advanced_config -v
```

Expected: FAIL because `controller.processor` does not yet load `advanced_config_file`.

- [ ] **Step 3: Implement controller advanced config loading**

Modify `src/gabbee/controller.py` imports:

```python
from .advanced_config import load_advanced_config
```

Add a helper near `build_sink`:

```python
def build_processor(config: AppConfig) -> TextProcessor:
    return TextProcessor(
        config.keyword_map,
        config.paths.vocabulary_file,
        load_advanced_config(config.paths.advanced_config_file),
    )
```

Change constructor processor setup:

```python
        self.processor = build_processor(config)
```

Change `reload_transcriber()` processor setup:

```python
            self.processor = build_processor(self.config)
```

- [ ] **Step 4: Run controller tests**

Run:

```bash
python -m pytest tests/test_controller.py -v
```

Expected: PASS, except for unrelated environment failures if local IBus GI bindings are missing in this machine-wide run.

- [ ] **Step 5: Commit Task 3 if commits are requested**

```bash
git add src/gabbee/controller.py tests/test_controller.py
git commit -m "Load advanced config in controller"
```

---

### Task 4: Add Command Studio UI for vocabulary, commands, and preview

**Files:**
- Create: `src/gabbee/ui/command_studio.py`
- Test: `tests/test_command_studio.py`

- [ ] **Step 1: Write failing UI tests**

Create `tests/test_command_studio.py`:

```python
from __future__ import annotations

from pathlib import Path
import os
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PyQt6.QtWidgets import QApplication

from gabbee.advanced_config import load_advanced_config
from gabbee.ui.command_studio import CommandStudioWindow


class CommandStudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_adds_vocabulary_entry_and_saves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "advanced.json"
            window = CommandStudioWindow(path)

            window.vocab_spoken_input.setText("gabby")
            window.vocab_written_input.setText("Gabbee")
            window.add_vocabulary_button.click()
            window.save()

            loaded = load_advanced_config(path)
            self.assertEqual(loaded.vocabulary[0].spoken, "gabby")
            self.assertEqual(loaded.vocabulary[0].written, "Gabbee")
            window.close()

    def test_adds_cli_command_entry_and_saves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "advanced.json"
            window = CommandStudioWindow(path)

            window.command_spoken_input.setText("run tests")
            window.command_action_type.setCurrentText("Type CLI command")
            window.command_payload_input.setText("pytest")
            window.add_command_button.click()
            window.save()

            loaded = load_advanced_config(path)
            self.assertEqual(loaded.commands[0].spoken, "run tests")
            self.assertEqual(loaded.commands[0].action.type, "type_cli")
            self.assertEqual(loaded.commands[0].action.text, "pytest")
            window.close()

    def test_preview_shows_dictation_vocabulary_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "advanced.json"
            window = CommandStudioWindow(path)
            window.vocab_spoken_input.setText("gabby")
            window.vocab_written_input.setText("Gabbee")
            window.add_vocabulary_button.click()

            window.preview_input.setText("open gabby")
            window.preview_mode.setCurrentText("Dictation")
            window.update_preview()

            self.assertIn("text: open Gabbee", window.preview_output.toPlainText())
            window.close()

    def test_preview_shows_command_only_on_command_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "advanced.json"
            window = CommandStudioWindow(path)
            window.command_spoken_input.setText("run tests")
            window.command_action_type.setCurrentText("Type CLI command")
            window.command_payload_input.setText("pytest")
            window.add_command_button.click()

            window.preview_input.setText("run tests")
            window.preview_mode.setCurrentText("Dictation")
            window.update_preview()
            self.assertIn("text: run tests", window.preview_output.toPlainText())

            window.preview_mode.setCurrentText("Command")
            window.update_preview()
            self.assertIn("text: pytest", window.preview_output.toPlainText())
            window.close()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
python -m pytest tests/test_command_studio.py -v
```

Expected: FAIL during import with `ModuleNotFoundError: No module named 'gabbee.ui.command_studio'`.

- [ ] **Step 3: Implement Command Studio window**

Create `src/gabbee/ui/command_studio.py`:

```python
from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..advanced_config import (
    AdvancedConfig,
    CommandAction,
    CommandEntry,
    VocabularyEntry,
    load_advanced_config,
    save_advanced_config,
)
from ..text_processor import TextProcessor


class CommandStudioWindow(QDialog):
    def __init__(self, config_path: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config_path = config_path
        self.advanced_config = load_advanced_config(config_path)
        self.setWindowTitle("Gabbee Command Studio")
        self.setMinimumSize(760, 460)

        root = QVBoxLayout(self)
        body = QHBoxLayout()
        root.addLayout(body, 1)

        self.sidebar = QListWidget()
        self.sidebar.addItems(["Vocabulary", "Commands", "Test / Preview", "Macros (later)", "Profiles (later)"])
        self.sidebar.setCurrentRow(0)
        body.addWidget(self.sidebar, 0)

        self.pages = QStackedWidget()
        body.addWidget(self.pages, 1)
        self.pages.addWidget(self._build_vocabulary_page())
        self.pages.addWidget(self._build_commands_page())
        self.pages.addWidget(self._build_preview_page())
        self.pages.addWidget(QLabel("Macro recording will be added in a later version."))
        self.pages.addWidget(QLabel("Profiles will be added in a later version."))
        self.sidebar.currentRowChanged.connect(self.pages.setCurrentIndex)

        buttons = QHBoxLayout()
        root.addLayout(buttons)
        buttons.addStretch(1)
        self.save_button = QPushButton("Save")
        self.revert_button = QPushButton("Revert")
        self.close_button = QPushButton("Close")
        self.save_button.clicked.connect(self.save)
        self.revert_button.clicked.connect(self.reload)
        self.close_button.clicked.connect(self.close)
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.revert_button)
        buttons.addWidget(self.close_button)

        self._refresh_tables()

    def _build_vocabulary_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("Preferred spellings apply to dictation and command mode."))
        self.vocab_table = QTableWidget(0, 3)
        self.vocab_table.setHorizontalHeaderLabels(["Enabled", "Spoken", "Written"])
        layout.addWidget(self.vocab_table)

        editor = QHBoxLayout()
        layout.addLayout(editor)
        self.vocab_enabled_input = QCheckBox("Enabled")
        self.vocab_enabled_input.setChecked(True)
        self.vocab_spoken_input = QLineEdit()
        self.vocab_spoken_input.setPlaceholderText("gabby")
        self.vocab_written_input = QLineEdit()
        self.vocab_written_input.setPlaceholderText("Gabbee")
        self.add_vocabulary_button = QPushButton("Add")
        self.delete_vocabulary_button = QPushButton("Delete selected")
        self.add_vocabulary_button.clicked.connect(self.add_vocabulary_entry)
        self.delete_vocabulary_button.clicked.connect(self.delete_selected_vocabulary)
        editor.addWidget(self.vocab_enabled_input)
        editor.addWidget(self.vocab_spoken_input)
        editor.addWidget(self.vocab_written_input)
        editor.addWidget(self.add_vocabulary_button)
        editor.addWidget(self.delete_vocabulary_button)
        return page

    def _build_commands_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("Custom commands only run from the secondary command shortcut."))
        self.command_table = QTableWidget(0, 4)
        self.command_table.setHorizontalHeaderLabels(["Enabled", "Spoken", "Action", "Payload"])
        layout.addWidget(self.command_table)

        editor = QHBoxLayout()
        layout.addLayout(editor)
        self.command_enabled_input = QCheckBox("Enabled")
        self.command_enabled_input.setChecked(True)
        self.command_spoken_input = QLineEdit()
        self.command_spoken_input.setPlaceholderText("run tests")
        self.command_action_type = QComboBox()
        self.command_action_type.addItems(["Type text", "Type CLI command", "Press key combo"])
        self.command_payload_input = QLineEdit()
        self.command_payload_input.setPlaceholderText("pytest")
        self.add_command_button = QPushButton("Add")
        self.delete_command_button = QPushButton("Delete selected")
        self.add_command_button.clicked.connect(self.add_command_entry)
        self.delete_command_button.clicked.connect(self.delete_selected_command)
        editor.addWidget(self.command_enabled_input)
        editor.addWidget(self.command_spoken_input)
        editor.addWidget(self.command_action_type)
        editor.addWidget(self.command_payload_input)
        editor.addWidget(self.add_command_button)
        editor.addWidget(self.delete_command_button)
        return page

    def _build_preview_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("Preview does not type text or press keys."))
        self.preview_mode = QComboBox()
        self.preview_mode.addItems(["Dictation", "Command"])
        self.preview_input = QLineEdit()
        self.preview_input.setPlaceholderText("Transcript to preview")
        self.preview_button = QPushButton("Preview")
        self.preview_output = QTextEdit()
        self.preview_output.setReadOnly(True)
        self.preview_button.clicked.connect(self.update_preview)
        self.preview_input.textChanged.connect(self.update_preview)
        self.preview_mode.currentTextChanged.connect(self.update_preview)
        layout.addWidget(self.preview_mode)
        layout.addWidget(self.preview_input)
        layout.addWidget(self.preview_button)
        layout.addWidget(self.preview_output)
        return page

    def _action_type_from_label(self, label: str) -> str:
        return {
            "Type text": "type_text",
            "Type CLI command": "type_cli",
            "Press key combo": "press_key",
        }[label]

    def _action_label(self, action_type: str) -> str:
        return {
            "type_text": "Type text",
            "type_cli": "Type CLI command",
            "press_key": "Press key combo",
        }.get(action_type, action_type)

    def add_vocabulary_entry(self) -> None:
        spoken = self.vocab_spoken_input.text().strip()
        written = self.vocab_written_input.text().strip()
        if not spoken or not written:
            return
        self.advanced_config.vocabulary.append(
            VocabularyEntry(spoken=spoken, written=written, enabled=self.vocab_enabled_input.isChecked())
        )
        self.vocab_spoken_input.clear()
        self.vocab_written_input.clear()
        self._refresh_tables()
        self.update_preview()

    def delete_selected_vocabulary(self) -> None:
        row = self.vocab_table.currentRow()
        if row >= 0:
            del self.advanced_config.vocabulary[row]
            self._refresh_tables()
            self.update_preview()

    def add_command_entry(self) -> None:
        spoken = self.command_spoken_input.text().strip()
        payload = self.command_payload_input.text().strip()
        action_type = self._action_type_from_label(self.command_action_type.currentText())
        if not spoken or not payload:
            return
        action = CommandAction(type=action_type, key=payload if action_type == "press_key" else "", text=payload if action_type != "press_key" else "")
        self.advanced_config.commands.append(
            CommandEntry(spoken=spoken, action=action, enabled=self.command_enabled_input.isChecked())
        )
        self.command_spoken_input.clear()
        self.command_payload_input.clear()
        self._refresh_tables()
        self.update_preview()

    def delete_selected_command(self) -> None:
        row = self.command_table.currentRow()
        if row >= 0:
            del self.advanced_config.commands[row]
            self._refresh_tables()
            self.update_preview()

    def _refresh_tables(self) -> None:
        self.vocab_table.setRowCount(len(self.advanced_config.vocabulary))
        for row, entry in enumerate(self.advanced_config.vocabulary):
            self.vocab_table.setItem(row, 0, QTableWidgetItem("yes" if entry.enabled else "no"))
            self.vocab_table.setItem(row, 1, QTableWidgetItem(entry.spoken))
            self.vocab_table.setItem(row, 2, QTableWidgetItem(entry.written))

        self.command_table.setRowCount(len(self.advanced_config.commands))
        for row, entry in enumerate(self.advanced_config.commands):
            payload = entry.action.key if entry.action.type == "press_key" else entry.action.text
            self.command_table.setItem(row, 0, QTableWidgetItem("yes" if entry.enabled else "no"))
            self.command_table.setItem(row, 1, QTableWidgetItem(entry.spoken))
            self.command_table.setItem(row, 2, QTableWidgetItem(self._action_label(entry.action.type)))
            self.command_table.setItem(row, 3, QTableWidgetItem(payload))

    def update_preview(self) -> None:
        processor = TextProcessor(advanced_config=self.advanced_config)
        actions = processor.process_to_actions(
            self.preview_input.text(),
            enable_commands=self.preview_mode.currentText() == "Command",
        )
        lines = [f"{action_type}: {value}" for action_type, value in actions]
        self.preview_output.setPlainText("\n".join(lines))

    def save(self) -> None:
        save_advanced_config(self.config_path, self.advanced_config)

    def reload(self) -> None:
        self.advanced_config = load_advanced_config(self.config_path)
        self._refresh_tables()
        self.update_preview()
```

- [ ] **Step 4: Run UI tests**

Run:

```bash
python -m pytest tests/test_command_studio.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit Task 4 if commits are requested**

```bash
git add src/gabbee/ui/command_studio.py tests/test_command_studio.py
git commit -m "Add Command Studio editor"
```

---

### Task 5: Wire Command Studio into configuration and tray UI

**Files:**
- Modify: `src/gabbee/ui/config_window.py`
- Modify: `src/gabbee/ui/tray.py`
- Modify: `src/gabbee/main_bar.py`
- Modify: `tests/test_tray.py`
- Create or modify: `tests/test_config_window.py`

- [ ] **Step 1: Write failing tray test**

Add to `tests/test_tray.py`:

```python
    def test_tray_has_command_studio_action(self) -> None:
        parent = QWidget()
        tray = GabbeeTrayIcon(QIcon(), parent)

        self.assertEqual(tray.command_studio_action.text(), "Command Studio...")
```

- [ ] **Step 2: Write failing config window test**

Create `tests/test_config_window.py`:

```python
from __future__ import annotations

from pathlib import Path
import os
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PyQt6.QtWidgets import QApplication

from gabbee.app_paths import AppPaths
from gabbee.config import AppConfig
from gabbee.ui.config_window import ConfigWindow


def make_config(root: Path) -> AppConfig:
    return AppConfig(
        paths=AppPaths(root / "config", root / "state", root / "cache", root / "runtime"),
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


class ConfigWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_has_command_studio_button(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            window = ConfigWindow(make_config(Path(tmp)))

            self.assertEqual(window.command_studio_button.text(), "Command Studio...")
            window.close()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run tests to verify they fail**

Run:

```bash
python -m pytest tests/test_tray.py tests/test_config_window.py -v
```

Expected: FAIL because `command_studio_action` and `command_studio_button` do not exist.

- [ ] **Step 4: Add UI entry points**

Modify `src/gabbee/ui/tray.py` after `config_action`:

```python
        self.command_studio_action = QAction("Command Studio...", self)
        self.menu.addAction(self.command_studio_action)
```

Modify `src/gabbee/ui/config_window.py`:

- import `pyqtSignal`:

```python
from PyQt6.QtCore import Qt, pyqtSignal
```

- add class signal:

```python
class ConfigWindow(QDialog):
    command_studio_requested = pyqtSignal()
```

- add button before Save/Cancel buttons:

```python
        self.command_studio_button = QPushButton("Command Studio...")
        self.command_studio_button.clicked.connect(self.command_studio_requested.emit)
        layout.addWidget(self.command_studio_button)
```

- [ ] **Step 5: Wire main_bar to open Command Studio and reload processor after saves**

Modify `src/gabbee/main_bar.py` imports:

```python
from .ui.command_studio import CommandStudioWindow
```

Add this function near `show_config()`:

```python
    def show_command_studio():
        dialog = CommandStudioWindow(config.paths.advanced_config_file, window)
        dialog.exec()
        controller.reload_transcriber()
```

Update `show_config()` after creating the config dialog:

```python
        diag.command_studio_requested.connect(show_command_studio)
```

Wire tray action:

```python
    tray.command_studio_action.triggered.connect(show_command_studio)
```

- [ ] **Step 6: Run entry-point tests**

Run:

```bash
python -m pytest tests/test_tray.py tests/test_config_window.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit Task 5 if commits are requested**

```bash
git add src/gabbee/ui/config_window.py src/gabbee/ui/tray.py src/gabbee/main_bar.py tests/test_tray.py tests/test_config_window.py
git commit -m "Wire Command Studio entry points"
```

---

### Task 6: Update docs and run validation

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update README**

Add a short section under the existing command shortcut usage text:

```markdown
### Command Studio

Open Command Studio from the tray menu or the Configuration window to manage advanced spoken behavior.

Command Studio supports:

- custom vocabulary such as `gabby` -> `Gabbee` or project/library names
- custom command-mode phrases such as `run tests` -> `pytest`
- CLI command text that is typed into the focused terminal without pressing Enter
- previewing dictation and command-mode output before saving behavior

Primary dictation remains text-only. Custom commands only run from the secondary command shortcut.
```

Update `## Next steps` to keep future-only items:

```markdown
- Add macro recording and editable multi-step command chains.
- Add per-app or per-project command profiles.
- Add import/export for vocabulary and commands.
```

- [ ] **Step 2: Run focused tests**

Run:

```bash
python -m pytest tests/test_advanced_config.py tests/test_text_processor.py tests/test_command_studio.py tests/test_tray.py tests/test_config_window.py -v
```

Expected: PASS.

- [ ] **Step 3: Run broader non-IBus tests if local IBus GI bindings are unavailable**

Run:

```bash
python -m pytest tests/test_advanced_config.py tests/test_config.py tests/test_controller.py tests/test_text_processor.py tests/test_ui_bar.py tests/test_tray.py tests/test_command_studio.py tests/test_config_window.py -v
```

Expected: PASS unless an existing unrelated environment dependency fails.

- [ ] **Step 4: Run full suite**

Run:

```bash
python -m pytest
```

Expected in a fully provisioned desktop environment: PASS. On this machine, full collection may fail with `ValueError: Namespace IBus not available`; if so, report that as an environment blocker and include the focused test results.

- [ ] **Step 5: Run lint if available**

Run:

```bash
python -m ruff check
```

Expected in a fully provisioned dev environment: PASS. On this machine, this may fail with `No module named ruff`; if so, report that `ruff` is not installed.

- [ ] **Step 6: Manually test UI if display access is available**

Manual checklist:

1. Start Gabbee from the project venv.
2. Open tray menu.
3. Open `Command Studio...`.
4. Add vocabulary `gabby` -> `Gabbee`.
5. Preview dictation text `open gabby` and confirm `open Gabbee`.
6. Add command `run tests` as `Type CLI command` with payload `pytest`.
7. Preview `run tests` in Dictation mode and confirm it remains literal text.
8. Preview `run tests` in Command mode and confirm it becomes `pytest`.
9. Save and close.
10. Use the secondary command shortcut and confirm the command text is typed without Enter.

If the session is non-interactive/offscreen, state that manual UI testing could not be completed.

- [ ] **Step 7: Commit Task 6 if commits are requested**

```bash
git add README.md
git commit -m "Document Command Studio"
```

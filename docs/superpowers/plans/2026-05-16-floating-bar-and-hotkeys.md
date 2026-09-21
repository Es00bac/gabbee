# Floating Bar Visibility and Live Hotkey Rebinding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add hide/show controls for the floating bar and make runtime shortcut changes, including `F23`, take effect without restarting Gabbee.

**Architecture:** Keep the existing `FloatingBar`, `GabbeeTrayIcon`, and `main_bar.py` boundaries. `FloatingBar` owns bar UI visibility and shortcut rebinding; `GabbeeTrayIcon` exposes show/hide actions; `main_bar.py` wires saved config updates into the live bar.

**Tech Stack:** Python, PyQt6, Qt widgets/events, xdg-desktop-portal global shortcuts over DBus, unittest/pytest.

---

## File structure

- Modify `src/gabbee/ui/bar.py`
  - Add a `Hide` button.
  - Add `show_bar()` and `hide_bar()` methods.
  - Prevent pinned refresh from showing a deliberately hidden bar.
  - Add `apply_shortcuts()` for runtime shortcut rebinding.
  - Pass distinct shortcut ids/descriptions to portal bindings.
- Modify `src/gabbee/ui/tray.py`
  - Add `Hide Bar` tray action.
  - Keep left-click mapped to showing the bar.
- Modify `src/gabbee/ui/global_shortcuts.py`
  - Replace module-level shared shortcut id usage with instance-specific id/description.
- Modify `src/gabbee/main_bar.py`
  - Wire tray show/hide actions to the bar methods.
  - Call `window.apply_shortcuts()` after saving config updates.
- Modify `tests/test_ui_bar.py`
  - Cover hide behavior, pinned refresh behavior, runtime rebinding, F23 local fallback, and distinct portal metadata.
- Create `tests/test_tray.py`
  - Cover tray hide/show actions and left-click show behavior.
- Modify `README.md`
  - Document hiding/restoring the bar and runtime shortcut changes.
  - Update Next steps with custom dictionaries and spoken CLI command hotkey.

---

### Task 1: Add bar hide/show behavior

**Files:**
- Modify: `src/gabbee/ui/bar.py`
- Test: `tests/test_ui_bar.py`

- [ ] **Step 1: Write failing tests for hide button and pinned refresh**

Add these methods to `FloatingBarTests` in `tests/test_ui_bar.py`:

```python
def test_hide_button_hides_bar_without_closing(self) -> None:
    controller = FakeController()
    window = FloatingBar(
        self.app,
        controller,
        toggle_shortcut="F5",
        command_shortcut="F6",
        global_shortcut_factory=lambda **kwargs: FakeShortcutBinding(registered=True, **kwargs),
    )
    window.show()
    self.app.processEvents()

    window.hide_button.click()
    self.app.processEvents()

    self.assertFalse(window.isVisible())
    self.assertEqual(controller.started, 0)
    window.close()


def test_pinned_refresh_does_not_show_hidden_bar(self) -> None:
    controller = FakeController()
    window = FloatingBar(
        self.app,
        controller,
        toggle_shortcut="F5",
        command_shortcut="F6",
        global_shortcut_factory=lambda **kwargs: FakeShortcutBinding(registered=True, **kwargs),
    )
    window.show()
    self.app.processEvents()

    window.hide_bar()
    window._refresh_pin_state()
    self.app.processEvents()

    self.assertFalse(window.isVisible())
    window.close()


def test_show_bar_restores_and_raises_bar(self) -> None:
    controller = FakeController()
    window = FloatingBar(
        self.app,
        controller,
        toggle_shortcut="F5",
        command_shortcut="F6",
        global_shortcut_factory=lambda **kwargs: FakeShortcutBinding(registered=True, **kwargs),
    )
    window.hide_bar()

    window.show_bar()
    self.app.processEvents()

    self.assertTrue(window.isVisible())
    window.close()
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
pytest tests/test_ui_bar.py::FloatingBarTests::test_hide_button_hides_bar_without_closing tests/test_ui_bar.py::FloatingBarTests::test_pinned_refresh_does_not_show_hidden_bar tests/test_ui_bar.py::FloatingBarTests::test_show_bar_restores_and_raises_bar -v
```

Expected: FAIL because `hide_button`, `hide_bar()`, and `show_bar()` do not exist.

- [ ] **Step 3: Implement hide/show methods and button**

In `src/gabbee/ui/bar.py`, after the pin button setup, add:

```python
        self.hide_button = QPushButton("Hide")
        self.hide_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.hide_button.clicked.connect(self.hide_bar)
        header.addWidget(self.hide_button)
```

Add these methods near `_set_pinned()`:

```python
    def show_bar(self) -> None:
        self.show()
        self.raise_()
        handle = self.windowHandle()
        if handle is not None:
            handle.raise_()

    def hide_bar(self) -> None:
        self.hide()
```

Change `_set_pinned()` from:

```python
        self.show()
```

to:

```python
        if self.isVisible():
            self.show_bar()
```

Change `_refresh_pin_state()` from:

```python
        if not self._pinned:
            return
        self.show()
        self.raise_()
        handle = self.windowHandle()
        if handle is not None:
            handle.raise_()
```

to:

```python
        if not self._pinned or not self.isVisible():
            return
        self.show_bar()
```

- [ ] **Step 4: Run tests to verify pass**

Run:

```bash
pytest tests/test_ui_bar.py::FloatingBarTests::test_hide_button_hides_bar_without_closing tests/test_ui_bar.py::FloatingBarTests::test_pinned_refresh_does_not_show_hidden_bar tests/test_ui_bar.py::FloatingBarTests::test_show_bar_restores_and_raises_bar -v
```

Expected: PASS.

- [ ] **Step 5: Commit if user requested commits**

Only commit if the user explicitly asked for commits. Otherwise leave changes uncommitted.

---

### Task 2: Add tray hide action and show wiring

**Files:**
- Modify: `src/gabbee/ui/tray.py`
- Modify: `src/gabbee/main_bar.py`
- Test: `tests/test_tray.py`

- [ ] **Step 1: Write failing tray tests**

Create `tests/test_tray.py`:

```python
from __future__ import annotations

from pathlib import Path
import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QSystemTrayIcon, QWidget

from gabbee.ui.tray import GabbeeTrayIcon


class TrayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_tray_has_show_and_hide_actions(self) -> None:
        parent = QWidget()
        tray = GabbeeTrayIcon(QIcon(), parent)

        self.assertEqual(tray.show_bar_action.text(), "Show Bar")
        self.assertEqual(tray.hide_bar_action.text(), "Hide Bar")

    def test_left_click_triggers_show_action(self) -> None:
        parent = QWidget()
        tray = GabbeeTrayIcon(QIcon(), parent)
        triggered = []
        tray.show_bar_action.triggered.connect(lambda: triggered.append(True))

        tray._on_activated(QSystemTrayIcon.ActivationReason.Trigger)

        self.assertEqual(triggered, [True])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tray tests to verify failure**

Run:

```bash
pytest tests/test_tray.py -v
```

Expected: FAIL because `hide_bar_action` does not exist.

- [ ] **Step 3: Add tray hide action**

In `src/gabbee/ui/tray.py`, add the hide action after the show action:

```python
        self.show_bar_action = QAction("Show Bar", self)
        self.menu.addAction(self.show_bar_action)

        self.hide_bar_action = QAction("Hide Bar", self)
        self.menu.addAction(self.hide_bar_action)
```

- [ ] **Step 4: Wire tray actions in main**

In `src/gabbee/main_bar.py`, change:

```python
    tray.show_bar_action.triggered.connect(window.show)
```

to:

```python
    tray.show_bar_action.triggered.connect(window.show_bar)
    tray.hide_bar_action.triggered.connect(window.hide_bar)
```

- [ ] **Step 5: Run tray tests**

Run:

```bash
pytest tests/test_tray.py -v
```

Expected: PASS.

---

### Task 3: Make portal shortcut identities distinct

**Files:**
- Modify: `src/gabbee/ui/global_shortcuts.py`
- Modify: `src/gabbee/ui/bar.py`
- Test: `tests/test_ui_bar.py`

- [ ] **Step 1: Update fake binding to record metadata**

Change `FakeShortcutBinding.__init__` in `tests/test_ui_bar.py` to:

```python
    def __init__(
        self,
        shortcut_text,
        on_pressed,
        on_released,
        on_status_change,
        registered=True,
        shortcut_id="push_to_talk",
        description="Gabbee push to talk",
    ) -> None:
        self.shortcut_text = shortcut_text
        self.shortcut_id = shortcut_id
        self.description = description
        self.on_pressed = on_pressed
        self.on_released = on_released
        self.on_status_change = on_status_change
        self.registered = registered
        self.closed = False
```

- [ ] **Step 2: Write failing test for distinct portal metadata**

Add this test to `FloatingBarTests`:

```python
def test_global_shortcuts_use_distinct_ids_and_descriptions(self) -> None:
    controller = FakeController()
    bindings = []

    def factory(**kwargs):
        binding = FakeShortcutBinding(registered=True, **kwargs)
        bindings.append(binding)
        return binding

    window = FloatingBar(self.app, controller, toggle_shortcut="F5", command_shortcut="F6", global_shortcut_factory=factory)

    self.assertEqual(bindings[0].shortcut_id, "dictation_push_to_talk")
    self.assertEqual(bindings[0].description, "Gabbee dictation push to talk")
    self.assertEqual(bindings[1].shortcut_id, "command_push_to_talk")
    self.assertEqual(bindings[1].description, "Gabbee command push to talk")
    window.close()
```

- [ ] **Step 3: Run test to verify failure**

Run:

```bash
pytest tests/test_ui_bar.py::FloatingBarTests::test_global_shortcuts_use_distinct_ids_and_descriptions -v
```

Expected: FAIL because `FloatingBar` does not pass metadata yet.

- [ ] **Step 4: Update portal binding constructor and comparisons**

In `src/gabbee/ui/global_shortcuts.py`, remove:

```python
SHORTCUT_ID = "push_to_talk"
```

Change the constructor to:

```python
    def __init__(
        self,
        shortcut_text: str,
        on_pressed: Callable[[], None],
        on_released: Callable[[], None],
        on_status_change: Callable[[bool, str], None],
        shortcut_id: str = "push_to_talk",
        description: str = "Gabbee push to talk",
    ) -> None:
        super().__init__()
        self.shortcut_text = shortcut_text
        self.shortcut_id = shortcut_id
        self.description = description
```

Keep the existing signal connections after those assignments.

Change the shortcut struct in `_request_binding()` to:

```python
                    self.shortcut_id,
                    dbus.Dictionary(
                        {
                            "description": self.description,
                            "preferred_trigger": self.shortcut_text,
                        },
                        signature="sv",
                    ),
```

Change both activation/deactivation guards from `shortcut_id != SHORTCUT_ID` to:

```python
shortcut_id != self.shortcut_id
```

- [ ] **Step 5: Pass distinct metadata from FloatingBar**

In `src/gabbee/ui/bar.py`, change the dictation binding factory call to include:

```python
                shortcut_id="dictation_push_to_talk",
                description="Gabbee dictation push to talk",
```

Change the command binding factory call to include:

```python
                shortcut_id="command_push_to_talk",
                description="Gabbee command push to talk",
```

- [ ] **Step 6: Run test to verify pass**

Run:

```bash
pytest tests/test_ui_bar.py::FloatingBarTests::test_global_shortcuts_use_distinct_ids_and_descriptions -v
```

Expected: PASS.

---

### Task 4: Add live shortcut rebinding

**Files:**
- Modify: `src/gabbee/ui/bar.py`
- Modify: `src/gabbee/main_bar.py`
- Test: `tests/test_ui_bar.py`

- [ ] **Step 1: Write failing tests for runtime rebinding and F23**

Add these tests to `FloatingBarTests`:

```python
def test_apply_shortcuts_closes_old_bindings_and_creates_new_ones(self) -> None:
    controller = FakeController()
    bindings = []

    def factory(**kwargs):
        binding = FakeShortcutBinding(registered=True, **kwargs)
        bindings.append(binding)
        return binding

    window = FloatingBar(self.app, controller, toggle_shortcut="F5", command_shortcut="F6", global_shortcut_factory=factory)

    window.apply_shortcuts("F7", "F23")

    self.assertTrue(bindings[0].closed)
    self.assertTrue(bindings[1].closed)
    self.assertEqual(window.shortcut_sequence.toString(), "F7")
    self.assertEqual(window.command_shortcut_sequence.toString(), "F23")
    self.assertEqual(bindings[2].shortcut_text, "F7")
    self.assertEqual(bindings[3].shortcut_text, "F23")
    window.close()


def test_apply_shortcuts_updates_f23_command_local_fallback(self) -> None:
    controller = FakeController()
    window = FloatingBar(
        self.app,
        controller,
        toggle_shortcut="F5",
        command_shortcut="F6",
        global_shortcut_factory=lambda **kwargs: FakeShortcutBinding(registered=False, **kwargs),
    )

    window.apply_shortcuts("F5", "F23")
    press = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_F23, Qt.KeyboardModifier.NoModifier)
    release = QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_F23, Qt.KeyboardModifier.NoModifier)

    self.assertTrue(window.eventFilter(self.app, press))
    self.assertEqual(controller.command_started, 1)
    self.assertTrue(window.eventFilter(self.app, release))
    self.assertEqual(controller.stopped, 1)
    window.close()
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
pytest tests/test_ui_bar.py::FloatingBarTests::test_apply_shortcuts_closes_old_bindings_and_creates_new_ones tests/test_ui_bar.py::FloatingBarTests::test_apply_shortcuts_updates_f23_command_local_fallback -v
```

Expected: FAIL because `apply_shortcuts()` does not exist.

- [ ] **Step 3: Store the shortcut factory on FloatingBar**

In `src/gabbee/ui/bar.py`, add this assignment in `__init__` after `_global_command_shortcut` is initialized:

```python
        self._global_shortcut_factory = global_shortcut_factory
```

Change the setup calls at the end of `__init__` from:

```python
        self._setup_shortcut_binding(global_shortcut_factory)
        self._setup_command_shortcut_binding(global_shortcut_factory)
```

to:

```python
        self._setup_shortcut_binding()
        self._setup_command_shortcut_binding()
```

Change both setup method signatures to take no factory argument:

```python
    def _setup_shortcut_binding(self) -> None:
        global_shortcut_factory = self._global_shortcut_factory
```

```python
    def _setup_command_shortcut_binding(self) -> None:
        global_shortcut_factory = self._global_shortcut_factory
```

- [ ] **Step 4: Add close helper and apply_shortcuts**

Add these methods before `_setup_shortcut_binding()`:

```python
    def _close_shortcut_bindings(self) -> None:
        closer = getattr(self._global_shortcut, "close", None)
        if callable(closer):
            closer()
        command_closer = getattr(self._global_command_shortcut, "close", None)
        if callable(command_closer):
            command_closer()
        self._global_shortcut = None
        self._global_command_shortcut = None

    def apply_shortcuts(self, toggle_shortcut: str, command_shortcut: str) -> None:
        self._close_shortcut_bindings()
        self.toggle_shortcut_text = toggle_shortcut.strip() or "F5"
        self.command_shortcut_text = command_shortcut.strip()
        self.shortcut_sequence = QKeySequence(self.toggle_shortcut_text)
        self.command_shortcut_sequence = QKeySequence(self.command_shortcut_text)
        self._shortcut_pressed = False
        self._command_shortcut_pressed = False
        self._use_local_shortcut = True
        self._use_local_command_shortcut = bool(self.command_shortcut_text)
        self._render_shortcut_hint()
        self._setup_shortcut_binding()
        self._setup_command_shortcut_binding()
```

Update `closeEvent()` to use the helper:

```python
    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.app.removeEventFilter(self)
        self._close_shortcut_bindings()
        super().closeEvent(event)
```

- [ ] **Step 5: Call apply_shortcuts after config save**

In `src/gabbee/main_bar.py`, change `show_config()` to:

```python
    def show_config():
        diag = ConfigWindow(config, window)
        if diag.exec():
            updates = diag.get_config_dict()
            config.save(updates)
            controller.reload_transcriber()
            window.apply_shortcuts(config.toggle_shortcut, config.command_shortcut)
```

- [ ] **Step 6: Run shortcut rebinding tests**

Run:

```bash
pytest tests/test_ui_bar.py::FloatingBarTests::test_apply_shortcuts_closes_old_bindings_and_creates_new_ones tests/test_ui_bar.py::FloatingBarTests::test_apply_shortcuts_updates_f23_command_local_fallback -v
```

Expected: PASS.

---

### Task 5: Update README usage and future features

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update Usage section**

In `README.md`, replace the Usage block at lines around `234-247` with:

```markdown
Usage:

1. Focus a text field.
2. If you want explicit IBus commit behavior, switch your current input method to `Gabbee Voice Input`.
3. Click `Start`, hold the dictation shortcut, or use the tray to show the bar if it is hidden.
4. Speak.
5. Click `Stop` or release the shortcut.
6. Gabbee will transcribe and try to type into the active window, use IBus when available, and mirror successful output to the clipboard.

Use `Hide` on the floating bar, or `Hide Bar` from the tray menu, to remove the bar from the screen without quitting Gabbee. Restore it from the tray with `Show Bar` or a tray left-click.

Normal dictation is text-only. Words such as `delete`, `undo`, and `copy that` are typed as words when you use `Start` or the dictation shortcut.

Use the command shortcut only when you want spoken keywords to perform actions such as delete, undo, copy, paste, or navigation. The default command shortcut is `F6`, configurable through `GABBEE_COMMAND_SHORTCUT` or the Configuration window. Saved shortcut changes apply to the running bar without restarting Gabbee.

On KDE Plasma Wayland, the first run may show desktop portal prompts to approve the global `F5` dictation shortcut and `F6` command shortcut. If you do not approve them, they still work while the Gabbee window is focused.
```

- [ ] **Step 2: Update Next steps**

Replace the `## Next steps` list with:

```markdown
## Next steps

- Add shortcut capture and validation so users can press a key combination instead of typing shortcut text.
- Add custom words and dictionaries for names, project names, libraries, and domain vocabulary.
- Add a separate bindable hotkey for spoken CLI command entry that works with any focused terminal.
- Add optional auto-hide behavior for low-clutter workflows.
- Add recording/provider/output diagnostics for easier troubleshooting.
- Add preedit and streaming partial transcript support.
- Add a guided first-run setup flow for IBus and desktop integration.
```

- [ ] **Step 3: No README-only test required**

Run no tests for this documentation-only step. Verify the markdown renders as normal text and fenced blocks remain balanced.

---

### Task 6: Run focused and full validation

**Files:**
- Validate modified code and docs.

- [ ] **Step 1: Run focused tests**

Run:

```bash
pytest tests/test_ui_bar.py tests/test_tray.py -v
```

Expected: PASS.

- [ ] **Step 2: Run full test suite**

Run:

```bash
pytest
```

Expected: PASS.

- [ ] **Step 3: Run lint**

Run:

```bash
ruff check
```

Expected: PASS.

If `ruff` is not available in the environment, record the exact command failure and do not mark lint as passed.

- [ ] **Step 4: Manual UI validation**

Run:

```bash
source /home/cabewse/gabbee/.venv/bin/activate
gabbee-bar
```

Manual checks:

1. The bar shows a `Hide` button.
2. Clicking `Hide` hides the bar and leaves the tray icon running.
3. Tray `Show Bar` restores the bar.
4. Tray `Hide Bar` hides the bar.
5. Left-clicking the tray icon restores the bar.
6. Changing command shortcut to `F23` in Configuration updates the running hint text.
7. Approving the portal prompt allows `F23` to start command-mode recording globally.
8. Releasing `F23` stops recording.
9. Dictation shortcut still works.

If the UI cannot be manually tested in the current environment, state that explicitly in the final response.

---

## Self-review

- Spec coverage: hide/show controls are covered by Tasks 1-2; live shortcut rebinding and F23 are covered by Task 4; distinct portal identities are covered by Task 3; README/backlog updates are covered by Task 5; validation is covered by Task 6.
- Placeholder scan: no TBD/TODO placeholders remain; each code-changing step includes exact code or replacement text.
- Type consistency: the plan consistently uses `show_bar()`, `hide_bar()`, `apply_shortcuts()`, `_global_shortcut_factory`, `shortcut_id`, and `description`.

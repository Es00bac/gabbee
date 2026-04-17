from __future__ import annotations

from pathlib import Path
import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication

from gabbee.models import ControllerSnapshot, ControllerState
from gabbee.ui.bar import FloatingBar


class FakeController:
    def __init__(self) -> None:
        self.started = 0
        self.stopped = 0
        self.cancelled = 0
        self._listener = None

    def add_listener(self, listener) -> None:
        self._listener = listener
        listener(
            ControllerSnapshot(
                state=ControllerState.IDLE,
                provider="elevenlabs",
                delivery_method="FallbackTextSink",
            )
        )

    def start(self) -> None:
        self.started += 1

    def stop(self) -> None:
        self.stopped += 1

    def cancel(self) -> None:
        self.cancelled += 1


class FakeShortcutBinding:
    def __init__(self, shortcut_text, on_pressed, on_released, on_status_change, registered=True) -> None:
        self.shortcut_text = shortcut_text
        self.on_pressed = on_pressed
        self.on_released = on_released
        self.on_status_change = on_status_change
        self.registered = registered
        self.closed = False

    def start(self) -> None:
        if self.registered:
            self.on_status_change(True, f"Hold {self.shortcut_text} anywhere to talk.")
        else:
            self.on_status_change(False, f"Focus Gabbee to use {self.shortcut_text} locally.")

    def close(self) -> None:
        self.closed = True


class FloatingBarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_uses_configured_ptt_shortcut(self) -> None:
        controller = FakeController()
        window = FloatingBar(
            self.app,
            controller,
            toggle_shortcut="F5",
            global_shortcut_factory=lambda **kwargs: FakeShortcutBinding(registered=False, **kwargs),
        )
        self.assertEqual(window.shortcut_sequence.toString(), "F5")
        self.assertIn("F5", window.hint_label.text())
        press = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_F5, Qt.KeyboardModifier.NoModifier)
        release = QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_F5, Qt.KeyboardModifier.NoModifier)
        self.assertTrue(window.eventFilter(self.app, press))
        self.assertEqual(controller.started, 1)
        self.assertTrue(window.eventFilter(self.app, release))
        self.assertEqual(controller.stopped, 1)
        window.close()

    def test_global_shortcut_binding_disables_local_fallback(self) -> None:
        controller = FakeController()
        binding_holder = {}

        def factory(**kwargs):
            binding = FakeShortcutBinding(registered=True, **kwargs)
            binding_holder["binding"] = binding
            return binding

        window = FloatingBar(self.app, controller, toggle_shortcut="F5", global_shortcut_factory=factory)
        self.assertIn("anywhere", window.hint_label.text())
        press = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_F5, Qt.KeyboardModifier.NoModifier)
        release = QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_F5, Qt.KeyboardModifier.NoModifier)
        self.assertFalse(window.eventFilter(self.app, press))
        self.assertEqual(controller.started, 0)
        binding_holder["binding"].on_pressed()
        binding_holder["binding"].on_released()
        self.assertEqual(controller.started, 1)
        self.assertEqual(controller.stopped, 1)
        window.close()
        self.assertTrue(binding_holder["binding"].closed)

    def test_window_is_shown_without_taking_focus(self) -> None:
        controller = FakeController()
        window = FloatingBar(
            self.app,
            controller,
            toggle_shortcut="F5",
            global_shortcut_factory=lambda **kwargs: FakeShortcutBinding(registered=True, **kwargs),
        )
        self.assertTrue(window.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus)
        self.assertTrue(window.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating))
        self.assertEqual(window.start_button.focusPolicy(), Qt.FocusPolicy.NoFocus)
        self.assertEqual(window.stop_button.focusPolicy(), Qt.FocusPolicy.NoFocus)
        self.assertEqual(window.cancel_button.focusPolicy(), Qt.FocusPolicy.NoFocus)
        self.assertLessEqual(window.last_text_label.maximumWidth(), 320)
        self.assertTrue(window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        self.assertTrue(window.pin_button.isCheckable())
        self.assertTrue(window.pin_button.isChecked())
        window._set_pinned(False)
        self.assertFalse(window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        window.close()


if __name__ == "__main__":
    unittest.main()

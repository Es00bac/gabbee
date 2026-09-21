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

    def test_tray_has_command_studio_action(self) -> None:
        parent = QWidget()
        tray = GabbeeTrayIcon(QIcon(), parent)

        self.assertEqual(tray.command_studio_action.text(), "Command Studio...")


if __name__ == "__main__":
    unittest.main()

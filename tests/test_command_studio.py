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

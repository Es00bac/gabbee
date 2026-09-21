from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gabbee.advanced_config import (
    AdvancedConfig,
    CommandAction,
    CommandEntry,
    MacroEntry,
    MacroStep,
    ProfileEntry,
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

    def test_ignores_command_entries_without_required_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "advanced.json"
            path.write_text(
                """
                {
                  "commands": [
                    {"spoken": "empty text", "action": {"type": "type_text"}},
                    {"spoken": "empty cli", "action": {"type": "type_cli", "text": ""}},
                    {"spoken": "empty key", "action": {"type": "press_key"}},
                    {"spoken": "valid", "action": {"type": "type_cli", "text": "pytest"}}
                  ]
                }
                """,
                encoding="utf-8",
            )

            config = load_advanced_config(path)

            self.assertEqual(len(config.commands), 1)
            self.assertEqual(config.commands[0].spoken, "valid")

    def test_loads_macro_sequence_and_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "advanced.json"
            path.write_text(
                """
                {
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
                }
                """,
                encoding="utf-8",
            )

            config = load_advanced_config(path)

            self.assertEqual(config.macros[0].spoken, "run tests")
            self.assertEqual(config.macros[0].steps[0].type, "type_cli")
            self.assertEqual(config.macros[0].steps[1].seconds, 0.2)
            self.assertEqual(config.profiles[0].name, "default")
            self.assertEqual(config.profiles[0].macro_names, ["test macro"])

    def test_saves_macro_sequence_and_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "advanced.json"
            config = AdvancedConfig(
                macros=[
                    MacroEntry(
                        name="tests",
                        spoken="run tests",
                        steps=[MacroStep(type="type_cli", text="pytest"), MacroStep(type="wait", seconds=0.2)],
                    )
                ],
                profiles=[ProfileEntry(name="default", macro_names=["tests"])],
            )

            save_advanced_config(path, config)
            loaded = load_advanced_config(path)

            self.assertEqual(loaded.macros[0].name, "tests")
            self.assertEqual(loaded.macros[0].steps[0].text, "pytest")
            self.assertEqual(loaded.profiles[0].macro_names, ["tests"])

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

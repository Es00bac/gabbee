from __future__ import annotations

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gabbee.text_processor import TextProcessor


class TextProcessorTests(unittest.TestCase):
    def test_dictation_mode_keeps_editing_words_as_literal_text(self) -> None:
        processor = TextProcessor()

        actions = processor.process_to_actions("please delete copy that and undo this")

        self.assertEqual(actions, [("text", "please delete copy that and undo this")])

    def test_command_mode_keeps_existing_keyword_actions_available(self) -> None:
        processor = TextProcessor()

        actions = processor.process_to_actions("delete word", enable_commands=True)

        self.assertEqual(actions, [("key", "Control+BackSpace")])

    def test_text_normalization_still_runs_in_dictation_mode(self) -> None:
        processor = TextProcessor()

        actions = processor.process_to_actions("google dot com has forty two examples")

        self.assertEqual(actions, [("text", "google.com has 42 examples")])

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


if __name__ == "__main__":
    unittest.main()

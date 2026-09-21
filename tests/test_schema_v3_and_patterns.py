from __future__ import annotations

import json

import pytest

from gabbee.advanced_config import (
    AdvancedConfig,
    CommandAction,
    CommandEntry,
    ConfigValidationError,
    MacroEntry,
    MacroStep,
    ProfileEntry,
    SlotDefinition,
    import_advanced_config,
    load_advanced_config,
    save_advanced_config,
)
from gabbee.command_patterns import compile_command_pattern
from gabbee.macro_runtime import action_from_step
from gabbee.models import AppContext, ScrollAction
from gabbee.profiles import ProfileManager


@pytest.mark.parametrize(
    ("slot_type", "spoken", "expected"),
    [
        ("text", "hello brave world", "brave world"),
        ("integer", "hello twenty one", 21),
        ("digits", "hello zero zero seven", "007"),
        ("grouped_digits", "hello one two dash three four", "12-34"),
        ("phone", "hello three oh three five five five one two one two", "(303) 555-1212"),
        ("app", "hello Firefox", "Firefox"),
        ("window", "hello Project Notes", "Project Notes"),
        ("ui_target", "hello Save As", "Save As"),
    ],
)
def test_typed_command_slots(slot_type: str, spoken: str, expected: object) -> None:
    entry = CommandEntry(
        "hello {value:" + slot_type + "}",
        CommandAction(type="type_text", text="{value}"),
    )
    match = compile_command_pattern(entry).match(spoken)
    assert match is not None
    assert match.slots["value"] == expected
    assert match.render("value={value}") == f"value={expected}"


def test_choice_slot_is_explicit_and_case_insensitive() -> None:
    entry = CommandEntry(
        "choose {answer:choice}",
        CommandAction(type="type_text", text="{answer}"),
        slots=[SlotDefinition("answer", "choice", ["Save", "Cancel"])],
    )
    assert compile_command_pattern(entry).match("choose save").slots["answer"] == "Save"  # type: ignore[union-attr]
    assert compile_command_pattern(entry).match("choose maybe") is None


def test_numeric_macro_fields_render_from_integer_slots() -> None:
    macro = MacroEntry(
        "scroll",
        "scroll down {count:integer}",
        [MacroStep(type="scroll", direction="down", amount="{count}")],
    )
    match = compile_command_pattern(macro).match("scroll down three")
    assert match is not None
    action = action_from_step(macro.steps[0], match)
    assert action == ScrollAction("down", 3)


def test_v1_v2_migration_preserves_unknown_data_and_writes_v3(tmp_path) -> None:
    path = tmp_path / "advanced.json"
    path.write_text(
        json.dumps(
            {
                "metadata": {"version": 2, "owner": "test"},
                "future_root": {"keep": True},
                "commands": [
                    {
                        "spoken": "say hello",
                        "future_entry": 7,
                        "action": {"type": "type_text", "text": "hello", "future_action": "kept"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    config = load_advanced_config(path)
    assert config.metadata["version"] == 3
    assert config.metadata["migrated_from"] == 2
    save_advanced_config(path, config)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["future_root"] == {"keep": True}
    assert saved["commands"][0]["future_entry"] == 7
    assert saved["commands"][0]["action"]["future_action"] == "kept"


@pytest.mark.parametrize(
    "forbidden",
    ["shell", "command", "exec", "eval", "process", "run", "run_program", "subprocess", "script", "bash"],
)
def test_import_rejects_process_and_shell_escape_actions(forbidden: str) -> None:
    payload = json.dumps(
        {
            "metadata": {"version": 3},
            "macros": [
                {"name": "unsafe", "spoken": "unsafe", "steps": [{"type": forbidden, "text": "whoami"}]}
            ],
        }
    )
    with pytest.raises(ConfigValidationError):
        import_advanced_config(payload)


def test_profile_selection_priority_matcher_and_manual_override() -> None:
    manager = ProfileManager(
        AdvancedConfig(
            profiles=[
                ProfileEntry("global", priority=100),
                ProfileEntry("firefox", desktop_file_id="firefox.desktop", priority=100),
                ProfileEntry(
                    "gmail",
                    desktop_file_id="firefox",
                    window_title_pattern="Gmail",
                    priority=200,
                ),
            ]
        )
    )
    app = AppContext(desktop_file_id="firefox", title="Inbox — Gmail")
    assert manager.select(app).name == "gmail"  # type: ignore[union-attr]
    manager.set_manual_override("global")
    assert manager.select(app).name == "global"  # type: ignore[union-attr]
    manager.clear_manual_override()
    assert manager.select(app).name == "gmail"  # type: ignore[union-attr]

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from typing import Any


SCHEMA_VERSION = 3
SLOT_TYPES = {
    "text",
    "integer",
    "digits",
    "grouped_digits",
    "phone",
    "choice",
    "app",
    "window",
    "ui_target",
}
COMMAND_ACTION_TYPES = {
    "type_text",
    "type_cli",  # v1/v2 compatibility: this only types; it never executes.
    "press_key",
    "macro",
    "activate_app",
    "activate_window",
    "launch_desktop_entry",
    "invoke_ui",
    "move_pointer",
    "click",
    "right_click",
    "double_click",
    "scroll",
}
MACRO_STEP_TYPES = COMMAND_ACTION_TYPES | {
    "wait",
    "wait_for_target",
    "repeat",
    "target_exists",
}
FORBIDDEN_ACTION_TYPES = {
    "shell",
    "command",
    "exec",
    "eval",
    "process",
    "run",
    "run_program",
    "subprocess",
    "script",
}
_DESKTOP_ID_RE = re.compile(r"^[A-Za-z0-9_.+-]+(?:\.desktop)?$")
_SLOT_VALUE_RE = re.compile(r"^\{([A-Za-z_][A-Za-z0-9_]*)\}$")
_KEY_CHORD_RE = re.compile(r"^[A-Za-z0-9_+-]+$")


class ConfigValidationError(ValueError):
    pass


@dataclass(slots=True)
class VocabularyEntry:
    spoken: str
    written: str
    enabled: bool = True
    priority: int = 0
    keyterm: bool = True
    extra: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(slots=True)
class SlotDefinition:
    name: str
    type: str
    choices: list[str] = field(default_factory=list)
    required: bool = True
    extra: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(slots=True)
class CommandAction:
    type: str
    text: str = ""
    key: str = ""
    macro: str = ""
    target: str = ""
    app: str = ""
    window: str = ""
    desktop_file_id: str = ""
    action: str = ""
    direction: str = ""
    amount: int | str = 0
    extra: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(slots=True)
class CommandEntry:
    spoken: str
    action: CommandAction
    enabled: bool = True
    slots: list[SlotDefinition] = field(default_factory=list)
    profiles: list[str] = field(default_factory=list)
    description: str = ""
    extra: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(slots=True)
class MacroStep:
    type: str
    text: str = ""
    key: str = ""
    seconds: float | str = 0.0
    target: str = ""
    app: str = ""
    window: str = ""
    desktop_file_id: str = ""
    action: str = ""
    direction: str = ""
    amount: int | str = 0
    count: int | str = 0
    timeout: float | str = 0.0
    continue_on_error: bool = False
    steps: list["MacroStep"] = field(default_factory=list)
    else_steps: list["MacroStep"] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(slots=True)
class MacroEntry:
    name: str
    spoken: str
    steps: list[MacroStep]
    enabled: bool = True
    slots: list[SlotDefinition] = field(default_factory=list)
    profiles: list[str] = field(default_factory=list)
    description: str = ""
    extra: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(slots=True)
class ProfileEntry:
    name: str
    enabled: bool = True
    vocabulary_spoken: list[str] = field(default_factory=list)
    command_spoken: list[str] = field(default_factory=list)
    macro_names: list[str] = field(default_factory=list)
    desktop_file_id: str = ""
    window_title_pattern: str = ""
    priority: int = 0
    terminal_mode: bool = False
    code_mode: bool = False
    keyterms_enabled: bool = False
    entity_hints: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(slots=True)
class AdvancedConfig:
    vocabulary: list[VocabularyEntry] = field(default_factory=list)
    commands: list[CommandEntry] = field(default_factory=list)
    macros: list[MacroEntry] = field(default_factory=list)
    profiles: list[ProfileEntry] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=lambda: {"version": SCHEMA_VERSION})
    behavior: dict[str, Any] = field(default_factory=dict)
    templates: list[dict[str, Any]] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict, repr=False)
    load_error: str = ""


def _as_bool(value: object, default: bool = True) -> bool:
    return value if isinstance(value, bool) else default


def _as_str(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _as_str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _as_float(value: object) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            pass
    return 0.0


def _as_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, (float, str)):
        try:
            return int(value)
        except (TypeError, ValueError):
            pass
    return 0


def _as_int_or_slot(value: object) -> int | str:
    if isinstance(value, str) and _SLOT_VALUE_RE.fullmatch(value.strip()):
        return value.strip()
    return _as_int(value)


def _as_float_or_slot(value: object) -> float | str:
    if isinstance(value, str) and _SLOT_VALUE_RE.fullmatch(value.strip()):
        return value.strip()
    return _as_float(value)


def _positive_or_slot(value: int | float | str, maximum: float) -> bool:
    if isinstance(value, str):
        return _SLOT_VALUE_RE.fullmatch(value) is not None
    return 0 < value <= maximum


def _extra(item: dict[str, Any], known: set[str]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if key not in known}


def _load_slots(raw_items: object) -> list[SlotDefinition]:
    if not isinstance(raw_items, list):
        return []
    result: list[SlotDefinition] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        name = _as_str(item.get("name"))
        slot_type = _as_str(item.get("type"))
        if not name or slot_type not in SLOT_TYPES:
            continue
        known = {"name", "type", "choices", "required"}
        result.append(
            SlotDefinition(
                name=name,
                type=slot_type,
                choices=_as_str_list(item.get("choices")),
                required=_as_bool(item.get("required")),
                extra=_extra(item, known),
            )
        )
    return result


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
            known = {"spoken", "written", "enabled", "priority", "keyterm"}
            entries.append(
                VocabularyEntry(
                    spoken=spoken,
                    written=written,
                    enabled=_as_bool(item.get("enabled")),
                    priority=_as_int(item.get("priority")),
                    keyterm=_as_bool(item.get("keyterm")),
                    extra=_extra(item, known),
                )
            )
    return entries


def _load_command_action(raw: object) -> CommandAction | None:
    if not isinstance(raw, dict):
        return None
    action_type = _as_str(raw.get("type"))
    if action_type not in COMMAND_ACTION_TYPES or action_type in FORBIDDEN_ACTION_TYPES:
        return None
    known = {
        "type", "text", "key", "macro", "target", "app", "window",
        "desktop_file_id", "action", "direction", "amount",
    }
    return CommandAction(
        type=action_type,
        text=_as_str(raw.get("text")),
        key=_as_str(raw.get("key")),
        macro=_as_str(raw.get("macro")),
        target=_as_str(raw.get("target")),
        app=_as_str(raw.get("app")),
        window=_as_str(raw.get("window")),
        desktop_file_id=_as_str(raw.get("desktop_file_id")),
        action=_as_str(raw.get("action")),
        direction=_as_str(raw.get("direction")),
        amount=_as_int_or_slot(raw.get("amount")),
        extra=_extra(raw, known),
    )


def _action_has_payload(action: CommandAction) -> bool:
    return {
        "type_text": bool(action.text),
        "type_cli": bool(action.text),
        "press_key": bool(action.key),
        "macro": bool(action.macro),
        "activate_app": bool(action.app),
        "activate_window": bool(action.window),
        "launch_desktop_entry": bool(action.desktop_file_id),
        "invoke_ui": bool(action.target),
        "move_pointer": bool(action.target),
        "click": bool(action.target),
        "right_click": bool(action.target),
        "double_click": bool(action.target),
        "scroll": action.direction in {"up", "down", "left", "right"},
    }.get(action.type, False)


def _load_commands(raw_items: object) -> list[CommandEntry]:
    entries: list[CommandEntry] = []
    if not isinstance(raw_items, list):
        return entries
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        spoken = _as_str(item.get("spoken"))
        action = _load_command_action(item.get("action"))
        if not spoken or action is None or not _action_has_payload(action):
            continue
        known = {"spoken", "action", "enabled", "slots", "profiles", "description"}
        entries.append(
            CommandEntry(
                spoken=spoken,
                action=action,
                enabled=_as_bool(item.get("enabled")),
                slots=_load_slots(item.get("slots")),
                profiles=_as_str_list(item.get("profiles")),
                description=_as_str(item.get("description")),
                extra=_extra(item, known),
            )
        )
    return entries


def _load_macro_step(raw_step: object) -> MacroStep | None:
    if not isinstance(raw_step, dict):
        return None
    step_type = _as_str(raw_step.get("type"))
    if step_type not in MACRO_STEP_TYPES or step_type in FORBIDDEN_ACTION_TYPES:
        return None
    known = {
        "type", "text", "key", "seconds", "target", "app", "window",
        "desktop_file_id", "action", "direction", "amount", "count", "timeout",
        "continue_on_error", "steps", "else_steps",
    }
    step = MacroStep(
        type=step_type,
        text=_as_str(raw_step.get("text")),
        key=_as_str(raw_step.get("key")),
        seconds=_as_float_or_slot(raw_step.get("seconds")),
        target=_as_str(raw_step.get("target")),
        app=_as_str(raw_step.get("app")),
        window=_as_str(raw_step.get("window")),
        desktop_file_id=_as_str(raw_step.get("desktop_file_id")),
        action=_as_str(raw_step.get("action")),
        direction=_as_str(raw_step.get("direction")),
        amount=_as_int_or_slot(raw_step.get("amount")),
        count=_as_int_or_slot(raw_step.get("count")),
        timeout=_as_float_or_slot(raw_step.get("timeout")),
        continue_on_error=_as_bool(raw_step.get("continue_on_error"), False),
        steps=[s for raw in raw_step.get("steps", []) if (s := _load_macro_step(raw)) is not None]
        if isinstance(raw_step.get("steps"), list) else [],
        else_steps=[s for raw in raw_step.get("else_steps", []) if (s := _load_macro_step(raw)) is not None]
        if isinstance(raw_step.get("else_steps"), list) else [],
        extra=_extra(raw_step, known),
    )
    return step if _step_has_payload(step) else None


def _step_has_payload(step: MacroStep) -> bool:
    if step.type in {"type_text", "type_cli"}:
        return bool(step.text)
    if step.type == "press_key":
        return bool(step.key)
    if step.type == "wait":
        return _positive_or_slot(step.seconds, 300)
    if step.type == "wait_for_target":
        return bool(step.target) and (not step.timeout or _positive_or_slot(step.timeout, 300))
    if step.type == "repeat":
        return bool(step.steps) and _positive_or_slot(step.count, 100)
    if step.type == "target_exists":
        return bool(step.target) and bool(step.steps or step.else_steps)
    action = CommandAction(
        type=step.type,
        text=step.text,
        key=step.key,
        target=step.target,
        app=step.app,
        window=step.window,
        desktop_file_id=step.desktop_file_id,
        action=step.action,
        direction=step.direction,
        amount=step.amount,
    )
    return _action_has_payload(action)


def _load_macros(raw_items: object) -> list[MacroEntry]:
    entries: list[MacroEntry] = []
    if not isinstance(raw_items, list):
        return entries
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        name = _as_str(item.get("name"))
        spoken = _as_str(item.get("spoken"))
        raw_steps = item.get("steps")
        if not name or not spoken or not isinstance(raw_steps, list):
            continue
        steps = [step for raw_step in raw_steps if (step := _load_macro_step(raw_step)) is not None]
        if steps:
            known = {"name", "spoken", "steps", "enabled", "slots", "profiles", "description"}
            entries.append(
                MacroEntry(
                    name=name,
                    spoken=spoken,
                    steps=steps,
                    enabled=_as_bool(item.get("enabled")),
                    slots=_load_slots(item.get("slots")),
                    profiles=_as_str_list(item.get("profiles")),
                    description=_as_str(item.get("description")),
                    extra=_extra(item, known),
                )
            )
    return entries


def _load_profiles(raw_items: object) -> list[ProfileEntry]:
    entries: list[ProfileEntry] = []
    if not isinstance(raw_items, list):
        return entries
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        name = _as_str(item.get("name"))
        if not name:
            continue
        known = {
            "name", "enabled", "vocabulary_spoken", "command_spoken", "macro_names",
            "desktop_file_id", "window_title_pattern", "priority", "terminal_mode",
            "code_mode", "keyterms_enabled", "entity_hints",
        }
        entries.append(
            ProfileEntry(
                name=name,
                enabled=_as_bool(item.get("enabled")),
                vocabulary_spoken=_as_str_list(item.get("vocabulary_spoken")),
                command_spoken=_as_str_list(item.get("command_spoken")),
                macro_names=_as_str_list(item.get("macro_names")),
                desktop_file_id=_as_str(item.get("desktop_file_id")),
                window_title_pattern=_as_str(item.get("window_title_pattern")),
                priority=_as_int(item.get("priority")),
                terminal_mode=_as_bool(item.get("terminal_mode"), False),
                code_mode=_as_bool(item.get("code_mode"), False),
                keyterms_enabled=_as_bool(item.get("keyterms_enabled"), False),
                entity_hints=_as_str_list(item.get("entity_hints")),
                extra=_extra(item, known),
            )
        )
    return entries


def load_advanced_config(path: Path) -> AdvancedConfig:
    if not path.exists():
        # Preserve the legacy observable for an as-yet-uncreated file. The
        # first save writes v3, and every existing v1/v2 file migrates to v3.
        return AdvancedConfig(metadata={"version": 1})
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return AdvancedConfig(load_error=f"{path}: {exc}")
    if not isinstance(raw, dict):
        return AdvancedConfig(load_error=f"{path}: expected JSON object")

    invalid_action = _first_invalid_action(raw)

    metadata = dict(raw.get("metadata")) if isinstance(raw.get("metadata"), dict) else {}
    old_version = _as_int(metadata.get("version")) or 1
    metadata["version"] = SCHEMA_VERSION
    if old_version < SCHEMA_VERSION:
        metadata.setdefault("migrated_from", old_version)
    known_root = {"metadata", "vocabulary", "commands", "macros", "profiles", "behavior", "templates"}
    config = AdvancedConfig(
        vocabulary=_load_vocabulary(raw.get("vocabulary")),
        commands=_load_commands(raw.get("commands")),
        macros=_load_macros(raw.get("macros")),
        profiles=_load_profiles(raw.get("profiles")),
        metadata=metadata,
        behavior=dict(raw.get("behavior")) if isinstance(raw.get("behavior"), dict) else {},
        templates=[dict(item) for item in raw.get("templates", []) if isinstance(item, dict)]
        if isinstance(raw.get("templates"), list) else [],
        extra=_extra(raw, known_root),
    )
    try:
        validate_advanced_config(config)
    except ConfigValidationError as exc:
        config.load_error = f"{path}: {exc}"
    if invalid_action:
        config.load_error = f"{path}: forbidden or unsupported desktop action {invalid_action!r}"
    return config


def _first_invalid_action(raw: dict[str, Any]) -> str:
    commands = raw.get("commands")
    if isinstance(commands, list):
        for command in commands:
            action = command.get("action") if isinstance(command, dict) else None
            if isinstance(action, dict):
                kind = _as_str(action.get("type"))
                if kind and kind not in COMMAND_ACTION_TYPES:
                    return kind

    def inspect_steps(steps: object) -> str:
        if not isinstance(steps, list):
            return ""
        for step in steps:
            if not isinstance(step, dict):
                continue
            kind = _as_str(step.get("type"))
            if kind and kind not in MACRO_STEP_TYPES:
                return kind
            nested = inspect_steps(step.get("steps")) or inspect_steps(step.get("else_steps"))
            if nested:
                return nested
        return ""

    macros = raw.get("macros")
    if isinstance(macros, list):
        for macro in macros:
            if isinstance(macro, dict) and (invalid := inspect_steps(macro.get("steps"))):
                return invalid
    return ""


def _with_extra(extra: dict[str, Any], **known: Any) -> dict[str, Any]:
    value = dict(extra)
    value.update(known)
    return value


def _slot_data(slot: SlotDefinition) -> dict[str, Any]:
    return _with_extra(
        slot.extra,
        name=slot.name,
        type=slot.type,
        choices=slot.choices,
        required=slot.required,
    )


def _action_data(action: CommandAction) -> dict[str, Any]:
    return _with_extra(
        action.extra,
        type=action.type,
        text=action.text,
        key=action.key,
        macro=action.macro,
        target=action.target,
        app=action.app,
        window=action.window,
        desktop_file_id=action.desktop_file_id,
        action=action.action,
        direction=action.direction,
        amount=action.amount,
    )


def _step_data(step: MacroStep) -> dict[str, Any]:
    return _with_extra(
        step.extra,
        type=step.type,
        text=step.text,
        key=step.key,
        seconds=step.seconds,
        target=step.target,
        app=step.app,
        window=step.window,
        desktop_file_id=step.desktop_file_id,
        action=step.action,
        direction=step.direction,
        amount=step.amount,
        count=step.count,
        timeout=step.timeout,
        continue_on_error=step.continue_on_error,
        steps=[_step_data(child) for child in step.steps],
        else_steps=[_step_data(child) for child in step.else_steps],
    )


def validate_advanced_config(config: AdvancedConfig) -> None:
    from .command_patterns import PatternCompileError, compile_command_pattern

    for command in config.commands:
        if command.action.type in FORBIDDEN_ACTION_TYPES or command.action.type not in COMMAND_ACTION_TYPES:
            raise ConfigValidationError(f"Command {command.spoken!r} uses forbidden action {command.action.type!r}.")
        if not _action_has_payload(command.action):
            raise ConfigValidationError(f"Command {command.spoken!r} has no valid action payload.")
        _validate_key_and_scroll(command.action.key, command.action.direction, command.action.amount, command.action.type, command.spoken)
        try:
            compiled = compile_command_pattern(command)
        except PatternCompileError as exc:
            raise ConfigValidationError(str(exc)) from exc
        slot_types = {slot.name: slot.type for slot in compiled.slots}
        if command.action.type == "launch_desktop_entry" and not _valid_desktop_template(
            command.action.desktop_file_id, slot_types
        ):
            raise ConfigValidationError(f"Command {command.spoken!r} has an invalid desktop-file ID.")
        _validate_action_templates(command.action, slot_types, command.spoken)
        _validate_slots(command.spoken, command.slots)
    for macro in config.macros:
        _validate_slots(macro.spoken, macro.slots)
        try:
            compiled = compile_command_pattern(macro)
        except PatternCompileError as exc:
            raise ConfigValidationError(str(exc)) from exc
        slot_types = {slot.name: slot.type for slot in compiled.slots}
        for step in macro.steps:
            _validate_step(step, macro.name, slot_types)
    for profile in config.profiles:
        if profile.desktop_file_id and not _DESKTOP_ID_RE.fullmatch(profile.desktop_file_id):
            raise ConfigValidationError(f"Profile {profile.name!r} has an invalid desktop-file ID.")
        if profile.window_title_pattern:
            try:
                re.compile(profile.window_title_pattern)
            except re.error as exc:
                raise ConfigValidationError(f"Profile {profile.name!r} has an invalid title pattern: {exc}") from exc


def _validate_slots(pattern: str, slots: list[SlotDefinition]) -> None:
    seen: set[str] = set()
    for slot in slots:
        if slot.type not in SLOT_TYPES:
            raise ConfigValidationError(f"Pattern {pattern!r} uses unsupported slot type {slot.type!r}.")
        if slot.name in seen:
            raise ConfigValidationError(f"Pattern {pattern!r} repeats slot name {slot.name!r}.")
        if slot.type == "choice" and not slot.choices:
            raise ConfigValidationError(f"Choice slot {slot.name!r} must define choices.")
        seen.add(slot.name)


def _validate_step(step: MacroStep, macro_name: str, slot_types: dict[str, str]) -> None:
    if step.type in FORBIDDEN_ACTION_TYPES or step.type not in MACRO_STEP_TYPES:
        raise ConfigValidationError(f"Macro {macro_name!r} uses forbidden action {step.type!r}.")
    if not _step_has_payload(step):
        raise ConfigValidationError(f"Macro {macro_name!r} has an invalid {step.type!r} step.")
    _validate_key_and_scroll(step.key, step.direction, step.amount, step.type, macro_name)
    if step.type == "launch_desktop_entry" and not _valid_desktop_template(step.desktop_file_id, slot_types):
        raise ConfigValidationError(f"Macro {macro_name!r} has an invalid desktop-file ID.")
    _validate_step_templates(step, slot_types, macro_name)
    for child in (*step.steps, *step.else_steps):
        _validate_step(child, macro_name, slot_types)


def _valid_desktop_template(value: str, slot_types: dict[str, str]) -> bool:
    matched = _SLOT_VALUE_RE.fullmatch(value)
    if matched:
        return slot_types.get(matched.group(1)) in {"app", "choice", "text"}
    return bool(_DESKTOP_ID_RE.fullmatch(value))


def _validate_template_names(value: str, slot_types: dict[str, str], owner: str) -> None:
    for name in re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", value):
        if name not in slot_types:
            raise ConfigValidationError(f"{owner!r} references undefined slot {name!r}.")


def _validate_numeric_template(
    value: int | float | str,
    slot_types: dict[str, str],
    owner: str,
) -> None:
    if not isinstance(value, str):
        return
    matched = _SLOT_VALUE_RE.fullmatch(value)
    if matched is None or slot_types.get(matched.group(1)) != "integer":
        raise ConfigValidationError(f"{owner!r} requires an integer slot template, not {value!r}.")


def _validate_action_templates(action: CommandAction, slot_types: dict[str, str], owner: str) -> None:
    for value in (
        action.text,
        action.key,
        action.macro,
        action.target,
        action.app,
        action.window,
        action.desktop_file_id,
        action.action,
        action.direction,
    ):
        _validate_template_names(value, slot_types, owner)
    _validate_numeric_template(action.amount, slot_types, owner)


def _validate_key_and_scroll(
    key: str,
    direction: str,
    amount: int | str,
    action_type: str,
    owner: str,
) -> None:
    if action_type == "press_key" and not _SLOT_VALUE_RE.fullmatch(key):
        if not _KEY_CHORD_RE.fullmatch(key):
            raise ConfigValidationError(f"{owner!r} has an invalid key chord.")
    if action_type == "scroll":
        if direction not in {"up", "down", "left", "right"}:
            raise ConfigValidationError(f"{owner!r} has an invalid scroll direction.")
        if not isinstance(amount, str) and amount not in range(0, 101):
            raise ConfigValidationError(f"{owner!r} scroll amount must be between 1 and 100.")


def _validate_step_templates(step: MacroStep, slot_types: dict[str, str], owner: str) -> None:
    action = CommandAction(
        type=step.type,
        text=step.text,
        key=step.key,
        target=step.target,
        app=step.app,
        window=step.window,
        desktop_file_id=step.desktop_file_id,
        action=step.action,
        direction=step.direction,
        amount=step.amount,
    )
    _validate_action_templates(action, slot_types, owner)
    for value in (step.seconds, step.count, step.timeout):
        _validate_numeric_template(value, slot_types, owner)


def save_advanced_config(path: Path, config: AdvancedConfig) -> None:
    validate_advanced_config(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    metadata = dict(config.metadata)
    metadata["version"] = SCHEMA_VERSION
    data = dict(config.extra)
    data.update(
        {
            "metadata": metadata,
            "behavior": config.behavior,
            "templates": config.templates,
            "vocabulary": [
                _with_extra(
                    entry.extra,
                    spoken=entry.spoken,
                    written=entry.written,
                    enabled=entry.enabled,
                    priority=entry.priority,
                    keyterm=entry.keyterm,
                )
                for entry in config.vocabulary
            ],
            "commands": [
                _with_extra(
                    entry.extra,
                    spoken=entry.spoken,
                    action=_action_data(entry.action),
                    enabled=entry.enabled,
                    slots=[_slot_data(slot) for slot in entry.slots],
                    profiles=entry.profiles,
                    description=entry.description,
                )
                for entry in config.commands
            ],
            "macros": [
                _with_extra(
                    entry.extra,
                    name=entry.name,
                    spoken=entry.spoken,
                    enabled=entry.enabled,
                    slots=[_slot_data(slot) for slot in entry.slots],
                    profiles=entry.profiles,
                    description=entry.description,
                    steps=[_step_data(step) for step in entry.steps],
                )
                for entry in config.macros
            ],
            "profiles": [
                _with_extra(
                    entry.extra,
                    name=entry.name,
                    enabled=entry.enabled,
                    vocabulary_spoken=entry.vocabulary_spoken,
                    command_spoken=entry.command_spoken,
                    macro_names=entry.macro_names,
                    desktop_file_id=entry.desktop_file_id,
                    window_title_pattern=entry.window_title_pattern,
                    priority=entry.priority,
                    terminal_mode=entry.terminal_mode,
                    code_mode=entry.code_mode,
                    keyterms_enabled=entry.keyterms_enabled,
                    entity_hints=entry.entity_hints,
                )
                for entry in config.profiles
            ],
        }
    )
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def export_advanced_config(config: AdvancedConfig) -> str:
    """Return a validated portable schema-v3 JSON document."""

    validate_advanced_config(config)
    # Reuse the canonical serializer without introducing a second schema.
    from tempfile import TemporaryDirectory

    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "advanced.json"
        save_advanced_config(path, config)
        return path.read_text(encoding="utf-8")


def import_advanced_config(payload: str) -> AdvancedConfig:
    from tempfile import TemporaryDirectory

    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "advanced.json"
        path.write_text(payload, encoding="utf-8")
        config = load_advanced_config(path)
    if config.load_error:
        raise ConfigValidationError(config.load_error)
    return config

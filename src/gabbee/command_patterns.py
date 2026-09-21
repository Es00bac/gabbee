from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Generic, Iterable, TypeVar

from .advanced_config import CommandEntry, MacroEntry, SLOT_TYPES, SlotDefinition


EntryT = TypeVar("EntryT", CommandEntry, MacroEntry)
_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*):([A-Za-z_][A-Za-z0-9_]*)\}")


class PatternCompileError(ValueError):
    pass


@dataclass(slots=True, frozen=True)
class CommandMatch(Generic[EntryT]):
    entry: EntryT
    slots: dict[str, Any]
    raw_slots: dict[str, str]

    def render(self, template: str) -> str:
        result = template
        for name, value in self.slots.items():
            result = result.replace("{" + name + "}", str(value))
        return result


@dataclass(slots=True)
class CompiledCommandPattern(Generic[EntryT]):
    entry: EntryT
    regex: re.Pattern[str]
    slots: list[SlotDefinition]

    def match(self, text: str) -> CommandMatch[EntryT] | None:
        matched = self.regex.fullmatch(text.strip())
        if matched is None:
            return None
        raw_slots: dict[str, str] = {}
        values: dict[str, Any] = {}
        for slot in self.slots:
            raw = matched.group(slot.name).strip()
            try:
                value = _convert_slot(slot, raw)
            except ValueError:
                return None
            raw_slots[slot.name] = raw
            values[slot.name] = value
        return CommandMatch(entry=self.entry, slots=values, raw_slots=raw_slots)


def compile_command_pattern(entry: EntryT) -> CompiledCommandPattern[EntryT]:
    definitions = {slot.name: slot for slot in entry.slots}
    seen: set[str] = set()
    pieces: list[str] = [r"\s*"]
    cursor = 0
    slots: list[SlotDefinition] = []
    for matched in _PLACEHOLDER_RE.finditer(entry.spoken):
        pieces.append(_literal_regex(entry.spoken[cursor : matched.start()]))
        name, slot_type = matched.groups()
        if name in seen:
            raise PatternCompileError(f"Slot {name!r} is repeated in {entry.spoken!r}.")
        if slot_type not in SLOT_TYPES:
            raise PatternCompileError(f"Unsupported slot type {slot_type!r} in {entry.spoken!r}.")
        configured = definitions.get(name)
        if configured is not None and configured.type != slot_type:
            raise PatternCompileError(
                f"Slot {name!r} is {slot_type!r} in the pattern but {configured.type!r} in its definition."
            )
        slot = configured or SlotDefinition(name=name, type=slot_type)
        if slot.type == "choice" and not slot.choices:
            raise PatternCompileError(f"Choice slot {name!r} has no choices.")
        pieces.append(f"(?P<{name}>{_slot_regex(slot)})")
        slots.append(slot)
        seen.add(name)
        cursor = matched.end()
    pieces.append(_literal_regex(entry.spoken[cursor:]))
    pieces.append(r"\s*")

    # Braces are reserved so malformed patterns fail at configuration time.
    remainder = _PLACEHOLDER_RE.sub("", entry.spoken)
    if "{" in remainder or "}" in remainder:
        raise PatternCompileError(f"Malformed slot in pattern {entry.spoken!r}.")
    unused = set(definitions) - seen
    if unused:
        raise PatternCompileError(f"Unused slot definitions in {entry.spoken!r}: {', '.join(sorted(unused))}")
    return CompiledCommandPattern(entry=entry, regex=re.compile("".join(pieces), re.IGNORECASE), slots=slots)


def compile_patterns(entries: Iterable[EntryT]) -> list[CompiledCommandPattern[EntryT]]:
    return [compile_command_pattern(entry) for entry in entries if entry.enabled]


def match_patterns(
    text: str,
    patterns: Iterable[CompiledCommandPattern[EntryT]],
) -> list[CommandMatch[EntryT]]:
    return [result for pattern in patterns if (result := pattern.match(text)) is not None]


def _literal_regex(text: str) -> str:
    # Spoken patterns treat runs of whitespace as equivalent.
    return "".join(
        r"\s+" if part.isspace() else re.escape(part)
        for part in re.split(r"(\s+)", text)
        if part
    )


def _slot_regex(slot: SlotDefinition) -> str:
    if slot.type == "choice":
        return "(?:" + "|".join(re.escape(choice) for choice in sorted(slot.choices, key=len, reverse=True)) + ")"
    if slot.type in {"integer", "digits", "grouped_digits", "phone"}:
        return r"[A-Za-z0-9+.,:/\- ]+?"
    return r".+?"


def _convert_slot(slot: SlotDefinition, raw: str) -> Any:
    from .number_normalizer import format_phone, parse_integer, spoken_digits

    if not raw:
        raise ValueError("empty slot")
    if slot.type in {"text", "app", "window", "ui_target"}:
        return raw
    if slot.type == "choice":
        return next((choice for choice in slot.choices if choice.casefold() == raw.casefold()), raw)
    if slot.type == "integer":
        value = parse_integer(raw)
        if value is None:
            raise ValueError("not an integer")
        return value
    if slot.type == "digits":
        value = spoken_digits(raw, preserve_separators=False)
        if value is None:
            raise ValueError("not digits")
        return value
    if slot.type == "grouped_digits":
        value = spoken_digits(raw, preserve_separators=True)
        if value is None:
            raise ValueError("not grouped digits")
        return value
    if slot.type == "phone":
        value = format_phone(raw)
        if value is None:
            raise ValueError("not a phone number")
        return value
    raise ValueError(f"unsupported slot type: {slot.type}")

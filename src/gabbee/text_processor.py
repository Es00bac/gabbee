from __future__ import annotations

import csv
from pathlib import Path
import re

from .advanced_config import AdvancedConfig, CommandEntry
from .command_patterns import CommandMatch, PatternCompileError, compile_patterns, match_patterns
from .models import ProcessedTranscript, ProcessingContext, SemanticSpan
from .number_normalizer import NumberNormalizer


class SpokenPunctuationFormatter:
    """Formats explicit punctuation words while supporting a literal escape."""

    _phrases = {
        "new paragraph": "\n\n",
        "new line": "\n",
        "newline": "\n",
        "open quote": "“",
        "close quote": "”",
        "open parenthesis": "(",
        "open parentheses": "(",
        "left parenthesis": "(",
        "close parenthesis": ")",
        "close parentheses": ")",
        "right parenthesis": ")",
        "semicolon": ";",
        "colon": ":",
        "comma": ",",
        "period": ".",
        "full stop": ".",
        "question mark": "?",
        "exclamation mark": "!",
        "exclamation point": "!",
        "dash": "—",
        "hyphen": "-",
        "slash": "/",
    }

    def format(self, text: str, *, prose_spacing: bool = True) -> str:
        if not text:
            return text
        protected: dict[str, str] = {}
        counter = 0

        # Literal escapes the next known layout phrase or, as a useful fallback,
        # the next word. Sentinels prevent all later formatting passes.
        phrase_alternation = "|".join(re.escape(item) for item in sorted(self._phrases, key=len, reverse=True))
        literal_pattern = re.compile(rf"\bliteral\s+({phrase_alternation}|\S+)", re.IGNORECASE)

        def protect(matched: re.Match[str]) -> str:
            nonlocal counter
            key = f"\ue000{counter}\ue001"
            counter += 1
            protected[key] = matched.group(1)
            return key

        text = literal_pattern.sub(protect, text)
        quote_open = True

        def replace_phrase(matched: re.Match[str]) -> str:
            nonlocal quote_open
            phrase = matched.group(0).casefold()
            if phrase == "quote":
                value = "“" if quote_open else "”"
                quote_open = not quote_open
                return value
            return self._phrases[phrase]

        all_phrases = dict(self._phrases)
        all_phrases["quote"] = ""
        pattern = re.compile(
            rf"\b(?:{'|'.join(re.escape(item) for item in sorted(all_phrases, key=len, reverse=True))})\b",
            re.IGNORECASE,
        )
        text = pattern.sub(replace_phrase, text)
        text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)

        if prose_spacing:
            text = re.sub(r"\s+([,.;:!?%\)\]\}”])", r"\1", text)
            text = re.sub(r"([\(\[\{“])\s+", r"\1", text)
            text = re.sub(r"\s*/\s*", "/", text)
            text = re.sub(r"\s*-\s*", "-", text)
            text = re.sub(r"\s*—\s*", " — ", text)
            text = re.sub(r"[ \t]{2,}", " ", text)

        for key, literal in protected.items():
            text = text.replace(key, literal)
        return text.strip(" \t")


class TextProcessor:
    def __init__(
        self,
        keyword_map: dict[str, str] | None = None,
        vocabulary_path: Path | None = None,
        advanced_config: AdvancedConfig | None = None,
    ) -> None:
        self.vocabulary_path = vocabulary_path
        self.advanced_config = advanced_config or AdvancedConfig()
        # These are only considered in command mode. Ordinary dictation cannot
        # trigger editing or desktop actions.
        self.keyword_map = {
            "new line": "\n",
            "next line": "\n",
            "new paragraph": "\n\n",
            "tab key": "Tab",
            "space bar": "space",
            "home key": "Home",
            "page up": "Prior",
            "page down": "Next",
            "end key": "End",
            "previous word": "Control+Left",
            "next word": "Control+Right",
            "first word": "Control+Home",
            "last word": "Control+End",
            "backspace": "BackSpace",
            "delete": "Delete",
            "enter key": "Return",
            "undo": "Control+z",
            "redo": "Control+y",
            "select all": "Control+a",
            "copy that": "Control+c",
            "cut that": "Control+x",
            "paste that": "Control+v",
            "escape key": "Escape",
            "delete word": "Control+BackSpace",
            "delete last word": "Control+BackSpace",
            "delete previous word": "Control+BackSpace",
            "delete line": "Control+u",
            "go to end of line": "End",
            "go to start of line": "Home",
            "select word": "Control+Shift+Left",
            "select line": "Shift+End",
            "select last sentence": "Control+Shift+Left",
            "go to previous line": "Up",
            "go to next line": "Down",
        }
        if keyword_map:
            self.keyword_map.update({key.casefold(): value for key, value in keyword_map.items()})

        self.custom_vocabulary: dict[str, str] = {}
        self._load_vocabulary()
        self.custom_vocabulary.update(
            {
                entry.spoken.casefold().strip(): entry.written.strip()
                for entry in self.advanced_config.vocabulary
                if entry.enabled and entry.spoken.strip() and entry.written.strip()
            }
        )
        self.key_commands = {
            "Tab", "Home", "Prior", "Next", "End", "BackSpace", "Delete", "Return",
            "Escape", "space", "Up", "Down", "Left", "Right",
        }
        self.compound_keys = {"Control+", "Alt+", "Shift+", "Meta+"}
        self.number_normalizer = NumberNormalizer()
        self.punctuation_formatter = SpokenPunctuationFormatter()
        try:
            self._compiled_commands = compile_patterns(self.advanced_config.commands)
        except PatternCompileError:
            # Loading remains resilient; schema validation surfaces the exact
            # configuration error in Command Studio/Diagnostics.
            self._compiled_commands = []

    def _load_vocabulary(self) -> None:
        if not self.vocabulary_path or not self.vocabulary_path.exists():
            return
        try:
            with self.vocabulary_path.open(mode="r", encoding="utf-8", newline="") as handle:
                for row in csv.reader(handle):
                    if len(row) >= 2:
                        self.custom_vocabulary[row[0].casefold().strip()] = row[1].strip()
        except Exception as exc:
            print(f"Error loading vocabulary: {exc}")

    def match_custom_commands(
        self,
        text: str,
        *,
        active_profile: str = "",
    ) -> list[CommandMatch[CommandEntry]]:
        matches = match_patterns(text, self._compiled_commands)
        if not active_profile:
            return [match for match in matches if not match.entry.profiles]
        folded = active_profile.casefold()
        return [
            match
            for match in matches
            if not match.entry.profiles or folded in {name.casefold() for name in match.entry.profiles}
        ]

    def _custom_command_for(self, text: str, *, active_profile: str = "") -> CommandMatch[CommandEntry] | None:
        matches = self.match_custom_commands(text, active_profile=active_profile)
        return matches[0] if len(matches) == 1 else None

    def process_transcript(
        self,
        text: str,
        context: ProcessingContext | None = None,
        *,
        entity_spans: list[SemanticSpan] | None = None,
    ) -> ProcessedTranscript:
        context = context or ProcessingContext()
        spans = entity_spans if entity_spans is not None else context.entity_spans
        normalized, semantic_spans = self.number_normalizer.normalize_with_spans(text, spans)
        normalized = self._apply_vocabulary(normalized)
        normalized = self._convert_dots(normalized)
        normalized = self.punctuation_formatter.format(normalized, prose_spacing=context.prose_spacing)
        normalized = self._apply_surrounding_context(normalized, context)
        return ProcessedTranscript(
            raw_text=text,
            text=normalized,
            spans=semantic_spans,
            profile_name=context.profile_name,
        )

    def process_to_actions(
        self,
        text: str,
        *,
        enable_commands: bool = False,
        context: ProcessingContext | None = None,
        entity_spans: list[SemanticSpan] | None = None,
    ) -> list[tuple[str, str]]:
        if not text:
            return []

        if enable_commands:
            active_profile = context.profile_name if context else ""
            custom_match = self._custom_command_for(text, active_profile=active_profile)
            if custom_match is not None:
                action = custom_match.entry.action
                if action.type in {"type_text", "type_cli"}:
                    rendered = custom_match.render(action.text)
                    return [("text", rendered)] if rendered else []
                if action.type == "press_key":
                    rendered = custom_match.render(action.key)
                    return [("key", rendered)] if rendered else []
                # The desktop command dispatcher consumes richer action types.
                return [("desktop", custom_match.entry.spoken)]

        processed = self.process_transcript(text, context, entity_spans=entity_spans).text
        if not enable_commands:
            return [("text", processed)]

        sorted_keywords = sorted(self.keyword_map, key=len, reverse=True)
        if not sorted_keywords:
            return [("text", processed)]
        pattern = re.compile(rf"\b({'|'.join(re.escape(key) for key in sorted_keywords)})\b", re.IGNORECASE)
        actions: list[tuple[str, str]] = []
        last_position = 0
        for matched in pattern.finditer(processed):
            before = processed[last_position : matched.start()].strip()
            if before:
                actions.append(("text", before))
            replacement = self.keyword_map[matched.group(0).casefold()]
            is_key = replacement in self.key_commands or any(
                replacement.startswith(prefix) for prefix in self.compound_keys
            )
            actions.append(("key" if is_key else "text", replacement))
            last_position = matched.end()
        after = processed[last_position:].strip()
        if after:
            actions.append(("text", after))
        return actions

    def process(self, text: str, *, enable_commands: bool = False) -> str:
        actions = self.process_to_actions(text, enable_commands=enable_commands)
        return "".join(value if kind == "text" else " " for kind, value in actions)

    def _apply_vocabulary(self, text: str) -> str:
        if not self.custom_vocabulary:
            return text
        spoken = sorted(self.custom_vocabulary, key=len, reverse=True)
        pattern = re.compile(rf"\b({'|'.join(re.escape(item) for item in spoken)})\b", re.IGNORECASE)
        return pattern.sub(lambda matched: self.custom_vocabulary[matched.group(0).casefold()], text)

    @staticmethod
    def _convert_dots(text: str) -> str:
        text = re.sub(
            r"\s+dot\s+(com|net|org|edu|gov|io|me|md|py|js|ts|c|cpp|h|rs)\b",
            r".\1",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(r"\bdot\s*$", ".", text, flags=re.IGNORECASE)
        text = re.sub(r"(\w+)\s+dot\s+(\w+)", r"\1.\2", text, flags=re.IGNORECASE)
        return text

    def _convert_numbers(self, text: str) -> str:
        return self.number_normalizer.normalize(text)

    @staticmethod
    def _apply_surrounding_context(text: str, context: ProcessingContext) -> str:
        if not text or not context.prose_spacing:
            return text
        before = context.surrounding_before
        after = context.surrounding_after
        # An AppContext distinguishes a known empty field from unavailable
        # surrounding text. Legacy callers without one retain provider casing.
        if context.app is not None and (not before or re.search(r"[.!?][\s\"'”)]*$", before)):
            text = re.sub(
                r"^([^A-Za-z]*)([a-z])",
                lambda matched: matched.group(1) + matched.group(2).upper(),
                text,
                count=1,
            )
        if before and not before[-1].isspace() and re.search(r"[\w\"'”)]$", before) and re.match(r"[\w\"'“(]", text):
            text = " " + text
        if before and before[-1].isspace():
            text = text.lstrip(" ")
        if re.match(r"^[,.;:!?%\)\]\}]", text):
            text = text.lstrip(" ")
        if after and not after[0].isspace() and re.search(r"[\w\"'”)]$", text) and re.match(r"[\w\"'“(]", after):
            text += " "
        return text

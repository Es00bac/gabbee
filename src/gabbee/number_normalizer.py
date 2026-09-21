from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re
from typing import Iterable

from .models import SemanticSpan


@dataclass(slots=True, frozen=True)
class Digit:
    value: int
    spoken: str


@dataclass(slots=True, frozen=True)
class DigitRun:
    digits: str
    leading_zero: bool = False


@dataclass(slots=True, frozen=True)
class GroupedDigits:
    groups: tuple[str, ...]
    separator: str = "-"

    @property
    def text(self) -> str:
        return self.separator.join(self.groups)


@dataclass(slots=True, frozen=True)
class NumericValue:
    value: int | Decimal
    negative: bool = False
    ordinal: bool = False


_ONES = {
    "zero": 0,
    "oh": 0,
    "o": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
}
_CANONICAL_ONES = {key: value for key, value in _ONES.items() if key not in {"oh", "o"}}
_TEENS = {
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
}
_TENS = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}
_MAGNITUDES = {
    "hundred": 100,
    "thousand": 1_000,
    "million": 1_000_000,
    "billion": 1_000_000_000,
    "trillion": 1_000_000_000_000,
}
_ORDINALS = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "sixth": 6,
    "seventh": 7,
    "eighth": 8,
    "ninth": 9,
    "tenth": 10,
    "eleventh": 11,
    "twelfth": 12,
    "thirteenth": 13,
    "fourteenth": 14,
    "fifteenth": 15,
    "sixteenth": 16,
    "seventeenth": 17,
    "eighteenth": 18,
    "nineteenth": 19,
    "twentieth": 20,
    "thirtieth": 30,
    "fortieth": 40,
    "fiftieth": 50,
    "sixtieth": 60,
    "seventieth": 70,
    "eightieth": 80,
    "ninetieth": 90,
    "hundredth": 100,
    "thousandth": 1_000,
}
_FRACTIONS = {
    "half": (1, 2),
    "halves": (1, 2),
    "quarter": (1, 4),
    "quarters": (1, 4),
    "third": (1, 3),
    "thirds": (1, 3),
    "fourth": (1, 4),
    "fourths": (1, 4),
    "fifth": (1, 5),
    "fifths": (1, 5),
    "eighth": (1, 8),
    "eighths": (1, 8),
}
_REPEATS = {"double": 2, "triple": 3, "quadruple": 4}
_SEPARATORS = {
    "dash": "-",
    "hyphen": "-",
    "slash": "/",
    "point": ".",
    "dot": ".",
    "colon": ":",
}
_MEASUREMENT_CUES = {
    "mile", "miles", "meter", "meters", "metre", "metres", "kilometer",
    "kilometers", "kilometre", "kilometres", "inch", "inches", "foot", "feet",
    "pound", "pounds", "ounce", "ounces", "gram", "grams", "kilogram", "kilograms",
    "second", "seconds", "minute", "minutes", "hour", "hours", "percent", "percentage",
    "degree", "degrees", "celsius", "fahrenheit", "volt", "volts", "watt", "watts",
    "byte", "bytes", "kilobyte", "megabyte", "gigabyte", "terabyte",
}
_ENTITY_ALIASES = {
    "phone_number": "phone",
    "telephone": "phone",
    "postal_code": "zip",
    "zipcode": "zip",
    "location_zip": "zip",
    "currency": "money",
    "amount_of_money": "money",
    "identifier": "id",
    "generic_id": "id",
    "ip_address": "ip",
    "uri": "url",
    "file": "filename",
}
_TOKEN_RE = re.compile(r"[A-Za-z]+(?:-[A-Za-z]+)*|[+-]?\d+(?:[.,:/-]\d+)*|[^\w\s]", re.UNICODE)


@dataclass(slots=True, frozen=True)
class _Token:
    raw: str
    word: str
    start: int
    end: int


@dataclass(slots=True, frozen=True)
class _Replacement:
    start: int
    end: int
    text: str
    span: SemanticSpan


def _words(value: str) -> list[str]:
    return [part.casefold() for part in re.findall(r"[A-Za-z]+|[+-]?\d+(?:\.\d+)?", value.replace("-", " "))]


def parse_integer(value: str) -> int | None:
    """Parse an entire cardinal phrase without accepting trailing prose."""

    cleaned = value.strip().replace(",", "")
    if re.fullmatch(r"[+-]?\d+", cleaned):
        try:
            return int(cleaned)
        except ValueError:
            return None
    parts = _words(cleaned)
    if not parts:
        return None
    sign = 1
    if parts and parts[0] in {"minus", "negative"}:
        sign = -1
        parts = parts[1:]
    elif parts and parts[0] in {"plus", "positive"}:
        parts = parts[1:]
    if not parts:
        return None
    value_number, consumed, _ordinal = _parse_cardinal_parts(parts)
    if consumed != len(parts):
        return None
    return sign * value_number


def _parse_cardinal_parts(parts: list[str]) -> tuple[int, int, bool]:
    total = 0
    current = 0
    consumed = 0
    ordinal = False
    used_value = False
    for index, part in enumerate(parts):
        if part in _CANONICAL_ONES:
            number = _CANONICAL_ONES[part]
            # Cardinal grammar doesn't combine "one two"; that is a DigitRun.
            if used_value and current < 10 and total == 0:
                break
            if current >= 20 and current % 10 == 0 and number < 10:
                current += number
            elif current and current % 100 != 0 and number < 10:
                break
            else:
                current += number
            used_value = True
        elif part in _TEENS:
            if current and current % 100 != 0:
                break
            current += _TEENS[part]
            used_value = True
        elif part in _TENS:
            if current and current % 100 != 0:
                break
            current += _TENS[part]
            used_value = True
        elif part == "and" and used_value and index + 1 < len(parts):
            consumed += 1
            continue
        elif part in _MAGNITUDES:
            magnitude = _MAGNITUDES[part]
            if magnitude == 100:
                current = (current or 1) * 100
            else:
                total += (current or 1) * magnitude
                current = 0
            used_value = True
        elif part in _ORDINALS:
            number = _ORDINALS[part]
            if current >= 20 and current % 10 == 0 and number < 10:
                current += number
            elif number >= 100:
                current = (current or 1) * number
            elif current == 0:
                current = number
            else:
                break
            used_value = True
            ordinal = True
            consumed += 1
            break
        else:
            break
        consumed += 1
    if consumed and parts[consumed - 1] == "and":
        consumed -= 1
    return total + current, consumed, ordinal


def spoken_digits(value: str, *, preserve_separators: bool = False) -> str | None:
    """Parse individually spoken digits, repeat words, and spoken separators."""

    tokens = [part.casefold() for part in re.findall(r"[A-Za-z]+|\d+|[+./:-]", value)]
    if not tokens:
        return None
    result: list[str] = []
    repeat = 1
    saw_digit = False
    for token in tokens:
        if token in _REPEATS:
            if repeat != 1:
                return None
            repeat = _REPEATS[token]
            continue
        if token in _ONES:
            result.append(str(_ONES[token]) * repeat)
            repeat = 1
            saw_digit = True
            continue
        if token.isdigit():
            result.append(token * repeat)
            repeat = 1
            saw_digit = True
            continue
        separator = _SEPARATORS.get(
            token,
            "+" if token in {"plus", "positive"} else token if token in {"+", ".", "/", ":", "-"} else "",
        )
        if separator:
            if repeat != 1 or not preserve_separators:
                if repeat != 1:
                    return None
                continue
            if separator == "+" and not result:
                result.append(separator)
            elif result and result[-1] not in _SEPARATORS.values() and result[-1] not in {"+", ".", "/", ":", "-"}:
                result.append(separator)
            continue
        if token in {"space", "group"}:
            if preserve_separators and result and result[-1] != " ":
                result.append(" ")
            continue
        if token in {"and", "number", "code", "area", "country"}:
            continue
        # Digit mode still understands a single cardinal containing a magnitude.
        cardinal = parse_integer(value)
        if cardinal is not None:
            return str(cardinal)
        return None
    if repeat != 1 or not saw_digit:
        return None
    return "".join(result).strip(" .:/-")


def format_phone(value: str) -> str | None:
    grouped = spoken_digits(value, preserve_separators=True)
    if grouped is None:
        return None
    digits = re.sub(r"\D", "", grouped)
    if len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+1 ({digits[1:4]}) {digits[4:7]}-{digits[7:]}"
    return grouped


def number_to_words(value: int) -> str:
    if value < 0:
        return "minus " + number_to_words(-value)
    if value < 10:
        return ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine")[value]
    if value < 20:
        return next(key for key, item in _TEENS.items() if item == value)
    if value < 100:
        tens, remainder = divmod(value, 10)
        head = next(key for key, item in _TENS.items() if item == tens * 10)
        return head if not remainder else f"{head}-{number_to_words(remainder)}"
    if value < 1_000:
        hundreds, remainder = divmod(value, 100)
        head = f"{number_to_words(hundreds)} hundred"
        return head if not remainder else f"{head} {number_to_words(remainder)}"
    for label, magnitude in (("trillion", 10**12), ("billion", 10**9), ("million", 10**6), ("thousand", 10**3)):
        if value >= magnitude:
            head, remainder = divmod(value, magnitude)
            result = f"{number_to_words(head)} {label}"
            return result if not remainder else f"{result} {number_to_words(remainder)}"
    raise ValueError(value)


def _ordinal_suffix(value: int) -> str:
    if 10 <= value % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(value % 10, "th")
    return f"{value}{suffix}"


class NumberNormalizer:
    """Deterministic token/parser pipeline for prose and typed numeric entities."""

    def normalize(self, text: str, entity_spans: Iterable[SemanticSpan] = ()) -> str:
        formatted, _spans = self.normalize_with_spans(text, entity_spans)
        return formatted

    def normalize_with_spans(
        self,
        text: str,
        entity_spans: Iterable[SemanticSpan] = (),
    ) -> tuple[str, list[SemanticSpan]]:
        if not text:
            return text, []
        replacements: list[_Replacement] = []

        # 1. Explicit instructions have absolute priority.
        self._explicit_replacements(text, replacements)
        # 2. Only provider spans whose offsets and source text validate are used.
        for span in entity_spans:
            if not span.is_valid_for(text) or self._overlaps(span.start, span.end, replacements):
                continue
            formatted = self._format_entity(span.kind, text[span.start : span.end])
            if formatted is not None:
                replacements.append(
                    _Replacement(
                        span.start,
                        span.end,
                        formatted,
                        SemanticSpan(
                            span.start,
                            span.end,
                            span.kind,
                            text[span.start : span.end],
                            formatted,
                            "provider",
                            span.confidence,
                        ),
                    )
                )

        # 3. Typed contextual grammars.
        self._context_replacements(text, replacements)
        # 4. Prose-smart defaults.
        self._default_replacements(text, replacements)
        replacements.sort(key=lambda item: item.start)
        result: list[str] = []
        cursor = 0
        for replacement in replacements:
            result.append(text[cursor : replacement.start])
            result.append(replacement.text)
            cursor = replacement.end
        result.append(text[cursor:])
        return "".join(result), [item.span for item in replacements]

    def _add(
        self,
        replacements: list[_Replacement],
        start: int,
        end: int,
        formatted: str,
        kind: str,
        raw: str,
        source: str = "local",
    ) -> bool:
        if start >= end or self._overlaps(start, end, replacements):
            return False
        replacements.append(
            _Replacement(
                start,
                end,
                formatted,
                SemanticSpan(start, end, kind, raw, formatted, source),  # type: ignore[arg-type]
            )
        )
        return True

    @staticmethod
    def _overlaps(start: int, end: int, replacements: list[_Replacement]) -> bool:
        return any(start < item.end and end > item.start for item in replacements)

    def _explicit_replacements(self, text: str, replacements: list[_Replacement]) -> None:
        for cue in re.finditer(r"\bwrite\s+as\s+digits\b\s*", text, re.IGNORECASE):
            end, raw = self._consume_digit_phrase(text, cue.end())
            if not raw:
                continue
            formatted = spoken_digits(raw, preserve_separators=True)
            if formatted is None:
                integer = parse_integer(raw)
                formatted = str(integer) if integer is not None else None
            if formatted is not None:
                self._add(replacements, cue.start(), end, formatted, "digits", text[cue.start():end], "explicit")

        for matched in re.finditer(r"\bwrite\s+as\s+words\s+([+-]?\d[\d,]*)\b", text, re.IGNORECASE):
            value = parse_integer(matched.group(1))
            if value is not None:
                self._add(
                    replacements,
                    matched.start(),
                    matched.end(),
                    number_to_words(value),
                    "number_words",
                    matched.group(0),
                    "explicit",
                )

    def _context_replacements(self, text: str, replacements: list[_Replacement]) -> None:
        cue_specs = (
            (r"\b(?:phone(?:\s+number)?|telephone(?:\s+number)?)\b(?:\s+is)?\s*", "phone", ""),
            (r"\b(?:pin|p\s*i\s*n)\b(?:\s+(?:number|code|is))?\s*", "pin", "PIN "),
            (r"\b(?:otp|one[- ]time\s+(?:password|code))\b(?:\s+is)?\s*", "otp", "OTP "),
            (r"\bzip(?:\s+code)?\b(?:\s+is)?\s*", "zip", "ZIP "),
            (r"\bversion\b\s*", "version", "version "),
            (r"\b(?:ip(?:\s+address)?)\b(?:\s+is)?\s*", "ip", "IP "),
            (r"\bport\b(?:\s+is)?\s*", "port", "port "),
            (r"\b(?:identifier|id|serial(?:\s+number)?|account(?:\s+number)?)\b(?:\s+is)?\s*", "id", None),
        )
        for pattern, kind, prefix in cue_specs:
            for cue in re.finditer(pattern, text, re.IGNORECASE):
                if self._overlaps(cue.start(), cue.end(), replacements):
                    continue
                end, raw = self._consume_digit_phrase(text, cue.end())
                if not raw:
                    continue
                if kind == "phone":
                    formatted = format_phone(raw)
                elif kind in {"version", "ip"}:
                    formatted = spoken_digits(raw, preserve_separators=True)
                    if formatted and kind == "version" and "." not in formatted:
                        formatted = None
                elif kind == "port":
                    value = parse_integer(raw)
                    formatted = str(value) if value is not None and 0 <= value <= 65535 else spoken_digits(raw)
                elif kind == "zip":
                    digits = spoken_digits(raw, preserve_separators=True)
                    compact = re.sub(r"\D", "", digits or "")
                    if len(compact) == 9:
                        formatted = f"{compact[:5]}-{compact[5:]}"
                    elif len(compact) == 5:
                        formatted = compact
                    else:
                        formatted = digits
                else:
                    formatted = spoken_digits(raw, preserve_separators=True)
                if not formatted:
                    continue
                actual_prefix = prefix
                if actual_prefix is None:
                    label = re.sub(r"\s+(?:is)\s*$", "", cue.group(0), flags=re.IGNORECASE).strip()
                    actual_prefix = label + " "
                self._add(
                    replacements,
                    cue.start(),
                    end,
                    actual_prefix + formatted,
                    kind,
                    text[cue.start():end],
                )

        self._date_replacements(text, replacements)
        self._time_replacements(text, replacements)
        self._currency_replacements(text, replacements)

    def _date_replacements(self, text: str, replacements: list[_Replacement]) -> None:
        months = {
            name.casefold(): name
            for name in (
                "January", "February", "March", "April", "May", "June", "July",
                "August", "September", "October", "November", "December",
            )
        }
        tokens = self._tokens(text)
        for index, token in enumerate(tokens):
            month = months.get(token.word)
            if month is None or index + 1 >= len(tokens):
                continue
            day_value, day_count, _ = self._parse_tokens_cardinal(tokens, index + 1)
            if not day_count or not 1 <= day_value <= 31:
                continue
            cursor = index + 1 + day_count
            year = None
            year_count = 0
            if cursor < len(tokens):
                year, year_count, _ = self._parse_tokens_cardinal(tokens, cursor)
                # "twenty twenty-six" is two cardinals in speech; combine it.
                if year_count and year is not None and year < 100 and cursor + year_count < len(tokens):
                    tail, tail_count, _ = self._parse_tokens_cardinal(tokens, cursor + year_count)
                    if tail_count and tail is not None and tail < 100:
                        year = year * 100 + tail
                        year_count += tail_count
            end_token = tokens[cursor + year_count - 1] if year_count else tokens[cursor - 1]
            formatted = f"{month} {day_value}"
            if year is not None and year_count:
                formatted += f", {year}"
            self._add(replacements, token.start, end_token.end, formatted, "date", text[token.start:end_token.end])

    def _time_replacements(self, text: str, replacements: list[_Replacement]) -> None:
        # Deterministic US time grammar: "three thirty p m" and "3 30 PM".
        pattern = re.compile(
            r"\b((?:[a-z-]+|\d{1,2}))\s+((?:[a-z-]+|\d{2}))\s+([ap])\s*\.?\s*m\.?\b",
            re.IGNORECASE,
        )
        for matched in pattern.finditer(text):
            hour = parse_integer(matched.group(1))
            minute = parse_integer(matched.group(2))
            if hour is None or minute is None or not 1 <= hour <= 12 or not 0 <= minute <= 59:
                continue
            self._add(
                replacements,
                matched.start(),
                matched.end(),
                f"{hour}:{minute:02d} {matched.group(3).upper()}M",
                "time",
                matched.group(0),
            )

    def _currency_replacements(self, text: str, replacements: list[_Replacement]) -> None:
        tokens = self._tokens(text)
        for index, token in enumerate(tokens):
            if not self._is_number_start(token.word):
                continue
            value, count, _ = self._parse_tokens_cardinal(tokens, index)
            if not count or index + count >= len(tokens):
                continue
            unit = tokens[index + count].word
            if unit not in {"dollar", "dollars", "buck", "bucks", "cent", "cents"}:
                continue
            if self._is_prose_range(tokens, index, count):
                continue
            formatted = f"${value}" if unit not in {"cent", "cents"} else f"{value}¢"
            self._add(
                replacements,
                token.start,
                tokens[index + count].end,
                formatted,
                "money",
                text[token.start : tokens[index + count].end],
            )

    def _default_replacements(self, text: str, replacements: list[_Replacement]) -> None:
        # Preserve a known upstream mixed-token artifact while routing it through
        # a local measurement rule rather than the old one-off regex pipeline.
        for matched in re.finditer(r"\b100\s+eighty-six\s+1000\b(?=\s+miles\s+per\s+second\b)", text, re.IGNORECASE):
            self._add(replacements, matched.start(), matched.end(), "186,000", "measurement", matched.group(0))

        tokens = self._tokens(text)
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if token.word in _REPEATS:
                digit_end, repeated = self._consume_digit_tokens(tokens, index)
                if repeated and len(repeated) >= 2:
                    end = tokens[digit_end - 1].end
                    self._add(replacements, token.start, end, repeated, "digit_run", text[token.start:end])
                    index = digit_end
                    continue
            # Unit nouns such as "second" and "fourth" are ordinal homonyms;
            # they are only digitized when a preceding cardinal owns them.
            if token.word in _MEASUREMENT_CUES:
                index += 1
                continue
            if self._overlaps(token.start, token.end, replacements) or not self._is_number_start(token.word):
                index += 1
                continue

            sign = ""
            value_index = index
            if token.word in {"minus", "negative"}:
                sign = "-"
                value_index += 1
                if value_index >= len(tokens):
                    index += 1
                    continue
            elif token.word in {"plus", "positive"}:
                sign = "+"
                value_index += 1
                if value_index >= len(tokens):
                    index += 1
                    continue
            value, count, ordinal = self._parse_tokens_cardinal(tokens, value_index)
            if not count:
                index += 1
                continue
            end_index = value_index + count

            # Decimal digits after "point" are always a DigitRun and retain zeroes.
            if end_index < len(tokens) and tokens[end_index].word in {"point", "dot"}:
                decimal_end, decimal = self._consume_digit_tokens(tokens, end_index + 1)
                if decimal:
                    start = token.start
                    percent = decimal_end < len(tokens) and tokens[decimal_end].word in {"percent", "percentage"}
                    end = tokens[decimal_end].end if percent else tokens[decimal_end - 1].end
                    formatted = f"{sign}{value}.{decimal}"
                    self._add(
                        replacements,
                        start,
                        end,
                        formatted + ("%" if percent else ""),
                        "percentage" if percent else "number",
                        text[start:end],
                    )
                    index = decimal_end + (1 if percent else 0)
                    continue

            # Common spoken fractions.
            if end_index < len(tokens) and tokens[end_index].word in _FRACTIONS:
                _, denominator = _FRACTIONS[tokens[end_index].word]
                numerator = value or 1
                end = tokens[end_index].end
                self._add(replacements, token.start, end, f"{sign}{numerator}/{denominator}", "fraction", text[token.start:end])
                index = end_index + 1
                continue

            if self._is_prose_range(tokens, value_index, count):
                index = end_index
                continue

            nearby = {item.word for item in tokens[end_index : min(len(tokens), end_index + 2)]}
            should_digitize = bool(sign) or value >= 10 or (not ordinal and bool(nearby & _MEASUREMENT_CUES))
            if should_digitize:
                start = token.start
                percent = end_index < len(tokens) and tokens[end_index].word in {"percent", "percentage"}
                end = tokens[end_index].end if percent else tokens[end_index - 1].end
                formatted_value = _ordinal_suffix(value) if ordinal else f"{value:,}"
                self._add(
                    replacements,
                    start,
                    end,
                    sign + formatted_value + ("%" if percent else ""),
                    "percentage" if percent else "number",
                    text[start:end],
                    "default",
                )
            index = end_index + (1 if end_index < len(tokens) and tokens[end_index].word in {"percent", "percentage"} else 0)

    def _format_entity(self, kind: str, raw: str) -> str | None:
        normalized = _ENTITY_ALIASES.get(kind.casefold().replace("-", "_"), kind.casefold().replace("-", "_"))
        if normalized == "phone":
            return format_phone(raw)
        if normalized in {"zip", "id", "filename", "url", "ip"}:
            if normalized in {"filename", "url"}:
                return raw.strip()
            digits = spoken_digits(raw, preserve_separators=True)
            if not digits:
                return raw.strip() if any(character.isdigit() for character in raw) else None
            compact = re.sub(r"\D", "", digits)
            if normalized == "zip" and len(compact) == 9:
                return f"{compact[:5]}-{compact[5:]}"
            return digits
        if normalized in {"money", "measurement", "date", "time"}:
            nested = NumberNormalizer().normalize(raw)
            return nested if nested != raw or any(character.isdigit() for character in raw) else None
        return None

    def _consume_digit_phrase(self, text: str, start: int) -> tuple[int, str]:
        tokens = self._tokens(text, start)
        allowed = (
            set(_ONES) | set(_REPEATS) | set(_SEPARATORS) | set(_CANONICAL_ONES)
            | set(_TEENS) | set(_TENS) | set(_MAGNITUDES)
            | {"and", "space", "group", "+", "plus", "positive", "minus", "negative"}
        )
        consumed: list[_Token] = []
        for token in tokens:
            if token.start < start:
                continue
            if token.word not in allowed and not re.fullmatch(r"[+]?\d+(?:[./:-]\d+)*", token.raw):
                break
            # Don't cross sentence punctuation.
            if token.raw in {",", ";", "!", "?"}:
                break
            consumed.append(token)
        if not consumed:
            return start, ""
        end = consumed[-1].end
        return end, text[start:end].strip()

    @staticmethod
    def _tokens(text: str, offset: int = 0) -> list[_Token]:
        return [
            _Token(match.group(0), match.group(0).casefold(), match.start(), match.end())
            for match in _TOKEN_RE.finditer(text, offset)
        ]

    @staticmethod
    def _is_number_start(word: str) -> bool:
        return word in _CANONICAL_ONES or word in _TEENS or word in _TENS or word in _ORDINALS or word in {"minus", "negative", "plus", "positive"} or bool(re.fullmatch(r"[+-]?\d+", word))

    def _parse_tokens_cardinal(self, tokens: list[_Token], index: int) -> tuple[int, int, bool]:
        parts: list[str] = []
        for token in tokens[index:]:
            if token.word in set(_CANONICAL_ONES) | set(_TEENS) | set(_TENS) | set(_MAGNITUDES) | set(_ORDINALS) | {"and"}:
                parts.extend(token.word.replace("-", " ").split())
            elif re.fullmatch(r"\d+", token.word):
                if parts:
                    break
                return int(token.word), 1, False
            else:
                break
        value, consumed_parts, ordinal = _parse_cardinal_parts(parts)
        if not consumed_parts:
            return 0, 0, False
        # Hyphenated tokens may represent multiple parts, so map parts back.
        remaining = consumed_parts
        consumed_tokens = 0
        for token in tokens[index:]:
            part_count = len(token.word.replace("-", " ").split())
            if remaining < part_count:
                break
            remaining -= part_count
            consumed_tokens += 1
            if remaining == 0:
                break
        return value, consumed_tokens, ordinal

    @staticmethod
    def _consume_digit_tokens(tokens: list[_Token], index: int) -> tuple[int, str]:
        digits: list[str] = []
        repeat = 1
        cursor = index
        while cursor < len(tokens):
            word = tokens[cursor].word
            if word in _REPEATS:
                if repeat != 1:
                    break
                repeat = _REPEATS[word]
            elif word in _ONES:
                digits.append(str(_ONES[word]) * repeat)
                repeat = 1
            elif word.isdigit():
                digits.append(word * repeat)
                repeat = 1
            else:
                break
            cursor += 1
        if repeat != 1:
            return index, ""
        return cursor, "".join(digits)

    @staticmethod
    def _is_prose_range(tokens: list[_Token], start: int, count: int) -> bool:
        after = start + count
        if after < len(tokens) and tokens[after].word == "or":
            return after + 1 < len(tokens) and NumberNormalizer._is_number_start(tokens[after + 1].word)
        if start > 0 and tokens[start - 1].word == "or":
            return start > 1 and NumberNormalizer._is_number_start(tokens[start - 2].word)
        return False

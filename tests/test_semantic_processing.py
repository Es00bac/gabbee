from __future__ import annotations

import pytest

from gabbee.models import AppContext, ProcessingContext, SemanticSpan
from gabbee.text_processor import TextProcessor


@pytest.mark.parametrize(
    ("spoken", "expected"),
    [
        ("I have two dogs and twelve cats", "I have two dogs and 12 cats"),
        ("phone number three oh three five five five one two one two", "(303) 555-1212"),
        ("phone number one three oh three five five five one two one two", "+1 (303) 555-1212"),
        ("PIN zero zero five five", "PIN 0055"),
        ("ZIP code zero two one three eight dash one two three four", "ZIP 02138-1234"),
        ("version one point two point three", "version 1.2.3"),
        ("IP one nine two dot one six eight dot zero dot one port eight zero", "IP 192.168.0.1 port 80"),
        ("write as digits one two three", "123"),
        ("write as words 105", "one hundred five"),
        ("one or two dollars", "one or two dollars"),
        ("negative three point five percent", "-3.5%"),
        ("January fifth twenty twenty six", "January 5, 2026"),
        ("three thirty PM", "3:30 PM"),
        ("double five triple two", "55222"),
    ],
)
def test_acceptance_number_and_entity_formatting(spoken: str, expected: str) -> None:
    assert TextProcessor().process_transcript(spoken).text == expected


def test_spoken_punctuation_layout_and_literal_escape() -> None:
    processor = TextProcessor()
    assert processor.process_transcript("hello comma world period new paragraph yes").text == "hello, world.\n\nyes"
    assert processor.process_transcript("literal comma slash literal new line").text == "comma/new line"


def test_surrounding_text_controls_spacing_and_capitalization() -> None:
    processor = TextProcessor()
    context = ProcessingContext(
        app=AppContext(desktop_file_id="org.kde.kate"),
        surrounding_before="Hello",
        surrounding_after="world",
    )
    assert processor.process_transcript("there comma", context).text == " there,"
    sentence = ProcessingContext(
        app=AppContext(desktop_file_id="org.kde.kate"),
        surrounding_before="Done. ",
    )
    assert processor.process_transcript("next idea", sentence).text == "Next idea"


def test_terminal_profile_disables_prose_spacing() -> None:
    context = ProcessingContext(
        app=AppContext(desktop_file_id="org.kde.konsole"),
        surrounding_before="echo",
        terminal_mode=True,
    )
    assert TextProcessor().process_transcript("hello comma", context).text == "hello ,"


def test_invalid_provider_span_is_ignored_and_valid_span_has_priority() -> None:
    text = "call three oh three five five five one two one two"
    start = text.index("three")
    raw = text[start:]
    valid = SemanticSpan(start, len(text), "phone", raw, source="provider")
    invalid = SemanticSpan(0, 4, "phone", "wrong", source="provider")
    processed = TextProcessor().process_transcript(text, entity_spans=[invalid, valid])
    assert processed.text == "call (303) 555-1212"
    assert any(span.source == "provider" for span in processed.spans)


def test_unicode_survives_processing() -> None:
    assert TextProcessor().process_transcript("café comma naïve twelve").text == "café, naïve 12"

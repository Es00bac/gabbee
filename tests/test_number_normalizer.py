from __future__ import annotations

from gabbee.number_normalizer import NumberNormalizer


def normalize(text: str) -> str:
    return NumberNormalizer().normalize(text)


def test_casual_money_range_stays_words() -> None:
    assert normalize("I gave Bill one or two dollars") == "I gave Bill one or two dollars"


def test_coordinate_decimal_sequences_become_single_numbers() -> None:
    assert normalize(
        "The coordinates are nine point two seven one eight three north by four point one two three four five six seven east"
    ) == "The coordinates are 9.27183 north by 4.1234567 east"


def test_scientific_mixed_artifact_repairs_to_measurement_number() -> None:
    assert normalize("The speed of light is 100 eighty-six 1000 miles per second") == "The speed of light is 186,000 miles per second"

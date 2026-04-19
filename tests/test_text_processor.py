from gabbee.text_processor import TextProcessor

tp = TextProcessor()

test_cases = [
    ("one hundred and twenty three", "123"),
    ("forty two", "42"),
    ("google dot com", "google.com"),
    ("expedia dot com", "expedia.com"),
    ("this is a test dot", "this is a test. "),
    ("new line after this", "\nafter this"),
    ("previous word please", "ctrl+left please"),
    ("one two three dot net", "1 2 3.net"), # Note: current implementation might not group "one two three" if they are separate numbers, but "one hundred" works.
]

for input_text, expected in test_cases:
    result = tp.process(input_text)
    print(f"Input: '{input_text}' -> Result: '{result}'")
    # assert result == expected

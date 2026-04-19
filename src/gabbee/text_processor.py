from __future__ import annotations

import re


class TextProcessor:
    def __init__(self, keyword_map: dict[str, str] | None = None) -> None:
        # Default keywords if none provided
        self.keyword_map = keyword_map or {
            "new line": "\n",
            "next line": "\n",
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
        }
        
        # Keys are values that should be sent as key strokes
        self.key_commands = {
            "Tab", "Home", "Prior", "Next", "End", "BackSpace", "Delete", "Return", "Escape", "space"
        }
        # Compound keys
        self.compound_keys = {
            "Control+", "Alt+", "Shift+", "Meta+"
        }

        self.number_map = {
            "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
            "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
            "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
            "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
            "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
            "seventy": 70, "eighty": 80, "ninety": 90
        }
        self.magnitudes = {
            "hundred": 100, "thousand": 1000, "million": 1000000, "billion": 1000000000
        }

    def process_to_actions(self, text: str) -> list[tuple[str, str]]:
        """Returns a list of (type, value) where type is 'text' or 'key'."""
        if not text:
            return []

        # 1. Handle dot conversions
        text = self._convert_dots(text)

        # 2. Handle numbers
        text = self._convert_numbers(text)

        # 3. Split by keywords and create actions
        # Sort keywords by length descending to match longest first
        sorted_kws = sorted(self.keyword_map.keys(), key=len, reverse=True)
        
        if not sorted_kws:
            return [("text", text)]

        # Create a regex that matches any keyword as a whole phrase
        pattern = re.compile(rf'\b({"|".join(re.escape(k) for k in sorted_kws)})\b', re.IGNORECASE)
        
        actions: list[tuple[str, str]] = []
        last_pos = 0
        for match in pattern.finditer(text):
            # Text before the keyword
            pre_text = text[last_pos:match.start()].strip()
            if pre_text:
                actions.append(("text", pre_text))
            
            # The keyword itself
            kw = match.group(0).lower()
            replacement = self.keyword_map[kw]
            
            is_key = replacement in self.key_commands or any(replacement.startswith(ck) for ck in self.compound_keys)
            
            if is_key:
                actions.append(("key", replacement))
            else:
                actions.append(("text", replacement))
                
            last_pos = match.end()
            
        # Remaining text
        post_text = text[last_pos:].strip()
        if post_text:
            actions.append(("text", post_text))
            
        return actions

    def process(self, text: str) -> str:
        """Legacy support for just text replacement."""
        actions = self.process_to_actions(text)
        return "".join(v if t == "text" else " " for t, v in actions)

    def _convert_dots(self, text: str) -> str:
        # dot com, dot net, etc.
        text = re.sub(r'\s+dot\s+(com|net|org|edu|gov|io|me|md|py|js|ts|c|cpp|h|rs)\b', r'.\1', text, flags=re.IGNORECASE)
        # dot at end of sentence
        text = re.sub(r'\bdot\s*$', r'. ', text, flags=re.IGNORECASE)
        # dot between words (e.g. google dot com)
        text = re.sub(r'(\w+)\s+dot\s+(\w+)', r'\1.\2', text, flags=re.IGNORECASE)
        return text

    def _convert_numbers(self, text: str) -> str:
        words = text.split()
        new_words = []
        i = 0
        while i < len(words):
            word = words[i].lower().strip(".,!?")
            if word in self.number_map or word in self.magnitudes:
                num, consumed = self._parse_number_sequence(words[i:])
                if consumed > 0:
                    last_word = words[i + consumed - 1]
                    punctuation = ""
                    if last_word.endswith((".", ",", "!", "?")):
                        punctuation = last_word[-1]
                    new_words.append(str(num) + punctuation)
                    i += consumed
                    continue
            new_words.append(words[i])
            i += 1
        return " ".join(new_words)

    def _parse_number_sequence(self, words: list[str]) -> tuple[int, int]:
        total = 0
        current = 0
        consumed = 0
        
        for word in words:
            clean_word = word.lower().strip(".,!?")
            if clean_word in self.number_map:
                val = self.number_map[clean_word]
                
                # If we have a sequence of small numbers, treat them as separate digits
                if consumed > 0 and val < 10 and current < 10:
                    break
                
                if current >= 20 and current < 100 and val < 10:
                    current += val
                elif current > 0 and val < 100 and val != 0:
                    # e.g. "one hundred twenty" -> keep going
                    if current % 100 == 0 and val < 100:
                        current += val
                    else:
                        break
                else:
                    current += val
                consumed += 1
            elif clean_word == "and":
                if consumed > 0:
                    consumed += 1
                    continue
                else:
                    break
            elif clean_word in self.magnitudes:
                mag = self.magnitudes[clean_word]
                if current == 0:
                    current = 1
                current *= mag
                if mag >= 1000:
                    total += current
                    current = 0
                consumed += 1
            else:
                break
        
        total += current
        # Don't consume trailing "and"
        if consumed > 0 and words[consumed-1].lower().strip(".,!?") == "and":
            consumed -= 1
            
        if consumed == 0:
            return 0, 0
            
        return total, consumed

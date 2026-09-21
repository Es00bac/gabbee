from __future__ import annotations

import re

from .advanced_config import AdvancedConfig, ProfileEntry, VocabularyEntry
from .models import AppContext, ProcessingContext


SUPPORTED_ENTITY_HINTS = {
    "phone",
    "zip",
    "date",
    "time",
    "money",
    "measurement",
    "id",
    "url",
    "filename",
    "ip",
}


class ProfileManager:
    """Selects one app profile deterministically and manages manual override."""

    def __init__(self, config: AdvancedConfig) -> None:
        self.config = config
        self._manual_override: str | None = None

    @property
    def manual_override(self) -> str | None:
        return self._manual_override

    def set_manual_override(self, profile_name: str | None) -> None:
        if profile_name is None or not profile_name.strip():
            self._manual_override = None
            return
        profile = self.by_name(profile_name)
        if profile is None or not profile.enabled:
            raise ValueError(f"Unknown or disabled profile: {profile_name}")
        self._manual_override = profile.name

    def clear_manual_override(self) -> None:
        self._manual_override = None

    def by_name(self, name: str) -> ProfileEntry | None:
        normalized = name.casefold().strip()
        return next((item for item in self.config.profiles if item.name.casefold() == normalized), None)

    def select(self, app: AppContext | None) -> ProfileEntry | None:
        if self._manual_override:
            manual = self.by_name(self._manual_override)
            if manual is not None and manual.enabled:
                return manual
        if app is None:
            return None

        matches = [profile for profile in self.config.profiles if self._matches(profile, app)]
        if not matches:
            return None
        # Highest explicit priority wins. Desktop-ID matches outrank title-only
        # matches at the same priority; config order provides a stable tie-break.
        indexed = {id(profile): index for index, profile in enumerate(self.config.profiles)}
        return max(
            matches,
            key=lambda profile: (
                profile.priority,
                bool(profile.desktop_file_id),
                -indexed[id(profile)],
            ),
        )

    def _matches(self, profile: ProfileEntry, app: AppContext) -> bool:
        if not profile.enabled:
            return False
        if profile.desktop_file_id:
            if _normalize_desktop_id(profile.desktop_file_id) != _normalize_desktop_id(app.desktop_file_id):
                return False
        if profile.window_title_pattern:
            try:
                if re.search(profile.window_title_pattern, app.title, re.IGNORECASE) is None:
                    return False
            except re.error:
                return False
        # A profile with no matcher is a global/default profile. It participates
        # at its declared priority but loses a tie to an app-specific profile.
        return True

    def processing_context(self, app: AppContext | None, *, previous_text: str = "") -> ProcessingContext:
        profile = self.select(app)
        return ProcessingContext(
            previous_text=previous_text,
            surrounding_before=app.surrounding_before if app else "",
            surrounding_after=app.surrounding_after if app else "",
            app=app,
            profile_name=profile.name if profile else "",
            terminal_mode=profile.terminal_mode if profile else False,
            code_mode=profile.code_mode if profile else False,
        )

    def keyterms_for(self, profile: ProfileEntry | None, *, limit: int = 50) -> list[str]:
        if profile is None or not profile.keyterms_enabled:
            return []
        allowed = {item.casefold() for item in profile.vocabulary_spoken}
        entries: list[VocabularyEntry] = [
            item
            for item in self.config.vocabulary
            if item.enabled and item.keyterm and (not allowed or item.spoken.casefold() in allowed)
        ]
        entries.sort(key=lambda item: (-item.priority, item.spoken.casefold()))
        result: list[str] = []
        seen: set[str] = set()
        for entry in entries:
            # Written forms are what Scribe should be biased toward. Fall back
            # to the spoken form only if it differs solely by surrounding space.
            term = entry.written.strip() or entry.spoken.strip()
            folded = term.casefold()
            if not term or folded in seen:
                continue
            result.append(term)
            seen.add(folded)
            if len(result) >= min(50, max(0, limit)):
                break
        return result

    def entity_hints_for(self, profile: ProfileEntry | None) -> list[str]:
        if profile is None:
            return []
        return [
            item.casefold()
            for item in profile.entity_hints
            if item.casefold() in SUPPORTED_ENTITY_HINTS
        ]

    def command_available(self, spoken: str, profile: ProfileEntry | None) -> bool:
        if profile is None or not profile.command_spoken:
            return True
        return spoken.casefold().strip() in {item.casefold() for item in profile.command_spoken}

    def macro_available(self, name: str, profile: ProfileEntry | None) -> bool:
        if profile is None or not profile.macro_names:
            return True
        return name.casefold().strip() in {item.casefold() for item in profile.macro_names}


def _normalize_desktop_id(value: str) -> str:
    value = value.casefold().strip()
    return value[:-8] if value.endswith(".desktop") else value

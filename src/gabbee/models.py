from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from time import monotonic
from typing import Any, Literal, TypeAlias
from pathlib import Path


class ControllerState(str, Enum):
    IDLE = "idle"
    CONNECTING = "connecting"
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"
    DELIVERING = "delivering"
    RUNNING_MACRO = "running_macro"
    ERROR = "error"


@dataclass(slots=True, frozen=True)
class WordTiming:
    """A provider word (or character) timing in seconds."""

    text: str
    start: float
    end: float
    kind: str = "word"
    speaker_id: str | None = None


@dataclass(slots=True, frozen=True)
class SemanticSpan:
    """A validated semantic annotation over the raw transcript."""

    start: int
    end: int
    kind: str
    text: str = ""
    value: Any = None
    source: Literal["explicit", "provider", "local", "default"] = "local"
    confidence: float | None = None

    def is_valid_for(self, text: str) -> bool:
        if self.start < 0 or self.end <= self.start or self.end > len(text):
            return False
        return not self.text or text[self.start : self.end] == self.text


@dataclass(slots=True)
class TranscriptionResult:
    text: str
    provider: str
    language_code: str
    words: list[WordTiming] = field(default_factory=list)
    entities: list[SemanticSpan] = field(default_factory=list)
    session_id: str = ""


@dataclass(slots=True, frozen=True)
class Rect:
    x: float
    y: float
    width: float
    height: float

    @property
    def center(self) -> tuple[float, float]:
        return (self.x + self.width / 2, self.y + self.height / 2)

    @property
    def area(self) -> float:
        return max(0.0, self.width) * max(0.0, self.height)

    def contains(self, other: "Rect") -> bool:
        return (
            other.x >= self.x
            and other.y >= self.y
            and other.x + other.width <= self.x + self.width
            and other.y + other.height <= self.y + self.height
        )


@dataclass(slots=True, frozen=True)
class AppContext:
    """Stable-enough identity for the non-Gabbee target captured at key press."""

    desktop_file_id: str = ""
    title: str = ""
    window_id: str = ""
    pid: int | None = None
    focused_selector: str = ""
    focused_name: str = ""
    focused_role: str = ""
    surrounding_before: str = ""
    surrounding_after: str = ""
    rect: Rect | None = None
    captured_at: float = field(default_factory=monotonic)

    def same_target(self, other: "AppContext | None") -> bool:
        if other is None:
            return False
        if self.window_id and other.window_id:
            if self.window_id != other.window_id:
                return False
        if self.pid is not None and other.pid is not None and self.pid != other.pid:
            return False
        if self.desktop_file_id and other.desktop_file_id:
            if self.desktop_file_id != other.desktop_file_id:
                return False
        if self.focused_selector:
            return bool(other.focused_selector and self.focused_selector == other.focused_selector)
        if self.window_id and other.window_id:
            return True
        return bool(self.title and self.title == other.title)


@dataclass(slots=True, frozen=True)
class UiTarget:
    id: str
    name: str
    role: str
    rect: Rect
    actions: tuple[str, ...] = ()
    selector: str = ""
    description: str = ""
    enabled: bool = True
    visible: bool = True
    showing: bool = True
    depth: int = 0

    @property
    def actionable(self) -> bool:
        return bool(self.actions)


@dataclass(slots=True)
class ProcessingContext:
    previous_text: str = ""
    surrounding_before: str = ""
    surrounding_after: str = ""
    app: AppContext | None = None
    profile_name: str = ""
    prose: bool = True
    terminal_mode: bool = False
    code_mode: bool = False
    entity_spans: list[SemanticSpan] = field(default_factory=list)

    @property
    def prose_spacing(self) -> bool:
        return self.prose and not (self.terminal_mode or self.code_mode)


@dataclass(slots=True)
class ProcessedTranscript:
    raw_text: str
    text: str
    spans: list[SemanticSpan] = field(default_factory=list)
    profile_name: str = ""


@dataclass(slots=True)
class LastDictation:
    raw_text: str
    formatted_text: str
    app: AppContext | None = None
    audio_path: Path | None = None
    delivery_method: str = ""
    delivered: bool = False


@dataclass(slots=True)
class DeliveryResult:
    ok: bool
    method: str
    detail: str = ""
    target_verified: bool = False
    attempted_methods: tuple[str, ...] = ()
    # Set when the text may already be partially delivered to the target even
    # though the attempt failed.  Callers must not silently retry or fall back
    # to another route, which would duplicate the delivered prefix.
    uncertain: bool = False


# Desktop-only macro actions. These deliberately don't include a shell,
# process, eval, or arbitrary executable action.
@dataclass(slots=True, frozen=True)
class TypeTextAction:
    text: str
    continue_on_error: bool = False


@dataclass(slots=True, frozen=True)
class PressKeysAction:
    keys: str
    continue_on_error: bool = False


@dataclass(slots=True, frozen=True)
class WaitAction:
    seconds: float
    continue_on_error: bool = False


@dataclass(slots=True, frozen=True)
class WaitForTargetAction:
    target: str
    timeout: float = 5.0
    continue_on_error: bool = False


@dataclass(slots=True, frozen=True)
class ActivateAppAction:
    app: str
    continue_on_error: bool = False


@dataclass(slots=True, frozen=True)
class ActivateWindowAction:
    window: str
    continue_on_error: bool = False


@dataclass(slots=True, frozen=True)
class LaunchDesktopEntryAction:
    desktop_file_id: str
    continue_on_error: bool = False


@dataclass(slots=True, frozen=True)
class InvokeUiAction:
    target: str
    action: str = "click"
    continue_on_error: bool = False


@dataclass(slots=True, frozen=True)
class MovePointerAction:
    target: str = ""
    x: float | None = None
    y: float | None = None
    continue_on_error: bool = False


@dataclass(slots=True, frozen=True)
class ClickAction:
    target: str = ""
    button: Literal["left", "right", "middle"] = "left"
    count: int = 1
    continue_on_error: bool = False


@dataclass(slots=True, frozen=True)
class ScrollAction:
    direction: Literal["up", "down", "left", "right"]
    amount: int = 1
    continue_on_error: bool = False


@dataclass(slots=True, frozen=True)
class RepeatAction:
    actions: tuple["Action", ...]
    count: int
    continue_on_error: bool = False


@dataclass(slots=True, frozen=True)
class TargetExistsAction:
    target: str
    then_actions: tuple["Action", ...]
    else_actions: tuple["Action", ...] = ()
    continue_on_error: bool = False


Action: TypeAlias = (
    TypeTextAction
    | PressKeysAction
    | WaitAction
    | WaitForTargetAction
    | ActivateAppAction
    | ActivateWindowAction
    | LaunchDesktopEntryAction
    | InvokeUiAction
    | MovePointerAction
    | ClickAction
    | ScrollAction
    | RepeatAction
    | TargetExistsAction
)


@dataclass(slots=True)
class ControllerSnapshot:
    state: ControllerState
    provider: str
    delivery_method: str
    last_text: str = ""
    error_message: str = ""
    queue_depth: int = 0
    active_recording: bool = False
    partial_text: str = ""
    provider_status: str = ""
    target_app: str = ""
    active_profile: str = ""
    macro_progress: str = ""
    command_mode: bool = False
    retained_audio: bool = False

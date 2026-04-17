from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ControllerState(str, Enum):
    IDLE = "idle"
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"
    DELIVERING = "delivering"
    ERROR = "error"


@dataclass(slots=True)
class TranscriptionResult:
    text: str
    provider: str
    language_code: str


@dataclass(slots=True)
class DeliveryResult:
    ok: bool
    method: str
    detail: str = ""


@dataclass(slots=True)
class ControllerSnapshot:
    state: ControllerState
    provider: str
    delivery_method: str
    last_text: str = ""
    error_message: str = ""

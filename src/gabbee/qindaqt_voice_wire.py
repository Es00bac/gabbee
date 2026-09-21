"""org.qindaqt.Voice1 wire contract: constants, bounds and value mapping.

The QindaQt desktop owns this interface and specifies it in
``docs/wiki/architecture/voice-input.md`` in the QindaQt repository. Gabbee is
one implementation of it; the desktop's panel applet, its Settings page and its
Voice console all speak only this contract and never reach into Gabbee.

Contract summary, so this file can be maintained without the other repository:

* Payloads are ``a{sv}`` maps, never D-Bus structures, so a provider can be
  written in any language. Every key below is mandatory and its type is
  checked on the consumer side; an unexpected key makes the payload invalid.
* ``revision`` is the only ordering authority. It advances when, and only
  when, a caller-visible field changed, and it never goes backwards while this
  process owns the name.
* Every mutating method takes ``(requestId, expectedRevision)`` and answers a
  result map for that exact request. A caller that loses the answer must treat
  the request as uncertain and must not replay it.
* ``Level`` is unrevisioned, lossy capture telemetry. It never gates anything.
"""

from __future__ import annotations

import re
from typing import Any

from .models import ControllerState

SERVICE_NAME = "org.qindaqt.Voice1"
OBJECT_PATH = "/org/qindaqt/Voice1"
INTERFACE_NAME = "org.qindaqt.Voice1"

SCHEMA_VERSION = 1

# SessionState
STATE_UNKNOWN = 0
STATE_IDLE = 1
STATE_ARMING = 2
STATE_LISTENING = 3
STATE_TRANSCRIBING = 4
STATE_DELIVERING = 5
STATE_ERROR = 6

# CaptureMode
MODE_DICTATION = 0
MODE_COMMAND = 1

# DeliveryRoute
ROUTE_NONE = 0
ROUTE_INPUT_METHOD = 1
ROUTE_ACCESSIBILITY = 2
ROUTE_CLIPBOARD = 3
ROUTE_KEY_SYNTHESIS = 4

# OperationKind
KIND_START_DICTATION = 0
KIND_START_COMMAND = 1
KIND_FINISH = 2
KIND_CANCEL = 3
KIND_RETRY = 4
KIND_UNDO = 5
KIND_COPY_LAST = 6
KIND_SET_PROVIDER = 7
KIND_SET_ENABLED = 8

# OperationStatus
STATUS_SUCCEEDED = 0
STATUS_REJECTED = 1
STATUS_FAILED = 2
STATUS_UNCERTAIN = 3
STATUS_BUSY = 4

# Capability bits
CAP_COMMAND_MODE = 1 << 0
CAP_REALTIME_PARTIALS = 1 << 1
CAP_RETRY = 1 << 2
CAP_UNDO = 1 << 3
CAP_COPY = 1 << 4
CAP_PROVIDER_SELECTION = 1 << 5

MAX_TRANSCRIPT_UTF8_BYTES = 512
MAX_LABEL_UTF8_BYTES = 128
MAX_SHORTCUT_UTF8_BYTES = 64
MAX_IDENTIFIER_UTF8_BYTES = 64
MAX_REASON_UTF8_BYTES = 64
MAX_PROVIDERS = 12

_STATE_MAP = {
    ControllerState.IDLE: STATE_IDLE,
    ControllerState.CONNECTING: STATE_ARMING,
    ControllerState.RECORDING: STATE_LISTENING,
    ControllerState.TRANSCRIBING: STATE_TRANSCRIBING,
    ControllerState.DELIVERING: STATE_DELIVERING,
    ControllerState.RUNNING_MACRO: STATE_DELIVERING,
    ControllerState.ERROR: STATE_ERROR,
}

_CAPTURING_STATES = frozenset({STATE_ARMING, STATE_LISTENING, STATE_TRANSCRIBING})

# Gabbee's delivery router names its routes after the tool it used. The desktop
# only wants to know which *kind* of path placed the text.
_ROUTE_MAP = {
    "ibus": ROUTE_INPUT_METHOD,
    "ibus-preedit": ROUTE_INPUT_METHOD,
    "at-spi": ROUTE_ACCESSIBILITY,
    "clipboard": ROUTE_CLIPBOARD,
    "clipboard-paste": ROUTE_CLIPBOARD,
    "dotool": ROUTE_KEY_SYNTHESIS,
    "xdotool": ROUTE_KEY_SYNTHESIS,
    "type": ROUTE_KEY_SYNTHESIS,
    "key": ROUTE_KEY_SYNTHESIS,
    "agent-input": ROUTE_KEY_SYNTHESIS,
}

# Structured error taxonomy. The desktop turns each of these into a sentence it
# can translate; a message we cannot classify becomes "provider-error" rather
# than a slug made out of an English sentence.
_ERROR_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"api[ _-]?key|unauthori[sz]ed|authenticat|401|403", "authentication-failed"),
    (r"quota|rate[ _-]?limit|429|billing|credit", "quota-exceeded"),
    (r"websocket|network|connect|timed? ?out|timeout|dns|unreachable", "connection-failed"),
    (r"pw-record|microphone|audio device|no audio|capture", "microphone-unavailable"),
    (r"deliver|insert|ibus|clipboard|dotool|at-spi|focus", "delivery-failed"),
    (r"unsupported|not supported|no provider", "provider-unsupported"),
)

_IDENTIFIER_RE = re.compile(r"^[a-z0-9._-]+$")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_TRANSCRIPT_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def _truncate_utf8(value: str, limit: int) -> str:
    """Trim to ``limit`` UTF-8 bytes without splitting a character."""

    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value
    return encoded[:limit].decode("utf-8", errors="ignore")


def _display_text(value: Any, limit: int) -> str:
    text = _CONTROL_RE.sub("", str(value or ""))
    return _truncate_utf8(text, limit)


def _transcript_text(value: Any) -> str:
    """Bounded transcript text: spoken layout survives, escapes do not."""

    text = _TRANSCRIPT_CONTROL_RE.sub("", str(value or "")).replace("\r\n", "\n")
    return _truncate_utf8(text, MAX_TRANSCRIPT_UTF8_BYTES)


def _identifier(value: Any, fallback: str) -> str:
    text = str(value or "").strip().lower().replace(" ", "-")
    text = _truncate_utf8(text, MAX_IDENTIFIER_UTF8_BYTES)
    return text if _IDENTIFIER_RE.match(text) else fallback


def classify_error(message: str) -> str:
    """Map a provider message to the desktop's structured error taxonomy."""

    lowered = (message or "").lower()
    if not lowered.strip():
        return "provider-error"
    for pattern, code in _ERROR_PATTERNS:
        if re.search(pattern, lowered):
            return code
    return "provider-error"


def build_provider_inventory(config) -> list[tuple[str, str, bool]]:
    """Every provider the user could switch to, and whether it would work now.

    A provider that is installed but unconfigured is still listed: the desktop
    shows it greyed with a reason rather than hiding a capability the user
    paid for and forgot to finish setting up.
    """

    def has_elevenlabs() -> bool:
        return bool(
            getattr(config, "secret_api_key", None)
            or config.env_values.get("ELEVENLABS_API_KEY")
        )

    def has_gemini() -> bool:
        return bool(getattr(config, "gemini_api_key", None))

    def has_whisper_local() -> bool:
        try:
            import faster_whisper  # noqa: F401
        except Exception:
            return False
        return True

    inventory = [
        ("elevenlabs", "ElevenLabs Scribe", has_elevenlabs()),
        ("gemini", "Google Gemini", has_gemini()),
        ("whisper_local", "Whisper (on this computer)", has_whisper_local()),
    ]
    # AGENT-GUARD: the desktop rejects a snapshot whose current provider is
    # absent from the list, because a settings combo with no selection over a
    # running provider is worse than an extra row. Anything configured that we
    # did not enumerate is appended rather than dropped.
    current = _identifier(getattr(config, "stt_provider", ""), "")
    if current and all(entry[0] != current for entry in inventory):
        inventory.append((current, current, True))
    return inventory[:MAX_PROVIDERS]

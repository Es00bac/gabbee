from __future__ import annotations

from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import Callable, Protocol

from .app_paths import default_paths
from .ibus_client import IBusBridgeClient
from .models import AppContext, DeliveryResult


class TextSink(Protocol):
    def deliver(self, text: str) -> DeliveryResult:
        ...

    def deliver_key(self, key_stroke: str) -> DeliveryResult:
        ...


class IBusTextSink:
    def __init__(self, socket_path: Path | None = None) -> None:
        self.client = IBusBridgeClient(socket_path or default_paths().engine_socket)

    def deliver(self, text: str) -> DeliveryResult:
        reply = self.client.commit_text(text)
        return DeliveryResult(ok=reply.ok, method="ibus", detail=reply.detail)

    def deliver_key(self, key_stroke: str) -> DeliveryResult:
        # IBus bridge doesn't support keys yet, fallback or ignore
        return DeliveryResult(ok=False, method="ibus", detail="Key delivery not supported by IBus bridge.")

    def update_preedit(self, text: str) -> DeliveryResult:
        reply = self.client.update_preedit(text)
        return DeliveryResult(ok=reply.ok, method="ibus-preedit", detail=reply.detail)

    def clear_preedit(self) -> DeliveryResult:
        reply = self.client.clear_preedit()
        return DeliveryResult(ok=reply.ok, method="ibus-preedit", detail=reply.detail)


class ActiveWindowTextSink:
    def deliver(self, text: str) -> DeliveryResult:
        if shutil.which("dotool"):
            return self._deliver_with_dotool(text)
        if shutil.which("xdotool"):
            return self._deliver_with_xdotool(text)
        return DeliveryResult(ok=False, method="type", detail="No active-window typing tool is available.")

    def deliver_key(self, key_stroke: str) -> DeliveryResult:
        if re.fullmatch(r"[A-Za-z0-9_+-]+", key_stroke) is None:
            return DeliveryResult(False, "key", "Key chord contains unsupported characters.")
        if shutil.which("dotool"):
            subprocess.run(["dotool"], input=f"key {key_stroke}\n".encode("utf-8"), check=True)
            return DeliveryResult(ok=True, method="key", detail=f"Sent key {key_stroke} with dotool.")
        if shutil.which("xdotool"):
            subprocess.run(["xdotool", "key", key_stroke], check=True)
            return DeliveryResult(ok=True, method="key", detail=f"Sent key {key_stroke} with xdotool.")
        return DeliveryResult(ok=False, method="key", detail="No active-window typing tool is available.")

    def _deliver_with_dotool(self, text: str) -> DeliveryResult:
        actions = ["typedelay 1"]
        parts = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        for index, part in enumerate(parts):
            if part:
                actions.append(f"type {part}")
            if index < len(parts) - 1:
                actions.append("key enter")
        subprocess.run(
            ["dotool"],
            input=("\n".join(actions) + "\n").encode("utf-8"),
            check=True,
        )
        return DeliveryResult(ok=True, method="type", detail="Typed into the active window with dotool.")

    def _deliver_with_xdotool(self, text: str) -> DeliveryResult:
        subprocess.run(
            ["xdotool", "type", "--clearmodifiers", "--delay", "1", "--file", "-"],
            input=text.encode("utf-8"),
            check=True,
        )
        return DeliveryResult(ok=True, method="type", detail="Typed into the active window with xdotool.")


class ClipboardTextSink:
    def deliver(self, text: str) -> DeliveryResult:
        if shutil.which("wl-copy"):
            subprocess.run(["wl-copy"], input=text.encode("utf-8"), check=True)
            return DeliveryResult(ok=True, method="clipboard", detail="Copied with wl-copy.")
        if shutil.which("xclip"):
            subprocess.run(["xclip", "-selection", "clipboard"], input=text.encode("utf-8"), check=True)
            return DeliveryResult(ok=True, method="clipboard", detail="Copied with xclip.")
        return DeliveryResult(ok=False, method="clipboard", detail="No clipboard command is available.")

    def deliver_key(self, key_stroke: str) -> DeliveryResult:
        return DeliveryResult(ok=False, method="clipboard", detail="Clipboard sink does not support key delivery.")


class FallbackTextSink:
    def __init__(self, primary: TextSink, fallback: TextSink) -> None:
        self.primary = primary
        self.fallback = fallback

    def deliver(self, text: str) -> DeliveryResult:
        primary_result = self.primary.deliver(text)
        if primary_result.ok:
            return primary_result
        fallback_result = self.fallback.deliver(text)
        if fallback_result.ok:
            fallback_result.detail = f"{primary_result.detail} Fallback: {fallback_result.detail}".strip()
        return fallback_result

    def deliver_key(self, key_stroke: str) -> DeliveryResult:
        primary_result = self.primary.deliver_key(key_stroke)
        if primary_result.ok:
            return primary_result
        fallback_result = self.fallback.deliver_key(key_stroke)
        if fallback_result.ok:
            fallback_result.detail = f"{primary_result.detail} Fallback: {fallback_result.detail}".strip()
        return fallback_result


class MirroringTextSink:
    def __init__(self, primary: TextSink, mirror: TextSink) -> None:
        self.primary = primary
        self.mirror = mirror

    def deliver(self, text: str) -> DeliveryResult:
        primary_result = self.primary.deliver(text)
        if not primary_result.ok:
            return primary_result
        methods = set(primary_result.method.split("+"))
        if "clipboard" in methods:
            return primary_result
        mirror_result = self.mirror.deliver(text)
        if not mirror_result.ok:
            return primary_result
        return DeliveryResult(
            ok=True,
            method=f"{primary_result.method}+{mirror_result.method}",
            detail=f"{primary_result.detail} Mirrored: {mirror_result.detail}".strip(),
        )

    def deliver_key(self, key_stroke: str) -> DeliveryResult:
        return self.primary.deliver_key(key_stroke)


class ClipboardBackend(Protocol):
    def read(self) -> bytes | None:
        ...

    def write(self, value: bytes) -> bool:
        ...

    def clear(self) -> bool:
        ...


class SystemClipboard:
    """Small command-backed clipboard adapter that preserves exact UTF-8 bytes."""

    def read(self) -> bytes | None:
        try:
            if shutil.which("wl-paste"):
                result = subprocess.run(
                    ["wl-paste", "--no-newline"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
                return result.stdout if result.returncode == 0 else None
            if shutil.which("xclip"):
                result = subprocess.run(
                    ["xclip", "-selection", "clipboard", "-o"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
                return result.stdout if result.returncode == 0 else None
        except OSError:
            pass
        return None

    def write(self, value: bytes) -> bool:
        try:
            if shutil.which("wl-copy"):
                subprocess.run(["wl-copy"], input=value, check=True)
                return True
            if shutil.which("xclip"):
                subprocess.run(["xclip", "-selection", "clipboard"], input=value, check=True)
                return True
        except (OSError, subprocess.CalledProcessError):
            pass
        return False

    def clear(self) -> bool:
        try:
            if shutil.which("wl-copy"):
                subprocess.run(["wl-copy", "--clear"], check=True)
                return True
            return self.write(b"")
        except (OSError, subprocess.CalledProcessError):
            return False


class ClipboardPasteSink:
    """Paste fallback with restoration of the user's prior clipboard."""

    def __init__(
        self,
        key_sink: TextSink | None = None,
        clipboard: ClipboardBackend | None = None,
        *,
        restore_delay: float = 0.08,
    ) -> None:
        self.key_sink = key_sink or ActiveWindowTextSink()
        self.clipboard = clipboard or SystemClipboard()
        self.restore_delay = restore_delay

    def deliver(self, text: str) -> DeliveryResult:
        previous = self.clipboard.read()
        if not self.clipboard.write(text.encode("utf-8")):
            return DeliveryResult(False, "clipboard-paste", "Could not write the clipboard.")
        try:
            pasted = self.key_sink.deliver_key("Control+v")
            if not pasted.ok:
                return DeliveryResult(False, "clipboard-paste", f"Clipboard was set, but paste failed: {pasted.detail}")
            if self.restore_delay > 0:
                time.sleep(self.restore_delay)
            return DeliveryResult(True, "clipboard-paste", "Pasted and restored the prior clipboard.")
        finally:
            if previous is None:
                self.clipboard.clear()
            else:
                self.clipboard.write(previous)

    def deliver_key(self, key_stroke: str) -> DeliveryResult:
        return self.key_sink.deliver_key(key_stroke)


class TextDeliveryRouter:
    """Deliver text without replacing Gabbee's established typing behaviour.

    For an unchanged focused target, normal dictation uses the original
    active-window typing path first.  This matters on Wayland terminals and
    web views where a synthetic ``Ctrl+V`` has application-specific meaning.
    IBus, AT-SPI, and clipboard-paste remain recovery paths for a focused
    target that cannot be typed into directly.
    """

    def __init__(
        self,
        ibus: TextSink,
        accessibility,
        clipboard_paste: TextSink,
        typing: TextSink,
        *,
        current_context: Callable[[], AppContext | None] | None = None,
        activate_target: Callable[[AppContext], bool] | None = None,
        clipboard_mirror: TextSink | None = None,
        agent_input: TextSink | None = None,
        prefer_direct_typing: bool = True,
    ) -> None:
        self.ibus = ibus
        self.accessibility = accessibility
        self.clipboard_paste = clipboard_paste
        self.typing = typing
        self.current_context = current_context
        self.activate_target = activate_target
        self.clipboard_mirror = clipboard_mirror
        self.agent_input = agent_input
        self.prefer_direct_typing = prefer_direct_typing
        self.last_method = ""

    def deliver(self, text: str, target: AppContext | None = None) -> DeliveryResult:
        attempted: list[str] = []
        details: list[str] = []
        focused = self._target_is_focused(target)
        direct_typing_attempted = False

        # Preserve the long-standing Gabbee default: type directly into the
        # currently focused app.  Clipboard mirroring is intentionally a copy
        # only; it must never become a synthetic paste before direct typing.
        if self.prefer_direct_typing and (focused or target is None):
            direct_typing_attempted = True
            attempted.append("dotool")
            result = self._deliver_text(self.typing, text, "dotool")
            details.append(result.detail)
            if result.ok:
                return self._direct_typing_success(text, result, attempted, target is not None)

        # IBus owns the focused input context and therefore remains a safe
        # verifier when the compositor cannot expose a window ID (for example
        # LXQt on Labwc).  A successful commit proves that Gabbee's engine is
        # active in the current field; a failed commit simply continues down
        # the recovery chain.
        if focused or target is None:
            attempted.append("ibus")
            result = self._deliver_text(self.ibus, text, "ibus")
            details.append(result.detail)
            if result.ok:
                return self._success(result, attempted, target is not None)

        if target is not None:
            attempted.append("at-spi")
            try:
                result = self.accessibility.insert_text(text, target)
            except Exception as exc:
                result = DeliveryResult(False, "at-spi", str(exc))
            details.append(result.detail)
            if result.ok:
                return self._success(result, attempted, True)

        if not focused and target is not None and self.activate_target is not None:
            try:
                focused = bool(self.activate_target(target))
            except Exception:
                focused = False

        # Terminals commonly expose no EditableText AT-SPI node.  Use the
        # explicitly approved helper only for a focused terminal target
        # (including one focus was just restored to); the helper is opt-in
        # and never changes editor or clipboard routing.
        if self.agent_input is not None and self._is_terminal_target(target, focused):
            attempted.append("agent-input")
            result = self._deliver_text(self.agent_input, text, "agent-input")
            details.append(result.detail)
            if result.ok:
                return self._success(result, attempted, True)
            if result.uncertain:
                # The helper may already have delivered a prefix of the text.
                # Falling through to another route (or replaying) would
                # duplicate it, so stop here and let the user check the
                # terminal before any retry.
                return DeliveryResult(
                    False, "agent-input", result.detail, True, tuple(attempted), uncertain=True,
                )

        if focused or target is None:
            if not direct_typing_attempted:
                attempted.append("dotool")
                result = self._deliver_text(self.typing, text, "dotool")
                details.append(result.detail)
                if result.ok:
                    return self._direct_typing_success(text, result, attempted, focused and target is not None)

            attempted.append("clipboard-paste")
            result = self._deliver_text(self.clipboard_paste, text, "clipboard-paste")
            details.append(result.detail)
            if result.ok:
                return self._success(result, attempted, focused and target is not None)

        # Do not discard a completed transcript merely because Wayland/KWin
        # could not re-verify the original focused target.  When the user has
        # selected clipboard fallback, preserve the text there rather than
        # promoting a delivery-routing limitation to a transcription error.
        # We deliberately copy only here: without a verified target, sending
        # a synthetic paste could place text in a different application.
        if target is not None and self.clipboard_mirror is not None:
            attempted.append("clipboard")
            result = self._deliver_text(self.clipboard_mirror, text, "clipboard")
            details.append(result.detail)
            if result.ok:
                return self._success(
                    DeliveryResult(
                        True,
                        result.method,
                        "Target focus could not be verified; copied the transcript to the clipboard.",
                    ),
                    attempted,
                    False,
                )

        return DeliveryResult(
            False,
            "none",
            " ".join(item for item in details if item) or "Captured focus target could not be verified.",
            False,
            tuple(attempted),
        )

    def _direct_typing_success(
        self,
        text: str,
        result: DeliveryResult,
        attempted: list[str],
        verified: bool,
    ) -> DeliveryResult:
        """Return a direct-typing result and optionally mirror it as plain text."""

        if self.clipboard_mirror is None:
            return self._success(result, attempted, verified)
        mirror = self._deliver_text(self.clipboard_mirror, text, "clipboard")
        if not mirror.ok:
            return self._success(result, attempted, verified)
        combined = DeliveryResult(
            True,
            f"{result.method}+{mirror.method}",
            f"{result.detail} Mirrored: {mirror.detail}".strip(),
            result.target_verified,
        )
        return self._success(combined, attempted, verified)

    def deliver_key(self, key_stroke: str, target: AppContext | None = None) -> DeliveryResult:
        if target is not None and not self._target_is_focused(target):
            if self.activate_target is None or not self.activate_target(target):
                return DeliveryResult(False, "key", "Captured focus target could not be verified.")
        try:
            return self.typing.deliver_key(key_stroke)
        except Exception as exc:
            return DeliveryResult(False, "key", f"Key delivery failed: {exc}")

    def update_preedit(self, text: str, target: AppContext | None = None) -> DeliveryResult:
        if not self._target_is_focused(target):
            return DeliveryResult(False, "ibus-preedit", "Captured focus target is not focused.")
        updater = getattr(self.ibus, "update_preedit", None)
        if not callable(updater):
            return DeliveryResult(False, "ibus-preedit", "IBus preedit is unavailable.")
        return updater(text)

    def clear_preedit(self) -> DeliveryResult:
        clearer = getattr(self.ibus, "clear_preedit", None)
        if not callable(clearer):
            return DeliveryResult(False, "ibus-preedit", "IBus preedit is unavailable.")
        return clearer()

    def _target_is_focused(self, target: AppContext | None) -> bool:
        if target is None or self.current_context is None:
            return target is None
        try:
            return target.same_target(self.current_context())
        except Exception:
            return False

    @staticmethod
    def _is_terminal_target(target: AppContext | None, focused: bool) -> bool:
        if target is None or not focused:
            return False
        return "terminal" in (target.desktop_file_id + " " + target.focused_role).casefold()

    @staticmethod
    def _deliver_text(sink: TextSink, text: str, method: str) -> DeliveryResult:
        """Convert an optional desktop-tool failure into a recoverable route failure."""

        try:
            return sink.deliver(text)
        except Exception as exc:
            return DeliveryResult(False, method, f"{method} delivery failed: {exc}")

    def _success(self, result: DeliveryResult, attempted: list[str], verified: bool) -> DeliveryResult:
        self.last_method = result.method
        return DeliveryResult(
            True,
            result.method,
            result.detail,
            verified or result.target_verified,
            tuple(attempted),
        )

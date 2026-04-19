from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
from typing import Protocol

from .app_paths import default_paths
from .ibus_client import IBusBridgeClient
from .models import DeliveryResult


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


class ActiveWindowTextSink:
    def deliver(self, text: str) -> DeliveryResult:
        if shutil.which("dotool"):
            return self._deliver_with_dotool(text)
        if shutil.which("xdotool"):
            return self._deliver_with_xdotool(text)
        return DeliveryResult(ok=False, method="type", detail="No active-window typing tool is available.")

    def deliver_key(self, key_stroke: str) -> DeliveryResult:
        if shutil.which("dotool"):
            subprocess.run(["dotool"], input=f"key {key_stroke}\n".encode("utf-8"), check=True)
            return DeliveryResult(ok=True, method="key", detail=f"Sent key {key_stroke} with dotool.")
        if shutil.which("xdotool"):
            subprocess.run(["xdotool", "key", key_stroke], check=True)
            return DeliveryResult(ok=True, method="key", detail=f"Sent key {key_stroke} with xdotool.")
        return DeliveryResult(ok=False, method="key", detail="No active-window typing tool is available.")

    def _deliver_with_dotool(self, text: str) -> DeliveryResult:
        actions = ["typedelay 1"]
        parts = text.split("\n")
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

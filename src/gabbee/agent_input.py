"""Opt-in QindaQt portal keyboard delivery for focused terminal targets."""

from __future__ import annotations

import json
import os
import select
import shlex
import shutil
import subprocess
import time
import uuid

from .models import DeliveryResult


class AgentInputTextSink:
    """Keep one explicitly approved helper session for dictation text."""

    def __init__(self, command: str, *, timeout: float = 60.0) -> None:
        self.command = tuple(shlex.split(command))
        self.timeout = timeout
        self._process: subprocess.Popen[str] | None = None
        self._ready = False

    @classmethod
    def from_environment(cls) -> "AgentInputTextSink | None":
        command = os.environ.get("GABBEE_AGENT_INPUT_COMMAND", "").strip()
        if not command:
            command = shutil.which("qindaqt-agent-input") or ""
        return cls(command) if command else None

    def deliver(self, text: str) -> DeliveryResult:
        if not self._ensure_started():
            return DeliveryResult(False, "agent-input", "Agent input helper did not publish READY.")
        process = self._process
        if process is None or not self._ready or process.stdin is None:
            return DeliveryResult(False, "agent-input", "Agent input session is not ready.")
        if process.stdout is None:
            self.close()
            return DeliveryResult(False, "agent-input", "Agent input helper has no acknowledgement channel.")
        request_id = uuid.uuid4().hex
        try:
            process.stdin.write(json.dumps({
                "action": "text", "text": text, "requestId": request_id,
            }) + "\n")
            process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            # write() can hand the full line to the OS pipe buffer (and the
            # helper may already have read it) before a later buffered
            # flush() fails, so this is not provably pre-dispatch. Treat it
            # the same as a post-dispatch failure: uncertain, no fallback.
            self.close()
            return self._uncertain(request_id, f"the helper failed before confirming dispatch: {exc}")
        # Everything past this point is post-dispatch: the helper may have
        # delivered some or all of the text to the portal.  Any failure here
        # is uncertain and must not trigger an automatic retry or fallback.
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            if process.poll() is not None:
                self.close()
                return self._uncertain(request_id, "the helper exited after dispatch without acknowledging")
            readable, _, _ = select.select([process.stdout], [], [], 0.1)
            if not readable:
                continue
            try:
                reply = json.loads(process.stdout.readline())
            except (json.JSONDecodeError, OSError):
                continue
            if reply.get("requestId") != request_id:
                continue
            # The installed helper contract acknowledges with exactly
            # {"requestId": ..., "ok": true|false[, "error"]}; legacy "ack" or
            # "event" variants are not proof of portal acceptance.
            if reply.get("ok") is True:
                return DeliveryResult(True, "agent-input", f"Acknowledged request {request_id}.")
            error = str(reply.get("error") or f"unacceptable acknowledgement {reply!r}")
            return self._uncertain(request_id, f"the helper reported an error: {error}")
        return self._uncertain(request_id, "the helper did not acknowledge in time")

    @staticmethod
    def _uncertain(request_id: str, reason: str) -> DeliveryResult:
        return DeliveryResult(
            False,
            "agent-input",
            f"Agent input request {request_id} is uncertain: {reason}. "
            "Part of the text may already be delivered; check the terminal before retrying.",
            uncertain=True,
        )

    def deliver_key(self, key_stroke: str) -> DeliveryResult:
        return DeliveryResult(False, "agent-input", "Agent input helper does not provide key-chord delivery.")

    def _ensure_started(self) -> bool:
        if self._process is not None:
            if self._ready and self._process.poll() is None:
                return True
            # A previously started helper that exited (or never became
            # ready) must not wedge every future call into this branch;
            # release it so the next invocation can start a fresh helper.
            self.close()
        if not self.command:
            return False
        try:
            process = subprocess.Popen(
                self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, bufsize=1,
            )
        except OSError:
            return False
        self._process = process
        self._ready = False
        if process.stderr is None:
            self.close()
            return False
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            if process.poll() is not None:
                self.close()
                return False
            readable, _, _ = select.select([process.stderr], [], [], 0.1)
            if readable and "READY" in process.stderr.readline():
                self._ready = True
                return True
        self.close()
        return False

    def close(self) -> None:
        process, self._process = self._process, None
        self._ready = False
        if process is None or process.poll() is not None:
            return
        try:
            if process.stdin is not None:
                process.stdin.write(json.dumps({"action": "close"}) + "\n")
                process.stdin.close()
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            process.terminate()

    def __del__(self) -> None:
        self.close()

from __future__ import annotations

import shlex

from gabbee.models import AppContext, DeliveryResult
from gabbee.agent_input import AgentInputTextSink
from gabbee.output import ClipboardPasteSink, TextDeliveryRouter


class Sink:
    def __init__(self, method: str, ok: bool) -> None:
        self.method = method
        self.ok = ok
        self.texts: list[str] = []
        self.keys: list[str] = []

    def deliver(self, text: str) -> DeliveryResult:
        self.texts.append(text)
        return DeliveryResult(self.ok, self.method, self.method)

    def deliver_key(self, key: str) -> DeliveryResult:
        self.keys.append(key)
        return DeliveryResult(self.ok, self.method, self.method)


class Accessibility:
    def __init__(self, ok: bool) -> None:
        self.ok = ok
        self.calls: list[tuple[str, AppContext]] = []

    def insert_text(self, text: str, app: AppContext) -> DeliveryResult:
        self.calls.append((text, app))
        return DeliveryResult(self.ok, "at-spi", "at-spi")


class RaisingSink(Sink):
    def deliver(self, text: str) -> DeliveryResult:
        self.texts.append(text)
        raise RuntimeError("desktop injection unavailable")


def router(*, active: AppContext, captured: AppContext, ibus_ok=False, atspi_ok=False, paste_ok=False, type_ok=False):
    ibus = Sink("ibus", ibus_ok)
    accessibility = Accessibility(atspi_ok)
    paste = Sink("clipboard-paste", paste_ok)
    typing = Sink("dotool", type_ok)
    value = TextDeliveryRouter(
        ibus,
        accessibility,
        paste,
        typing,
        current_context=lambda: active,
        activate_target=lambda _target: True,
    )
    return value, ibus, accessibility, paste, typing


def test_focused_ibus_is_used_after_direct_typing_is_unavailable() -> None:
    captured = AppContext(window_id="one", focused_selector="editor")
    value, ibus, accessibility, paste, typing = router(
        active=captured, captured=captured, ibus_ok=True
    )
    result = value.deliver("hello\n世界", captured)
    assert result.ok and result.method == "ibus"
    assert result.attempted_methods == ("dotool", "ibus")
    assert ibus.texts == ["hello\n世界"]
    assert accessibility.calls == [] and paste.texts == [] and typing.texts == ["hello\n世界"]


def test_opt_in_agent_input_is_scoped_to_focused_terminal() -> None:
    target = AppContext(
        desktop_file_id="org.qindaqt.Terminal",
        window_id="terminal",
        focused_role="page tab",
    )
    value, _ibus, _accessibility, _paste, _typing = router(
        active=target, captured=target,
    )
    value.agent_input = AgentInputTextSink(
        "python3 -c 'import sys,json; print(\"READY\", file=sys.stderr, flush=True); r=json.loads(sys.stdin.readline()); print(json.dumps({\"requestId\":r[\"requestId\"],\"ok\":True}), flush=True); sys.stdin.read()'"
    )

    result = value.deliver("dictated terminal text", target)

    assert result.ok and result.method == "agent-input"
    assert result.attempted_methods == ("dotool", "ibus", "at-spi", "agent-input")


def test_agent_input_is_not_used_for_editor_targets() -> None:
    target = AppContext(
        desktop_file_id="org.qindaqt.TextEditor",
        window_id="editor",
        focused_role="text",
    )
    value, _ibus, _accessibility, paste, _typing = router(
        active=target, captured=target,
    )
    value.agent_input = AgentInputTextSink(
        "python3 -c 'import sys,json; print(\"READY\", file=sys.stderr, flush=True); r=json.loads(sys.stdin.readline()); print(json.dumps({\"requestId\":r[\"requestId\"],\"ok\":True}), flush=True); sys.stdin.read()'"
    )

    result = value.deliver("editor text", target)

    assert not result.ok
    assert "agent-input" not in result.attempted_methods


def test_agent_input_requires_ready_and_can_retry_after_timeout() -> None:
    sink = AgentInputTextSink("python3 -c 'import time; time.sleep(2)'", timeout=0.05)

    first = sink.deliver("blocked")
    assert not first.ok and "READY" in first.detail
    assert not first.uncertain

    sink.command = tuple(shlex.split(
        "python3 -c 'import sys,json; print(\"READY\", file=sys.stderr, flush=True); r=json.loads(sys.stdin.readline()); print(json.dumps({\"requestId\":r[\"requestId\"],\"ok\":True}), flush=True); sys.stdin.read()'"
    ))
    second = sink.deliver("accepted")
    assert second.ok and second.method == "agent-input"
    sink.close()


def test_agent_input_reports_helper_start_failure() -> None:
    sink = AgentInputTextSink("python3 -c 'raise SystemExit(7)'", timeout=1)

    result = sink.deliver("rejected")

    assert not result.ok and "READY" in result.detail


def test_agent_input_ignores_mismatched_requestid_and_reports_uncertain_timeout() -> None:
    sink = AgentInputTextSink(
        "python3 -c 'import sys,json; print(\"READY\", file=sys.stderr, flush=True); "
        "sys.stdin.readline(); "
        "print(json.dumps({\"requestId\": \"not-this-request\", \"ok\": True}), flush=True); "
        "sys.stdin.read()'",
        timeout=0.2,
    )

    result = sink.deliver("no matching ack")

    assert not result.ok
    assert result.uncertain
    assert "did not acknowledge in time" in result.detail
    assert "check the terminal before retrying" in result.detail
    sink.close()


def test_agent_input_only_accepts_exact_ok_true_reply() -> None:
    sink = AgentInputTextSink(
        "python3 -c 'import sys,json; print(\"READY\", file=sys.stderr, flush=True); "
        "r=json.loads(sys.stdin.readline()); "
        "print(json.dumps({\"requestId\":r[\"requestId\"],\"ack\":True}), flush=True); "
        "sys.stdin.read()'",
        timeout=1,
    )

    result = sink.deliver("legacy ack variant")

    assert not result.ok
    assert result.uncertain
    assert "unacceptable acknowledgement" in result.detail
    sink.close()


def test_agent_input_propagates_matching_acknowledgement_error_as_uncertain() -> None:
    sink = AgentInputTextSink(
        "python3 -c 'import sys,json; print(\"READY\", file=sys.stderr, flush=True); r=json.loads(sys.stdin.readline()); print(json.dumps({\"requestId\":r[\"requestId\"],\"error\":\"denied\"}), flush=True); sys.stdin.read()'",
        timeout=1,
    )

    result = sink.deliver("denied")

    assert not result.ok
    assert result.uncertain
    assert "denied" in result.detail
    assert "check the terminal before retrying" in result.detail


def test_uncertain_agent_input_delivery_does_not_fall_back() -> None:
    # A prefix of the text may already be sitting in the terminal, so the
    # router must stop at agent-input rather than retrying via clipboard.
    target = AppContext(
        desktop_file_id="org.qindaqt.Terminal",
        window_id="terminal",
        focused_role="page tab",
    )
    value, _ibus, _accessibility, _paste, _typing = router(active=target, captured=target)
    mirror = Sink("clipboard", True)
    value.clipboard_mirror = mirror
    value.agent_input = AgentInputTextSink(
        "python3 -c 'import sys; print(\"READY\", file=sys.stderr, flush=True); sys.stdin.readline()'",
        timeout=0.2,
    )

    result = value.deliver("dictated terminal text", target)

    assert not result.ok
    assert result.uncertain
    assert result.method == "agent-input"
    assert result.target_verified
    assert result.attempted_methods == ("dotool", "ibus", "at-spi", "agent-input")
    assert mirror.texts == []
    value.agent_input.close()


def test_agent_input_used_after_focus_restored_to_originally_unfocused_terminal() -> None:
    # If the terminal wasn't the active window at capture time, the router
    # must restore focus via activate_target before trying the terminal
    # helper, rather than skipping it just because it started unfocused.
    captured = AppContext(
        desktop_file_id="org.qindaqt.Terminal",
        window_id="terminal",
        focused_role="page tab",
    )
    active = AppContext(window_id="two", focused_selector="other")
    ibus = Sink("ibus", False)
    accessibility = Accessibility(False)
    paste = Sink("clipboard-paste", False)
    typing = Sink("dotool", False)
    activated: list[AppContext] = []

    def activate(target: AppContext) -> bool:
        activated.append(target)
        return True

    value = TextDeliveryRouter(
        ibus,
        accessibility,
        paste,
        typing,
        current_context=lambda: active,
        activate_target=activate,
    )
    value.agent_input = AgentInputTextSink(
        "python3 -c 'import sys,json; print(\"READY\", file=sys.stderr, flush=True); r=json.loads(sys.stdin.readline()); print(json.dumps({\"requestId\":r[\"requestId\"],\"ok\":True}), flush=True); sys.stdin.read()'"
    )

    result = value.deliver("dictated terminal text", captured)

    assert result.ok and result.method == "agent-input"
    assert activated == [captured]
    assert result.attempted_methods == ("at-spi", "agent-input")


def test_no_helper_used_when_activate_target_fails_to_restore_focus() -> None:
    # If focus restoration itself fails, the terminal helper must not run
    # against a target that still is not actually focused.
    captured = AppContext(
        desktop_file_id="org.qindaqt.Terminal",
        window_id="terminal",
        focused_role="page tab",
    )
    active = AppContext(window_id="two", focused_selector="other")
    ibus = Sink("ibus", False)
    accessibility = Accessibility(False)
    paste = Sink("clipboard-paste", False)
    typing = Sink("dotool", False)
    value = TextDeliveryRouter(
        ibus,
        accessibility,
        paste,
        typing,
        current_context=lambda: active,
        activate_target=lambda _target: False,
    )
    mirror = Sink("clipboard", True)
    value.clipboard_mirror = mirror
    value.agent_input = AgentInputTextSink(
        "python3 -c 'import sys,json; print(\"READY\", file=sys.stderr, flush=True); r=json.loads(sys.stdin.readline()); print(json.dumps({\"requestId\":r[\"requestId\"],\"ok\":True}), flush=True); sys.stdin.read()'"
    )

    result = value.deliver("dictated terminal text", captured)

    assert "agent-input" not in result.attempted_methods
    assert result.attempted_methods == ("at-spi", "clipboard")
    assert mirror.texts == ["dictated terminal text"]


def test_uncertain_helper_after_focus_restore_does_not_auto_fallback() -> None:
    # Once focus is restored and the helper reply is ambiguous, the router
    # must stop rather than risk duplicating a partially delivered prefix.
    captured = AppContext(
        desktop_file_id="org.qindaqt.Terminal",
        window_id="terminal",
        focused_role="page tab",
    )
    active = AppContext(window_id="two", focused_selector="other")
    ibus = Sink("ibus", False)
    accessibility = Accessibility(False)
    paste = Sink("clipboard-paste", False)
    typing = Sink("dotool", False)
    value = TextDeliveryRouter(
        ibus,
        accessibility,
        paste,
        typing,
        current_context=lambda: active,
        activate_target=lambda _target: True,
    )
    mirror = Sink("clipboard", True)
    value.clipboard_mirror = mirror
    value.agent_input = AgentInputTextSink(
        "python3 -c 'import sys; print(\"READY\", file=sys.stderr, flush=True); sys.stdin.readline()'",
        timeout=0.2,
    )

    result = value.deliver("dictated terminal text", captured)

    assert not result.ok
    assert result.uncertain
    assert result.method == "agent-input"
    assert result.attempted_methods == ("at-spi", "agent-input")
    assert mirror.texts == []
    value.agent_input.close()


def test_clean_agent_input_startup_failure_still_allows_fallback() -> None:
    # A helper that never publishes READY has not dispatched anything, so the
    # router may safely continue down the recovery chain.
    target = AppContext(
        desktop_file_id="org.qindaqt.Terminal",
        window_id="terminal",
        focused_role="page tab",
    )
    value, _ibus, _accessibility, _paste, _typing = router(active=target, captured=target)
    mirror = Sink("clipboard", True)
    value.clipboard_mirror = mirror
    value.agent_input = AgentInputTextSink("python3 -c 'raise SystemExit(7)'", timeout=1)

    result = value.deliver("dictated terminal text", target)

    assert result.ok
    assert not result.uncertain
    assert result.method == "clipboard"
    assert result.attempted_methods == ("dotool", "ibus", "at-spi", "agent-input", "clipboard-paste", "clipboard")
    assert mirror.texts == ["dictated terminal text"]


def test_agent_input_write_failure_is_uncertain_and_router_does_not_fall_back() -> None:
    # A write/flush failure can happen after the OS pipe buffer already
    # handed the full line to the helper, so it is not provably a
    # pre-dispatch failure and must not be treated as safe to fall back
    # from. Force the failure deterministically (a real race on stdin
    # closure is not reliable to reproduce in a test).
    target = AppContext(
        desktop_file_id="org.qindaqt.Terminal",
        window_id="terminal",
        focused_role="page tab",
    )
    value, _ibus, _accessibility, _paste, _typing = router(active=target, captured=target)
    mirror = Sink("clipboard", True)
    value.clipboard_mirror = mirror
    sink = AgentInputTextSink(
        "python3 -c 'import sys,time; print(\"READY\", file=sys.stderr, flush=True); time.sleep(2)'",
        timeout=1,
    )
    value.agent_input = sink
    assert sink._ensure_started()
    process = sink._process
    assert process is not None and process.stdin is not None

    def _raise_broken_pipe(_data: str) -> int:
        raise BrokenPipeError()

    process.stdin.write = _raise_broken_pipe  # type: ignore[method-assign]

    result = value.deliver("dictated terminal text", target)

    assert not result.ok
    assert result.uncertain
    assert result.method == "agent-input"
    assert "before confirming dispatch" in result.detail
    assert "check the terminal before retrying" in result.detail
    assert result.attempted_methods == ("dotool", "ibus", "at-spi", "agent-input")
    assert mirror.texts == []
    process.kill()
    process.wait(timeout=2)


def test_agent_input_restarts_after_helper_exits_between_deliveries() -> None:
    # A helper that becomes READY, is used successfully, and then exits on
    # its own must not wedge the sink: the next deliver() should detect the
    # dead process and start a fresh helper rather than returning False
    # forever.
    script = (
        "python3 -c 'import sys,json; print(\"READY\", file=sys.stderr, flush=True); "
        "r=json.loads(sys.stdin.readline()); "
        "print(json.dumps({\"requestId\":r[\"requestId\"],\"ok\":True}), flush=True)'"
    )
    sink = AgentInputTextSink(script, timeout=1)

    first = sink.deliver("first")
    assert first.ok and first.method == "agent-input"

    process = sink._process
    assert process is not None
    process.wait(timeout=1)  # the helper exits on its own after replying

    second = sink.deliver("second")
    assert second.ok and second.method == "agent-input"
    sink.close()


def test_ibus_verifies_focus_without_a_compositor_window_api() -> None:
    ibus = Sink("ibus", True)
    accessibility = Accessibility(False)
    paste = Sink("clipboard-paste", False)
    typing = Sink("dotool", False)
    value = TextDeliveryRouter(ibus, accessibility, paste, typing)

    result = value.deliver("hello from Labwc", None)

    assert result.ok and result.method == "ibus"
    assert result.attempted_methods == ("dotool", "ibus")
    assert typing.texts == ["hello from Labwc"]
    assert ibus.texts == ["hello from Labwc"]
    assert accessibility.calls == [] and paste.texts == []


def test_router_preserves_direct_typing_as_the_normal_delivery_path() -> None:
    captured = AppContext(window_id="one", focused_selector="editor")
    value, _ibus, _accessibility, _paste, _typing = router(
        active=captured,
        captured=captured,
        ibus_ok=False,
        atspi_ok=False,
        paste_ok=False,
        type_ok=True,
    )
    result = value.deliver("hello", captured)
    assert result.ok and result.method == "dotool"
    assert result.attempted_methods == ("dotool",)


def test_direct_typing_mirrors_plain_text_without_synthesizing_a_paste() -> None:
    captured = AppContext(window_id="one", focused_selector="editor")
    value, _ibus, _accessibility, paste, typing = router(
        active=captured,
        captured=captured,
        type_ok=True,
    )
    mirror = Sink("clipboard", True)
    value.clipboard_mirror = mirror

    result = value.deliver("plain dictated text", captured)

    assert result.ok and result.method == "dotool+clipboard"
    assert result.attempted_methods == ("dotool",)
    assert typing.texts == ["plain dictated text"]
    assert mirror.texts == ["plain dictated text"]
    assert paste.texts == []


def test_focus_change_skips_ibus_but_uses_captured_accessible() -> None:
    captured = AppContext(window_id="one", focused_selector="editor")
    active = AppContext(window_id="two", focused_selector="other")
    value, ibus, accessibility, paste, typing = router(
        active=active,
        captured=captured,
        ibus_ok=True,
        atspi_ok=True,
    )
    result = value.deliver("safe target", captured)
    assert result.ok and result.method == "at-spi"
    assert result.attempted_methods == ("at-spi",)
    assert ibus.texts == [] and accessibility.calls == [("safe target", captured)]
    assert paste.texts == [] and typing.texts == []


def test_control_change_inside_same_window_is_not_treated_as_focused() -> None:
    captured = AppContext(window_id="one", focused_selector="editor-one")
    active = AppContext(window_id="one", focused_selector="editor-two")
    value, ibus, accessibility, _paste, _typing = router(
        active=active,
        captured=captured,
        ibus_ok=True,
        atspi_ok=True,
    )
    result = value.deliver("captured", captured)
    assert result.method == "at-spi"
    assert ibus.texts == []
    assert accessibility.calls == [("captured", captured)]


def test_failed_direct_typing_continues_to_ibus() -> None:
    captured = AppContext(window_id="one", focused_selector="editor")
    value, ibus, _accessibility, _paste, _typing = router(
        active=captured,
        captured=captured,
        ibus_ok=True,
    )
    raising = RaisingSink("dotool", False)
    value.typing = raising

    result = value.deliver("still deliver this", captured)

    assert result.ok and result.method == "ibus"
    assert result.attempted_methods == ("dotool", "ibus")
    assert raising.texts == ["still deliver this"]
    assert ibus.texts == ["still deliver this"]


def test_unverified_target_preserves_text_in_configured_clipboard_fallback() -> None:
    captured = AppContext(window_id="one", focused_selector="editor")
    active = AppContext(window_id="two", focused_selector="other")
    value, _ibus, accessibility, _paste, typing = router(
        active=active,
        captured=captured,
        atspi_ok=False,
        type_ok=True,
    )
    value.activate_target = lambda _target: False
    mirror = Sink("clipboard", True)
    value.clipboard_mirror = mirror

    result = value.deliver("do not lose this", captured)

    assert result.ok and result.method == "clipboard"
    assert result.target_verified is False
    assert result.attempted_methods == ("at-spi", "clipboard")
    assert "copied the transcript" in result.detail
    assert accessibility.calls == [("do not lose this", captured)]
    assert typing.texts == []
    assert mirror.texts == ["do not lose this"]


class MemoryClipboard:
    def __init__(self, value: bytes | None) -> None:
        self.value = value
        self.writes: list[bytes] = []

    def read(self) -> bytes | None:
        return self.value

    def write(self, value: bytes) -> bool:
        self.value = value
        self.writes.append(value)
        return True

    def clear(self) -> bool:
        self.value = None
        return True


def test_clipboard_paste_restores_previous_clipboard() -> None:
    clipboard = MemoryClipboard(b"private prior value")
    key_sink = Sink("key", True)
    sink = ClipboardPasteSink(key_sink, clipboard, restore_delay=0)
    result = sink.deliver("dictated text")
    assert result.ok
    assert key_sink.keys == ["Control+v"]
    assert clipboard.writes == [b"dictated text", b"private prior value"]
    assert clipboard.value == b"private prior value"

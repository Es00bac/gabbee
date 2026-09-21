from __future__ import annotations

import threading
import time

from gabbee.macro_runtime import ActionResult, MacroRunner
from gabbee.models import RepeatAction, TypeTextAction, WaitAction


class RecordingExecutor:
    def __init__(self, fail_on: str = "") -> None:
        self.fail_on = fail_on
        self.values: list[str] = []

    def execute(self, action, cancel_event):
        if isinstance(action, WaitAction):
            cancelled = cancel_event.wait(action.seconds)
            return ActionResult(not cancelled, "cancelled" if cancelled else "wait")
        value = getattr(action, "text", type(action).__name__)
        self.values.append(value)
        return ActionResult(value != self.fail_on, "failed" if value == self.fail_on else "ok")


def test_macro_stops_on_first_failure() -> None:
    executor = RecordingExecutor(fail_on="bad")
    result = MacroRunner(executor).run(
        [TypeTextAction("one"), TypeTextAction("bad"), TypeTextAction("never")]
    )
    assert not result.ok
    assert executor.values == ["one", "bad"]


def test_step_can_explicitly_continue_after_failure() -> None:
    executor = RecordingExecutor(fail_on="bad")
    result = MacroRunner(executor).run(
        [TypeTextAction("bad", continue_on_error=True), TypeTextAction("after")]
    )
    assert result.ok
    assert executor.values == ["bad", "after"]


def test_bounded_repeat_runs_deterministically() -> None:
    executor = RecordingExecutor()
    result = MacroRunner(executor).run([RepeatAction((TypeTextAction("again"),), 3)])
    assert result.ok
    assert executor.values == ["again", "again", "again"]
    assert result.completed_steps == 3


def test_cancel_interrupts_wait_and_aborts_remaining_steps() -> None:
    executor = RecordingExecutor()
    runner = MacroRunner(executor)
    thread = runner.run_async([WaitAction(5), TypeTextAction("never")])
    deadline = time.monotonic() + 1
    while not runner.is_running and time.monotonic() < deadline:
        time.sleep(0.005)
    runner.cancel()
    thread.join(1)
    assert not thread.is_alive()
    assert runner.last_result is not None and runner.last_result.cancelled
    assert executor.values == []

from __future__ import annotations

from dataclasses import dataclass, replace
import threading
from time import monotonic
from typing import Callable, Protocol

from .advanced_config import AdvancedConfig, CommandAction, MacroEntry, MacroStep
from .command_patterns import CommandMatch, compile_patterns, match_patterns
from .models import (
    Action,
    ActivateAppAction,
    ActivateWindowAction,
    ClickAction,
    InvokeUiAction,
    LaunchDesktopEntryAction,
    MovePointerAction,
    PressKeysAction,
    RepeatAction,
    ScrollAction,
    TargetExistsAction,
    TypeTextAction,
    WaitAction,
    WaitForTargetAction,
)


MAX_REPEAT = 100
MAX_WAIT_SECONDS = 300.0


@dataclass(slots=True, frozen=True)
class ActionResult:
    ok: bool
    detail: str = ""
    method: str = ""


@dataclass(slots=True)
class MacroResult:
    ok: bool
    completed_steps: int
    total_steps: int
    detail: str = ""
    cancelled: bool = False
    elapsed_seconds: float = 0.0


class DesktopActionExecutor(Protocol):
    def execute(self, action: Action, cancel_event: threading.Event) -> ActionResult:
        ...


class AmbiguousCommandError(RuntimeError):
    def __init__(self, matches: list[CommandMatch]) -> None:
        super().__init__("More than one saved command pattern matched.")
        self.matches = matches


class MacroRunner:
    """Runs validated desktop-only actions, stopping on the first hard failure."""

    def __init__(
        self,
        executor: DesktopActionExecutor,
        *,
        on_progress: Callable[[int, int, str], None] | None = None,
    ) -> None:
        self.executor = executor
        self.on_progress = on_progress or (lambda _done, _total, _label: None)
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._running = False
        self.last_result: MacroResult | None = None

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._running or (self._thread is not None and self._thread.is_alive())

    def cancel(self) -> None:
        self._cancel.set()

    def run(self, actions: list[Action] | tuple[Action, ...]) -> MacroResult:
        with self._lock:
            if self._running:
                raise RuntimeError("A macro is already running.")
            self._running = True
        try:
            return self._run_impl(actions)
        finally:
            with self._lock:
                self._running = False

    def _run_impl(self, actions: list[Action] | tuple[Action, ...]) -> MacroResult:
        self._cancel.clear()
        started = monotonic()
        flattened_total = _action_count(actions)
        completed = 0

        def execute_many(items: list[Action] | tuple[Action, ...]) -> ActionResult:
            nonlocal completed
            for action in items:
                if self._cancel.is_set():
                    return ActionResult(False, "Macro cancelled.", "cancel")
                label = type(action).__name__.removesuffix("Action")
                self.on_progress(completed, flattened_total, label)
                if isinstance(action, RepeatAction):
                    if not 1 <= action.count <= MAX_REPEAT:
                        result = ActionResult(False, f"Repeat count must be between 1 and {MAX_REPEAT}.")
                    else:
                        result = ActionResult(True)
                        for _ in range(action.count):
                            result = execute_many(action.actions)
                            if not result.ok:
                                break
                elif isinstance(action, TargetExistsAction):
                    # The executor resolves the predicate and reports its result
                    # via a target_exists method when available.
                    checker = getattr(self.executor, "target_exists", None)
                    exists = bool(checker(action.target)) if callable(checker) else False
                    result = execute_many(action.then_actions if exists else action.else_actions)
                else:
                    result = self.executor.execute(action, self._cancel)
                    if result.ok:
                        completed += 1
                if not result.ok and not action.continue_on_error:
                    return result
            return ActionResult(True, "Macro completed.")

        outcome = execute_many(actions)
        cancelled = self._cancel.is_set()
        result = MacroResult(
            ok=outcome.ok and not cancelled,
            completed_steps=completed,
            total_steps=flattened_total,
            detail="Macro cancelled." if cancelled else outcome.detail,
            cancelled=cancelled,
            elapsed_seconds=monotonic() - started,
        )
        self.last_result = result
        self.on_progress(completed, flattened_total, result.detail)
        return result

    def run_async(
        self,
        actions: list[Action] | tuple[Action, ...],
        *,
        on_complete: Callable[[MacroResult], None] | None = None,
    ) -> threading.Thread:
        if self.is_running:
            raise RuntimeError("A macro is already running.")

        def worker() -> None:
            result = self.run(actions)
            if on_complete:
                on_complete(result)

        thread = threading.Thread(target=worker, daemon=True, name="gabbee-macro")
        with self._lock:
            self._thread = thread
        thread.start()
        return thread


class CommandDispatcher:
    def __init__(self, config: AdvancedConfig, runner: MacroRunner) -> None:
        self.config = config
        self.runner = runner
        self._commands = compile_patterns(config.commands)
        self._macros = compile_patterns(config.macros)

    def what_can_i_say(self, profile_name: str = "") -> list[str]:
        folded = profile_name.casefold()
        values = [
            item.spoken
            for item in (*self.config.commands, *self.config.macros)
            if item.enabled and (not item.profiles or folded in {name.casefold() for name in item.profiles})
        ]
        return sorted(values, key=str.casefold)

    def resolve(self, text: str, profile_name: str = "") -> CommandMatch | None:
        folded = profile_name.casefold()
        matches = [
            match
            for match in (*match_patterns(text, self._commands), *match_patterns(text, self._macros))
            if not match.entry.profiles or folded in {name.casefold() for name in match.entry.profiles}
        ]
        if len(matches) > 1:
            raise AmbiguousCommandError(matches)
        return matches[0] if matches else None

    def dispatch(self, text: str, profile_name: str = "", *, dry_run: bool = False) -> MacroResult | list[Action] | None:
        matched = self.resolve(text, profile_name)
        if matched is None:
            return None
        if isinstance(matched.entry, MacroEntry):
            actions = [action_from_step(step, matched) for step in matched.entry.steps]
        else:
            action = matched.entry.action
            if action.type == "macro":
                macro = next(
                    (
                        item
                        for item in self.config.macros
                        if item.enabled and item.name.casefold() == action.macro.casefold()
                    ),
                    None,
                )
                if macro is None:
                    return MacroResult(False, 0, 0, f"Unknown macro: {action.macro}")
                actions = [action_from_step(step, matched) for step in macro.steps]
            else:
                actions = [action_from_command(action, matched)]
        if dry_run:
            return actions
        return self.runner.run(actions)


def _render(value: str, matched: CommandMatch | None) -> str:
    return matched.render(value) if matched is not None else value


def _render_int(value: int | str, matched: CommandMatch | None, field: str) -> int:
    rendered = _render(str(value), matched)
    try:
        return int(rendered)
    except ValueError as exc:
        raise ValueError(f"{field} must resolve to an integer.") from exc


def _render_float(value: float | str, matched: CommandMatch | None, field: str) -> float:
    rendered = _render(str(value), matched)
    try:
        return float(rendered)
    except ValueError as exc:
        raise ValueError(f"{field} must resolve to a number.") from exc


def action_from_command(action: CommandAction, matched: CommandMatch | None = None) -> Action:
    kind = action.type
    if kind in {"type_text", "type_cli"}:
        return TypeTextAction(_render(action.text, matched))
    if kind == "press_key":
        return PressKeysAction(_render(action.key, matched))
    if kind == "activate_app":
        return ActivateAppAction(_render(action.app, matched))
    if kind == "activate_window":
        return ActivateWindowAction(_render(action.window, matched))
    if kind == "launch_desktop_entry":
        return LaunchDesktopEntryAction(_render(action.desktop_file_id, matched))
    if kind == "invoke_ui":
        return InvokeUiAction(_render(action.target, matched), _render(action.action or "click", matched))
    if kind == "move_pointer":
        return MovePointerAction(target=_render(action.target, matched))
    if kind in {"click", "right_click", "double_click"}:
        return ClickAction(
            target=_render(action.target, matched),
            button="right" if kind == "right_click" else "left",
            count=2 if kind == "double_click" else 1,
        )
    if kind == "scroll":
        return ScrollAction(
            _render(action.direction or "down", matched),  # type: ignore[arg-type]
            max(1, _render_int(action.amount or 1, matched, "Scroll amount")),
        )
    raise ValueError(f"Unsupported desktop action: {kind}")


def action_from_step(step: MacroStep, matched: CommandMatch | None = None) -> Action:
    common = {"continue_on_error": step.continue_on_error}
    if step.type in {"type_text", "type_cli"}:
        return TypeTextAction(_render(step.text, matched), **common)
    if step.type == "press_key":
        return PressKeysAction(_render(step.key, matched), **common)
    if step.type == "wait":
        return WaitAction(min(MAX_WAIT_SECONDS, _render_float(step.seconds, matched, "Wait time")), **common)
    if step.type == "wait_for_target":
        return WaitForTargetAction(
            _render(step.target, matched),
            min(MAX_WAIT_SECONDS, _render_float(step.timeout or 5, matched, "Target timeout")),
            **common,
        )
    if step.type == "activate_app":
        return ActivateAppAction(_render(step.app, matched), **common)
    if step.type == "activate_window":
        return ActivateWindowAction(_render(step.window, matched), **common)
    if step.type == "launch_desktop_entry":
        return LaunchDesktopEntryAction(_render(step.desktop_file_id, matched), **common)
    if step.type == "invoke_ui":
        return InvokeUiAction(_render(step.target, matched), _render(step.action or "click", matched), **common)
    if step.type == "move_pointer":
        return MovePointerAction(target=_render(step.target, matched), **common)
    if step.type in {"click", "right_click", "double_click"}:
        return ClickAction(
            target=_render(step.target, matched),
            button="right" if step.type == "right_click" else "left",
            count=2 if step.type == "double_click" else 1,
            **common,
        )
    if step.type == "scroll":
        return ScrollAction(
            _render(step.direction or "down", matched),  # type: ignore[arg-type]
            max(1, _render_int(step.amount or 1, matched, "Scroll amount")),
            **common,
        )
    if step.type == "repeat":
        return RepeatAction(
            tuple(action_from_step(child, matched) for child in step.steps),
            min(MAX_REPEAT, _render_int(step.count, matched, "Repeat count")),
            **common,
        )
    if step.type == "target_exists":
        return TargetExistsAction(
            _render(step.target, matched),
            tuple(action_from_step(child, matched) for child in step.steps),
            tuple(action_from_step(child, matched) for child in step.else_steps),
            **common,
        )
    raise ValueError(f"Unsupported macro step: {step.type}")


def _action_count(actions: list[Action] | tuple[Action, ...]) -> int:
    total = 0
    for action in actions:
        if isinstance(action, RepeatAction):
            total += min(MAX_REPEAT, action.count) * _action_count(action.actions)
        elif isinstance(action, TargetExistsAction):
            total += max(_action_count(action.then_actions), _action_count(action.else_actions))
        else:
            total += 1
    return total

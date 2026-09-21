from __future__ import annotations

import re
import threading
import time
from typing import Callable

from .desktop import AccessibilityBackend, PointerBackend, TargetResolver, WindowBackend
from .macro_runtime import ActionResult
from .models import (
    Action,
    ActivateAppAction,
    ActivateWindowAction,
    AppContext,
    ClickAction,
    InvokeUiAction,
    LaunchDesktopEntryAction,
    MovePointerAction,
    PressKeysAction,
    ScrollAction,
    TypeTextAction,
    UiTarget,
    WaitAction,
    WaitForTargetAction,
)
from .output import TextDeliveryRouter


SelectionCallback = Callable[[list[UiTarget]], UiTarget | None]


class DesktopExecutor:
    def __init__(
        self,
        router: TextDeliveryRouter,
        window_backend: WindowBackend,
        accessibility: AccessibilityBackend,
        pointer: PointerBackend,
        *,
        app_context: Callable[[], AppContext | None],
        choose_target: SelectionCallback | None = None,
    ) -> None:
        self.router = router
        self.window_backend = window_backend
        self.accessibility = accessibility
        self.pointer = pointer
        self.app_context = app_context
        self.choose_target = choose_target
        self.resolver = TargetResolver()
        self.last_ambiguity: list[UiTarget] = []
        self.last_ambiguous_action: Action | None = None
        self.last_window_ambiguity: list[AppContext] = []

    def execute(self, action: Action, cancel_event: threading.Event) -> ActionResult:
        self.last_ambiguity = []
        self.last_ambiguous_action = None
        self.last_window_ambiguity = []
        context = self.app_context()
        if isinstance(action, TypeTextAction):
            try:
                result = self.router.deliver(action.text, context)
            except TypeError:
                result = self.router.deliver(action.text)
            return ActionResult(result.ok, result.detail, result.method)
        if isinstance(action, PressKeysAction):
            if not re.fullmatch(r"[A-Za-z0-9_+-]+", action.keys):
                return ActionResult(False, "Key chord contains unsupported characters.")
            try:
                result = self.router.deliver_key(action.keys, context)
            except TypeError:
                result = self.router.deliver_key(action.keys)
            return ActionResult(result.ok, result.detail, result.method)
        if isinstance(action, WaitAction):
            return self._wait(action.seconds, cancel_event)
        if isinstance(action, WaitForTargetAction):
            deadline = time.monotonic() + action.timeout
            while time.monotonic() < deadline and not cancel_event.is_set():
                if self.target_exists(action.target):
                    return ActionResult(True, f"Target {action.target!r} appeared.", "at-spi")
                cancel_event.wait(0.05)
            return ActionResult(False, "Cancelled." if cancel_event.is_set() else f"Timed out waiting for {action.target!r}.")
        if isinstance(action, ActivateAppAction):
            return self._activate_named(action.app, window_only=False)
        if isinstance(action, ActivateWindowAction):
            return self._activate_named(action.window, window_only=True)
        if isinstance(action, LaunchDesktopEntryAction):
            ok = self.window_backend.launch_desktop_entry(action.desktop_file_id)
            return ActionResult(ok, f"Opened {action.desktop_file_id}." if ok else "Desktop entry was not found or could not be opened.", "desktop-entry")
        if isinstance(action, InvokeUiAction):
            target = self._one_target(action.target)
            if target is None:
                self.last_ambiguous_action = action if self.last_ambiguity else None
                return ActionResult(False, self._target_failure(action.target))
            if self.accessibility.invoke_action(target, action.action):
                return ActionResult(True, f"Invoked {target.name} through accessibility.", "at-spi-action")
            x, y = target.rect.center
            if not self.pointer.move(x, y):
                return ActionResult(False, f"Could not move to {target.name}.")
            if action.action.casefold() == "focus":
                return ActionResult(True, f"Moved to {target.name}.", "pointer")
            ok = self.pointer.click("right" if action.action.casefold() == "right_click" else "left")
            return ActionResult(ok, f"Clicked {target.name}." if ok else f"Could not click {target.name}.", "pointer")
        if isinstance(action, MovePointerAction):
            if action.target:
                target = self._one_target(action.target)
                if target is None:
                    self.last_ambiguous_action = action if self.last_ambiguity else None
                    return ActionResult(False, self._target_failure(action.target))
                x, y = target.rect.center
            elif action.x is not None and action.y is not None:
                x, y = action.x, action.y
            else:
                return ActionResult(False, "Move action has no target or coordinates.")
            ok = self.pointer.move(x, y)
            return ActionResult(ok, "Pointer moved." if ok else "Pointer move failed.", "pointer")
        if isinstance(action, ClickAction):
            if action.target:
                target = self._one_target(action.target)
                if target is None:
                    self.last_ambiguous_action = action if self.last_ambiguity else None
                    return ActionResult(False, self._target_failure(action.target))
                # Accessible action always wins for ordinary left click.
                if action.button == "left" and action.count == 1 and self.accessibility.invoke_action(target, "click"):
                    return ActionResult(True, f"Invoked {target.name} through accessibility.", "at-spi-action")
                x, y = target.rect.center
                if not self.pointer.move(x, y):
                    return ActionResult(False, f"Could not move to {target.name}.")
            ok = self.pointer.click(action.button, action.count)
            return ActionResult(ok, "Pointer click sent." if ok else "Pointer click failed.", "pointer")
        if isinstance(action, ScrollAction):
            ok = self.pointer.scroll(action.direction, action.amount)
            return ActionResult(ok, "Scroll sent." if ok else "Scroll failed.", "pointer")
        return ActionResult(False, f"Unsupported action: {type(action).__name__}")

    def execute_selected(
        self,
        target: UiTarget,
        action: Action | None = None,
        *,
        operation: str = "click",
    ) -> ActionResult:
        """Execute a numbered selection without resolving its name again."""

        if isinstance(action, ClickAction):
            button, count = action.button, action.count
            if button == "left" and count == 1 and self.accessibility.invoke_action(target, "click"):
                return ActionResult(True, f"Invoked {target.name} through accessibility.", "at-spi-action")
            if not self.pointer.move(*target.rect.center):
                return ActionResult(False, f"Could not move to {target.name}.")
            ok = self.pointer.click(button, count)
            return ActionResult(ok, f"Selected {target.name}." if ok else "Pointer click failed.", "pointer")
        if isinstance(action, InvokeUiAction):
            if self.accessibility.invoke_action(target, action.action):
                return ActionResult(True, f"Invoked {target.name} through accessibility.", "at-spi-action")
            operation = "move" if action.action.casefold() == "focus" else "click"
        if isinstance(action, MovePointerAction):
            operation = "move"

        if operation == "click" and self.accessibility.invoke_action(target, "click"):
            return ActionResult(True, f"Invoked {target.name} through accessibility.", "at-spi-action")
        if not self.pointer.move(*target.rect.center):
            return ActionResult(False, f"Could not move to {target.name}.")
        if operation == "move":
            return ActionResult(True, f"Moved to {target.name}.", "pointer")
        ok = self.pointer.click("left", 1)
        return ActionResult(ok, f"Selected {target.name}." if ok else "Pointer click failed.", "pointer")

    def target_exists(self, target_name: str) -> bool:
        context = self.app_context()
        if context is None:
            return False
        return bool(self.resolver.resolve(target_name, self.accessibility.discover_targets(context)))

    def _wait(self, seconds: float, cancel_event: threading.Event) -> ActionResult:
        if seconds < 0 or seconds > 300:
            return ActionResult(False, "Wait must be between 0 and 300 seconds.")
        cancelled = cancel_event.wait(seconds)
        return ActionResult(not cancelled, "Cancelled." if cancelled else "Wait complete.", "wait")

    def _activate_named(self, query: str, *, window_only: bool) -> ActionResult:
        self.last_window_ambiguity = []
        folded = query.casefold()
        windows = self.window_backend.list_windows()
        exact = [
            item
            for item in windows
            if item.title.casefold() == folded
            or (not window_only and item.desktop_file_id.casefold().removesuffix(".desktop") == folded.removesuffix(".desktop"))
        ]
        matches = exact or [item for item in windows if folded in item.title.casefold()]
        if len(matches) != 1:
            self.last_window_ambiguity = matches if len(matches) > 1 else []
            return ActionResult(False, "No matching window." if not matches else "Window name is ambiguous.", "window")
        ok = self.window_backend.activate_window(matches[0])
        return ActionResult(ok, f"Activated {matches[0].title}." if ok else "Window activation failed.", "window")

    def _one_target(self, spoken: str) -> UiTarget | None:
        self.last_ambiguity = []
        self.last_ambiguous_action = None
        context = self.app_context()
        if context is None:
            return None
        matches = self.resolver.resolve(spoken, self.accessibility.discover_targets(context))
        self.last_ambiguity = matches if len(matches) > 1 else []
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1 and self.choose_target is not None:
            return self.choose_target(matches)
        return None

    def _target_failure(self, spoken: str) -> str:
        return f"Target {spoken!r} is ambiguous; choose a number." if self.last_ambiguity else f"Target {spoken!r} was not found."


class BuiltinDesktopCommands:
    """Small deterministic grammar for the roadmap's standard commands."""

    def parse(self, text: str) -> Action | tuple[str, str] | None:
        normalized = " ".join(text.casefold().split())
        matched = re.fullmatch(r"switch to (.+)", normalized)
        if matched:
            return ActivateAppAction(matched.group(1))
        matched = re.fullmatch(r"(?:activate|focus) window (.+)", normalized)
        if matched:
            return ActivateWindowAction(matched.group(1))
        matched = re.fullmatch(r"open ([a-z0-9_.+-]+)", normalized)
        if matched:
            return LaunchDesktopEntryAction(matched.group(1))
        # Numbered-overlay selections take priority over the generic target
        # grammar (otherwise "click 12" would search for a control named 12).
        matched = re.fullmatch(r"(click|move to|narrow|zoom) (\d+)", normalized)
        if matched:
            return (matched.group(1), matched.group(2))
        matched = re.fullmatch(r"click (.+)", normalized)
        if matched:
            return ClickAction(matched.group(1))
        matched = re.fullmatch(r"right[ -]click (.+)", normalized)
        if matched:
            return ClickAction(matched.group(1), "right")
        matched = re.fullmatch(r"double[ -]click (.+)", normalized)
        if matched:
            return ClickAction(matched.group(1), "left", 2)
        matched = re.fullmatch(r"(?:focus|move mouse to|move to) (.+)", normalized)
        if matched:
            return InvokeUiAction(matched.group(1), "focus")
        matched = re.fullmatch(r"scroll (up|down|left|right)(?: (\d+))?", normalized)
        if matched:
            return ScrollAction(matched.group(1), int(matched.group(2) or 1))  # type: ignore[arg-type]
        if normalized == "show numbers":
            return ("show_numbers", "")
        if normalized == "previous app":
            return ("previous_app", "")
        matched = re.fullmatch(r"(next|previous) (.+) window", normalized)
        if matched:
            return (matched.group(1) + "_window", matched.group(2))
        return None

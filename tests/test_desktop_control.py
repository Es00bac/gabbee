from __future__ import annotations

import os
import threading

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from gabbee.desktop import AppContextService, KWinQtScriptBridge, KWinWindowBackend, TargetResolver, deduplicate_targets
from gabbee.desktop_actions import DesktopExecutor
from gabbee.models import AppContext, ClickAction, DeliveryResult, Rect, UiTarget
from gabbee.ui.target_overlay import RecursiveScreenGrid


def target(name: str, x: float, y: float, *, actions=(), depth=0, enabled=True) -> UiTarget:
    return UiTarget(
        id=f"{name}-{depth}",
        name=name,
        role="button",
        rect=Rect(x, y, 40, 20),
        actions=tuple(actions),
        selector=f"{name}-{depth}",
        depth=depth,
        enabled=enabled,
    )


def test_target_filtering_deduplication_and_number_order() -> None:
    items = [
        target("Later", 200, 100),
        target("Save", 10, 10, depth=1),
        target("Save", 10, 10, actions=("click",), depth=2),
        target("Disabled", 0, 0, enabled=False),
        target("First", 100, 10),
    ]
    resolved = TargetResolver().filter_targets(items)
    assert [item.name for item in resolved] == ["Save", "First", "Later"]
    assert resolved[0].actions == ("click",)
    assert [number for number, _item in TargetResolver().numbered(items)] == [1, 2, 3]


class NoWindows:
    def active_window(self):
        return None


class FocusedAccessibility:
    def __init__(self, context: AppContext) -> None:
        self.context = context

    def active_context(self):
        return self.context

    def enrich_context(self, app):
        raise AssertionError("an accessibility context is already enriched")


def test_app_context_falls_back_to_accessibility_without_kwin() -> None:
    focused = AppContext(
        desktop_file_id="qterminal",
        title="QTerminal",
        pid=42,
        focused_selector="terminal/text",
    )
    service = AppContextService(NoWindows(), FocusedAccessibility(focused))

    assert service.capture() == focused
    assert service.current() == focused
    assert service.current_matches(focused)


class Router:
    def deliver(self, text, context=None):
        return DeliveryResult(True, "fake")

    def deliver_key(self, key, context=None):
        return DeliveryResult(True, "fake")


class Windows:
    def active_window(self):
        return AppContext(window_id="one")

    def list_windows(self):
        return []

    def activate_window(self, window):
        return True

    def launch_desktop_entry(self, desktop_file_id):
        return True


class Accessibility:
    def __init__(self, targets, invoke=True):
        self.targets = targets
        self.invoke = invoke
        self.invoked = []

    def discover_targets(self, app):
        return self.targets

    def invoke_action(self, item, action="click"):
        self.invoked.append((item.name, action))
        return self.invoke


class Pointer:
    def __init__(self):
        self.moves = []
        self.clicks = []

    def move(self, x, y):
        self.moves.append((x, y))
        return True

    def click(self, button="left", count=1):
        self.clicks.append((button, count))
        return True

    def scroll(self, direction, amount=1):
        return True


def test_accessible_action_is_preferred_to_pointer_click() -> None:
    save = target("Save", 10, 10, actions=("click",))
    accessibility, pointer = Accessibility([save], invoke=True), Pointer()
    executor = DesktopExecutor(
        Router(), Windows(), accessibility, pointer, app_context=lambda: AppContext(window_id="one")
    )
    result = executor.execute(ClickAction("Save"), threading.Event())
    assert result.ok and result.method == "at-spi-action"
    assert accessibility.invoked == [("Save", "click")]
    assert pointer.moves == [] and pointer.clicks == []


def test_pointer_center_is_used_when_accessible_action_fails() -> None:
    save = target("Save", 10, 20)
    accessibility, pointer = Accessibility([save], invoke=False), Pointer()
    executor = DesktopExecutor(
        Router(), Windows(), accessibility, pointer, app_context=lambda: AppContext(window_id="one")
    )
    result = executor.execute(ClickAction("Save", "right"), threading.Event())
    assert result.ok and result.method == "pointer"
    assert pointer.moves == [(30, 30)]
    assert pointer.clicks == [("right", 1)]


def test_recursive_grid_geometry_is_deterministic_with_offset_monitor() -> None:
    grid = RecursiveScreenGrid(Rect(-1920, 0, 3840, 1080))
    assert len(grid.cells()) == 9
    selected = grid.descend(1)
    assert selected == Rect(-1920, 0, 1280, 360)
    grid.descend(9)
    assert grid.center() == pytest.approx((-1920 + 1280 - 1280 / 6, 360 - 60))


class FakeKWinBridge:
    def __init__(self):
        self.activated = []

    def list_windows(self):
        return [
            {"id": "a", "desktopFileId": "firefox", "title": "One", "active": False, "stackingOrder": 1},
            {"id": "b", "desktopFileId": "kate", "title": "Two", "active": True, "stackingOrder": 2},
        ]

    def activate(self, window_id):
        self.activated.append(window_id)
        return True


def test_kwin_backend_uses_generated_bridge_data() -> None:
    bridge = FakeKWinBridge()
    backend = KWinWindowBackend(bridge)
    assert backend.active_window().desktop_file_id == "kate"  # type: ignore[union-attr]
    firefox = backend.find("firefox")
    assert len(firefox) == 1
    assert backend.activate_window(firefox[0])
    assert bridge.activated == ["a"]
    assert "workspace.stackingOrder" in backend.ENUMERATE_SCRIPT
    assert "windowList" not in backend.ENUMERATE_SCRIPT


def test_kwin_qt_bridge_marshals_worker_query_to_application_thread(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    bridge = KWinQtScriptBridge(qdbus="/bin/true")
    application_thread = threading.get_ident()
    callback_threads: list[int] = []
    expected = [{"id": "active", "active": True}]

    def query():
        callback_threads.append(threading.get_ident())
        return expected

    monkeypatch.setattr(bridge, "_list_windows_on_application_thread", query)
    result: list[list[dict[str, object]]] = []
    finished = threading.Event()

    def run_query() -> None:
        result.append(bridge.list_windows())
        finished.set()

    worker = threading.Thread(target=run_query)
    worker.start()
    while not finished.wait(0.005):
        app.processEvents()
    worker.join(timeout=1)

    assert not worker.is_alive()
    assert result == [expected]
    assert callback_threads == [application_thread]

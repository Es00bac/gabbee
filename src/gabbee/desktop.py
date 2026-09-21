from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import uuid
from typing import Callable, Protocol
from urllib.parse import unquote

from .models import AppContext, DeliveryResult, Rect, UiTarget


class WindowBackend(Protocol):
    def active_window(self) -> AppContext | None:
        ...

    def list_windows(self) -> list[AppContext]:
        ...

    def activate_window(self, window: AppContext) -> bool:
        ...

    def launch_desktop_entry(self, desktop_file_id: str) -> bool:
        ...


class AccessibilityBackend(Protocol):
    def enrich_context(self, app: AppContext) -> AppContext:
        ...

    def insert_text(self, text: str, app: AppContext) -> DeliveryResult:
        ...

    def discover_targets(self, app: AppContext) -> list[UiTarget]:
        ...

    def invoke_action(self, target: UiTarget, action: str = "click") -> bool:
        ...

    def restore_focus(self, app: AppContext) -> bool:
        ...


class PointerBackend(Protocol):
    def move(self, x: float, y: float) -> bool:
        ...

    def click(self, button: str = "left", count: int = 1) -> bool:
        ...

    def scroll(self, direction: str, amount: int = 1) -> bool:
        ...


class NullWindowBackend:
    def active_window(self) -> AppContext | None:
        return None

    def list_windows(self) -> list[AppContext]:
        return []

    def activate_window(self, window: AppContext) -> bool:
        return False

    def launch_desktop_entry(self, desktop_file_id: str) -> bool:
        return False


class NullAccessibilityBackend:
    def enrich_context(self, app: AppContext) -> AppContext:
        return app

    def insert_text(self, text: str, app: AppContext) -> DeliveryResult:
        return DeliveryResult(False, "at-spi", "AT-SPI is unavailable.")

    def discover_targets(self, app: AppContext) -> list[UiTarget]:
        return []

    def invoke_action(self, target: UiTarget, action: str = "click") -> bool:
        return False

    def restore_focus(self, app: AppContext) -> bool:
        return False


class NullPointerBackend:
    def move(self, x: float, y: float) -> bool:
        return False

    def click(self, button: str = "left", count: int = 1) -> bool:
        return False

    def scroll(self, direction: str, amount: int = 1) -> bool:
        return False


class AppContextService:
    def __init__(
        self,
        window_backend: WindowBackend,
        accessibility_backend: AccessibilityBackend | None = None,
        *,
        excluded_desktop_ids: tuple[str, ...] = ("gabbee", "gabbee.desktop"),
        excluded_title_pattern: str = r"\bGabbee\b",
    ) -> None:
        self.window_backend = window_backend
        self.accessibility_backend = accessibility_backend or NullAccessibilityBackend()
        self.excluded_desktop_ids = {item.casefold().removesuffix(".desktop") for item in excluded_desktop_ids}
        self.excluded_title_pattern = re.compile(excluded_title_pattern, re.IGNORECASE)
        self.last_non_gabbee: AppContext | None = None

    def capture(self) -> AppContext | None:
        active = self._active_context()
        if active is not None and not self._is_gabbee(active):
            if not active.focused_selector:
                try:
                    active = self.accessibility_backend.enrich_context(active)
                except Exception:
                    pass
            self.last_non_gabbee = active
            return active
        # A push-to-talk portal may momentarily report Gabbee itself. Preserve
        # the last verified external target instead of falling back blindly.
        return self.last_non_gabbee

    def current(self) -> AppContext | None:
        """Return only the currently verified external target, never cached state."""

        active = self._active_context()
        if active is None or self._is_gabbee(active):
            return None
        if active.focused_selector:
            return active
        try:
            return self.accessibility_backend.enrich_context(active)
        except Exception:
            return active

    def current_matches(self, captured: AppContext | None) -> bool:
        if captured is None:
            return False
        return captured.same_target(self.current())

    def _is_gabbee(self, app: AppContext) -> bool:
        desktop_id = app.desktop_file_id.casefold().removesuffix(".desktop")
        return desktop_id in self.excluded_desktop_ids or bool(self.excluded_title_pattern.search(app.title))

    def _active_context(self) -> AppContext | None:
        try:
            active = self.window_backend.active_window()
        except Exception:
            active = None
        if active is not None:
            return active
        accessible_context = getattr(self.accessibility_backend, "active_context", None)
        if not callable(accessible_context):
            return None
        try:
            return accessible_context()
        except Exception:
            return None


class TargetResolver:
    """Deterministic accessible-name resolution; ambiguity stays explicit."""

    def filter_targets(self, targets: list[UiTarget]) -> list[UiTarget]:
        usable = [
            item
            for item in targets
            if item.name.strip()
            and item.enabled
            and item.visible
            and item.showing
            and item.rect.width > 0
            and item.rect.height > 0
        ]
        return deduplicate_targets(usable)

    def resolve(self, spoken: str, targets: list[UiTarget]) -> list[UiTarget]:
        query = _normalize_name(spoken)
        usable = self.filter_targets(targets)
        exact = [item for item in usable if _normalize_name(item.name) == query]
        if exact:
            return exact
        return [item for item in usable if query and query in _normalize_name(item.name)]

    def numbered(self, targets: list[UiTarget]) -> list[tuple[int, UiTarget]]:
        return list(enumerate(self.filter_targets(targets), start=1))


def deduplicate_targets(targets: list[UiTarget]) -> list[UiTarget]:
    # Prefer actionable/deeper controls for identical rectangles and labels.
    preferred: dict[tuple[str, int, int, int, int], UiTarget] = {}
    for target in targets:
        key = (
            _normalize_name(target.name),
            round(target.rect.x),
            round(target.rect.y),
            round(target.rect.width),
            round(target.rect.height),
        )
        previous = preferred.get(key)
        if previous is None or (target.actionable, target.depth) > (previous.actionable, previous.depth):
            preferred[key] = target
    values = list(preferred.values())
    nested: list[UiTarget] = []
    for target in values:
        duplicate_index = next(
            (
                index
                for index, existing in enumerate(nested)
                if _normalize_name(existing.name) == _normalize_name(target.name)
                and (existing.rect.contains(target.rect) or target.rect.contains(existing.rect))
            ),
            None,
        )
        if duplicate_index is None:
            nested.append(target)
            continue
        existing = nested[duplicate_index]
        if (target.actionable, target.depth, -target.rect.area) > (
            existing.actionable,
            existing.depth,
            -existing.rect.area,
        ):
            nested[duplicate_index] = target
    return sorted(
        nested,
        key=lambda item: (round(item.rect.y), round(item.rect.x), item.rect.area, item.name.casefold()),
    )


def _normalize_name(value: str) -> str:
    return " ".join(re.findall(r"[\w]+", value.casefold()))


class AtSpiAccessibilityBackend:
    """AT-SPI adapter with duck-typed seams for deterministic unit testing."""

    def __init__(self, atspi_module=None, desktop_provider: Callable[[], list[object]] | None = None) -> None:
        if atspi_module is None:
            if not _atspi_bus_reachable():
                self.Atspi = None
                self.desktop_provider = desktop_provider or (lambda: [])
                self._objects = {}
                return
            try:
                import gi

                gi.require_version("Atspi", "2.0")
                from gi.repository import Atspi

                atspi_module = Atspi
            except Exception:
                atspi_module = None
        self.Atspi = atspi_module
        self.desktop_provider = desktop_provider or self._desktops
        self._objects: dict[str, object] = {}

    @property
    def available(self) -> bool:
        return self.Atspi is not None

    def active_context(self) -> AppContext | None:
        """Build a focused-app context without a compositor window API."""

        for desktop in self.desktop_provider():
            count = int(_call(desktop, "get_child_count", default=0) or 0)
            for index in range(count):
                application = _call(desktop, "get_child_at_index", index)
                if application is None:
                    continue
                focused = self._focused_descendant(application)
                if focused is None:
                    continue
                app_name = str(_call(application, "get_name", default="") or "")
                focused_name = str(_call(focused, "get_name", default="") or "")
                focused_role = str(_call(focused, "get_role_name", default="") or "")
                selector = self._selector_for(focused, [focused_role, focused_name])
                before, after = self._surrounding_text(focused)
                self._objects[selector] = focused
                pid = _call(application, "get_process_id", default=None)
                try:
                    pid = int(pid) if pid is not None else None
                except (TypeError, ValueError):
                    pid = None
                return AppContext(
                    desktop_file_id=app_name,
                    title=app_name,
                    pid=pid,
                    focused_selector=selector,
                    focused_name=focused_name,
                    focused_role=focused_role,
                    surrounding_before=before,
                    surrounding_after=after,
                )
        return None

    def enrich_context(self, app: AppContext) -> AppContext:
        focused = self._focused_accessible(app)
        if focused is None:
            return app
        name = _call(focused, "get_name", default="")
        role = _call(focused, "get_role_name", default="")
        selector = self._selector_for(focused, [str(role), str(name)])
        before, after = self._surrounding_text(focused)
        self._objects[selector] = focused
        return replace(
            app,
            focused_selector=selector,
            focused_name=str(name or ""),
            focused_role=str(role or ""),
            surrounding_before=before,
            surrounding_after=after,
        )

    def insert_text(self, text: str, app: AppContext) -> DeliveryResult:
        accessible = self._objects.get(app.focused_selector) if app.focused_selector else None
        if accessible is None:
            accessible = self._focused_accessible(app)
        if accessible is None:
            return DeliveryResult(False, "at-spi", "Captured editable control is no longer available.")
        editable = _call(accessible, "get_editable_text_iface")
        if editable is None:
            return DeliveryResult(False, "at-spi", "Captured control does not implement EditableText.")
        text_iface = _call(accessible, "get_text_iface")
        caret = _call(text_iface, "get_caret_offset", default=-1) if text_iface is not None else -1
        if not isinstance(caret, int) or caret < 0:
            caret = 0
        try:
            inserted = editable.insert_text(caret, text, len(text))
        except Exception as exc:
            return DeliveryResult(False, "at-spi", f"AT-SPI insertion failed: {exc}")
        ok = inserted is not False
        return DeliveryResult(ok, "at-spi", "Inserted through AT-SPI EditableText." if ok else "AT-SPI rejected text.", True)

    def discover_targets(self, app: AppContext) -> list[UiTarget]:
        root = self._application_accessible(app)
        if root is None:
            return []
        result: list[UiTarget] = []

        def walk(node: object, path: list[str], depth: int) -> None:
            if depth > 40 or len(result) >= 2_000:
                return
            name = str(_call(node, "get_name", default="") or "").strip()
            role = str(_call(node, "get_role_name", default="") or "")
            selector = self._selector_for(node, [*path, role, name])
            actions = self._actions(node)
            rect = self._rect(node)
            enabled, visible, showing = self._states(node)
            if name and rect is not None:
                target = UiTarget(
                    id=selector,
                    name=name,
                    role=role,
                    rect=rect,
                    actions=tuple(actions),
                    selector=selector,
                    enabled=enabled,
                    visible=visible,
                    showing=showing,
                    depth=depth,
                )
                result.append(target)
                self._objects[selector] = node
            count = _call(node, "get_child_count", default=0)
            try:
                count = int(count)
            except (TypeError, ValueError):
                count = 0
            for index in range(max(0, count)):
                child = _call(node, "get_child_at_index", index)
                if child is not None:
                    walk(child, [*path, f"{role}:{index}"], depth + 1)

        walk(root, [], 0)
        return deduplicate_targets(
            [item for item in result if item.enabled and item.visible and item.showing]
        )

    def invoke_action(self, target: UiTarget, action: str = "click") -> bool:
        node = self._objects.get(target.selector or target.id)
        if node is None:
            return False
        iface = _call(node, "get_action_iface")
        if iface is None:
            return False
        count = _call(iface, "get_n_actions", default=0)
        aliases = {"click": {"click", "press", "activate", "open"}, "focus": {"focus", "grab focus"}}
        wanted = aliases.get(action.casefold(), {action.casefold()})
        for index in range(int(count or 0)):
            name = str(_call(iface, "get_action_name", index, default="") or "").casefold()
            if name in wanted or (action == "click" and any(item in name for item in wanted)):
                try:
                    return bool(iface.do_action(index))
                except Exception:
                    return False
        return False

    def restore_focus(self, app: AppContext) -> bool:
        node = self._objects.get(app.focused_selector) if app.focused_selector else None
        if node is None:
            return False
        component = _call(node, "get_component_iface")
        if component is not None:
            focused = _call(component, "grab_focus", default=False)
            if focused:
                return True
        target = UiTarget(
            id=app.focused_selector,
            name=app.focused_name or "captured control",
            role=app.focused_role,
            rect=Rect(0, 0, 1, 1),
            selector=app.focused_selector,
        )
        return self.invoke_action(target, "focus")

    def _desktops(self) -> list[object]:
        if self.Atspi is None:
            return []
        try:
            return [self.Atspi.get_desktop(index) for index in range(self.Atspi.get_desktop_count())]
        except Exception:
            return []

    def _application_accessible(self, app: AppContext) -> object | None:
        for desktop in self.desktop_provider():
            count = int(_call(desktop, "get_child_count", default=0) or 0)
            for index in range(count):
                candidate = _call(desktop, "get_child_at_index", index)
                if candidate is None:
                    continue
                pid = _call(candidate, "get_process_id", default=None)
                name = str(_call(candidate, "get_name", default="") or "")
                if app.pid is not None and pid == app.pid:
                    return candidate
                if app.desktop_file_id and _normalize_name(app.desktop_file_id.removesuffix(".desktop")) == _normalize_name(name):
                    return candidate
        return None

    def _focused_accessible(self, app: AppContext) -> object | None:
        root = self._application_accessible(app)
        if root is None:
            return None
        return self._focused_descendant(root)

    def _focused_descendant(self, root: object) -> object | None:
        queue: list[object] = [root]
        fallback: object | None = None
        while queue:
            node = queue.pop(0)
            if self._has_state(node, "FOCUSED"):
                role = str(_call(node, "get_role_name", default="") or "").casefold()
                if role in {"text", "editable text", "terminal"}:
                    return node
                try:
                    if _call(node, "get_editable_text_iface") is not None:
                        return node
                except Exception:
                    pass
                if fallback is None:
                    fallback = node
            count = int(_call(node, "get_child_count", default=0) or 0)
            for index in range(count):
                child = _call(node, "get_child_at_index", index)
                if child is not None:
                    queue.append(child)
        return fallback

    def _surrounding_text(self, node: object) -> tuple[str, str]:
        iface = _call(node, "get_text_iface")
        if iface is None:
            return "", ""
        count = _call(iface, "get_character_count", default=0)
        caret = _call(iface, "get_caret_offset", default=0)
        try:
            count, caret = int(count), int(caret)
            # GI exposes get_text on the Accessible proxy as the interface
            # accessor; call the Text interface explicitly to pass offsets.
            value = str(self.Atspi.Text.get_text(node, 0, count) or "")
        except Exception:
            return "", ""
        return value[:caret][-500:], value[caret:][:500]

    def _states(self, node: object) -> tuple[bool, bool, bool]:
        return (
            self._has_state(node, "ENABLED", default=True),
            self._has_state(node, "VISIBLE", default=True),
            self._has_state(node, "SHOWING", default=True),
        )

    def _has_state(self, node: object, name: str, default: bool = False) -> bool:
        state_set = _call(node, "get_state_set")
        state_type = getattr(getattr(self.Atspi, "StateType", None), name, None) if self.Atspi is not None else name
        if state_set is None or state_type is None:
            return default
        try:
            return bool(state_set.contains(state_type))
        except Exception:
            return default

    def _actions(self, node: object) -> list[str]:
        iface = _call(node, "get_action_iface")
        if iface is None:
            return []
        count = int(_call(iface, "get_n_actions", default=0) or 0)
        return [str(_call(iface, "get_action_name", index, default="") or "") for index in range(count)]

    def _rect(self, node: object) -> Rect | None:
        iface = _call(node, "get_component_iface")
        if iface is None:
            return None
        coord = getattr(getattr(self.Atspi, "CoordType", None), "SCREEN", 0) if self.Atspi is not None else 0
        extent = _call(iface, "get_extents", coord)
        if extent is None:
            return None
        try:
            return Rect(float(extent.x), float(extent.y), float(extent.width), float(extent.height))
        except (AttributeError, TypeError, ValueError):
            return None

    @staticmethod
    def _selector_for(_node: object, path: list[str]) -> str:
        stable = "/".join(re.sub(r"[^A-Za-z0-9_. -]", "", item).strip() for item in path if item)
        return stable[-500:]


def _atspi_bus_reachable() -> bool:
    """Probe the AT-SPI socket before libatspi can abort on a stale bus."""

    address = os.environ.get("AT_SPI_BUS_ADDRESS", "")
    if not address and os.environ.get("DBUS_SESSION_BUS_ADDRESS") and shutil.which("gdbus"):
        try:
            result = subprocess.run(
                [
                    "gdbus",
                    "call",
                    "--session",
                    "--dest",
                    "org.a11y.Bus",
                    "--object-path",
                    "/org/a11y/bus",
                    "--method",
                    "org.a11y.Bus.GetAddress",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=1,
            )
            if result.returncode == 0:
                matched = re.search(r"unix:(?:path|abstract)=([^,'\)]+)", result.stdout)
                if matched:
                    key = "abstract" if "unix:abstract=" in matched.group(0) else "path"
                    value = unquote(matched.group(1))
                    address = f"unix:{key}={value}"
        except (OSError, subprocess.SubprocessError):
            return False
    matched = re.search(r"unix:(path|abstract)=([^,;]+)", address)
    if matched is None:
        return False
    endpoint = unquote(matched.group(2))
    if matched.group(1) == "abstract":
        endpoint = "\0" + endpoint
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    probe.settimeout(0.25)
    try:
        probe.connect(endpoint)
        return True
    except OSError:
        return False
    finally:
        probe.close()


def _call(obj: object | None, name: str, *args, default=None):
    if obj is None:
        return default
    method = getattr(obj, name, None)
    if not callable(method):
        return default
    try:
        return method(*args)
    except Exception:
        return default


class KWinScriptBridge(Protocol):
    def list_windows(self) -> list[dict[str, object]]:
        ...

    def activate(self, window_id: str) -> bool:
        ...


class KWinQtScriptBridge:
    """Exchange generated KWin-script results over a private session-bus name.

    The bridge never evaluates configuration or speech as JavaScript.  It only
    fills JSON-quoted values into the templates below, loads the script through
    KWin's Scripting interface, receives one result, and unloads it again.
    """

    WINDOW_INTERFACE = "io.gabbee.WindowBridge"
    WINDOW_PATH = "/WindowBridge"

    def __init__(self, *, timeout_ms: int = 1_500, qdbus: str | None = None) -> None:
        self.timeout_ms = timeout_ms
        self.qdbus = qdbus or shutil.which("qdbus6") or shutil.which("qdbus")
        self._main_thread_invoker = _create_main_thread_invoker()

    @classmethod
    def try_create(cls) -> KWinScriptBridge | None:
        bridge = cls()
        if not bridge.qdbus or not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
            return None
        try:
            from PyQt6.QtCore import QCoreApplication
            from PyQt6.QtDBus import QDBusConnection

            connection = QDBusConnection.sessionBus()
            if QCoreApplication.instance() is None or not connection.isConnected():
                return None
            interface = connection.interface()
            registered = interface.isServiceRegistered("org.kde.KWin") if interface is not None else False
            if hasattr(registered, "value"):
                registered = registered.value()
            if not registered:
                return None
        except Exception:
            return None
        return bridge

    def list_windows(self) -> list[dict[str, object]]:
        result = self._run_on_application_thread(self._list_windows_on_application_thread, [])
        return result if isinstance(result, list) else []

    def _list_windows_on_application_thread(self) -> list[dict[str, object]]:
        receiver = self._new_receiver()
        if receiver is None:
            return []
        service_name, callback = receiver
        script = f"""
const result = workspace.stackingOrder.map((window, index) => ({{
  id: String(window.internalId),
  desktopFileId: String(window.desktopFileName || ""),
  title: String(window.caption || ""),
  pid: Number(window.pid || 0),
  geometry: {{
    x: Number(window.frameGeometry.x),
    y: Number(window.frameGeometry.y),
    width: Number(window.frameGeometry.width),
    height: Number(window.frameGeometry.height)
  }},
  active: window === workspace.activeWindow,
  stackingOrder: index
}}));
callDBus({json.dumps(service_name)}, {json.dumps(self.WINDOW_PATH)},
         {json.dumps(self.WINDOW_INTERFACE)}, "windows", JSON.stringify(result));
""".strip()
        try:
            if not self._run_script(script, callback):
                return []
            payload = callback.windows_payload
            decoded = json.loads(payload) if payload else []
            return [item for item in decoded if isinstance(item, dict)] if isinstance(decoded, list) else []
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
        finally:
            callback.close()

    def activate(self, window_id: str) -> bool:
        return bool(
            self._run_on_application_thread(
                lambda: self._activate_on_application_thread(window_id),
                False,
            )
        )

    def _activate_on_application_thread(self, window_id: str) -> bool:
        # KWin internal IDs are UUID-like opaque values. JSON quoting keeps the
        # generated source inert even if a future KWin uses a different format.
        receiver = self._new_receiver()
        if receiver is None:
            return False
        service_name, callback = receiver
        script = f"""
const wanted = {json.dumps(str(window_id))};
const match = workspace.stackingOrder.find((window) => String(window.internalId) === wanted);
if (match) {{ workspace.activeWindow = match; }}
callDBus({json.dumps(service_name)}, {json.dumps(self.WINDOW_PATH)},
         {json.dumps(self.WINDOW_INTERFACE)}, "activated", Boolean(match));
""".strip()
        try:
            return self._run_script(script, callback) and callback.activated_result is True
        finally:
            callback.close()

    def _run_on_application_thread(self, callback: Callable[[], object], default: object) -> object:
        try:
            from PyQt6.QtCore import QCoreApplication, QThread

            app = QCoreApplication.instance()
            if app is None:
                return default
            if QThread.currentThread() is app.thread():
                return callback()
            if self._main_thread_invoker is None:
                return default
            return self._main_thread_invoker.call(
                callback,
                timeout=max(5.0, self.timeout_ms / 1000 + 4.0),
            )
        except Exception:
            return default

    def _new_receiver(self):
        try:
            from PyQt6.QtCore import QCoreApplication, QThread

            app = QCoreApplication.instance()
            # Public bridge methods marshal here before creating the callback
            # receiver, whose nested event loop must live on Qt's main thread.
            if app is None or QThread.currentThread() is not app.thread():
                return None
            receiver = _KWinReplyReceiver(self.timeout_ms)
            service_name = f"io.gabbee.Desktop.p{os.getpid()}.b{uuid.uuid4().hex}"
            if not receiver.open(service_name, self.WINDOW_PATH):
                return None
            return service_name, receiver
        except Exception:
            return None

    def _run_script(self, script: str, receiver: "_KWinReplyReceiver") -> bool:
        if not self.qdbus:
            return False
        script_path = ""
        plugin_name = f"gabbee-window-{uuid.uuid4().hex}"
        script_id = ""
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", suffix=".js", prefix="gabbee-kwin-", delete=False
            ) as handle:
                handle.write(script)
                script_path = handle.name
            loaded = subprocess.run(
                [
                    self.qdbus,
                    "org.kde.KWin",
                    "/Scripting",
                    "org.kde.kwin.Scripting.loadScript",
                    script_path,
                    plugin_name,
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            )
            if loaded.returncode != 0:
                return False
            script_id = loaded.stdout.strip().splitlines()[-1].strip()
            if not script_id.isdigit():
                return False
            ran = subprocess.run(
                [
                    self.qdbus,
                    "org.kde.KWin",
                    f"/Scripting/Script{script_id}",
                    "org.kde.kwin.Script.run",
                ],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2,
            )
            if ran.returncode != 0:
                return False
            return receiver.wait()
        except (OSError, subprocess.SubprocessError):
            return False
        finally:
            if script_id.isdigit():
                subprocess.run(
                    [
                        self.qdbus,
                        "org.kde.KWin",
                        "/Scripting",
                        "org.kde.kwin.Scripting.unloadScript",
                        plugin_name,
                    ],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            if script_path:
                try:
                    Path(script_path).unlink()
                except OSError:
                    pass


class _KWinThreadCall:
    def __init__(self, callback: Callable[[], object]) -> None:
        self.callback = callback
        self.done = threading.Event()
        self.result: object = None
        self.error: BaseException | None = None


try:
    from PyQt6.QtCore import QObject, QEventLoop, Qt, QTimer, pyqtClassInfo, pyqtSignal, pyqtSlot
    from PyQt6.QtDBus import QDBusConnection

    class _KWinMainThreadInvoker(QObject):
        requested = pyqtSignal(object)

        def __init__(self) -> None:
            super().__init__()
            self.requested.connect(self._execute, Qt.ConnectionType.QueuedConnection)

        @pyqtSlot(object)
        def _execute(self, request: _KWinThreadCall) -> None:
            try:
                request.result = request.callback()
            except BaseException as exc:
                request.error = exc
            finally:
                request.done.set()

        def call(self, callback: Callable[[], object], *, timeout: float) -> object:
            request = _KWinThreadCall(callback)
            self.requested.emit(request)
            if not request.done.wait(timeout):
                raise TimeoutError("Timed out waiting for the Qt application thread.")
            if request.error is not None:
                raise request.error
            return request.result

    @pyqtClassInfo("D-Bus Interface", KWinQtScriptBridge.WINDOW_INTERFACE)
    class _KWinReplyReceiver(QObject):
        def __init__(self, timeout_ms: int) -> None:
            super().__init__()
            self.timeout_ms = timeout_ms
            self.windows_payload: str | None = None
            self.activated_result: bool | None = None
            self._received = False
            self._loop: QEventLoop | None = None
            self._service_name = ""
            self._connection = QDBusConnection.sessionBus()

        def open(self, service_name: str, path: str) -> bool:
            self._service_name = service_name
            if not self._connection.registerService(service_name):
                return False
            registered = self._connection.registerObject(
                path, self, QDBusConnection.RegisterOption.ExportAllSlots
            )
            if not registered:
                self._connection.unregisterService(service_name)
                self._service_name = ""
            return bool(registered)

        @pyqtSlot(str)
        def windows(self, payload: str) -> None:
            self.windows_payload = payload
            self._finish()

        @pyqtSlot(bool)
        def activated(self, result: bool) -> None:
            self.activated_result = bool(result)
            self._finish()

        def wait(self) -> bool:
            if self._received:
                return True
            self._loop = QEventLoop()
            QTimer.singleShot(self.timeout_ms, self._loop.quit)
            self._loop.exec()
            self._loop = None
            return self._received

        def close(self) -> None:
            self._connection.unregisterObject(KWinQtScriptBridge.WINDOW_PATH)
            if self._service_name:
                self._connection.unregisterService(self._service_name)
                self._service_name = ""

        def _finish(self) -> None:
            self._received = True
            if self._loop is not None:
                self._loop.quit()

except Exception:
    class _KWinMainThreadInvoker:  # type: ignore[no-redef]
        pass

    class _KWinReplyReceiver:  # type: ignore[no-redef]
        pass


def _create_main_thread_invoker():
    try:
        from PyQt6.QtCore import QCoreApplication, QThread

        app = QCoreApplication.instance()
        if app is None or QThread.currentThread() is not app.thread():
            return None
        return _KWinMainThreadInvoker()
    except Exception:
        return None


_DEFAULT_BRIDGE = object()


class KWinWindowBackend:
    """KWin backend whose script bridge accepts only generated templates."""

    ENUMERATE_SCRIPT = """
const result = workspace.stackingOrder.map((window, index) => ({
  id: String(window.internalId),
  desktopFileId: String(window.desktopFileName || ""),
  title: String(window.caption || ""),
  pid: Number(window.pid || 0),
  geometry: {
    x: Number(window.frameGeometry.x),
    y: Number(window.frameGeometry.y),
    width: Number(window.frameGeometry.width),
    height: Number(window.frameGeometry.height)
  },
  active: window === workspace.activeWindow,
  stackingOrder: index
}));
callDBus("io.gabbee.Desktop", "/WindowBridge", "io.gabbee.WindowBridge", "windows", JSON.stringify(result));
""".strip()

    def __init__(self, bridge: KWinScriptBridge | None | object = _DEFAULT_BRIDGE) -> None:
        self.bridge = KWinQtScriptBridge.try_create() if bridge is _DEFAULT_BRIDGE else bridge
        self._history: list[str] = []

    def list_windows(self) -> list[AppContext]:
        if self.bridge is None:
            return []
        result: list[tuple[bool, int, AppContext]] = []
        for item in self.bridge.list_windows():
            try:
                geometry = item.get("geometry")
                rect = None
                if isinstance(geometry, dict):
                    rect = Rect(
                        float(geometry.get("x", 0)),
                        float(geometry.get("y", 0)),
                        float(geometry.get("width", 0)),
                        float(geometry.get("height", 0)),
                    )
                app = AppContext(
                    desktop_file_id=str(item.get("desktopFileId") or item.get("desktop_file_id") or ""),
                    title=str(item.get("title") or ""),
                    window_id=str(item.get("id") or item.get("window_id") or ""),
                    pid=int(item["pid"]) if item.get("pid") else None,
                    rect=rect,
                )
                if not app.window_id or not (app.desktop_file_id or app.title):
                    continue
                result.append((bool(item.get("active")), int(item.get("stackingOrder") or 0), app))
            except (TypeError, ValueError):
                continue
        result.sort(key=lambda row: (row[0], row[1]), reverse=True)
        return [row[2] for row in result]

    def active_window(self) -> AppContext | None:
        windows = self.list_windows()
        active = windows[0] if windows else None
        if active and (not self._history or self._history[-1] != active.window_id):
            self._history.append(active.window_id)
            self._history = self._history[-50:]
        return active

    def activate_window(self, window: AppContext) -> bool:
        if self.bridge is None or not window.window_id:
            return False
        return bool(self.bridge.activate(window.window_id))

    def find(self, query: str) -> list[AppContext]:
        folded = query.casefold().removesuffix(".desktop")
        windows = self.list_windows()
        exact = [item for item in windows if item.desktop_file_id.casefold().removesuffix(".desktop") == folded]
        return exact or [item for item in windows if folded in item.title.casefold()]

    def activate_named(self, query: str, offset: int = 0) -> list[AppContext]:
        matches = self.find(query)
        if len(matches) == 1:
            self.activate_window(matches[0])
        elif matches and offset:
            selected = matches[offset % len(matches)]
            self.activate_window(selected)
            return [selected]
        return matches

    def previous_app(self) -> bool:
        if len(self._history) < 2:
            return False
        target_id = self._history[-2]
        target = next((item for item in self.list_windows() if item.window_id == target_id), None)
        return self.activate_window(target) if target else False

    def launch_desktop_entry(self, desktop_file_id: str) -> bool:
        path = resolve_desktop_entry(desktop_file_id)
        if path is None:
            return False
        normalized = path.name
        if shutil.which("kioclient6"):
            return subprocess.run(
                ["kioclient6", "exec", f"applications:{normalized}"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ).returncode == 0
        if shutil.which("gtk-launch"):
            return subprocess.run(
                ["gtk-launch", normalized.removesuffix(".desktop")],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ).returncode == 0
        return False


def resolve_desktop_entry(desktop_file_id: str) -> Path | None:
    if not re.fullmatch(r"[A-Za-z0-9_.+-]+(?:\.desktop)?", desktop_file_id):
        return None
    filename = desktop_file_id if desktop_file_id.endswith(".desktop") else desktop_file_id + ".desktop"
    roots = [
        Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))),
        *(Path(item) for item in os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":")),
    ]
    for root in roots:
        candidate = root / "applications" / filename
        if candidate.is_file():
            return candidate
    # Spoken app names such as "Kate" commonly differ from the reverse-DNS
    # desktop-file ID (org.kde.kate.desktop). Resolve only installed entries
    # and require a unique deterministic match.
    query = desktop_file_id.casefold().removesuffix(".desktop")
    matches: list[Path] = []
    seen_ids: set[str] = set()
    for root in roots:
        applications = root / "applications"
        if not applications.is_dir():
            continue
        try:
            entries = sorted(applications.glob("*.desktop"))
        except OSError:
            continue
        for entry in entries:
            if entry.name.casefold() in seen_ids:
                continue
            seen_ids.add(entry.name.casefold())
            stem = entry.stem.casefold()
            aliases = {stem, stem.rsplit(".", 1)[-1], stem.rsplit("-", 1)[-1]}
            name = _desktop_entry_name(entry)
            if query in aliases or (name and query == name.casefold()):
                matches.append(entry)
    unique = list(dict.fromkeys(matches))
    return unique[0] if len(unique) == 1 else None


def _desktop_entry_name(path: Path) -> str | None:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            in_entry = False
            for line in handle:
                line = line.strip()
                if line.startswith("["):
                    in_entry = line == "[Desktop Entry]"
                    continue
                if in_entry and line.startswith("Name="):
                    return line.partition("=")[2].strip()
    except OSError:
        pass
    return None


class DotoolPointerBackend:
    def __init__(self, virtual_screen: Rect) -> None:
        self.virtual_screen = virtual_screen

    def move(self, x: float, y: float) -> bool:
        if not shutil.which("dotool") or self.virtual_screen.width <= 0 or self.virtual_screen.height <= 0:
            return False
        normalized_x = min(1.0, max(0.0, (x - self.virtual_screen.x) / self.virtual_screen.width))
        normalized_y = min(1.0, max(0.0, (y - self.virtual_screen.y) / self.virtual_screen.height))
        return self._send(f"mouseto {normalized_x:.6f} {normalized_y:.6f}\n")

    def click(self, button: str = "left", count: int = 1) -> bool:
        if button not in {"left", "right", "middle"} or not 1 <= count <= 2:
            return False
        return self._send("".join(f"click {button}\n" for _ in range(count)))

    def scroll(self, direction: str, amount: int = 1) -> bool:
        if direction not in {"up", "down", "left", "right"} or not 1 <= amount <= 100:
            return False
        horizontal = direction in {"left", "right"}
        sign = -1 if direction in {"up", "left"} else 1
        return self._send(f"{'hwheel' if horizontal else 'wheel'} {sign * amount}\n")

    @staticmethod
    def _send(payload: str) -> bool:
        if not shutil.which("dotool"):
            return False
        try:
            subprocess.run(["dotool"], input=payload.encode("utf-8"), check=True)
        except (OSError, subprocess.CalledProcessError):
            return False
        return True


def qt_virtual_screen_rect() -> Rect:
    """Return Qt logical desktop geometry, which already accounts for scaling."""

    try:
        from PyQt6.QtGui import QGuiApplication

        app = QGuiApplication.instance()
        screens = app.screens() if app is not None else []
        if screens:
            left = min(screen.geometry().left() for screen in screens)
            top = min(screen.geometry().top() for screen in screens)
            right = max(screen.geometry().right() + 1 for screen in screens)
            bottom = max(screen.geometry().bottom() + 1 for screen in screens)
            return Rect(left, top, right - left, bottom - top)
    except Exception:
        pass
    return Rect(0, 0, 1920, 1080)

from __future__ import annotations

import os
from pathlib import Path
import secrets
import subprocess
import threading
from dataclasses import dataclass
from typing import Callable

import dbus
from dbus.mainloop.glib import DBusGMainLoop
from PyQt6.QtCore import QObject, pyqtSignal

from gi.repository import GLib

from ..labwc_install import END_MARKER as LABWC_END_MARKER
from ..labwc_install import START_MARKER as LABWC_START_MARKER
from ..labwc_install import qt_shortcut_to_labwc


PORTAL_SERVICE = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
PORTAL_INTERFACE = "org.freedesktop.portal.GlobalShortcuts"
REQUEST_INTERFACE = "org.freedesktop.portal.Request"
SESSION_INTERFACE = "org.freedesktop.portal.Session"


@dataclass(frozen=True)
class PortalShortcutSpec:
    shortcut_id: str
    shortcut_text: str
    description: str
    on_pressed: Callable[[], None]
    on_released: Callable[[], None]
    on_status_change: Callable[[bool, str], None]


class PortalPushToTalkBinding(QObject):
    pressed = pyqtSignal()
    released = pyqtSignal()
    status_changed = pyqtSignal(bool, str)
    _pressed_for_id = pyqtSignal(str)
    _released_for_id = pyqtSignal(str)
    _status_for_id = pyqtSignal(str, bool, str)

    def __init__(
        self,
        shortcut_text: str | None = None,
        on_pressed: Callable[[], None] | None = None,
        on_released: Callable[[], None] | None = None,
        on_status_change: Callable[[bool, str], None] | None = None,
        shortcut_id: str = "push_to_talk",
        description: str = "Gabbee push to talk",
        shortcut_specs: list[PortalShortcutSpec] | None = None,
        configure_on_start: bool = False,
    ) -> None:
        super().__init__()
        uses_legacy_callbacks = shortcut_specs is None
        if shortcut_specs is None:
            if shortcut_text is None or on_pressed is None or on_released is None or on_status_change is None:
                raise TypeError("single-shortcut binding requires shortcut_text and callbacks")
            shortcut_specs = [
                PortalShortcutSpec(
                    shortcut_id=shortcut_id,
                    shortcut_text=shortcut_text,
                    description=description,
                    on_pressed=on_pressed,
                    on_released=on_released,
                    on_status_change=on_status_change,
                )
            ]
        self.shortcut_specs = shortcut_specs
        self._specs_by_id = {spec.shortcut_id: spec for spec in self.shortcut_specs}
        if not self.shortcut_specs:
            raise ValueError("at least one shortcut spec is required")
        self.shortcut_text = self.shortcut_specs[0].shortcut_text
        self.shortcut_id = self.shortcut_specs[0].shortcut_id
        self.description = self.shortcut_specs[0].description
        self.configure_on_start = configure_on_start
        self._registered = False
        self._registered_ids: set[str] = set()
        self._thread: threading.Thread | None = None
        self._loop: GLib.MainLoop | None = None
        self._bus = None
        self._session_handle: str | None = None
        self._create_session_done = False
        self._list_shortcuts_done = False
        self._bind_shortcuts_done = False
        self._binding_specs_pending: list[PortalShortcutSpec] = []
        self._uses_legacy_callbacks = uses_legacy_callbacks
        self._portal_repair_attempted = False
        self._pressed_for_id.connect(self._dispatch_pressed)
        self._released_for_id.connect(self._dispatch_released)
        self._status_for_id.connect(self._dispatch_status)
        if self._uses_legacy_callbacks:
            self.pressed.connect(self.shortcut_specs[0].on_pressed)
            self.released.connect(self.shortcut_specs[0].on_released)
            self.status_changed.connect(self.shortcut_specs[0].on_status_change)

    def start(self) -> None:
        if self._thread is not None:
            return
        for spec in self.shortcut_specs:
            self._emit_status(
                spec,
                False,
                f"Approve the desktop-wide {spec.shortcut_text} shortcut prompt, or focus Gabbee to use it locally.",
            )
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def close(self) -> None:
        if self._loop is not None:
            self._loop.quit()

    def _run_loop(self) -> None:
        try:
            DBusGMainLoop(set_as_default=True)
            self._bus = dbus.SessionBus()
            self._loop = GLib.MainLoop()
            self._bus.add_signal_receiver(
                self._on_portal_activated,
                signal_name="Activated",
                dbus_interface=PORTAL_INTERFACE,
                bus_name=PORTAL_SERVICE,
                path=PORTAL_PATH,
            )
            self._bus.add_signal_receiver(
                self._on_portal_deactivated,
                signal_name="Deactivated",
                dbus_interface=PORTAL_INTERFACE,
                bus_name=PORTAL_SERVICE,
                path=PORTAL_PATH,
            )
            GLib.idle_add(self._request_session)
            self._loop.run()
        except Exception as exc:
            self._emit_all_statuses(
                False,
                lambda spec: f"Global shortcut portal unavailable: {exc}. Focus Gabbee to use {spec.shortcut_text} locally.",
            )

    def _portal(self):
        if self._bus is None:
            raise RuntimeError("Session bus is unavailable")
        proxy = self._bus.get_object(PORTAL_SERVICE, PORTAL_PATH)
        return dbus.Interface(proxy, PORTAL_INTERFACE)

    def _request_path(self, token: str) -> str:
        if self._bus is None:
            raise RuntimeError("Session bus is unavailable")
        unique_name = self._bus.get_unique_name()
        sender = unique_name[1:].replace(".", "_") if unique_name.startswith(":") else unique_name
        return f"/org/freedesktop/portal/desktop/request/{sender}/{token}"

    def _add_response_receiver(self, request_path: str, handler: Callable[[int, dict[str, object]], None]) -> None:
        if self._bus is None:
            raise RuntimeError("Session bus is unavailable")
        self._bus.add_signal_receiver(
            handler,
            signal_name="Response",
            dbus_interface=REQUEST_INTERFACE,
            bus_name=PORTAL_SERVICE,
            path=request_path,
        )

    def _add_returned_response_receiver(
        self,
        returned_handle: object,
        expected_path: str,
        handler: Callable[[int, dict[str, object]], None],
    ) -> None:
        if isinstance(returned_handle, (str, dbus.ObjectPath)) and returned_handle:
            request_path = str(returned_handle)
            if request_path != expected_path:
                self._add_response_receiver(request_path, handler)

    def _request_session(self) -> bool:
        try:
            handle_token = self._token("create")
            request_path = self._request_path(handle_token)
            self._create_session_done = False
            self._add_response_receiver(request_path, self._on_create_session_response)
            returned_handle = self._portal().CreateSession(
                {
                    "handle_token": handle_token,
                    "session_handle_token": self._token("session"),
                }
            )
            self._add_returned_response_receiver(
                returned_handle,
                request_path,
                self._on_create_session_response,
            )
        except Exception as exc:
            if self._try_repair_stale_kde_portal(exc):
                return False
            self._emit_all_statuses(
                False,
                lambda spec: f"Global shortcut portal error: {exc}. Focus Gabbee to use {spec.shortcut_text} locally.",
            )
        return False

    def _try_repair_stale_kde_portal(self, exc: Exception) -> bool:
        if self._portal_repair_attempted:
            return False
        if not self._is_missing_global_shortcuts_error(exc) or not self._is_kde_session():
            return False
        if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
            return False

        self._portal_repair_attempted = True
        self._emit_all_statuses(
            False,
            lambda spec: (
                "Refreshing KDE desktop portal after missing GlobalShortcuts; "
                f"focus Gabbee to use {spec.shortcut_text} locally while it retries."
            ),
        )
        commands = [
            [
                "dbus-update-activation-environment",
                "--systemd",
                "DISPLAY",
                "WAYLAND_DISPLAY",
                "XDG_CURRENT_DESKTOP",
                "XDG_SESSION_DESKTOP",
                "DESKTOP_SESSION",
                "KDE_FULL_SESSION",
                "DBUS_SESSION_BUS_ADDRESS",
            ],
            ["systemctl", "--user", "try-restart", "xdg-desktop-portal.service"],
        ]
        try:
            for command in commands:
                subprocess.run(
                    command,
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
        except (OSError, subprocess.SubprocessError) as repair_exc:
            self._emit_all_statuses(
                False,
                lambda spec: (
                    f"KDE desktop portal refresh failed: {repair_exc}. "
                    f"Focus Gabbee to use {spec.shortcut_text} locally."
                ),
            )
            return False

        GLib.timeout_add_seconds(2, self._request_session)
        return True

    def _is_missing_global_shortcuts_error(self, exc: Exception) -> bool:
        message = str(exc)
        return "GlobalShortcuts" in message and (
            "UnknownMethod" in message
            or "No such interface" in message
            or "no such interface" in message
        )

    def _is_kde_session(self) -> bool:
        session_parts = [
            os.environ.get("XDG_CURRENT_DESKTOP", ""),
            os.environ.get("XDG_SESSION_DESKTOP", ""),
            os.environ.get("DESKTOP_SESSION", ""),
        ]
        return "kde" in ":".join(session_parts).lower()

    def _request_list_shortcuts(self) -> None:
        if self._bus is None or not self._session_handle:
            self._emit_all_statuses(
                False,
                lambda spec: f"Global shortcut session failed; focus Gabbee to use {spec.shortcut_text}.",
            )
            return
        try:
            handle_token = self._token("list")
            request_path = self._request_path(handle_token)
            self._list_shortcuts_done = False
            self._add_response_receiver(request_path, self._on_list_shortcuts_response)
            returned_handle = self._portal().ListShortcuts(
                dbus.ObjectPath(self._session_handle),
                {"handle_token": handle_token},
            )
            self._add_returned_response_receiver(
                returned_handle,
                request_path,
                self._on_list_shortcuts_response,
            )
        except Exception:
            self._request_binding(self.shortcut_specs)

    def _request_binding(self, shortcut_specs: list[PortalShortcutSpec] | None = None) -> None:
        if self._bus is None or not self._session_handle:
            self._emit_all_statuses(
                False,
                lambda spec: f"Global shortcut session failed; focus Gabbee to use {spec.shortcut_text}.",
            )
            return
        shortcut_specs = self.shortcut_specs if shortcut_specs is None else shortcut_specs
        if not shortcut_specs:
            self._request_configure_shortcuts()
            return
        try:
            handle_token = self._token("bind")
            request_path = self._request_path(handle_token)
            self._bind_shortcuts_done = False
            self._binding_specs_pending = list(shortcut_specs)
            self._add_response_receiver(request_path, self._on_bind_shortcuts_response)
            shortcuts = dbus.Array(
                [
                    dbus.Struct(
                        [
                            spec.shortcut_id,
                            dbus.Dictionary(
                                {
                                    "description": spec.description,
                                    "preferred_trigger": normalize_portal_trigger(spec.shortcut_text),
                                },
                                signature="sv",
                            ),
                        ],
                        signature=None,
                    )
                    for spec in shortcut_specs
                ],
                signature="(sa{sv})",
            )
            returned_handle = self._portal().BindShortcuts(
                dbus.ObjectPath(self._session_handle),
                shortcuts,
                "",
                {"handle_token": handle_token},
            )
            self._add_returned_response_receiver(
                returned_handle,
                request_path,
                self._on_bind_shortcuts_response,
            )
        except Exception as exc:
            for spec in shortcut_specs:
                if spec.shortcut_id not in self._registered_ids:
                    self._emit_status(
                        spec,
                        False,
                        f"Global {spec.shortcut_text} binding failed: {exc}. Focus Gabbee to use it locally.",
                    )
            self._registered = bool(self._registered_ids)
            self._request_configure_shortcuts()

    def _request_configure_shortcuts(self) -> None:
        if not self.configure_on_start:
            return
        if self._bus is None or not self._session_handle:
            return
        try:
            self._portal().ConfigureShortcuts(
                dbus.ObjectPath(self._session_handle),
                "",
                {},
            )
        except Exception as exc:
            self._emit_registered_configure_status(
                f"Global shortcut configuration unavailable: {exc}. Existing global shortcuts remain active."
            )

    def _token(self, prefix: str) -> str:
        return f"gabbee_{prefix}_{secrets.token_hex(4)}"

    def _on_create_session_response(self, response: int, results: dict[str, object]) -> None:
        if self._create_session_done:
            return
        self._create_session_done = True
        if response != 0:
            self._emit_all_statuses(
                False,
                lambda spec: f"Global shortcut approval was skipped; focus Gabbee to use {spec.shortcut_text}.",
            )
            return
        session_handle = results.get("session_handle")
        if not isinstance(session_handle, (str, dbus.ObjectPath)) or not session_handle:
            self._emit_all_statuses(
                False,
                lambda spec: f"Global shortcut session failed; focus Gabbee to use {spec.shortcut_text}.",
            )
            return
        self._session_handle = str(session_handle)
        self._request_list_shortcuts()

    def _on_list_shortcuts_response(self, response: int, results: dict[str, object]) -> None:
        if self._list_shortcuts_done:
            return
        self._list_shortcuts_done = True
        shortcuts = results.get("shortcuts")
        if response != 0 or shortcuts is None:
            self._request_binding(self.shortcut_specs)
            return

        listed_ids = self._shortcut_ids_from_response(shortcuts)
        self._registered_ids.update(listed_ids)
        for spec in self.shortcut_specs:
            if spec.shortcut_id in listed_ids:
                self._emit_status(spec, True, f"Hold {spec.shortcut_text} anywhere to talk.")

        missing_specs = [spec for spec in self.shortcut_specs if spec.shortcut_id not in self._registered_ids]
        self._registered = bool(self._registered_ids)
        if missing_specs:
            self._request_binding(missing_specs)
        else:
            self._request_configure_shortcuts()

    def _on_bind_shortcuts_response(self, response: int, results: dict[str, object]) -> None:
        if self._bind_shortcuts_done:
            return
        self._bind_shortcuts_done = True
        shortcuts = results.get("shortcuts")
        pending_specs = self._binding_specs_pending or self.shortcut_specs
        self._binding_specs_pending = []
        if response != 0 or not shortcuts:
            for spec in pending_specs:
                if spec.shortcut_id not in self._registered_ids:
                    self._emit_status(
                        spec,
                        False,
                        f"Global {spec.shortcut_text} binding was not approved; focus Gabbee to use it locally.",
                    )
            self._registered = bool(self._registered_ids)
            self._request_configure_shortcuts()
            return
        self._registered_ids.update(self._shortcut_ids_from_response(shortcuts))
        self._registered = bool(self._registered_ids)
        for spec in pending_specs:
            if spec.shortcut_id in self._registered_ids:
                self._emit_status(spec, True, f"Hold {spec.shortcut_text} anywhere to talk.")
            else:
                self._emit_status(
                    spec,
                    False,
                    f"Global {spec.shortcut_text} binding was not approved; focus Gabbee to use it locally.",
                )
        self._request_configure_shortcuts()

    def _on_portal_activated(
        self,
        session_handle,
        shortcut_id: str,
        _timestamp,
        _options,
    ) -> None:
        if str(session_handle) != self._session_handle or str(shortcut_id) not in self._registered_ids:
            return
        self._pressed_for_id.emit(str(shortcut_id))

    def _on_portal_deactivated(
        self,
        session_handle,
        shortcut_id: str,
        _timestamp,
        _options,
    ) -> None:
        if str(session_handle) != self._session_handle or str(shortcut_id) not in self._registered_ids:
            return
        self._released_for_id.emit(str(shortcut_id))

    def _shortcut_ids_from_response(self, shortcuts) -> set[str]:
        registered: set[str] = set()
        for shortcut in shortcuts:
            try:
                shortcut_id = str(shortcut[0])
            except (IndexError, TypeError):
                continue
            if shortcut_id in self._specs_by_id:
                registered.add(shortcut_id)
        return registered

    def _emit_all_statuses(self, registered: bool, message_for_spec: Callable[[PortalShortcutSpec], str]) -> None:
        for spec in self.shortcut_specs:
            self._emit_status(spec, registered, message_for_spec(spec))

    def _emit_status(self, spec: PortalShortcutSpec, registered: bool, message: str) -> None:
        self._status_for_id.emit(spec.shortcut_id, registered, message)

    def _emit_registered_configure_status(self, message: str) -> None:
        for spec in self.shortcut_specs:
            if spec.shortcut_id in self._registered_ids:
                self._emit_status(spec, True, message)

    def _dispatch_pressed(self, shortcut_id: str) -> None:
        spec = self._specs_by_id.get(shortcut_id)
        if spec is not None and not self._dispatches_via_legacy_signal(shortcut_id):
            spec.on_pressed()
        if shortcut_id == self.shortcut_id:
            self.pressed.emit()

    def _dispatch_released(self, shortcut_id: str) -> None:
        spec = self._specs_by_id.get(shortcut_id)
        if spec is not None and not self._dispatches_via_legacy_signal(shortcut_id):
            spec.on_released()
        if shortcut_id == self.shortcut_id:
            self.released.emit()

    def _dispatch_status(self, shortcut_id: str, registered: bool, message: str) -> None:
        spec = self._specs_by_id.get(shortcut_id)
        if spec is not None and not self._dispatches_via_legacy_signal(shortcut_id):
            spec.on_status_change(registered, message)
        if shortcut_id == self.shortcut_id:
            self.status_changed.emit(registered, message)

    def _dispatches_via_legacy_signal(self, shortcut_id: str) -> bool:
        return self._uses_legacy_callbacks and shortcut_id == self.shortcut_id


class LabwcPushToTalkBinding(QObject):
    """Report compositor-owned bindings that call ``gabbee-control``."""

    def __init__(
        self,
        *,
        shortcut_specs: list[PortalShortcutSpec],
        configure_on_start: bool = False,
    ) -> None:
        super().__init__()
        self.shortcut_specs = shortcut_specs
        self.configure_on_start = configure_on_start

    def start(self) -> None:
        block = self._installed_block()
        for spec in self.shortcut_specs:
            key = qt_shortcut_to_labwc(spec.shortcut_text)
            action = "start-command" if spec.shortcut_id == "command_push_to_talk" else "start"
            pressed = f'<keybind key="{key}">' in block and f" {action}\"" in block
            released = f'<keybind key="{key}" onRelease="yes">' in block and " stop\"" in block
            registered = pressed and released
            if registered:
                message = f"Hold {spec.shortcut_text} anywhere to talk through Labwc."
            else:
                message = (
                    f"Labwc binding for {spec.shortcut_text} is missing; "
                    "run gabbee-install-labwc or focus Gabbee to use it locally."
                )
            spec.on_status_change(registered, message)

    def close(self) -> None:
        return

    @staticmethod
    def _installed_block() -> str:
        config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        rc_path = config_home / "labwc" / "rc.xml"
        try:
            text = rc_path.read_text(encoding="utf-8")
        except OSError:
            return ""
        if LABWC_START_MARKER not in text or LABWC_END_MARKER not in text:
            return ""
        return text.split(LABWC_START_MARKER, 1)[1].split(LABWC_END_MARKER, 1)[0]


def shortcut_binding_factory_for_session():
    session = ":".join(
        (
            os.environ.get("XDG_CURRENT_DESKTOP", ""),
            os.environ.get("XDG_SESSION_DESKTOP", ""),
            os.environ.get("DESKTOP_SESSION", ""),
        )
    ).casefold()
    if "labwc" in session:
        return LabwcPushToTalkBinding
    return PortalPushToTalkBinding


def normalize_portal_trigger(shortcut_text: str) -> str:
    """Convert Qt shortcut text into the portal accelerator spelling."""
    modifier_names = {
        "ctrl": "CTRL",
        "control": "CTRL",
        "alt": "ALT",
        "shift": "SHIFT",
        "meta": "LOGO",
        "super": "LOGO",
    }
    parts = [part.strip() for part in shortcut_text.split("+")]
    normalized: list[str] = []
    for part in parts:
        if not part:
            normalized.append(part)
            continue
        modifier = modifier_names.get(part.lower())
        if modifier is not None:
            normalized.append(modifier)
        elif part[:1].lower() == "f" and part[1:].isdigit():
            normalized.append(part.upper())
        elif len(part) == 1:
            normalized.append(part.lower())
        else:
            normalized.append(part)
    return "+".join(normalized)

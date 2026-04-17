from __future__ import annotations

import secrets
import threading
from typing import Callable

import dbus
from dbus.mainloop.glib import DBusGMainLoop
from PyQt6.QtCore import QObject, pyqtSignal

from gi.repository import GLib


PORTAL_SERVICE = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
PORTAL_INTERFACE = "org.freedesktop.portal.GlobalShortcuts"
REQUEST_INTERFACE = "org.freedesktop.portal.Request"
SESSION_INTERFACE = "org.freedesktop.portal.Session"
SHORTCUT_ID = "push_to_talk"


class PortalPushToTalkBinding(QObject):
    pressed = pyqtSignal()
    released = pyqtSignal()
    status_changed = pyqtSignal(bool, str)

    def __init__(
        self,
        shortcut_text: str,
        on_pressed: Callable[[], None],
        on_released: Callable[[], None],
        on_status_change: Callable[[bool, str], None],
    ) -> None:
        super().__init__()
        self.shortcut_text = shortcut_text
        self.pressed.connect(on_pressed)
        self.released.connect(on_released)
        self.status_changed.connect(on_status_change)
        self._registered = False
        self._thread: threading.Thread | None = None
        self._loop: GLib.MainLoop | None = None
        self._bus = None
        self._session_handle: str | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self.status_changed.emit(
            False,
            f"Approve the desktop-wide {self.shortcut_text} shortcut prompt, or focus Gabbee to use it locally.",
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
            self.status_changed.emit(
                False,
                f"Global shortcut portal unavailable: {exc}. Focus Gabbee to use {self.shortcut_text} locally.",
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

    def _request_session(self) -> bool:
        try:
            handle_token = self._token("create")
            request_path = self._request_path(handle_token)
            self._bus.add_signal_receiver(
                self._on_create_session_response,
                signal_name="Response",
                dbus_interface=REQUEST_INTERFACE,
                bus_name=PORTAL_SERVICE,
                path=request_path,
            )
            self._portal().CreateSession(
                {
                    "handle_token": handle_token,
                    "session_handle_token": self._token("session"),
                }
            )
        except Exception as exc:
            self.status_changed.emit(
                False,
                f"Global shortcut portal error: {exc}. Focus Gabbee to use {self.shortcut_text} locally.",
            )
        return False

    def _request_binding(self) -> None:
        if self._bus is None or not self._session_handle:
            self.status_changed.emit(
                False,
                f"Global shortcut session failed; focus Gabbee to use {self.shortcut_text}.",
            )
            return
        handle_token = self._token("bind")
        request_path = self._request_path(handle_token)
        self._bus.add_signal_receiver(
            self._on_bind_shortcuts_response,
            signal_name="Response",
            dbus_interface=REQUEST_INTERFACE,
            bus_name=PORTAL_SERVICE,
            path=request_path,
        )
        shortcuts = [
            dbus.Struct(
                [
                    SHORTCUT_ID,
                    dbus.Dictionary(
                        {
                            "description": "Gabbee push to talk",
                            "preferred_trigger": self.shortcut_text,
                        },
                        signature="sv",
                    ),
                ],
                signature=None,
            )
        ]
        self._portal().BindShortcuts(
            dbus.ObjectPath(self._session_handle),
            shortcuts,
            "",
            {"handle_token": handle_token},
        )

    def _token(self, prefix: str) -> str:
        return f"gabbee_{prefix}_{secrets.token_hex(4)}"

    def _on_create_session_response(self, response: int, results: dict[str, object]) -> None:
        if response != 0:
            self.status_changed.emit(
                False,
                f"Global shortcut approval was skipped; focus Gabbee to use {self.shortcut_text}.",
            )
            return
        session_handle = results.get("session_handle")
        if not isinstance(session_handle, (str, dbus.ObjectPath)) or not session_handle:
            self.status_changed.emit(
                False,
                f"Global shortcut session failed; focus Gabbee to use {self.shortcut_text}.",
            )
            return
        self._session_handle = str(session_handle)
        self._request_binding()

    def _on_bind_shortcuts_response(self, response: int, results: dict[str, object]) -> None:
        shortcuts = results.get("shortcuts")
        if response != 0 or not shortcuts:
            self.status_changed.emit(
                False,
                f"Global {self.shortcut_text} binding was not approved; focus Gabbee to use it locally.",
            )
            return
        self._registered = True
        self.status_changed.emit(True, f"Hold {self.shortcut_text} anywhere to talk.")

    def _on_portal_activated(
        self,
        session_handle,
        shortcut_id: str,
        _timestamp,
        _options,
    ) -> None:
        if not self._registered or str(session_handle) != self._session_handle or shortcut_id != SHORTCUT_ID:
            return
        self.pressed.emit()

    def _on_portal_deactivated(
        self,
        session_handle,
        shortcut_id: str,
        _timestamp,
        _options,
    ) -> None:
        if not self._registered or str(session_handle) != self._session_handle or shortcut_id != SHORTCUT_ID:
            return
        self.released.emit()

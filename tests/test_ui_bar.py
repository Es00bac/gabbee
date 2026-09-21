from __future__ import annotations

from pathlib import Path
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication

from gabbee.models import ControllerSnapshot, ControllerState
from gabbee.ui.bar import FloatingBar
from gabbee.ui.global_shortcuts import (
    LabwcPushToTalkBinding,
    PortalPushToTalkBinding,
    PortalShortcutSpec,
    normalize_portal_trigger,
    shortcut_binding_factory_for_session,
)


class FakeController:
    def __init__(self) -> None:
        self.started = 0
        self.command_started = 0
        self.stopped = 0
        self.cancelled = 0
        self._listener = None

    def add_listener(self, listener) -> None:
        self._listener = listener
        listener(
            ControllerSnapshot(
                state=ControllerState.IDLE,
                provider="elevenlabs",
                delivery_method="FallbackTextSink",
            )
        )

    def start(self) -> None:
        self.started += 1

    def start_command(self) -> None:
        self.command_started += 1

    def stop(self) -> None:
        self.stopped += 1

    def cancel(self) -> None:
        self.cancelled += 1


class FakeShortcutBinding:
    def __init__(
        self,
        shortcut_text=None,
        on_pressed=None,
        on_released=None,
        on_status_change=None,
        registered=True,
        shortcut_id="push_to_talk",
        description="Gabbee push to talk",
        shortcut_specs=None,
        configure_on_start=False,
    ) -> None:
        if shortcut_specs is None:
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
        self.shortcut_text = shortcut_specs[0].shortcut_text
        self.shortcut_id = shortcut_specs[0].shortcut_id
        self.description = shortcut_specs[0].description
        self.on_pressed = shortcut_specs[0].on_pressed
        self.on_released = shortcut_specs[0].on_released
        self.on_status_change = shortcut_specs[0].on_status_change
        self.configure_on_start = configure_on_start
        self.registered = registered
        self.closed = False

    def start(self) -> None:
        for spec in self.shortcut_specs:
            if self._is_registered(spec.shortcut_id):
                spec.on_status_change(True, f"Hold {spec.shortcut_text} anywhere to talk.")
            else:
                spec.on_status_change(False, f"Focus Gabbee to use {spec.shortcut_text} locally.")

    def close(self) -> None:
        self.closed = True

    def press(self, shortcut_id) -> None:
        self._spec(shortcut_id).on_pressed()

    def release(self, shortcut_id) -> None:
        self._spec(shortcut_id).on_released()

    def _is_registered(self, shortcut_id) -> bool:
        if isinstance(self.registered, bool):
            return self.registered
        return shortcut_id in self.registered

    def _spec(self, shortcut_id):
        for spec in self.shortcut_specs:
            if spec.shortcut_id == shortcut_id:
                return spec
        raise KeyError(shortcut_id)


class FakeShortcutBus:
    def __init__(self) -> None:
        self.signal_paths = []

    def get_unique_name(self) -> str:
        return ":1.234"

    def add_signal_receiver(self, _handler, **kwargs) -> None:
        self.signal_paths.append(kwargs["path"])


class FakePortal:
    def __init__(
        self,
        create_handle="/returned/create",
        list_handle="/returned/list",
        bind_handle="/returned/bind",
    ) -> None:
        self.create_handle = create_handle
        self.list_handle = list_handle
        self.bind_handle = bind_handle
        self.create_options = None
        self.list_options = None
        self.bind_options = None
        self.configure_options = None
        self.list_session_handle = None
        self.configure_session_handle = None
        self.shortcuts = None
        self.configure_calls = 0

    def CreateSession(self, options):
        self.create_options = options
        return self.create_handle

    def ListShortcuts(self, session_handle, options):
        self.list_session_handle = session_handle
        self.list_options = options
        return self.list_handle

    def BindShortcuts(self, _session_handle, shortcuts, _parent_window, options):
        self.shortcuts = shortcuts
        self.bind_options = options
        return self.bind_handle

    def ConfigureShortcuts(self, session_handle, _parent_window, options):
        self.configure_calls += 1
        self.configure_session_handle = session_handle
        self.configure_options = options
        return None


class FloatingBarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_uses_configured_ptt_shortcut(self) -> None:
        controller = FakeController()
        window = FloatingBar(
            self.app,
            controller,
            toggle_shortcut="F5",
            command_shortcut="F6",
            global_shortcut_factory=lambda **kwargs: FakeShortcutBinding(registered=False, **kwargs),
        )
        self.assertEqual(window.shortcut_sequence.toString(), "F5")
        self.assertIn("F5", window.hint_label.text())
        press = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_F5, Qt.KeyboardModifier.NoModifier)
        release = QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_F5, Qt.KeyboardModifier.NoModifier)
        self.assertTrue(window.eventFilter(self.app, press))
        self.assertEqual(controller.started, 1)
        self.assertTrue(window.eventFilter(self.app, release))
        self.assertEqual(controller.stopped, 1)
        window.close()

    def test_labwc_binding_reports_installed_compositor_shortcuts(self) -> None:
        controller = FakeController()
        with tempfile.TemporaryDirectory() as temp_dir:
            rc = Path(temp_dir) / "labwc" / "rc.xml"
            rc.parent.mkdir()
            rc.write_text(
                """<labwc_config><keyboard>
<!-- Gabbee push-to-talk shortcuts: begin -->
<keybind key="F24"><action command="gabbee-control start" /></keybind>
<keybind key="F24" onRelease="yes"><action command="gabbee-control stop" /></keybind>
<keybind key="F23"><action command="gabbee-control start-command" /></keybind>
<keybind key="F23" onRelease="yes"><action command="gabbee-control stop" /></keybind>
<!-- Gabbee push-to-talk shortcuts: end -->
</keyboard></labwc_config>
""",
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {
                    "XDG_CURRENT_DESKTOP": "LXQt:labwc:wlroots",
                    "XDG_CONFIG_HOME": temp_dir,
                },
            ):
                self.assertIs(shortcut_binding_factory_for_session(), LabwcPushToTalkBinding)
                window = FloatingBar(
                    self.app,
                    controller,
                    toggle_shortcut="F24",
                    command_shortcut="F23",
                    global_shortcut_factory=LabwcPushToTalkBinding,
                )

        self.assertIn("F24 anywhere", window.hint_label.text())
        self.assertIn("F23 anywhere", window.hint_label.text())
        window.close()

    def test_uses_configured_command_shortcut_for_command_mode(self) -> None:
        controller = FakeController()
        window = FloatingBar(
            self.app,
            controller,
            toggle_shortcut="F5",
            command_shortcut="F6",
            global_shortcut_factory=lambda **kwargs: FakeShortcutBinding(registered=False, **kwargs),
        )
        self.assertEqual(window.command_shortcut_sequence.toString(), "F6")
        self.assertIn("F6", window.hint_label.text())
        press = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_F6, Qt.KeyboardModifier.NoModifier)
        release = QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_F6, Qt.KeyboardModifier.NoModifier)
        self.assertTrue(window.eventFilter(self.app, press))
        self.assertEqual(controller.command_started, 1)
        self.assertEqual(controller.started, 0)
        self.assertTrue(window.eventFilter(self.app, release))
        self.assertEqual(controller.stopped, 1)
        window.close()

    def test_global_shortcut_binding_disables_local_fallback_and_dispatches_callbacks(self) -> None:
        controller = FakeController()
        bindings = []

        def factory(**kwargs):
            binding = FakeShortcutBinding(registered=True, **kwargs)
            bindings.append(binding)
            return binding

        window = FloatingBar(self.app, controller, toggle_shortcut="F5", command_shortcut="F6", global_shortcut_factory=factory)
        self.assertIn("anywhere", window.hint_label.text())
        press = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_F5, Qt.KeyboardModifier.NoModifier)
        self.assertFalse(window.eventFilter(self.app, press))
        command_press = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_F6, Qt.KeyboardModifier.NoModifier)
        self.assertFalse(window.eventFilter(self.app, command_press))
        self.assertEqual(controller.started, 0)
        self.assertEqual(controller.command_started, 0)
        bindings[0].press("dictation_push_to_talk")
        bindings[0].release("dictation_push_to_talk")
        bindings[0].press("command_push_to_talk")
        bindings[0].release("command_push_to_talk")
        self.assertEqual(controller.started, 1)
        self.assertEqual(controller.command_started, 1)
        self.assertEqual(controller.stopped, 2)
        window.close()
        self.assertTrue(all(binding.closed for binding in bindings))

    def test_unapproved_portal_shortcut_keeps_individual_local_fallback(self) -> None:
        controller = FakeController()
        window = FloatingBar(
            self.app,
            controller,
            toggle_shortcut="F5",
            command_shortcut="F6",
            global_shortcut_factory=lambda **kwargs: FakeShortcutBinding(
                registered={"dictation_push_to_talk"},
                **kwargs,
            ),
        )

        dictation_press = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_F5, Qt.KeyboardModifier.NoModifier)
        command_press = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_F6, Qt.KeyboardModifier.NoModifier)

        self.assertFalse(window.eventFilter(self.app, dictation_press))
        self.assertTrue(window.eventFilter(self.app, command_press))
        self.assertEqual(controller.started, 0)
        self.assertEqual(controller.command_started, 1)
        window.close()

    def test_window_is_shown_without_taking_focus(self) -> None:
        controller = FakeController()
        window = FloatingBar(
            self.app,
            controller,
            toggle_shortcut="F5",
            command_shortcut="F6",
            global_shortcut_factory=lambda **kwargs: FakeShortcutBinding(registered=True, **kwargs),
        )
        self.assertTrue(window.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus)
        self.assertTrue(window.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating))
        self.assertEqual(window.start_button.focusPolicy(), Qt.FocusPolicy.NoFocus)
        self.assertEqual(window.stop_button.focusPolicy(), Qt.FocusPolicy.NoFocus)
        self.assertEqual(window.cancel_button.focusPolicy(), Qt.FocusPolicy.NoFocus)
        self.assertLessEqual(window.last_text_label.maximumWidth(), 320)
        self.assertTrue(window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        self.assertTrue(window.pin_button.isCheckable())
        self.assertTrue(window.pin_button.isChecked())
        window._set_pinned(False)
        self.assertFalse(window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        window.close()

    def test_hide_button_hides_bar_without_closing(self) -> None:
        controller = FakeController()
        window = FloatingBar(
            self.app,
            controller,
            toggle_shortcut="F5",
            command_shortcut="F6",
            global_shortcut_factory=lambda **kwargs: FakeShortcutBinding(registered=True, **kwargs),
        )
        window.show()
        self.app.processEvents()

        window.hide_button.click()
        self.app.processEvents()

        self.assertFalse(window.isVisible())
        self.assertEqual(controller.started, 0)
        window.close()

    def test_pinned_refresh_does_not_show_hidden_bar(self) -> None:
        controller = FakeController()
        window = FloatingBar(
            self.app,
            controller,
            toggle_shortcut="F5",
            command_shortcut="F6",
            global_shortcut_factory=lambda **kwargs: FakeShortcutBinding(registered=True, **kwargs),
        )
        window.show()
        self.app.processEvents()

        window.hide_bar()
        window._refresh_pin_state()
        self.app.processEvents()

        self.assertFalse(window.isVisible())
        window.close()

    def test_show_bar_restores_and_raises_bar(self) -> None:
        controller = FakeController()
        window = FloatingBar(
            self.app,
            controller,
            toggle_shortcut="F5",
            command_shortcut="F6",
            global_shortcut_factory=lambda **kwargs: FakeShortcutBinding(registered=True, **kwargs),
        )
        window.hide_bar()

        window.show_bar()
        self.app.processEvents()

        self.assertTrue(window.isVisible())
        window.close()

    def test_global_shortcuts_use_distinct_ids_and_descriptions(self) -> None:
        controller = FakeController()
        bindings = []

        def factory(**kwargs):
            binding = FakeShortcutBinding(registered=True, **kwargs)
            bindings.append(binding)
            return binding

        window = FloatingBar(
            self.app,
            controller,
            toggle_shortcut="F5",
            command_shortcut="F6",
            global_shortcut_factory=factory,
        )

        self.assertEqual(len(bindings), 1)
        self.assertEqual(len(bindings[0].shortcut_specs), 2)
        self.assertEqual(bindings[0].shortcut_specs[0].shortcut_id, "dictation_push_to_talk")
        self.assertEqual(bindings[0].shortcut_specs[0].description, "Gabbee dictation push to talk")
        self.assertEqual(bindings[0].shortcut_specs[1].shortcut_id, "command_push_to_talk")
        self.assertEqual(bindings[0].shortcut_specs[1].description, "Gabbee command push to talk")
        window.close()

    def test_apply_shortcuts_closes_old_bindings_and_creates_new_ones(self) -> None:
        controller = FakeController()
        bindings = []

        def factory(**kwargs):
            binding = FakeShortcutBinding(registered=True, **kwargs)
            bindings.append(binding)
            return binding

        window = FloatingBar(
            self.app,
            controller,
            toggle_shortcut="F5",
            command_shortcut="F6",
            global_shortcut_factory=factory,
        )

        window.apply_shortcuts("F7", "F23")

        self.assertTrue(bindings[0].closed)
        self.assertEqual(window.shortcut_sequence.toString(), "F7")
        self.assertEqual(window.command_shortcut_sequence.toString(), "F23")
        self.assertEqual(len(bindings), 2)
        self.assertFalse(bindings[0].configure_on_start)
        self.assertTrue(bindings[1].configure_on_start)
        self.assertEqual(bindings[1].shortcut_specs[0].shortcut_text, "F7")
        self.assertEqual(bindings[1].shortcut_specs[1].shortcut_text, "F23")
        window.close()

    def test_apply_shortcuts_updates_f23_command_local_fallback(self) -> None:
        controller = FakeController()
        window = FloatingBar(
            self.app,
            controller,
            toggle_shortcut="F5",
            command_shortcut="F6",
            global_shortcut_factory=lambda **kwargs: FakeShortcutBinding(registered=False, **kwargs),
        )

        window.apply_shortcuts("F5", "F23")
        press = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_F23, Qt.KeyboardModifier.NoModifier)
        release = QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_F23, Qt.KeyboardModifier.NoModifier)

        self.assertTrue(window.eventFilter(self.app, press))
        self.assertEqual(controller.command_started, 1)
        self.assertTrue(window.eventFilter(self.app, release))
        self.assertEqual(controller.stopped, 1)
        window.close()

    def test_portal_session_uses_returned_request_handle(self) -> None:
        binding = PortalPushToTalkBinding("Ctrl+Alt+F5", lambda: None, lambda: None, lambda _ok, _msg: None)
        bus = FakeShortcutBus()
        portal = FakePortal(create_handle="/org/freedesktop/portal/desktop/request/other/create")
        binding._bus = bus
        binding._portal = lambda: portal

        binding._request_session()

        self.assertEqual(len(bus.signal_paths), 2)
        self.assertIn("/org/freedesktop/portal/desktop/request/1_234/" + portal.create_options["handle_token"], bus.signal_paths)
        self.assertIn(portal.create_handle, bus.signal_paths)

    def test_missing_kde_global_shortcuts_portal_repairs_environment_once(self) -> None:
        events = []
        binding = PortalPushToTalkBinding(
            "F24",
            lambda: None,
            lambda: None,
            lambda ok, message: events.append((ok, message)),
        )
        binding._bus = FakeShortcutBus()
        binding._portal = lambda: (_ for _ in ()).throw(
            RuntimeError(
                "org.freedesktop.DBus.Error.UnknownMethod: "
                "No such interface “org.freedesktop.portal.GlobalShortcuts” "
                "on object at path /org/freedesktop/portal/desktop"
            )
        )

        with patch.dict(
            os.environ,
            {
                "XDG_CURRENT_DESKTOP": "KDE",
                "DISPLAY": ":1",
                "WAYLAND_DISPLAY": "wayland-0",
            },
            clear=False,
        ), patch("subprocess.run") as run, patch(
            "gabbee.ui.global_shortcuts.GLib.timeout_add_seconds"
        ) as add_timeout:
            result = binding._request_session()

        self.assertFalse(result)
        self.assertEqual(run.call_count, 2)
        self.assertIn("dbus-update-activation-environment", run.call_args_list[0].args[0][0])
        self.assertEqual(run.call_args_list[1].args[0][:4], ["systemctl", "--user", "try-restart", "xdg-desktop-portal.service"])
        self.assertEqual(add_timeout.call_args.args[0], 2)
        self.assertIs(add_timeout.call_args.args[1].__self__, binding)
        self.assertIs(add_timeout.call_args.args[1].__func__, PortalPushToTalkBinding._request_session)
        self.assertFalse(events[-1][0])
        self.assertIn("Refreshing KDE desktop portal", events[-1][1])

    def test_portal_lists_existing_shortcuts_without_binding_and_optionally_configures(self) -> None:
        events = []
        binding = PortalPushToTalkBinding(
            shortcut_specs=[
                PortalShortcutSpec(
                    "dictation_push_to_talk",
                    "F5",
                    "Dictation",
                    lambda: None,
                    lambda: None,
                    lambda ok, message: events.append(("dictation", ok, message)),
                ),
                PortalShortcutSpec(
                    "command_push_to_talk",
                    "F6",
                    "Command",
                    lambda: None,
                    lambda: None,
                    lambda ok, message: events.append(("command", ok, message)),
                ),
            ],
            configure_on_start=True,
        )
        bus = FakeShortcutBus()
        portal = FakePortal()
        binding._bus = bus
        binding._portal = lambda: portal
        binding._session_handle = "/org/freedesktop/portal/desktop/session/1_234/session"

        binding._on_list_shortcuts_response(
            0,
            {
                "shortcuts": [
                    ("dictation_push_to_talk", {}),
                    ("command_push_to_talk", {}),
                ]
            },
        )

        self.assertIsNone(portal.shortcuts)
        self.assertEqual(binding._registered_ids, {"dictation_push_to_talk", "command_push_to_talk"})
        self.assertTrue(binding._registered)
        self.assertEqual(portal.configure_calls, 1)
        self.assertEqual(portal.configure_options, {})
        self.assertEqual(str(portal.configure_session_handle), binding._session_handle)
        self.assertEqual(events[0][:2], ("dictation", True))
        self.assertEqual(events[1][:2], ("command", True))

        no_config_binding = PortalPushToTalkBinding(
            shortcut_specs=binding.shortcut_specs,
            configure_on_start=False,
        )
        no_config_portal = FakePortal()
        no_config_binding._bus = FakeShortcutBus()
        no_config_binding._portal = lambda: no_config_portal
        no_config_binding._session_handle = "/org/freedesktop/portal/desktop/session/1_234/session"

        no_config_binding._on_list_shortcuts_response(
            0, {"shortcuts": [("dictation_push_to_talk", {}), ("command_push_to_talk", {})]},
        )

        self.assertEqual(no_config_portal.configure_calls, 0)

    def test_portal_list_missing_current_shortcuts_binds_missing_ids_only(self) -> None:
        binding = PortalPushToTalkBinding(
            shortcut_specs=[
                PortalShortcutSpec(
                    "dictation_push_to_talk",
                    "Ctrl+Alt+F5",
                    "Dictation",
                    lambda: None,
                    lambda: None,
                    lambda _ok, _msg: None,
                ),
                PortalShortcutSpec(
                    "command_push_to_talk",
                    "Meta+Shift+A",
                    "Command",
                    lambda: None,
                    lambda: None,
                    lambda _ok, _msg: None,
                ),
            ]
        )
        bus = FakeShortcutBus()
        portal = FakePortal(bind_handle="/org/freedesktop/portal/desktop/request/other/bind")
        binding._bus = bus
        binding._portal = lambda: portal
        binding._session_handle = "/org/freedesktop/portal/desktop/session/1_234/session"

        binding._on_list_shortcuts_response(0, {"shortcuts": [("push_to_talk", {})]})

        self.assertEqual(portal.shortcuts.signature, "(sa{sv})")
        self.assertEqual([shortcut[0] for shortcut in portal.shortcuts], ["dictation_push_to_talk", "command_push_to_talk"])
        self.assertEqual(portal.shortcuts[0][1]["preferred_trigger"], "CTRL+ALT+F5")
        self.assertEqual(portal.shortcuts[1][1]["preferred_trigger"], "LOGO+SHIFT+a")

    def test_portal_list_missing_one_current_shortcut_preserves_existing_binding(self) -> None:
        events = []
        binding = PortalPushToTalkBinding(
            shortcut_specs=[
                PortalShortcutSpec(
                    "dictation_push_to_talk",
                    "F5",
                    "Dictation",
                    lambda: None,
                    lambda: None,
                    lambda ok, message: events.append(("dictation", ok, message)),
                ),
                PortalShortcutSpec(
                    "command_push_to_talk",
                    "F6",
                    "Command",
                    lambda: None,
                    lambda: None,
                    lambda ok, message: events.append(("command", ok, message)),
                ),
            ]
        )
        portal = FakePortal()
        binding._bus = FakeShortcutBus()
        binding._portal = lambda: portal
        binding._session_handle = "/org/freedesktop/portal/desktop/session/1_234/session"

        binding._on_list_shortcuts_response(0, {"shortcuts": [("dictation_push_to_talk", {})]})

        self.assertEqual(binding._registered_ids, {"dictation_push_to_talk"})
        self.assertEqual([shortcut[0] for shortcut in portal.shortcuts], ["command_push_to_talk"])
        self.assertEqual(events[0][:2], ("dictation", True))

    def test_legacy_portal_binding_public_signals_invoke_callbacks(self) -> None:
        events = []
        binding = PortalPushToTalkBinding(
            "Ctrl+Alt+F5",
            lambda: events.append("pressed"),
            lambda: events.append("released"),
            lambda ok, message: events.append((ok, message)),
        )

        binding.pressed.emit()
        binding.released.emit()
        binding.status_changed.emit(True, "ready")

        self.assertEqual(events, ["pressed", "released", (True, "ready")])

    def test_portal_binding_uses_returned_request_handle_and_normalized_trigger(self) -> None:
        binding = PortalPushToTalkBinding(
            shortcut_specs=[
                PortalShortcutSpec(
                    "dictation_push_to_talk",
                    "Ctrl+Alt+F5",
                    "Dictation",
                    lambda: None,
                    lambda: None,
                    lambda _ok, _msg: None,
                ),
                PortalShortcutSpec(
                    "command_push_to_talk",
                    "Meta+Shift+A",
                    "Command",
                    lambda: None,
                    lambda: None,
                    lambda _ok, _msg: None,
                ),
            ]
        )
        bus = FakeShortcutBus()
        portal = FakePortal(bind_handle="/org/freedesktop/portal/desktop/request/other/bind")
        binding._bus = bus
        binding._portal = lambda: portal
        binding._session_handle = "/org/freedesktop/portal/desktop/session/1_234/session"

        binding._request_binding()

        self.assertEqual(len(bus.signal_paths), 2)
        self.assertIn("/org/freedesktop/portal/desktop/request/1_234/" + portal.bind_options["handle_token"], bus.signal_paths)
        self.assertIn(portal.bind_handle, bus.signal_paths)
        self.assertEqual(portal.shortcuts.signature, "(sa{sv})")
        self.assertEqual(len(portal.shortcuts), 2)
        self.assertEqual(portal.shortcuts[0][0], "dictation_push_to_talk")
        self.assertEqual(portal.shortcuts[0][1]["preferred_trigger"], "CTRL+ALT+F5")
        self.assertEqual(portal.shortcuts[1][0], "command_push_to_talk")
        self.assertEqual(portal.shortcuts[1][1]["preferred_trigger"], "LOGO+SHIFT+a")

    def test_normalize_portal_trigger_keeps_local_shortcut_text_separate(self) -> None:
        self.assertEqual(normalize_portal_trigger("Ctrl+Alt+F5"), "CTRL+ALT+F5")
        self.assertEqual(normalize_portal_trigger("Meta+Shift+A"), "LOGO+SHIFT+a")
        self.assertEqual(normalize_portal_trigger("Super+Alt+B"), "LOGO+ALT+b")
        self.assertEqual(normalize_portal_trigger("F23"), "F23")


if __name__ == "__main__":
    unittest.main()

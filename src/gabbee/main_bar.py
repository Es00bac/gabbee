from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QDialog

from .config import load_config
from .control import GabbeeControlService
from .controller import GabbeeController
from .qindaqt_voice import QindaQtVoiceService

from .ui.bar import FloatingBar
from .ui.command_studio import CommandStudioWindow
from .ui.tray import GabbeeTrayIcon
from .ui.config_window import ConfigWindow
from .ui.first_run import FirstRunWizard
from .ui.global_shortcuts import shortcut_binding_factory_for_session
from .ui.sounds import FeedbackSounds


def main(argv: list[str] | None = None) -> int:
    _argv = argv or sys.argv
    try:
        config = load_config()
    except Exception as e:
        print(f"Failed to load Gabbee configuration: {e}", file=sys.stderr)
        return 1

    try:
        app = QApplication(_argv)
    except Exception as e:
        print(f"Failed to initialize Gabbee UI: {e}", file=sys.stderr)
        return 1

    app.setApplicationName("Gabbee")
    app.setDesktopFileName("gabbee-bar")
    app.setQuitOnLastWindowClosed(False)

    icon_path = Path(__file__).parent.parent.parent / "gabbee.png"
    app_icon = QIcon(str(icon_path))
    app.setWindowIcon(app_icon)

    if not config.paths.setup_marker.exists():
        wizard = FirstRunWizard(config)
        if wizard.exec() != QDialog.DialogCode.Accepted:
            return 0

    try:
        controller = GabbeeController(config, sounds=FeedbackSounds())
    except Exception as e:
        print(f"Failed to initialize Gabbee controller: {e}", file=sys.stderr)
        return 1
    app.aboutToQuit.connect(controller.shutdown)

    control_service = GabbeeControlService(controller)
    if not control_service.open():
        print(
            "Warning: desktop-wide compositor shortcuts cannot reach Gabbee; "
            "another Gabbee bar may already be running.",
            file=sys.stderr,
        )
    app.aboutToQuit.connect(control_service.close)

    # The QindaQt desktop's voice contract. Its absence is not fatal: Gabbee
    # keeps working on desktops that do not consume org.qindaqt.Voice1, and a
    # name already taken means another provider is serving this session.
    voice_service = QindaQtVoiceService(controller, config)
    if not voice_service.open():
        print(
            "Note: the QindaQt voice applet cannot reach Gabbee; "
            "another org.qindaqt.Voice1 provider may already own that name.",
            file=sys.stderr,
        )
    app.aboutToQuit.connect(voice_service.close)

    try:
        window = FloatingBar(
            app=app,
            controller=controller,
            title=config.ui_title,
            toggle_shortcut=config.toggle_shortcut,
            command_shortcut=config.command_shortcut,
            global_shortcut_factory=shortcut_binding_factory_for_session(),
        )
    except Exception as e:
        print(f"Failed to initialize Gabbee bar: {e}", file=sys.stderr)
        return 1
    
    tray = GabbeeTrayIcon(app_icon, window)
    tray.show_bar_action.triggered.connect(window.show_bar)
    tray.hide_bar_action.triggered.connect(window.hide_bar)
    tray.quit_action.triggered.connect(app.quit)

    def show_command_studio():
        captured_app = controller.capture_current_app()
        dialog = CommandStudioWindow(
            config.paths.advanced_config_file,
            window,
            capture_app=lambda: captured_app or controller.capture_current_app(),
            capture_control=lambda: controller.capture_current_control(captured_app),
        )
        active_profile = controller.profile_manager.select(captured_app)
        if active_profile is not None:
            dialog.preview_profile.setCurrentText(active_profile.name)
        dialog.exec()
        controller.reload_transcriber()

    def show_config():
        diag = ConfigWindow(config, window)
        diag.command_studio_requested.connect(show_command_studio)
        if diag.exec():
            updates = diag.get_config_dict()
            config.save(updates)
            controller.reload_transcriber()
            window.apply_shortcuts(config.toggle_shortcut, config.command_shortcut)

    tray.config_action.triggered.connect(show_config)
    tray.command_studio_action.triggered.connect(show_command_studio)
    window.settings_requested.connect(show_config)
    tray.show()

    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

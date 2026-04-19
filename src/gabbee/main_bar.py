from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from .config import load_config
from .controller import GabbeeController
from .ui.bar import FloatingBar
from .ui.tray import GabbeeTrayIcon
from .ui.config_window import ConfigWindow


def main(argv: list[str] | None = None) -> int:
    _argv = argv or sys.argv
    config = load_config()

    app = QApplication(_argv)
    app.setApplicationName("Gabbee")
    app.setDesktopFileName("gabbee-bar")
    app.setQuitOnLastWindowClosed(False) # Keep running with tray

    icon_path = Path(__file__).parent.parent.parent / "gabbee.png"
    app_icon = QIcon(str(icon_path))
    app.setWindowIcon(app_icon)

    controller = GabbeeController(config)
    window = FloatingBar(
        app=app,
        controller=controller,
        title=config.ui_title,
        toggle_shortcut=config.toggle_shortcut,
    )
    
    tray = GabbeeTrayIcon(app_icon, window)
    tray.show_bar_action.triggered.connect(window.show)
    tray.quit_action.triggered.connect(app.quit)
    
    def show_config():
        diag = ConfigWindow(config, window)
        if diag.exec():
            updates = diag.get_config_dict()
            config.save(updates)
            # Potentially notify controller if stt_provider changed
            # For now, a restart might be needed for some changes, 
            # but we update the config object in place.
    
    tray.config_action.triggered.connect(show_config)
    window.settings_requested.connect(show_config)
    tray.show()

    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

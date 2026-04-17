from __future__ import annotations

import sys

from PyQt6.QtWidgets import QApplication

from .config import load_config
from .controller import GabbeeController
from .ui.bar import FloatingBar


def main(argv: list[str] | None = None) -> int:
    _argv = argv or sys.argv
    config = load_config()

    app = QApplication(_argv)
    app.setApplicationName("Gabbee")
    app.setDesktopFileName("gabbee-bar")
    app.setQuitOnLastWindowClosed(True)

    controller = GabbeeController(config)
    window = FloatingBar(
        app=app,
        controller=controller,
        title=config.ui_title,
        toggle_shortcut=config.toggle_shortcut,
    )
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

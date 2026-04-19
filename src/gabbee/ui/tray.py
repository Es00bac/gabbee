from __future__ import annotations

from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import QMenu, QSystemTrayIcon, QWidget


class GabbeeTrayIcon(QSystemTrayIcon):
    def __init__(self, icon: QIcon, parent: QWidget | None = None) -> None:
        super().__init__(icon, parent)
        self.setToolTip("Gabbee Voice Input")
        
        self.menu = QMenu(parent)
        
        self.show_bar_action = QAction("Show Bar", self)
        self.menu.addAction(self.show_bar_action)
        
        self.config_action = QAction("Configuration...", self)
        self.menu.addAction(self.config_action)
        
        self.menu.addSeparator()
        
        self.quit_action = QAction("Quit", self)
        self.menu.addAction(self.quit_action)
        
        self.setContextMenu(self.menu)
        self.activated.connect(self._on_activated)

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.show_bar_action.trigger()

from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import QEvent, QPoint, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QKeyEvent, QKeySequence
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..controller import GabbeeController
from ..models import ControllerSnapshot, ControllerState
from .global_shortcuts import PortalPushToTalkBinding


class _SnapshotBus(QWidget):
    snapshot_ready = pyqtSignal(object)


class FloatingBar(QWidget):
    def __init__(
        self,
        app: QApplication,
        controller: GabbeeController,
        title: str = "Gabbee",
        toggle_shortcut: str = "F5",
        global_shortcut_factory: Callable[..., object] | None = PortalPushToTalkBinding,
    ) -> None:
        super().__init__()
        self.app = app
        self.controller = controller
        self.title = title
        self.toggle_shortcut_text = toggle_shortcut
        self.shortcut_sequence = QKeySequence(self.toggle_shortcut_text)
        self._shortcut_pressed = False
        self._use_local_shortcut = True
        self._global_shortcut = None
        self._pinned = True
        self._drag_origin: QPoint | None = None
        self._pin_refresh_timer = QTimer(self)
        self._pin_refresh_timer.setInterval(1500)
        self._pin_refresh_timer.timeout.connect(self._refresh_pin_state)
        self._snapshot_bus = _SnapshotBus()
        self._snapshot_bus.snapshot_ready.connect(self._apply_snapshot)

        self.setWindowTitle(title)
        self._apply_window_flags()
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)

        card = QFrame()
        card.setObjectName("card")
        root.addWidget(card)

        outer = QVBoxLayout(card)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(4)

        header = QHBoxLayout()
        header.setSpacing(6)
        outer.addLayout(header)

        title_label = QLabel(self.title)
        title_font = QFont()
        title_font.setPointSize(11)
        title_font.setBold(True)
        title_label.setFont(title_font)
        header.addWidget(title_label)

        self.status_chip = QLabel("Idle")
        self.status_chip.setObjectName("statusChip")
        header.addWidget(self.status_chip)
        header.addStretch(1)

        self.pin_button = QPushButton("Pin")
        self.pin_button.setCheckable(True)
        self.pin_button.setChecked(True)
        self.pin_button.setText("Pinned")
        self.pin_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.pin_button.clicked.connect(self._set_pinned)
        header.addWidget(self.pin_button)

        self.start_button = QPushButton("Start")
        self.stop_button = QPushButton("Stop")
        self.cancel_button = QPushButton("Cancel")
        self.start_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.stop_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.cancel_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self.start_button.clicked.connect(self.controller.start)
        self.stop_button.clicked.connect(self.controller.stop)
        self.cancel_button.clicked.connect(self.controller.cancel)

        self.provider_label = QLabel("Provider: unknown")
        self.provider_label.setObjectName("subtle")
        self.provider_label.hide()
        header.addWidget(self.provider_label)

        self.last_text_label = QLabel("No transcript yet.")
        self.last_text_label.setWordWrap(False)
        self.last_text_label.setObjectName("preview")
        self.last_text_label.setMinimumWidth(140)
        self.last_text_label.setMaximumWidth(220)
        self.last_text_label.setMaximumHeight(20)
        self.last_text_label.hide()
        header.addWidget(self.last_text_label, 1)

        header.addWidget(self.start_button)
        header.addWidget(self.stop_button)
        header.addWidget(self.cancel_button)

        self.hint_label = QLabel(f"Hold {self.toggle_shortcut_text} while Gabbee is focused, or use the buttons.")
        self.hint_label.setObjectName("subtle")
        self.hint_label.hide()
        outer.addWidget(self.hint_label)

        self.setStyleSheet(
            """
            QWidget {
                color: #ecf4f1;
                font-size: 11px;
            }
            QFrame#card {
                background: rgba(16, 24, 32, 238);
                border: 1px solid rgba(98, 161, 150, 120);
                border-radius: 10px;
            }
            QLabel#statusChip {
                background: rgba(44, 113, 102, 190);
                border-radius: 8px;
                padding: 2px 7px;
                color: #dff7f0;
                font-weight: 600;
            }
            QLabel#subtle {
                color: #9cc4bc;
            }
            QLabel#preview {
                background: rgba(255, 255, 255, 18);
                border-radius: 6px;
                padding: 3px 6px;
            }
            QPushButton {
                background: #1d7f73;
                color: white;
                border: none;
                border-radius: 7px;
                padding: 4px 8px;
                min-width: 44px;
                font-weight: 600;
            }
            QPushButton:disabled {
                background: #51636a;
                color: #cad4d0;
            }
            QPushButton:hover:!disabled {
                background: #24988a;
            }
            """
        )

        self.controller.add_listener(self._queue_snapshot)
        self.app.installEventFilter(self)
        self._setup_shortcut_binding(global_shortcut_factory)
        self._move_to_default_position()
        self._pin_refresh_timer.start()
        self._refresh_pin_state()

    def _apply_window_flags(self) -> None:
        flags = (
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        if self._pinned:
            flags |= Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.BypassWindowManagerHint
        self.setWindowFlags(flags)

    def _set_pinned(self, pinned: bool) -> None:
        self._pinned = pinned
        self.pin_button.setText("Pinned" if pinned else "Pin")
        self._apply_window_flags()
        self.show()
        if pinned:
            self._pin_refresh_timer.start()
        else:
            self._pin_refresh_timer.stop()
        self._refresh_pin_state()

    def _refresh_pin_state(self) -> None:
        if not self._pinned:
            return
        self.show()
        self.raise_()
        handle = self.windowHandle()
        if handle is not None:
            handle.raise_()

    def _setup_shortcut_binding(self, global_shortcut_factory: Callable[..., object] | None) -> None:
        if global_shortcut_factory is None:
            return
        try:
            self._global_shortcut = global_shortcut_factory(
                shortcut_text=self.toggle_shortcut_text,
                on_pressed=self._trigger_shortcut_press,
                on_released=self._trigger_shortcut_release,
                on_status_change=self._update_shortcut_status,
            )
        except Exception:
            self._global_shortcut = None
            self._update_shortcut_status(
                False,
                f"Global shortcut setup failed; focus Gabbee to use {self.toggle_shortcut_text} locally.",
            )
            return

        starter = getattr(self._global_shortcut, "start", None)
        if callable(starter):
            starter()

    def _update_shortcut_status(self, registered: bool, message: str) -> None:
        self._use_local_shortcut = not registered
        self.hint_label.setText(message)

    def _trigger_shortcut_press(self) -> None:
        if not self._shortcut_pressed:
            self._shortcut_pressed = True
            self.controller.start()

    def _trigger_shortcut_release(self) -> None:
        if self._shortcut_pressed:
            self._shortcut_pressed = False
            self.controller.stop()

    def _queue_snapshot(self, snapshot: ControllerSnapshot) -> None:
        self._snapshot_bus.snapshot_ready.emit(snapshot)

    def _apply_snapshot(self, snapshot: ControllerSnapshot) -> None:
        state_map = {
            ControllerState.IDLE: ("Idle", QColor("#2fbf9f")),
            ControllerState.RECORDING: ("Recording", QColor("#d95d39")),
            ControllerState.TRANSCRIBING: ("Transcribing", QColor("#d1a208")),
            ControllerState.DELIVERING: ("Delivering", QColor("#4b9cd3")),
            ControllerState.ERROR: ("Error", QColor("#b43e5a")),
        }
        label, color = state_map[snapshot.state]
        self.status_chip.setText(label)
        self.status_chip.setStyleSheet(
            f"background: {color.name()}; border-radius: 10px; padding: 4px 10px; color: white; font-weight: 600;"
        )

        if snapshot.error_message:
            self.last_text_label.setText(snapshot.error_message)
            self.last_text_label.show()
        elif snapshot.last_text:
            self.last_text_label.setText(snapshot.last_text)
            self.last_text_label.hide()
        else:
            self.last_text_label.setText("No transcript yet.")
            self.last_text_label.hide()

        self.start_button.setEnabled(snapshot.state in (ControllerState.IDLE, ControllerState.ERROR))
        self.stop_button.setEnabled(snapshot.state == ControllerState.RECORDING)
        self.cancel_button.setEnabled(snapshot.state == ControllerState.RECORDING)

    def _move_to_default_position(self) -> None:
        screen = self.app.primaryScreen()
        if screen is None:
            return
        geometry = screen.availableGeometry()
        self.adjustSize()
        x = geometry.x() + (geometry.width() - self.width()) // 2
        y = geometry.y() + 36
        self.move(x, y)

    def eventFilter(self, watched, event) -> bool:  # type: ignore[override]
        if self._use_local_shortcut and isinstance(event, QKeyEvent):
            sequence_text = QKeySequence(event.keyCombination()).toString(
                QKeySequence.SequenceFormat.PortableText
            )
            expected_text = self.shortcut_sequence.toString(QKeySequence.SequenceFormat.PortableText)
            if sequence_text == expected_text:
                if event.type() == QEvent.Type.KeyPress and not event.isAutoRepeat():
                    self._trigger_shortcut_press()
                    return True
                if event.type() == QEvent.Type.KeyRelease and not event.isAutoRepeat():
                    self._trigger_shortcut_release()
                    return True
        return super().eventFilter(watched, event)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.app.removeEventFilter(self)
        closer = getattr(self._global_shortcut, "close", None)
        if callable(closer):
            closer()
        super().closeEvent(event)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if self._drag_origin and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_origin)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        self._drag_origin = None
        event.accept()

from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import QEvent, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QKeyEvent, QKeySequence
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..controller import GabbeeController
from ..models import ControllerSnapshot, ControllerState
from .global_shortcuts import PortalPushToTalkBinding, PortalShortcutSpec


class _SnapshotBus(QWidget):
    snapshot_ready = pyqtSignal(object)


class FloatingBar(QWidget):
    settings_requested = pyqtSignal()

    def __init__(
        self,
        app: QApplication,
        controller: GabbeeController,
        title: str = "Gabbee",
        toggle_shortcut: str = "F5",
        command_shortcut: str = "F6",
        global_shortcut_factory: Callable[..., object] | None = PortalPushToTalkBinding,
    ) -> None:
        super().__init__()
        self.app = app
        self.controller = controller
        self.title = title
        self.toggle_shortcut_text = toggle_shortcut
        self.command_shortcut_text = command_shortcut.strip()
        self.shortcut_sequence = QKeySequence(self.toggle_shortcut_text)
        self.command_shortcut_sequence = QKeySequence(self.command_shortcut_text)
        self._shortcut_pressed = False
        self._command_shortcut_pressed = False
        self._use_local_shortcut = True
        self._use_local_command_shortcut = bool(self.command_shortcut_text)
        self._global_shortcut_factory = global_shortcut_factory
        self._global_shortcut = None
        self._global_command_shortcut = None
        self._pinned = True
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

        self.drag_handle = QFrame()
        self.drag_handle.setObjectName("dragHandle")
        self.drag_handle.setFixedSize(12, 24)
        self.drag_handle.setCursor(Qt.CursorShape.SizeAllCursor)
        header.addWidget(self.drag_handle)
        
        # Make the drag handle forward press to trigger compositor-assisted move
        self.drag_handle.mousePressEvent = self.mousePressEvent

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

        self.hide_button = QPushButton("Hide")
        self.hide_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.hide_button.clicked.connect(self.hide_bar)
        header.addWidget(self.hide_button)

        self.start_button = QPushButton("Start")
        self.stop_button = QPushButton("Stop")
        self.cancel_button = QPushButton("Cancel")
        self.start_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.stop_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.cancel_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self.start_button.clicked.connect(self.controller.start)
        self.stop_button.clicked.connect(self.controller.stop)
        self.cancel_button.clicked.connect(self.controller.cancel)

        self.settings_button = QPushButton("⚙")
        self.settings_button.setObjectName("settingsButton")
        self.settings_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.settings_button.setFixedSize(24, 24)
        self.settings_button.clicked.connect(self.settings_requested.emit)
        header.addWidget(self.settings_button)

        self.provider_label = QLabel("Provider: unknown")
        self.provider_label.setObjectName("subtle")
        header.addWidget(self.provider_label)

        self.profile_button = QPushButton("Profile: Auto")
        self.profile_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.profile_button.clicked.connect(self._choose_profile)
        header.addWidget(self.profile_button)

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

        self.hint_label = QLabel("")
        self.hint_label.setObjectName("subtle")
        self.hint_label.hide()
        outer.addWidget(self.hint_label)

        self.context_label = QLabel("")
        self.context_label.setObjectName("subtle")
        self.context_label.hide()
        outer.addWidget(self.context_label)

        last_actions = QHBoxLayout()
        last_actions.setSpacing(4)
        outer.addLayout(last_actions)
        self.undo_button = QPushButton("Undo")
        self.edit_button = QPushButton("Edit & Replace")
        self.retry_button = QPushButton("Retry")
        self.copy_raw_button = QPushButton("Copy Raw")
        self.copy_formatted_button = QPushButton("Copy Formatted")
        for button in (
            self.undo_button,
            self.edit_button,
            self.retry_button,
            self.copy_raw_button,
            self.copy_formatted_button,
        ):
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.hide()
            last_actions.addWidget(button)
        self.undo_button.clicked.connect(lambda: self._invoke_controller("undo_last"))
        self.edit_button.clicked.connect(self._edit_and_replace)
        self.retry_button.clicked.connect(lambda: self._invoke_controller("retry_last"))
        self.copy_raw_button.clicked.connect(lambda: self._invoke_controller("copy_raw"))
        self.copy_formatted_button.clicked.connect(lambda: self._invoke_controller("copy_formatted"))

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
            QFrame#dragHandle {
                background: rgba(255, 255, 255, 30);
                border-radius: 3px;
                border: 1px solid rgba(255, 255, 255, 50);
            }
            QFrame#dragHandle:hover {
                background: rgba(255, 255, 255, 60);
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
            QPushButton#settingsButton {
                background: rgba(255, 255, 255, 15);
                font-size: 14px;
                padding: 0;
                min-width: 24px;
            }
            QPushButton#settingsButton:hover {
                background: rgba(255, 255, 255, 30);
            }
            """
        )

        self.controller.add_listener(self._queue_snapshot)
        self.app.installEventFilter(self)
        self._render_shortcut_hint()
        # Reuse the existing portal bindings on startup. ConfigureShortcuts
        # opens System Settings, so reserve it for an explicit shortcut edit;
        # otherwise every bar restart steals focus from the dictation target.
        self._setup_shortcut_binding(configure_global=False)
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
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)

    def show_bar(self) -> None:
        self.show()
        self.raise_()
        handle = self.windowHandle()
        if handle is not None:
            handle.raise_()

    def hide_bar(self) -> None:
        self.hide()

    def _set_pinned(self, pinned: bool) -> None:
        self._pinned = pinned
        self.pin_button.setText("Pinned" if pinned else "Pin")
        self._apply_window_flags()
        if self.isVisible():
            self.show_bar()
        if pinned:
            self._pin_refresh_timer.start()
        else:
            self._pin_refresh_timer.stop()
        self._refresh_pin_state()

    def _refresh_pin_state(self) -> None:
        if not self._pinned or not self.isVisible():
            return
        self.show_bar()

    def _setup_shortcut_binding(self, configure_global: bool = False) -> None:
        if self._global_shortcut_factory is None:
            self._render_shortcut_hint()
            return
        shortcut_specs = [
            PortalShortcutSpec(
                shortcut_id="dictation_push_to_talk",
                shortcut_text=self.toggle_shortcut_text,
                description="Gabbee dictation push to talk",
                on_pressed=self._trigger_shortcut_press,
                on_released=self._trigger_shortcut_release,
                on_status_change=self._update_shortcut_status,
            )
        ]
        if self.command_shortcut_text:
            shortcut_specs.append(
                PortalShortcutSpec(
                    shortcut_id="command_push_to_talk",
                    shortcut_text=self.command_shortcut_text,
                    description="Gabbee command push to talk",
                    on_pressed=self._trigger_command_shortcut_press,
                    on_released=self._trigger_command_shortcut_release,
                    on_status_change=self._update_command_shortcut_status,
                )
            )
        try:
            self._global_shortcut = self._global_shortcut_factory(
                shortcut_specs=shortcut_specs,
                configure_on_start=configure_global,
            )
        except Exception:
            self._global_shortcut = None
            self._update_shortcut_status(
                False,
                f"Global shortcut setup failed; focus Gabbee to use {self.toggle_shortcut_text} locally.",
            )
            if self.command_shortcut_text:
                self._update_command_shortcut_status(
                    False,
                    f"Global shortcut setup failed; focus Gabbee to use {self.command_shortcut_text} locally.",
                )
            return

        starter = getattr(self._global_shortcut, "start", None)
        if callable(starter):
            starter()

    def _close_shortcut_bindings(self) -> None:
        closer = getattr(self._global_shortcut, "close", None)
        if callable(closer):
            closer()
        self._global_shortcut = None
        self._global_command_shortcut = None

    def apply_shortcuts(self, toggle_shortcut: str, command_shortcut: str) -> None:
        self._close_shortcut_bindings()
        self.toggle_shortcut_text = toggle_shortcut.strip() or "F5"
        self.command_shortcut_text = command_shortcut.strip()
        self.shortcut_sequence = QKeySequence(self.toggle_shortcut_text)
        self.command_shortcut_sequence = QKeySequence(self.command_shortcut_text)
        self._shortcut_pressed = False
        self._command_shortcut_pressed = False
        self._use_local_shortcut = True
        self._use_local_command_shortcut = bool(self.command_shortcut_text)
        self._render_shortcut_hint()
        self._setup_shortcut_binding(configure_global=True)

    def _update_shortcut_status(self, registered: bool, message: str) -> None:
        self._use_local_shortcut = not registered
        self._render_shortcut_hint()

    def _update_command_shortcut_status(self, registered: bool, message: str) -> None:
        self._use_local_command_shortcut = not registered
        self._render_shortcut_hint()

    def _render_shortcut_hint(self) -> None:
        dictation_scope = "while Gabbee is focused" if self._use_local_shortcut else "anywhere"
        parts = [f"Hold {self.toggle_shortcut_text} {dictation_scope} to dictate"]
        if self.command_shortcut_text:
            command_scope = "while Gabbee is focused" if self._use_local_command_shortcut else "anywhere"
            parts.append(f"hold {self.command_shortcut_text} {command_scope} for commands")
        self.hint_label.setText("; ".join(parts) + ", or use the buttons.")

    def _trigger_shortcut_press(self) -> None:
        if not self._shortcut_pressed:
            self._shortcut_pressed = True
            self.controller.start()

    def _trigger_shortcut_release(self) -> None:
        if self._shortcut_pressed:
            self._shortcut_pressed = False
            self.controller.stop()

    def _trigger_command_shortcut_press(self) -> None:
        if not self._command_shortcut_pressed:
            self._command_shortcut_pressed = True
            self.controller.start_command()

    def _trigger_command_shortcut_release(self) -> None:
        if self._command_shortcut_pressed:
            self._command_shortcut_pressed = False
            self.controller.stop()

    def _queue_snapshot(self, snapshot: ControllerSnapshot) -> None:
        self._snapshot_bus.snapshot_ready.emit(snapshot)

    def _choose_profile(self) -> None:
        provider = getattr(self.controller, "available_profiles", None)
        choices = ["Automatic", *(provider() if callable(provider) else [])]
        selected, accepted = QInputDialog.getItem(
            self,
            "Gabbee profile",
            "Use profile until cleared:",
            choices,
            0,
            False,
        )
        if accepted:
            setter = getattr(self.controller, "set_profile_override", None)
            if callable(setter):
                setter(None if selected == "Automatic" else selected)

    def _apply_snapshot(self, snapshot: ControllerSnapshot) -> None:
        state_map = {
            ControllerState.IDLE: ("Idle", QColor("#2fbf9f")),
            ControllerState.CONNECTING: ("Connecting", QColor("#d1a208")),
            ControllerState.RECORDING: ("Recording", QColor("#d95d39")),
            ControllerState.TRANSCRIBING: ("Transcribing", QColor("#d1a208")),
            ControllerState.DELIVERING: ("Delivering", QColor("#4b9cd3")),
            ControllerState.RUNNING_MACRO: ("Command", QColor("#8b5cf6")),
            ControllerState.ERROR: ("Error", QColor("#b43e5a")),
        }
        label, color = state_map[snapshot.state]
        if snapshot.state == ControllerState.RECORDING and snapshot.command_mode:
            label, color = "Command", QColor("#8b5cf6")
        self.status_chip.setText(label)
        self.status_chip.setStyleSheet(
            f"background: {color.name()}; border-radius: 10px; padding: 4px 10px; color: white; font-weight: 600;"
        )

        if snapshot.error_message:
            self.last_text_label.setText(snapshot.error_message)
            self.last_text_label.show()
        elif snapshot.partial_text:
            self.last_text_label.setText(snapshot.partial_text)
            self.last_text_label.show()
        elif snapshot.last_text:
            self.last_text_label.setText(snapshot.last_text)
            self.last_text_label.show()
        else:
            self.last_text_label.setText("No transcript yet.")
            self.last_text_label.hide()

        provider_detail = snapshot.provider
        if snapshot.provider_status:
            provider_detail += f" · {snapshot.provider_status}"
        self.provider_label.setText(provider_detail)
        manager = getattr(self.controller, "profile_manager", None)
        self.profile_button.setText(f"Profile: {getattr(manager, 'manual_override', None) or 'Auto'}")
        context_parts = []
        if snapshot.queue_depth:
            context_parts.append(f"Queue {snapshot.queue_depth}")
        if snapshot.target_app:
            context_parts.append(f"Target: {snapshot.target_app}")
        if snapshot.active_profile:
            context_parts.append(f"Profile: {snapshot.active_profile}")
        if snapshot.delivery_method:
            context_parts.append(f"Output: {snapshot.delivery_method}")
        if snapshot.macro_progress:
            context_parts.append(snapshot.macro_progress)
        self.context_label.setText(" · ".join(context_parts))
        self.context_label.setVisible(bool(context_parts))

        has_last = bool(snapshot.last_text)
        self.undo_button.setVisible(has_last)
        self.edit_button.setVisible(has_last)
        self.copy_raw_button.setVisible(has_last or snapshot.retained_audio)
        self.copy_formatted_button.setVisible(has_last)
        self.retry_button.setVisible(snapshot.retained_audio or snapshot.state == ControllerState.ERROR)

        self.start_button.setEnabled(snapshot.state in (ControllerState.IDLE, ControllerState.ERROR))
        self.stop_button.setEnabled(snapshot.state == ControllerState.RECORDING)
        self.cancel_button.setEnabled(snapshot.state in (ControllerState.RECORDING, ControllerState.RUNNING_MACRO))
        self.cancel_button.setText("Cancel Macro" if snapshot.state == ControllerState.RUNNING_MACRO else "Cancel")

    def _invoke_controller(self, name: str) -> None:
        callback = getattr(self.controller, name, None)
        if callable(callback):
            callback()

    def _edit_and_replace(self) -> None:
        current = getattr(getattr(self.controller, "last_dictation", None), "formatted_text", "")
        value, accepted = QInputDialog.getMultiLineText(self, "Edit Last Dictation", "Replacement text:", current)
        if accepted:
            replacer = getattr(self.controller, "edit_and_replace", None)
            result = replacer(value) if callable(replacer) else None
            suggestion_factory = getattr(self.controller, "correction_suggestion", None)
            suggestion = suggestion_factory(value) if callable(suggestion_factory) else None
            if getattr(result, "ok", False) and suggestion is not None:
                answer = QMessageBox.question(
                    self,
                    "Save correction?",
                    "Save this spelling or formatting rule to the active profile?",
                )
                if answer == QMessageBox.StandardButton.Yes:
                    saver = getattr(self.controller, "save_correction_rule", None)
                    if callable(saver):
                        saver(suggestion.spoken, suggestion.written)

    def _invoke_controller_with_value(self, name: str, value: str) -> None:
        callback = getattr(self.controller, name, None)
        if callable(callback):
            callback(value)

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
        if self._use_local_command_shortcut and self.command_shortcut_text and isinstance(event, QKeyEvent):
            sequence_text = QKeySequence(event.keyCombination()).toString(
                QKeySequence.SequenceFormat.PortableText
            )
            expected_text = self.command_shortcut_sequence.toString(QKeySequence.SequenceFormat.PortableText)
            if sequence_text == expected_text:
                if event.type() == QEvent.Type.KeyPress and not event.isAutoRepeat():
                    self._trigger_command_shortcut_press()
                    return True
                if event.type() == QEvent.Type.KeyRelease and not event.isAutoRepeat():
                    self._trigger_command_shortcut_release()
                    return True
        return super().eventFilter(watched, event)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.app.removeEventFilter(self)
        self._close_shortcut_bindings()
        super().closeEvent(event)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.windowHandle()
            if handle is not None:
                handle.startSystemMove()
            event.accept()

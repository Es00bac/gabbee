from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..advanced_config import (
    CommandAction,
    CommandEntry,
    ConfigValidationError,
    MacroEntry,
    MacroStep,
    ProfileEntry,
    SlotDefinition,
    VocabularyEntry,
    import_advanced_config,
    load_advanced_config,
    save_advanced_config,
)
from ..command_patterns import PatternCompileError, compile_command_pattern
from ..desktop_actions import BuiltinDesktopCommands
from ..macro_runtime import AmbiguousCommandError, CommandDispatcher
from ..text_processor import TextProcessor


ACTION_LABELS = {
    "Type text": "type_text",
    "Type CLI command": "type_cli",
    "Press key combo": "press_key",
    "Run saved macro": "macro",
    "Activate app": "activate_app",
    "Activate window": "activate_window",
    "Open desktop app": "launch_desktop_entry",
    "Invoke accessible control": "invoke_ui",
    "Move pointer to control": "move_pointer",
    "Click control": "click",
    "Right-click control": "right_click",
    "Double-click control": "double_click",
    "Scroll": "scroll",
}
STEP_LABELS = {
    **ACTION_LABELS,
    "Wait": "wait",
    "Wait for target": "wait_for_target",
    "Repeat previous step": "repeat",
    "If target exists, run previous step": "target_exists",
}


class CommandStudioWindow(QDialog):
    def __init__(
        self,
        config_path: Path,
        parent: QWidget | None = None,
        *,
        capture_app=None,
        capture_control=None,
    ) -> None:
        super().__init__(parent)
        self.config_path = config_path
        self.advanced_config = load_advanced_config(config_path)
        self.capture_app = capture_app
        self.capture_control = capture_control
        self.macro_steps_draft: list[MacroStep] = []
        self.setWindowTitle("Gabbee Command Studio")
        self.setMinimumSize(920, 600)

        root = QVBoxLayout(self)
        body = QHBoxLayout()
        root.addLayout(body, 1)
        self.sidebar = QListWidget()
        self.sidebar.addItems(["Vocabulary", "Commands", "Test / Preview", "Macros", "Profiles"])
        self.sidebar.setCurrentRow(0)
        body.addWidget(self.sidebar, 0)
        self.pages = QStackedWidget()
        body.addWidget(self.pages, 1)
        self.pages.addWidget(self._build_vocabulary_page())
        self.pages.addWidget(self._build_commands_page())
        self.pages.addWidget(self._build_preview_page())
        self.pages.addWidget(self._build_macros_page())
        self.pages.addWidget(self._build_profiles_page())
        self.sidebar.currentRowChanged.connect(self.pages.setCurrentIndex)

        buttons = QHBoxLayout()
        root.addLayout(buttons)
        self.import_button = QPushButton("Import…")
        self.export_button = QPushButton("Export…")
        self.import_button.clicked.connect(self.import_dialog)
        self.export_button.clicked.connect(self.export_dialog)
        buttons.addWidget(self.import_button)
        buttons.addWidget(self.export_button)
        buttons.addStretch(1)
        self.save_button = QPushButton("Save")
        self.revert_button = QPushButton("Revert")
        self.close_button = QPushButton("Close")
        self.save_button.clicked.connect(self.save)
        self.revert_button.clicked.connect(self.reload)
        self.close_button.clicked.connect(self.close)
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.revert_button)
        buttons.addWidget(self.close_button)
        self._refresh_tables()

    def _build_vocabulary_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("Preferred spellings apply to dictation and command mode."))
        self.vocab_table = QTableWidget(0, 5)
        self.vocab_table.setHorizontalHeaderLabels(["Enabled", "Spoken", "Written", "Priority", "Keyterm"])
        layout.addWidget(self.vocab_table)
        editor = QHBoxLayout()
        layout.addLayout(editor)
        self.vocab_enabled_input = QCheckBox("Enabled")
        self.vocab_enabled_input.setChecked(True)
        self.vocab_spoken_input = QLineEdit()
        self.vocab_spoken_input.setPlaceholderText("gabby")
        self.vocab_written_input = QLineEdit()
        self.vocab_written_input.setPlaceholderText("Gabbee")
        self.vocab_priority_input = QSpinBox()
        self.vocab_priority_input.setRange(-1000, 1000)
        self.vocab_keyterm_input = QCheckBox("Keyterm")
        self.vocab_keyterm_input.setChecked(True)
        self.add_vocabulary_button = QPushButton("Add")
        self.delete_vocabulary_button = QPushButton("Delete selected")
        self.add_vocabulary_button.clicked.connect(self.add_vocabulary_entry)
        self.delete_vocabulary_button.clicked.connect(self.delete_selected_vocabulary)
        for widget in (
            self.vocab_enabled_input, self.vocab_spoken_input, self.vocab_written_input,
            self.vocab_priority_input, self.vocab_keyterm_input, self.add_vocabulary_button,
            self.delete_vocabulary_button,
        ):
            editor.addWidget(widget)
        return page

    def _build_commands_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("Commands only run from the secondary shortcut. Slots use {name:type}."))
        self.command_table = QTableWidget(0, 5)
        self.command_table.setHorizontalHeaderLabels(["Enabled", "Spoken Pattern", "Action", "Payload", "Profiles"])
        layout.addWidget(self.command_table)
        form = QFormLayout()
        layout.addLayout(form)
        self.command_enabled_input = QCheckBox("Enabled")
        self.command_enabled_input.setChecked(True)
        self.command_spoken_input = QLineEdit()
        self.command_spoken_input.setPlaceholderText("enter {phone:phone}")
        self.command_action_type = QComboBox()
        self.command_action_type.addItems(list(ACTION_LABELS))
        self.command_payload_input = QLineEdit()
        self.command_payload_input.setPlaceholderText("{phone}")
        self.command_slots_input = QLineEdit()
        self.command_slots_input.setPlaceholderText("choice slots only: target:choice=Save|Cancel")
        self.command_profiles_input = QLineEdit()
        self.command_profiles_input.setPlaceholderText("optional profile names, comma separated")
        form.addRow("", self.command_enabled_input)
        form.addRow("Spoken pattern:", self.command_spoken_input)
        form.addRow("Action:", self.command_action_type)
        form.addRow("Payload:", self.command_payload_input)
        form.addRow("Slot definitions:", self.command_slots_input)
        form.addRow("Profiles:", self.command_profiles_input)
        actions = QHBoxLayout()
        layout.addLayout(actions)
        self.add_command_button = QPushButton("Add")
        self.edit_command_button = QPushButton("Update selected")
        self.duplicate_command_button = QPushButton("Duplicate")
        self.toggle_command_button = QPushButton("Enable / Disable")
        self.delete_command_button = QPushButton("Delete selected")
        self.use_current_control_button = QPushButton("Use current control")
        self.add_command_button.clicked.connect(self.add_command_entry)
        self.edit_command_button.clicked.connect(self.edit_selected_command)
        self.duplicate_command_button.clicked.connect(self.duplicate_selected_command)
        self.toggle_command_button.clicked.connect(self.toggle_selected_command)
        self.delete_command_button.clicked.connect(self.delete_selected_command)
        self.use_current_control_button.clicked.connect(self.use_current_control)
        for button in (
            self.add_command_button, self.edit_command_button, self.duplicate_command_button,
            self.toggle_command_button, self.delete_command_button, self.use_current_control_button,
        ):
            actions.addWidget(button)
        return page

    def _build_preview_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("Dry-run preview never types, clicks, opens apps, or presses keys."))
        self.preview_mode = QComboBox()
        self.preview_mode.addItems(["Dictation", "Command"])
        self.preview_profile = QComboBox()
        self.preview_input = QLineEdit()
        self.preview_input.setPlaceholderText("Transcript to preview")
        self.preview_button = QPushButton("Dry Run")
        self.what_can_i_say_button = QPushButton("What can I say?")
        controls = QHBoxLayout()
        for widget in (self.preview_mode, self.preview_profile, self.preview_input, self.preview_button, self.what_can_i_say_button):
            controls.addWidget(widget)
        layout.addLayout(controls)
        self.preview_output = QTextEdit()
        self.preview_output.setReadOnly(True)
        layout.addWidget(self.preview_output)
        self.preview_button.clicked.connect(self.update_preview)
        self.what_can_i_say_button.clicked.connect(self.show_available_commands)
        self.preview_input.textChanged.connect(self.update_preview)
        self.preview_mode.currentTextChanged.connect(self.update_preview)
        return page

    def _build_macros_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.macro_table = QTableWidget(0, 5)
        self.macro_table.setHorizontalHeaderLabels(["Enabled", "Name", "Spoken Pattern", "Steps", "Profiles"])
        layout.addWidget(self.macro_table)
        form = QFormLayout()
        layout.addLayout(form)
        self.macro_enabled_input = QCheckBox("Enabled")
        self.macro_enabled_input.setChecked(True)
        self.macro_name_input = QLineEdit()
        self.macro_spoken_input = QLineEdit()
        self.macro_spoken_input.setPlaceholderText("fill form for {target:choice}")
        self.macro_slots_input = QLineEdit()
        self.macro_profiles_input = QLineEdit()
        form.addRow("", self.macro_enabled_input)
        form.addRow("Name:", self.macro_name_input)
        form.addRow("Spoken pattern:", self.macro_spoken_input)
        form.addRow("Slot definitions:", self.macro_slots_input)
        form.addRow("Profiles:", self.macro_profiles_input)

        layout.addWidget(QLabel("Step builder (drag to reorder; a failed step stops unless continuation is enabled):"))
        self.macro_step_list = QListWidget()
        self.macro_step_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.macro_step_list.model().rowsMoved.connect(self._sync_dragged_steps)
        layout.addWidget(self.macro_step_list)
        templates = QHBoxLayout()
        self.macro_template_combo = QComboBox()
        self.macro_template_combo.addItems(
            ["Type text, then Tab", "Click control, then type", "Activate app, then type"]
        )
        self.apply_macro_template_button = QPushButton("Use template")
        self.save_macro_template_button = QPushButton("Save draft as template")
        self.apply_macro_template_button.clicked.connect(self.apply_macro_template)
        self.save_macro_template_button.clicked.connect(self.save_macro_template)
        templates.addWidget(QLabel("Reusable template:"))
        templates.addWidget(self.macro_template_combo, 1)
        templates.addWidget(self.apply_macro_template_button)
        templates.addWidget(self.save_macro_template_button)
        layout.addLayout(templates)
        step_editor = QHBoxLayout()
        self.macro_step_type = QComboBox()
        self.macro_step_type.addItems(list(STEP_LABELS))
        self.macro_step_payload = QLineEdit()
        self.macro_step_payload.setPlaceholderText("Text, key, seconds, app/window, or accessible target")
        self.macro_step_continue = QCheckBox("Continue on failure")
        self.add_macro_step_button = QPushButton("Add step")
        self.remove_macro_step_button = QPushButton("Remove step")
        self.move_macro_step_up_button = QPushButton("Up")
        self.move_macro_step_down_button = QPushButton("Down")
        self.add_macro_step_button.clicked.connect(self.add_macro_step)
        self.remove_macro_step_button.clicked.connect(self.remove_macro_step)
        self.move_macro_step_up_button.clicked.connect(lambda: self.move_macro_step(-1))
        self.move_macro_step_down_button.clicked.connect(lambda: self.move_macro_step(1))
        for widget in (
            self.macro_step_type, self.macro_step_payload, self.macro_step_continue,
            self.add_macro_step_button, self.remove_macro_step_button,
            self.move_macro_step_up_button, self.move_macro_step_down_button,
        ):
            step_editor.addWidget(widget)
        layout.addLayout(step_editor)
        macro_actions = QHBoxLayout()
        self.add_macro_button = QPushButton("Add macro")
        self.edit_macro_button = QPushButton("Update selected")
        self.duplicate_macro_button = QPushButton("Duplicate")
        self.toggle_macro_button = QPushButton("Enable / Disable")
        self.delete_macro_button = QPushButton("Delete")
        self.add_macro_button.clicked.connect(self.add_macro_entry)
        self.edit_macro_button.clicked.connect(self.edit_selected_macro)
        self.duplicate_macro_button.clicked.connect(self.duplicate_selected_macro)
        self.toggle_macro_button.clicked.connect(self.toggle_selected_macro)
        self.delete_macro_button.clicked.connect(self.delete_selected_macro)
        for button in (
            self.add_macro_button, self.edit_macro_button, self.duplicate_macro_button,
            self.toggle_macro_button, self.delete_macro_button,
        ):
            macro_actions.addWidget(button)
        layout.addLayout(macro_actions)
        return page

    def _build_profiles_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("Desktop-file ID is matched first; title regex refines it. Highest priority wins."))
        self.profile_table = QTableWidget(0, 6)
        self.profile_table.setHorizontalHeaderLabels(["Enabled", "Name", "Desktop ID", "Title Pattern", "Priority", "Modes"])
        layout.addWidget(self.profile_table)
        form = QFormLayout()
        layout.addLayout(form)
        self.profile_enabled_input = QCheckBox("Enabled")
        self.profile_enabled_input.setChecked(True)
        self.profile_name_input = QLineEdit()
        self.profile_desktop_id_input = QLineEdit()
        self.profile_title_pattern_input = QLineEdit()
        self.profile_priority_input = QSpinBox()
        self.profile_priority_input.setRange(-1000, 1000)
        self.profile_terminal_input = QCheckBox("Terminal spacing")
        self.profile_code_input = QCheckBox("Code spacing")
        self.profile_keyterms_input = QCheckBox("Enable metered keyterms for this profile")
        self.profile_entity_hints_input = QLineEdit()
        self.profile_entity_hints_input.setPlaceholderText("phone,zip,date (metered add-on)")
        form.addRow("", self.profile_enabled_input)
        form.addRow("Name:", self.profile_name_input)
        form.addRow("Desktop-file ID:", self.profile_desktop_id_input)
        form.addRow("Window-title regex:", self.profile_title_pattern_input)
        form.addRow("Priority:", self.profile_priority_input)
        form.addRow("", self.profile_terminal_input)
        form.addRow("", self.profile_code_input)
        form.addRow("", self.profile_keyterms_input)
        form.addRow("Entity hints:", self.profile_entity_hints_input)
        actions = QHBoxLayout()
        layout.addLayout(actions)
        self.add_profile_button = QPushButton("Add profile")
        self.edit_profile_button = QPushButton("Update selected")
        self.duplicate_profile_button = QPushButton("Duplicate")
        self.toggle_profile_button = QPushButton("Enable / Disable")
        self.delete_profile_button = QPushButton("Delete")
        self.use_current_app_button = QPushButton("Use current app")
        self.add_profile_button.clicked.connect(self.add_profile_entry)
        self.edit_profile_button.clicked.connect(self.edit_selected_profile)
        self.duplicate_profile_button.clicked.connect(self.duplicate_selected_profile)
        self.toggle_profile_button.clicked.connect(self.toggle_selected_profile)
        self.delete_profile_button.clicked.connect(self.delete_selected_profile)
        self.use_current_app_button.clicked.connect(self.use_current_app)
        for button in (
            self.add_profile_button, self.edit_profile_button, self.duplicate_profile_button,
            self.toggle_profile_button, self.delete_profile_button, self.use_current_app_button,
        ):
            actions.addWidget(button)
        return page

    # Vocabulary ------------------------------------------------------------
    def add_vocabulary_entry(self) -> None:
        spoken, written = self.vocab_spoken_input.text().strip(), self.vocab_written_input.text().strip()
        if not spoken or not written:
            return
        self.advanced_config.vocabulary.append(
            VocabularyEntry(
                spoken, written, self.vocab_enabled_input.isChecked(),
                self.vocab_priority_input.value(), self.vocab_keyterm_input.isChecked(),
            )
        )
        self.vocab_spoken_input.clear()
        self.vocab_written_input.clear()
        self._refresh_tables()
        self.update_preview()

    def delete_selected_vocabulary(self) -> None:
        row = self.vocab_table.currentRow()
        if row >= 0:
            del self.advanced_config.vocabulary[row]
            self._refresh_tables()
            self.update_preview()

    # Commands --------------------------------------------------------------
    def _command_from_inputs(self) -> CommandEntry | None:
        spoken, payload = self.command_spoken_input.text().strip(), self.command_payload_input.text().strip()
        if not spoken or not payload:
            return None
        action_type = self._action_type_from_label(self.command_action_type.currentText())
        action = CommandAction(type=action_type)
        if action_type == "press_key":
            action.key = payload
        elif action_type == "macro":
            action.macro = payload
        elif action_type == "activate_app":
            action.app = payload
        elif action_type == "activate_window":
            action.window = payload
        elif action_type == "launch_desktop_entry":
            action.desktop_file_id = payload
        elif action_type in {"invoke_ui", "move_pointer", "click", "right_click", "double_click"}:
            action.target = payload
        elif action_type == "scroll":
            pieces = payload.split()
            action.direction = pieces[0] if pieces else "down"
            action.amount = self._numeric_payload(pieces[1]) if len(pieces) > 1 else 1
        else:
            action.text = payload
        entry = CommandEntry(
            spoken,
            action,
            self.command_enabled_input.isChecked(),
            self._parse_slots(self.command_slots_input.text()),
            self._csv(self.command_profiles_input.text()),
        )
        compile_command_pattern(entry)
        return entry

    def add_command_entry(self) -> None:
        try:
            entry = self._command_from_inputs()
        except (PatternCompileError, ValueError) as exc:
            self._show_error(str(exc))
            return
        if entry is None:
            return
        self.advanced_config.commands.append(entry)
        self.command_spoken_input.clear()
        self.command_payload_input.clear()
        self._refresh_tables()
        self.update_preview()

    def edit_selected_command(self) -> None:
        row = self.command_table.currentRow()
        if row < 0:
            return
        try:
            entry = self._command_from_inputs()
        except (PatternCompileError, ValueError) as exc:
            self._show_error(str(exc))
            return
        if entry:
            self.advanced_config.commands[row] = entry
            self._refresh_tables()

    def duplicate_selected_command(self) -> None:
        row = self.command_table.currentRow()
        if row >= 0:
            source = self.advanced_config.commands[row]
            duplicate = deepcopy(source)
            duplicate.spoken += " copy"
            self.advanced_config.commands.append(duplicate)
            self._refresh_tables()

    def toggle_selected_command(self) -> None:
        row = self.command_table.currentRow()
        if row >= 0:
            self.advanced_config.commands[row].enabled = not self.advanced_config.commands[row].enabled
            self._refresh_tables()

    def delete_selected_command(self) -> None:
        row = self.command_table.currentRow()
        if row >= 0:
            del self.advanced_config.commands[row]
            self._refresh_tables()
            self.update_preview()

    # Macros ----------------------------------------------------------------
    def add_macro_step(self) -> None:
        kind = STEP_LABELS[self.macro_step_type.currentText()]
        payload = self.macro_step_payload.text().strip()
        step = MacroStep(type=kind, continue_on_error=self.macro_step_continue.isChecked())
        if kind in {"type_text", "type_cli"}:
            step.text = payload
        elif kind == "press_key":
            step.key = payload
        elif kind == "wait":
            try:
                step.seconds = self._numeric_payload(payload, floating=True)
            except ValueError:
                return
        elif kind == "wait_for_target":
            step.target, step.timeout = payload, 5
        elif kind == "activate_app":
            step.app = payload
        elif kind == "activate_window":
            step.window = payload
        elif kind == "launch_desktop_entry":
            step.desktop_file_id = payload
        elif kind in {"invoke_ui", "move_pointer", "click", "right_click", "double_click"}:
            step.target = payload
        elif kind == "scroll":
            pieces = payload.split()
            step.direction = pieces[0] if pieces else "down"
            try:
                step.amount = self._numeric_payload(pieces[1]) if len(pieces) > 1 else 1
            except ValueError:
                return
        elif kind == "repeat":
            if not self.macro_steps_draft:
                return
            try:
                step.count = self._numeric_payload(payload)
            except ValueError:
                return
            step.steps = [self.macro_steps_draft.pop()]
        elif kind == "target_exists":
            if not payload or not self.macro_steps_draft:
                return
            step.target = payload
            step.steps = [self.macro_steps_draft.pop()]
        if not payload:
            return
        self.macro_steps_draft.append(step)
        self._refresh_step_list()

    def remove_macro_step(self) -> None:
        row = self.macro_step_list.currentRow()
        if row >= 0:
            del self.macro_steps_draft[row]
            self._refresh_step_list()

    def move_macro_step(self, delta: int) -> None:
        row = self.macro_step_list.currentRow()
        destination = row + delta
        if row < 0 or destination < 0 or destination >= len(self.macro_steps_draft):
            return
        self.macro_steps_draft[row], self.macro_steps_draft[destination] = (
            self.macro_steps_draft[destination], self.macro_steps_draft[row]
        )
        self._refresh_step_list()
        self.macro_step_list.setCurrentRow(destination)

    def _sync_dragged_steps(self, *_args) -> None:
        reordered = [
            self.macro_step_list.item(index).data(Qt.ItemDataRole.UserRole)
            for index in range(self.macro_step_list.count())
        ]
        if all(isinstance(step, MacroStep) for step in reordered):
            self.macro_steps_draft = reordered
            self._refresh_step_list()

    def apply_macro_template(self) -> None:
        builtins = {
            "Type text, then Tab": [
                MacroStep(type="type_text", text="{text}"),
                MacroStep(type="press_key", key="Tab"),
            ],
            "Click control, then type": [
                MacroStep(type="click", target="{target}"),
                MacroStep(type="type_text", text="{text}"),
            ],
            "Activate app, then type": [
                MacroStep(type="activate_app", app="{app}"),
                MacroStep(type="type_text", text="{text}"),
            ],
        }
        name = self.macro_template_combo.currentText()
        steps = builtins.get(name)
        if steps is None:
            custom = next((item for item in self.advanced_config.templates if item.get("name") == name), None)
            steps = self._steps_from_template(custom.get("steps", [])) if custom else []
        self.macro_steps_draft.extend(deepcopy(steps))
        self._refresh_step_list()

    def save_macro_template(self) -> None:
        name = self.macro_name_input.text().strip() or f"Template {len(self.advanced_config.templates) + 1}"
        if not self.macro_steps_draft:
            return
        self.advanced_config.templates.append(
            {"name": name, "steps": [self._template_data(step) for step in self.macro_steps_draft]}
        )
        self.macro_template_combo.addItem(name)
        self.macro_template_combo.setCurrentText(name)

    def _macro_from_inputs(self) -> MacroEntry | None:
        name, spoken = self.macro_name_input.text().strip(), self.macro_spoken_input.text().strip()
        if not name or not spoken or not self.macro_steps_draft:
            return None
        entry = MacroEntry(
            name, spoken, list(self.macro_steps_draft), self.macro_enabled_input.isChecked(),
            self._parse_slots(self.macro_slots_input.text()), self._csv(self.macro_profiles_input.text()),
        )
        compile_command_pattern(entry)
        return entry

    def add_macro_entry(self) -> None:
        try:
            entry = self._macro_from_inputs()
        except (PatternCompileError, ValueError) as exc:
            self._show_error(str(exc))
            return
        if entry:
            self.advanced_config.macros.append(entry)
            self.macro_steps_draft = []
            self._refresh_step_list()
            self._refresh_tables()

    def edit_selected_macro(self) -> None:
        row = self.macro_table.currentRow()
        if row < 0:
            return
        try:
            entry = self._macro_from_inputs()
        except (PatternCompileError, ValueError) as exc:
            self._show_error(str(exc))
            return
        if entry:
            self.advanced_config.macros[row] = entry
            self._refresh_tables()

    def duplicate_selected_macro(self) -> None:
        row = self.macro_table.currentRow()
        if row >= 0:
            item = self.advanced_config.macros[row]
            duplicate = deepcopy(item)
            duplicate.name += " copy"
            duplicate.spoken += " copy"
            self.advanced_config.macros.append(duplicate)
            self._refresh_tables()

    def toggle_selected_macro(self) -> None:
        row = self.macro_table.currentRow()
        if row >= 0:
            self.advanced_config.macros[row].enabled = not self.advanced_config.macros[row].enabled
            self._refresh_tables()

    def delete_selected_macro(self) -> None:
        row = self.macro_table.currentRow()
        if row >= 0:
            del self.advanced_config.macros[row]
            self._refresh_tables()

    # Profiles --------------------------------------------------------------
    def _profile_from_inputs(self) -> ProfileEntry | None:
        name = self.profile_name_input.text().strip()
        if not name:
            return None
        return ProfileEntry(
            name=name,
            enabled=self.profile_enabled_input.isChecked(),
            desktop_file_id=self.profile_desktop_id_input.text().strip(),
            window_title_pattern=self.profile_title_pattern_input.text().strip(),
            priority=self.profile_priority_input.value(),
            terminal_mode=self.profile_terminal_input.isChecked(),
            code_mode=self.profile_code_input.isChecked(),
            keyterms_enabled=self.profile_keyterms_input.isChecked(),
            entity_hints=self._csv(self.profile_entity_hints_input.text()),
        )

    def add_profile_entry(self) -> None:
        entry = self._profile_from_inputs()
        if entry:
            self.advanced_config.profiles.append(entry)
            self._refresh_tables()

    def edit_selected_profile(self) -> None:
        row = self.profile_table.currentRow()
        entry = self._profile_from_inputs()
        if row >= 0 and entry:
            self.advanced_config.profiles[row] = entry
            self._refresh_tables()

    def duplicate_selected_profile(self) -> None:
        row = self.profile_table.currentRow()
        if row >= 0:
            item = self.advanced_config.profiles[row]
            self.advanced_config.profiles.append(
                ProfileEntry(
                    name=item.name + " copy", enabled=item.enabled,
                    vocabulary_spoken=list(item.vocabulary_spoken), command_spoken=list(item.command_spoken),
                    macro_names=list(item.macro_names), desktop_file_id=item.desktop_file_id,
                    window_title_pattern=item.window_title_pattern, priority=item.priority,
                    terminal_mode=item.terminal_mode, code_mode=item.code_mode,
                    keyterms_enabled=item.keyterms_enabled, entity_hints=list(item.entity_hints),
                )
            )
            self._refresh_tables()

    def toggle_selected_profile(self) -> None:
        row = self.profile_table.currentRow()
        if row >= 0:
            self.advanced_config.profiles[row].enabled = not self.advanced_config.profiles[row].enabled
            self._refresh_tables()

    def delete_selected_profile(self) -> None:
        row = self.profile_table.currentRow()
        if row >= 0:
            del self.advanced_config.profiles[row]
            self._refresh_tables()

    def use_current_app(self) -> None:
        if callable(self.capture_app):
            app = self.capture_app()
            if app is not None:
                self.profile_desktop_id_input.setText(app.desktop_file_id)
                self.profile_title_pattern_input.setText("^" + __import__("re").escape(app.title) + "$")

    def use_current_control(self) -> None:
        if callable(self.capture_control):
            target = self.capture_control()
            if target is not None:
                self.command_payload_input.setText(target.name)

    # Preview / persistence -------------------------------------------------
    def update_preview(self) -> None:
        transcript = self.preview_input.text()
        command_mode = self.preview_mode.currentText() == "Command"
        if command_mode:
            profile = self.preview_profile.currentText()
            try:
                dispatcher = CommandDispatcher(self.advanced_config, object())  # type: ignore[arg-type]
                resolved = dispatcher.resolve(transcript, profile)
                simple = resolved is not None and hasattr(resolved.entry, "action") and resolved.entry.action.type in {
                    "type_text",
                    "type_cli",
                    "press_key",
                }
                actions = None if simple else dispatcher.dispatch(transcript, profile, dry_run=True)
            except (AmbiguousCommandError, ValueError) as exc:
                self.preview_output.setPlainText(f"error: {exc}")
                return
            if isinstance(actions, list):
                self.preview_output.setPlainText(
                    "\n".join(f"desktop action: {action!r}" for action in actions)
                )
                return
            builtin = BuiltinDesktopCommands().parse(transcript)
            if builtin is not None:
                self.preview_output.setPlainText(f"desktop action: {builtin!r}")
                return
        processor = TextProcessor(advanced_config=self.advanced_config)
        actions = processor.process_to_actions(
            transcript,
            enable_commands=command_mode,
        )
        self.preview_output.setPlainText("\n".join(f"{kind}: {value}" for kind, value in actions))

    def show_available_commands(self) -> None:
        profile = self.preview_profile.currentText()
        values = [
            item.spoken
            for item in (*self.advanced_config.commands, *self.advanced_config.macros)
            if item.enabled and (not item.profiles or profile in item.profiles)
        ]
        self.preview_output.setPlainText("\n".join(sorted(values, key=str.casefold)) or "No saved commands.")

    def save(self) -> None:
        try:
            save_advanced_config(self.config_path, self.advanced_config)
        except ConfigValidationError as exc:
            self._show_error(str(exc))

    def reload(self) -> None:
        self.advanced_config = load_advanced_config(self.config_path)
        self.macro_steps_draft = []
        self._refresh_step_list()
        self._refresh_tables()
        self.update_preview()

    def export_to(self, path: Path) -> None:
        save_advanced_config(path, self.advanced_config)

    def import_from(self, path: Path) -> None:
        self.advanced_config = import_advanced_config(path.read_text(encoding="utf-8"))
        self._refresh_tables()

    def export_dialog(self) -> None:
        filename, _ = QFileDialog.getSaveFileName(self, "Export Command Studio", "gabbee-commands.json", "JSON (*.json)")
        if filename:
            self.export_to(Path(filename))

    def import_dialog(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "Import Command Studio", "", "JSON (*.json)")
        if filename:
            try:
                self.import_from(Path(filename))
            except (OSError, ConfigValidationError) as exc:
                self._show_error(str(exc))

    def _refresh_tables(self) -> None:
        self.vocab_table.setRowCount(len(self.advanced_config.vocabulary))
        for row, entry in enumerate(self.advanced_config.vocabulary):
            for column, value in enumerate(("yes" if entry.enabled else "no", entry.spoken, entry.written, str(entry.priority), "yes" if entry.keyterm else "no")):
                self.vocab_table.setItem(row, column, QTableWidgetItem(value))

        self.command_table.setRowCount(len(self.advanced_config.commands))
        for row, entry in enumerate(self.advanced_config.commands):
            payload = self._command_payload(entry.action)
            for column, value in enumerate(("yes" if entry.enabled else "no", entry.spoken, self._action_label(entry.action.type), payload, ", ".join(entry.profiles))):
                self.command_table.setItem(row, column, QTableWidgetItem(value))

        self.macro_table.setRowCount(len(self.advanced_config.macros))
        for row, entry in enumerate(self.advanced_config.macros):
            for column, value in enumerate(("yes" if entry.enabled else "no", entry.name, entry.spoken, str(len(entry.steps)), ", ".join(entry.profiles))):
                self.macro_table.setItem(row, column, QTableWidgetItem(value))

        self.profile_table.setRowCount(len(self.advanced_config.profiles))
        for row, entry in enumerate(self.advanced_config.profiles):
            modes = ", ".join(item for item, enabled in (("terminal", entry.terminal_mode), ("code", entry.code_mode), ("keyterms", entry.keyterms_enabled)) if enabled)
            for column, value in enumerate(("yes" if entry.enabled else "no", entry.name, entry.desktop_file_id, entry.window_title_pattern, str(entry.priority), modes)):
                self.profile_table.setItem(row, column, QTableWidgetItem(value))

        self.preview_profile.clear()
        self.preview_profile.addItem("")
        self.preview_profile.addItems([item.name for item in self.advanced_config.profiles if item.enabled])
        builtins = {"Type text, then Tab", "Click control, then type", "Activate app, then type"}
        existing = {
            self.macro_template_combo.itemText(index)
            for index in range(self.macro_template_combo.count())
        }
        for template in self.advanced_config.templates:
            name = template.get("name")
            if isinstance(name, str) and name and name not in existing and name not in builtins:
                self.macro_template_combo.addItem(name)
                existing.add(name)

    def _refresh_step_list(self) -> None:
        self.macro_step_list.clear()
        for index, step in enumerate(self.macro_steps_draft, start=1):
            payload = step.text or step.key or step.target or step.app or step.window or step.desktop_file_id or step.direction or str(step.seconds or "")
            suffix = " (continue)" if step.continue_on_error else ""
            item = QListWidgetItem(f"{index}. {step.type}: {payload}{suffix}")
            item.setData(Qt.ItemDataRole.UserRole, step)
            self.macro_step_list.addItem(item)

    @staticmethod
    def _numeric_payload(value: str, *, floating: bool = False) -> int | float | str:
        value = value.strip()
        if value.startswith("{") and value.endswith("}"):
            return value
        return float(value) if floating else int(value)

    @classmethod
    def _steps_from_template(cls, raw_steps: object) -> list[MacroStep]:
        if not isinstance(raw_steps, list):
            return []
        result: list[MacroStep] = []
        fields = set(MacroStep.__dataclass_fields__)
        for raw in raw_steps:
            if not isinstance(raw, dict) or not isinstance(raw.get("type"), str):
                continue
            values = {
                key: value
                for key, value in raw.items()
                if key in fields and key not in {"steps", "else_steps"}
            }
            values["steps"] = cls._steps_from_template(raw.get("steps", []))
            values["else_steps"] = cls._steps_from_template(raw.get("else_steps", []))
            try:
                result.append(MacroStep(**values))
            except TypeError:
                continue
        return result

    @classmethod
    def _template_data(cls, step: MacroStep) -> dict:
        return {
            "type": step.type,
            "text": step.text,
            "key": step.key,
            "seconds": step.seconds,
            "target": step.target,
            "app": step.app,
            "window": step.window,
            "desktop_file_id": step.desktop_file_id,
            "action": step.action,
            "direction": step.direction,
            "amount": step.amount,
            "count": step.count,
            "timeout": step.timeout,
            "continue_on_error": step.continue_on_error,
            "steps": [cls._template_data(child) for child in step.steps],
            "else_steps": [cls._template_data(child) for child in step.else_steps],
        }

    @staticmethod
    def _parse_slots(value: str) -> list[SlotDefinition]:
        slots: list[SlotDefinition] = []
        for raw in (item.strip() for item in value.split(",") if item.strip()):
            name_type, separator, choices_raw = raw.partition("=")
            name, colon, slot_type = name_type.partition(":")
            if not colon:
                raise ValueError(f"Invalid slot definition: {raw}")
            choices = [item.strip() for item in choices_raw.split("|") if item.strip()] if separator else []
            slots.append(SlotDefinition(name.strip(), slot_type.strip(), choices))
        return slots

    @staticmethod
    def _csv(value: str) -> list[str]:
        return [item.strip() for item in value.split(",") if item.strip()]

    def _action_type_from_label(self, label: str) -> str:
        return ACTION_LABELS[label]

    @staticmethod
    def _action_label(action_type: str) -> str:
        return next((label for label, value in ACTION_LABELS.items() if value == action_type), action_type)

    @staticmethod
    def _command_payload(action: CommandAction) -> str:
        return (
            action.key or action.text or action.macro or action.target or action.app or action.window
            or action.desktop_file_id or " ".join(item for item in (action.direction, str(action.amount or "")) if item)
        )

    def _show_error(self, message: str) -> None:
        QMessageBox.warning(self, "Command Studio", message)

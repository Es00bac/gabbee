from __future__ import annotations

import json
from pathlib import Path

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..config import AppConfig
from ..diagnostics import DiagnosticsLog, build_redacted_report, export_redacted_report
from ..secret_store import save_api_key


ENTITY_HINTS = ("phone", "zip", "date", "time", "money", "measurement", "id", "url", "filename", "ip")


class ConfigWindow(QDialog):
    command_studio_requested = pyqtSignal()

    def __init__(self, config: AppConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self.diagnostics_log = DiagnosticsLog(config.paths.diagnostics_log)
        self.setWindowTitle("Gabbee Configuration")
        self.setMinimumSize(520, 460)
        root = QVBoxLayout(self)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs)

        settings_page = QWidget()
        settings_layout = QVBoxLayout(settings_page)
        general = QFormLayout()
        settings_layout.addLayout(general)
        self.stt_provider = QComboBox()
        self.stt_provider.addItems(["elevenlabs", "gemini", "whisper_local", "mock"])
        self.stt_provider.setCurrentText(config.stt_provider)
        general.addRow("STT Provider:", self.stt_provider)
        self.toggle_shortcut = QLineEdit(config.toggle_shortcut)
        general.addRow("Dictation Shortcut:", self.toggle_shortcut)
        self.command_shortcut = QLineEdit(config.command_shortcut)
        general.addRow("Command Shortcut:", self.command_shortcut)
        self.sample_rate = QLineEdit(str(config.sample_rate))
        general.addRow("Audio Sample Rate:", self.sample_rate)

        self.provider_stack = QStackedWidget()
        settings_layout.addWidget(self.provider_stack, 1)
        self._provider_indexes: dict[str, int] = {}
        self._build_elevenlabs_page()
        self._build_gemini_page()
        self._build_whisper_page()
        self._build_mock_page()
        self.stt_provider.currentTextChanged.connect(self._show_provider)
        self._show_provider(self.stt_provider.currentText())

        self.command_studio_button = QPushButton("Command Studio...")
        self.command_studio_button.clicked.connect(self.command_studio_requested.emit)
        settings_layout.addWidget(self.command_studio_button)
        self.tabs.addTab(settings_page, "Settings")

        diagnostics_page = QWidget()
        diagnostics_layout = QVBoxLayout(diagnostics_page)
        diagnostics_layout.addWidget(
            QLabel("This report excludes audio, transcripts, and credential values by default.")
        )
        self.diagnostics_output = QPlainTextEdit()
        self.diagnostics_output.setReadOnly(True)
        diagnostics_layout.addWidget(self.diagnostics_output, 1)
        diagnostic_buttons = QHBoxLayout()
        self.refresh_diagnostics_button = QPushButton("Refresh")
        self.export_diagnostics_button = QPushButton("Export Redacted Report…")
        self.refresh_diagnostics_button.clicked.connect(self.refresh_diagnostics)
        self.export_diagnostics_button.clicked.connect(self.export_diagnostics)
        diagnostic_buttons.addWidget(self.refresh_diagnostics_button)
        diagnostic_buttons.addWidget(self.export_diagnostics_button)
        diagnostics_layout.addLayout(diagnostic_buttons)
        self.tabs.addTab(diagnostics_page, "Diagnostics")
        self.refresh_diagnostics()

        buttons = QHBoxLayout()
        root.addLayout(buttons)
        buttons.addStretch(1)
        self.save_button = QPushButton("Save")
        self.save_button.clicked.connect(self.accept)
        buttons.addWidget(self.save_button)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_button)

    def _build_elevenlabs_page(self) -> None:
        page = QWidget()
        layout = QFormLayout(page)
        self.elevenlabs_api_key = QLineEdit(
            self.config.secret_api_key or self.config.env_values.get("ELEVENLABS_API_KEY", "") or ""
        )
        self.elevenlabs_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.elevenlabs_api_key.setPlaceholderText("Stored in Secret Service / desktop keyring")
        layout.addRow("API Key:", self.elevenlabs_api_key)
        self.elevenlabs_realtime = QCheckBox("Use Scribe v2 Realtime with automatic batch recovery")
        self.elevenlabs_realtime.setChecked(self.config.elevenlabs_realtime_enabled)
        layout.addRow("Realtime:", self.elevenlabs_realtime)
        self.elevenlabs_keyterms = QCheckBox("Enable profile keyterms — metered add-on (+20% transcription premium)")
        self.elevenlabs_keyterms.setChecked(self.config.elevenlabs_keyterms_enabled)
        layout.addRow("Keyterms:", self.elevenlabs_keyterms)
        layout.addRow(
            QLabel("<b>Entity detection — metered add-on</b>"),
            QLabel("Only selected typed hints are sent."),
        )
        selected = {item.casefold() for item in self.config.elevenlabs_entity_detection}
        self.entity_checks: dict[str, QCheckBox] = {}
        for entity in ENTITY_HINTS:
            checkbox = QCheckBox(entity.replace("_", " ").title())
            checkbox.setChecked(entity in selected)
            self.entity_checks[entity] = checkbox
            layout.addRow("", checkbox)
        self._provider_indexes["elevenlabs"] = self.provider_stack.addWidget(page)

    def _build_gemini_page(self) -> None:
        page = QWidget()
        layout = QFormLayout(page)
        self.vertex_project = QLineEdit(self.config.vertex_project or "")
        layout.addRow("Vertex Project:", self.vertex_project)
        self.vertex_location = QLineEdit(self.config.vertex_location)
        layout.addRow("Vertex Location:", self.vertex_location)
        self.gemini_model = QLineEdit(self.config.gemini_model)
        layout.addRow("Gemini Model:", self.gemini_model)
        self._provider_indexes["gemini"] = self.provider_stack.addWidget(page)

    def _build_whisper_page(self) -> None:
        page = QWidget()
        layout = QFormLayout(page)
        self.whisper_model = QComboBox()
        self.whisper_model.addItems(["tiny", "base", "small", "medium", "large-v3"])
        self.whisper_model.setCurrentText(self.config.whisper_local_model)
        layout.addRow("Whisper Model:", self.whisper_model)
        self.whisper_device = QComboBox()
        self.whisper_device.addItems(["cpu", "rocm", "cuda"])
        self.whisper_device.setCurrentText(self.config.whisper_local_device)
        layout.addRow("Whisper Device:", self.whisper_device)
        self.whisper_compute_type = QComboBox()
        self.whisper_compute_type.addItems(["default", "int8", "float16", "float32"])
        self.whisper_compute_type.setCurrentText(self.config.whisper_local_compute_type)
        layout.addRow("Compute Type:", self.whisper_compute_type)
        self.whisper_rocm_gfx_version = QLineEdit(self.config.whisper_local_rocm_gfx_version)
        layout.addRow("ROCm GFX Override:", self.whisper_rocm_gfx_version)
        self.whisper_fallback_device = QComboBox()
        self.whisper_fallback_device.addItems(["cpu", ""])
        self.whisper_fallback_device.setCurrentText(self.config.whisper_local_fallback_device)
        layout.addRow("Fallback Device:", self.whisper_fallback_device)
        self._provider_indexes["whisper_local"] = self.provider_stack.addWidget(page)

    def _build_mock_page(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("Mock mode is intended for setup and delivery testing."))
        layout.addStretch(1)
        self._provider_indexes["mock"] = self.provider_stack.addWidget(page)

    def _show_provider(self, provider: str) -> None:
        self.provider_stack.setCurrentIndex(self._provider_indexes.get(provider, self._provider_indexes["mock"]))

    def get_config_dict(self) -> dict[str, str]:
        updates = {
            "GABBEE_STT_PROVIDER": self.stt_provider.currentText(),
            "GABBEE_TOGGLE_SHORTCUT": self.toggle_shortcut.text(),
            "GABBEE_COMMAND_SHORTCUT": self.command_shortcut.text(),
            "GABBEE_WHISPER_LOCAL_MODEL": self.whisper_model.currentText(),
            "GABBEE_WHISPER_LOCAL_DEVICE": self.whisper_device.currentText(),
            "GABBEE_WHISPER_LOCAL_COMPUTE_TYPE": self.whisper_compute_type.currentText(),
            "GABBEE_WHISPER_LOCAL_ROCM_GFX_VERSION": self.whisper_rocm_gfx_version.text(),
            "GABBEE_WHISPER_LOCAL_FALLBACK_DEVICE": self.whisper_fallback_device.currentText(),
            "GABBEE_SAMPLE_RATE": self.sample_rate.text(),
            "GABBEE_GEMINI_MODEL": self.gemini_model.text(),
            "GABBEE_VERTEX_PROJECT": self.vertex_project.text(),
            "GABBEE_VERTEX_LOCATION": self.vertex_location.text(),
            "GABBEE_ELEVENLABS_REALTIME_ENABLED": str(self.elevenlabs_realtime.isChecked()).lower(),
            "GABBEE_ELEVENLABS_KEYTERMS_ENABLED": str(self.elevenlabs_keyterms.isChecked()).lower(),
            "GABBEE_ELEVENLABS_ENTITY_DETECTION": ",".join(
                name for name, checkbox in self.entity_checks.items() if checkbox.isChecked()
            ),
        }
        key = self.elevenlabs_api_key.text().strip()
        if key:
            if save_api_key(key):
                self.config.secret_api_key = key
            else:
                # Secret Service isn't universal; retain the existing .env
                # fallback rather than preventing setup.
                updates["ELEVENLABS_API_KEY"] = key
        return updates

    def refresh_diagnostics(self) -> None:
        self.diagnostics_output.setPlainText(
            json.dumps(build_redacted_report(self.config, self.diagnostics_log), indent=2, sort_keys=True)
        )

    def export_diagnostics(self) -> None:
        filename, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export Redacted Diagnostics",
            "gabbee-diagnostics.json",
            "JSON files (*.json)",
        )
        if filename:
            export_redacted_report(Path(filename), self.config, self.diagnostics_log)

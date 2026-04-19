from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QComboBox,
    QLabel,
    QCheckBox,
)

from ..config import AppConfig


class ConfigWindow(QDialog):
    def __init__(self, config: AppConfig, parent: None = None) -> None:
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("Gabbee Configuration")
        self.setMinimumWidth(400)
        
        layout = QVBoxLayout(self)
        
        form = QFormLayout()
        layout.addLayout(form)
        
        self.stt_provider = QComboBox()
        self.stt_provider.addItems(["elevenlabs", "whisper_local", "mock"])
        self.stt_provider.setCurrentText(config.stt_provider)
        form.addRow("STT Provider:", self.stt_provider)
        
        self.toggle_shortcut = QLineEdit(config.toggle_shortcut)
        form.addRow("Toggle Shortcut:", self.toggle_shortcut)
        
        self.whisper_model = QComboBox()
        self.whisper_model.addItems(["tiny", "base", "small", "medium", "large-v3"])
        self.whisper_model.setCurrentText(config.whisper_local_model)
        form.addRow("Whisper Model:", self.whisper_model)
        
        self.whisper_device = QComboBox()
        self.whisper_device.addItems(["cpu", "cuda"])
        self.whisper_device.setCurrentText(config.whisper_local_device)
        form.addRow("Whisper Device:", self.whisper_device)

        self.sample_rate = QLineEdit(str(config.sample_rate))
        form.addRow("Sample Rate:", self.sample_rate)
        
        # Add a section for keywords eventually
        form.addRow(QLabel("<b>Advanced Configuration</b>"), QLabel(""))
        
        self.elevenlabs_api_key = QLineEdit(config.env_values.get("ELEVENLABS_API_KEY", ""))
        self.elevenlabs_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("ElevenLabs API Key:", self.elevenlabs_api_key)

        buttons = QHBoxLayout()
        layout.addLayout(buttons)
        
        self.save_button = QPushButton("Save")
        self.save_button.clicked.connect(self.accept)
        buttons.addWidget(self.save_button)
        
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_button)

    def get_config_dict(self) -> dict[str, str]:
        return {
            "GABBEE_STT_PROVIDER": self.stt_provider.currentText(),
            "GABBEE_TOGGLE_SHORTCUT": self.toggle_shortcut.text(),
            "GABBEE_WHISPER_LOCAL_MODEL": self.whisper_model.currentText(),
            "GABBEE_WHISPER_LOCAL_DEVICE": self.whisper_device.currentText(),
            "GABBEE_SAMPLE_RATE": self.sample_rate.text(),
            "ELEVENLABS_API_KEY": self.elevenlabs_api_key.text(),
        }

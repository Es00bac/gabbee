from __future__ import annotations

from array import array
import os
import shutil
import subprocess

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QKeySequenceEdit,
    QVBoxLayout,
    QWizard,
    QWizardPage,
    QWidget,
)

from ..config import AppConfig
from ..desktop import AtSpiAccessibilityBackend, KWinWindowBackend
from ..ibus_client import IBusBridgeClient
from ..secret_store import save_api_key


class FirstRunWizard(QWizard):
    """Guided credential, audio, shortcut, and desktop integration setup."""

    def __init__(self, config: AppConfig, controller=None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self.controller = controller
        self.setWindowTitle("Welcome to Gabbee")
        self.setMinimumSize(600, 480)
        self._mic_process: subprocess.Popen[bytes] | None = None
        self.credentials_page = self._credentials_page()
        self.microphone_page = self._microphone_page()
        self.shortcuts_page = self._shortcuts_page()
        self.output_page = self._output_page()
        for page in (self.credentials_page, self.microphone_page, self.shortcuts_page, self.output_page):
            self.addPage(page)

    def _credentials_page(self) -> QWizardPage:
        page = QWizardPage()
        page.setTitle("ElevenLabs")
        page.setSubTitle("Scribe v2 Realtime is the default; retained WAV audio provides automatic batch recovery.")
        layout = QFormLayout(page)
        self.api_key = QLineEdit(self.config.elevenlabs_api_key() or "")
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addRow("API key:", self.api_key)
        self.realtime = QCheckBox("Enable realtime dictation")
        self.realtime.setChecked(self.config.elevenlabs_realtime_enabled)
        layout.addRow("", self.realtime)
        layout.addRow(QLabel("Profile keyterms and entity detection are optional metered add-ons and remain off."))
        return page

    def _microphone_page(self) -> QWizardPage:
        page = QWizardPage()
        page.setTitle("Microphone test")
        layout = QVBoxLayout(page)
        self.microphone_status = QLabel("Press Test Microphone to verify PipeWire capture is available.")
        layout.addWidget(self.microphone_status)
        self.microphone_level = QProgressBar()
        self.microphone_level.setRange(0, 100)
        self.microphone_level.setValue(0)
        layout.addWidget(self.microphone_level)
        self.microphone_test_button = QPushButton("Test Microphone")
        self.microphone_test_button.clicked.connect(self._test_microphone)
        layout.addWidget(self.microphone_test_button)
        layout.addStretch(1)
        return page

    def _shortcuts_page(self) -> QWizardPage:
        page = QWizardPage()
        page.setTitle("Push-to-talk shortcuts")
        page.setSubTitle("Dictation is text-only. Commands run only while the separate command shortcut is held.")
        layout = QFormLayout(page)
        self.dictation_shortcut = QKeySequenceEdit(QKeySequence(self.config.toggle_shortcut))
        self.command_shortcut = QKeySequenceEdit(QKeySequence(self.config.command_shortcut))
        layout.addRow("Dictation:", self.dictation_shortcut)
        layout.addRow("Command:", self.command_shortcut)
        return page

    def _output_page(self) -> QWizardPage:
        page = QWizardPage()
        page.setTitle("Text and desktop integration")
        layout = QFormLayout(page)
        self.output_test = QPlainTextEdit()
        self.output_test.setPlaceholderText("Focus here and use dictation to test final output.")
        layout.addRow("Output test:", self.output_test)
        ibus = IBusBridgeClient(self.config.paths.engine_socket).ping()
        layout.addRow("IBus:", QLabel("Ready" if ibus.ok else "Not active — AT-SPI/clipboard/dotool fallbacks remain available"))
        layout.addRow("Accessibility:", QLabel("Ready" if AtSpiAccessibilityBackend().available else "AT-SPI unavailable"))
        desktop = ":".join(
            value
            for value in (
                os.environ.get("XDG_CURRENT_DESKTOP", ""),
                os.environ.get("DESKTOP_SESSION", ""),
            )
            if value
        )
        kwin_ready = bool(shutil.which("qdbus6") and KWinWindowBackend().active_window())
        if "kde" in desktop.casefold() or "plasma" in desktop.casefold():
            window_status = "Ready" if kwin_ready else "KWin window API unavailable"
        else:
            window_status = "IBus/AT-SPI mode (compositor window switching unavailable)"
        layout.addRow("Desktop integration:", QLabel(window_status))
        layout.addRow("Pointer fallback:", QLabel("Available" if shutil.which("dotool") else "dotool not found"))
        return page

    def _test_microphone(self) -> None:
        if not shutil.which("pw-record"):
            self.microphone_status.setText("pw-record was not found.")
            self.microphone_level.setValue(0)
            return
        if self._mic_process is not None:
            return
        try:
            self._mic_process = subprocess.Popen(
                ["pw-record", "--rate", "16000", "--channels", "1", "--format", "s16", "--raw", "-"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            self.microphone_status.setText(f"Could not start microphone test: {exc}")
            return
        self.microphone_test_button.setEnabled(False)
        self.microphone_status.setText("Listening for one second…")
        QTimer.singleShot(1_000, self._finish_microphone_test)

    def _finish_microphone_test(self) -> None:
        process, self._mic_process = self._mic_process, None
        self.microphone_test_button.setEnabled(True)
        if process is None:
            return
        try:
            process.terminate()
            pcm, _stderr = process.communicate(timeout=2)
        except (OSError, subprocess.SubprocessError):
            process.kill()
            pcm, _stderr = process.communicate()
        samples = array("h")
        samples.frombytes(pcm[: len(pcm) - len(pcm) % 2])
        if not samples:
            self.microphone_level.setValue(0)
            self.microphone_status.setText("No microphone samples were received.")
            return
        rms = (sum(sample * sample for sample in samples) / len(samples)) ** 0.5
        level = min(100, round(rms / 32767 * 400))
        self.microphone_level.setValue(level)
        self.microphone_status.setText(f"Microphone captured audio (level {level}%).")

    def accept(self) -> None:
        self._stop_microphone_test()
        updates = {
            "GABBEE_STT_PROVIDER": "elevenlabs" if self.api_key.text().strip() else self.config.stt_provider,
            "GABBEE_ELEVENLABS_REALTIME_ENABLED": str(self.realtime.isChecked()).lower(),
            "GABBEE_TOGGLE_SHORTCUT": self.dictation_shortcut.keySequence().toString().strip() or "F5",
            "GABBEE_COMMAND_SHORTCUT": self.command_shortcut.keySequence().toString().strip() or "F6",
        }
        key = self.api_key.text().strip()
        if key:
            if save_api_key(key):
                self.config.secret_api_key = key
            else:
                updates["ELEVENLABS_API_KEY"] = key
        self.config.save(updates)
        self.config.paths.setup_marker.write_text("complete\n", encoding="utf-8")
        super().accept()

    def reject(self) -> None:
        self._stop_microphone_test()
        super().reject()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._stop_microphone_test()
        super().closeEvent(event)

    def _stop_microphone_test(self) -> None:
        process, self._mic_process = self._mic_process, None
        if process is None:
            return
        try:
            process.terminate()
            process.wait(timeout=1)
        except (OSError, subprocess.SubprocessError):
            try:
                process.kill()
            except OSError:
                pass

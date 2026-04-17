from __future__ import annotations

from pathlib import Path
import subprocess


class PipeWireRecorder:
    def __init__(self, sample_rate: int = 16000, channels: int = 1) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self._process: subprocess.Popen[bytes] | None = None
        self._current_output: Path | None = None

    @property
    def is_recording(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self, output_path: Path, source_name: str | None = None) -> None:
        if self.is_recording:
            raise RuntimeError("Recording is already active.")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path.exists():
            output_path.unlink()

        cmd = [
            "pw-record",
            "--rate",
            str(self.sample_rate),
            "--channels",
            str(self.channels),
            "--format",
            "s16",
            "--container",
            "wav",
        ]
        if source_name:
            cmd.extend(["--target", source_name])
        cmd.append(str(output_path))

        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("pw-record is required but was not found in PATH.") from exc
        self._current_output = output_path

    def stop(self) -> Path:
        if not self.is_recording or self._process is None or self._current_output is None:
            raise RuntimeError("Recording is not active.")

        self._process.terminate()
        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=5)
        output_path = self._current_output
        self._process = None
        self._current_output = None
        return output_path

    def cancel(self) -> None:
        if self._process is not None and self.is_recording:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=5)
        if self._current_output and self._current_output.exists():
            self._current_output.unlink()
        self._process = None
        self._current_output = None

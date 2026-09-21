from __future__ import annotations

from collections import deque
from collections.abc import Callable
from pathlib import Path
import array
import subprocess
import sys
import threading
import wave


AudioChunkCallback = Callable[[bytes], None]


class BufferedAudioRelay:
    """Keep early microphone frames until a realtime consumer is connected."""

    def __init__(self, *, max_buffer_seconds: float = 30, sample_rate: int = 16000, channels: int = 1) -> None:
        self._max_bytes = max(32_000, int(max_buffer_seconds * sample_rate * channels * 2))
        self._chunks: deque[bytes] = deque()
        self._buffered_bytes = 0
        self._consumer: AudioChunkCallback | None = None
        self._lock = threading.Lock()

    def __call__(self, chunk: bytes) -> None:
        with self._lock:
            if self._consumer is not None:
                self._consumer(chunk)
                return
            self._chunks.append(chunk)
            self._buffered_bytes += len(chunk)
            while self._buffered_bytes > self._max_bytes and self._chunks:
                self._buffered_bytes -= len(self._chunks.popleft())

    def attach(self, consumer: AudioChunkCallback) -> None:
        with self._lock:
            for chunk in self._chunks:
                consumer(chunk)
            self._chunks.clear()
            self._buffered_bytes = 0
            self._consumer = consumer

    def discard(self) -> None:
        with self._lock:
            self._chunks.clear()
            self._buffered_bytes = 0
            self._consumer = None


def _peak_percent(chunk: bytes) -> int:
    """Peak amplitude of one signed 16-bit little-endian mono chunk, 0-100.

    Kept here rather than in a consumer because the reader thread is the only
    place the raw PCM exists; audioop is gone from Python 3.13 onward, so the
    peak is taken from an array view instead.
    """

    usable = len(chunk) - (len(chunk) % 2)
    if usable <= 0:
        return 0
    samples = array.array("h")
    samples.frombytes(chunk[:usable])
    if sys.byteorder != "little":
        samples.byteswap()
    peak = max(max(samples), -min(samples))
    return min(100, round(peak * 100 / 32768))


class PipeWireRecorder:
    """Records mono PCM once, teeing it to realtime STT and a recovery WAV."""

    def __init__(
        self,
        sample_rate: int = 16000,
        channels: int = 1,
        chunk_seconds: float = 0.04,
        startup_timeout: float = 1.5,
    ) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk_bytes = max(320, int(sample_rate * channels * 2 * chunk_seconds))
        self.startup_timeout = max(0.1, startup_timeout)
        self._process: subprocess.Popen[bytes] | None = None
        self._current_output: Path | None = None
        self._reader: threading.Thread | None = None
        self._wave: wave.Wave_write | None = None
        self._chunk_callback: AudioChunkCallback | None = None
        self._stream_error: Exception | None = None
        self._stream_ready = threading.Event()
        # Optional peak-level observer, called with 0-100 for each PCM chunk
        # from the reader thread. Set by a capture-level consumer such as the
        # QindaQt Voice1 provider; None disables the computation entirely.
        self.level_observer: Callable[[int], None] | None = None

    @property
    def is_recording(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def stream_error(self) -> Exception | None:
        return self._stream_error

    def start(self, output_path: Path, source_name: str | None = None) -> None:
        self.start_streaming(output_path, source_name, None)

    def start_streaming(
        self,
        output_path: Path,
        source_name: str | None = None,
        on_audio: AudioChunkCallback | None = None,
    ) -> None:
        if self.is_recording:
            raise RuntimeError("Recording is already active.")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path.exists():
            output_path.unlink()

        command = [
            "pw-record",
            "--rate",
            str(self.sample_rate),
            "--channels",
            str(self.channels),
            "--format",
            "s16",
            "--raw",
        ]
        if source_name:
            command.extend(["--target", source_name])
        command.append("-")
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("pw-record is required but was not found in PATH.") from exc

        try:
            wav = wave.open(str(output_path), "wb")
            wav.setnchannels(self.channels)
            wav.setsampwidth(2)
            wav.setframerate(self.sample_rate)
        except Exception:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
            raise
        self._process = process
        self._current_output = output_path
        self._wave = wav
        self._chunk_callback = on_audio
        self._stream_error = None
        self._stream_ready.clear()
        self._reader = threading.Thread(target=self._read_pcm, daemon=True, name="gabbee-pcm-recorder")
        self._reader.start()
        # Do not report that dictation is live until PipeWire has actually
        # delivered audio.  Otherwise a person who starts talking on Gabbee's
        # start cue can lose the leading syllables while pw-record is still
        # negotiating the source stream.
        if not self._stream_ready.wait(self.startup_timeout):
            self.cancel()
            raise RuntimeError("Microphone stream did not deliver an initial audio frame in time.")

    def _read_pcm(self) -> None:
        process = self._process
        wav = self._wave
        if process is None or process.stdout is None or wav is None:
            return
        while True:
            chunk = process.stdout.read(self.chunk_bytes)
            if not chunk:
                break
            wav.writeframesraw(chunk)
            self._stream_ready.set()
            observer = self.level_observer
            if observer is not None:
                try:
                    observer(_peak_percent(chunk))
                except Exception:
                    # A meter is never allowed to interrupt capture.
                    self.level_observer = None
            callback = self._chunk_callback
            if callback is not None and self._stream_error is None:
                try:
                    callback(chunk)
                except Exception as exc:
                    # Continue retaining recoverable audio after a provider or
                    # network failure; the controller will use batch fallback.
                    self._stream_error = exc
        try:
            wav.close()
        finally:
            self._wave = None

    def stop(self) -> Path:
        if not self.is_recording or self._process is None or self._current_output is None:
            raise RuntimeError("Recording is not active.")
        process = self._process
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        if self._reader is not None:
            self._reader.join(timeout=5)
        if self._wave is not None:
            self._wave.close()
            self._wave = None
        output_path = self._current_output
        self._process = None
        self._current_output = None
        self._reader = None
        self._chunk_callback = None
        return output_path

    def cancel(self) -> None:
        if self._process is not None and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=5)
        if self._reader is not None:
            self._reader.join(timeout=5)
        if self._wave is not None:
            self._wave.close()
        if self._current_output and self._current_output.exists():
            self._current_output.unlink()
        self._process = None
        self._current_output = None
        self._reader = None
        self._wave = None
        self._chunk_callback = None
        self._stream_error = None
        self._stream_ready.clear()

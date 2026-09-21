from __future__ import annotations

import io
import threading
import wave

from gabbee.audio import BufferedAudioRelay, PipeWireRecorder


class ChunkStream:
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks

    def read(self, _size: int) -> bytes:
        return self.chunks.pop(0) if self.chunks else b""


class Process:
    def __init__(self, chunks: list[bytes]) -> None:
        self.stdout = ChunkStream(chunks)
        self.terminated = False

    def poll(self):
        return 0 if self.terminated else None

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.terminated = True


def test_pcm_is_teed_to_recovery_wav_after_stream_failure(tmp_path, monkeypatch) -> None:
    chunks = [b"\x01\x00\x02\x00", b"\x03\x00\x04\x00"]
    process = Process(list(chunks))
    monkeypatch.setattr("gabbee.audio.subprocess.Popen", lambda *args, **kwargs: process)
    callbacks = []

    def stream(chunk: bytes) -> None:
        callbacks.append(chunk)
        raise RuntimeError("network failed")

    path = tmp_path / "recovery.wav"
    recorder = PipeWireRecorder(sample_rate=16000, chunk_seconds=0.001)
    recorder.start_streaming(path, on_audio=stream)
    recorder._reader.join(1)  # type: ignore[union-attr]
    stopped = recorder.stop()

    assert stopped == path
    assert isinstance(recorder.stream_error, RuntimeError)
    assert callbacks == [chunks[0]]
    with wave.open(str(path), "rb") as recovered:
        assert recovered.getnchannels() == 1
        assert recovered.getframerate() == 16000
        assert recovered.readframes(recovered.getnframes()) == b"".join(chunks)


def test_recorder_waits_for_the_first_captured_audio_frame(tmp_path, monkeypatch) -> None:
    first_frame = threading.Event()
    release_frame = threading.Event()

    class DelayedChunkStream:
        def __init__(self) -> None:
            self.sent = False

        def read(self, _size: int) -> bytes:
            first_frame.set()
            release_frame.wait(1)
            if not release_frame.is_set() or self.sent:
                return b""
            self.sent = True
            return b"\x01\x00"

    class DelayedProcess(Process):
        def __init__(self) -> None:
            self.stdout = DelayedChunkStream()
            self.terminated = False

    process = DelayedProcess()
    monkeypatch.setattr("gabbee.audio.subprocess.Popen", lambda *args, **kwargs: process)
    recorder = PipeWireRecorder(sample_rate=16000, chunk_seconds=0.001, startup_timeout=1)
    started = threading.Event()

    def start() -> None:
        recorder.start_streaming(tmp_path / "ready.wav")
        started.set()

    thread = threading.Thread(target=start)
    thread.start()
    assert first_frame.wait(1)
    assert not started.is_set()
    release_frame.set()
    thread.join(1)
    assert started.is_set()
    recorder.stop()


def test_buffered_relay_flushes_leading_audio_in_order() -> None:
    relay = BufferedAudioRelay(max_buffer_seconds=1, sample_rate=10, channels=1)
    received: list[bytes] = []

    relay(b"first")
    relay(b"second")
    relay.attach(received.append)
    relay(b"third")

    assert received == [b"first", b"second", b"third"]

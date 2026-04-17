from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gabbee.models import DeliveryResult
from gabbee.output import ActiveWindowTextSink, FallbackTextSink, MirroringTextSink


class FakeSink:
    def __init__(self, result: DeliveryResult) -> None:
        self.result = result
        self.received: list[str] = []

    def deliver(self, text: str) -> DeliveryResult:
        self.received.append(text)
        return DeliveryResult(
            ok=self.result.ok,
            method=self.result.method,
            detail=self.result.detail,
        )


class OutputTests(unittest.TestCase):
    def test_fallback_sink_returns_primary_success_without_running_fallback(self) -> None:
        primary = FakeSink(DeliveryResult(ok=True, method="ibus", detail="Committed through IBus."))
        fallback = FakeSink(DeliveryResult(ok=True, method="clipboard", detail="Copied with wl-copy."))
        sink = FallbackTextSink(primary=primary, fallback=fallback)

        result = sink.deliver("hello")

        self.assertTrue(result.ok)
        self.assertEqual(result.method, "ibus")
        self.assertEqual(result.detail, "Committed through IBus.")
        self.assertEqual(primary.received, ["hello"])
        self.assertEqual(fallback.received, [])

    def test_fallback_sink_uses_clipboard_when_primary_fails(self) -> None:
        primary = FakeSink(DeliveryResult(ok=False, method="ibus", detail="No active Gabbee IBus engine is focused."))
        fallback = FakeSink(DeliveryResult(ok=True, method="clipboard", detail="Copied with wl-copy."))
        sink = FallbackTextSink(primary=primary, fallback=fallback)

        result = sink.deliver("hello")

        self.assertTrue(result.ok)
        self.assertEqual(result.method, "clipboard")
        self.assertIn("Fallback: Copied with wl-copy.", result.detail)
        self.assertEqual(primary.received, ["hello"])
        self.assertEqual(fallback.received, ["hello"])

    def test_mirroring_sink_copies_success_to_clipboard(self) -> None:
        primary = FakeSink(DeliveryResult(ok=True, method="type", detail="Typed into the active window with dotool."))
        mirror = FakeSink(DeliveryResult(ok=True, method="clipboard", detail="Copied with wl-copy."))
        sink = MirroringTextSink(primary=primary, mirror=mirror)

        result = sink.deliver("hello")

        self.assertTrue(result.ok)
        self.assertEqual(result.method, "type+clipboard")
        self.assertIn("Mirrored: Copied with wl-copy.", result.detail)
        self.assertEqual(primary.received, ["hello"])
        self.assertEqual(mirror.received, ["hello"])

    def test_active_window_sink_prefers_dotool(self) -> None:
        sink = ActiveWindowTextSink()
        with patch("gabbee.output.shutil.which") as which_mock, patch("gabbee.output.subprocess.run") as run_mock:
            which_mock.side_effect = lambda name: "/usr/bin/dotool" if name == "dotool" else None
            result = sink.deliver("hello\nworld")

        self.assertTrue(result.ok)
        self.assertEqual(result.method, "type")
        run_mock.assert_called_once()
        args, kwargs = run_mock.call_args
        self.assertEqual(args[0], ["dotool"])
        self.assertEqual(kwargs["input"], b"typedelay 1\ntype hello\nkey enter\ntype world\n")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

from pathlib import Path
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gabbee import install


class InstallTests(unittest.TestCase):
    def test_ibus_env_uses_component_directory(self) -> None:
        component = Path("/tmp/gabbee/component/gabbee.xml")
        env = install._ibus_env(component)
        self.assertEqual(env["IBUS_COMPONENT_PATH"], str(component.parent))
        self.assertEqual(env["PATH"], os.environ["PATH"])

    def test_refresh_ibus_updates_cache_then_restarts_daemon(self) -> None:
        calls = []

        def fake_run(command, **kwargs):
            calls.append((command, kwargs["env"]["IBUS_COMPONENT_PATH"]))
            if command == ["ibus-daemon", "-dsrx"]:
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        with patch("gabbee.install.subprocess.run", side_effect=fake_run):
            ok, messages = install._refresh_ibus(Path("/tmp/gabbee/component/gabbee.xml"))

        self.assertTrue(ok)
        self.assertEqual(calls[0][0], ["ibus", "write-cache"])
        self.assertEqual(calls[1][0], ["ibus-daemon", "-dsrx"])
        self.assertEqual(calls[0][1], "/tmp/gabbee/component")
        self.assertIn("Refreshed IBus cache", messages[0])

    def test_refresh_ibus_falls_back_to_ibus_restart(self) -> None:
        calls = []

        def fake_run(command, **kwargs):
            calls.append(command)
            if command == ["ibus-daemon", "-dsrx"]:
                return subprocess.CompletedProcess(command, 1, stdout="", stderr="no default config")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        with patch("gabbee.install.subprocess.run", side_effect=fake_run):
            ok, messages = install._refresh_ibus(Path("/tmp/gabbee/component/gabbee.xml"))

        self.assertTrue(ok)
        self.assertEqual(calls[-1], ["ibus", "restart"])
        self.assertIn("ibus restart", messages[-1])


if __name__ == "__main__":
    unittest.main()

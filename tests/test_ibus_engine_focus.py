from __future__ import annotations

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gabbee.ibus_engine import ActiveEngineRegistry, GabbeeEngine


class _DummyEngine:
    def __init__(self, registry: ActiveEngineRegistry) -> None:
        self.registry = registry


class IBusEngineFocusTests(unittest.TestCase):
    def test_enable_does_not_mark_engine_active(self) -> None:
        registry = ActiveEngineRegistry()
        engine = _DummyEngine(registry)
        GabbeeEngine.__dict__["do_enable"](engine)
        self.assertFalse(registry.has_active_engine())

    def test_focus_in_marks_engine_active(self) -> None:
        registry = ActiveEngineRegistry()
        engine = _DummyEngine(registry)
        GabbeeEngine.__dict__["do_focus_in"](engine)
        self.assertTrue(registry.has_active_engine())

    def test_focus_out_and_disable_clear_active_engine(self) -> None:
        registry = ActiveEngineRegistry()
        engine = _DummyEngine(registry)
        GabbeeEngine.__dict__["do_focus_in"](engine)
        self.assertTrue(registry.has_active_engine())
        GabbeeEngine.__dict__["do_focus_out"](engine)
        self.assertFalse(registry.has_active_engine())
        GabbeeEngine.__dict__["do_focus_in"](engine)
        self.assertTrue(registry.has_active_engine())
        GabbeeEngine.__dict__["do_disable"](engine)
        self.assertFalse(registry.has_active_engine())


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
PLASMOID = ROOT / "plasmoid"


class PlasmoidPackageTests(unittest.TestCase):
    def test_compact_representation_has_panel_layout_hints(self) -> None:
        qml = (PLASMOID / "contents" / "ui" / "CompactRepresentation.qml").read_text(encoding="utf-8")

        self.assertIn("import org.kde.plasma.plasmoid", qml)
        self.assertIn("required property PlasmoidItem plasmoidItem", qml)
        self.assertIn("Layout.minimumWidth", qml)
        self.assertIn("Layout.preferredWidth", qml)
        self.assertIn("Layout.minimumHeight", qml)
        self.assertIn("plasmoidItem.currentState", qml)
        self.assertNotIn("Plasmoid.currentState", qml)

    def test_representation_fills_thin_panel_without_outer_padding(self) -> None:
        qml = (PLASMOID / "contents" / "ui" / "CompactRepresentation.qml").read_text(encoding="utf-8")

        self.assertIn("Layout.fillHeight: true", qml)
        self.assertIn("anchors.margins: 0", qml)
        self.assertIn("radius: 0", qml)
        self.assertNotIn("anchors.margins: 2", qml)
        self.assertNotIn("Layout.maximumHeight: implicitHeight", qml)
        self.assertNotIn("radius: 10", qml)

    def test_main_uses_full_representation_for_panel_bar(self) -> None:
        qml = (PLASMOID / "contents" / "ui" / "main.qml").read_text(encoding="utf-8")

        self.assertIn("compactRepresentation: CompactRepresentation {", qml)
        self.assertIn("fullRepresentation: CompactRepresentation {", qml)
        self.assertIn("preferredRepresentation: fullRepresentation", qml)
        self.assertIn("plasmoidItem: root", qml)

    def test_backend_user_service_is_packaged(self) -> None:
        service = ROOT / "share" / "systemd" / "user" / "gabbee-plasma.service"

        self.assertTrue(service.exists())
        text = service.read_text(encoding="utf-8")
        self.assertIn("ExecStart=%h/gabbee/.venv/bin/python -m gabbee.plasma_dbus_service", text)
        self.assertIn("After=graphical-session.target", text)
        self.assertIn("WantedBy=graphical-session.target", text)
        self.assertNotIn("WantedBy=default.target", text)


if __name__ == "__main__":
    unittest.main()

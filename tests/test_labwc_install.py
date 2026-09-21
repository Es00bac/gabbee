from __future__ import annotations

from pathlib import Path

import pytest

from gabbee.labwc_install import (
    END_MARKER,
    START_MARKER,
    install_shortcuts,
    qt_shortcut_to_labwc,
    write_autostart,
    write_ibus_autostart,
)


def test_qt_shortcuts_are_converted_to_labwc_key_names() -> None:
    assert qt_shortcut_to_labwc("F5") == "F5"
    assert qt_shortcut_to_labwc("Ctrl+Alt+F6") == "C-A-F6"
    assert qt_shortcut_to_labwc("Meta+Shift+A") == "W-S-A"


def test_installer_adds_press_and_release_bindings_idempotently(tmp_path: Path) -> None:
    rc = tmp_path / "rc.xml"
    original = "<labwc_config>\n  <keyboard>\n  </keyboard>\n</labwc_config>\n"
    rc.write_text(original, encoding="utf-8")

    backup = install_shortcuts(
        rc,
        control_command="/opt/gabbee-control",
        dictation_shortcut="F5",
        command_shortcut="Ctrl+F6",
    )
    first = rc.read_text(encoding="utf-8")
    install_shortcuts(
        rc,
        control_command="/opt/gabbee-control",
        dictation_shortcut="F5",
        command_shortcut="Ctrl+F6",
    )

    assert backup.read_text(encoding="utf-8") == original
    assert rc.read_text(encoding="utf-8") == first
    assert first.count(START_MARKER) == first.count(END_MARKER) == 1
    assert '<keybind key="F5">' in first
    assert '<keybind key="F5" onRelease="yes">' in first
    assert '<keybind key="C-F6">' in first
    assert '<keybind key="C-F6" onRelease="yes">' in first
    assert "/opt/gabbee-control start-command" in first


def test_installer_rejects_malformed_existing_markers(tmp_path: Path) -> None:
    rc = tmp_path / "rc.xml"
    rc.write_text(f"<labwc_config><keyboard>{START_MARKER}</keyboard></labwc_config>", encoding="utf-8")

    with pytest.raises(ValueError, match="incomplete or duplicate"):
        install_shortcuts(
            rc,
            control_command="gabbee-control",
            dictation_shortcut="F5",
            command_shortcut="F6",
        )


def test_autostart_is_lxqt_scoped(tmp_path: Path) -> None:
    target = tmp_path / "autostart" / "gabbee.desktop"
    write_autostart(target, bar_command="/opt/gabbee-bar")

    text = target.read_text(encoding="utf-8")
    assert "Exec=/opt/gabbee-bar" in text
    assert "OnlyShowIn=LXQt;" in text

    ibus_target = tmp_path / "autostart" / "ibus.desktop"
    write_ibus_autostart(ibus_target)
    ibus_text = ibus_target.read_text(encoding="utf-8")
    assert "Exec=ibus-daemon -drx" in ibus_text
    assert "OnlyShowIn=LXQt;" in ibus_text

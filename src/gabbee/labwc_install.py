from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from xml.sax.saxutils import escape

from .config import load_config


START_MARKER = "<!-- Gabbee push-to-talk shortcuts: begin -->"
END_MARKER = "<!-- Gabbee push-to-talk shortcuts: end -->"


def qt_shortcut_to_labwc(shortcut: str) -> str:
    modifiers = {
        "ctrl": "C",
        "control": "C",
        "alt": "A",
        "shift": "S",
        "meta": "W",
        "super": "W",
    }
    parts = [part.strip() for part in shortcut.split("+") if part.strip()]
    if not parts:
        raise ValueError("Shortcut cannot be empty.")
    normalized = [modifiers.get(part.casefold(), part) for part in parts]
    return "-".join(normalized)


def shortcut_block(
    *,
    control_command: str,
    dictation_shortcut: str,
    command_shortcut: str,
) -> str:
    command = escape(control_command, {'"': "&quot;"})
    bindings = [
        (qt_shortcut_to_labwc(dictation_shortcut), "start"),
        (qt_shortcut_to_labwc(dictation_shortcut), "stop", True),
    ]
    if command_shortcut.strip():
        bindings.extend(
            [
                (qt_shortcut_to_labwc(command_shortcut), "start-command"),
                (qt_shortcut_to_labwc(command_shortcut), "stop", True),
            ]
        )
    lines = ["    " + START_MARKER]
    for binding in bindings:
        key, action, *release = binding
        release_attribute = ' onRelease="yes"' if release else ""
        lines.extend(
            [
                f'    <keybind key="{escape(key)}"{release_attribute}>',
                f'      <action name="Execute" command="{command} {action}" />',
                "    </keybind>",
            ]
        )
    lines.append("    " + END_MARKER)
    return "\n".join(lines)


def install_shortcuts(
    rc_path: Path,
    *,
    control_command: str,
    dictation_shortcut: str,
    command_shortcut: str,
) -> Path:
    text = rc_path.read_text(encoding="utf-8")
    block = shortcut_block(
        control_command=control_command,
        dictation_shortcut=dictation_shortcut,
        command_shortcut=command_shortcut,
    )
    if START_MARKER in text or END_MARKER in text:
        if text.count(START_MARKER) != 1 or text.count(END_MARKER) != 1:
            raise ValueError("The Labwc config has incomplete or duplicate Gabbee markers.")
        start = text.index(START_MARKER)
        end = text.index(END_MARKER, start) + len(END_MARKER)
        line_start = text.rfind("\n", 0, start) + 1
        line_end = text.find("\n", end)
        if line_end < 0:
            line_end = len(text)
            separator = ""
        else:
            line_end += 1
            separator = "\n"
        text = text[:line_start] + block + separator + text[line_end:]
    else:
        closings = list(re.finditer(r"(?m)^[ \t]*</keyboard>", text))
        if len(closings) != 1:
            raise ValueError("Could not find exactly one </keyboard> element in the Labwc config.")
        closing = closings[0]
        text = text[: closing.start()] + block + "\n" + text[closing.start() :]

    backup = rc_path.with_name(rc_path.name + ".pre-gabbee")
    if not backup.exists():
        shutil.copy2(rc_path, backup)
    rc_path.write_text(text, encoding="utf-8")
    return backup


def write_autostart(path: Path, *, bar_command: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """[Desktop Entry]
Type=Application
Name=Gabbee Voice Input
Comment=Start Gabbee for desktop-wide push-to-talk
Exec={bar_command}
Icon=gabbee
Terminal=false
OnlyShowIn=LXQt;
X-GNOME-Autostart-enabled=true
""".format(bar_command=bar_command),
        encoding="utf-8",
    )


def write_ibus_autostart(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """[Desktop Entry]
Type=Application
Name=IBus
Comment=Start the input method daemon for Gabbee
Exec=ibus-daemon -drx
Terminal=false
OnlyShowIn=LXQt;
X-GNOME-Autostart-enabled=true
""",
        encoding="utf-8",
    )


def _default_command(name: str, module: str) -> str:
    installed = shutil.which(name)
    return installed or f"{sys.executable} -m {module}"


def main(argv: list[str] | None = None) -> int:
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    parser = argparse.ArgumentParser(
        prog="gabbee-install-labwc",
        description="Install Gabbee push-to-talk bindings for an LXQt/Labwc session.",
    )
    parser.add_argument("--rc", type=Path, default=config_home / "labwc" / "rc.xml")
    parser.add_argument("--autostart", type=Path, default=config_home / "autostart" / "gabbee-bar.desktop")
    parser.add_argument("--control-command", default=_default_command("gabbee-control", "gabbee.control"))
    parser.add_argument("--bar-command", default=_default_command("gabbee-bar", "gabbee.main_bar"))
    parser.add_argument("--no-autostart", action="store_true")
    parser.add_argument("--no-reconfigure", action="store_true")
    args = parser.parse_args(argv)

    if not args.rc.is_file():
        parser.error(f"Labwc config not found: {args.rc}")
    config = load_config()
    backup = install_shortcuts(
        args.rc,
        control_command=args.control_command,
        dictation_shortcut=config.toggle_shortcut,
        command_shortcut=config.command_shortcut,
    )
    if not args.no_autostart:
        write_autostart(args.autostart, bar_command=args.bar_command)
        write_ibus_autostart(args.autostart.with_name("ibus-daemon.desktop"))
    if not args.no_reconfigure and shutil.which("labwc"):
        subprocess.run(["labwc", "--reconfigure"], check=False)

    print(f"Installed Labwc shortcuts in {args.rc}")
    print(f"Backup: {backup}")
    if not args.no_autostart:
        print(f"Installed LXQt autostart entry: {args.autostart}")
        print(f"Installed IBus autostart entry: {args.autostart.with_name('ibus-daemon.desktop')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import List, Tuple

from .ibus_component import write_user_component_file


def _default_engine_command() -> str:
    installed = shutil.which("gabbee-engine")
    if installed:
        return installed
    return f"{sys.executable} -m gabbee.main_engine"


def _default_project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _default_icon_path() -> Path | None:
    env_icon = Path(os.environ.get("GABBEE_ICON_PATH")).expanduser() if os.environ.get("GABBEE_ICON_PATH") else None
    if env_icon and env_icon.exists():
        return env_icon

    repo_root = _default_project_root()
    for candidate in (
        Path.cwd() / "gabbee.png",
        repo_root / "gabbee.png",
        repo_root / "share" / "icons" / "gabbee.png",
    ):
        if candidate.exists():
            return candidate
    return None


def _default_bar_command() -> str:
    installed = shutil.which("gabbee-bar")
    if installed:
        return installed
    return f"{sys.executable} -m gabbee.main_bar"


def _install_icon(source: Path, icon_name: str = "gabbee") -> Path:
    target_root = Path.home() / ".local" / "share" / "icons" / "hicolor" / "256x256" / "apps"
    target_root.mkdir(parents=True, exist_ok=True)
    target = target_root / f"{icon_name}.png"
    shutil.copy2(source, target)
    return target


def _write_desktop_file(
    icon_path: Path | None = None,
    desktop_path: Path | None = None,
    bar_command: str | None = None,
) -> Path:
    icon_value = icon_path.name[:-4] if icon_path and icon_path.suffix.lower() == ".png" else "gabbee"
    if icon_path is not None and icon_path.suffix.lower() != ".png":
        icon_value = icon_path.name

    desktop_path = desktop_path or (Path.home() / ".local" / "share" / "applications" / "gabbee-bar.desktop")
    desktop_path.parent.mkdir(parents=True, exist_ok=True)
    command = bar_command or _default_bar_command()
    desktop_path.write_text(
        f"""[Desktop Entry]
Type=Application
Name=Gabbee Voice Input
Comment=Start the Gabbee floating voice input bar
Exec={command}
Icon={icon_value}
Terminal=false
Categories=Utility;Qt;
StartupNotify=true
""",
        encoding="utf-8",
    )
    return desktop_path


def _resolve_icon_install_path(icon: Path | None) -> Path | None:
    if icon is None:
        return None
    icon_path = icon.expanduser()
    return icon_path if icon_path.exists() else None


def _ibus_component_directory(component_path: Path | None) -> Path:
    if component_path is not None:
        return component_path.expanduser().resolve().parent
    return Path.home() / ".local" / "share" / "ibus" / "component"


def _ibus_env(component_path: Path | None) -> dict[str, str]:
    env = os.environ.copy()
    env["IBUS_COMPONENT_PATH"] = str(_ibus_component_directory(component_path))
    return env


def _run_ibus_command(command: list[str], component_path: Path | None) -> Tuple[bool, str]:
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            env=_ibus_env(component_path),
        )
    except FileNotFoundError:
        return False, f"{' '.join(command)} unavailable"

    if completed.returncode == 0:
        return True, f"{' '.join(command)}"

    detail = completed.stderr.strip() or completed.stdout.strip() or f"exit code {completed.returncode}"
    return False, f"{' '.join(command)} failed: {detail}"


def _refresh_ibus(component_path: Path | None) -> Tuple[bool, list[str]]:
    messages: list[str] = []
    cache_ok, cache_message = _run_ibus_command(["ibus", "write-cache"], component_path)
    messages.append(("✓ Refreshed IBus cache with: " if cache_ok else "Warning: ") + cache_message)

    daemon_ok, daemon_message = _run_ibus_command(["ibus-daemon", "-dsrx"], component_path)
    messages.append(("✓ Restarted IBus with: " if daemon_ok else "Warning: ") + daemon_message)
    if daemon_ok:
        return True, messages

    restart_ok, restart_message = _run_ibus_command(["ibus", "restart"], component_path)
    messages.append(("✓ Restarted IBus with: " if restart_ok else "Warning: ") + restart_message)
    return restart_ok, messages


def _verify_file_contains(path: Path, expected: str | None = None) -> bool:
    if not path.exists():
        return False
    if expected is None:
        return True
    try:
        return expected in path.read_text(encoding="utf-8")
    except OSError:
        return False


def _print_setup_summary(
    engine_written: Path | None,
    icon_installed: Path | None,
    desktop_written: Path | None,
    did_work: List[str],
    restart_ok: bool | None = None,
) -> None:
    if engine_written:
        icon_ok = "✓ IBus component written"
        if _verify_file_contains(engine_written, "<name>org.freedesktop.IBus.Gabbee</name>"):
            icon_ok = "✓ IBus component contains Gabbee ID"
        did_work.append(f"{icon_ok}: {engine_written}")
    if icon_installed:
        did_work.append(f"✓ Icon installed: {icon_installed}")
    if desktop_written:
        did_work.append(f"✓ Desktop launcher written: {desktop_written}")

    if did_work:
        print("Done:")
        for item in did_work:
            print(f" - {item}")
    else:
        print("No setup actions were selected.")

    if restart_ok is not None:
        if restart_ok:
            print("✓ IBus restart attempted")
        else:
            component_dir = _ibus_component_directory(engine_written)
            print(
                "Note: automatic IBus restart failed; you can manually run: "
                f"IBUS_COMPONENT_PATH={component_dir} ibus-daemon -dsrx"
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gabbee-install-ibus",
        description="Write the user-local IBus component file for Gabbee.",
    )
    parser.add_argument(
        "--engine-command",
        default=_default_engine_command(),
        help="Command used by IBus to launch the Gabbee engine.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional destination for the generated component XML.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Write both IBus component and desktop launcher (and install icon if available).",
    )
    parser.add_argument(
        "--setup",
        action="store_true",
        help="Run the full setup flow: component, icon install, desktop launcher, and IBus restart guidance.",
    )
    parser.add_argument(
        "--desktop-file",
        type=Path,
        help="Destination path for the launcher desktop file.",
    )
    parser.add_argument(
        "--icon",
        type=Path,
        help="Path to icon image for the desktop launcher and IBus engine.",
    )
    parser.add_argument(
        "--bar-command",
        default=_default_bar_command(),
        help="Command used by desktop launcher to open the bar.",
    )
    parser.add_argument(
        "--skip-engine-install",
        action="store_true",
        help="Skip writing the IBus component file.",
    )
    parser.add_argument(
        "--skip-desktop-install",
        action="store_true",
        help="Skip writing the desktop launcher file.",
    )
    parser.add_argument(
        "--no-restart",
        action="store_true",
        help="Do not attempt to restart IBus automatically when using --setup.",
    )
    args = parser.parse_args(argv)
    did_work: List[str] = []

    requested_setup = args.setup
    should_write_engine = not args.skip_engine_install
    should_write_desktop = (requested_setup or args.all) and not args.skip_desktop_install
    should_install_icon = requested_setup or args.all or args.icon is not None

    attempt_restart = requested_setup and not args.no_restart and (should_write_engine or should_write_desktop)

    component_path = None
    icon_install_path = None
    desktop_path = None

    if should_write_engine:
        component_path = write_user_component_file(args.engine_command, destination=args.output)

    resolved_icon_path = _resolve_icon_install_path(args.icon) or _default_icon_path()

    if should_install_icon and resolved_icon_path is not None:
        icon_install_path = _install_icon(resolved_icon_path)
    elif should_install_icon:
        print(f"Icon not found (looked for {args.icon or 'default locations'}); continuing without icon install.")

    if should_write_desktop:
        desktop_path = _write_desktop_file(
            icon_path=icon_install_path or resolved_icon_path,
            desktop_path=args.desktop_file,
            bar_command=args.bar_command,
        )

    restart_ok = None
    if attempt_restart:
        restart_ok, messages = _refresh_ibus(component_path)
        did_work.extend(messages)

    _print_setup_summary(component_path, icon_install_path, desktop_path, did_work, restart_ok=restart_ok)
    print("Next: select 'Gabbee Voice Input' in IBus when you want direct typing, then start gabbee-bar.")

    if args.setup and (not component_path):
        print("No component was written; run with --setup without --skip-engine-install to enable IBus integration.")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

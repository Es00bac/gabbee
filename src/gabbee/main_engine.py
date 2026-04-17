from __future__ import annotations

import os
import sys

from .app_paths import default_paths
from .ibus_component import COMPONENT_NAME, ENGINE_NAME, write_user_component_file


def _load_ibus_runtime():
    try:
        import gi
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "PyGObject / IBus bindings are missing. Install system packages such as `python3-gi` and `gir1.2-ibus-1.0`."
        ) from exc

    gi.require_version("IBus", "1.0")

    from gi.repository import GLib, GObject, IBus

    from .ibus_engine import (
        ActiveEngineRegistry,
        GabbeeEngineFactory,
        SocketBridge,
        build_component,
    )

    return GLib, GObject, IBus, ActiveEngineRegistry, GabbeeEngineFactory, SocketBridge, build_component


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    executable = os.path.abspath(sys.argv[0])

    if "--write-component" in argv:
        path = write_user_component_file(executable)
        print(path)
        return 0

    try:
        GLib, GObject, IBus, ActiveEngineRegistry, GabbeeEngineFactory, SocketBridge, build_component = (
            _load_ibus_runtime()
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    IBus.init()
    paths = default_paths()
    paths.ensure()

    loop = GLib.MainLoop()
    bus = IBus.Bus()
    registry = ActiveEngineRegistry()
    bridge = SocketBridge(registry=registry, socket_path=paths.engine_socket)
    bridge.start()

    def _quit(*_args) -> None:
        bridge.stop()
        loop.quit()

    bus.connect("disconnected", _quit)

    factory = GabbeeEngineFactory(bus, registry)
    factory.add_engine(ENGINE_NAME, GObject.type_from_name("GabbeeEngine"))

    if "--ibus" in argv:
        bus.request_name(COMPONENT_NAME, 0)
    else:
        bus.register_component(build_component(executable))

    try:
        loop.run()
    finally:
        bridge.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

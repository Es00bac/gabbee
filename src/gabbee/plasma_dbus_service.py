from __future__ import annotations

import json
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

from .config import load_config
from .controller import GabbeeController


class GabbeeDBusService(dbus.service.Object):
    def __init__(self, controller: GabbeeController) -> None:
        self.controller = controller
        bus_name = dbus.service.BusName(
            "com.gabbee.Controller", bus=dbus.SessionBus()
        )
        super().__init__(bus_name, "/com/gabbee/Controller")
        controller.add_listener(self._on_snapshot)

    @dbus.service.method("com.gabbee.Controller", in_signature="", out_signature="")
    def Start(self) -> None:
        self.controller.start()

    @dbus.service.method("com.gabbee.Controller", in_signature="", out_signature="")
    def Stop(self) -> None:
        self.controller.stop()

    @dbus.service.method("com.gabbee.Controller", in_signature="", out_signature="")
    def Cancel(self) -> None:
        self.controller.cancel()

    @dbus.service.method("com.gabbee.Controller", in_signature="", out_signature="")
    def Toggle(self) -> None:
        self.controller.toggle()

    @dbus.service.method("com.gabbee.Controller", in_signature="", out_signature="s")
    def GetState(self) -> str:
        return self.controller.snapshot().state.name

    @dbus.service.method("com.gabbee.Controller", in_signature="", out_signature="s")
    def GetProvider(self) -> str:
        return self.controller.snapshot().provider

    @dbus.service.method("com.gabbee.Controller", in_signature="", out_signature="s")
    def GetLastText(self) -> str:
        return self.controller.snapshot().last_text

    @dbus.service.method("com.gabbee.Controller", in_signature="", out_signature="s")
    def GetErrorMessage(self) -> str:
        return self.controller.snapshot().error_message

    @dbus.service.signal("com.gabbee.Controller", signature="ssss")
    def StateChanged(self, state: str, provider: str, last_text: str, error_message: str) -> None:
        pass

    def _on_snapshot(self, snapshot) -> None:
        def emit() -> None:
            self.StateChanged(
                snapshot.state.name,
                snapshot.provider,
                snapshot.last_text,
                snapshot.error_message,
            )

        GLib.idle_add(emit)


def _make_http_handler(controller: GabbeeController):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            # Suppress default HTTP logging
            pass

        def _send_json(self, data: dict, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())

        def do_GET(self) -> None:
            if self.path == "/state":
                snap = controller.snapshot()
                self._send_json({
                    "state": snap.state.name,
                    "provider": snap.provider,
                    "last_text": snap.last_text,
                    "error_message": snap.error_message,
                })
            else:
                self.send_error(404)

        def do_POST(self) -> None:
            if self.path == "/start":
                controller.start()
            elif self.path == "/stop":
                controller.stop()
            elif self.path == "/cancel":
                controller.cancel()
            elif self.path == "/toggle":
                controller.toggle()
            else:
                self.send_error(404)
                return
            self._send_json({"ok": True})

        def do_OPTIONS(self) -> None:
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.end_headers()

    return Handler


def _start_http_server(controller: GabbeeController, port: int = 28765) -> HTTPServer:
    print(f"DEBUG: Starting HTTP server on port {port}...")
    handler = _make_http_handler(controller)
    try:
        server = HTTPServer(("127.0.0.1", port), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        print(f"DEBUG: HTTP server thread started: {thread.name}")
        return server
    except Exception as e:
        print(f"DEBUG: Failed to start HTTP server: {e}")
        raise


def main(argv: list[str] | None = None) -> int:
    DBusGMainLoop(set_as_default=True)

    config = load_config()
    controller = GabbeeController(config)
    GabbeeDBusService(controller)

    http_server = _start_http_server(controller)

    loop = GLib.MainLoop()

    def _on_sigint(*_):
        http_server.shutdown()
        loop.quit()

    signal.signal(signal.SIGINT, _on_sigint)
    signal.signal(signal.SIGTERM, _on_sigint)

    loop.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

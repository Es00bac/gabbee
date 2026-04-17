from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import socketserver
import threading

import gi

gi.require_version("IBus", "1.0")

from gi.repository import GLib, GObject, IBus

from .ibus_component import COMPONENT_NAME, ENGINE_LONGNAME, ENGINE_NAME, ENGINE_OBJECT_PATH



class ActiveEngineRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._engine: GabbeeEngine | None = None

    def set_active(self, engine: "GabbeeEngine") -> None:
        with self._lock:
            self._engine = engine

    def clear(self, engine: "GabbeeEngine") -> None:
        with self._lock:
            if self._engine is engine:
                self._engine = None

    def has_active_engine(self) -> bool:
        with self._lock:
            return self._engine is not None

    def commit_text(self, text: str) -> bool:
        with self._lock:
            engine = self._engine
        if engine is None:
            return False
        GLib.idle_add(engine.commit_plain_text, text)
        return True


class GabbeeEngine(IBus.Engine):
    __gtype_name__ = "GabbeeEngine"

    def __init__(self, bus: IBus.Bus, object_path: str, registry: ActiveEngineRegistry) -> None:
        kwargs = {
            "connection": bus.get_connection(),
            "object_path": object_path,
        }
        if hasattr(IBus.Engine.props, "has_focus_id"):
            kwargs["has_focus_id"] = True
        super().__init__(**kwargs)
        self.registry = registry

    def commit_plain_text(self, text: str) -> bool:
        self.commit_text(IBus.Text.new_from_string(text))
        return False

    def do_enable(self) -> None:
        return None

    def do_disable(self) -> None:
        self.registry.clear(self)

    def do_focus_in(self) -> None:
        self.registry.set_active(self)

    def do_focus_in_id(self, object_path: str, client: str) -> None:
        self.registry.set_active(self)

    def do_focus_out(self) -> None:
        self.registry.clear(self)

    def do_focus_out_id(self, object_path: str, client: str) -> None:
        self.registry.clear(self)

    def do_process_key_event(self, keyval: int, keycode: int, state: int) -> bool:
        return False


class GabbeeEngineFactory(IBus.Factory):
    __gtype_name__ = "GabbeeEngineFactory"

    def __init__(self, bus: IBus.Bus, registry: ActiveEngineRegistry) -> None:
        super().__init__(object_path=IBus.PATH_FACTORY, connection=bus.get_connection())
        self._bus = bus
        self._registry = registry

    def do_create_engine(self, engine_name: str):
        if engine_name != ENGINE_NAME:
            return super().do_create_engine(engine_name)
        return GabbeeEngine(self._bus, ENGINE_OBJECT_PATH, self._registry)


def build_component(executable: str) -> IBus.Component:
    component = IBus.Component(
        name=COMPONENT_NAME,
        description="Gabbee voice input component",
        version="0.1.0",
        license="MIT",
        author="cabewse",
        homepage="https://local.gabbee",
        command_line=f"{executable} --ibus",
        textdomain="gabbee",
    )
    component.add_engine(
        IBus.EngineDesc(
            name=ENGINE_NAME,
            longname=ENGINE_LONGNAME,
            description="English voice input through IBus",
            language="en",
            license="MIT",
            author="cabewse",
            icon="gabbee",
            layout="default",
        )
    )
    return component


@dataclass
class SocketBridge:
    registry: ActiveEngineRegistry
    socket_path: Path

    def __post_init__(self) -> None:
        self._server: _UnixSocketServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        if self.socket_path.exists():
            self.socket_path.unlink()
        self._server = _UnixSocketServer(str(self.socket_path), _SocketHandler)
        self._server.bridge = self
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self.socket_path.exists():
            self.socket_path.unlink()

    def handle_request(self, body: dict[str, str]) -> dict[str, object]:
        action = body.get("action")
        if action == "ping":
            return {"ok": True, "detail": "pong"}
        if action == "commit_text":
            text = str(body.get("text", "")).strip()
            if not text:
                return {"ok": False, "detail": "No text to commit."}
            if not self.registry.has_active_engine():
                return {"ok": False, "detail": "No active Gabbee IBus engine is focused."}
            committed = self.registry.commit_text(text)
            return {"ok": committed, "detail": "Committed through IBus." if committed else "Commit failed."}
        return {"ok": False, "detail": f"Unknown action: {action}"}


class _UnixSocketServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


class _SocketHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        raw = self.rfile.readline().decode("utf-8").strip()
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            response = {"ok": False, "detail": "Invalid JSON request."}
        else:
            response = self.server.bridge.handle_request(body)
        self.wfile.write((json.dumps(response) + "\n").encode("utf-8"))

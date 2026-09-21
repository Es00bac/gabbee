from __future__ import annotations

from types import SimpleNamespace

from gabbee.control import CONTROL_PATH, CONTROL_SERVICE, GabbeeControlService


class Controller:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def start(self) -> None:
        self.calls.append("start")

    def start_command(self) -> None:
        self.calls.append("start-command")

    def stop(self) -> None:
        self.calls.append("stop")

    def cancel(self) -> None:
        self.calls.append("cancel")

    def snapshot(self):
        return SimpleNamespace(state=SimpleNamespace(value="recording"))


class Connection:
    def __init__(self, *, connected: bool = True, service_ok: bool = True, object_ok: bool = True) -> None:
        self.connected = connected
        self.service_ok = service_ok
        self.object_ok = object_ok
        self.registered_services: list[str] = []
        self.registered_objects: list[str] = []
        self.unregistered_services: list[str] = []
        self.unregistered_objects: list[str] = []

    def isConnected(self) -> bool:
        return self.connected

    def registerService(self, name: str) -> bool:
        self.registered_services.append(name)
        return self.service_ok

    def registerObject(self, path: str, _object, _options) -> bool:
        self.registered_objects.append(path)
        return self.object_ok

    def unregisterService(self, name: str) -> None:
        self.unregistered_services.append(name)

    def unregisterObject(self, path: str) -> None:
        self.unregistered_objects.append(path)


def test_control_service_dispatches_press_and_release_actions() -> None:
    controller = Controller()
    service = GabbeeControlService(controller, Connection())

    service.Start()
    service.Stop()
    service.StartCommand()
    service.Stop()
    service.Cancel()

    assert controller.calls == ["start", "stop", "start-command", "stop", "cancel"]
    assert service.GetState() == "recording"


def test_control_service_registers_and_cleans_up_session_bus_name() -> None:
    connection = Connection()
    service = GabbeeControlService(Controller(), connection)

    assert service.open()
    assert connection.registered_services == [CONTROL_SERVICE]
    assert connection.registered_objects == [CONTROL_PATH]

    service.close()
    assert connection.unregistered_objects == [CONTROL_PATH]
    assert connection.unregistered_services == [CONTROL_SERVICE]


def test_control_service_releases_name_when_object_registration_fails() -> None:
    connection = Connection(object_ok=False)
    service = GabbeeControlService(Controller(), connection)

    assert not service.open()
    assert connection.unregistered_services == [CONTROL_SERVICE]

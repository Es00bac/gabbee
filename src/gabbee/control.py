from __future__ import annotations

import argparse
import sys

from PyQt6.QtCore import QCoreApplication, QObject, pyqtClassInfo, pyqtSlot
from PyQt6.QtDBus import QDBus, QDBusConnection, QDBusMessage


CONTROL_SERVICE = "io.gabbee.Controller"
CONTROL_PATH = "/io/gabbee/Controller"
CONTROL_INTERFACE = "io.gabbee.Controller"


@pyqtClassInfo("D-Bus Interface", CONTROL_INTERFACE)
class GabbeeControlService(QObject):
    """Expose the running bar's controller to compositor key bindings."""

    def __init__(self, controller, connection=None) -> None:
        super().__init__()
        self.controller = controller
        self.connection = connection or QDBusConnection.sessionBus()
        self._open = False

    def open(self) -> bool:
        if self._open:
            return True
        if not self.connection.isConnected():
            return False
        if not self.connection.registerService(CONTROL_SERVICE):
            return False
        registered = self.connection.registerObject(
            CONTROL_PATH,
            self,
            QDBusConnection.RegisterOption.ExportAllSlots,
        )
        if not registered:
            self.connection.unregisterService(CONTROL_SERVICE)
            return False
        self._open = True
        return True

    def close(self) -> None:
        if not self._open:
            return
        self.connection.unregisterObject(CONTROL_PATH)
        self.connection.unregisterService(CONTROL_SERVICE)
        self._open = False

    @pyqtSlot()
    def Start(self) -> None:
        self.controller.start()

    @pyqtSlot()
    def StartCommand(self) -> None:
        self.controller.start_command()

    @pyqtSlot()
    def Stop(self) -> None:
        self.controller.stop()

    @pyqtSlot()
    def Cancel(self) -> None:
        self.controller.cancel()

    @pyqtSlot(result=str)
    def GetState(self) -> str:
        state = self.controller.snapshot().state
        return getattr(state, "value", getattr(state, "name", str(state)))


def _call_method(method: str, *, timeout_ms: int = 2_000) -> tuple[bool, str]:
    connection = QDBusConnection.sessionBus()
    if not connection.isConnected():
        return False, "The desktop session bus is unavailable."
    message = QDBusMessage.createMethodCall(
        CONTROL_SERVICE,
        CONTROL_PATH,
        CONTROL_INTERFACE,
        method,
    )
    reply = connection.call(message, QDBus.CallMode.Block, timeout_ms)
    if reply.type() == QDBusMessage.MessageType.ErrorMessage:
        detail = reply.errorMessage() or "Gabbee is not running."
        return False, detail
    arguments = reply.arguments()
    return True, str(arguments[0]) if arguments else ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gabbee-control",
        description="Control the Gabbee bar in the current desktop session.",
    )
    parser.add_argument(
        "action",
        choices=("start", "start-command", "stop", "cancel", "status"),
    )
    args = parser.parse_args(argv)
    QCoreApplication.instance() or QCoreApplication(["gabbee-control"])
    methods: dict[str, str] = {
        "start": "Start",
        "start-command": "StartCommand",
        "stop": "Stop",
        "cancel": "Cancel",
        "status": "GetState",
    }
    ok, detail = _call_method(methods[args.action])
    if detail:
        print(detail, file=sys.stdout if ok else sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

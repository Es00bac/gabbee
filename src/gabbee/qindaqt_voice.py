"""org.qindaqt.Voice1 provider: Gabbee as the QindaQt desktop's voice input.

The QindaQt desktop owns this interface and specifies it in
``docs/wiki/architecture/voice-input.md`` in the QindaQt repository. Gabbee is
one implementation of it; the desktop's panel applet, its Settings page and its
Voice console all speak only this contract and never reach into Gabbee.

The wire constants, bounds and value mapping live in :mod:`qindaqt_voice_wire`;
this module is the live service object. Contract summary, so this file can be
maintained without the other repository:

* Payloads are ``a{sv}`` maps, never D-Bus structures, so a provider can be
  written in any language. Every key is mandatory and its type is checked on
  the consumer side; an unexpected key makes the payload invalid.
* ``revision`` is the only ordering authority. It advances when, and only
  when, a caller-visible field changed, and never goes backwards while this
  process owns the name.
* Every mutating method takes ``(requestId, expectedRevision)`` and answers a
  result map for that exact request. A caller that loses the answer must treat
  the request as uncertain and must not replay it.
* ``Level`` is unrevisioned, lossy capture telemetry. It never gates anything.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from PyQt6.QtCore import QObject, pyqtClassInfo, pyqtSignal, pyqtSlot
from PyQt6.QtDBus import QDBusConnection

from .qindaqt_voice_wire import (
    CAP_COMMAND_MODE,
    CAP_COPY,
    CAP_PROVIDER_SELECTION,
    CAP_REALTIME_PARTIALS,
    CAP_RETRY,
    CAP_UNDO,
    INTERFACE_NAME,
    KIND_CANCEL,
    KIND_COPY_LAST,
    KIND_FINISH,
    KIND_RETRY,
    KIND_SET_ENABLED,
    KIND_SET_PROVIDER,
    KIND_START_COMMAND,
    KIND_START_DICTATION,
    KIND_UNDO,
    MAX_LABEL_UTF8_BYTES,
    MAX_REASON_UTF8_BYTES,
    MAX_SHORTCUT_UTF8_BYTES,
    MODE_COMMAND,
    MODE_DICTATION,
    OBJECT_PATH,
    ROUTE_NONE,
    SCHEMA_VERSION,
    SERVICE_NAME,
    STATE_ERROR,
    STATE_UNKNOWN,
    STATUS_FAILED,
    STATUS_REJECTED,
    STATUS_SUCCEEDED,
    _CAPTURING_STATES,
    _ROUTE_MAP,
    _STATE_MAP,
    _display_text,
    _identifier,
    _transcript_text,
    _truncate_utf8,
    build_provider_inventory,
    classify_error,
)

class _SnapshotRelay(QObject):
    """Main-thread hop for everything the controller reports.

    AGENT-GUARD: this deliberately lives outside QindaQtVoiceService. That
    object is registered with ExportAllSlots|ExportAllSignals, so a
    ``pyqtSignal(object)`` declared on it is offered on the bus, rejected as an
    unregistered ``PyQt_PyObject``, and logged on every registration. Only the
    two contract signals may live on the exported class.

    AGENT-GUARD: both hops matter. Controller snapshots arrive on worker
    threads and capture levels arrive on the PCM reader thread; emitting a
    D-Bus-exported signal from either would put the session bus in the capture
    path on a thread Qt did not set it up on.
    """

    arrived = pyqtSignal(object)
    levelled = pyqtSignal(int)


@pyqtClassInfo("D-Bus Interface", INTERFACE_NAME)
class QindaQtVoiceService(QObject):
    """Publish the running Gabbee controller as org.qindaqt.Voice1.

    Threading: every method here runs on the Qt main thread. The controller
    notifies listeners from its worker threads, so ``_on_snapshot`` is routed
    through a queued signal before it touches any state this object publishes.

    Lifetime: the controller is borrowed and must outlive this object.
    """

    # Wire signals. Names and signatures are the contract; see the module
    # docstring. `Changed` carries the new revision so a consumer holding that
    # revision can skip the round trip.
    Changed = pyqtSignal("qulonglong")
    Level = pyqtSignal("uint")

    def __init__(self, controller, config, connection: QDBusConnection | None = None) -> None:
        super().__init__()
        self.controller = controller
        self.config = config
        self.connection = connection or QDBusConnection.sessionBus()
        self._open = False
        self._revision = 1
        self._payload: dict[str, Any] = {}
        self._last_level_emit = 0.0
        self._level_interval = 0.08
        self._relay = _SnapshotRelay()
        self._relay.arrived.connect(self._apply_snapshot)
        self._relay.levelled.connect(self._publish_level)
        self._payload = self._compose(controller.snapshot())

    # -- lifecycle ---------------------------------------------------------

    def open(self) -> bool:
        if self._open:
            return True
        if not self.connection.isConnected():
            return False
        if not self.connection.registerService(SERVICE_NAME):
            return False
        options = (
            QDBusConnection.RegisterOption.ExportAllSlots
            | QDBusConnection.RegisterOption.ExportAllSignals
        )
        if not self.connection.registerObject(OBJECT_PATH, self, options):
            self.connection.unregisterService(SERVICE_NAME)
            return False
        self._open = True
        self.controller.add_listener(self._on_snapshot)
        recorder = getattr(self.controller, "recorder", None)
        if recorder is not None and hasattr(recorder, "level_observer"):
            recorder.level_observer = self._on_level
        return True

    def close(self) -> None:
        if not self._open:
            return
        recorder = getattr(self.controller, "recorder", None)
        if recorder is not None and getattr(recorder, "level_observer", None) is self._on_level:
            recorder.level_observer = None
        self.connection.unregisterObject(OBJECT_PATH)
        self.connection.unregisterService(SERVICE_NAME)
        self._open = False

    # -- projection --------------------------------------------------------

    def _on_snapshot(self, snapshot) -> None:
        # Called from controller worker threads. Hand it to the main thread.
        self._relay.arrived.emit(snapshot)

    def _apply_snapshot(self, snapshot) -> None:
        payload = self._compose(snapshot)
        if payload == self._payload:
            return
        self._payload = payload
        self._revision += 1
        self._payload["revision"] = self._revision
        self.Changed.emit(self._revision)

    def _on_level(self, percent: int) -> None:
        """Called on the PCM reader thread for every captured chunk.

        Lossy by design: dropping a frame costs nothing, and a signal per chunk
        would put the session bus in the capture path. The rate limit is
        applied here so the discarded frames never cross a thread at all.
        """

        now = time.monotonic()
        if now - self._last_level_emit < self._level_interval:
            return
        self._last_level_emit = now
        self._relay.levelled.emit(max(0, min(100, int(percent))))

    def _publish_level(self, percent: int) -> None:
        self.Level.emit(percent)

    def _capabilities(self) -> int:
        capabilities = (
            CAP_COMMAND_MODE | CAP_RETRY | CAP_UNDO | CAP_COPY | CAP_PROVIDER_SELECTION
        )
        if (
            getattr(self.config, "stt_provider", "") == "elevenlabs"
            and getattr(self.config, "elevenlabs_realtime_enabled", False)
        ):
            capabilities |= CAP_REALTIME_PARTIALS
        return capabilities

    def _compose(self, snapshot) -> dict[str, Any]:
        state = _STATE_MAP.get(snapshot.state, STATE_UNKNOWN)
        capturing = state in _CAPTURING_STATES
        route = ROUTE_NONE
        method = str(getattr(snapshot, "delivery_method", "") or "")
        for part in method.split("+"):
            mapped = _ROUTE_MAP.get(part.strip().lower())
            if mapped is not None:
                route = mapped
                break

        if state == STATE_ERROR:
            reason = classify_error(getattr(snapshot, "error_message", ""))
        else:
            reason = "ok"

        providers = build_provider_inventory(self.config)
        mask = 0
        for index, entry in enumerate(providers):
            if entry[2]:
                mask |= 1 << index

        return {
            "schemaVersion": SCHEMA_VERSION,
            "revision": self._revision,
            "state": state,
            "mode": MODE_COMMAND if getattr(snapshot, "command_mode", False) else MODE_DICTATION,
            "enabled": bool(getattr(self.controller, "input_enabled", True)),
            "capabilities": self._capabilities(),
            "lastRoute": route,
            "providerId": _identifier(getattr(self.config, "stt_provider", ""), "unknown"),
            "providerLabel": _display_text(
                next(
                    (label for pid, label, _ in providers
                     if pid == getattr(self.config, "stt_provider", "")),
                    getattr(snapshot, "provider", ""),
                ),
                MAX_LABEL_UTF8_BYTES,
            ),
            "languageCode": _identifier(getattr(self.config, "language_code", "en"), "en"),
            "microphoneLabel": _display_text(
                getattr(self.config, "audio_source", None) or "System default",
                MAX_LABEL_UTF8_BYTES,
            ),
            "dictationShortcut": _display_text(
                getattr(self.config, "toggle_shortcut", ""), MAX_SHORTCUT_UTF8_BYTES
            ),
            "commandShortcut": _display_text(
                getattr(self.config, "command_shortcut", ""), MAX_SHORTCUT_UTF8_BYTES
            ),
            # AGENT-GUARD: a partial outside capture is rejected by the
            # desktop as a stale fragment, and rightly so: it would show text
            # the user already replaced or discarded.
            "partialText": _transcript_text(snapshot.partial_text) if capturing else "",
            "lastText": _transcript_text(getattr(snapshot, "last_text", "")),
            "reasonCode": _truncate_utf8(reason, MAX_REASON_UTF8_BYTES),
            "providerIds": [entry[0] for entry in providers],
            "providerLabels": [
                _display_text(entry[1], MAX_LABEL_UTF8_BYTES) for entry in providers
            ],
            "providerAvailableMask": mask,
        }

    # -- request handling --------------------------------------------------

    def _result(
        self,
        kind: int,
        status: int,
        request_id: int,
        initiating_revision: int,
        reason: str,
    ) -> dict[str, Any]:
        return {
            "kind": kind,
            "status": status,
            "requestId": request_id,
            "initiatingRevision": initiating_revision,
            # Never below the initiating revision: the consumer treats a
            # regression as a different lineage and discards the answer.
            "observedRevision": max(self._revision, initiating_revision),
            "reasonCode": _truncate_utf8(reason, MAX_REASON_UTF8_BYTES),
        }

    def _admit(self, kind: int, request_id: int, expected_revision: int):
        """Shared admission. Returns a result map to answer with, or None."""

        if request_id == 0 or expected_revision == 0:
            return self._result(kind, STATUS_REJECTED, request_id,
                                max(expected_revision, 1), "malformed-request")
        if expected_revision != self._revision:
            # The caller decided against a projection we have already
            # replaced. Refusing is what keeps a stale button from acting on
            # state the user can no longer see.
            return self._result(kind, STATUS_REJECTED, request_id,
                                expected_revision, "revision-stale")
        return None

    def _capturing(self) -> bool:
        return _STATE_MAP.get(self.controller.snapshot().state, STATE_UNKNOWN) in _CAPTURING_STATES

    def _run(self, kind: int, request_id: int, expected_revision: int,
             action: Callable[[], str | None]) -> dict[str, Any]:
        refusal = self._admit(kind, request_id, expected_revision)
        if refusal is not None:
            return refusal
        try:
            reason = action()
        except Exception as exc:  # noqa: BLE001 - reported, never swallowed
            return self._result(kind, STATUS_FAILED, request_id, expected_revision,
                                classify_error(str(exc)))
        if reason is not None:
            return self._result(kind, STATUS_REJECTED, request_id, expected_revision,
                                reason)
        return self._result(kind, STATUS_SUCCEEDED, request_id, expected_revision, "ok")

    @pyqtSlot(result="QVariantMap")
    def GetSnapshot(self) -> dict[str, Any]:
        return dict(self._payload)

    @pyqtSlot("qulonglong", "qulonglong", result="QVariantMap")
    def StartDictation(self, requestId: int, expectedRevision: int) -> dict[str, Any]:
        def action() -> str | None:
            if not getattr(self.controller, "input_enabled", True):
                return "input-disabled"
            if self._capturing():
                return "already-capturing"
            self.controller.start()
            return None

        return self._run(KIND_START_DICTATION, requestId, expectedRevision, action)

    @pyqtSlot("qulonglong", "qulonglong", result="QVariantMap")
    def StartCommand(self, requestId: int, expectedRevision: int) -> dict[str, Any]:
        def action() -> str | None:
            if not getattr(self.controller, "input_enabled", True):
                return "input-disabled"
            if self._capturing():
                return "already-capturing"
            self.controller.start_command()
            return None

        return self._run(KIND_START_COMMAND, requestId, expectedRevision, action)

    @pyqtSlot("qulonglong", "qulonglong", result="QVariantMap")
    def Finish(self, requestId: int, expectedRevision: int) -> dict[str, Any]:
        def action() -> str | None:
            if not self._capturing():
                return "not-capturing"
            self.controller.stop()
            return None

        return self._run(KIND_FINISH, requestId, expectedRevision, action)

    @pyqtSlot("qulonglong", "qulonglong", result="QVariantMap")
    def Cancel(self, requestId: int, expectedRevision: int) -> dict[str, Any]:
        def action() -> str | None:
            if not self._capturing():
                return "not-capturing"
            self.controller.cancel()
            return None

        return self._run(KIND_CANCEL, requestId, expectedRevision, action)

    @pyqtSlot("qulonglong", "qulonglong", result="QVariantMap")
    def Retry(self, requestId: int, expectedRevision: int) -> dict[str, Any]:
        def action() -> str | None:
            return None if self.controller.retry_last() else "nothing-to-retry"

        return self._run(KIND_RETRY, requestId, expectedRevision, action)

    @pyqtSlot("qulonglong", "qulonglong", result="QVariantMap")
    def Undo(self, requestId: int, expectedRevision: int) -> dict[str, Any]:
        def action() -> str | None:
            result = self.controller.undo_last()
            return None if getattr(result, "ok", False) else "undo-failed"

        return self._run(KIND_UNDO, requestId, expectedRevision, action)

    @pyqtSlot("qulonglong", "qulonglong", result="QVariantMap")
    def CopyLast(self, requestId: int, expectedRevision: int) -> dict[str, Any]:
        def action() -> str | None:
            result = self.controller.copy_formatted()
            return None if getattr(result, "ok", False) else "copy-failed"

        return self._run(KIND_COPY_LAST, requestId, expectedRevision, action)

    @pyqtSlot("qulonglong", "qulonglong", "QString", result="QVariantMap")
    def SetProvider(self, requestId: int, expectedRevision: int,
                    providerId: str) -> dict[str, Any]:
        def action() -> str | None:
            candidate = _identifier(providerId, "")
            if not candidate:
                return "malformed-provider"
            known = {entry[0] for entry in build_provider_inventory(self.config)}
            if candidate not in known:
                return "unknown-provider"
            if self._capturing():
                return "already-capturing"
            # Persisted, then reloaded, so the choice survives a restart and
            # the next dictation actually uses it.
            self.config.save({"GABBEE_STT_PROVIDER": candidate})
            self.config.stt_provider = candidate
            self.controller.reload_transcriber()
            return None

        return self._run(KIND_SET_PROVIDER, requestId, expectedRevision, action)

    @pyqtSlot("qulonglong", "qulonglong", "bool", result="QVariantMap")
    def SetEnabled(self, requestId: int, expectedRevision: int,
                   enabled: bool) -> dict[str, Any]:
        def action() -> str | None:
            setter = getattr(self.controller, "set_input_enabled", None)
            if setter is None:
                return "unsupported-capability"
            if not enabled and self._capturing():
                self.controller.cancel()
            setter(bool(enabled))
            return None

        return self._run(KIND_SET_ENABLED, requestId, expectedRevision, action)

"""org.qindaqt.Voice1 provider contract.

These rows cover the half of the contract Gabbee owns: the bounds it must not
exceed, the revision rule the desktop orders everything by, and the admission
rules that decide what a request is allowed to do. The wire decode on the
other side is covered by QindaQt's own tests.
"""

from __future__ import annotations

import pytest

from gabbee.models import ControllerSnapshot, ControllerState
from gabbee.qindaqt_voice import QindaQtVoiceService
from gabbee.qindaqt_voice_wire import (
    CAP_PROVIDER_SELECTION,
    CAP_REALTIME_PARTIALS,
    MAX_TRANSCRIPT_UTF8_BYTES,
    MODE_COMMAND,
    ROUTE_CLIPBOARD,
    ROUTE_INPUT_METHOD,
    ROUTE_KEY_SYNTHESIS,
    ROUTE_NONE,
    SCHEMA_VERSION,
    STATE_ERROR,
    STATE_IDLE,
    STATE_LISTENING,
    STATUS_REJECTED,
    STATUS_SUCCEEDED,
    build_provider_inventory,
    classify_error,
)


class FakeConfig:
    def __init__(self, **overrides):
        self.stt_provider = "elevenlabs"
        self.language_code = "en"
        self.audio_source = None
        self.toggle_shortcut = "F5"
        self.command_shortcut = "F6"
        self.elevenlabs_realtime_enabled = True
        self.secret_api_key = "key"
        self.gemini_api_key = None
        self.env_values = {}
        self.saved = {}
        for key, value in overrides.items():
            setattr(self, key, value)

    def save(self, updates):
        # Mirrors AppConfig.save(): it writes the attribute, not the caller.
        self.saved.update(updates)
        if "GABBEE_STT_PROVIDER" in updates:
            self.stt_provider = updates["GABBEE_STT_PROVIDER"]


class FakeRecorder:
    def __init__(self):
        self.level_observer = None


class FakeController:
    def __init__(self, state=ControllerState.IDLE, **snapshot_overrides):
        self.input_enabled = True
        self.recorder = FakeRecorder()
        self.calls = []
        self._state = state
        self._overrides = snapshot_overrides
        self.reload_count = 0

    def snapshot(self):
        fields = dict(
            state=self._state,
            provider="elevenlabs",
            delivery_method="",
            last_text="",
            error_message="",
            partial_text="",
            command_mode=False,
        )
        fields.update(self._overrides)
        return ControllerSnapshot(**fields)

    def set_state(self, state, **overrides):
        self._state = state
        self._overrides.update(overrides)

    def add_listener(self, listener):
        self.calls.append(("add_listener",))

    def start(self):
        self.calls.append(("start",))

    def start_command(self):
        self.calls.append(("start_command",))

    def stop(self):
        self.calls.append(("stop",))

    def cancel(self):
        self.calls.append(("cancel",))

    def retry_last(self):
        self.calls.append(("retry_last",))
        return True

    def reload_transcriber(self):
        self.reload_count += 1

    def set_input_enabled(self, enabled):
        self.input_enabled = bool(enabled)


@pytest.fixture
def service():
    controller = FakeController()
    config = FakeConfig()
    instance = QindaQtVoiceService(controller, config, connection=None)
    instance.controller = controller
    instance.config = config
    return instance


def test_snapshot_carries_every_contract_key(service):
    payload = service.GetSnapshot()
    assert payload["schemaVersion"] == SCHEMA_VERSION
    assert payload["revision"] >= 1
    assert payload["state"] == STATE_IDLE
    assert set(payload) == {
        "schemaVersion", "revision", "state", "mode", "enabled", "capabilities",
        "lastRoute", "providerId", "providerLabel", "languageCode",
        "microphoneLabel", "dictationShortcut", "commandShortcut", "partialText",
        "lastText", "reasonCode", "providerIds", "providerLabels",
        "providerAvailableMask",
    }


def test_reason_code_is_structured_and_ok_outside_error(service):
    assert service.GetSnapshot()["reasonCode"] == "ok"
    service.controller.set_state(
        ControllerState.ERROR, error_message="ElevenLabs rejected the API key (401)"
    )
    service._apply_snapshot(service.controller.snapshot())
    payload = service.GetSnapshot()
    assert payload["state"] == STATE_ERROR
    assert payload["reasonCode"] == "authentication-failed"


@pytest.mark.parametrize(
    "message,expected",
    [
        ("Connection to wss://api timed out", "connection-failed"),
        ("Monthly quota exceeded", "quota-exceeded"),
        ("pw-record is required but was not found", "microphone-unavailable"),
        ("Could not insert text through ibus", "delivery-failed"),
        ("something nobody anticipated", "provider-error"),
        ("", "provider-error"),
    ],
)
def test_error_taxonomy(message, expected):
    assert classify_error(message) == expected


def test_partial_text_is_dropped_outside_capture(service):
    service.controller.set_state(ControllerState.RECORDING, partial_text="hello there")
    service._apply_snapshot(service.controller.snapshot())
    assert service.GetSnapshot()["partialText"] == "hello there"
    assert service.GetSnapshot()["state"] == STATE_LISTENING

    service.controller.set_state(ControllerState.IDLE, partial_text="hello there")
    service._apply_snapshot(service.controller.snapshot())
    # A partial that outlived its capture would show text the user already
    # replaced; the desktop rejects such a snapshot outright.
    assert service.GetSnapshot()["partialText"] == ""


def test_transcript_is_bounded_and_never_splits_a_character(service):
    long_text = "é" * 600
    service.controller.set_state(ControllerState.IDLE, last_text=long_text)
    service._apply_snapshot(service.controller.snapshot())
    last = service.GetSnapshot()["lastText"]
    assert len(last.encode("utf-8")) <= MAX_TRANSCRIPT_UTF8_BYTES
    assert last == "é" * (MAX_TRANSCRIPT_UTF8_BYTES // 2)


def test_control_characters_are_stripped_but_spoken_layout_survives(service):
    service.controller.set_state(
        ControllerState.IDLE, last_text="first\n\tsecond\x1b[31mred\x00"
    )
    service._apply_snapshot(service.controller.snapshot())
    assert service.GetSnapshot()["lastText"] == "first\n\tsecond[31mred"


@pytest.mark.parametrize(
    "method,expected",
    [
        ("ibus", ROUTE_INPUT_METHOD),
        ("clipboard", ROUTE_CLIPBOARD),
        ("dotool", ROUTE_KEY_SYNTHESIS),
        ("agent-input", ROUTE_KEY_SYNTHESIS),
        ("dotool+clipboard", ROUTE_KEY_SYNTHESIS),
        ("desktop-action", ROUTE_NONE),
    ],
)
def test_delivery_route_mapping(service, method, expected):
    service.controller.set_state(ControllerState.IDLE, delivery_method=method)
    service._apply_snapshot(service.controller.snapshot())
    assert service.GetSnapshot()["lastRoute"] == expected


def test_revision_advances_only_on_a_caller_visible_change(service):
    first = service.GetSnapshot()["revision"]
    service._apply_snapshot(service.controller.snapshot())
    assert service.GetSnapshot()["revision"] == first

    service.controller.set_state(ControllerState.RECORDING)
    service._apply_snapshot(service.controller.snapshot())
    second = service.GetSnapshot()["revision"]
    assert second > first

    service._apply_snapshot(service.controller.snapshot())
    assert service.GetSnapshot()["revision"] == second


def test_command_mode_is_projected(service):
    service.controller.set_state(ControllerState.RECORDING, command_mode=True)
    service._apply_snapshot(service.controller.snapshot())
    assert service.GetSnapshot()["mode"] == MODE_COMMAND


def test_realtime_capability_follows_the_configured_provider():
    controller = FakeController()
    realtime = QindaQtVoiceService(controller, FakeConfig(), connection=None)
    assert realtime.GetSnapshot()["capabilities"] & CAP_REALTIME_PARTIALS

    batch = QindaQtVoiceService(
        FakeController(), FakeConfig(stt_provider="whisper_local"), connection=None
    )
    assert not batch.GetSnapshot()["capabilities"] & CAP_REALTIME_PARTIALS
    assert batch.GetSnapshot()["capabilities"] & CAP_PROVIDER_SELECTION


def test_provider_inventory_always_lists_the_current_provider():
    inventory = build_provider_inventory(FakeConfig(stt_provider="something_else"))
    assert any(entry[0] == "something_else" for entry in inventory)
    ids = [entry[0] for entry in inventory]
    assert ids[:3] == ["elevenlabs", "gemini", "whisper_local"]


def test_available_mask_matches_the_listed_order():
    config = FakeConfig(secret_api_key=None, env_values={}, gemini_api_key="g")
    service = QindaQtVoiceService(FakeController(), config, connection=None)
    payload = service.GetSnapshot()
    ids = payload["providerIds"]
    mask = payload["providerAvailableMask"]
    assert not mask & (1 << ids.index("elevenlabs"))
    assert mask & (1 << ids.index("gemini"))


def test_a_stale_revision_is_refused_without_touching_the_controller(service):
    result = service.StartDictation(1, 999)
    assert result["status"] == STATUS_REJECTED
    assert result["reasonCode"] == "revision-stale"
    assert ("start",) not in service.controller.calls


def test_a_malformed_request_is_refused(service):
    result = service.StartDictation(0, service.GetSnapshot()["revision"])
    assert result["status"] == STATUS_REJECTED
    assert result["reasonCode"] == "malformed-request"


def test_start_dictation_reaches_the_controller(service):
    revision = service.GetSnapshot()["revision"]
    result = service.StartDictation(7, revision)
    assert result["status"] == STATUS_SUCCEEDED
    assert result["requestId"] == 7
    assert result["initiatingRevision"] == revision
    assert result["observedRevision"] >= revision
    assert ("start",) in service.controller.calls


def test_disarmed_input_refuses_to_open_the_microphone(service):
    service.controller.input_enabled = False
    service._apply_snapshot(service.controller.snapshot())
    result = service.StartDictation(3, service.GetSnapshot()["revision"])
    assert result["status"] == STATUS_REJECTED
    assert result["reasonCode"] == "input-disabled"
    assert ("start",) not in service.controller.calls


def test_a_second_recording_is_refused_while_one_is_live(service):
    service.controller.set_state(ControllerState.RECORDING)
    service._apply_snapshot(service.controller.snapshot())
    result = service.StartDictation(4, service.GetSnapshot()["revision"])
    assert result["status"] == STATUS_REJECTED
    assert result["reasonCode"] == "already-capturing"


def test_finish_is_refused_when_nothing_is_recording(service):
    result = service.Finish(5, service.GetSnapshot()["revision"])
    assert result["status"] == STATUS_REJECTED
    assert result["reasonCode"] == "not-capturing"
    assert ("stop",) not in service.controller.calls


def test_set_provider_persists_and_reloads(service):
    revision = service.GetSnapshot()["revision"]
    result = service.SetProvider(9, revision, "whisper_local")
    assert result["status"] == STATUS_SUCCEEDED
    assert service.config.saved == {"GABBEE_STT_PROVIDER": "whisper_local"}
    assert service.config.stt_provider == "whisper_local"
    assert service.controller.reload_count == 1
    # The next snapshot reports the provider the user actually chose.
    service._apply_snapshot(service.controller.snapshot())
    assert service.GetSnapshot()["providerId"] == "whisper_local"


def test_set_provider_refuses_an_unknown_identifier(service):
    result = service.SetProvider(10, service.GetSnapshot()["revision"], "not-a-provider")
    assert result["status"] == STATUS_REJECTED
    assert result["reasonCode"] == "unknown-provider"
    assert service.config.saved == {}


def test_set_enabled_disarms_and_cancels_a_live_recording(service):
    service.controller.set_state(ControllerState.RECORDING)
    service._apply_snapshot(service.controller.snapshot())
    result = service.SetEnabled(11, service.GetSnapshot()["revision"], False)
    assert result["status"] == STATUS_SUCCEEDED
    assert service.controller.input_enabled is False
    assert ("cancel",) in service.controller.calls


def test_level_reports_are_rate_limited_and_clamped(service):
    # _on_level runs on the PCM reader thread and hands the value to the relay;
    # the relay is what reaches the exported D-Bus signal.
    relayed = []
    service._relay.levelled.connect(relayed.append)
    service._on_level(250)
    service._on_level(10)
    assert relayed == [100]
    service._last_level_emit = 0.0
    service._on_level(-5)
    assert relayed == [100, 0]


def test_level_reaches_the_exported_signal_through_the_relay(service):
    emitted = []
    service.Level.connect(emitted.append)
    service._publish_level(42)
    assert emitted == [42]


def test_the_exported_class_declares_only_the_two_contract_signals(service):
    # AGENT-GUARD: anything else on this class is offered on the bus. A
    # pyqtSignal(object) here is rejected by Qt as an unregistered type and
    # logged on every registration.
    meta = type(service).staticMetaObject
    signals = {
        bytes(meta.method(i).methodSignature()).decode().split("(")[0]
        for i in range(meta.methodOffset(), meta.methodCount())
        if meta.method(i).methodType() == meta.method(i).methodType().Signal
    }
    assert signals == {"Changed", "Level"}

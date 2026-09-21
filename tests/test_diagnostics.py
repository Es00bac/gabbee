from __future__ import annotations

import json

import pytest

from gabbee.diagnostics import DiagnosticsLog, build_redacted_report
from test_elevenlabs_realtime import make_config


def test_diagnostics_reject_content_categories_and_redacts_secrets(tmp_path) -> None:
    log = DiagnosticsLog(tmp_path / "diagnostics.jsonl")
    with pytest.raises(ValueError):
        log.record("transcript", success=True, detail="private words")
    log.record(
        "provider_connection",
        success=False,
        failure_category="auth_error",
        detail="api_key=super-secret",
    )
    event = log.recent()[0]
    assert "super-secret" not in json.dumps(event)
    assert "[REDACTED]" in event["detail"]


def test_redacted_report_omits_credential_and_transcript_values(tmp_path) -> None:
    config = make_config(tmp_path)
    report = build_redacted_report(config, DiagnosticsLog(tmp_path / "missing.jsonl"))
    encoded = json.dumps(report)
    assert "secret" not in encoded
    assert report["provider"]["credential_present"] is True
    assert report["privacy"] == "Audio and transcript contents are excluded by default."

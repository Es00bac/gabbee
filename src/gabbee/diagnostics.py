from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import re
import shutil
import threading
from typing import Any

from .config import AppConfig


_SECRET_RE = re.compile(r"(?i)(api[_ -]?key|token|secret|authorization)\s*[:=]\s*\S+")


@dataclass(slots=True, frozen=True)
class DiagnosticEvent:
    timestamp: str
    category: str
    success: bool
    duration_ms: int | None = None
    failure_category: str = ""
    detail: str = ""


class DiagnosticsLog:
    """Metadata-only diagnostics; transcript and audio contents are rejected."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def record(
        self,
        category: str,
        *,
        success: bool,
        duration_ms: int | None = None,
        failure_category: str = "",
        detail: str = "",
    ) -> None:
        if category.casefold() in {"audio", "transcript", "raw_text", "formatted_text"}:
            raise ValueError("Audio and transcript contents are not accepted by diagnostics.")
        event = DiagnosticEvent(
            timestamp=datetime.now(timezone.utc).isoformat(),
            category=category,
            success=success,
            duration_ms=duration_ms,
            failure_category=failure_category,
            detail=_redact(detail)[:500],
        )
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(asdict(event), sort_keys=True) + "\n")

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            if not self.path.exists():
                return []
            lines = self.path.read_text(encoding="utf-8", errors="replace").splitlines()[-max(0, limit):]
        result: list[dict[str, Any]] = []
        for line in lines:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                result.append(item)
        return result


def build_redacted_report(config: AppConfig, log: DiagnosticsLog | None = None) -> dict[str, Any]:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "python": platform.python_version(),
        },
        "provider": {
            "name": config.stt_provider,
            "language": config.language_code,
            "elevenlabs_batch_model": config.elevenlabs_model_id,
            "elevenlabs_realtime_model": config.elevenlabs_realtime_model_id,
            "realtime_enabled": config.elevenlabs_realtime_enabled,
            "keyterms_metered_enabled": config.elevenlabs_keyterms_enabled,
            "entity_detection_metered": list(config.elevenlabs_entity_detection),
            "credential_present": bool(config.elevenlabs_api_key()),
        },
        "desktop": {
            "session_type": _safe_env("XDG_SESSION_TYPE"),
            "desktop": _safe_env("XDG_CURRENT_DESKTOP"),
            "wayland_display_present": bool(_safe_env("WAYLAND_DISPLAY")),
        },
        "commands": {
            name: bool(shutil.which(name))
            for name in ("pw-record", "dotool", "wl-copy", "wl-paste", "qdbus6", "ibus")
        },
        "paths": {
            "config_dir": str(config.paths.config_dir),
            "state_dir": str(config.paths.state_dir),
            "cache_dir": str(config.paths.cache_dir),
            # Deliberately omit the env-file path: it may identify a private
            # workspace or home layout.
        },
        "events": log.recent() if log else [],
        "privacy": "Audio and transcript contents are excluded by default.",
    }


def export_redacted_report(path: Path, config: AppConfig, log: DiagnosticsLog | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(build_redacted_report(config, log), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _redact(value: str) -> str:
    return _SECRET_RE.sub(lambda matched: matched.group(1) + "=[REDACTED]", value)


def _safe_env(name: str) -> str:
    import os

    return os.environ.get(name, "")[:100]

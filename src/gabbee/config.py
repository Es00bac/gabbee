from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import os

from dotenv import dotenv_values, load_dotenv

from .app_paths import AppPaths, default_paths


@dataclass(slots=True)
class AppConfig:
    paths: AppPaths
    env_file: Path
    env_values: dict[str, str] = field(repr=False)
    stt_provider: str
    language_code: str
    elevenlabs_model_id: str
    elevenlabs_base_url: str
    audio_source: str | None
    sample_rate: int
    fallback_sink: str
    ui_title: str
    toggle_shortcut: str = "F5"
    whisper_local_model: str = "tiny"
    whisper_local_device: str = "cpu"
    whisper_local_compute_type: str = "default"
    keyword_map: dict[str, str] = field(default_factory=dict)

    def env(self, name: str, default: str | None = None) -> str | None:
        return self.env_values.get(name, default)

    def save(self, updates: dict[str, str] | None = None) -> None:
        if updates:
            self.env_values.update(updates)
            # Update attributes as well
            if "GABBEE_STT_PROVIDER" in updates:
                self.stt_provider = updates["GABBEE_STT_PROVIDER"]
            if "GABBEE_TOGGLE_SHORTCUT" in updates:
                self.toggle_shortcut = updates["GABBEE_TOGGLE_SHORTCUT"]
            if "GABBEE_WHISPER_LOCAL_MODEL" in updates:
                self.whisper_local_model = updates["GABBEE_WHISPER_LOCAL_MODEL"]
            if "GABBEE_WHISPER_LOCAL_DEVICE" in updates:
                self.whisper_local_device = updates["GABBEE_WHISPER_LOCAL_DEVICE"]
            if "GABBEE_SAMPLE_RATE" in updates:
                self.sample_rate = int(updates["GABBEE_SAMPLE_RATE"])

        content = []
        # Filter out system env vars, only save what we manage
        keys_to_save = [
            "ELEVENLABS_API_KEY",
            "GABBEE_STT_PROVIDER",
            "GABBEE_LANGUAGE_CODE",
            "GABBEE_ELEVENLABS_MODEL_ID",
            "GABBEE_ELEVENLABS_BASE_URL",
            "GABBEE_AUDIO_SOURCE",
            "GABBEE_SAMPLE_RATE",
            "GABBEE_FALLBACK_SINK",
            "GABBEE_UI_TITLE",
            "GABBEE_TOGGLE_SHORTCUT",
            "GABBEE_WHISPER_LOCAL_MODEL",
            "GABBEE_WHISPER_LOCAL_DEVICE",
            "GABBEE_WHISPER_LOCAL_COMPUTE_TYPE",
            "GABBEE_KEYWORDS",
        ]
        for key in keys_to_save:
            value = self.env_values.get(key)
            if value is not None:
                content.append(f"{key}={value}")
        
        # Add keywords if they exist and aren't in env_values as string already
        if self.keyword_map and "GABBEE_KEYWORDS" not in updates:
            kw_str = ",".join([f"{k}:{v}" for k, v in self.keyword_map.items()])
            # Check if GABBEE_KEYWORDS is already in content, if so replace it
            found = False
            for i, line in enumerate(content):
                if line.startswith("GABBEE_KEYWORDS="):
                    content[i] = f"GABBEE_KEYWORDS={kw_str}"
                    found = True
                    break
            if not found:
                content.append(f"GABBEE_KEYWORDS={kw_str}")

        self.env_file.write_text("\n".join(content) + "\n")

    def elevenlabs_api_key(self) -> str | None:
        return self.env("ELEVENLABS_API_KEY")

    def provider_label(self) -> str:
        return {
            "elevenlabs": "ElevenLabs",
            "mock": "Mock",
            "whisper_local": "Whisper (local)",
        }.get(self.stt_provider, self.stt_provider)


def _env_lookup(env_values: dict[str, str], name: str) -> str | None:
    return env_values.get(name)


def _get_str(env_values: dict[str, str], name: str, default: str) -> str:
    value = (_env_lookup(env_values, name) or "").strip()
    return value or default


def _get_optional_str(env_values: dict[str, str], name: str) -> str | None:
    value = (_env_lookup(env_values, name) or "").strip()
    return value or None


def _get_int(env_values: dict[str, str], name: str, default: int) -> int:
    raw = (_env_lookup(env_values, name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def load_config(paths: AppPaths | None = None) -> AppConfig:
    paths = paths or default_paths()
    paths.ensure()

    env_file = paths.env_file
    env_values: dict[str, str] = {}
    if env_file.exists():
        load_dotenv(env_file, override=False)
        env_values = {
            key: value
            for key, value in dotenv_values(env_file).items()
            if value is not None
        }

    merged_env = dict(env_values)
    merged_env.update(os.environ)

    default_provider = "elevenlabs" if _env_lookup(merged_env, "ELEVENLABS_API_KEY") else "mock"

    kw_raw = _get_str(merged_env, "GABBEE_KEYWORDS", "")
    keyword_map = {}
    if kw_raw:
        for pair in kw_raw.split(","):
            if ":" in pair:
                k, v = pair.split(":", 1)
                keyword_map[k.strip()] = v.strip()

    return AppConfig(
        paths=paths,
        env_file=env_file,
        env_values=merged_env,
        stt_provider=_get_str(merged_env, "GABBEE_STT_PROVIDER", default_provider).lower(),
        language_code=_get_str(merged_env, "GABBEE_LANGUAGE_CODE", "en"),
        elevenlabs_model_id=_get_str(merged_env, "GABBEE_ELEVENLABS_MODEL_ID", "scribe_v2"),
        elevenlabs_base_url=_get_str(merged_env, "GABBEE_ELEVENLABS_BASE_URL", "https://api.elevenlabs.io/v1"),
        audio_source=_get_optional_str(merged_env, "GABBEE_AUDIO_SOURCE"),
        sample_rate=_get_int(merged_env, "GABBEE_SAMPLE_RATE", 16000),
        fallback_sink=_get_str(merged_env, "GABBEE_FALLBACK_SINK", "clipboard"),
        ui_title=_get_str(merged_env, "GABBEE_UI_TITLE", "Gabbee"),
        toggle_shortcut=_get_str(merged_env, "GABBEE_TOGGLE_SHORTCUT", "F5"),
        whisper_local_model=_get_str(merged_env, "GABBEE_WHISPER_LOCAL_MODEL", "tiny"),
        whisper_local_device=_get_str(merged_env, "GABBEE_WHISPER_LOCAL_DEVICE", "cpu"),
        whisper_local_compute_type=_get_str(merged_env, "GABBEE_WHISPER_LOCAL_COMPUTE_TYPE", "default"),
        keyword_map=keyword_map,
    )

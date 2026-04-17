from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


APP_NAME = "gabbee"


def _xdg_dir(env_name: str, fallback: Path) -> Path:
    value = os.environ.get(env_name)
    return Path(value).expanduser() if value else fallback


@dataclass(frozen=True)
class AppPaths:
    config_dir: Path
    state_dir: Path
    cache_dir: Path
    runtime_dir: Path

    @property
    def env_file(self) -> Path:
        override = os.environ.get("GABBEE_ENV_FILE")
        if override:
            return Path(override).expanduser()
        return Path.home() / ".opencasenv" / ".env"

    @property
    def recording_path(self) -> Path:
        return self.cache_dir / "recording.wav"

    @property
    def engine_socket(self) -> Path:
        return self.runtime_dir / "engine.sock"

    @property
    def ibus_component_path(self) -> Path:
        data_home = _xdg_dir("XDG_DATA_HOME", Path.home() / ".local" / "share")
        return data_home / "ibus" / "component" / "gabbee.xml"

    def ensure(self) -> None:
        for path in (self.config_dir, self.state_dir, self.cache_dir, self.runtime_dir):
            path.mkdir(parents=True, exist_ok=True)


def default_paths() -> AppPaths:
    config_home = _xdg_dir("XDG_CONFIG_HOME", Path.home() / ".config")
    state_home = _xdg_dir("XDG_STATE_HOME", Path.home() / ".local" / "state")
    cache_home = _xdg_dir("XDG_CACHE_HOME", Path.home() / ".cache")
    runtime_base = Path(os.environ.get("XDG_RUNTIME_DIR", state_home))
    return AppPaths(
        config_dir=config_home / APP_NAME,
        state_dir=state_home / APP_NAME,
        cache_dir=cache_home / APP_NAME,
        runtime_dir=runtime_base / APP_NAME,
    )

from __future__ import annotations

import shutil
import subprocess


class FeedbackSounds:
    """Non-blocking, distinct sound cues backed by the desktop sound theme."""

    _EVENTS = {
        "dictation_start": "service-login",
        "dictation_stop": "complete",
        "command_start": "message-new-instant",
        "command_stop": "message",
        "error": "dialog-warning",
    }

    def __init__(self, player: str | None = None) -> None:
        self.player = player or shutil.which("canberra-gtk-play")

    def start(self, command_mode: bool) -> None:
        self._play("command_start" if command_mode else "dictation_start")

    def stop(self, command_mode: bool) -> None:
        self._play("command_stop" if command_mode else "dictation_stop")

    def error(self) -> None:
        self._play("error")

    def _play(self, cue: str) -> None:
        if not self.player:
            return
        try:
            subprocess.Popen(
                [self.player, "--id", self._EVENTS[cue]],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError:
            pass


class NullFeedbackSounds:
    def start(self, command_mode: bool) -> None:
        pass

    def stop(self, command_mode: bool) -> None:
        pass

    def error(self) -> None:
        pass

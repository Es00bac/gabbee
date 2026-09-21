from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic
from typing import Any

from .models import AppContext


@dataclass(slots=True, frozen=True)
class UtteranceJob:
    sequence: int
    audio_path: Path
    command_mode: bool
    created_at: float = field(default_factory=monotonic)
    app_context: AppContext | None = None
    profile_name: str = ""
    realtime_session: Any = field(default=None, compare=False, repr=False)
    keyterms: tuple[str, ...] = ()
    entity_detection: tuple[str, ...] = ()


def group_dictation_chains(
    jobs: list[UtteranceJob],
    *,
    continuation_seconds: float,
) -> list[list[UtteranceJob]]:
    chains: list[list[UtteranceJob]] = []
    for job in sorted(jobs, key=lambda item: item.sequence):
        if job.command_mode or not chains:
            chains.append([job])
            continue

        previous = chains[-1][-1]
        if previous.command_mode:
            chains.append([job])
            continue

        if previous.app_context is not None or job.app_context is not None:
            if previous.app_context is None or not previous.app_context.same_target(job.app_context):
                chains.append([job])
                continue

        if job.created_at - previous.created_at <= continuation_seconds:
            chains[-1].append(job)
        else:
            chains.append([job])
    return chains

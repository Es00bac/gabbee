from __future__ import annotations

from pathlib import Path

from gabbee.models import AppContext
from gabbee.utterance_queue import UtteranceJob, group_dictation_chains


def test_group_dictation_chains_merges_close_dictation_jobs() -> None:
    jobs = [
        UtteranceJob(sequence=1, audio_path=Path("one.wav"), command_mode=False, created_at=10.0),
        UtteranceJob(sequence=2, audio_path=Path("two.wav"), command_mode=False, created_at=11.5),
    ]

    chains = group_dictation_chains(jobs, continuation_seconds=3.0)

    assert [[job.sequence for job in chain] for chain in chains] == [[1, 2]]


def test_group_dictation_chains_separates_old_dictation_jobs() -> None:
    jobs = [
        UtteranceJob(sequence=1, audio_path=Path("one.wav"), command_mode=False, created_at=10.0),
        UtteranceJob(sequence=2, audio_path=Path("two.wav"), command_mode=False, created_at=20.0),
    ]

    chains = group_dictation_chains(jobs, continuation_seconds=3.0)

    assert [[job.sequence for job in chain] for chain in chains] == [[1], [2]]


def test_group_dictation_chains_keeps_commands_separate() -> None:
    jobs = [
        UtteranceJob(sequence=1, audio_path=Path("one.wav"), command_mode=True, created_at=10.0),
        UtteranceJob(sequence=2, audio_path=Path("two.wav"), command_mode=True, created_at=11.0),
    ]

    chains = group_dictation_chains(jobs, continuation_seconds=3.0)

    assert [[job.sequence for job in chain] for chain in chains] == [[1], [2]]


def test_group_dictation_chains_never_crosses_captured_focus_targets() -> None:
    jobs = [
        UtteranceJob(
            sequence=1,
            audio_path=Path("one.wav"),
            command_mode=False,
            created_at=10.0,
            app_context=AppContext(window_id="one", focused_selector="editor-a"),
        ),
        UtteranceJob(
            sequence=2,
            audio_path=Path("two.wav"),
            command_mode=False,
            created_at=10.1,
            app_context=AppContext(window_id="one", focused_selector="editor-b"),
        ),
    ]
    chains = group_dictation_chains(jobs, continuation_seconds=3.0)
    assert [[job.sequence for job in chain] for chain in chains] == [[1], [2]]

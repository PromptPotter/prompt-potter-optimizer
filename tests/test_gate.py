"""A gate's verdict lost to a sibling gate.

Owns `scripts/last_green.py` and the lock it takes in `scripts/processes.py`: gates in every
worktree of a clone file their greens in one `greens.json`, and one finishing beside another must
never drop what the other recorded.
"""

from __future__ import annotations

import threading
from pathlib import Path

import processes
import pytest
from last_green import LastGreen

# 1. Two gates recording at once


def test_two_gates_recording_at_once_both_keep_their_greens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = LastGreen("", tmp_path / "greens.json")
    first_loaded = threading.Event()
    second_moved = threading.Event()  # refused the lock, or done without one
    load, locked = LastGreen._load, processes._locked

    def first_holds_its_load(self: LastGreen) -> dict[str, list[str]]:
        held = load(self)
        if threading.current_thread() is first:
            first_loaded.set()
            assert second_moved.wait(30), "the second gate neither waited nor recorded"
        return held

    def second_refused(fd: int) -> bool:
        got = locked(fd)
        if not got and threading.current_thread() is second:
            second_moved.set()
        return got

    def second_gate() -> None:
        assert first_loaded.wait(30)
        store.record({"eslint": "k-eslint"})
        second_moved.set()

    monkeypatch.setattr(LastGreen, "_load", first_holds_its_load)
    monkeypatch.setattr(processes, "_locked", second_refused)
    first = threading.Thread(target=store.record, args=({"mypy": "k-mypy"},))
    second = threading.Thread(target=second_gate)
    for gate in (first, second):
        gate.start()
    for gate in (first, second):
        gate.join(30)
        assert not gate.is_alive()

    # The first gate loaded before the second recorded, and replaced after: only the lock keeps both.
    assert store.holds("mypy", "k-mypy")
    assert store.holds("eslint", "k-eslint")

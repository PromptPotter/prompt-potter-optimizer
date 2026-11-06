"""A gate's verdict lost to a sibling gate, or kept over what a sibling built.

Owns `scripts/last_green.py`, the lock and the writer it takes in `scripts/processes.py`, and the
build stamps of `scripts/gate.py::Check`: gates in every worktree of a clone file their greens in
one `greens.json` and walk one offline world, so one finishing beside another must never drop what
the other recorded, nor keep green over a world the other built.
"""

from __future__ import annotations

import dataclasses
import os
import threading
import time
from pathlib import Path

import gate
import processes
import pytest
from last_green import OFFLINE_RUN, LastGreen

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
    for each in (first, second):
        each.start()
    for each in (first, second):
        each.join(30)
        assert not each.is_alive()

    # The first gate loaded before the second recorded, and replaced after: only the lock keeps both.
    assert store.holds("mypy", "k-mypy")
    assert store.holds("eslint", "k-eslint")


# 2. A world a sibling worktree built


def test_a_world_another_key_built_keeps_no_green(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gate, "_BUILT_KEYS", tmp_path / "built")
    store = LastGreen("", tmp_path / "greens.json")
    world = tmp_path / "offline-run" / "potter"
    (world / "projects").mkdir(parents=True)
    named = next(c for c in gate.CHECKS if c.name == OFFLINE_RUN)
    check = dataclasses.replace(named, builds=str(world))
    store.record({OFFLINE_RUN: "k-a"})

    # Worktree B built the world last; A's green alone must not hold it.
    check.stamp().parent.mkdir(parents=True)
    processes.replace_changed(check.stamp(), "k-b")
    assert not check.kept(store, "k-a")
    # A's rebuild removes B's world before the run, so no tool's own green keeps it.
    gate._unstamp(check, store, "k-a")
    assert not world.exists()
    assert not check.stamp().exists()

    # Rebuilt and stamped with A's key: A's green holds, B's key does not.
    (world / "projects").mkdir(parents=True)
    processes.replace_changed(check.stamp(), "k-a")
    assert check.kept(store, "k-a")
    assert not check.kept(store, "k-b")


# 3. A write while a reader holds the output


def test_a_write_a_reader_refuses_once_still_lands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "types.generated.ts"
    out.write_text("old", encoding="utf-8")
    replace = os.replace
    refused: list[str] = []

    def held_once(src: str, dst: str) -> None:
        if not refused:
            refused.append(dst)
            raise PermissionError(5, "Access is denied", dst)
        replace(src, dst)

    monkeypatch.setattr(os, "replace", held_once)
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    assert processes.replace_changed(out, "new")
    assert refused
    assert out.read_text(encoding="utf-8") == "new"
    assert not list(tmp_path.glob(".*.tmp"))

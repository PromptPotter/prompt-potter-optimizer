"""Session exchange channel tests — ``journal.md`` / ``notes.md``.

Covers the helper functions in ``session_emitter`` and the routing on
``NotebookDisplay``. Artifact parity (both files exist on mint) is
already enforced by ``test_artifact_parity.py``.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from promptpotter.infrastructure.persistence.session_emitter import (
    CAMPAIGN_ARTIFACTS,
    SESSION_ARTIFACTS,
    append_journal,
    read_claude_notes,
)
from promptpotter.presentation.ui.campaign.notebook_display import NotebookDisplay


def test_artifact_sets_split_session_and_campaign() -> None:
    """Per-cycle and per-session artifact sets are disjoint."""
    assert CAMPAIGN_ARTIFACTS.isdisjoint(SESSION_ARTIFACTS)
    assert "journal.md" in SESSION_ARTIFACTS
    assert "notes.md" in SESSION_ARTIFACTS
    assert "session.json" in SESSION_ARTIFACTS
    assert "control.json" in SESSION_ARTIFACTS
    assert "dashboard.json" in CAMPAIGN_ARTIFACTS
    assert "index.json" in CAMPAIGN_ARTIFACTS


def test_append_journal_writes_timestamped_section(tmp_path: Path) -> None:
    (tmp_path / "journal.md").touch()
    append_journal(tmp_path, "action one", "body line")
    append_journal(tmp_path, "action two")

    content = (tmp_path / "journal.md").read_text(encoding="utf-8")
    assert "## " in content and "action one" in content
    assert "body line" in content
    assert content.count("## ") == 2, "second append must not clobber first"


def test_read_claude_notes_returns_empty_for_missing_file(tmp_path: Path) -> None:
    assert read_claude_notes(tmp_path) == ""


def test_read_claude_notes_returns_file_contents(tmp_path: Path) -> None:
    (tmp_path / "notes.md").write_text("## 2026 [FYI]\n\nbody\n", encoding="utf-8")
    assert "FYI" in read_claude_notes(tmp_path)


def _make_display(tmp_path: Path, session_id: str) -> NotebookDisplay:
    sessions = SimpleNamespace(
        session_dir=lambda sid: tmp_path / "sessions" / sid,
    )
    store = SimpleNamespace(sessions=sessions, base_dir=tmp_path)
    return NotebookDisplay(
        campaign_rounds=[],
        baseline_acc=0.0,
        l1_patience=3,
        pipeline_schema=None,
        store=store,
    )


def test_notebook_display_note_routes_to_journal(tmp_path: Path) -> None:
    session_dir = tmp_path / "sessions" / "s_abc12345"
    session_dir.mkdir(parents=True)
    (session_dir / "journal.md").touch()

    display = _make_display(tmp_path, "s_abc12345")
    with patch(
        "promptpotter.infrastructure.store.read_active_pointer",
        return_value=("default", "s_abc12345", "cycle_xyz"),
    ):
        display.note("smoke", "body")

    content = (session_dir / "journal.md").read_text(encoding="utf-8")
    assert "smoke" in content and "body" in content


def test_notebook_display_note_raises_without_active_pointer(tmp_path: Path) -> None:
    display = _make_display(tmp_path, "")
    with (
        patch(
            "promptpotter.infrastructure.store.read_active_pointer",
            return_value=("", "", ""),
        ),
        pytest.raises(RuntimeError, match="No active session"),
    ):
        display.note("will not write")

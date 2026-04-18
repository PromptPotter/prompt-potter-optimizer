"""Session exchange channel tests — ``journal.md`` / ``notes.md``.

Covers the helper functions in ``session_emitter`` and the routing on
``NotebookDisplay``. Artifact parity (both files exist on mint) is
already enforced by ``test_artifact_parity.py``.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from promptpotter.infrastructure.persistence.session_emitter import (
    CAMPAIGN_NARRATIVE_ARTIFACTS,
    CAMPAIGN_OPERATIONAL_ARTIFACTS,
    CAMPAIGN_SESSION_ARTIFACTS,
    append_journal,
    read_claude_notes,
)
from promptpotter.presentation.ui.campaign.notebook_display import NotebookDisplay


def test_artifact_set_splits_operational_and_narrative() -> None:
    """The split exposes two named tiers; the union matches the contract."""
    assert CAMPAIGN_OPERATIONAL_ARTIFACTS.isdisjoint(CAMPAIGN_NARRATIVE_ARTIFACTS)
    assert CAMPAIGN_SESSION_ARTIFACTS == (
        CAMPAIGN_OPERATIONAL_ARTIFACTS | CAMPAIGN_NARRATIVE_ARTIFACTS
    )
    assert "journal.md" in CAMPAIGN_NARRATIVE_ARTIFACTS
    assert "notes.md" in CAMPAIGN_NARRATIVE_ARTIFACTS
    assert len(CAMPAIGN_NARRATIVE_ARTIFACTS) == 2


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


def _make_display(tmp_path: Path, cycle_id: str) -> NotebookDisplay:
    campaigns = SimpleNamespace(
        read_active_pointer=lambda: ("default", cycle_id),
        campaign_dir=lambda cid: tmp_path / "campaigns" / cid,
    )
    store = SimpleNamespace(campaigns=campaigns, base_dir=tmp_path)
    return NotebookDisplay(
        campaign_rounds=[],
        baseline_acc=0.0,
        l1_patience=3,
        pipeline_schema=None,
        store=store,
    )


def test_notebook_display_note_routes_to_journal(tmp_path: Path) -> None:
    cycle_dir = tmp_path / "campaigns" / "cycle_abc"
    cycle_dir.mkdir(parents=True)
    (cycle_dir / "journal.md").touch()

    display = _make_display(tmp_path, "cycle_abc")
    display.note("smoke", "body")

    content = (cycle_dir / "journal.md").read_text(encoding="utf-8")
    assert "smoke" in content and "body" in content


def test_notebook_display_note_raises_without_active_pointer(tmp_path: Path) -> None:
    display = _make_display(tmp_path, "")
    with pytest.raises(RuntimeError, match="No active session"):
        display.note("will not write")

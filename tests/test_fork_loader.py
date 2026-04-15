"""Tests for fork_loader — spec parsing and events.jsonl read path.

Covers the two public primitives that forking is built on:

- ``parse_fork_spec`` — string grammar → ``ForkAddress``
- ``load_fork_seed`` — streams a synthesized ``events.jsonl`` and extracts
  the ``state_snapshot`` for a given write point.
"""

from __future__ import annotations

import json

import pytest

from promptpotter.application.campaign.fork_loader import (
    ForkAddress,
    events_log_path,
    load_fork_seed,
    parse_fork_spec,
)

# ---------------------------------------------------------------------------
# parse_fork_spec
# ---------------------------------------------------------------------------


class TestParseForkSpec:
    def test_absolute_form(self):
        addr = parse_fork_spec("@42")
        assert addr.event_index == 42
        assert addr.round_num is None
        assert addr.write_point is None

    def test_human_form_round_and_write_point(self):
        addr = parse_fork_spec("1:l1_generate")
        assert addr.round_num == 1
        assert addr.write_point == "candidate_created"  # aliased
        assert addr.candidate_idx is None
        assert addr.query_idx is None

    def test_human_form_with_candidate_idx(self):
        addr = parse_fork_spec("2:candidate:3")
        assert addr.round_num == 2
        assert addr.write_point == "candidate_scored"
        assert addr.candidate_idx == 3

    def test_human_form_query_scored(self):
        addr = parse_fork_spec("1:query_scored:0:7")
        assert addr.write_point == "query_scored"
        assert addr.candidate_idx == 0
        assert addr.query_idx == 7

    def test_canonical_name_accepted(self):
        addr = parse_fork_spec("1:candidate_created:0")
        assert addr.write_point == "candidate_created"

    def test_critique_alias(self):
        assert parse_fork_spec("3:critique").write_point == "critique_written"

    def test_l2_alias(self):
        assert parse_fork_spec("5:l2").write_point == "l2_applied"

    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "   ",
            "@notanint",
            "@-1",
            "abc:l1_generate",
            "1",
            "1:unknown_write_point",
            "1:l1_generate:not_int",
            "1:l1_generate:0:1:extra",
            "1:candidate:0:4",  # query_idx only valid for query_scored
        ],
    )
    def test_invalid_specs_raise(self, bad: str):
        with pytest.raises(ValueError):
            parse_fork_spec(bad)


# ---------------------------------------------------------------------------
# load_fork_seed
# ---------------------------------------------------------------------------


def _write_events(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def _snap(tag: str) -> dict:
    """Minimal state_snapshot payload keyed by a unique tag so tests can
    assert they landed on the right event.
    """
    return {"instruction": f"snap-{tag}", "changes_description": tag}


class TestLoadForkSeed:
    def _setup_log(self, tmp_path):
        """Build a minimal events.jsonl with every fork-addressable write point."""
        base = tmp_path / "store"
        log_path = events_log_path(base, "bid")
        rows = [
            {"event": "campaign_start", "campaign_id": "cycle_a"},
            {
                "event": "candidate_created",
                "campaign_id": "cycle_a",
                "round": 1,
                "candidate_idx": 0,
                "state_snapshot": _snap("cc0"),
            },
            {
                "event": "candidate_created",
                "campaign_id": "cycle_a",
                "round": 1,
                "candidate_idx": 1,
                "state_snapshot": _snap("cc1"),
            },
            {
                "event": "query_scored",
                "campaign_id": "cycle_a",
                "round": 1,
                "candidate_idx": 0,
                "query_idx": 3,
                "state_snapshot": _snap("qs_0_3"),
            },
            {
                "event": "candidate_scored",
                "campaign_id": "cycle_a",
                "round": 1,
                "candidate_idx": 0,
                "state_snapshot": _snap("cs0"),
            },
            {
                "event": "critique_written",
                "campaign_id": "cycle_a",
                "round": 1,
                "state_snapshot": _snap("crit"),
            },
            {
                "event": "l2_applied",
                "campaign_id": "cycle_a",
                "round": 1,
                "state_snapshot": _snap("l2"),
            },
            # Contamination — a different cycle's events must be ignored.
            {
                "event": "candidate_created",
                "campaign_id": "cycle_b",
                "round": 1,
                "candidate_idx": 0,
                "state_snapshot": _snap("other"),
            },
        ]
        _write_events(log_path, rows)
        return base

    def test_l1_generate_default_candidate(self, tmp_path):
        base = self._setup_log(tmp_path)
        seed = load_fork_seed(base, "bid", "cycle_a", "1:l1_generate:0")
        assert seed.state_snapshot == _snap("cc0")
        assert seed.event_name == "candidate_created"

    def test_query_scored_exact(self, tmp_path):
        base = self._setup_log(tmp_path)
        seed = load_fork_seed(base, "bid", "cycle_a", "1:query_scored:0:3")
        assert seed.state_snapshot == _snap("qs_0_3")

    def test_candidate_scored(self, tmp_path):
        base = self._setup_log(tmp_path)
        seed = load_fork_seed(base, "bid", "cycle_a", "1:candidate:0")
        assert seed.state_snapshot == _snap("cs0")

    def test_critique(self, tmp_path):
        base = self._setup_log(tmp_path)
        seed = load_fork_seed(base, "bid", "cycle_a", "1:critique")
        assert seed.state_snapshot == _snap("crit")

    def test_l2(self, tmp_path):
        base = self._setup_log(tmp_path)
        seed = load_fork_seed(base, "bid", "cycle_a", "1:l2")
        assert seed.state_snapshot == _snap("l2")

    def test_absolute_index(self, tmp_path):
        base = self._setup_log(tmp_path)
        # Cycle slice (after filtering cycle_b): index 0 = campaign_start,
        # index 1 = candidate_created cc0. Campaign_start has no snapshot
        # so we target index 1.
        seed = load_fork_seed(base, "bid", "cycle_a", "@1")
        assert seed.state_snapshot == _snap("cc0")
        assert seed.event_index == 1

    def test_parsed_address_accepted_directly(self, tmp_path):
        base = self._setup_log(tmp_path)
        addr = ForkAddress(raw="1:critique", round_num=1, write_point="critique_written")
        seed = load_fork_seed(base, "bid", "cycle_a", addr)
        assert seed.state_snapshot == _snap("crit")

    def test_other_cycle_events_ignored(self, tmp_path):
        base = self._setup_log(tmp_path)
        seed = load_fork_seed(base, "bid", "cycle_a", "1:l1_generate:0")
        assert seed.state_snapshot != _snap("other")

    def test_missing_log_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_fork_seed(tmp_path, "bid", "cycle_a", "1:l1_generate")

    def test_unknown_cycle_raises(self, tmp_path):
        base = self._setup_log(tmp_path)
        with pytest.raises(LookupError):
            load_fork_seed(base, "bid", "cycle_nonexistent", "1:l1_generate")

    def test_no_matching_event_raises(self, tmp_path):
        base = self._setup_log(tmp_path)
        with pytest.raises(LookupError):
            load_fork_seed(base, "bid", "cycle_a", "2:l1_generate")

    def test_candidate_idx_filter_no_match(self, tmp_path):
        base = self._setup_log(tmp_path)
        with pytest.raises(LookupError):
            load_fork_seed(base, "bid", "cycle_a", "1:l1_generate:99")

    def test_event_without_snapshot_rejected(self, tmp_path):
        """campaign_start has no state_snapshot — attempting to fork from it
        must fail loudly rather than silently returning an empty seed.
        """
        base = self._setup_log(tmp_path)
        with pytest.raises(ValueError, match="state_snapshot"):
            load_fork_seed(base, "bid", "cycle_a", "@0")

"""Unit coverage for the shared candidate render kernel.

``build_candidate_summary`` classifies a score report into four branches
(ok / invalid / aborted / eliminated) and pre-formats the pieces both
displays consume. ``format_elimination_summary`` renders the one-line
summary for early-stopped candidates. Assertions are substring-based so
ANSI wrapping changes don't churn the tests.
"""

from __future__ import annotations

from promptpotter.presentation.ui.campaign.notebook_primitives import (
    build_candidate_summary,
    fmt_candidate_header,
    fmt_pp_override,
    format_elimination_summary,
)


def test_build_summary_ok() -> None:
    scores = {
        "accuracy": 0.75,
        "hits": 15,
        "total": 20,
        "composite": 0.75,
        "pipeline_params_override": {"web_search": {"max_sites": 7}},
    }
    s = build_candidate_summary(scores, baseline_acc=0.60)
    assert s.status == "ok"
    assert "75.0%" in s.tag
    assert "web_search.max_sites: 7" in s.body_line
    assert "15/20 hits" in s.body_line
    assert "+15.0%" in s.body_line
    assert s.detail_lines == ()


def test_build_summary_composite_vs_accuracy_splits() -> None:
    scores = {
        "accuracy": 0.50,
        "hits": 10,
        "total": 20,
        "composite": 0.63,
        "pipeline_params_override": None,
    }
    s = build_candidate_summary(scores, baseline_acc=0.50)
    assert s.status == "ok"
    # Body line has no mutation prefix when override is empty.
    assert s.body_line.lstrip().startswith("10/20 hits")
    # Composite differs from accuracy → surfaces as a detail line.
    assert any("composite=0.6300" in line for line in s.detail_lines)


def test_build_summary_invalid() -> None:
    scores = {
        "accuracy": 0.0,
        "hits": 0,
        "total": 0,
        "composite": 0.0,
        "invalid": True,
        "validation_failures": [
            {"axis": "temperature", "value": 1.5, "allowed": ["0.0", "0.5", "1.0"]}
        ],
        "pipeline_params_override": None,
    }
    s = build_candidate_summary(scores, baseline_acc=0.40)
    assert s.status == "invalid"
    assert "INVALID" in s.tag
    assert s.body_line == ""
    # Each failure renders as a cause + response pair.
    assert len(s.detail_lines) == 2
    assert "temperature" in s.detail_lines[0]
    assert "scored 0" in s.detail_lines[1]


def test_build_summary_aborted() -> None:
    scores = {
        "accuracy": 0.30,
        "hits": 3,
        "total": 10,
        "composite": 0.30,
        "escalation_aborted": True,
        "scored_queries": 10,
        "expected_queries": 20,
        "pipeline_params_override": None,
    }
    s = build_candidate_summary(scores, baseline_acc=0.50)
    assert s.status == "aborted"
    assert "aborted 10/20" in s.body_line
    assert "vs baseline" in s.body_line


def test_build_summary_eliminated() -> None:
    scores = {
        "accuracy": 0.20,
        "hits": 1,
        "total": 5,
        "composite": 0.20,
        "elimination_stopped": True,
        "elimination_context": {
            "triggered_p": 0.023,
            "triggered_by_prior_idx": 0,
            "triggered_by_prior_label": "C1",
            "queries_evaluated": 5,
            "total_queries": 15,
            "n_priors": 1,
        },
        "pipeline_params_override": None,
    }
    s = build_candidate_summary(scores, baseline_acc=0.60)
    assert s.status == "eliminated"
    assert s.detail_lines, "elimination summary must be surfaced"
    first = s.detail_lines[0]
    assert "eliminated q5/15" in first
    assert "p=" in first
    assert "C1" in first


def test_fmt_pp_override_flatten() -> None:
    assert fmt_pp_override(None) == ""
    assert fmt_pp_override({}) == ""
    out = fmt_pp_override({"web_search": {"max_sites": 5}, "entity_profiling": "off"})
    assert "web_search.max_sites: 5" in out
    assert "entity_profiling: off" in out


def test_fmt_candidate_header_branches() -> None:
    # Override present → mutation body
    line = fmt_candidate_header(1, 3, "", {"web_search": {"max_sites": 7}})
    assert "cand 2/3" in line
    assert "web_search.max_sites: 7" in line

    # No override but description present
    line2 = fmt_candidate_header(0, 2, "raised temperature", None)
    assert "raised temperature" in line2

    # Neither → parent re-eval sentinel
    line3 = fmt_candidate_header(0, 1, "", None)
    assert "parent re-eval" in line3


def test_format_elimination_summary_with_and_without_label() -> None:
    ctx = {
        "triggered_p": 0.01,
        "triggered_by_prior_idx": 2,
        "queries_evaluated": 7,
        "total_queries": 15,
        "n_priors": 3,
    }
    # With resolved label
    out = format_elimination_summary(ctx, prior_label="C3")
    assert "eliminated q7/15" in out
    assert "C3" in out
    assert "3 priors" in out

    # Without resolved label — falls back to the integer index
    out2 = format_elimination_summary(ctx, prior_label=None)
    assert "prior #2" in out2

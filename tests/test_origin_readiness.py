"""Origin-resolution gate — parser split + the deterministic readiness checklist.

Guards two contracts (per tests/CLAUDE.md categories 3 + 4): the header-agnostic
parser materializes arbitrary column names once a mapping is confirmed, and the
closed-set checklist blocks mint until every origin field is CONFIRMED — the
column mapping *and* the once-hidden config defaults, with `task_description`
the one field that lands UNSET (no default framing).
"""

from __future__ import annotations

from promptpotter.application.datasets.csv_ingest import materialize_samples, read_tabular
from promptpotter.application.datasets.draft_campaign import DraftCampaignRegistry
from promptpotter.application.datasets.origin_readiness import origin_readiness, resolution_block
from promptpotter.domain.identity import TenantId
from promptpotter.domain.origin_provenance import Provenance, ProvenanceSource


def test_read_tabular_is_header_agnostic_and_materializes_arbitrary_columns() -> None:
    """`input` / `gt` (not the literal `query` / `ground_truth`) parse + map cleanly."""
    table = read_tabular(b"input,gt\nq1,a1\nq2,a2\n")
    assert table.headers == ("input", "gt")
    assert len(table.rows) == 2

    samples = materialize_samples(table, query_col="input", ground_truth_col="gt")
    assert [(s.id, s.query, s.ground_truth) for s in samples] == [
        (0, "q1", "a1"),
        (1, "q2", "a2"),
    ]


def test_readiness_gates_on_closed_set() -> None:
    """Config knobs auto-confirm from template defaults; columns + task framing gate."""
    registry = DraftCampaignRegistry()
    tenant = TenantId("t1")

    # Non-literal headers → columns UNSET; task_description has no default → UNSET.
    # The config knobs (connector/scoring/max_rounds/provider/model/node_config)
    # auto-confirm at create, so they are NOT gaps — a hidden default is now a
    # visible, confirmed, operator-overridable value.
    draft = registry.create(
        tenant_id=tenant,
        slug="d1",
        n_samples=1,
        sample_preview=[{"input": "q", "gt": "a"}],
        headers=["input", "gt"],
    )
    verdict = origin_readiness(draft)
    assert not verdict.complete
    assert {g.field for g in verdict.gaps} == {
        "column.query",
        "column.ground_truth",
        "task_description",
    }
    # Auto-confirmed config knobs carry the AUTO source (a once-hidden default
    # now visible); UNSET fields carry no source at all.
    assert draft.sources["connector"] is ProvenanceSource.AUTO
    assert "task_description" not in draft.sources

    # Confirm the columns (operator-stated) + state the framing → every
    # closed-set field CONFIRMED, and the operator-set fields sourced STATED.
    opened = draft.confirm_columns(query_col="input", ground_truth_col="gt").apply_resolution(
        values={"task_description": "Map lab-test names to codes."},
        provenance={"task_description": Provenance.CONFIRMED},
        sources={"task_description": ProvenanceSource.STATED},
    )
    assert origin_readiness(opened).complete
    assert opened.sources["column.query"] is ProvenanceSource.STATED
    # The on-disk block carries the source axis alongside provenance.
    block = resolution_block(opened)
    assert block["sources"]["connector"] == "auto"
    assert block["sources"]["task_description"] == "stated"

    # Literal headers auto-confirm the columns, but task_description still gates.
    literal = registry.create(
        tenant_id=tenant,
        slug="d2",
        n_samples=1,
        sample_preview=[{"query": "q", "ground_truth": "a"}],
        headers=["query", "ground_truth"],
    )
    assert not origin_readiness(literal).complete
    assert {g.field for g in origin_readiness(literal).gaps} == {"task_description"}


def test_low_confidence_proposal_blocks_until_confirmed() -> None:
    """A PROPOSED field (low-confidence resolver finding) blocks mint; CONFIRMED opens it."""
    registry = DraftCampaignRegistry()
    draft = registry.create(
        tenant_id=TenantId("t1"),
        slug="d3",
        n_samples=1,
        sample_preview=[{"query": "q", "ground_truth": "a"}],
        headers=["query", "ground_truth"],
    )
    # Resolver proposes a framing at low confidence → PROPOSED, still blocks.
    proposed = draft.apply_resolution(
        values={"task_description": "maybe map codes"},
        provenance={"task_description": Provenance.PROPOSED},
    )
    verdict = origin_readiness(proposed)
    assert not verdict.complete
    assert verdict.gaps[0].reason == "proposed_unconfirmed"

    # Operator confirms → complete.
    confirmed = proposed.apply_resolution(provenance={"task_description": Provenance.CONFIRMED})
    assert origin_readiness(confirmed).complete


def test_optimizer_locks_surface_connector_clamp() -> None:
    """The draft wire exposes TermNorm's reasoning clamp + forbidden axes pre-commit.

    A draft's `pipeline_overlay` is empty until commit, so the new-campaign UI
    can only show "the optimizer is locked out of medium/high thinking" if the
    wire carries the connector's seed. `draft_wire_with_locks` is that surface.
    """
    from promptpotter.application.jobs.launcher import draft_wire_with_locks

    registry = DraftCampaignRegistry()
    draft = registry.create(
        tenant_id=TenantId("t1"),
        slug="d4",
        n_samples=1,
        sample_preview=[{"query": "q", "ground_truth": "a"}],
        headers=["query", "ground_truth"],
    )
    locks = draft_wire_with_locks(draft)["optimizer_locks"]

    assert locks["pipeline"] == ["llm_only"]
    # Model/provider are pinned campaign-wide by forbidden_axes_strict (default on).
    assert locks["forbidden_axes"] == ["model", "provider"]
    node = locks["nodes"]["llm_only"]
    assert node["config"]["reasoning_effort"] == "low"
    # `medium` / `high` are absent from the allowed set → crossed out in the UI.
    allowed = node["param_allowed_values"]["reasoning_effort"]
    assert "low" in allowed and "medium" not in allowed and "high" not in allowed

"""Origin-resolution gate — parser split + the deterministic readiness checklist.

Guards two contracts (per tests/CLAUDE.md categories 3 + 4): the header-agnostic
parser materializes arbitrary column names once a mapping is confirmed, and the
checklist blocks mint until the input/target columns are CONFIRMED.
"""

from __future__ import annotations

from promptpotter.application.datasets.csv_ingest import materialize_samples, read_tabular
from promptpotter.application.datasets.draft_campaign import DraftCampaignRegistry
from promptpotter.application.datasets.origin_readiness import origin_readiness
from promptpotter.domain.identity import TenantId


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


def test_readiness_gates_on_column_mapping() -> None:
    """Unset columns block; deterministic auto-confirm of literal headers passes."""
    registry = DraftCampaignRegistry()
    tenant = TenantId("t1")

    # Non-literal headers → mapping UNSET → two gaps, not complete.
    unset = registry.create(
        tenant_id=tenant,
        slug="d1",
        n_samples=1,
        sample_preview=[{"input": "q", "gt": "a"}],
        headers=["input", "gt"],
    )
    verdict = origin_readiness(unset)
    assert not verdict.complete
    assert {g.field for g in verdict.gaps} == {"column.query", "column.ground_truth"}

    # Literal headers → auto-confirmed → complete. Operator confirmation of the
    # non-literal draft reaches the same CONFIRMED state.
    literal = registry.create(
        tenant_id=tenant,
        slug="d2",
        n_samples=1,
        sample_preview=[{"query": "q", "ground_truth": "a"}],
        headers=["query", "ground_truth"],
    )
    assert origin_readiness(literal).complete

    opened = unset.confirm_columns(query_col="input", ground_truth_col="gt")
    assert origin_readiness(opened).complete

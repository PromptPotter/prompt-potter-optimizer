"""MeasurementArchive + AxisIndex/ConfigIndex/SampleIndex retrieval contracts +
zero-signal sample filtering.

Four named invariants:
  1. ``MeasurementArchive`` retrieval — ``measurements_for_sample`` returns
     only matching rows; ``measurements_for_config`` honours subset / value /
     multi-node predicates and returns empty for ``{}``;
     ``find_by_node_configs`` is positional prefix-exact (cache reuse).
  2. ``AxisIndex.refresh`` rebuilds ``_axis_values`` from the archive (pure
     derivation, idempotent under no-change refresh, no persistence under
     ``archive/``); ``ConfigIndex.run_ids_matching`` parity with
     ``measurements_for_config`` full scan.
  3. Cross-cycle aggregators — ``StopReason.DIAG_COMPLETE`` is a named
     wire-shape member; ``build_archive_observations`` keys candidates by
     ``content_hash[:12]`` and drops error/missing-id rows.
  4. Zero-signal filter — ``SampleIndex.dead`` respects ``min_observations``
     and ``include_always_hit``; ``BackendStore.exclude_dataset_items`` /
     ``restore_dataset_items`` round-trip; ``apply_zero_signal_exclusions``
     mutates both the in-memory active list and the on-disk dataset.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from promptpotter.application.intelligence.hard_sample_archive import (
    build_archive_observations,
)
from promptpotter.application.intelligence.indexes import AxisIndex, SampleIndex
from promptpotter.domain.phases import StopReason
from promptpotter.domain.sample import Sample
from promptpotter.infrastructure.store import build_stores
from promptpotter.infrastructure.store.measurement_archive import MeasurementArchive

# ===========================================================================
# MeasurementArchive retrieval
# ===========================================================================


def _seed_archive_run(
    archive: MeasurementArchive,
    run_id: str,
    *,
    node_configs: list[tuple[str, dict[str, Any]]],
    items: list[dict[str, Any]],
) -> None:
    archive.save(
        "any-backend",
        run_id,
        {
            "run_id": run_id,
            "name": run_id,
            "content_hash": f"hash_{run_id}",
            "prompt_fields_id": "pf_x",
            "item_count": len(items),
            "scores": {"accuracy": 0.5, "total": len(items)},
            "node_configs": node_configs,
            "pipeline_params": dict(node_configs),
            "created_at": "2026-01-01T00:00:00Z",
            "measurements": items,
        },
    )


def _archive_item(sample_id: int, *, hit: bool, fitness: float = 1.0) -> dict[str, Any]:
    return {
        "sample_id": sample_id,
        "query": f"q_{sample_id}",
        "ground_truth": f"gt_{sample_id}",
        "predicted": "pred",
        "hit": hit,
        "fitness": fitness,
        "pipeline_data": {"terminated_at": "llm_only"},
    }


@pytest.fixture
def archive(tmp_path: Path) -> MeasurementArchive:
    a = MeasurementArchive(tmp_path)
    _seed_archive_run(
        a,
        "run_a",
        node_configs=[("llm_only", {"model": "X", "temperature": 0.0})],
        items=[_archive_item(0, hit=True), _archive_item(1, hit=False)],
    )
    _seed_archive_run(
        a,
        "run_b",
        node_configs=[("llm_only", {"model": "Y", "temperature": 0.0})],
        items=[_archive_item(0, hit=False), _archive_item(1, hit=True)],
    )
    _seed_archive_run(
        a,
        "run_c",
        node_configs=[("web_search", {"max_sites": 5}), ("llm_only", {"model": "X"})],
        items=[_archive_item(0, hit=True)],
    )
    return a


def test_measurements_for_sample_returns_only_matching_rows(
    archive: MeasurementArchive,
) -> None:
    rows = archive.measurements_for_sample("any-backend", 0)
    assert len(rows) == 3  # sample 0 in all three runs
    assert {r.run_id for r in rows} == {"run_a", "run_b", "run_c"}
    assert all(r.sample_id == 0 for r in rows)
    a_row = next(r for r in rows if r.run_id == "run_a")
    assert a_row.hit is True
    assert a_row.node_configs == [("llm_only", {"model": "X", "temperature": 0.0})]
    assert a_row.fitness == 1.0


def test_measurements_for_config_subset_predicate(archive: MeasurementArchive) -> None:
    # Node-presence: every run has llm_only → all rows.
    presence = archive.measurements_for_config("any-backend", {"llm_only": {}})
    assert {r.run_id for r in presence} == {"run_a", "run_b", "run_c"}

    # Value match: only run_a + run_c carry model=X.
    by_model_x = archive.measurements_for_config("any-backend", {"llm_only": {"model": "X"}})
    assert {r.run_id for r in by_model_x} == {"run_a", "run_c"}

    # Multi-node predicate: only run_c has both nodes.
    multi = archive.measurements_for_config("any-backend", {"web_search": {}, "llm_only": {}})
    assert {r.run_id for r in multi} == {"run_c"}

    # No-match value: returns nothing.
    none_match = archive.measurements_for_config("any-backend", {"llm_only": {"model": "Z"}})
    assert none_match == []


def test_measurements_for_config_empty_predicate_returns_empty(
    archive: MeasurementArchive,
) -> None:
    assert archive.measurements_for_config("any-backend", {}) == []


def test_find_by_node_configs_prefix_exact(archive: MeasurementArchive) -> None:
    # Single-node prefix: matches run_a and run_b (both start with llm_only),
    # but each with a different config — only run_a matches model=X exactly.
    matches = archive.find_by_node_configs(
        "any-backend",
        [("llm_only", {"model": "X", "temperature": 0.0})],
    )
    assert len(matches) == 1
    entry, match_len = matches[0]
    assert entry["run_id"] == "run_a"
    assert match_len == 1

    # Two-node prefix matches run_c only.
    matches2 = archive.find_by_node_configs(
        "any-backend",
        [("web_search", {"max_sites": 5}), ("llm_only", {"model": "X"})],
    )
    assert len(matches2) == 1
    assert matches2[0][0]["run_id"] == "run_c"
    assert matches2[0][1] == 2

    # Empty spec returns empty list (cache-reuse no-op contract).
    assert archive.find_by_node_configs("any-backend", []) == []


# ===========================================================================
# AxisIndex / ConfigIndex
# ===========================================================================


def _seed_indexed_run(
    archive: MeasurementArchive,
    run_id: str,
    *,
    pipeline_params: dict[str, dict[str, Any]],
    accuracy: float,
) -> None:
    archive.save(
        "any-backend",
        run_id,
        {
            "run_id": run_id,
            "name": run_id,
            "content_hash": f"hash_{run_id}",
            "prompt_fields_id": "pf_x",
            "item_count": 1,
            "scores": {"accuracy": accuracy, "total": 1},
            "node_configs": list(pipeline_params.items()),
            "pipeline_params": pipeline_params,
            "created_at": "2026-01-01T00:00:00Z",
            "measurements": [
                {
                    "sample_id": 0,
                    "query": "q",
                    "ground_truth": "gt",
                    "predicted": "p",
                    "hit": accuracy >= 0.5,
                    "fitness": accuracy,
                    "pipeline_data": {"terminated_at": "llm_only"},
                }
            ],
        },
    )


@pytest.fixture
def indexed_stores(tmp_path: Path):
    s = build_stores(tmp_path / "projects", datasets_root=tmp_path / "datasets")
    _seed_indexed_run(
        s.archive,
        "run_a",
        pipeline_params={"llm_only": {"model": "X", "temperature": 0.0}},
        accuracy=0.8,
    )
    _seed_indexed_run(
        s.archive,
        "run_b",
        pipeline_params={"llm_only": {"model": "Y", "temperature": 0.0}},
        accuracy=0.4,
    )
    return s


def test_axis_index_refresh_rebuilds_axis_values(indexed_stores: Any) -> None:
    idx = AxisIndex()
    idx.refresh(indexed_stores, "any-backend", dataset_name=None)

    # Axis side: model has two values (X, Y); temperature has one (0.0).
    assert set(idx._axis_values["llm_only.model"].keys()) == {"X", "Y"}
    assert idx._axis_values["llm_only.model"]["X"] == [0.8]
    assert idx._axis_values["llm_only.model"]["Y"] == [0.4]
    assert set(idx._axis_values["llm_only.temperature"].keys()) == {"0.0"}
    assert sorted(idx._axis_values["llm_only.temperature"]["0.0"]) == [0.4, 0.8]

    # Idempotent under full rebuild.
    snapshot = {
        a: {v: list(accs) for v, accs in vals.items()} for a, vals in idx._axis_values.items()
    }
    idx.refresh(indexed_stores, "any-backend", dataset_name=None)
    rebuilt = {
        a: {v: list(accs) for v, accs in vals.items()} for a, vals in idx._axis_values.items()
    }
    assert rebuilt == snapshot


def test_axis_index_no_persistence(indexed_stores: Any, tmp_path: Path) -> None:
    """Neither digest side writes anything to ``archive/``."""
    idx = AxisIndex()
    idx.refresh(indexed_stores, "any-backend", dataset_name=None)
    library = indexed_stores.base_dir / "archive"
    assert not (library / "axes.json").exists()
    assert not (library / "search_memory.json").exists()
    assert not (library / "samples.json").exists()


def test_config_index_run_ids_match_archive_full_scan(indexed_stores: Any) -> None:
    """ConfigIndex.run_ids_matching parity with measurements_for_config full scan.

    Webapp + ablation paths poll measurements_for_config repeatedly; the
    indexed fast path MUST return the same run set as the unindexed scan
    or downstream views diverge. Predicate variants cover exact-config,
    subset, and no-match.
    """
    idx = AxisIndex()
    idx.refresh(indexed_stores, "any-backend", dataset_name=None)

    for predicate in (
        {"llm_only": {"model": "X"}},  # subset, matches run_a
        {"llm_only": {"temperature": 0.0}},  # subset, matches both
        {"llm_only": {"model": "Z"}},  # no match
        {},  # archive contract: empty predicate → empty
    ):
        indexed_run_ids = idx.config_index.run_ids_matching(predicate)
        full_scan = indexed_stores.archive.measurements_for_config("any-backend", predicate)
        full_scan_run_ids = {m.run_id for m in full_scan}
        assert indexed_run_ids == full_scan_run_ids, (
            f"ConfigIndex/archive parity broken for predicate {predicate!r}"
        )

        # And the archive's own indexed-load path returns the same measurements.
        indexed_load = indexed_stores.archive.measurements_for_config(
            "any-backend", predicate, run_ids=indexed_run_ids
        )
        assert {m.run_id for m in indexed_load} == full_scan_run_ids


# ===========================================================================
# Cross-cycle aggregators
# ===========================================================================


def _seed_cross_cycle(
    archive: MeasurementArchive,
    *,
    run_id: str,
    content_hash: str,
    items: list[dict[str, Any]],
) -> None:
    archive.save(
        "bk",
        run_id,
        {
            "run_id": run_id,
            "name": run_id,
            "content_hash": content_hash,
            "prompt_fields_id": "pf_x",
            "item_count": len(items),
            "scores": {"accuracy": 0.5},
            "node_configs": [["llm_only", {"model": "stub"}]],
            "pipeline_params": {"llm_only": {"model": "stub"}},
            "created_at": "2026-04-30T00:00:00Z",
            "measurements": items,
        },
    )


def _cross_cycle_item(
    sid: int, *, hit: bool, fitness: float = 1.0, error: bool = False
) -> dict[str, Any]:
    return {
        "sample_id": sid,
        "query": f"q_{sid}",
        "ground_truth": f"gt_{sid}",
        "predicted": "ERROR" if error else "pred",
        "hit": hit,
        "fitness": fitness,
        "error": error,
        "pipeline_data": {"terminated_at": "llm_only"},
    }


@pytest.fixture
def aggregator_archive(tmp_path: Path) -> MeasurementArchive:
    return MeasurementArchive(tmp_path)


def test_diag_complete_is_a_named_stop_reason() -> None:
    """Diag mode wire shape: the new stop reason must exist for the runner +
    dashboard to recognize a diag-mode halt."""
    assert StopReason.DIAG_COMPLETE.value == "diag_complete"


def test_archive_observations_use_content_hash_prefix(
    aggregator_archive: MeasurementArchive,
) -> None:
    """Candidate IDs are ``content_hash[:12]`` so the cross-cycle Rasch fit
    can identify the same JSP across cycles, sessions, and forks. Error
    rows and rows missing ``sample_id`` must be dropped before fitting."""
    long_hash = "abcdef0123456789aaaa"
    _seed_cross_cycle(
        aggregator_archive,
        run_id="run_1",
        content_hash=long_hash,
        items=[
            _cross_cycle_item(1, hit=True),
            _cross_cycle_item(2, hit=False),
            _cross_cycle_item(3, hit=False, error=True),  # dropped: error row
            {"hit": True},  # dropped: missing sample_id
        ],
    )
    stores = SimpleNamespace(archive=aggregator_archive)
    # dataset_name=None bypasses the dataset filter (forensic mode) so the
    # test's unstamped fixture entries are still visible. Per-dataset
    # scoping is covered in tests/test_archive_dataset_scoping.py.
    obs = build_archive_observations(stores, "bk", dataset_name=None)
    assert len(obs) == 2
    assert all(o.candidate_id == long_hash[:12] for o in obs)
    assert {o.sample_id for o in obs} == {1, 2}


# ===========================================================================
# Zero-signal sample filter
# ===========================================================================


def _seed_axes(hits: dict[str, list[bool]]) -> AxisIndex:
    """Seed an AxisIndex (via SampleIndex) with synthetic hit histories."""
    idx = SampleIndex()
    for i, (query, seq) in enumerate(hits.items()):
        sample = Sample(id=i, query=query, ground_truth=f"gt_{i}")
        idx.register(sample)
        idx._hits[i] = list(seq)
    return AxisIndex(sample_index=idx)


def test_dead_queries_respects_min_observations() -> None:
    axes = _seed_axes(
        {
            "fresh_miss": [False] * 3,  # always miss, only 3 obs → filtered out
            "proven_miss": [False] * 6,  # always miss, 6 obs → dead
            "proven_hit": [True] * 6,  # always hit, 6 obs → dead
            "variable": [True, False, True, False, True, True],  # 5/6 hit → alive
        }
    )

    dead = axes.sample_index.dead(min_observations=5)
    dead_qs = {r.query for r in dead}
    assert dead_qs == {"proven_miss", "proven_hit"}

    miss_only = axes.sample_index.dead(min_observations=5, include_always_hit=False)
    assert {r.query for r in miss_only} == {"proven_miss"}


def test_exclude_and_restore_dataset_items(tmp_path: Path) -> None:
    store = build_stores(tmp_path / "projects", datasets_root=tmp_path / "datasets")
    items = [
        {"query": "a", "ground_truth": "A"},
        {"query": "b", "ground_truth": "B"},
        {"query": "c", "ground_truth": "C"},
    ]
    store.backends.save_dataset("train", items)

    moved = store.backends.exclude_dataset_items(
        "train",
        [{"query": "b", "reason": "zero_signal", "hit_rate": 0.0, "observations": 7}],
    )
    assert moved == 1

    data = store.backends.load_dataset("train")
    assert data is not None
    assert [d["query"] for d in data["items"]] == ["a", "c"]
    assert len(data["excluded"]) == 1
    assert data["excluded"][0]["item"]["query"] == "b"
    assert data["excluded"][0]["reason"] == "zero_signal"

    restored = store.backends.restore_dataset_items("train")
    assert restored == 1
    data = store.backends.load_dataset("train")
    assert data is not None
    assert {d["query"] for d in data["items"]} == {"a", "b", "c"}
    assert data["excluded"] == []


# ===========================================================================
# Adaptive picker — 1PL Rasch CAT with one blended decision-led objective
# ===========================================================================


def test_update_theta_posterior_hits_raise_mean_misses_lower_it() -> None:
    """One observation moves μ correctly — HIT → up, MISS → down — and a
    poorly-characterized sample (large se_δ) moves it less.

    Statistical correctness of the closed-form Newton update at the prior
    mean. Variance shrinks on every observation (info adds, so posterior
    precision grows); a large se_δ damps both the score and the information,
    so an outcome there cannot be told from a hard-sample miss and updates
    θ_c less than a well-known sample's outcome would."""
    from promptpotter.application.intelligence.adaptive_picker import (
        update_theta_posterior,
    )

    mu0, var0 = 0.0, 1.0
    mu_hit, var_hit = update_theta_posterior(mu0, var0, 0.0, 0.0, hit=True)
    mu_miss, var_miss = update_theta_posterior(mu0, var0, 0.0, 0.0, hit=False)

    assert mu_hit > mu0, "HIT must raise posterior mean above prior"
    assert mu_miss < mu0, "MISS must lower posterior mean below prior"
    assert mu_hit == pytest.approx(-mu_miss, abs=1e-9), (
        "symmetric prior + δ=μ → symmetric Newton step"
    )
    assert var_hit < var0 and var_miss < var0, "any observation must shrink posterior variance"

    # A barely-measured sample (large se_δ) is a noisier instrument.
    mu_hit_uncertain, _ = update_theta_posterior(mu0, var0, 0.0, 2.0, hit=True)
    assert 0.0 < mu_hit_uncertain < mu_hit, "large se_δ damps the ability update"


def test_decision_information_gain_peaks_at_candidate_ability() -> None:
    """The decision objective scores a measurement by how much it moves the
    keep/abort verdict ``θ_c > θ_s``. Since only the candidate is measured,
    the most decision-informative sample sits near the candidate's own
    ability — where its outcome is most uncertain and most shifts θ_c — not
    out where the candidate always hits or always misses."""
    from promptpotter.application.intelligence.adaptive_picker import (
        decision_information_gain,
    )

    # Contestable verdict: candidate μ_c=0, seed μ_s=1. δ near μ_c vs flanks.
    near = decision_information_gain(0.0, 1.0, mu_s=1.0, var_s=1.0, delta_s=0.0, se_delta_s=0.1)
    far_easy = decision_information_gain(
        0.0, 1.0, mu_s=1.0, var_s=1.0, delta_s=-4.0, se_delta_s=0.1
    )
    far_hard = decision_information_gain(0.0, 1.0, mu_s=1.0, var_s=1.0, delta_s=4.0, se_delta_s=0.1)
    assert near > far_easy, "a sample at the candidate's ability beats a too-easy one"
    assert near > far_hard, "a sample at the candidate's ability beats a too-hard one"


# ===========================================================================
# Hard-sample sorter — pick_score artifact
# ===========================================================================


def test_pick_score_artifact_ranks_contested_above_settled() -> None:
    """Artifact ``pick_score.per_sample`` carries the picker's pick-value for a
    brand-new candidate ``N(θ_seed, σ_θ²)`` — a fresh mutation of the best
    fitted candidate, centred on its ability.

    Construction: sample 1 measured by every candidate, all HIT (settled,
    easy — a fresh candidate's outcome there is predictable, low decision
    value); sample 2 measured by every candidate, split (settled, contestable).
    The contestable sample outranks the settled all-HIT one, which tails the
    pick order."""
    from promptpotter.application.intelligence.exploration import Observation
    from promptpotter.application.intelligence.hard_sample_sorter import (
        ARTIFACT_SCHEMA_VERSION,
        build_hard_samples_artifact_from_observations,
    )

    obs = [
        # Sample 1: every candidate HITs — settled easy, decision-poor.
        *[Observation(candidate_id=c, sample_id=1, hit=True) for c in "abcd"],
        # Sample 2: split — settled, contestable for a fresh candidate.
        Observation(candidate_id="a", sample_id=2, hit=True),
        Observation(candidate_id="b", sample_id=2, hit=False),
        Observation(candidate_id="c", sample_id=2, hit=True),
        Observation(candidate_id="d", sample_id=2, hit=False),
    ]
    artifact = build_hard_samples_artifact_from_observations(obs)
    assert artifact["schema_version"] == ARTIFACT_SCHEMA_VERSION == 3

    per_sample = artifact["pick_score"]["per_sample"]
    assert per_sample["2"] > per_sample["1"], "contestable sample beats settled-easy"
    assert artifact["pick_score"]["sample_order"][-1] == 1

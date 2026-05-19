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
# Adaptive picker — 1PL Rasch CAT with Fisher-info objective (Phase A)
# ===========================================================================


def test_update_theta_posterior_hits_raise_mean_misses_lower_it() -> None:
    """One observation moves μ in the correct direction: HIT → up, MISS → down.

    Statistical correctness of the closed-form Newton update at the
    prior mean. Variance shrinks on every observation (info adds, so
    posterior precision grows)."""
    from promptpotter.application.intelligence.adaptive_picker import (
        update_theta_posterior,
    )

    mu0, var0 = 0.0, 1.0
    mu_hit, var_hit = update_theta_posterior(mu0, var0, delta_s=0.0, hit=True)
    mu_miss, var_miss = update_theta_posterior(mu0, var0, delta_s=0.0, hit=False)

    assert mu_hit > mu0, "HIT must raise posterior mean above prior"
    assert mu_miss < mu0, "MISS must lower posterior mean below prior"
    assert mu_hit == pytest.approx(-mu_miss, abs=1e-9), (
        "symmetric prior + δ=μ → symmetric Newton step"
    )
    assert var_hit < var0 and var_miss < var0, "any observation must shrink posterior variance"


def test_fisher_info_peaks_at_delta_equals_theta() -> None:
    """1PL Fisher info ``p(1-p)`` peaks at δ = μ_c and falls off symmetrically.

    This is the "capable region" centred on the candidate's currently-
    estimated ability — what the picker targets."""
    from promptpotter.application.intelligence.adaptive_picker import fisher_info

    peak = fisher_info(mu_c=0.5, delta_s=0.5)
    assert peak == pytest.approx(0.25)
    # Symmetric falloff around the peak.
    left = fisher_info(mu_c=0.5, delta_s=-1.5)
    right = fisher_info(mu_c=0.5, delta_s=2.5)
    assert left == pytest.approx(right, abs=1e-9)
    assert left < peak and right < peak


def test_next_sample_mfi_adapts_to_outcomes() -> None:
    """The picker shifts toward harder samples after a HIT and easier
    samples after a MISS — the online adaptive contract.

    Prior μ=0 on three samples at δ = {−1, 0, +1}:
    - First pick: δ=0 (Fisher info peak at prior θ̂).
    - After HIT: μ moves positive → next argmax shifts to δ=+1.
    - After MISS: μ moves negative → next argmax shifts to δ=−1.
    """
    from promptpotter.application.intelligence.adaptive_picker import (
        next_sample_mfi,
        update_theta_posterior,
    )

    delta_map = {1: -1.0, 2: 0.0, 3: 1.0}

    first = next_sample_mfi(mu_c=0.0, delta_map=delta_map, remaining={1, 2, 3})
    assert first == 2, "prior μ=0 → δ=0 (sample 2) is the Fisher info peak"

    # HIT on sample 2 → μ moves positive → next argmax should be sample 3 (δ=+1)
    mu_after_hit, _ = update_theta_posterior(0.0, 1.0, delta_s=0.0, hit=True)
    next_after_hit = next_sample_mfi(mu_c=mu_after_hit, delta_map=delta_map, remaining={1, 3})
    assert next_after_hit == 3, "after HIT, picker reaches for the harder sample"

    # MISS on sample 2 → μ moves negative → next argmax should be sample 1 (δ=-1)
    mu_after_miss, _ = update_theta_posterior(0.0, 1.0, delta_s=0.0, hit=False)
    next_after_miss = next_sample_mfi(mu_c=mu_after_miss, delta_map=delta_map, remaining={1, 3})
    assert next_after_miss == 1, "after MISS, picker reaches for the easier sample"


# ===========================================================================
# Adaptive picker — Track-and-Stop objective (Phase B)
# ===========================================================================


def test_chernoff_info_pair_peaks_in_the_decision_gap() -> None:
    """Chernoff info between candidate-θ and seed-θ predictive Bernoullis peaks
    where δ_s sits in the gap between μ_c and μ_s — the sample whose outcome
    most pushes the keep/abort decision."""
    from promptpotter.application.intelligence.adaptive_picker import (
        chernoff_info_pair,
    )

    # Candidate weaker than seed: μ_c=0, μ_s=2. Gap is δ ≈ 1.
    in_gap = chernoff_info_pair(mu_c=0.0, mu_s=2.0, delta_s=1.0)
    below_gap = chernoff_info_pair(mu_c=0.0, mu_s=2.0, delta_s=-1.0)
    above_gap = chernoff_info_pair(mu_c=0.0, mu_s=2.0, delta_s=3.0)

    assert in_gap > below_gap, "decision-gap sample beats below-gap easy sample"
    assert in_gap > above_gap, "decision-gap sample beats above-gap hard sample"
    # Equal-θ pair: zero info (no discrimination possible).
    assert chernoff_info_pair(mu_c=1.0, mu_s=1.0, delta_s=0.5) == pytest.approx(0.0)


def test_track_and_stop_picks_decision_gap_first() -> None:
    """T&S picks the sample whose outcome maximally separates candidate from
    seed. Loser candidate (μ_c < μ_s): picks the sample where the seed
    confidently hits but the candidate is uncertain — that's where one
    candidate-MISS reveals the gap fastest."""
    from promptpotter.application.intelligence.adaptive_picker import (
        next_sample_track_and_stop,
    )

    # μ_c = 0 (broad), μ_s = 2. δ map: easy (-2), gap (1), hard (4).
    delta_map = {1: -2.0, 2: 1.0, 3: 4.0}
    pick = next_sample_track_and_stop(mu_c=0.0, mu_s=2.0, delta_map=delta_map, remaining={1, 2, 3})
    assert pick == 2, "decision-gap sample (δ=1) wins over both flanks"


def test_track_and_stop_accumulates_decision_evidence_faster_than_mfi() -> None:
    """T&S accumulates log-likelihood-ratio evidence between hypotheses (θ_c, θ_s)
    faster than MFI on the SAME measurement budget. MFI optimizes for θ_c-
    variance reduction; T&S directly optimizes for the keep/abort decision, so
    its samples drive the SPRT statistic away from zero per query.

    Simulates 10 queries on each picker against a clear-loser candidate
    (θ_c = -2) vs a winning seed (θ_s = +2), averaged across seeds. The
    LLR ``Σ log p_c(y) - log p_s(y)`` reaches a larger absolute magnitude
    under T&S — the picker is mechanically aligned with the decision."""
    import math
    import random

    from promptpotter.application.intelligence.adaptive_picker import (
        next_sample_mfi,
        next_sample_track_and_stop,
        posterior_from_outcomes,
        predicted_hit_probability,
    )

    delta_map = {i: -3.0 + 6.0 * (i / 19.0) for i in range(20)}  # spread δ from -3 to +3
    true_mu_c = -2.0
    true_mu_s = 2.0

    PRIOR_MU = 0.0
    PRIOR_VAR = 1.0
    BUDGET = 10

    def llr_after_budget(objective: str, seed: int) -> float:
        """SPRT log-likelihood-ratio between H_c (true loser) and H_s (true winner)
        after ``BUDGET`` measurements selected by ``objective``. Larger
        |LLR| means each measurement carried more decision evidence."""
        rng = random.Random(seed)
        scored: dict[int, bool] = {}
        llr = 0.0
        for _ in range(BUDGET):
            mu_c, _ = posterior_from_outcomes(
                PRIOR_MU,
                PRIOR_VAR,
                ((delta_map[s], h) for s, h in scored.items()),
            )
            remaining = set(delta_map) - scored.keys()
            if objective == "track_and_stop":
                pick = next_sample_track_and_stop(mu_c, true_mu_s, delta_map, remaining)
            else:
                pick = next_sample_mfi(mu_c, delta_map, remaining)
            assert pick is not None
            d = delta_map[pick]
            p_true = predicted_hit_probability(true_mu_c, d)
            hit = rng.random() < p_true
            scored[pick] = hit
            p_c = predicted_hit_probability(true_mu_c, d)
            p_s = predicted_hit_probability(true_mu_s, d)
            llr += math.log(p_c if hit else 1 - p_c) - math.log(p_s if hit else 1 - p_s)
        return llr

    seeds = list(range(16))
    mfi_llr = sum(llr_after_budget("mfi", s) for s in seeds) / len(seeds)
    ts_llr = sum(llr_after_budget("track_and_stop", s) for s in seeds) / len(seeds)
    # Loser case → both LLRs are negative (evidence favours H_c being loser).
    # T&S accumulates more negative LLR per query → stronger decision evidence.
    assert ts_llr < mfi_llr, (
        f"T&S must accumulate more decision evidence per query against MFI. "
        f"Got T&S LLR={ts_llr:.2f}, MFI LLR={mfi_llr:.2f} (more negative = stronger)."
    )


# ===========================================================================
# Hard-sample sorter — pick_score artifact (descriptive snapshot)
# ===========================================================================


def test_pick_score_artifact_carries_fisher_info_under_population_prior() -> None:
    """Artifact ``pick_score.per_sample`` carries 1PL Fisher info at θ=0
    (the Rasch identifiability anchor / population-mean ability). This
    is the snapshot consumed by the webapp; the live picker recomputes
    against the candidate's running θ̂_c posterior, so the artifact is
    descriptive, not the live iteration order.

    Samples with δ near 0 (mid-difficulty for the population) score
    high; both extreme-easy and extreme-hard samples tail the order
    symmetrically because Fisher info is the variance of a Bernoulli."""
    from promptpotter.application.intelligence.exploration import Observation
    from promptpotter.application.intelligence.hard_sample_sorter import (
        ARTIFACT_SCHEMA_VERSION,
        build_hard_samples_artifact_from_observations,
    )

    obs = [
        # Sample 1: all candidates HIT — easy → δ very negative → low Fisher
        *[Observation(candidate_id=c, sample_id=1, hit=True) for c in "abcd"],
        # Sample 2: split — mid difficulty → δ ≈ 0 → high Fisher
        Observation(candidate_id="a", sample_id=2, hit=True),
        Observation(candidate_id="b", sample_id=2, hit=False),
        Observation(candidate_id="c", sample_id=2, hit=True),
        Observation(candidate_id="d", sample_id=2, hit=False),
        # Sample 3: all candidates MISS — hard → δ very positive → low Fisher
        *[Observation(candidate_id=c, sample_id=3, hit=False) for c in "abcd"],
    ]
    artifact = build_hard_samples_artifact_from_observations(obs)
    assert artifact["schema_version"] == ARTIFACT_SCHEMA_VERSION == 3

    per_sample = artifact["pick_score"]["per_sample"]
    assert per_sample["2"] > per_sample["1"], "mid-difficulty leads easy"
    assert per_sample["2"] > per_sample["3"], "mid-difficulty leads hard"
    # Both extremes tail the order; sample 2 leads.
    assert artifact["pick_score"]["sample_order"][0] == 2
    assert artifact["pick_score"]["sample_order"][-1] in {1, 3}

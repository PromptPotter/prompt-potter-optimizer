from __future__ import annotations

import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from itertools import combinations, pairwise
from typing import TYPE_CHECKING, Any

from promptpotter.application.intelligence.indexes.sample import SampleIndex
from promptpotter.application.scoring.formula import ScoringTermMissingError, rescore_results
from promptpotter.domain.measurement_provenance import entry_grade
from promptpotter.domain.rendering import display_fitness
from promptpotter.domain.scoring import Scorer
from promptpotter.domain.search_point import PARAM_FORBIDDEN_KEYS
from promptpotter.infrastructure.store import archive_views


def _is_forbidden_axis(axis: str) -> bool:
    _, _, param = axis.partition(".")
    return param in PARAM_FORBIDDEN_KEYS


if TYPE_CHECKING:
    from promptpotter.application.intelligence.indexes.sample import FailureCluster, SampleRecord
    from promptpotter.infrastructure.store.stores import Stores

logger = logging.getLogger(__name__)


NOISE_THRESHOLD = 0.02


def _value_preview(value: Any) -> str:
    s = str(value)
    return s[:80] if len(s) > 80 else s


def _fmt_axis_rankings(
    rankings: list[AxisImpact], peaked_axes: frozenset[str] | None = None
) -> str:
    """The top-axes line, with peakedness tagged INLINE. Split across two lines it let L1 read
    "highest effect ⇒ mutate" without ever seeing that the parent's value IS the measured peak."""
    peaked = peaked_axes or frozenset()
    parts: list[str] = []
    for a in rankings:
        base = f"{a.axis} (effect={a.effect_size:.3f}, {a.classification}"
        if a.axis in peaked:
            base += (
                ", PEAKED — do not mutate unless the critique names this axis "
                "or exploration_budget=wide rebut"
            )
        base += ")"
        parts.append(base)
    return "; ".join(parts)


def _fmt_clusters(clusters: list[FailureCluster], *, with_counts: bool) -> str:
    if with_counts:
        return "; ".join(
            f"{c.failure_mode} ({c.fraction:.0%}, {c.sample_count} samples)" for c in clusters
        )
    return "; ".join(f"{c.failure_mode} ({c.fraction:.0%})" for c in clusters)


def _fmt_bottleneck(bottleneck: dict[str, float] | None) -> str | None:
    if not bottleneck:
        return None
    return "; ".join(f"{step}: {frac:.0%}" for step, frac in bottleneck.items())


def _fmt_persistent_failures(persistent: list[SampleRecord]) -> str:
    intractable = [q for q in persistent if q.hit_rate == 0]
    chronic = [q for q in persistent if q.hit_rate > 0]
    parts: list[str] = []
    if intractable:
        parts.append(f"{len(intractable)} intractable (never hit in any config)")
    if chronic:
        parts.append(f"{len(chronic)} chronic (recently failing but hit_rate > 0)")
    return "; ".join(parts)


@dataclass
class ValueRecord:
    value_preview: str
    mean_accuracy: float
    sample_count: int


@dataclass
class AxisImpact:
    axis: str
    effect_size: float
    consistency: float
    classification: str
    top_values: list[ValueRecord] = field(default_factory=list)
    sample_count: int = 0


@dataclass
class RunRecord:
    run_id: str
    name: str
    accuracy: float
    composite: float
    total: int


def _collect(*items: tuple[str, str | None]) -> dict[str, str] | None:
    out = {k: v for k, v in items if v}
    return out or None


class AxisIndex:
    """Derived axis-keyed view over the MeasurementArchive (axis → value → [accuracy])."""

    def __init__(
        self,
        sample_index: SampleIndex | None = None,
    ) -> None:
        self.sample_index: SampleIndex = sample_index or SampleIndex()
        self._axis_values: dict[str, dict[str, list[float]]] = defaultdict(
            lambda: defaultdict(list),
        )
        self._axis_seen_runs: set[str] = set()
        # Archived runs this formula cannot score (they predate a term it names). Bound once,
        # read by BOTH halves of `refresh` — the per-sample ingest and the axis fold.
        self._unscoreable_runs: set[str] = set()
        self._axis_failure_group_deltas: dict[str, dict[str, float]] = {}
        self._top_runs: list[RunRecord] = []
        # Whether the persisted per-run fold was replayed instead of re-derived. Decides
        # append-vs-replace when this refresh writes back; `None` until the first refresh.
        self._fold_seeded: bool | None = None

    # ----- axis analytics -----

    def peaked_axes(self) -> frozenset[str]:
        return frozenset(
            axis for axis in self._axis_values if self._axis_value_trend(axis) == "peaked"
        )

    def axis_rankings(self) -> list[AxisImpact]:
        impacts = [
            i
            for axis, vals in self._axis_values.items()
            if not _is_forbidden_axis(axis) and (i := self._compute_axis_impact(axis, vals))
        ]
        return sorted(impacts, key=lambda a: -a.effect_size)

    def _exhausted_axes(self, min_values: int = 4, max_effect: float = 0.02) -> list[AxisImpact]:
        out = [
            i
            for axis, vals in self._axis_values.items()
            if not _is_forbidden_axis(axis)
            and len(vals) >= min_values
            and (i := self._compute_axis_impact(axis, vals))
            and i.effect_size <= max_effect
        ]
        return sorted(out, key=lambda a: a.effect_size)

    def _axis_value_trend(self, axis: str) -> str:
        pairs: list[tuple[float, float]] = []
        for v, accs in self._axis_values.get(axis, {}).items():
            if not accs:
                continue
            try:
                pairs.append((float(v), sum(accs) / len(accs)))
            except (ValueError, TypeError):
                return "non_numeric"
        if len(pairs) < 3:
            return "flat"
        means = [m for _, m in sorted(pairs)]
        deltas = [b - a for a, b in pairwise(means)]
        pos = sum(1 for d in deltas if d > NOISE_THRESHOLD)
        neg = sum(1 for d in deltas if d < -NOISE_THRESHOLD)
        if pos > len(deltas) * 0.6 and neg == 0:
            return "increasing"
        if neg > len(deltas) * 0.6 and pos == 0:
            return "decreasing"
        if pos > 0 and neg > 0:
            peak = means.index(max(means))
            if 0 < peak < len(means) - 1:
                return "peaked"
        return "flat"

    # ----- digest construction (single entry-point, layer-agnostic) -----

    def digest(self) -> dict[str, str] | None:
        """Layer-agnostic axis-keyed digest — one payload into every L1/L2/L3 prompt. Per-layer filtering,
        if it ever returns, lives in the renderers and not here."""
        rankings5 = self.axis_rankings()[:5]
        top_vals_str: str | None = None
        if rankings5:
            impact = self._compute_axis_impact(
                rankings5[0].axis, self._axis_values.get(rankings5[0].axis, {})
            )
            if impact and impact.top_values:
                top_vals_str = "; ".join(
                    f"{r.value_preview} (acc={r.mean_accuracy:.1%})" for r in impact.top_values[:2]
                )

        clusters = self.sample_index.failure_clusters(2)
        dead = self.sample_index.dead(include_always_hit=False)
        disc = self.sample_index.discriminating()
        persistent = self.sample_index.persistent_failures(min_streak=3)
        bottleneck = self.sample_index.bottleneck_distribution()
        exhausted = self._exhausted_axes()
        exhausted_str = (
            "; ".join(
                f"{a.axis} ({len(self._axis_values.get(a.axis, {}))} values tested, "
                f"effect={a.effect_size:.3f})"
                for a in exhausted[:5]
            )
            if exhausted
            else None
        )
        peaked = self.peaked_axes()

        fg_lines: list[str] = []
        for a in rankings5[:3]:
            corr = self._axis_failure_group_deltas.get(a.axis, {})
            if corr:
                parts = [
                    f"{m}: {d:+.0%}" for m, d in sorted(corr.items(), key=lambda x: -abs(x[1]))[:3]
                ]
                fg_lines.append(f"{a.axis} → {', '.join(parts)}")

        flips = self.sample_index.flips(limit=50) if rankings5 else []
        # Counted by sample_id, the cell's identity — `flips()` already detects them that
        # way. Bucketing by the raw query text merged two samples that phrase the same
        # question into one inflated volatility score. The text is the LABEL, not the key.
        flip_counts = Counter(f["sample_id"] for f in flips)
        flip_labels = {f["sample_id"]: str(f.get("query", "")) for f in flips}
        volatile = [
            (flip_labels.get(sid, ""), n) for sid, n in flip_counts.most_common(5) if n >= 2
        ]

        return _collect(
            ("axis_rankings", _fmt_axis_rankings(rankings5, peaked) if rankings5 else None),
            ("top_values", top_vals_str),
            ("failure_clusters", _fmt_clusters(clusters, with_counts=True) if clusters else None),
            ("dead_queries", f"{len(dead)} queries never hit" if dead else None),
            (
                "discriminating_queries",
                f"{len(disc)} queries vary across configs" if disc else None,
            ),
            ("bottleneck_distribution", _fmt_bottleneck(bottleneck)),
            (
                "persistent_failures",
                _fmt_persistent_failures(persistent) if persistent else None,
            ),
            ("failure_group_insights", "; ".join(fg_lines) if fg_lines else None),
            (
                "volatile_queries",
                "; ".join(f"{q[:50]} ({n} flips)" for q, n in volatile) if volatile else None,
            ),
            ("exhausted_axes", exhausted_str),
            ("improvement_attribution", self._format_recent_attributions(limit=3)),
        )

    def _format_recent_attributions(self, limit: int = 5) -> str | None:
        positive = [f for f in self.sample_index.all_flips() if f["new_hit"] and not f["old_hit"]]
        if not positive:
            return None
        recent = positive[-limit:]
        parts = [
            f"  Round {f['round']}: {f['query'][:50]} started hitting "
            f"after: {f['changes_description']}"
            for f in recent
        ]
        return f"{len(positive)} queries improved (last {len(recent)}):\n" + "\n".join(parts)

    # ----- failure-group correlation -----

    def _recompute_failure_group_correlations(self) -> None:
        clusters = self.sample_index.failure_clusters(5)
        if not clusters:
            self._axis_failure_group_deltas = {}
            return

        groups: dict[str, set[int]] = {}
        for cluster in clusters:
            mode = cluster.failure_mode
            sids: set[int] = set()
            for sid in self.sample_index.sample_ids():
                modes = self.sample_index.failure_modes(sid)
                if modes and Counter(modes).most_common(1)[0][0] == mode:
                    sids.add(sid)
            if sids:
                groups[mode] = sids

        if not groups:
            self._axis_failure_group_deltas = {}
            return

        def _hit_rate(sids: set[int]) -> float:
            rates = [sum(h) / len(h) for sid in sids if (h := self.sample_index.hits(sid))]
            return sum(rates) / len(sids) if sids else 0.0

        hit_rates = {name: _hit_rate(sids) for name, sids in groups.items()}

        new_deltas: dict[str, dict[str, float]] = {}
        for axis, values in self._axis_values.items():
            if len(values) < 2:
                continue
            impact = self._compute_axis_impact(axis, values)
            if not (impact and impact.effect_size > NOISE_THRESHOLD):
                continue
            for group_name, hit_rate in hit_rates.items():
                corr = impact.effect_size * (1 - hit_rate)
                if corr > 0.005:
                    new_deltas.setdefault(axis, {})[group_name] = round(corr, 4)

        self._axis_failure_group_deltas = new_deltas

    # ----- ingest / refresh -----

    def _seed_from_fold(self, stores: Stores, dataset_name: str | None, formula_key: str) -> bool:
        """Replay the persisted per-run fold; ``True`` if it was whole enough to trust. Validation is
        all-or-nothing so the replay ORDER matches ingest — ``persistent_failures`` reads a tail streak."""
        if not dataset_name:
            return False
        rows = archive_views.sample_fold_rows(stores, dataset_name=dataset_name)
        if not rows:
            return False

        signatures = archive_views.run_signatures(stores)
        for row in rows:
            run_id = row.get("run_id") or ""
            if row.get("fk") != formula_key:
                return False
            if list(signatures.get(run_id) or ()) != list(row.get("sig") or ()):
                return False

        for row in rows:
            run_id = row["run_id"]
            if row.get("unscoreable"):
                self._unscoreable_runs.add(run_id)
            else:
                self.sample_index.replay_row(row)
            self.sample_index.mark_seen(run_id)
        logger.debug("AxisIndex seeded %d run(s) from the persisted fold", len(rows))
        return True

    def refresh(
        self,
        stores: Stores,
        scorer: Scorer | None = None,
        scorer_id: str = "none",
        scorer_formula: str | None = None,
        *,
        dataset_name: str | None,
    ) -> None:
        """Incremental archive refresh, dataset-scoped. A row this formula cannot score is SKIPPED, counted
        and logged — never 0.0, which would poison the digest — and the skip binds BOTH halves below."""
        formula_key = f"{scorer_id}\x1f{scorer_formula or ''}"
        if self._fold_seeded is None:
            self._fold_seeded = self._seed_from_fold(stores, dataset_name, formula_key)

        # Captured BEFORE the details are read, never after: a run whose log grows between the
        # two must end up stamped with the OLDER signature, so the next process re-derives it.
        # Stamping the newer one would leave a fold that silently omits the rows it gained.
        signatures = archive_views.run_signatures(stores)

        added = 0
        skipped: list[str] = []
        folded: list[dict[str, Any]] = []
        for run_id, detail in archive_views.runs_since(
            stores, self.sample_index._seen_runs, dataset_name=dataset_name
        ):
            stamp = {"fk": formula_key, "sig": list(signatures.get(run_id) or ())}
            if scorer is not None:
                try:
                    rescore_results(
                        detail.get("measurements") or [], scorer, scorer_id, scorer_formula
                    )
                except ScoringTermMissingError as exc:
                    skipped.append(run_id)
                    self._unscoreable_runs.add(run_id)
                    self.sample_index.mark_seen(run_id)
                    folded.append({"run_id": run_id, "unscoreable": True, **stamp})
                    logger.warning(
                        "axis refresh: archived run %r is unscoreable under the active formula "
                        "— skipping it (it predates the current observation vocabulary). %s",
                        run_id,
                        exc,
                    )
                    continue
            folded.append({**self.sample_index.ingest_run(detail), **stamp})
            self.sample_index.mark_seen(run_id)
            added += 1

        # Replace rather than append whenever the seed was rejected: what this process just
        # derived IS the whole fold, and appending would leave the rejected rows in front of it.
        if dataset_name and (folded or not self._fold_seeded):
            archive_views.write_sample_fold(
                stores, dataset_name=dataset_name, rows=folded, append=self._fold_seeded
            )
            self._fold_seeded = True
        if skipped:
            logger.warning(
                "axis refresh: %d archived run(s) skipped as unscoreable under the active "
                "formula (%s) — the digest is built from the %d that scored.",
                len(skipped),
                ", ".join(skipped[:5]),
                added,
            )

        # Grade-C runs (incidental connector-retrieval short-circuits) are dropped here so the
        # cross-cycle digest the L1/L2/L3 prompts read reflects the deliberately-explored
        # datapoints, not whichever connector replayed most. Unscoreable runs are dropped for the
        # same reason: a fitness from a dead vocabulary is not comparable to one from this run's.
        all_entries: list[dict[str, Any]] = []
        for entry in archive_views.list_runs(stores, dataset_name=dataset_name):
            run_id = entry.get("run_id", "")
            if entry_grade(entry) == "C" or run_id in self._unscoreable_runs:
                continue
            all_entries.append(entry)
            if not run_id or run_id in self._axis_seen_runs:
                continue
            self._fold_entry(self._axis_values, entry)
            self._axis_seen_runs.add(run_id)
        self._recompute_failure_group_correlations()
        self._refresh_top_runs(all_entries)

        if added:
            logger.debug(
                "AxisIndex refreshed: %d new runs (total seen: %d)",
                added,
                len(self.sample_index._seen_runs),
            )

    def _refresh_top_runs(self, entries: list[dict[str, Any]], k: int = 10) -> None:
        """Top-K by (composite_fitness, accuracy) desc. Only the modal ``total`` count is kept: an 8/20
        composite is not comparable with a 20/20 one, and mixing them inflates the leaderboard."""
        from collections import Counter

        all_totals = [
            (entry.get("scores") or {}).get("total", 0)
            for entry in entries
            if (entry.get("scores") or {}).get("total", 0) > 0
        ]
        if not all_totals:
            self._top_runs = []
            return
        modal_total = Counter(all_totals).most_common(1)[0][0]

        # One run can have several archive entries (e.g. per-sample backfill rows);
        # collapse to the best record per run_id so the leaderboard never lists the
        # same run twice (wasted bytes + a misleading panel for L1/L2).
        best_by_run: dict[str, RunRecord] = {}
        for entry in entries:
            scores = entry.get("scores") or {}
            total = scores.get("total") or 0
            if total != modal_total:
                continue
            # An absence is not a measurement: a row that recorded no accuracy must not
            # enter the leaderboard as a 0% run (the rule `noise_floor.py` already states).
            if "accuracy" not in scores:
                continue
            accuracy = float(scores["accuracy"])
            run_id = entry.get("run_id", "")
            rec = RunRecord(
                run_id=run_id,
                name=entry.get("name", ""),
                accuracy=accuracy,
                composite=display_fitness(scores.get("composite_fitness"), accuracy),
                total=total,
            )
            prev = best_by_run.get(run_id)
            if prev is None or (rec.composite, rec.accuracy) > (prev.composite, prev.accuracy):
                best_by_run[run_id] = rec
        scored = sorted(best_by_run.values(), key=lambda r: (-r.composite, -r.accuracy))
        self._top_runs = scored[:k]

    def top_runs(self, k: int = 3) -> list[RunRecord]:
        return self._top_runs[:k]

    def record_flips_from_rounds(self, rounds: list[Any], round_num: int) -> None:
        if len(rounds) < 2 or not (rounds[-2].results and rounds[-1].results):
            return
        desc = (
            rounds[-1].candidate_scores[0].changes_description
            if rounds[-1].candidate_scores
            else ""
        )
        flips = self.sample_index.record_flips(
            round_num, desc, rounds[-2].results, rounds[-1].results
        )
        if flips:
            logger.debug("Round %d: %d query flips recorded", round_num, flips)

    @classmethod
    def ensure_for(
        cls,
        stores: Stores | None,
        scorer: Scorer | None = None,
        scorer_id: str = "none",
        scorer_formula: str | None = None,
        *,
        dataset_name: str | None,
    ) -> AxisIndex | None:
        if stores is None:
            return None
        idx = cls()
        idx.refresh(
            stores,
            scorer=scorer,
            scorer_id=scorer_id,
            scorer_formula=scorer_formula,
            dataset_name=dataset_name,
        )
        return idx

    # ----- helpers -----

    @staticmethod
    def _fold_entry(
        axis_values: dict[str, dict[str, list[float]]],
        entry: dict[str, Any],
    ) -> None:
        """Fold one entry into ``axis_values``. An entry with no accuracy is skipped, never folded as 0.0 —
        a fabricated arm manufactures ``effect_size`` against every real arm on the same axis."""
        scores = entry.get("scores") or {}
        if "accuracy" not in scores:
            return
        accuracy = float(scores["accuracy"])
        for node_name, node_config in (entry.get("pipeline_params") or {}).items():
            if isinstance(node_config, dict):
                for param, value in node_config.items():
                    axis = f"{node_name}.{param}" if node_name else param
                    axis_values[axis][_value_preview(value)].append(accuracy)
            else:
                axis_values[node_name][_value_preview(node_config)].append(accuracy)

    def _compute_axis_impact(
        self,
        axis: str,
        values: dict[str, list[float]],
    ) -> AxisImpact | None:
        records = [ValueRecord(v, sum(a) / len(a), len(a)) for v, a in values.items() if a]
        records.sort(key=lambda r: -r.mean_accuracy)
        means = [r.mean_accuracy for r in records]
        total = sum(r.sample_count for r in records)

        if len(means) < 2:
            return AxisImpact(axis, 0.0, 0.0, "dead", sample_count=total)

        deltas = [abs(a - b) for a, b in combinations(means, 2)]
        effect = sum(deltas) / len(deltas)
        consistency = sum(1 for d in deltas if d > NOISE_THRESHOLD) / len(deltas)
        cls = (
            "consistently_impactful"
            if consistency >= 0.7
            else "sometimes_impactful"
            if consistency >= 0.3
            else "dead"
        )
        return AxisImpact(
            axis=axis,
            effect_size=round(effect, 4),
            consistency=round(consistency, 4),
            classification=cls,
            top_values=records[:5],
            sample_count=total,
        )


__all__ = ["NOISE_THRESHOLD", "AxisIndex"]

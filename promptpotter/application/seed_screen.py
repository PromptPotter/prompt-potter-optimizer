"""Seed-screen use-case — which candidate inner-bank draws are fit to sit on the L4 panel.

Not a cycle/fork/sweep and not a loop mechanism: no config field, no L1 injection, no
``detect_invariants`` exemption. A fenced, Claude-Code-invoked debug diagnostic, idiomatic to
``verify`` / ``noise_floor``, and the loop never learns it exists.

**It answers ONE question: does this bank reward a candidate for giving up?** A bank whose
constant-answer floor sits above its origin accuracy pays a candidate that stops reasoning and
hedges to a single label MORE than the incumbent — so the cell buys the exact collapse the loop
exists to prevent, and it buys it every round. That is a disqualifier, not a score to weigh, and
it is why this module exists: it retired ``justlogic-d234``'s seed-1 (floor 0.550, exact, against
origin reads of 0.525 and 0.500 — below on both).

**The floor is EXACT; the origin is MEASURED. Only one side of that comparison has an error
bar, and it is the side that decides.** At 40 rows one origin pass carries ~0.08 SE, which is
larger than any margin worth arguing about, so a verdict from a single read is a coin toss
wearing three decimals. This screen learned that the expensive way: it reported seed-5 as
rewarding collapse (floor 0.450 vs origin 0.425) and a second independent read returned 0.525 —
the two straddle the floor, mean +0.025 ABOVE it. **The seed-5 collapse verdict was never
settled and should not be cited.** (Its retirement stands on separate and much stronger evidence:
11 of 40 rows carrying no information, and a leave-one-out that took the outer panel's residual
from 0.134 to 0.100.) Hence ``repeat`` defaults above 1, ``verdict_settled``, and a CLI that
names a suspected bank rather than rejecting it.

**The raw floor is the wrong reading — the GAP is.** seed-4 draws a HIGHER floor (0.425) than
seed-5 and does not reward collapse at all. It was retired anyway, and on the better criterion:
over 6 settled passes its margin is **+0.021**, about ONE row in 40. A bank that pays a single
row for reasoning over hedging is not measuring reasoning, whichever side of its floor it lands
on. So compare banks on ``reasoning_margin``, never on accuracy, and read a thin positive margin
as the disqualifier it is.

**What it deliberately does NOT try to measure, and why.** The number a panel cell is really
worth is its INFORMATIVE width — rows that are neither impossible nor free, since only those can
separate two optimizer prompts. The obvious estimate — score each bank a second time with the
strongest prompt in the archive and count the disagreements — cannot work here, so this module
does not attempt it:

- A fresh bank shares only ~14 of 40 rows with everything ever measured, so the width cannot be
  inherited from history.
- One extra probe cannot substitute for that history. The best MATCHED lift ever banked on
  ``justlogic-d234`` is +0.143 — four rows in 28 — so even a correctly chosen probe separates
  3-6 rows in 40, and between-seed differences in that are +/-2 rows of noise.
- The audit that DOES resolve it (11 dead rows on seed-5, 0 on seed-2) pools 40-90 measurements
  per row across dozens of different prompts accumulated over a whole campaign. That is a
  by-product of running the panel, not something a screen can buy in advance.

So the screen measures only what repeated origin passes can honestly support, and the
informative width stays an after-the-fact read off the measurement archive.

Persistence mirrors ``application/verify.py`` / ``noise_floor.py``: workspace ``archive/``
sidecar only, no ledger event.
"""

from __future__ import annotations

import collections
import logging
import random
import statistics
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from promptpotter.domain.scoring import is_hit, modal_answer_share
from promptpotter.shared.clock import utcnow_iso

if TYPE_CHECKING:
    from promptpotter.domain.sample import Sample
    from promptpotter.infrastructure.store.stores import Stores
    from promptpotter.shared.identity import IdentityContext

logger = logging.getLogger(__name__)

__all__ = [
    "SeedReading",
    "SeedScreenError",
    "SeedScreenOutcome",
    "class_floor",
    "draw_bank",
    "screen_inner_seeds",
]


class SeedScreenError(Exception):
    """A resolved-state failure: the dataset or its rows are missing, or a bank scored nothing.
    The CLI shell maps this to a clean ``SystemExit``."""


@dataclass(frozen=True)
class SeedReading:
    """One candidate seed's bank, measured — ``origin_reads`` independent passes over it.

    The verdict below is a comparison of two numbers, and ONE origin pass carries SE ~0.08 at 40
    rows, so a bank sitting near its floor cannot be called from a single read. Measured: seed-4
    read 0.525 and then 0.425 against a floor of 0.425 — sound, then exactly on the line, same
    rows and same prompt. That is why repeats exist and why ``origin_spread`` is reported beside
    the mean rather than left for a reader to wonder about.
    """

    seed: int
    n: int
    class_floor: float
    origin_reads: tuple[float, ...]
    # What the passes above COST to take, in the two axes an operator ranks before quality
    # when choosing a target model. Both are already on every row this screen scored
    # (``step_timings`` + the wire ``step_tokens[*].cost_usd``), so reporting them adds no
    # call and no store — and without them the tool spends real money on the exact
    # comparison it cannot answer. One per backend call, across every pass.
    latencies: tuple[float, ...] = ()
    cost_usd: float = 0.0
    # How much of the model's OWN answering went to one label, over every pass
    # (``domain/scoring.py::modal_answer_share``); ``None`` where the answer space makes the
    # question meaningless. The bank-side twin of ``class_floor``: that says how much a
    # constant answer would SCORE here, this says how nearly the model IS one. A candidate
    # model can clear the floor while hedging almost everything — measured, the incumbent
    # answered one label 95% of the time for +0.05 over a 0.400 floor — and a screen that
    # ranks models has to show the operator which kind of margin they are buying.
    answer_modal_share: float | None = None

    @property
    def origin_accuracy(self) -> float:
        """Mean over the independent passes — what the verdict reads."""
        return sum(self.origin_reads) / len(self.origin_reads)

    @property
    def latency_median(self) -> float:
        """Median per-call wall-clock. The median, not the mean, is the route's normal
        speed: a cold first call and a provider stall both land in the tail, and one 92 s
        outlier in 20 moves a mean by more than it tells you."""
        return statistics.median(self.latencies) if self.latencies else 0.0

    @property
    def latency_mean(self) -> float:
        """Mean per-call wall-clock. Reported BESIDE the median rather than instead of it:
        the gap between the two IS the instability signal."""
        return sum(self.latencies) / len(self.latencies) if self.latencies else 0.0

    @property
    def cost_per_pass(self) -> float:
        """Wire cost of one pass over this bank — what a comparison between models is
        actually denominated in."""
        return self.cost_usd / len(self.origin_reads) if self.origin_reads else 0.0

    @property
    def origin_spread(self) -> float:
        """Max − min across passes. Zero on a single read, which is itself the warning."""
        return max(self.origin_reads) - min(self.origin_reads)

    @property
    def margin_se(self) -> float:
        """Standard error of the mean origin — how firmly the verdict can be held.

        Estimated from the passes themselves when there are enough to say (>=3), else from the
        binomial at the measured rate, which is the honest fallback rather than reporting 0.0
        for a single read.
        """
        k = len(self.origin_reads)
        if k >= 3:
            mean = self.origin_accuracy
            var = sum((x - mean) ** 2 for x in self.origin_reads) / (k - 1)
            return float((var / k) ** 0.5)
        p = self.origin_accuracy
        return float((p * (1 - p) / self.n) ** 0.5 / (k**0.5))

    @property
    def verdict_settled(self) -> bool:
        """Is the collapse verdict further from the line than its own error bar (2 SE)?"""
        return abs(self.reasoning_margin) > 2 * self.margin_se

    @property
    def rewards_collapse(self) -> bool:
        """Does answering ONE label to every row beat the origin here? A DISQUALIFIER.

        A bool rather than a number to weigh, and the asymmetry is the reason: below the origin
        a skewed bank only distorts how hard the bank LOOKS, and that distortion cancels anyway
        — arms are compared within a seed against a matched origin. Above the origin it stops
        being cosmetic, because a candidate that gives up now outscores the incumbent.
        """
        return self.class_floor > self.origin_accuracy

    @property
    def reasoning_margin(self) -> float:
        """Origin accuracy above what collapse would score — the bank's real difficulty.

        Raw accuracy conflates "this bank is easy" with "this bank's majority class is large".
        Two panel seeds scored an identical 0.525 while one handed 0.350 of it to a constant
        answerer and the other 0.425; in real reasoning they are 0.175 and 0.100. Compare banks
        on this, never on accuracy.
        """
        return self.origin_accuracy - self.class_floor

    def as_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "n": self.n,
            "class_floor": self.class_floor,
            "origin_reads": list(self.origin_reads),
            "origin_accuracy": self.origin_accuracy,
            "origin_spread": self.origin_spread,
            "margin_se": self.margin_se,
            "reasoning_margin": self.reasoning_margin,
            "answer_modal_share": self.answer_modal_share,
            "rewards_collapse": self.rewards_collapse,
            "verdict_settled": self.verdict_settled,
            "latency_median": self.latency_median,
            "latency_mean": self.latency_mean,
            "cost_usd": self.cost_usd,
            "cost_per_pass": self.cost_per_pass,
        }


@dataclass(frozen=True)
class SeedScreenOutcome:
    dataset_name: str
    readings: list[SeedReading]
    artifact_path: str


def class_floor(bank: list[Sample]) -> float:
    """What answering ONE label to every row would score on this bank.

    Free — no measurement — and the only screen axis that needs none, which is why a candidate
    sweep filters on it before spending a call on anything.
    """
    counts = collections.Counter(str(s.ground_truth) for s in bank)
    return max(counts.values()) / len(bank) if bank else 0.0


def _call_cost_and_latency(rows: Sequence[Mapping[str, Any]]) -> tuple[list[float], float]:
    """``(per-call latencies, summed wire cost)`` for one pass's rows.

    Latency sums ``step_timings`` rather than reading ``total_time``: the per-node timings
    are what the backend actually measured for each LLM call, they cover a multi-node
    pipeline the same way they cover a single-node one, and ``total_time`` reads 0.0 on a
    row served from the measurement cache. Cost is the provider's own ``cost_usd`` off the
    wire — never a rate-table estimate — so a row that carried none contributes nothing
    rather than a guess.
    """
    latencies: list[float] = []
    cost = 0.0
    for row in rows:
        pd = row.get("pipeline_data") or {}
        seconds = sum(
            float(v) for v in (pd.get("step_timings") or {}).values() if isinstance(v, int | float)
        )
        if seconds > 0:
            latencies.append(seconds)
        for entry in (pd.get("step_tokens") or {}).values():
            if isinstance(entry, dict) and isinstance(entry.get("cost_usd"), int | float):
                cost += float(entry["cost_usd"])
    return latencies, cost


def draw_bank(all_samples: list[Sample], n: int, seed: int) -> list[Sample]:
    """One candidate seed's bank — the SAME draw ``runner/inner/cycle.py`` performs.

    Spelled once, here, and imported there: a screen that drew differently from the runner would
    be screening a bank nobody will ever run, and nothing would report the divergence.
    """
    return random.Random(seed).sample(all_samples, min(n, len(all_samples)))


async def screen_inner_seeds(
    *,
    stores: Stores,
    identity: IdentityContext,
    dataset_name: str,
    seeds: list[int],
    n_samples: int,
    repeat: int = 3,
    log: Callable[[str], None] | None = None,
) -> SeedScreenOutcome:
    """Score each candidate seed's bank with the dataset origin; report floor, margin, verdict.

    ``repeat`` takes that many INDEPENDENT passes per bank, and the default is NOT 1: the
    verdict compares an EXACT floor against a MEASURED origin carrying ~0.08 SE at 40 rows, so
    one pass cannot settle a bank near its line. A screen that cannot settle its own verdict is
    cheaper only in the sense that its answer is unusable — this one condemned seed-5 on a
    margin its own second read reversed. Above 1 the passes run ``force_fresh``,
    and they have to: the archive is content-addressed, so a second pass over the same rows with
    the same searchpoint replays the first and would report a spread of exactly zero — a
    fabricated certainty, which is worse than the single read it replaced.

    Cost is ``repeat x n_samples`` target calls per seed and NO optimizer calls — a screen is not
    a campaign. Each reading REPORTS its own wall-clock and wire cost (``latency_median`` /
    ``latency_mean`` / ``cost_per_pass``), so price a wide sweep off the last reading rather than
    off a figure written here (``<one-budget>``) — a hand-quoted rate goes stale the first time
    the target model or the route moves, and the prose cannot tell you that it has.
    """
    from promptpotter.application.config import configure_and_apply_pipeline, load_campaign_config
    from promptpotter.application.datasets.authored import (
        dataset_campaign_path,
        read_campaign_config_file,
    )
    from promptpotter.application.initialization.loop_start import populate_session_scoring
    from promptpotter.application.initialization.wiring import init_services
    from promptpotter.application.origin import resolve_origin_opt_search_point
    from promptpotter.application.scoring.formula import split_scoring_block
    from promptpotter.application.scoring.search_point_scorer import score_search_point
    from promptpotter.infrastructure.store.io import write_json

    log_fn = log or (lambda *_a, **_k: None)
    session = await init_services(dataset_name=dataset_name, identity=identity, stores=stores)
    all_samples = session.samples
    if not all_samples:
        raise SeedScreenError(f"dataset {dataset_name!r} loaded zero samples.")

    file_config: dict[str, Any] = {}
    if session.dataset_config_dir is not None:
        cfg_path = dataset_campaign_path(session.dataset_config_dir)
        if cfg_path.exists():
            file_config = read_campaign_config_file(cfg_path)
    campaign_config = load_campaign_config(file_config)
    pipeline_params = configure_and_apply_pipeline(session, campaign_config, log=log_fn)
    scoring_spec = split_scoring_block(campaign_config.scoring)
    populate_session_scoring(
        session,
        obs=None,
        scoring_formula=scoring_spec.per_sample,
        scoring_round_formula=scoring_spec.per_round,
        scorer_id=scoring_spec.scorer_id,
        source=f"seed_screen:{dataset_name}",
    )
    origin_sp = resolve_origin_opt_search_point(
        [session.llm_node_name()], session.dataset_config_dir
    ).to_job_search_point(pipeline_params, schema=session.pipeline_schema)

    readings: list[SeedReading] = []
    for seed in seeds:
        bank = draw_bank(all_samples, n_samples, seed)
        reads: list[float] = []
        latencies: list[float] = []
        all_rows: list[Mapping[str, Any]] = []
        cost_usd = 0.0
        n_scored = 0
        for i in range(max(1, repeat)):
            rows, _scores, _signal = await score_search_point(
                origin_sp,
                bank,
                session,
                # Distinct per pass: `force_fresh` truncates its run's detail log first, so a
                # shared label would have each pass overwrite the last.
                label=f"seed{seed}_origin_{i}",
                # A screen measures the BANK, not an individual's own report, so every seed sits
                # on the same vacuous fallback — otherwise the readings would partly carry
                # prompt length rather than the bank (`score_search_point`'s contract for
                # `opt_sp`).
                opt_sp=None,
                measured=None,
                # No per-sample callbacks, declared rather than defaulted: a screen has no live
                # display to report a row to.
                on_sample_scored=None,
                on_sample_starting=None,
                source=f"seed_screen:{dataset_name}:seed{seed}:{i}",
                force_fresh=repeat > 1,
            )
            # `is_hit` is the ONE definition of "this configuration solved the row" — re-deriving
            # it from predicted-vs-ground_truth would be a second answer to a question the scorer
            # owns, and the two would disagree the moment a dataset grades non-binary.
            scored = [is_hit(r.get("fitness")) for r in rows if r.get("sample_id") is not None]
            if not scored:
                raise SeedScreenError(
                    f"seed {seed} pass {i}: the origin pass returned no scoreable row — the "
                    "screen measured nothing and must not report a bank on that basis."
                )
            reads.append(sum(scored) / len(scored))
            n_scored = len(scored)
            pass_latencies, pass_cost = _call_cost_and_latency(rows)
            latencies += pass_latencies
            cost_usd += pass_cost
            all_rows += rows
        reading = SeedReading(
            seed=seed,
            n=n_scored,
            class_floor=class_floor(bank),
            origin_reads=tuple(reads),
            latencies=tuple(latencies),
            cost_usd=cost_usd,
            # Pooled across passes: hedging is a property of the model on this bank, and one
            # pass of 40 rows resolves a share no better than it resolves an accuracy.
            answer_modal_share=modal_answer_share(all_rows),
        )
        readings.append(reading)
        log_fn(
            f"seed {seed}: floor {reading.class_floor:.3f} origin {reading.origin_accuracy:.3f} "
            f"(spread {reading.origin_spread:.3f} over {len(reads)}) "
            f"margin {reading.reasoning_margin:+.3f} +/-{reading.margin_se:.3f} "
            f"lat {reading.latency_median:.1f}s/{reading.latency_mean:.1f}s "
            f"${reading.cost_per_pass:.4f}/pass"
            + (" REWARDS COLLAPSE" if reading.rewards_collapse else "")
        )

    # Workspace `archive/` sidecar, the same disk discipline `verify` and `noise_floor` use — no
    # ledger event, because a screen is not a run. Its own file rather than a
    # `DiagnosticRunRecord`: that model is shaped for a re-scored campaign origin (k, CI, a
    # source cycle), and bending seed readings into those fields would name them wrongly.
    path = stores.base_dir / "archive" / f"seed-screen-{dataset_name}-{utcnow_iso()[:10]}.json"
    write_json(
        path,
        {
            "ts": utcnow_iso(),
            "dataset": dataset_name,
            "n_samples": n_samples,
            "readings": [r.as_dict() for r in readings],
        },
    )
    logger.info("seed-screen: wrote %d reading(s) -> %s", len(readings), path)
    return SeedScreenOutcome(dataset_name=dataset_name, readings=readings, artifact_path=str(path))

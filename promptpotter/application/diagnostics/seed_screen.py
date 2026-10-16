from __future__ import annotations

import collections
import logging
import random
import re
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Literal

from promptpotter.application.bench.task_context import campaign_framing
from promptpotter.application.campaign_config import CampaignConfig
from promptpotter.application.datasets.authored import load_dataset_campaign_config
from promptpotter.application.initialization.loop_start import (
    arm_diagnostic_scoring,
    diagnostic_pass,
    diagnostic_trace,
)
from promptpotter.application.initialization.wiring import init_services
from promptpotter.application.jobs.launcher.admission import paid_pass
from promptpotter.application.origin import resolve_origin_opt_search_point
from promptpotter.application.scoring.search_point_scorer import score_search_point
from promptpotter.domain.measurement_provenance import RunSource
from promptpotter.domain.scoring import (
    all_verifier_graded,
    is_verifier_graded,
    modal_answer_share,
)
from promptpotter.domain.spend import SpendCeilings
from promptpotter.infrastructure.store.io import write_json
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import DiagnosticError

if TYPE_CHECKING:
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.scoring import GradedCell
    from promptpotter.infrastructure.store.stores import Stores
    from promptpotter.shared.identity import IdentityContext

logger = logging.getLogger(__name__)

__all__ = ["class_floor", "draw_bank", "draw_bench", "screen_inner_seeds"]


SeedVerdict = Literal["reject", "suspect", "unsettled", "pass", "no_floor"]


@dataclass(frozen=True)
class SeedReading:
    seed: int
    n: int
    # ``None`` where the bank carries no labels: a verifier-graded bank has no collapse half.
    class_floor: float | None
    origin_reads: tuple[float, ...]
    latencies: tuple[float, ...] = ()
    # ``None`` = the provider surfaced no wire price at all, which is not a price of zero.
    cost_usd: float | None = None
    # ``None`` where the answer space makes the question meaningless.
    answer_modal_share: float | None = None

    @property
    def origin_accuracy(self) -> float:
        return sum(self.origin_reads) / len(self.origin_reads)

    @property
    def latency_median(self) -> float | None:
        return statistics.median(self.latencies) if self.latencies else None

    @property
    def latency_mean(self) -> float | None:
        return sum(self.latencies) / len(self.latencies) if self.latencies else None

    @property
    def cost_per_pass(self) -> float | None:
        if self.cost_usd is None or not self.origin_reads:
            return None
        return self.cost_usd / len(self.origin_reads)

    @property
    def origin_spread(self) -> float:
        return max(self.origin_reads) - min(self.origin_reads)

    @property
    def margin_se(self) -> float:
        """Below 3 passes the binomial at the measured rate, never a 0.0 for a single read."""
        k = len(self.origin_reads)
        if k >= 3:
            mean = self.origin_accuracy
            var = sum((x - mean) ** 2 for x in self.origin_reads) / (k - 1)
            return float((var / k) ** 0.5)
        p = self.origin_accuracy
        return float((p * (1 - p) / self.n) ** 0.5 / (k**0.5))

    @property
    def verdict_settled(self) -> bool | None:
        margin = self.reasoning_margin
        return None if margin is None else abs(margin) > 2 * self.margin_se

    @property
    def rewards_collapse(self) -> bool | None:
        return None if self.class_floor is None else self.class_floor > self.origin_accuracy

    @property
    def reasoning_margin(self) -> float | None:
        return None if self.class_floor is None else self.origin_accuracy - self.class_floor

    @property
    def verdict(self) -> SeedVerdict:
        """A rejection needs a SETTLED margin: the floor is exact and the origin is one noisy read."""
        collapse, settled = self.rewards_collapse, self.verdict_settled
        if collapse is None or settled is None:
            return "no_floor"
        if collapse:
            return "reject" if settled else "suspect"
        return "pass" if settled else "unsettled"

    @property
    def rank_key(self) -> tuple[bool, bool, float]:
        margin = self.reasoning_margin
        return (
            self.rewards_collapse is not True,
            margin is None,
            0.0 if margin is None else -margin,
        )

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
            "verdict": self.verdict,
            "latency_median": self.latency_median,
            "latency_mean": self.latency_mean,
            "cost_usd": self.cost_usd,
            "cost_per_pass": self.cost_per_pass,
        }


@dataclass(frozen=True)
class SeedScreenOutcome:
    dataset_name: str
    # The solver pinned over the dataset's own, or ``None`` where the dataset's node config answered.
    solver: str | None
    readings: list[SeedReading]
    artifact_path: str


def class_floor(bank: list[Sample]) -> float | None:
    labels = [s.ground_truth for s in bank]
    if all_verifier_graded(labels):
        return None
    if any(is_verifier_graded(gt) for gt in labels):
        raise DiagnosticError(
            "this bank mixes labelled and verifier-graded rows, so a constant answer's score "
            "would be taken over the labelled part alone and read as the whole bank's floor."
        )
    counts = collections.Counter(str(gt) for gt in labels)
    return max(counts.values()) / len(bank) if bank else 0.0


def _call_cost_and_latency(cells: Sequence[GradedCell]) -> tuple[list[float], float | None]:
    """``step_timings``, not ``total_time``: that one reads 0.0 on a cache-served row."""
    latencies: list[float] = []
    cost: float | None = None
    for cell in cells:
        pd = cell.facts.pipeline
        seconds = sum(pd.step_timings.values())
        if seconds > 0:
            latencies.append(seconds)
        for usage in pd.step_tokens.values():
            if usage.cost_usd is not None:
                cost = (cost or 0.0) + usage.cost_usd
    return latencies, cost


def draw_bank(all_samples: list[Sample], n: int, seed: int) -> list[Sample]:
    """The draw ``runner/inner/spawn.py`` imports: a screen must draw the bank a run draws."""
    return random.Random(seed).sample(all_samples, min(n, len(all_samples)))


def draw_bench(all_samples: list[Sample], bank: list[Sample], n: int, seed: int) -> list[Sample]:
    """The held-out rows BESIDE a drawn bank, by content: the bank a screen passed stays the search pool byte for byte."""
    taken = {s.key for s in bank}
    rest = [s for s in all_samples if s.key not in taken]
    drawn = random.Random(seed).sample(rest, min(n, len(rest)))
    return [s.model_copy(update={"bench_only": True}) for s in drawn]


async def screen_inner_seeds(
    *,
    stores: Stores,
    identity: IdentityContext,
    dataset_name: str,
    seeds: list[int],
    n_samples: int,
    repeat: int = 3,
    parallel: int = 1,
    model: str | None = None,
    provider: str | None = None,
) -> SeedScreenOutcome:
    # Argument checks BEFORE anything is built: `init_services` spends a backend handshake.
    if parallel < 1:
        raise DiagnosticError(f"--parallel must be at least 1, got {parallel}.")
    if provider and not model:
        raise DiagnosticError("--provider is the gateway a --model is asked through; pass both.")
    async with paid_pass(stores=stores, verb="seed-screen", hop=None, declared=SpendCeilings()):
        return await _screen(
            stores, identity, dataset_name, seeds, n_samples, repeat, parallel, model, provider
        )


async def _screen(
    stores: Stores,
    identity: IdentityContext,
    dataset_name: str,
    seeds: list[int],
    n_samples: int,
    repeat: int,
    parallel: int,
    model: str | None,
    provider: str | None,
) -> SeedScreenOutcome:
    session = await init_services(dataset_name=dataset_name, identity=identity, stores=stores)
    all_samples = session.samples
    if not all_samples:
        raise DiagnosticError(f"dataset {dataset_name!r} loaded zero samples.")

    dataset_dir = None if session.dataset is None else session.dataset.directory
    campaign_config = (
        CampaignConfig.model_validate({})
        if dataset_dir is None
        else load_dataset_campaign_config(dataset_dir)
    )
    if model:
        # The ENVIRONMENT pin an L4 pool cell carries (`panel.tasks[].inner_model`), laid on the
        # dataset's own node config, so a (dataset, solver) pair is screened as its cell would run.
        llm_node = session.llm_node_name()
        po = dict(campaign_config.pipeline_overlay)
        po[llm_node] = {
            **po.get(llm_node, {}),
            "model": model,
            **({"provider": provider} if provider else {}),
        }
        campaign_config = campaign_config.model_copy(update={"pipeline_overlay": po})
    arm_diagnostic_scoring(session, campaign_config, source=RunSource.SEED_SCREEN)
    pipeline_params = session.pipeline_params
    # `run_walks` re-reads this at every launch boundary and clamps it to the backend's ceiling.
    session.control = replace(session.control, held_lookahead=parallel)
    ceiling = session.backend_client.max_cells_in_flight
    if parallel > ceiling:
        # A warning: a terminal shows this layer's info only under `--verbose`.
        logger.warning(
            "seed-screen: --parallel %d exceeds this backend's declared ceiling of %d "
            "(Connector.max_cells_in_flight); running at %d.",
            parallel,
            ceiling,
            ceiling,
        )
    # The run's C0 framing: the origin must be the prompt the campaign will actually score.
    origin = resolve_origin_opt_search_point(
        [session.llm_node_name()],
        dataset_dir,
        pipeline_params=pipeline_params,
        schema=session.pipeline_schema,
    )
    if session.pipeline_schema.prompt_node_names() and not any(origin.prompt_field_dict().values()):
        # A campaign's check-in writes the missing prompt; a screen has none, so a bare question
        # would be graded under no answer grammar and the matcher's misses read as the model's.
        raise DiagnosticError(
            f"dataset {dataset_name!r} declares no `{session.llm_node_name()}` prompt in its "
            "origin.yaml, so its origin is the bare question. Write one before screening."
        )
    origin_sp = origin.to_job_search_point(
        schema=session.pipeline_schema,
        framing=campaign_framing(stores, campaign_config, session.dataset),
        demo=session.scoring.require_partition().demo,
    )

    readings: list[SeedReading] = []
    for seed in seeds:
        bank = draw_bank(all_samples, n_samples, seed)
        # Refuses a mixed bank BEFORE the loop buys `repeat` passes over it.
        bank_floor = class_floor(bank)
        reads: list[float] = []
        latencies: list[float] = []
        all_rows: list[GradedCell] = []
        cost_usd: float | None = None
        n_scored = 0
        for i in range(max(1, repeat)):
            # A screen answers for no campaign, so its bills land on the workspace's ledger.
            with diagnostic_trace(stores, None):
                passed = await diagnostic_pass(
                    score_search_point(
                        origin_sp,
                        bank,
                        session,
                        # Distinct per pass: `force_fresh` truncates its run's detail log first.
                        label=f"seed{seed}_origin_{i}",
                        measured=None,
                        # A replay off the content-addressed archive would fabricate a zero spread.
                        force_fresh=repeat > 1,
                    ),
                )
            rows = passed.sheet.cells
            scored = [cell.hit for cell in rows]
            if not scored:
                raise DiagnosticError(
                    f"seed {seed} pass {i}: the origin pass returned no scoreable row — the "
                    "screen measured nothing and must not report a bank on that basis."
                )
            reads.append(sum(scored) / len(scored))
            n_scored = len(scored)
            pass_latencies, pass_cost = _call_cost_and_latency(rows)
            latencies += pass_latencies
            if pass_cost is not None:
                cost_usd = (cost_usd or 0.0) + pass_cost
            all_rows += rows
        reading = SeedReading(
            seed=seed,
            n=n_scored,
            class_floor=bank_floor,
            origin_reads=tuple(reads),
            latencies=tuple(latencies),
            cost_usd=cost_usd,
            answer_modal_share=modal_answer_share(all_rows),
        )
        readings.append(reading)
        margin = reading.reasoning_margin
        logger.info(
            f"seed {seed}: floor "
            f"{'--' if reading.class_floor is None else f'{reading.class_floor:.3f}'} "
            f"origin {reading.origin_accuracy:.3f} "
            f"(spread {reading.origin_spread:.3f} over {len(reads)}) "
            f"margin {'--' if margin is None else f'{margin:+.3f}'} +/-{reading.margin_se:.3f} "
            f"lat {'--' if reading.latency_median is None else f'{reading.latency_median:.1f}s'}"
            f"/{'--' if reading.latency_mean is None else f'{reading.latency_mean:.1f}s'} "
            f"{'--' if reading.cost_per_pass is None else f'${reading.cost_per_pass:.4f}'}/pass"
            + (" REWARDS COLLAPSE" if reading.rewards_collapse else "")
        )

    # No ledger event: a screen is not a run, nor a `DiagnosticRunRecord` (a re-scored origin).
    readings.sort(key=lambda r: r.rank_key)
    solver_tag = "" if model is None else "-" + re.sub(r"[^A-Za-z0-9.-]+", "-", model)
    path = stores.diagnostic_runs.sidecar_path(
        f"seed-screen-{dataset_name}{solver_tag}-{utcnow_iso()[:10]}.json"
    )
    write_json(
        path,
        {
            "ts": utcnow_iso(),
            "dataset": dataset_name,
            "solver": model,
            "provider": provider,
            "n_samples": n_samples,
            "readings": [r.as_dict() for r in readings],
        },
    )
    logger.info("seed-screen: wrote %d reading(s) -> %s", len(readings), path)
    return SeedScreenOutcome(
        dataset_name=dataset_name, solver=model, readings=readings, artifact_path=str(path)
    )

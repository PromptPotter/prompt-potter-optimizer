from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from promptpotter.application.campaign_config import CampaignConfig
from promptpotter.application.knobs import check_couplings
from promptpotter.application.optimization.dispatch.llm_call.prompts import optimizer_model
from promptpotter.domain.search_point import has_framing
from promptpotter.infrastructure.llm.registry import model_profile

if TYPE_CHECKING:
    from promptpotter.domain.sample import Sample


__all__ = [
    "PreflightWarning",
    "check_model_reasoning_floors",
    "run_preflight_checks",
]


@dataclass(frozen=True)
class PreflightWarning:
    code: str
    title: str
    detail: str


def _check_sp_budget_vs_dataset(
    config: CampaignConfig, dataset: list[Sample]
) -> PreflightWarning | None:
    # Only the PER-ROUND budget is checked against the bank. An origin budget above the
    # bank is not a misconfiguration: `sp_budget_origin` defaults ABOVE `sp_budget_ttest`
    # (DEFAULT_ORIGIN_BUDGET), `sample_dataset` is a prefix slice, and "score the origin
    # on everything there is" is exactly what a wide-origin default wants on a small bank.
    # Warning on it told every small-bank dataset to lower a knob nobody set, on every run.
    # `sp_budget_ttest > bank` IS a real finding — it means the adaptive queue mechanism
    # has no bank to select from and every round re-scores the same full set.
    n = config.sp_budget_ttest
    m = len(dataset)
    if m > 0 and n > m:
        return PreflightWarning(
            code="sp_budget_exceeds_dataset",
            title=f"per-round eval budget ({n}) exceeds bank size ({m})",
            detail=(
                f"The bank (full train split) has only {m} samples, so every round "
                f"scores on all {m} and `select_round_subset` has nothing to select "
                f"from — the adaptive queue mechanism is inert. Lower sp_budget_ttest "
                f"to below {m}, or grow the dataset."
            ),
        )
    return None


_PARAMS_B_RE = re.compile(r"(\d+(?:\.\d+)?)\s*b\b", re.IGNORECASE)


def _model_params_b(model_id: str) -> float | None:
    segment = model_id.rsplit("/", 1)[-1]
    match = _PARAMS_B_RE.search(segment)
    return float(match.group(1)) if match else None


def _check_optimizer_below_target(target_models: tuple[str, ...]) -> PreflightWarning | None:
    """An optimizer smaller than the target it optimizes is almost always an accidental inversion.
    Its model is the install-global optimizer node config, never a per-campaign copy."""

    opt_model = optimizer_model()
    opt_b = _model_params_b(opt_model)
    if opt_b is None:
        return None
    bigger = sorted(
        {m for m in target_models if (b := _model_params_b(m)) is not None and b > opt_b}
    )
    if not bigger:
        return None
    return PreflightWarning(
        code="optimizer_below_target",
        title=f"optimizer LLM ({opt_model}) is smaller than the target ({', '.join(bigger)})",
        detail=(
            "The optimizer is the strong model that improves the pipeline; running "
            "it on a model smaller than the target it optimizes is usually an "
            "accidental inversion. Raise the optimizer node `model` in "
            "`promptpotter/assets/optimizer/pipeline.yaml` to a larger tier."
        ),
    )


def _check_config_couplings(config: CampaignConfig) -> list[PreflightWarning]:
    """The declared map lives in ``knobs``, which the webapp config-map endpoint also reads; this is
    its pre-run CLI leg."""

    return [
        PreflightWarning(
            code=f"config_coupling.{c.name}",
            title=f"config coupling [{c.severity}]: {', '.join(c.knobs)}",
            detail=f"{c.relation} {c.consequence}",
        )
        for c in check_couplings(config)
    ]


def _check_task_context_present(framing: Mapping[str, Any] | None) -> PreflightWarning | None:
    """The operator's frozen framing is the SOLE source of l1_generate's ``task_intent`` slot, and
    an empty one renders as nothing at all — no header, no placeholder — so the slot falls back to
    the static template and the wire schema drops ``task_context_override`` with it. Decidable
    before a cell is bought, and afterwards visible only as ``review.md``'s ``_(empty)_``."""
    if has_framing(framing):
        return None
    return PreflightWarning(
        code="task_context_empty",
        title="no task framing — L1 will not be told what this dataset is",
        detail=(
            "`task_context` carries every field the operator wrote about the task, and it is the "
            "only panel feeding l1_generate's task_intent slot. Empty, that slot renders as the "
            "static template alone: the generator proposes edits knowing the failing samples and "
            "nothing about what the pipeline is for. Ship a `task_context.yaml` beside the "
            "dataset, or pass `--task-file` / `--task-text` so the decomposition runs — `new "
            "<name>` with neither never calls it."
        ),
    )


def run_preflight_checks(
    config: CampaignConfig,
    dataset: list[Sample],
    target_models: tuple[str, ...] = (),
    task_context: Mapping[str, Any] | None = None,
) -> list[PreflightWarning]:
    """``target_models`` are the resolved per-node target/scoring model ids, empty when the backend
    owns the model. Pure — no mutation, no I/O."""
    warnings: list[PreflightWarning] = []
    if (w := _check_sp_budget_vs_dataset(config, dataset)) is not None:
        warnings.append(w)
    if (w := _check_optimizer_below_target(target_models)) is not None:
        warnings.append(w)
    if (w := _check_lives_have_headroom(config)) is not None:
        warnings.append(w)
    if (w := _check_task_context_present(task_context)) is not None:
        warnings.append(w)
    warnings.extend(_check_config_couplings(config))
    return warnings


def _check_lives_have_headroom(config: CampaignConfig) -> PreflightWarning | None:
    """``lives`` that cannot run out before ``max_rounds`` is an inert brake — and when the two
    coincide the stop reason no longer says which fact stopped the run."""
    lives = config.optimization.lives
    max_rounds = config.optimization.max_rounds
    # `max_rounds=None` is the unbounded case — there is no calendar for hearts to race.
    if lives is None or max_rounds is None or lives.start < max_rounds:
        return None
    return PreflightWarning(
        code="config.lives_no_headroom",
        title="the stall brake cannot fire before the calendar cap",
        detail=(
            f"optimization.lives.start={lives.start} >= max_rounds="
            f"{max_rounds}, so hearts can never run out first: the run "
            "stops on the calendar and reports `lives_exhausted` for it. Lower lives.start "
            "to brake a stalling run early, or raise max_rounds to give it room — leaving "
            "both equal makes the stop reason unreadable."
        ),
    )


def check_model_reasoning_floors(
    node_configs: Iterable[tuple[str, Mapping[str, Any]]],
) -> list[str]:
    """Below ``ModelProfile.min_max_tokens`` a reasoning model can spend its whole budget thinking
    and emit nothing. An ABSENT ``max_tokens`` is the sanctioned default, never a violation."""

    violations: list[str] = []
    for node, cfg in node_configs:
        model = cfg.get("model")
        max_tokens = cfg.get("max_tokens")
        if not model or max_tokens is None:
            continue
        profile = model_profile(str(model))
        if profile is None or not profile.is_reasoning:
            continue
        if int(max_tokens) < profile.min_max_tokens:
            violations.append(
                f"node '{node}': reasoning model '{model}' is pinned to max_tokens="
                f"{max_tokens}, below its floor {profile.min_max_tokens}. It will spend the "
                f"budget reasoning and emit zero content (reasoning_budget_exhausted). Raise "
                f"max_tokens to >= {profile.min_max_tokens} and keep reasoning_effort low."
            )
    return violations

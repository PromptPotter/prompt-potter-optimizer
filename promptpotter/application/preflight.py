from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from promptpotter.application.campaign_config import CampaignConfig
from promptpotter.application.knobs import check_couplings
from promptpotter.application.optimizer_manifest import checkin_manifest, select_optimizer
from promptpotter.domain.pipeline_overlay import node_config_items
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.infrastructure.llm.registry import model_profile
from promptpotter.shared.errors import PayloadInvalidError

if TYPE_CHECKING:
    from promptpotter.domain.campaign import Arm
    from promptpotter.domain.sample import Sample


__all__ = [
    "PreflightWarning",
    "check_search_pool_holds_round",
    "refuse_arm_below_round",
    "refuse_below_reasoning_floor",
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
    # Only the PER-ROUND draw is checked against the bank. An origin budget above the bank is
    # not a misconfiguration: `sample_dataset` is a prefix slice, and "score the origin on
    # everything there is" is exactly what a wide-origin default wants on a small bank.
    # A round asking more cells than the bank holds IS a finding: its sampler has nothing to
    # select from and every round re-scores the same full set.
    m = len(dataset)
    n = select_optimizer(config.optimization).round_cells(m)
    if m > 0 and n > m:
        return PreflightWarning(
            code="sp_budget_exceeds_dataset",
            title=f"per-round eval budget ({n}) exceeds bank size ({m})",
            detail=(
                f"The bank (full train split) has only {m} samples, so every round "
                f"scores on all {m} and the sampler has nothing to select from. Lower "
                f"its per-round budget to below {m}, or grow the dataset."
            ),
        )
    return None


_PARAMS_B_RE = re.compile(r"(\d+(?:\.\d+)?)\s*b\b", re.IGNORECASE)


def _model_params_b(model_id: str) -> float | None:
    segment = model_id.rsplit("/", 1)[-1]
    match = _PARAMS_B_RE.search(segment)
    return float(match.group(1)) if match else None


def _check_optimizer_below_target(
    opt_model: str, target_models: tuple[str, ...]
) -> PreflightWarning | None:
    """An optimizer smaller than the target it optimizes is almost always an accidental inversion.
    Its model is the selected manifest's proposing node's."""

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
            "accidental inversion. Raise the proposing node's `model` — in the manifest under "
            "`promptpotter/assets/optimizers/`, or this campaign's `optimization.nodes` — to a "
            "larger tier."
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


def _check_task_context_present(
    config: CampaignConfig, framing: TaskDecomposition
) -> PreflightWarning | None:
    """The operator's frozen framing is the SOLE source of l1_generate's ``task_intent`` slot, and
    an empty one renders as nothing at all — no header, no placeholder — so the slot falls back to
    the static template. Decidable before a cell is bought, and afterwards visible only as
    ``review.md``'s ``_(empty)_``."""
    if config.task_framing == "off":
        return PreflightWarning(
            code="task_framing_off",
            title="running UNFRAMED on purpose — `task_framing: off`",
            detail=(
                "The framing ablation: no target render splices the dataset's task framing and "
                "l1_generate's task_intent slot renders as the static template alone. Compare "
                "this run only against framed runs of the same dataset, never pool with them."
            ),
        )
    if framing:
        return None
    return PreflightWarning(
        code="task_context_empty",
        title="no task framing — L1 will not be told what this dataset is",
        detail=(
            "`task_context` carries every field the operator wrote about the task, and it is the "
            "only panel feeding l1_generate's task_intent slot. Empty, that slot renders as the "
            "static template alone: the generator proposes edits knowing the failing samples and "
            "nothing about what the pipeline is for. Ship a `task_description.md` beside the "
            "dataset — the first mint decomposes it — or pass `--task-file` / `--task-text`."
        ),
    )


def _check_cap_funds_round(
    config: CampaignConfig,
    search_pool: int,
    cell_usd: float | None,
    measured_cell_usd: float | None,
) -> PreflightWarning | None:
    """A warning, a block only for an arm. A round is priced at what a cell of this dataset BILLED
    where the archive says, and only an unmeasured one at ``cell_usd`` — every retry at its token
    ceiling on the dearest host, a bound a cell bills far under, so priced there alone it warns on every run."""
    cap = config.optimization.spend_budget_usd
    price = cell_usd if measured_cell_usd is None else measured_cell_usd
    if cap is None or price is None:
        return None
    selected = select_optimizer(config.optimization)
    cells = selected.round_cells_ceiling(search_pool)
    need = cells * price
    if need <= cap:
        return None
    if measured_cell_usd is not None:
        return PreflightWarning(
            code="spend_cap_below_round",
            title=f"spend cap ${cap:.2f} is under one round's expected cost (${need:.2f})",
            detail=(
                f"One round of {selected.name} can measure {cells} cells off the {search_pool} "
                f"search rows, and a cell of this dataset has billed ${measured_cell_usd:.5f} on "
                f"these models, so a full round is expected to cost ${need:.2f} and this run to "
                "stop on `spend_budget` inside round 1 unless an eliminator cuts arms short. Raise "
                "it with `set-limits --max-usd`, or narrow the round in `optimization.nodes`."
            ),
        )
    return PreflightWarning(
        code="spend_cap_below_round",
        title=f"spend cap ${cap:.2f} is under one round's ceiling (${need:.2f})",
        detail=(
            f"One round of {selected.name} can measure {cells} cells off the {search_pool} search "
            f"rows, and the spend book admits a cell at up to ${cell_usd:.4f}, so only a cap of "
            f"${need:.2f} is certain to close a round. A cell usually bills well under its bound "
            "and an eliminator cuts arms short, so this run may still close rounds — or stop on "
            "`spend_budget` inside round 1 with none closed. Raise it with `set-limits --max-usd`, "
            "or narrow the round in `optimization.nodes`."
        ),
    )


def check_search_pool_holds_round(config: CampaignConfig, search_pool: int) -> str | None:
    """A HARD block, decidable before a mint: the sampler raises on such a pool at round 1, after
    the origin's cells are paid."""
    selected = select_optimizer(config.optimization)
    if selected.round_cells_ceiling(search_pool) > 0:
        return None
    return (
        f"the search pool holds {search_pool} rows, which optimizer {selected.name!r} cannot draw "
        "one round's panel from. Grow the dataset, hold fewer rows out in `dataset_split`, or "
        "narrow the round in `optimization.nodes`."
    )


def run_preflight_checks(
    config: CampaignConfig,
    dataset: list[Sample],
    target_models: tuple[str, ...] = (),
    *,
    framing: TaskDecomposition,
    cell_usd: float | None,
    measured_cell_usd: float | None,
) -> list[PreflightWarning]:
    """``target_models`` are the resolved per-node target/scoring model ids, empty when the backend
    owns the model; ``dataset`` is the search pool, ``cell_usd`` the most one of its cells can
    bill and ``measured_cell_usd`` what one has billed, each ``None`` where nothing prices it.
    Pure — no mutation, no I/O."""
    warnings: list[PreflightWarning] = []
    if (w := _check_sp_budget_vs_dataset(config, dataset)) is not None:
        warnings.append(w)
    if (w := _check_cap_funds_round(config, len(dataset), cell_usd, measured_cell_usd)) is not None:
        warnings.append(w)
    opt_model = select_optimizer(config.optimization).model()
    if (w := _check_optimizer_below_target(opt_model, target_models)) is not None:
        warnings.append(w)
    if (w := _check_task_context_present(config, framing)) is not None:
        warnings.append(w)
    warnings.extend(_check_config_couplings(config))
    return warnings


def refuse_arm_below_round(
    arm: Arm | None, warnings: Iterable[PreflightWarning], *, measured_cell_usd: float | None
) -> None:
    """A HARD block for an arm alone: stopped on `spend_budget` inside round 1 it is still graded
    beside the arms that searched. Never on the unmeasured bound, which a cell bills far under."""
    if arm is None or measured_cell_usd is None:
        return
    for w in warnings:
        if w.code == "spend_cap_below_round":
            raise PayloadInvalidError(
                f"arm {arm.arm_key} of head-to-head {arm.head_to_head_id} cannot close one round: "
                f"{w.title}. {w.detail} Raise this arm's cap with `set-limits --max-usd`, which "
                "moves this arm alone, or size the round down with the optimizer's own knobs.",
                code="spend_cap_below_round",
            )


def refuse_below_reasoning_floor(
    config: CampaignConfig, pipeline_params: Mapping[str, Any] | None
) -> None:
    """Below ``ModelProfile.min_max_tokens`` a reasoning model can spend its whole budget thinking
    and emit nothing. An ABSENT ``max_tokens`` is the sanctioned default, never a violation."""
    selected = select_optimizer(config.optimization)
    node_configs: Iterable[tuple[str, Mapping[str, Any]]] = [
        *node_config_items(dict(pipeline_params or {})),
        # Every llm node the optimizer DECLARES, off its `default` chain too, or an escalation
        # node escapes.
        *((n, selected.node_config(n)) for n in selected.llm_nodes),
        *((n.name, n.current_config) for n in checkin_manifest().schema.declared_nodes),
    ]
    violations: list[str] = []
    for node, cfg in node_configs:
        model = cfg.get("model")
        max_tokens = cfg.get("max_tokens")
        if not model or max_tokens is None:
            continue
        profile = model_profile(str(model))
        if profile is None:
            continue
        if int(max_tokens) < profile.min_max_tokens:
            violations.append(
                f"node '{node}': reasoning model '{model}' is pinned to max_tokens="
                f"{max_tokens}, below its floor {profile.min_max_tokens}. It will spend the "
                f"budget reasoning and emit zero content (reasoning_budget_exhausted). Raise "
                f"max_tokens to >= {profile.min_max_tokens} and keep reasoning_effort low."
            )
    if violations:
        raise PayloadInvalidError(
            "a reasoning model is configured below its token floor and would emit zero content:"
            "\n  - " + "\n  - ".join(violations),
            code="model_below_reasoning_floor",
        )

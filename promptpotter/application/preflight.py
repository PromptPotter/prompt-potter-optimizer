from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from promptpotter.application import optimizers
from promptpotter.application.knobs import check_couplings
from promptpotter.application.optimizer_manifest import SelectedOptimizer, checkin_manifest
from promptpotter.domain.campaign_config import CampaignConfig
from promptpotter.domain.pipeline_overlay import node_config_items
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.infrastructure.llm.pricing import route_priced
from promptpotter.infrastructure.llm.registry import model_profile
from promptpotter.shared.errors import PayloadInvalidError

if TYPE_CHECKING:
    from promptpotter.domain.campaign import Arm
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.sample import Sample


@dataclass(frozen=True)
class PreflightWarning:
    code: str
    title: str
    detail: str


def _check_sp_budget_vs_dataset(
    selected: SelectedOptimizer, dataset: list[Sample]
) -> PreflightWarning | None:
    # Only the PER-ROUND draw: an origin budget above the bank is fine (`sample_dataset` slices).
    m = len(dataset)
    n = selected.round_cells(m)
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


def _check_config_couplings(
    config: CampaignConfig, selected: SelectedOptimizer
) -> list[PreflightWarning]:
    return [
        PreflightWarning(
            code=f"config_coupling.{c.name}",
            title=f"config coupling [{c.severity}]: {', '.join(c.knobs)}",
            detail=f"{c.relation} {c.consequence}",
        )
        for c in check_couplings(config, selected)
    ]


def _check_task_context_present(
    config: CampaignConfig, framing: TaskDecomposition
) -> PreflightWarning | None:
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
            "nothing about what the pipeline is for. Ship a `task.md` in the "
            "dataset's definition — the first mint decomposes its body — or pass `--task-file` / `--task-text`."
        ),
    )


@dataclass(frozen=True)
class _RoundShort:
    """A USD cap under what one round can cost, priced on a billed cell where one is archived."""

    cap: float
    need: float
    cells: int
    search: int
    price: float
    billed: bool

    def costing(self, optimizer: str) -> str:
        priced = (
            f"a cell of this dataset has billed ${self.price:.5f} on these models"
            if self.billed
            else f"the spend book admits a cell at up to ${self.price:.4f}"
        )
        return (
            f"One round of {optimizer} can measure {self.cells} cells off the {self.search} "
            f"search rows, and {priced}"
        )


def _round_short(
    config: CampaignConfig,
    selected: SelectedOptimizer,
    search: int,
    cell_usd: float | None,
    measured_cell_usd: float | None,
) -> _RoundShort | None:
    cap = config.optimization.budget.usd
    price = cell_usd if measured_cell_usd is None else measured_cell_usd
    if cap is None or price is None:
        return None
    cells = selected.round_cells_ceiling(search)
    if (need := cells * price) <= cap:
        return None
    return _RoundShort(cap, need, cells, search, price, billed=measured_cell_usd is not None)


def check_cap_funds_round(
    config: CampaignConfig,
    selected: SelectedOptimizer,
    search: int,
    cell_usd: float | None,
    measured_cell_usd: float | None,
) -> PreflightWarning | None:
    """Priced at what a cell BILLED where the archive says; the ``cell_usd`` bound alone warns on every run."""
    short = _round_short(config, selected, search, cell_usd, measured_cell_usd)
    if short is None:
        return None
    advice = "Raise it with `set-limits --max-usd`, or narrow the round in `optimization.nodes`."
    if short.billed:
        return PreflightWarning(
            code="spend_cap_below_round",
            title=f"spend cap ${short.cap:.2f} is under one round's expected cost (${short.need:.2f})",
            detail=(
                f"{short.costing(selected.name)}, so a full round is expected to cost "
                f"${short.need:.2f} and this run to stop on `spend_budget` inside round 1 unless an "
                f"eliminator cuts arms short. {advice}"
            ),
        )
    return PreflightWarning(
        code="spend_cap_below_round",
        title=f"spend cap ${short.cap:.2f} is under one round's ceiling (${short.need:.2f})",
        detail=(
            f"{short.costing(selected.name)}, so only a cap of ${short.need:.2f} is certain to "
            "close a round. A cell usually bills well under its bound and an eliminator cuts arms "
            "short, so this run may still close rounds — or stop on `spend_budget` inside round 1 "
            f"with none closed. {advice}"
        ),
    )


def _check_origin_already_run(origin_campaigns: Sequence[str]) -> PreflightWarning | None:
    """A cycle id is content-addressed: another campaign holding it ran the identical origin."""
    if not origin_campaigns:
        return None
    return PreflightWarning(
        code="origin_already_run",
        title=f"identical origin already run in {', '.join(origin_campaigns)}",
        detail=(
            "A fresh `new` re-measures the identical seed. `resume` continues one of those "
            "instead; `new` is for an origin you have CHANGED (optimizer prompt, config, or "
            "dataset)."
        ),
    )


def _check_instrument_replays(
    instrument: str | None, banked_instruments: Sequence[str]
) -> PreflightWarning | None:
    if instrument is None or not banked_instruments or instrument in banked_instruments:
        return None
    return PreflightWarning(
        code="instrument_novel",
        title=(
            f"instrument {instrument} is new — none of {len(banked_instruments)} prior "
            "campaigns replay"
        ),
        detail=(
            f"Those campaigns carry {len(set(banked_instruments))} other fingerprint(s), so no "
            "banked cell replays: this run re-measures its origin and can be compared to none of "
            "them. Expected while the engine is being rewritten; it is also the reason a panel "
            "does not accumulate across runs."
        ),
    )


def check_search_split_holds_round(selected: SelectedOptimizer, search: int) -> str | None:
    """Asked before a mint: the sampler raises on such a pool at round 1, after the origin is paid."""
    if selected.round_cells_ceiling(search) > 0:
        return None
    return (
        f"the search split holds {search} rows, which optimizer {selected.name!r} cannot draw "
        "one round's panel from. Grow the dataset, hold fewer rows out in `dataset_split`, or "
        "narrow the round in `optimization.nodes`."
    )


def run_preflight_checks(
    config: CampaignConfig,
    selected: SelectedOptimizer,
    dataset: list[Sample],
    target_models: tuple[str, ...] = (),
    *,
    framing: TaskDecomposition,
    cell_usd: float | None,
    measured_cell_usd: float | None,
    origin_campaigns: Sequence[str],
    instrument: str | None,
    banked_instruments: Sequence[str],
) -> list[PreflightWarning]:
    warnings: list[PreflightWarning] = []
    if (w := _check_sp_budget_vs_dataset(selected, dataset)) is not None:
        warnings.append(w)
    funds = check_cap_funds_round(config, selected, len(dataset), cell_usd, measured_cell_usd)
    if funds is not None:
        warnings.append(funds)
    if (w := _check_optimizer_below_target(selected.model(), target_models)) is not None:
        warnings.append(w)
    if (w := _check_task_context_present(config, framing)) is not None:
        warnings.append(w)
    if (w := _check_origin_already_run(origin_campaigns)) is not None:
        warnings.append(w)
    if (w := _check_instrument_replays(instrument, banked_instruments)) is not None:
        warnings.append(w)
    warnings.extend(_check_config_couplings(config, selected))
    return warnings


def refuse_arm_below_round(
    arm: Arm,
    config: CampaignConfig,
    selected: SelectedOptimizer,
    *,
    search: int,
    cell_usd: float | None,
    unpriced: Sequence[str],
    measured_cell_usd: float | None,
) -> None:
    """Asked as the arm is planned, before its key is taken: stopped inside round 1 it is still
    graded beside the arms that searched. A first run has no billed price, so the bound prices it,
    and an arm neither prices is refused rather than let through. *unpriced* names the routes no
    rate prices, where the backend bounds the cell."""
    cap = config.optimization.budget.usd
    if cap is None:
        return
    where = f"arm {arm.arm_key} of head-to-head {arm.head_to_head_id}"
    if cell_usd is None and measured_cell_usd is None:
        bound = (
            f"no rate on file prices {', '.join(unpriced)}"
            if unpriced
            else "the backend bounds no cell"
        )
        raise PayloadInvalidError(
            f"{where} cannot be shown to close one round under its ${cap:.2f}: no archived run "
            f"prices a cell on these models and {bound}. Nothing was minted. Measure the origin "
            "once with a plain `new` on this dataset, or bound the arms by tokens or rounds with "
            "no dollar cap.",
            code="arm_unpriced",
        )
    short = _round_short(config, selected, search, cell_usd, measured_cell_usd)
    if short is None:
        return
    raise PayloadInvalidError(
        f"{where} cannot close one round: its ${cap:.2f} is under the ${short.need:.2f} "
        f"{'one round is expected to cost' if short.billed else 'certain to close one'}. "
        f"{short.costing(selected.name)}. Nothing was minted: raise the head-to-head's budget, "
        "which every arm runs, or size this arm's round down with its own knobs.",
        code="spend_cap_below_round",
    )


def refuse_unfit_pipeline(selected: SelectedOptimizer, schema: PipelineSchema) -> None:
    """A member that cannot write the chain's prompt nodes would raise inside round 1, after the origin is paid."""
    if (unfit := optimizers.unfit_pipeline(selected, schema)) is not None:
        raise PayloadInvalidError(unfit, code="optimizer_unfit_pipeline")


def optimizer_node_configs(selected: SelectedOptimizer) -> list[tuple[str, Mapping[str, Any]]]:
    """Every llm node a run DECLARES beside its pipeline, off the `default` chain too, or an
    escalation node escapes."""
    return [
        *((n, selected.node_config(n)) for n in selected.llm_nodes),
        *((n.name, n.current_config) for n in checkin_manifest().schema.declared_nodes),
    ]


def refuse_below_reasoning_floor(node_configs: Iterable[tuple[str, Mapping[str, Any]]]) -> None:
    """Over every node a pass sends to: a run's (`jobs/mint.py::refuse_unrunnable`), or a
    pipeline scored outside every run (`loop_start.py::arm_diagnostic_scoring`). An ABSENT
    ``max_tokens`` is the sanctioned default, never a violation."""
    refused: list[str] = []
    below_floor: list[str] = []
    for node, cfg in node_configs:
        model = cfg.get("model")
        if not model:
            continue
        profile = model_profile(str(model))
        if profile is None:
            continue
        if (effort := cfg.get("reasoning_effort")) in profile.refuses_efforts:
            refused.append(
                f"node '{node}': model '{model}' is measured to refuse reasoning_effort="
                f"{effort}, so every call to it fails. Set reasoning_effort to a rung it "
                f"answers, or another model."
            )
        max_tokens = cfg.get("max_tokens")
        if max_tokens is not None and int(max_tokens) < profile.min_max_tokens:
            below_floor.append(
                f"node '{node}': reasoning model '{model}' is pinned to max_tokens="
                f"{max_tokens}, below its floor {profile.min_max_tokens}. It will spend the "
                f"budget reasoning and emit zero content (reasoning_budget_exhausted). Raise "
                f"max_tokens to >= {profile.min_max_tokens} and keep reasoning_effort low."
            )
    if refused:
        raise PayloadInvalidError(
            "a model is configured with a reasoning_effort it refuses:"
            "\n  - " + "\n  - ".join(refused),
            code="reasoning_effort_refused",
        )
    if below_floor:
        raise PayloadInvalidError(
            "a reasoning model is configured below its floor and would answer nothing:"
            "\n  - " + "\n  - ".join(below_floor),
            code="model_below_reasoning_floor",
        )


Route = tuple[str, str, tuple[str, ...] | None]


def task_routes(pipeline_params: Mapping[str, Any], schema: PipelineSchema) -> list[Route]:
    """Each task node's model, and each model its axis offers: ``(model, provider, hosts)``."""
    routes: dict[Route, None] = {}
    for _node, cfg in node_config_items(dict(pipeline_params)):
        model, provider, hosts = cfg.get("model"), cfg.get("provider"), cfg.get("route_order")
        if model and provider:
            routes[str(model), str(provider), tuple(hosts) if hosts else None] = None
    for node in schema.nodes:
        if node.provider and "model" in node.param_keys:
            for model in schema.model_options(node):
                routes[model, node.provider, None] = None
    return list(routes)


def unpriced_routes(
    config: CampaignConfig,
    selected: SelectedOptimizer,
    pipeline_params: Mapping[str, Any],
    schema: PipelineSchema,
) -> list[str]:
    """Every route a run may send on, the optimizer's too, that no rate prices — under a dollar
    ceiling only. An unreachable price list refuses nothing here."""
    if config.optimization.budget.usd is None:
        return []
    routes = dict.fromkeys(task_routes(pipeline_params, schema))
    for cfg in (selected.node_config(n) for n in selected.llm_nodes):
        model, provider, hosts = cfg.get("model"), cfg.get("provider"), cfg.get("route_order")
        if model and provider:
            routes[str(model), str(provider), tuple(hosts) if hosts else None] = None
    return [
        f"{model} ({provider})"
        for model, provider, hosts in routes
        if route_priced(model, provider, hosts=hosts) is False
    ]


def refuse_unpriced(unpriced: Sequence[str]) -> None:
    if unpriced:
        raise PayloadInvalidError(
            f"no rate prices {', '.join(unpriced)}, so a dollar ceiling cannot bound what it "
            "sends. Pick a model the rate table prices, or drop the dollar cap and bound the run "
            "in tokens (`budget: {usd: null, tokens: N}`).",
            code="model_unpriced",
        )

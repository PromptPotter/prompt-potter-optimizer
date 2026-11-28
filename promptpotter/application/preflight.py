from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence

from promptpotter.application.config_map import check_couplings
from promptpotter.application.initialization.session import Session
from promptpotter.application.optimizer_contract import unfit_pipeline
from promptpotter.application.optimizer_manifest import SelectedOptimizer, checkin_manifest
from promptpotter.connectors.protocol import Connector
from promptpotter.domain.campaign import ArmSeat
from promptpotter.domain.campaign_config import CampaignConfig
from promptpotter.domain.phase_acts import (
    CapUnderRound,
    CouplingActive,
    InstrumentNovel,
    NoRanker,
    OptimizerBelowTarget,
    OriginAlreadyRun,
    PreflightNote,
    RoundOverBank,
    SurveyUnread,
    Unframed,
)
from promptpotter.domain.pipeline_manifest import CHECKIN_NODE
from promptpotter.domain.pipeline_overlay import fold_output_contract
from promptpotter.domain.pipeline_params import NodeCall, PipelineParams
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.domain.sample import Bank, Sample
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.domain.spend import usd_text
from promptpotter.infrastructure.llm.pricing import route_priced
from promptpotter.infrastructure.llm.registry import model_profile
from promptpotter.shared.errors import MINT_FRESH, PayloadInvalidError


def _check_sp_budget_vs_dataset(
    selected: SelectedOptimizer, dataset: Sequence[Sample]
) -> RoundOverBank | None:
    # Only the PER-ROUND draw: an origin budget above the bank is fine (`sample_dataset` slices).
    m = len(dataset)
    n = selected.round_cells_asked(m)
    return RoundOverBank(asked=n, bank=m) if m > 0 and n > m else None


_PARAMS_B_RE = re.compile(r"(\d+(?:\.\d+)?)\s*b\b", re.IGNORECASE)


def _model_params_b(model_id: str) -> float | None:
    segment = model_id.rsplit("/", 1)[-1]
    match = _PARAMS_B_RE.search(segment)
    return float(match.group(1)) if match else None


def _check_optimizer_below_target(
    opt_model: str, target_models: tuple[str, ...]
) -> OptimizerBelowTarget | None:
    opt_b = _model_params_b(opt_model)
    if opt_b is None:
        return None
    bigger = sorted(
        {m for m in target_models if (b := _model_params_b(m)) is not None and b > opt_b}
    )
    return OptimizerBelowTarget(optimizer=opt_model, bigger=tuple(bigger)) if bigger else None


def _check_task_context_present(
    config: CampaignConfig, framing: TaskDecomposition
) -> Unframed | None:
    if config.task_framing == "off":
        return Unframed(chosen=True)
    return None if framing else Unframed(chosen=False)


def check_cap_funds_round(
    config: CampaignConfig,
    selected: SelectedOptimizer,
    search: int,
    cell_usd: float | None,
    measured_cell_usd: float | None,
) -> CapUnderRound | None:
    """Priced at what a cell BILLED where the archive says; the ``cell_usd`` bound alone warns on every run."""
    cap = config.optimization.budget.usd
    price = cell_usd if measured_cell_usd is None else measured_cell_usd
    if cap is None or price is None:
        return None
    cells = selected.round_cells_ceiling(search)
    if (need := cells * price) <= cap:
        return None
    billed = measured_cell_usd is not None
    return CapUnderRound(
        optimizer=selected.name,
        cap=cap,
        need=need,
        cells=cells,
        search=search,
        price=price,
        billed=billed,
    )


def _check_labels_have_ranker(
    schema: PipelineSchema, bank: Bank, connector: Connector | None
) -> NoRanker | None:
    """After the samples, never before: the invariant is about the schema AND the bank together."""
    if not schema.running_nodes or not bank.samples or schema.emits_ranking:
        return None
    if bank.verifier_graded:
        return None
    return None if connector is not None and connector.answer_key else NoRanker()


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
    session: Session,
    dataset: Sequence[Sample],
    *,
    framing: TaskDecomposition,
    cell_usd: float | None,
    measured_cell_usd: float | None,
    origin_campaigns: Sequence[str],
    instrument: str | None,
    banked_instruments: Sequence[str],
    unread: Mapping[str, Exception],
) -> tuple[PreflightNote, ...]:
    """*unread* is each campaign the survey of earlier runs could not read, by its fault."""
    target_models = tuple(v.model for v in session.pipeline_params.nodes.values() if v.model)
    novel = (
        instrument is not None and bool(banked_instruments) and instrument not in banked_instruments
    )
    found = [
        _check_sp_budget_vs_dataset(selected, dataset),
        check_cap_funds_round(config, selected, len(dataset), cell_usd, measured_cell_usd),
        _check_optimizer_below_target(selected.model(), target_models),
        _check_task_context_present(config, framing),
        OriginAlreadyRun(campaigns=tuple(origin_campaigns)) if origin_campaigns else None,
        InstrumentNovel(
            instrument=instrument,
            campaigns=len(banked_instruments),
            fingerprints=len(set(banked_instruments)),
        )
        if novel and instrument is not None
        else None,
        _check_labels_have_ranker(
            session.bound_resolution.chain, session.bank, session.backend_client.connector
        ),
        SurveyUnread(campaigns=tuple(unread), reason=str(next(iter(unread.values()))))
        if unread
        else None,
        *(
            CouplingActive(
                name=c.name,
                severity=c.severity,
                knobs=c.knobs,
                relation=c.relation,
                consequence=c.consequence,
            )
            for c in check_couplings(config, selected)
        ),
    ]
    return tuple(note for note in found if note is not None)


def refuse_arm_below_round(
    arm: ArmSeat,
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
            f"{where} cannot be shown to close one round under its {usd_text(cap)}: no archived run "
            f"prices a cell on these models and {bound}. Nothing was minted. Measure the origin "
            "once with a plain campaign on this dataset, or bound the arms by tokens or rounds "
            "with no dollar cap.",
            code="arm_unpriced",
            next_acts=(MINT_FRESH,),
        )
    short = check_cap_funds_round(config, selected, search, cell_usd, measured_cell_usd)
    if short is None:
        return
    raise PayloadInvalidError(
        f"{where} cannot close one round: its {usd_text(cap)} is under the {usd_text(short.need)} "
        f"{'one round is expected to cost' if short.billed else 'certain to close one'}. "
        f"{short.costing}. Nothing was minted: raise the head-to-head's budget, "
        "which every arm runs, or size this arm's round down with its own knobs.",
        code="spend_cap_below_round",
    )


def refuse_unfit_pipeline(
    selected: SelectedOptimizer, schema: PipelineSchema, connector: str
) -> None:
    """A member that cannot write the chain's prompt nodes would raise inside round 1, after the origin is paid."""
    if (unfit := unfit_pipeline(selected, schema, connector)) is not None:
        raise PayloadInvalidError(unfit, code="optimizer_unfit_pipeline")


def refuse_off_contract(pipeline_params: PipelineParams, schema: PipelineSchema) -> None:
    """A key a node's door does not read (`PipelineNode.wire_keys`) is a 400 on every cell."""
    refused = [
        f"node '{node}' sends '{key}'"
        for node, laid in fold_output_contract(pipeline_params, schema).laid_nodes.items()
        if (declared := schema.get_node(node)) is not None
        for key in laid
        if not declared.reads(key)
    ]
    if refused:
        raise PayloadInvalidError(
            "a node is configured with a key its backend does not read, so every cell would be "
            "refused (wire_key_unknown):\n  - " + "\n  - ".join(refused),
            code="wire_key_unknown",
        )


def optimizer_node_configs(selected: SelectedOptimizer) -> list[tuple[str, NodeCall]]:
    """Every llm node a run DECLARES beside its pipeline, off the `default` chain too, or an
    escalation node escapes."""
    return [
        *((n, selected.node_configs.get(n) or NodeCall()) for n in selected.llm_nodes),
        (CHECKIN_NODE, checkin_manifest().config),
    ]


def refuse_below_reasoning_floor(node_configs: Iterable[tuple[str, NodeCall]]) -> None:
    """Over every node a pass sends to: a run's (`jobs/mint.py::refuse_unrunnable`), or a
    pipeline scored outside every run (`scoring_scope.py::arm_diagnostic_scoring`). An ABSENT
    ``max_tokens`` is the sanctioned default, never a violation."""
    refused: list[str] = []
    below_floor: list[str] = []
    for node, cfg in node_configs:
        if not (model := cfg.model):
            continue
        profile = model_profile(model)
        if profile is None:
            continue
        if (effort := cfg.reasoning_effort) in profile.refuses_efforts:
            refused.append(
                f"node '{node}': model '{model}' is measured to refuse reasoning_effort="
                f"{effort}, so every call to it fails. Set reasoning_effort to a rung it "
                f"answers, or another model."
            )
        max_tokens = cfg.max_tokens
        if max_tokens is not None and max_tokens < profile.min_max_tokens:
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


def task_routes(pipeline_params: PipelineParams, schema: PipelineSchema) -> list[Route]:
    """Each task node's model, and each model its axis offers: ``(model, provider, hosts)``."""
    routes: dict[Route, None] = {}
    for cfg in pipeline_params.nodes.values():
        model, provider, hosts = cfg.model, cfg.provider, cfg.route_order
        if model and provider:
            routes[model, provider, tuple(hosts) if hosts else None] = None
    for node in schema.running_nodes:
        if node.provider and "model" in node.param_keys:
            for model in schema.model_options(node):
                routes[model, node.provider, None] = None
    return list(routes)


def unpriced_routes(
    config: CampaignConfig,
    selected: SelectedOptimizer,
    pipeline_params: PipelineParams,
    schema: PipelineSchema,
) -> list[str]:
    """Every route a run may send on, the optimizer's too, that no rate prices — under a dollar
    ceiling only. An unreachable price list refuses nothing here."""
    if config.optimization.budget.usd is None:
        return []
    routes = dict.fromkeys(task_routes(pipeline_params, schema))
    for cfg in (selected.node_config(n) for n in selected.llm_nodes):
        hosts = cfg.route_order
        routes[cfg.model, cfg.provider, tuple(hosts) if hosts else None] = None
    return [
        f"{model} ({provider})"
        for model, provider, hosts in routes
        if route_priced(model, provider, hosts=hosts) is False
    ]


def unpriced_refusal(unpriced: Sequence[str]) -> PayloadInvalidError | None:
    if unpriced:
        return PayloadInvalidError(
            f"no rate prices {', '.join(unpriced)}, so a dollar ceiling cannot bound what it "
            "sends. Pick a model the rate table prices, or drop the dollar cap and bound the run "
            "in tokens (`budget: {usd: unbounded, tokens: N}`).",
            code="model_unpriced",
        )
    return None

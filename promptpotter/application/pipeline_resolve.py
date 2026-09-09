from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, MutableMapping
from typing import TYPE_CHECKING, Any

from pydantic import Field

from promptpotter.application.campaign_config import (
    CampaignConfig,
    apply_inherited_overlay,
    load_campaign_config,
)
from promptpotter.application.datasets.authored import (
    dataset_campaign_path,
    load_dataset_campaign_config,
)
from promptpotter.application.datasets.draft_campaign import (
    DraftCampaign,
    declared_pipeline_json,
    draft_campaign_config,
    load_checkin_draft,
    rendered_pipeline_json,
)
from promptpotter.application.datasets.prompts import (
    dataset_declared_nodes,
    has_dataset_prompts,
    load_dataset_node_overlay,
    load_node_prompt,
)
from promptpotter.application.evidence import SubjectSpec
from promptpotter.application.runner.inner.tasks import inner_tasks_path, load_inner_tasks
from promptpotter.config.settings import (
    PROMPT_STRING_FIELDS,
)
from promptpotter.connectors import CONNECTORS, DEFAULT_CONNECTOR
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.l4.proxies import InnerCycleUnscoreableError
from promptpotter.domain.pipeline_parsing import parse_pipeline_response
from promptpotter.domain.pipeline_schema import (
    ModelCapability,
    NestedPipelineRef,
    NodeConfigParam,
    NodeOutputSchema,
    NodeReach,
    NodeSearchNarrowing,
    ParamSource,
    PipelineView,
    reach_map,
)
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.llm.capabilities import resolve_schema_menu
from promptpotter.infrastructure.runtime_flags import is_checkin
from promptpotter.infrastructure.store.dataset_access import (
    DatasetAccessError,
    dataset_pipeline_path,
    readable_dataset_dir,
)
from promptpotter.infrastructure.store.io import read_yaml_optional
from promptpotter.shared.errors import PayloadInvalidError

if TYPE_CHECKING:
    from pathlib import Path

    from promptpotter.application.initialization.session import Session
    from promptpotter.domain.campaign import Campaign
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.infrastructure.store.stores import Stores
    from promptpotter.judges.protocol import JudgeSpec

logger = logging.getLogger(__name__)

JUDGE_INSTRUMENT_KEY = "judge_instrument"
"""The node-config key the judges' fingerprint rides into measurement identity — ONE key over the
whole term → judge mapping. Twin of `connectors/harbor.py::INSTRUMENT_KEY`, named apart because
that one says which task bytes ran and this one which grader read the answer. Both are identity,
neither a tunable — nothing may put either in `param_keys`."""


__all__ = [
    "CampaignPipelineResponse",
    "apply_node_overlay",
    "configure_and_apply_pipeline",
    "missing_template_vars",
    "resolve_pipeline_config_params",
    "resolve_pipeline_for_campaign",
    "resolve_pipeline_for_draft",
    "resolved_dataset_name",
]


def apply_node_overlay(
    base: dict[str, Any],
    overlay: Mapping[str, Any],
    schema: PipelineSchema | None,
    *,
    source: ParamSource | None = None,
    provenance: MutableMapping[str, dict[str, ParamSource]] | None = None,
) -> dict[str, Any]:
    """The ONE per-node overlay merge. Depth is bounded by the DECLARATION (``param_types: object``
    merges one level deeper, so a sibling entry a parent earned survives), never by sniffing types.
    ``provenance`` stamps ``source`` on every ``(node, param)`` written; layers apply last-writer-wins,
    so the final stamp IS the winning layer. The merge is on the identity path and ignores both."""
    merged = dict(base)
    for node, cfg in overlay.items():
        existing = merged.get(node)
        if not (isinstance(existing, dict) and isinstance(cfg, dict)):
            merged[node] = cfg
            _stamp(provenance, source, node, cfg)
            continue
        node_obj = schema.get_node(node) if schema else None
        param_types = node_obj.param_types if node_obj else {}
        node_cfg = {**existing, **cfg}
        for param, incoming in cfg.items():
            prior = existing.get(param)
            if (
                param_types.get(param) == "object"
                and isinstance(prior, dict)
                and isinstance(incoming, dict)
            ):
                node_cfg[param] = {**prior, **incoming}
        merged[node] = node_cfg
        _stamp(provenance, source, node, cfg)
    return merged


def _stamp(
    provenance: MutableMapping[str, dict[str, ParamSource]] | None,
    source: ParamSource | None,
    node: str,
    cfg: object,
) -> None:
    """Param granularity, never a sub-key: an ``object`` merge writes sub-keys from two layers into
    one dict, and the config surface has no sub-key row to report that on."""
    if provenance is None or source is None or not isinstance(cfg, dict):
        return
    provenance.setdefault(node, {}).update(dict.fromkeys(cfg, source))


def resolve_pipeline_config_params(
    active: list[str],
    campaign_overlay: Mapping[str, Any],
    dataset_dir: Path | None,
    schema: PipelineSchema,
    judges: Mapping[str, JudgeSpec] | None = None,
    *,
    base_config: Mapping[str, Any] | None = None,
    provenance: MutableMapping[str, dict[str, ParamSource]] | None = None,
) -> dict[str, Any]:
    """The SINGLE definition of which node config a cycle id and a measurement key hash — shared
    with ``GET /origins``, so the prospective origin and the real one cannot diverge.
    ``base_config`` is the layer a CHECK-IN has instead of a dataset file: the backend's own
    captured declaration. ``provenance`` is the display sink of :func:`apply_node_overlay`."""

    pipeline_params: dict[str, Any] = {"steps": list(active)}
    if base_config is not None:
        pipeline_params = apply_node_overlay(
            pipeline_params,
            {node: cfg for node, cfg in base_config.items() if node in active},
            schema,
            source="backend",
            provenance=provenance,
        )
    if dataset_dir is not None:
        # Per-dataset overlay — sparse overrides on backend defaults (e.g. AIME →
        # OpenRouter+Mistral). `dataset_dir` is tenant-first, so ingested datasets honor it.
        dataset_overlay = {
            node: cfg
            for node, cfg in load_dataset_node_overlay(dataset_dir).items()
            if node in active
        }
        pipeline_params = apply_node_overlay(
            pipeline_params, dataset_overlay, schema, source="dataset", provenance=provenance
        )
    # The campaign's overlay layers on top (campaign > dataset); non-dict / inactive-node
    # entries are dropped here with an operator-visible log, then the survivors merge.
    valid_overrides: dict[str, Any] = {}
    for key, value in campaign_overlay.items():
        if isinstance(value, dict) and key in active:
            valid_overrides[key] = value
        elif isinstance(value, dict):
            logger.debug(
                "resolve_pipeline_config_params: skipping override for inactive node %r", key
            )
        else:
            logger.warning(
                "resolve_pipeline_config_params: ignoring non-nested override %r=%r "
                '(use {"node_name": {"param": value}} format)',
                key,
                value,
            )
    pipeline_params = apply_node_overlay(
        pipeline_params, valid_overrides, schema, source="campaign", provenance=provenance
    )
    # Identity contributions LAST and unoverridable: what a measurement was taken UNDER that no
    # operator wrote — the connector's and the judges', ONE channel, or a second place a fact can
    # enter the archive key.
    identity = {
        node: cfg
        for node, cfg in _identity_contributions(dataset_dir, judges, active).items()
        if node in active
    }
    if identity:
        pipeline_params = apply_node_overlay(
            pipeline_params, identity, schema, source="identity", provenance=provenance
        )
    return pipeline_params


def _identity_contributions(
    dataset_dir: Path | None, judges: Mapping[str, JudgeSpec] | None, active: list[str]
) -> dict[str, dict[str, Any]]:
    """What this measurement was taken UNDER, beyond the node configs themselves — the CONNECTOR's
    contribution and the JUDGES', which answer the same question and so share one channel.

    Sorted by term rather than kept in declaration order: two campaigns declaring the same graders
    in a different order measured the same thing (``judges/CLAUDE.md`` § Identity). Pure over its
    inputs, which is what keeps the live setup and the prospective-origin id (`GET /origins`)
    agreeing by construction rather than by both remembering to."""
    out: dict[str, dict[str, Any]] = {}
    if dataset_dir is not None:
        raw = read_yaml_optional(dataset_pipeline_path(dataset_dir))
        connector = CONNECTORS.get(str((raw or {}).get("backend_type") or ""))
        if connector is not None and connector.identity_config is not None:
            out.update(connector.identity_config(dataset_dir))
    if judges and active:
        from promptpotter.domain.pipeline_schema import stable_hash
        from promptpotter.judges import get as get_judge

        # Attached to the TERMINAL step: a judge grades the pipeline's answer, and that is the
        # node the answer comes out of. Any stable node would move the hash, but this one says
        # what the fingerprint actually qualifies.
        node = active[-1]
        out.setdefault(node, {})[JUDGE_INSTRUMENT_KEY] = stable_hash(
            [
                [term, get_judge(spec.name).fingerprint(spec)]
                for term, spec in sorted(judges.items())
            ]
        )
    return out


def missing_template_vars(rendered: str, declared: list[str]) -> list[str]:
    """The SINGLE definition of a required placeholder, shared by the mint-time setup check and the
    in-loop L1 guard. ``PROMPT_STRING_FIELDS`` are excluded: ``render()`` ASSEMBLES them."""
    return [
        v for v in declared if v not in PROMPT_STRING_FIELDS and "{{" + v + "}}" not in rendered
    ]


def _resolve_active_schema(
    pipeline_schema: PipelineSchema,
    *,
    exclude: list[str],
    narrowing: dict[str, NodeSearchNarrowing],
    dataset_dir: Path | None,
    workspace: Path | None = None,
) -> tuple[list[str], PipelineSchema]:
    """``steps`` is two shapes under one word: here the BACKEND's ``list[dict]`` from ``GET
    /pipeline``, on ``pipeline_params`` the reserved ``list[str]`` of active node names."""

    active = pipeline_schema.active_steps_excluding(exclude)

    filtered = pipeline_schema
    if exclude:
        filtered = pipeline_schema.filter_to_steps(active)
    # A discovered backend answers with its whole inventory; the DATASET says which of those nodes
    # are this campaign's. Unioned with the running chain so narrowing can only ever drop a node
    # nothing executes — a dataset that declares fewer nodes than its own pipeline runs must not
    # lose the difference. NOT `active_steps` alone: `promptpotter-self` declares `l2_context` and
    # `l3_plan` off-chain on purpose, and they are the L4 arc's main mutation targets.
    declared = dataset_declared_nodes(dataset_dir) if dataset_dir is not None else frozenset()
    if declared:
        keep = declared | set(filtered.active_steps)
        filtered = filtered.filter_to_steps(sorted(keep))
    # Campaign search-space narrowing — the per-node param-lock + allowed-values
    # subset, peer to exclude (above). The dataset declares the max; the campaign
    # snapshot may only narrow it.
    if narrowing:
        filtered = filtered.narrow(narrowing)
    # LAST, over the narrowed schema, so `selectable_models` reads the nodes as the run holds them.
    # This is what puts the model's answer in front of the SEARCH and not just the screen.
    if workspace is not None:
        filtered = filtered.model_copy(
            update={"model_capabilities": resolve_schema_menu(filtered, workspace=workspace)}
        )
    return active, filtered


def _apply_starting_prompts(
    pipeline_params: dict[str, Any],
    *,
    filtered: PipelineSchema,
    active: list[str],
    dataset_dir: Path,
    dataset_name: str,
    log: Callable[[str], None],
) -> None:
    """Assumes the caller checked ``has_dataset_prompts``."""

    prompt_nodes = [n for n in filtered.prompt_node_names() if n in active]
    if not prompt_nodes:
        # The dataset ships starting prompts but no active node declares
        # `prompt_info` — so the rendered prompt has nowhere to land and is
        # dropped before the wire. Silent here = every backend call runs with
        # an empty system prompt (the bug that made an ingested dataset score
        # 0% on email-replies). Fail loud: a generation node must advertise
        # `prompt_info` in GET /pipeline (or the dataset overlay).
        logger.warning(
            "configure_and_apply_pipeline: dataset %r has starting prompts but NO "
            "prompt-bearing node in the active pipeline %s — the prompt will "
            "NOT reach the backend. A generation node must declare `prompt_info`.",
            dataset_name,
            active,
        )
    prompt_info_by_node = {n.name: n.prompt_info for n in filtered.nodes}
    for pnode in prompt_nodes:
        template = load_node_prompt(dataset_dir, pnode, "default")
        rendered = template.render()
        # A prompt-bearing node declares the `{{vars}}` the backend injects by
        # literal substitution (query / research / output-schema). If the rendered
        # prompt omits one, that injection silently no-ops and the model never sees
        # it — the bug that made entity_profiling emit term-not-JSON → NO_RESULT.
        # Fail loud at setup, before a single degraded backend call. Exclude the
        # six-field decomposition names (PROMPT_STRING_FIELDS): some nodes (e.g. the
        # promptpotter-self L4 connector) declare THOSE as template_variables, but
        # `render()` ASSEMBLES them — they are never `{{substituted}}`.
        pinfo = prompt_info_by_node.get(pnode)
        declared = pinfo.template_variables if pinfo else []
        missing = missing_template_vars(rendered, declared)
        if missing:
            raise PayloadInvalidError(
                f"Dataset {dataset_name!r} prompt for node {pnode!r} is missing required "
                f"template variables {missing} — the backend injects these by literal "
                f"{{{{name}}}} substitution, so without them the query / research / output "
                f"schema never reach the model. Add the placeholders to "
                f"datasets/{dataset_name}/prompts/[{pnode}|default].yaml "
                f"(node declares: {declared}).",
                code="pipeline_config_invalid",
            )
        # Starting prompt lands on the sparse wire payload, on top of the merged
        # config above — never on `current_config`.
        pipeline_params.setdefault(pnode, {})["prompt"] = rendered
        log(f"Starting prompt: {dataset_name}/prompts/[{pnode}|default].yaml → {pnode}")


def _validate_model_ownership(
    pipeline_params: dict[str, Any],
    *,
    filtered: PipelineSchema,
    active: list[str],
    dataset_name: str,
) -> None:
    """The dataset OWNS its task model; a missing one is a setup bug, because a silent fall-through
    lets the backend's hidden ``GET /pipeline`` default decide. L4 optimizer nodes are exempt."""
    if filtered is None:
        return
    for name in active:
        node_obj = filtered.get_node(name)
        if node_obj and node_obj.is_llm and not pipeline_params.get(name, {}).get("model"):
            raise PayloadInvalidError(
                f"dataset {dataset_name!r}: LLM node {name!r} has no owned model. "
                f"Declare it in the dataset's pipeline.yaml::nodes.{name}.config.model "
                f"— the dataset owns its task model, never the backend default.",
                code="pipeline_config_invalid",
            )


def resolved_dataset_name(session: Session, campaign_config: CampaignConfig) -> str:
    """One rule, one place: this feeds the same identity as the mint seam's ``Campaign.dataset_name``,
    so a divergence renames campaigns and files measurements under a name nothing looks up."""
    return campaign_config.dataset_name or session.dataset_name or ""


class CampaignPipelineResponse(StrictModel):
    """One campaign's pipeline at one searchpoint — the body of ``GET /campaigns/{id}/pipeline``.
    The peer of ``readable_dataset_dir`` one question up: that seam answers which bytes are on disk,
    this one which values a campaign runs (``architecture.md`` §0, two resolution seams)."""

    campaign_id: str
    cycle_id: str
    dataset_name: str
    connector: str
    backend_type: str
    params: dict[str, Any] = Field(
        description="Resolved config as the engine holds it — the bytes a round document carries "
        "as `resolved_pipeline_params`, which makes that field this endpoint's check"
    )
    node_config_schema: dict[str, list[NodeConfigParam]]
    view: PipelineView | None
    node_output_schema: dict[str, NodeOutputSchema | None]
    model_capabilities: dict[str, ModelCapability]
    reach: dict[str, NodeReach]
    nests: NestedPipelineRef | None = Field(
        description="The inner pipeline this chain nests, if any — the L4 drill-in, on this read"
    )
    is_single_node: bool


def _recorded_identity(
    params: dict[str, Any],
    recorded: Mapping[str, Any],
    provenance: Mapping[str, dict[str, ParamSource]],
) -> dict[str, Any]:
    """Give a MEASURED point back the identity values it ran under. Recomputing is right on the RUN
    path and wrong on a finished read — the same defect as a shared ``pipeline.yaml``, one layer
    down. Which keys those are is read off the merge's own provenance, never a name list."""
    out = dict(params)
    for node, stamps in provenance.items():
        owned = [k for k, source in stamps.items() if source == "identity"]
        node_recorded = recorded.get(node)
        if not owned or not isinstance(node_recorded, dict):
            continue
        merged = dict(out.get(node) or {})
        merged.update({k: node_recorded[k] for k in owned if k in node_recorded})
        out[node] = merged
    return out


def _evolved_overlay(stores: Stores, at: SubjectSpec) -> tuple[dict[str, Any], dict[str, Any]]:
    """The addressed candidate's OWN sparse delta and the COMPLETE config it was measured under,
    off ONE document read. The delta is ``pipeline_params_override``, never
    ``resolved_pipeline_params`` — read as a delta that stamps every param ``evolved`` on every
    candidate; the complete one is for identity alone. ``stores`` is already the leaf."""
    if at.kind != "candidate" or not at.cycle_id:
        return {}, {}
    hop = CycleHop(campaign_id=at.campaign_id, cycle_id=at.cycle_id)
    index = stores.campaigns.load(hop) or {}
    # Newest round first: a candidate id is unique, but scanning down means a re-measured point
    # answers from the document that measured it last.
    for round_num in reversed(range(int(index.get("n_rounds") or 0) + 1)):
        doc = stores.campaigns.load_round_file(hop, round_num)
        if doc is None:
            continue
        for cand in doc.candidate_scores:
            if cand.candidate_id == at.candidate_id:
                return (
                    dict(cand.pipeline_params_override or {}),
                    dict(cand.resolved_pipeline_params or {}),
                )
    return {}, {}


def nested_pipeline_ref(dataset_dir: Path, view: PipelineView | None) -> NestedPipelineRef | None:
    """Owning an ``inner_tasks.yaml`` IS what makes a dataset outer (``runner/inner/tasks.py``);
    no name test recognises one. Here because both read doors need it and neither imports a router."""
    try:
        panel = load_inner_tasks(inner_tasks_path(dataset_dir))
    except InnerCycleUnscoreableError:
        # A read-only view must not raise where the runner would.
        return None
    node = next((n for n in (view.nodes if view else []) if n.kind == "measurement"), None)
    return NestedPipelineRef(node=node.id, dataset=panel.inner_benchmark) if node else None


def _config_floor(campaign: Campaign, dataset_dir: Path | None) -> CampaignConfig:
    """The base the frozen delta layers onto, in falling preference: the dataset template, the
    snapshot itself, the connector's defaults. There is no blank ``CampaignConfig`` to fall to —
    ``optimization.degradation_threshold`` is required — and a campaign outlives its dataset dir."""
    template = dataset_campaign_path(dataset_dir) if dataset_dir else None
    if template is not None and template.is_file():
        return load_dataset_campaign_config(template)
    if campaign.config:
        return load_campaign_config(campaign.config)
    defaults = CONNECTORS[campaign.backend_type or DEFAULT_CONNECTOR].default_optimization
    return load_campaign_config({"optimization": dict(defaults)})


def resolve_pipeline_for_draft(
    draft: DraftCampaign,
    *,
    campaign_id: str,
    cycle_id: str,
    workspace: Path | None = None,
) -> CampaignPipelineResponse:
    """The CHECK-IN arm of the one resolution — same merge, same rows, same provenance.
    ``is_checkin`` decides which layers EXIST, never which endpoint answers: seed, evolved and the
    dataset file are simply absent, leaving the backend's declaration under the operator's overlay.
    The dataset file is not consulted even when the slug exists — one file, shared by every
    campaign on it, is the defect this seam ends; a reused origin's values arrive via
    ``origins.py::draft_from_origin`` instead."""
    schema = parse_pipeline_response(rendered_pipeline_json(draft))
    # What was on OFFER before this draft narrowed anything, so a menu cannot shrink to its own pick.
    declared = parse_pipeline_response(declared_pipeline_json(draft))
    cfg = draft_campaign_config(draft)
    active, filtered = _resolve_active_schema(
        schema,
        exclude=list(cfg.exclude_nodes),
        narrowing=cfg.optimizer_narrowing,
        dataset_dir=None,
    )

    provenance: dict[str, dict[str, ParamSource]] = {}
    params = resolve_pipeline_config_params(
        active,
        cfg.pipeline_overlay,
        None,
        filtered,
        judges=cfg.judges,
        base_config={n.name: dict(n.current_config) for n in filtered.config_nodes},
        provenance=provenance,
    )
    rows = filtered.node_config_schema(
        values={n: c for n, c in params.items() if isinstance(c, dict)},
        sources=provenance,
        model_menu=filtered.selectable_models(),
        declared=declared,
    )
    return CampaignPipelineResponse(
        campaign_id=campaign_id,
        cycle_id=cycle_id,
        dataset_name=draft.slug,
        connector=draft.connector,
        backend_type=draft.connector,
        params=params,
        node_config_schema=rows,
        view=filtered.view,
        node_output_schema=filtered.node_output_schemas(),
        model_capabilities=resolve_schema_menu(filtered, workspace=workspace),
        reach=reach_map(rows),
        # A dataset dir is what declares an inner panel, and a check-in has none yet.
        nests=None,
        is_single_node=filtered.is_single_node,
    )


def resolve_pipeline_for_campaign(
    stores: Stores,
    campaign: Campaign,
    *,
    at: SubjectSpec,
    workspace: Path | None = None,
) -> CampaignPipelineResponse:
    """The ONE campaign-scoped resolution. Five layers through the one merge, each stamping its
    provenance: ``dataset`` < ``campaign`` (the FROZEN snapshot) < ``seed`` < ``evolved`` <
    ``identity``. ``stores`` must already be the store *campaign* lives in: for an L4 inner
    searchpoint the caller descends ``at.inside`` first, so manifest and rounds come off one tree.

    A campaign still AUTHORING takes the check-in arm — one door, two sets of layers. The FLAG
    decides it, never the presence of a draft: ``finalize_checkin_to_active`` leaves ``draft.json``
    in place, so a started campaign would otherwise answer from state it has since frozen."""
    hop_now = campaign.root_hop
    if is_checkin(stores.campaigns.cycle_dir(hop_now)):
        draft = load_checkin_draft(stores, campaign.campaign_id)
        if draft is not None:
            return resolve_pipeline_for_draft(
                draft,
                campaign_id=campaign.campaign_id,
                cycle_id=hop_now.cycle_id,
                workspace=workspace,
            )

    dataset_dir: Path | None
    try:
        dataset_dir = readable_dataset_dir(stores, campaign.dataset_name)
    except DatasetAccessError:
        # A campaign outlives its dataset dir; `_config_floor` below says what answers instead.
        dataset_dir = None

    raw = read_yaml_optional(dataset_pipeline_path(dataset_dir)) if dataset_dir else None
    schema = parse_pipeline_response(raw or {"nodes": {}, "pipelines": {"default": []}})

    live = _config_floor(campaign, dataset_dir)
    hop = CycleHop(campaign_id=campaign.campaign_id, cycle_id=at.cycle_id or campaign.root_cycle_id)
    seed = stores.campaigns.read_cycle_seed(hop) if at.cycle_id else None
    cfg = apply_inherited_overlay(live, campaign.config or {}, seed)

    active, filtered = _resolve_active_schema(
        schema,
        exclude=list(cfg.exclude_nodes),
        narrowing=cfg.optimizer_narrowing,
        dataset_dir=dataset_dir,
    )

    provenance: dict[str, dict[str, ParamSource]] = {}
    params = resolve_pipeline_config_params(
        active,
        cfg.pipeline_overlay,
        dataset_dir,
        filtered,
        judges=cfg.judges,
        provenance=provenance,
    )
    if seed is not None and seed.pipeline_overlay:
        params = apply_node_overlay(
            params, seed.pipeline_overlay, filtered, source="seed", provenance=provenance
        )
    evolved, measured = _evolved_overlay(stores, at)
    if evolved:
        params = apply_node_overlay(
            params, evolved, filtered, source="evolved", provenance=provenance
        )
    # LAST, after the layer that computed them: a measured point's identity is a fact of the
    # measurement, not of today's files.
    if measured:
        params = _recorded_identity(params, measured, provenance)

    rows = filtered.node_config_schema(
        values={n: c for n, c in params.items() if isinstance(c, dict)},
        sources=provenance,
        model_menu=filtered.selectable_models(),
        declared=schema,
    )
    return CampaignPipelineResponse(
        campaign_id=campaign.campaign_id,
        cycle_id=hop.cycle_id,
        dataset_name=campaign.dataset_name,
        connector=str((raw or {}).get("backend_name") or campaign.dataset_name),
        # The campaign's FROZEN kind — one `pipeline.yaml` serves every campaign on the slug.
        backend_type=campaign.backend_type,
        params=params,
        node_config_schema=rows,
        view=filtered.view,
        node_output_schema=filtered.node_output_schemas(),
        model_capabilities=resolve_schema_menu(filtered, workspace=workspace),
        reach=reach_map(rows),
        # On this read so the L4 drill-in needs no second fetch to stitch against.
        nests=nested_pipeline_ref(dataset_dir, filtered.view) if dataset_dir else None,
        is_single_node=filtered.is_single_node,
    )


def configure_and_apply_pipeline(
    session: Session,
    campaign_config: CampaignConfig,
    *,
    log: Callable[[str], None] = logger.info,
) -> dict[str, Any]:

    exclude = list(campaign_config.exclude_nodes)
    dataset_name = resolved_dataset_name(session, campaign_config)
    dataset_dir = session.dataset_config_dir

    active, filtered = _resolve_active_schema(
        session.pipeline_schema,
        exclude=exclude,
        narrowing=campaign_config.optimizer_narrowing,
        dataset_dir=dataset_dir,
        workspace=session.store.base_dir,
    )

    # The dataset→effective node-config merge (sparse `{steps}` base + dataset overlay +
    # campaign overrides) is the shared resolver — the SAME definition the prospective-origin
    # id uses, so a fresh run and `GET /origins` agree on which config the cycle id hashes.
    # This is where the connector `model`/config enters BOTH the measurement identity
    # (`content_hash`/`node_configs` over `session.pipeline_params`) AND the origin cycle id
    # (`build_origin_cycle_id` hashes these merged params). Starting prompts land on top below.
    pipeline_params = resolve_pipeline_config_params(
        active,
        campaign_config.pipeline_overlay,
        dataset_dir,
        filtered,
        judges=campaign_config.judges,
    )

    # Starting prompts from `{dataset_dir}/prompts/[<node>|default].yaml`, per prompt-bearing node.
    if dataset_dir is not None and has_dataset_prompts(dataset_dir):
        _apply_starting_prompts(
            pipeline_params,
            filtered=filtered,
            active=active,
            dataset_dir=dataset_dir,
            dataset_name=dataset_name,
            log=log,
        )

    _validate_model_ownership(
        pipeline_params, filtered=filtered, active=active, dataset_name=dataset_name
    )

    session.pipeline_schema = filtered
    session.pipeline_params = pipeline_params

    nodes_str = ", ".join(active)
    excl_str = f"  Excluded: {', '.join(exclude)}" if exclude else ""
    log(f"Active nodes: {nodes_str}{excl_str}")

    return pipeline_params

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

from promptpotter.application.campaign_config import CampaignConfig
from promptpotter.application.datasets.prompts import (
    dataset_declared_nodes,
    has_dataset_prompts,
    load_dataset_node_overlay,
    load_node_prompt,
)
from promptpotter.config.settings import (
    PROMPT_STRING_FIELDS,
)
from promptpotter.connectors import CONNECTORS
from promptpotter.domain.pipeline_schema import NodeSearchNarrowing
from promptpotter.infrastructure.store.dataset_access import dataset_pipeline_path
from promptpotter.infrastructure.store.io import read_yaml_optional
from promptpotter.shared.errors import PayloadInvalidError

if TYPE_CHECKING:
    from pathlib import Path

    from promptpotter.application.initialization.session import Session
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.judges.protocol import JudgeSpec

logger = logging.getLogger(__name__)

JUDGE_INSTRUMENT_KEY = "judge_instrument"
"""The node-config key a judge's fingerprint rides into measurement identity.

Twin of `connectors/harbor.py::INSTRUMENT_KEY`, and named apart from it because they answer
different questions: that one says which task bytes were run, this one says which grader read the
answer. Both are identity, neither is a tunable — nothing may put either in `param_keys`."""


__all__ = [
    "apply_node_overlay",
    "configure_and_apply_pipeline",
    "missing_template_vars",
    "resolve_pipeline_config_params",
    "resolved_dataset_name",
]


def apply_node_overlay(
    base: dict[str, Any],
    overlay: Mapping[str, Any],
    schema: PipelineSchema | None,
) -> dict[str, Any]:
    """The ONE per-node overlay merge. Depth is bounded by the DECLARATION (``param_types: object``
    merges one level deeper, so a sibling entry a parent earned survives), never by sniffing types."""
    merged = dict(base)
    for node, cfg in overlay.items():
        existing = merged.get(node)
        if not (isinstance(existing, dict) and isinstance(cfg, dict)):
            merged[node] = cfg
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
    return merged


def resolve_pipeline_config_params(
    active: list[str],
    pipeline_overrides: Mapping[str, Any],
    dataset_dir: Path | None,
    schema: PipelineSchema,
    judge: JudgeSpec | None = None,
) -> dict[str, Any]:
    """The SINGLE definition of which node config a cycle id and a measurement key hash — shared
    with ``GET /origins``, so the prospective origin and the real one cannot diverge."""

    pipeline_params: dict[str, Any] = {"steps": list(active)}
    if dataset_dir is not None:
        # Per-dataset overlay — sparse overrides on backend defaults (e.g. AIME →
        # OpenRouter+Mistral). `dataset_dir` is tenant-first, so ingested datasets honor it.
        dataset_overlay = {
            node: cfg
            for node, cfg in load_dataset_node_overlay(dataset_dir).items()
            if node in active
        }
        pipeline_params = apply_node_overlay(pipeline_params, dataset_overlay, schema)
    # Campaign overrides layer on top (override > dataset); non-dict / inactive-node
    # entries are dropped here with an operator-visible log, then the survivors merge.
    valid_overrides: dict[str, Any] = {}
    for key, value in pipeline_overrides.items():
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
    pipeline_params = apply_node_overlay(pipeline_params, valid_overrides, schema)
    # Identity contributions — LAST, never overridable: per-node entries that are part of what a
    # measurement was taken UNDER but that no operator wrote into a node config. ONE channel with
    # two contributors (the connector, and the judge); a second overlay pass beside this one would
    # be a second place a fact can enter the archive key.
    identity = {
        node: cfg
        for node, cfg in _identity_contributions(dataset_dir, judge, active).items()
        if node in active
    }
    if identity:
        pipeline_params = apply_node_overlay(pipeline_params, identity, schema)
    return pipeline_params


def _identity_contributions(
    dataset_dir: Path | None, judge: JudgeSpec | None, active: list[str]
) -> dict[str, dict[str, Any]]:
    """What this measurement was taken UNDER, beyond the node configs themselves.

    Both contributors answer the same question and so share one channel. The CONNECTOR's is
    resolved from the dataset dir's own ``backend_type`` (e.g. harbor's resolved task pins); the
    JUDGE's is its fingerprint, because an archive row is keyed on config and a judge swapped
    without moving the key would have every prior verdict replayed under the new grader.

    Pure over its inputs, which is what keeps the live setup and the prospective-origin id
    (`GET /origins`) agreeing by construction rather than by both remembering to.
    """
    out: dict[str, dict[str, Any]] = {}
    if dataset_dir is not None:
        raw = read_yaml_optional(dataset_pipeline_path(dataset_dir))
        connector = CONNECTORS.get(str((raw or {}).get("backend_type") or ""))
        if connector is not None and connector.identity_config is not None:
            out.update(connector.identity_config(dataset_dir))
    if judge is not None and active:
        from promptpotter.judges import get as get_judge

        # Attached to the TERMINAL step: the judge grades the pipeline's answer, and that is the
        # node the answer comes out of. Any stable node would move the hash, but this one says
        # what the fingerprint actually qualifies.
        node = active[-1]
        out.setdefault(node, {})[JUDGE_INSTRUMENT_KEY] = get_judge(judge.name).fingerprint(judge)
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
    )

    # The dataset→effective node-config merge (sparse `{steps}` base + dataset overlay +
    # campaign overrides) is the shared resolver — the SAME definition the prospective-origin
    # id uses, so a fresh run and `GET /origins` agree on which config the cycle id hashes.
    # This is where the connector `model`/config enters BOTH the measurement identity
    # (`content_hash`/`node_configs` over `session.pipeline_params`) AND the origin cycle id
    # (`build_origin_cycle_id` hashes these merged params). Starting prompts land on top below.
    pipeline_params = resolve_pipeline_config_params(
        active,
        campaign_config.pipeline_overrides,
        dataset_dir,
        filtered,
        judge=campaign_config.judge,
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

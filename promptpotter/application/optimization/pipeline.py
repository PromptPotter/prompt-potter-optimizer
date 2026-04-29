"""Optimizer pipeline — everything about *talking to the optimizer LLM*.

Five labeled sections, in dependency order:

1. **LLM call primitive** (``llm_call``, ``run_optimizer_node``,
   ``get_optimizer_schema``) — schema loader + 429-Retry-After loop +
   token emission + recorder hook. The single chokepoint every optimizer
   node passes through.

2. **Prompt loading** (``load_optimizer_prompt``, ``_load_local``,
   ``_try_langfuse``, ``push_all_to_langfuse``, ``list_optimizer_prompts``)
   — Langfuse production → local JSON fallback.

3. **Prompt decomposition** (``decompose_prompt_fields``,
   ``decompose_task_context``) — one-time restructure pass that turns
   raw context into the 8-field prompt scheme + ``TaskDecomposition``.

4. **Formatting helpers** (``format_pipeline_section``,
   ``format_axis_digest_block``, ``format_runtime_failure_line``,
   ``format_runtime_failures_for_l3``, ``TrajectoryReport``,
   ``build_trajectory_report``, ``build_cross_candidate_diff``,
   ``candidate_summaries``, ``summarize_warning_inventory``,
   ``warning_summary``, ``format_escalation_report``) — pure string
   builders consumed by section renderers and the L1 round driver.

5. **Layer dispatch + transitions** (``Layer``, ``LayerContext``,
   ``assemble_dispatch_msg``, ``run_l1_critique``,
   ``format_l1_critique_for_prompt``, ``CritiqueContext``,
   ``LayerTransition``, ``L2RefineStrategy``, ``L3ModifyPlan``) — the
   five-noun flow: archive → AxisIndex (cached) → LayerContext (per-call
   payload) → sections (pure formatters) → dispatch_msg, plus the L2/L3
   transition templates that run an optimizer node and project the
   parsed result back into ``OptSearchPoint`` updates.
"""

from __future__ import annotations

import enum
import functools
import hashlib
import json
import logging
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Literal, cast

from promptpotter.application.optimization.elimination import (
    candidate_keys_from_schema,
    get_candidates,
)
from promptpotter.application.scoring.metrics import extract_sample_diagnostics, find_rank
from promptpotter.config.settings import PROMPT_STRING_FIELDS
from promptpotter.domain.opt_search_point import OptSearchPoint, PromptTemplate
from promptpotter.domain.phases import CampaignPhase
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.infrastructure.llm import (
    MAX_429_ATTEMPTS,
    LLMClientBase,
    LLMResponse,
    TokenUsage,
    emit_token_usage,
    extract_parsed_json,
    parse_retry_after,
    wait_with_countdown,
)
from promptpotter.infrastructure.store.base import (
    read_json_optional,
    validate_path_component,
    write_json,
)
from promptpotter.shared.errors import is_error_result
from promptpotter.shared.hashing import HASH_TRUNCATE

if TYPE_CHECKING:
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.application.optimization.l1 import L1ScoringResult
    from promptpotter.domain.results import CandidateProposal
    from promptpotter.domain.scoring import QueryResult
    from promptpotter.infrastructure.persistence import RoundRecorder

logger = logging.getLogger(__name__)

__all__ = [
    "LAYER_ORDER",
    "CritiqueContext",
    "L2RefineStrategy",
    "L3ModifyPlan",
    "Layer",
    "LayerContext",
    "LayerTransition",
    "TrajectoryReport",
    "TransitionAction",
    "TransitionResult",
    "assemble_dispatch_msg",
    "build_cross_candidate_diff",
    "build_trajectory_report",
    "candidate_summaries",
    "compile_critique_context",
    "compile_layer_context",
    "decompose_prompt_fields",
    "decompose_prompt_fields_cached",
    "decompose_task_context",
    "extract_runtime_failure_fields",
    "format_axis_digest_block",
    "format_escalation_report",
    "format_l1_critique_for_prompt",
    "format_pipeline_section",
    "format_runtime_failure_line",
    "format_runtime_failures_for_l3",
    "get_optimizer_schema",
    "list_optimizer_prompts",
    "llm_call",
    "load_cached_decomposition",
    "load_optimizer_prompt",
    "push_all_to_langfuse",
    "run_l1_critique",
    "run_optimizer_node",
    "save_decomposition_cache",
    "summarize_warning_inventory",
    "warning_summary",
]


# ===========================================================================
# Section 1 — LLM call primitive (schema loader + chokepoint)
# ===========================================================================

_PIPELINE_PATH = Path(__file__).parent / "optimizer_pipeline.json"


@functools.lru_cache(maxsize=1)
def get_optimizer_schema() -> PipelineSchema:
    """Load optimizer_pipeline.json as PipelineSchema (cached)."""
    from promptpotter.domain.pipeline_schema import PipelineNode

    data = json.loads(_PIPELINE_PATH.read_text())
    nodes = [
        PipelineNode(
            name=name,
            current_config=node_data.get("config", {}),
            param_keys=set(node_data.get("optimizer", {}).get("param_keys", [])),
        )
        for name, node_data in data.get("nodes", {}).items()
    ]
    return PipelineSchema(
        name=data.get("name", ""),
        version=data.get("version", ""),
        nodes=nodes,
    )


_LLM_DEFAULTS = {"temperature": 0.0, "output_format": "text"}


async def llm_call(
    llm_client: LLMClientBase,
    messages: list[dict[str, str]],
    *,
    node: str | None = None,
    config: dict | None = None,
    trace_meta: dict | None = None,
    json_schema: dict | None = None,
    recorder: RoundRecorder | None = None,
    **overrides,
) -> LLMResponse:
    """LLM call with config-driven defaults; precedence: _LLM_DEFAULTS < config < overrides."""
    if config is None:
        if node:
            schema_node = get_optimizer_schema().get_node(node)
            if schema_node is None:
                raise KeyError(f"Unknown optimizer node: {node}")
            config = schema_node.current_config
        else:
            config = {}
    merged = {**_LLM_DEFAULTS, **config, **overrides}

    _t0 = time.monotonic()

    effective_output_format = cast(
        Literal["text", "json", "json_schema"],
        "json_schema" if json_schema else merged["output_format"],
    )

    # 429 honor-Retry-After loop, bounded. Server sets the header per RFC 7231;
    # if missing or attempts run out, surface the SDK exception unchanged.
    for attempt in range(MAX_429_ATTEMPTS):
        try:
            response = await llm_client.chat(
                messages=messages,
                model=merged.get("model"),
                temperature=merged["temperature"],
                max_tokens=merged.get("max_tokens"),
                output_format=effective_output_format,
                json_schema=json_schema,
            )
            break
        except Exception as exc:
            if getattr(exc, "status_code", None) != 429:
                raise
            resp = getattr(exc, "response", None)
            wait = parse_retry_after(getattr(resp, "headers", None) if resp is not None else None)
            if wait is None or wait <= 0 or attempt == MAX_429_ATTEMPTS - 1:
                raise
            logger.warning(
                "Rate limit on %s (attempt %d/%d); waiting %.1fs",
                node or "llm_call",
                attempt + 1,
                MAX_429_ATTEMPTS,
                wait,
            )
            await wait_with_countdown(wait + 1.0, node or "optimizer")

    duration_s = round(time.monotonic() - _t0, 2)

    emit_token_usage(
        TokenUsage(
            node=node or "llm_call",
            kind="optimizer",
            input_tokens=response.usage.get("prompt_tokens", 0),
            output_tokens=response.usage.get("completion_tokens", 0),
            duration_s=duration_s,
        )
    )

    if recorder is not None:
        response_data: dict | str
        try:
            response_data = json.loads(response.content)
        except (json.JSONDecodeError, TypeError):
            response_data = response.content

        action: dict = {
            "type": node or "llm_call",
            "config": {
                "model": merged.get("model"),
                "temperature": merged["temperature"],
                "max_tokens": merged.get("max_tokens"),
            },
            "response": response_data,
            "usage": response.usage,
            "model": response.model,
            "duration_s": duration_s,
        }
        if trace_meta:
            action.update(trace_meta)
        else:
            action["messages"] = messages
        recorder.add_action(action)

    return response


async def run_optimizer_node(
    *,
    template_name: str,
    compile_vars: dict,
    llm_client: LLMClientBase,
    model: str | None,
    temperature: float = 0.0,
    json_schema: dict | None = None,
    user_content: str | None = None,
    recorder: RoundRecorder | None = None,
) -> tuple[Any, str]:
    """Load prompt template, compile, call LLM, parse JSON → (parsed_result, prompt_text)."""
    template = load_optimizer_prompt(template_name)
    prompt = template.compile_prompt(**compile_vars)
    if user_content is not None:
        messages: list[dict[str, str]] = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_content},
        ]
    else:
        messages = [{"role": "user", "content": prompt}]
    response = await llm_call(
        llm_client,
        messages=messages,
        node=template_name,
        model=model,
        temperature=temperature,
        json_schema=json_schema,
        recorder=recorder,
        trace_meta={
            "template_name": template_name,
            "template_fields": template.prompt_field_dict(),
            "variables": compile_vars,
        },
    )
    return extract_parsed_json(response), prompt


# ===========================================================================
# Section 2 — Prompt loading (Langfuse → local JSON fallback)
# ===========================================================================

_PROMPT_DIR = Path(__file__).parent / "prompts"
_LANGFUSE_PREFIX = "optimizer_"
_LANGFUSE_CACHE_TTL = 300  # seconds


@functools.lru_cache(maxsize=32)
def _load_local(name: str) -> PromptTemplate:
    path = _PROMPT_DIR / f"{name}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return PromptTemplate(**data)


def _try_langfuse(name: str) -> PromptTemplate | None:
    """Fetch prompt from Langfuse 'production' label (None on any failure)."""
    try:
        from promptpotter.config.settings import settings

        if not settings.LANGFUSE_PROMPTS_ENABLED:
            return None

        from promptpotter.infrastructure.tracing import LangfuseLogger

        lf = LangfuseLogger.get_instance()
        if not lf.enabled or not lf.client:
            return None

        prompt_client = lf.client.get_prompt(
            name=f"{_LANGFUSE_PREFIX}{name}",
            label="production",
            cache_ttl_seconds=_LANGFUSE_CACHE_TTL,
        )
        config = getattr(prompt_client, "config", None)
        if not config or not isinstance(config, dict):
            return None

        return PromptTemplate(**config)
    except Exception:
        logger.debug("Langfuse prompt fetch failed for %s", name, exc_info=True)
        return None


def load_optimizer_prompt(name: str) -> PromptTemplate:
    """Load optimizer prompt: Langfuse production → local JSON fallback."""
    lf_prompt = _try_langfuse(name)
    return lf_prompt or _load_local(name)


def push_all_to_langfuse(*, label: str = "production") -> dict[str, bool]:
    """Push local JSON prompt defaults to Langfuse; returns {name: success}."""
    from promptpotter.infrastructure.tracing import LangfuseLogger

    lf = LangfuseLogger.get_instance()
    if not lf.enabled or not lf.client:
        logger.warning("push_all_to_langfuse: Langfuse not available")
        return {}

    results: dict[str, bool] = {}
    for name in list_optimizer_prompts():
        try:
            tpl = _load_local(name)
            lf.client.create_prompt(
                name=f"{_LANGFUSE_PREFIX}{name}",
                prompt=tpl.render(),
                config=tpl.model_dump(),
                labels=[label],
                tags=["optimizer", "meta-prompt"],
                commit_message=f"Push local default for {name}",
            )
            results[name] = True
            logger.info("Pushed optimizer prompt %s to Langfuse", name)
        except Exception:
            logger.warning("Failed to push %s to Langfuse", name, exc_info=True)
            results[name] = False

    # Clear local cache so next load picks up Langfuse versions
    _load_local.cache_clear()
    return results


def list_optimizer_prompts() -> list[str]:
    """List available optimizer prompt names from local JSON files."""
    return sorted(p.stem for p in _PROMPT_DIR.glob("*.json"))


# ===========================================================================
# Section 3 — Prompt decomposition (raw text → 8-field prompt scheme)
# ===========================================================================


async def decompose_prompt_fields(
    context_input: Any,
    llm_client: LLMClientBase,
    model: str | None = None,
) -> dict:
    """LLM-restructure raw context → Layer 1 prompt fields + task_context sub-dict."""
    if isinstance(context_input, dict):
        user_content = (
            "The user has provided partial Layer 1 fields for a prompt. "
            "Validate them, fill any gaps, and suggest improvements.\n\n"
            f"Provided fields:\n{json.dumps(context_input, indent=2)}"
        )
    else:
        user_content = (
            "The user has provided a raw context description. Parse it into "
            "structured Layer 1 prompt fields.\n\n"
            f"Context:\n{context_input}"
        )

    consultation_instruction = (
        "Return a JSON object with exactly these keys. Use empty string for "
        "fields that don't apply. Be concise and actionable."
    )

    result, _ = await run_optimizer_node(
        template_name="restructure",
        compile_vars={"consultation_instruction": consultation_instruction},
        llm_client=llm_client,
        model=model,
        user_content=user_content,
    )

    for key in (
        "persona",
        "task_intent",
        "problem_description",
        "instruction",
        "thinking_style",
        "answer_format",
    ):
        result.setdefault(key, "")

    tc = result.setdefault("task_context", {})
    for key in (
        "domain",
        "pipeline_purpose",
        "data_characteristics",
        "optimization_goals",
        "key_challenges",
    ):
        tc.setdefault(key, "")

    return result


def _decomposition_cache_path(base_dir: Path, backend_id: str) -> Path:
    validate_path_component(backend_id)
    return base_dir / backend_id / "restructure_cache.json"


def load_cached_decomposition(
    base_dir: Path,
    backend_id: str,
    alias_hashes: set[str],
) -> dict | None:
    """Scan *alias_hashes* for a cached restructure result."""
    cache = read_json_optional(_decomposition_cache_path(base_dir, backend_id))
    if not cache:
        return None
    for h in alias_hashes:
        entry = cache.get(h)
        if entry:
            return entry["layer1_fields"]
    return None


def save_decomposition_cache(
    base_dir: Path,
    backend_id: str,
    rp_hash: str,
    layer1_fields: dict,
) -> None:
    """Persist restructure output keyed by *rp_hash*."""
    path = _decomposition_cache_path(base_dir, backend_id)
    cache = read_json_optional(path) or {}
    cache[rp_hash] = {
        "layer1_fields": layer1_fields,
        "cached_at": datetime.now(UTC).isoformat(),
    }
    write_json(path, cache)


async def decompose_prompt_fields_cached(
    context_input: Any,
    llm_client: LLMClientBase,
    *,
    model: str | None = None,
    store_base_dir: Path | None = None,
    backend_id: str = "",
    alias_hashes: set[str] | None = None,
    rp_hash: str = "",
    force: bool = False,
) -> tuple[dict, bool]:
    """Disk-cached decompose_prompt_fields; returns (layer1_fields, was_cached)."""
    can_cache = bool(store_base_dir and backend_id)

    if can_cache and not force and alias_hashes:
        assert store_base_dir is not None
        cached = load_cached_decomposition(store_base_dir, backend_id, alias_hashes)
        if cached is not None:
            logger.debug("decompose_prompt_fields_cached: hit (alias group)")
            return cached, True

    layer1_fields = await decompose_prompt_fields(context_input, llm_client, model=model)

    if can_cache:
        assert store_base_dir is not None
        save_key = rp_hash
        if not save_key:
            instruction = (
                context_input
                if isinstance(context_input, str)
                else json.dumps(context_input, sort_keys=True)
            )
            save_key = hashlib.sha256(instruction.encode()).hexdigest()[:HASH_TRUNCATE]
        save_decomposition_cache(store_base_dir, backend_id, save_key, layer1_fields)

    return layer1_fields, False


async def decompose_task_context(
    task_description: str,
    llm_client: LLMClientBase,
    model: str,
    store_base_dir: Path | None = None,
    backend_id: str = "",
) -> tuple[TaskDecomposition, str | None, bool]:
    """Decompose task description → ``(task_context, consultation, was_cached)`` (disk-cached)."""
    if not task_description:
        return TaskDecomposition(), None, False

    rp_hash = hashlib.sha256(f"task_ctx:{task_description}".encode()).hexdigest()[:16]

    result, was_cached = await decompose_prompt_fields_cached(
        task_description,
        llm_client,
        model=model,
        store_base_dir=store_base_dir,
        backend_id=backend_id,
        rp_hash=rp_hash,
    )

    tc_dict = result.get("task_context", {})
    tc_dict["raw_description"] = task_description
    return TaskDecomposition.from_dict(tc_dict), result.get("consultation"), was_cached


# ===========================================================================
# Section 4 — Formatting helpers (pure string builders)
# ===========================================================================


def format_pipeline_section(
    pipeline_params: dict | None,
    pipeline_schema: PipelineSchema | None,
) -> str:
    """Build the pipeline parameters section for L2/L3 LLM prompts."""
    if not pipeline_schema:
        return ""
    param_keys = pipeline_schema.node_param_keys()
    if not param_keys:
        return ""
    lines = ["AVAILABLE PIPELINE PARAMETERS (in pipeline execution order):\n"]
    for step_name, keys in param_keys.items():
        current_vals = {}
        if pipeline_params:
            step_cfg = pipeline_params.get(step_name, {})
            if isinstance(step_cfg, dict):
                current_vals = {k: step_cfg.get(k, "?") for k in keys}
        lines.append(f"  {step_name}: {', '.join(sorted(keys))}")
        if current_vals:
            lines.append(f"    current: {json.dumps(current_vals)}")
        lines.append("")
    return "\n".join(lines) + "\n"


def format_axis_digest_block(
    digest: dict | None,
    key_labels: dict[str, str],
    *,
    header: str = "",
) -> str:
    """Render an AxisIndex digest dict as a labelled block. Empty digest → ``""``."""
    if not digest:
        return ""
    entries = [f"  {label}: {val}" for key, label in key_labels.items() if (val := digest.get(key))]
    if not entries:
        return ""
    return "\n".join([header, *entries]) if header else "\n".join(entries)


def extract_runtime_failure_fields(rf: dict) -> tuple[int, str, str, int]:
    """Parse the common (rate_pct, dominant, cfg_str, n_evaluated) tuple from an RF dict."""
    rate_pct = round(float(rf.get("degraded_rate", 0.0)) * 100)
    dominant = rf.get("dominant_warning", "unknown")
    cfg = rf.get("observed_config") or {}
    cfg_parts = [f"{k}={v}" for k, v in cfg.items() if k != "prompt"]
    cfg_str = ", ".join(cfg_parts[:6]) if cfg_parts else "(config n/a)"
    return rate_pct, dominant, cfg_str, rf.get("total_scored", 0)


def format_runtime_failure_line(rf: dict, label: str = "") -> list[str]:
    """Render one runtime-failure dict as a 2-line warning block."""
    rate_pct, dominant, cfg_str, n = extract_runtime_failure_fields(rf)
    if label:
        head = f"  ⚠ {label[:60]} — {rate_pct}% degraded on {n} queries, dominant={dominant}"
    else:
        head = f"  ⚠ {dominant} — {rate_pct}% degraded on {n} queries"
    return [head, f"    observed_config: {cfg_str}"]


def format_runtime_failures_for_l3(runtime_failures: list[dict] | None) -> str:
    """Render the accumulated RuntimeFailure trail for L3 modify_plan (L2 self-heal exhausted)."""
    if not runtime_failures:
        return ""

    lines = [
        "L3 RUNTIME FAILURE TRAIL — L2 SELF-HEALING EXHAUSTED",
        "  (these patterns survived L2's prior strategy adjustments; replan required)",
        "",
    ]
    for rf in runtime_failures:
        lines.extend(format_runtime_failure_line(rf))

    lines.append("")
    lines.append(
        "  ↳ Required L3 action: treat these as discovered constraints on the "
        "search space. Your replan must either change pipeline_params to "
        "escape the failing region (switch model, raise max_tokens floor, "
        "swap a node) OR change the plan text to steer L1/L2 around it. "
        "Do NOT propose a plan that re-enters the same failure mode."
    )
    return "\n".join(lines)


@dataclass
class TrajectoryReport:
    """Campaign trajectory; classification ∈ healthy/plateau/oscillating/ceiling."""

    text: str
    classification: str
    description: str
    recommended_action: str


def build_trajectory_report(rounds: list[Any]) -> TrajectoryReport | None:
    """Compute trend direction, stall streak, and classification from round accuracies."""
    if not rounds or len(rounds) < 2:
        return None

    accuracies = [r.accuracy for r in rounds]
    best_acc = max(accuracies)
    best_round = accuracies.index(best_acc)
    current_acc = accuracies[-1]
    gap = best_acc - current_acc
    rounds_since_best = len(accuracies) - 1 - best_round

    deltas = [accuracies[i] - accuracies[i - 1] for i in range(1, len(accuracies))]
    recent = deltas[-5:]
    improvements = sum(1 for d in recent if d > 0.005)
    regressions = sum(1 for d in recent if d < -0.005)
    flat = len(recent) - improvements - regressions

    stall = 0
    for d in reversed(deltas):
        if abs(d) < 0.01:
            stall += 1
        else:
            break

    if improvements > len(recent) * 0.6:
        direction = "improving"
    elif regressions > len(recent) * 0.6:
        direction = "degrading"
    elif stall >= 3:
        direction = "stalled"
    else:
        direction = "oscillating"

    delta_str = ", ".join(f"{d:+.1%}" for d in recent)
    text = (
        f"Trend: {direction} | "
        f"Current: {current_acc:.1%} | Best: {best_acc:.1%} (round {best_round}) | "
        f"Gap: {gap:.1%} | Stall: {stall} rounds | "
        f"Recent deltas: [{delta_str}]"
    )

    # Need ≥ 3 rounds to classify; fall back to healthy/mixed otherwise.
    if len(rounds) < 3:
        return TrajectoryReport(
            text=text,
            classification="healthy",
            description="Too few rounds to classify",
            recommended_action="continue current approach",
        )

    if improvements >= len(recent) * 0.5 and regressions <= 1:
        classification = "healthy"
        description = f"Improving — {improvements}/{len(recent)} recent rounds improved"
        action = "continue current approach"
    elif rounds_since_best >= 5 and stall >= 3:
        classification = "ceiling"
        description = (
            f"Hard ceiling at {best_acc:.1%} (round {best_round}) — "
            f"{rounds_since_best} rounds without new best"
        )
        action = "escalate — try fundamentally different axes or strategy"
    elif improvements > 0 and regressions > 0 and abs(improvements - regressions) <= 1:
        classification = "oscillating"
        description = (
            f"Oscillating — {improvements} improvements, {regressions} regressions "
            f"in last {len(recent)} rounds"
        )
        action = "narrow search space — candidates are exploring unstable region"
    elif flat >= len(recent) * 0.6 or stall >= 3:
        classification = "plateau"
        description = (
            f"Plateau — {stall} consecutive rounds with < 1% change, gap to best: {gap:.1%}"
        )
        action = "widen search — try different axes or larger parameter ranges"
    else:
        classification = "healthy"
        description = "Mixed progress — no clear pattern"
        action = "continue current approach"

    return TrajectoryReport(
        text=text,
        classification=classification,
        description=description,
        recommended_action=action,
    )


def build_cross_candidate_diff(
    winner_results: list[dict],
    all_candidate_results: dict[str, list[dict]],
    candidate_scores: list[dict],
) -> str | None:
    """Surface missed opportunities — queries other candidates hit but winner missed."""
    if not winner_results or not all_candidate_results or len(all_candidate_results) < 2:
        return None

    winner_hits: set[str] = set()
    winner_misses: set[str] = set()
    for r in winner_results:
        q = r.get("query", "")
        if not q:
            continue
        if r.get("hit"):
            winner_hits.add(q)
        else:
            winner_misses.add(q)

    if not winner_misses:
        return None

    missed_by: dict[str, list[str]] = {}
    for cand_id, results in all_candidate_results.items():
        desc = cand_id
        for cs in candidate_scores:
            if cs.get("label") == cand_id or str(cs.get("idx")) == cand_id:
                desc = cs.get("changes_description", cand_id)[:60]
                break

        for r in results:
            q = r.get("query", "")
            if q in winner_misses and r.get("hit"):
                missed_by.setdefault(q, []).append(desc)

    if not missed_by:
        return None

    sorted_missed = sorted(missed_by.items(), key=lambda x: -len(x[1]))
    parts = []
    for q, candidates in sorted_missed[:5]:
        parts.append(f"  {q[:60]} — solved by {len(candidates)} other candidate(s)")
    total = len(missed_by)
    return (
        f"{total} missed opportunities (queries other candidates solved but winner missed):\n"
        + "\n".join(parts)
    )


def candidate_summaries(proposals: list[CandidateProposal]) -> list[dict]:
    """Build compact per-candidate summary dicts for phase event data."""
    summaries = []
    for i, cp in enumerate(proposals):
        prompt_fields = {k: getattr(cp.osp, k) for k in PROMPT_STRING_FIELDS if getattr(cp.osp, k)}
        summary: dict = {
            "idx": i,
            "changes_description": cp.osp.lineage.changes_description or "",
        }
        if cp.node_overrides:
            summary["pipeline_params_override"] = cp.node_overrides
        if prompt_fields:
            summary["prompt_fields"] = prompt_fields
        summaries.append(summary)
    return summaries


def summarize_warning_inventory(tracker: dict[str, dict]) -> str:
    """Group queries by warning type with per-query hit/miss stats."""
    by_warning: dict[str, list[tuple[str, dict]]] = {}
    for query, entry in tracker.items():
        for wtype, _count in entry.get("warnings", {}).items():
            by_warning.setdefault(wtype, []).append((query, entry))

    if not by_warning:
        return ""

    max_rounds = max((e.get("rounds_seen", 0) for e in tracker.values()), default=0)
    lines = [f"## RECURRING PIPELINE WARNINGS (across {max_rounds} rounds)"]
    for wtype, entries in sorted(by_warning.items(), key=lambda x: -len(x[1])):
        lines.append(f"  {wtype} — {len(entries)} queries affected:")
        for query, entry in sorted(
            entries,
            key=lambda x: -x[1]["warnings"].get(wtype, 0),
        )[:10]:
            wcount = entry["warnings"].get(wtype, 0)
            seen = entry["rounds_seen"]
            hits = entry["hits"]
            lines.append(f"    {query[:70]}  ({wcount}/{seen} rounds, {hits} hits)")
    return "\n".join(lines)


def warning_summary(tracker: dict[str, dict]) -> tuple[int, str]:
    """Return (warned_count, top_warning_type) from the warning inventory."""
    if not tracker:
        return 0, ""
    warned_count = sum(1 for e in tracker.values() if e.get("warnings"))
    all_wtypes: dict[str, int] = {}
    for e in tracker.values():
        for wt, c in e.get("warnings", {}).items():
            all_wtypes[wt] = all_wtypes.get(wt, 0) + c
    top_warning = max(all_wtypes, key=all_wtypes.get) if all_wtypes else ""  # type: ignore[arg-type]
    return warned_count, top_warning


def format_escalation_report(
    escalation_check_result: dict | None,
    escalation_journal: list[dict] | None,
    pipeline_params: dict | None = None,
    pipeline_schema: PipelineSchema | None = None,
) -> str:
    """Build the escalation diagnostics section for L2 prompts (empty if no context)."""
    if not escalation_check_result:
        return ""

    dominant = escalation_check_result.get("dominant_warning", "unknown")
    step_name = dominant.split(":")[0] if ":" in dominant else "unknown"
    rate = escalation_check_result.get("degraded_rate", 0)

    wt = escalation_check_result.get("warning_types", {})
    wt_str = ", ".join(f"{k} ({v})" for k, v in sorted(wt.items(), key=lambda x: -x[1]))

    lines = [
        f"PIPELINE STABILITY REPORT ({step_name}):\n",
        f"  Current degradation: {rate:.0%} of queries ({wt_str})",
    ]

    step_cfg = (pipeline_params or {}).get(step_name, {})
    if isinstance(step_cfg, dict) and step_cfg:
        lines.append(f"  Current {step_name} config: {json.dumps(step_cfg)}")

    lines.append("")

    if escalation_journal:
        lines.append("  Tried configs and stability:")
        for entry in escalation_journal:
            step = entry.get("problem_step", "unknown")
            ec = entry.get("step_config", {})
            prev_rate = entry.get("degraded_rate", 0)
            outcome = entry.get("outcome_degraded_rate")
            outcome_str = f" -> {outcome:.0%}" if outcome is not None else ""
            cfg_parts = [f"{k}={v!r}" for k, v in sorted(ec.items())]
            lines.append(
                f"    Round {entry.get('round', '?')}: "
                f"{step} [{', '.join(cfg_parts) or 'defaults'}]"
                f" | {prev_rate:.0%} degraded{outcome_str}"
            )
        lines.append("")

    if pipeline_schema:
        all_keys = pipeline_schema.node_param_keys()
        step_keys = all_keys.get(step_name, set())
        if step_keys:
            lines.append(f"  Available {step_name} parameters: {', '.join(sorted(step_keys))}")

    lines.append(
        "  The configurations above are all unstable. Suggest different "
        "parameter values to stabilize the pipeline."
    )
    lines.append("")
    return "\n".join(lines) + "\n"


# ===========================================================================
# Section 5 — Layer dispatch + transitions
# ===========================================================================


class Layer(enum.StrEnum):
    """Optimizer layer that consumes a dispatch_msg."""

    L1_GENERATE = "L1_GENERATE"
    L1_CRITIQUE = "L1_CRITIQUE"
    L2 = "L2"
    L3 = "L3"


_PROMPT_BLOAT_CHARS = 3000
_RF_CFG_AXES = ("model", "temperature", "max_tokens", "reasoning_effort")

_L1_AXIS_LABELS: dict[str, str] = {
    "failure_clusters": "Common failure patterns",
    "dead_queries": "Dead queries (never hit)",
    "top_axes": "High-impact axes",
    "top_values": "Best-performing values",
}
_L1C_AXIS_LABELS: dict[str, str] = {
    "discriminating_queries": "Discriminating queries",
    "failure_clusters": "Failure clusters",
    "tractability": "Query tractability",
    "exhausted_axes": "Exhausted axes (DO NOT suggest these)",
    "value_trends": "Value trends",
    "improvement_attribution": "WHAT WORKED",
}
_L2_AXIS_LABELS: dict[str, str] = {
    "axis_rankings": "Axis impact rankings",
    "bottleneck_distribution": "Bottleneck distribution",
    "failure_group_insights": "Failure group x axis",
    "persistent_failures": "Persistent failures",
    "volatile_queries": "Volatile queries (oscillating)",
}
_L3_AXIS_LABELS: dict[str, str] = {
    "axis_rankings": "Axis impact rankings",
    "bottleneck_distribution": "Bottleneck distribution",
    "failure_clusters": "Failure clusters",
    "persistent_failures": "Persistent failures",
}

_TASK_CONTEXT_SKIP = frozenset({"raw_description", "upstream_context", "downstream_context"})


# ---------------------------------------------------------------------------
# L1 critique — pre-pass cross-cutting facts (computed once per call)
# ---------------------------------------------------------------------------


@dataclass
class CritiqueContext:
    """L1_CRITIQUE pre-pass — cross-cutting facts computed once per call."""

    prompt_chars: int = 0
    candidate_keys: list[str] | None = None
    nm_queries: set[str] = field(default_factory=set)
    anomalies: list[str] = field(default_factory=list)
    rank_text: str = ""
    evolution_text: str = ""


def compile_critique_context(
    cycle: Cycle,
    scoring_result: L1ScoringResult,
    schema: PipelineSchema | None,
) -> CritiqueContext:
    candidate_keys = candidate_keys_from_schema(schema)
    prompt_chars = len(cycle.opt_sp.render())
    rank_text, nm_queries = _compute_rank_analysis(scoring_result.winner_results, candidate_keys)
    evolution_text, anomalies = _compute_round_evolution(cycle)
    return CritiqueContext(
        prompt_chars=prompt_chars,
        candidate_keys=candidate_keys,
        nm_queries=nm_queries,
        anomalies=anomalies,
        rank_text=rank_text,
        evolution_text=evolution_text,
    )


def _compute_rank_analysis(
    results: list[QueryResult], candidate_keys: list[str] | None
) -> tuple[str, set[str]]:
    """Return (section_text, near_miss_query_set)."""
    keys = candidate_keys or None
    rank_map: dict[int, int | None] = {
        i: find_rank(get_candidates(r, keys), r.get("ground_truth", ""))
        for i, r in enumerate(results)
        if not is_error_result(r)
    }
    rank_buckets = {"1": 0, "2-5": 0, "6-10": 0, "11-20": 0, "not_found": 0}
    near_misses: list[dict] = []
    for i, r in enumerate(results):
        if is_error_result(r):
            continue
        rank = rank_map.get(i)
        if rank == 1:
            rank_buckets["1"] += 1
        elif rank is not None and rank <= 10:
            rank_buckets["2-5" if rank <= 5 else "6-10"] += 1
            near_misses.append(
                {
                    "query": r["query"][:80],
                    "ground_truth": r.get("ground_truth", "")[:60],
                    "rank": rank,
                    "predicted": r.get("predicted", "?")[:60],
                }
            )
        elif rank is not None and rank <= 20:
            rank_buckets["11-20"] += 1
        else:
            rank_buckets["not_found"] += 1
    nm_queries = {nm["query"] for nm in near_misses}

    n_valid = sum(1 for r in results if not is_error_result(r))
    if not n_valid:
        return "", nm_queries
    lines = [
        "## CANDIDATE RANK ANALYSIS",
        "Where does ground truth appear in candidate list?",
    ]
    for bucket, count in rank_buckets.items():
        lines.append(f"  Rank {bucket}: {count}")
    for k in (1, 3, 5, 10):
        in_top_k = sum(1 for rank in rank_map.values() if rank is not None and rank <= k)
        lines.append(f"  top-{k}: {in_top_k / n_valid:.0%}")
    if near_misses:
        lines.append(f"\nNear misses ({len(near_misses)} — GT in candidates but not rank 1):")
        for nm in near_misses[:15]:
            lines.append(
                f"  [{nm['rank']}] {nm['query']} → predicted: {nm['predicted']} "
                f"(GT: {nm['ground_truth']})"
            )
    return "\n".join(lines), nm_queries


def _compute_round_evolution(cycle: Cycle) -> tuple[str, list[str]]:
    """Return (section_text, anomalies). Plateau detection emits a [MEDIUM] flag."""
    anomalies: list[str] = []
    rounds = cycle.rounds
    if not rounds:
        return "", anomalies
    lines = [
        "## ROUND EVOLUTION",
        "Round  Accuracy  Delta   Degraded  Candidates",
    ]
    prev_acc: float | None = None
    plateau_count = 0
    for r in rounds:
        acc = r.accuracy
        delta = acc - prev_acc if prev_acc is not None else 0.0
        lines.append(
            f"  {r.round:>5}  {acc:>7.1%}  {delta:>+6.1%}  "
            f"{getattr(r, 'degraded_queries', 0):>8}  {len(r.candidate_scores):>10}"
        )
        plateau_count = plateau_count + 1 if abs(delta) < 0.01 else 0
        prev_acc = acc
    for i in range(1, len(rounds)):
        prev_pp = rounds[i - 1].pipeline_params or {}
        curr_pp = rounds[i].pipeline_params or {}
        changed = {
            k
            for k in set(prev_pp) | set(curr_pp)
            if prev_pp.get(k) != curr_pp.get(k) and k != "steps"
        }
        if changed:
            lines.append(
                f"  Round {rounds[i - 1].round}→{rounds[i].round}: {', '.join(sorted(changed))}"
            )
    if plateau_count >= 2:
        anomalies.append(
            f"[MEDIUM] plateau_signal: {plateau_count} consecutive rounds with <1% improvement."
        )
    return "\n".join(lines), anomalies


# ---------------------------------------------------------------------------
# Per-call payload — single declarative bundle of every input a section reads.
# ---------------------------------------------------------------------------


@dataclass
class LayerContext:
    """Per-call payload passed to every section renderer."""

    cycle: Cycle
    layer: Layer
    round_num: int = 0
    pipeline_schema_text: str = ""
    pipeline_schema: PipelineSchema | None = None
    pipeline_params: dict | None = None
    candidate_scores: list[dict] | None = None
    escalation_check_result: dict | None = None
    scoring_result: L1ScoringResult | None = None
    axis_digest: dict[str, str] | None = None
    critique: CritiqueContext | None = None


def _layer_axis_digest(layer: Layer, cycle: Cycle) -> dict[str, str] | None:
    """Pre-fetch the layer-appropriate axis digest from ``cycle.axes``."""
    if cycle.axes is None:
        return None
    if layer is Layer.L1_GENERATE:
        return cycle.axes.digest_for_l1_generate()
    if layer is Layer.L1_CRITIQUE:
        return cycle.axes.digest_for_l1_critique()
    if layer is Layer.L2:
        return cycle.axes.digest_for_l2()
    if layer is Layer.L3:
        return cycle.axes.digest_for_l3()
    return None


def compile_layer_context(
    layer: Layer,
    cycle: Cycle,
    *,
    round_num: int = 0,
    pipeline_schema_text: str = "",
    pipeline_schema: PipelineSchema | None = None,
    pipeline_params: dict | None = None,
    candidate_scores: list[dict] | None = None,
    escalation_check_result: dict | None = None,
    scoring_result: L1ScoringResult | None = None,
) -> LayerContext:
    """Build the per-call :class:`LayerContext` for *layer* over *cycle*."""
    critique: CritiqueContext | None = None
    if layer is Layer.L1_CRITIQUE and scoring_result is not None:
        critique = compile_critique_context(cycle, scoring_result, pipeline_schema)
    return LayerContext(
        cycle=cycle,
        layer=layer,
        round_num=round_num,
        pipeline_schema_text=pipeline_schema_text,
        pipeline_schema=pipeline_schema,
        pipeline_params=pipeline_params,
        candidate_scores=candidate_scores,
        escalation_check_result=escalation_check_result,
        scoring_result=scoring_result,
        axis_digest=_layer_axis_digest(layer, cycle),
        critique=critique,
    )


# ---------------------------------------------------------------------------
# Section renderers — uniform ``(ctx: LayerContext) -> str`` signature.
# ---------------------------------------------------------------------------


def _section_pipeline_schema_text(ctx: LayerContext) -> str:
    return ctx.pipeline_schema_text or ""


def _section_failure_analysis(ctx: LayerContext) -> str:
    fa = ctx.cycle.opt_sp.failure_analysis
    if not fa or not fa.patterns:
        return ""
    lines = [f"FAILURE ANALYSIS ({fa.total_failures} failures / {fa.total_results} total):"]
    for i, pat in enumerate(fa.patterns[:2], 1):
        lines.append(f"  {i}. {pat.name} — {pat.query_count} queries ({pat.fraction:.0%})")
        if pat.example_queries:
            lines.append(f'     Example: "{pat.example_queries[0][:60]}"')
        sig = {
            k: v for k, v in pat.signals.items() if k not in ("error", "degraded", "total_time_ms")
        }
        if sig:
            sig_str = ", ".join(f"{k}={v}" for k, v in list(sig.items())[:2])
            lines.append(f"     Signals: {sig_str}")
    return "\n".join(lines)


def _section_axes_l1(ctx: LayerContext) -> str:
    return (
        format_axis_digest_block(ctx.axis_digest, _L1_AXIS_LABELS, header="HISTORICAL CONTEXT:")
        if ctx.axis_digest
        else ""
    )


def _section_task_context(ctx: LayerContext) -> str:
    tc = ctx.cycle.opt_sp.task_context
    if not tc:
        return ""
    lines = "\n".join(
        f"  {k}: {val}" for k, val in tc.items() if val and k not in _TASK_CONTEXT_SKIP
    )
    return f"CONTEXT:\n{lines}" if lines else ""


def _section_escalation_probe(ctx: LayerContext) -> str:
    """Probe-round per-query warning block — fires only when probe AND journal present."""
    cycle = ctx.cycle
    if not cycle.probe_next_round:
        return ""
    journal = cycle.opt_sp.escalation_journal
    if not journal:
        return ""
    lines = [
        "PROBE ROUND: queries have recurring pipeline warnings. "
        "Generate candidates that address pipeline robustness."
    ]
    warning_inventory = cycle.opt_sp.warning_inventory or None
    if warning_inventory:
        inv = summarize_warning_inventory(warning_inventory)
        if inv:
            lines.extend(["", inv])
        step_counts: Counter[str] = Counter()
        for entry in warning_inventory.values():
            for wtype in entry.get("warnings", {}):
                step_counts[wtype.split(":", 1)[0]] += 1
        if step_counts:
            dom_step, dom_count = step_counts.most_common(1)[0]
            lines.append(f"\nDominant step: {dom_step} ({dom_count} warnings)")
            tried = [ej for ej in journal if ej.get("problem_step") == dom_step][-3:]
            if tried:
                lines.append(f"Previous attempts at {dom_step}:")
                lines.extend(
                    f"  - {ej.get('degraded_rate', 0):.0%} degraded, {ej.get('warning_types', {})}"
                    for ej in tried
                )
    return "\n".join(lines)


def _section_escalation_alert(ctx: LayerContext) -> str:
    """Non-probe aggregated alert — suppressed by an active l2_directive."""
    cycle = ctx.cycle
    if cycle.probe_next_round:
        return ""
    if cycle.opt_sp.l2_directive:
        return ""
    journal = cycle.opt_sp.escalation_journal
    if not journal:
        return ""
    latest = journal[-1]
    alert = [
        f"PIPELINE ISSUE: {latest.get('degraded_rate', 0):.0%} of queries "
        f"degrade at {latest.get('problem_step', 'unknown')}. "
        "Address pipeline instability."
    ]
    if len(journal) > 1:
        alert.append(f"{len(journal)} prior attempts unresolved.")
    if latest.get("warning_types"):
        alert.append(f"Warnings: {latest['warning_types']}")
    return "\n".join(alert)


def _section_l2_directive(ctx: LayerContext) -> str:
    v = ctx.cycle.opt_sp.l2_directive
    if not v:
        return ""
    label = "DIRECTIVE:" if ctx.layer is Layer.L1_GENERATE else "PREVIOUS DIRECTIVE:"
    return f"{label}\n{v}"


def _section_l1_critique_text(ctx: LayerContext) -> str:
    v = ctx.cycle.opt_sp.l1_critique_text
    return f"CRITIQUE:\n{v}" if v else ""


def _section_plan(ctx: LayerContext) -> str:
    v = ctx.cycle.opt_sp.plan
    return f"PLAN:\n{v}" if v else ""


def _escalation_report_text(ctx: LayerContext) -> str:
    if not ctx.escalation_check_result:
        return ""
    cycle = ctx.cycle
    schema = cycle.session.pipeline_schema
    text = format_escalation_report(
        ctx.escalation_check_result,
        cycle.opt_sp.escalation_journal or None,
        ctx.pipeline_params,
        pipeline_schema=schema,
    )
    return text or ""


def _section_escalation_section(ctx: LayerContext) -> str:
    return _escalation_report_text(ctx)


def _section_warning_inventory(ctx: LayerContext) -> str:
    """L2 fallback: per-query warning inventory when no escalation section."""
    if _escalation_report_text(ctx):
        return ""
    inventory = ctx.cycle.opt_sp.warning_inventory
    if not inventory:
        return ""
    text = summarize_warning_inventory(inventory)
    return (text + "\n") if text else ""


def _section_validation_failures(ctx: LayerContext) -> str:
    vfs: list[dict] = []
    for cs in ctx.candidate_scores or []:
        vfs.extend(cs["validation_failures"])
    if not vfs:
        return ""
    lines = ["L1 VALIDATION FAILURES (prior round produced structurally invalid candidates):"]
    for vf in vfs:
        allowed = vf.get("allowed") or []
        allowed_str = ", ".join(allowed[:5]) + (
            f" (+{len(allowed) - 5} more)" if len(allowed) > 5 else ""
        )
        lines.append(
            f"  ⚠ axis={vf.get('axis')} proposed={vf.get('value')!r} reason={vf.get('reason')}"
        )
        lines.append(f"    allowed: [{allowed_str}]")
    lines.append(
        "  ↳ Required L2 action: produce a directive that names the disallowed "
        "value(s) explicitly and instructs L1 to choose only from the allowed "
        'set. Example: "For llm_only.model, use ONLY one of: <list>. Do NOT '
        'propose any other value such as gpt-4o." Self-healing depends on the '
        "directive being explicit."
    )
    return "\n".join(lines)


def _section_runtime_failures(ctx: LayerContext) -> str:
    rfs = [rf.to_dict() for rf in ctx.cycle.opt_sp.runtime_failures]
    if not rfs:
        return ""
    rfs_new = [rf for rf in rfs if rf.get("first_seen_round", 0) == ctx.round_num]
    rfs_acc = [rf for rf in rfs if rf.get("first_seen_round", 0) != ctx.round_num]
    lines = [
        "RUNTIME FAILURES — L2 SELF-HEALING EVIDENCE",
        "  (candidates ran but produced high warning rates; L2 must adjust "
        "its own strategy — directive, task_context, optimizer_params — to "
        "steer L1 away from the failing config region)",
    ]
    if rfs_new:
        lines.append("")
        lines.append("NEW (this round):")
        for rf in rfs_new:
            lines.extend(format_runtime_failure_line(rf, rf.get("candidate_label", "")))
    if rfs_acc:
        lines.append("")
        lines.append(
            f"ACCUMULATED (surviving from earlier rounds, {len(rfs_acc)} patterns — "
            "L2's prior strategy adjustments did NOT reduce these):"
        )
        for rf in rfs_acc:
            lines.extend(format_runtime_failure_line(rf, rf.get("candidate_label", "")))
    lines.append("")
    lines.append(
        "  ↳ Required L2 action: this is L2 self-healing, not L1 correction. "
        "Update your OWN outputs — tighten the directive to name the failing "
        "config range, refine task_context with the discovered constraint, or "
        "adjust optimizer_params (creativity, n_variants) to narrow L1's search "
        "around the safe region. Do NOT just parrot 'don't use X' to L1 — "
        'restructure the search. Example directive: "Reasoning models on this '
        "task need max_tokens ≥ 2000 to emit a final answer; propose variants "
        'only within that range." '
        "If ACCUMULATED entries persist across multiple L2 attempts, L3 will "
        "replan the pipeline itself next."
    )
    return "\n".join(lines)


def _section_axes_l2(ctx: LayerContext) -> str:
    return (
        format_axis_digest_block(ctx.axis_digest, _L2_AXIS_LABELS, header="HISTORICAL CONTEXT:")
        if ctx.axis_digest
        else ""
    )


def _section_axes_l3(ctx: LayerContext) -> str:
    return (
        format_axis_digest_block(ctx.axis_digest, _L3_AXIS_LABELS, header="HISTORICAL CONTEXT:")
        if ctx.axis_digest
        else ""
    )


# ---------------------------------------------------------------------------
# L1_CRITIQUE section renderers — pure consumers of ctx.critique.
# ---------------------------------------------------------------------------


def _section_l1c_scoring_summary(ctx: LayerContext) -> str:
    if ctx.scoring_result is None or ctx.critique is None:
        return ""
    sr = ctx.scoring_result
    cr = ctx.critique
    cycle = ctx.cycle
    n_results = len(sr.winner_results)
    lines = [
        "## SCORING SUMMARY",
        f"Accuracy: {sr.winner_accuracy:.1%} | "
        f"Composite: {sr.winner_composite:.4f} | "
        f"Degraded: {sr.degraded_queries}/{n_results}",
        f"Round {ctx.round_num} | L1 stall count: {cycle.escalation.l1_stall_count} | "
        f"Best so far: {cycle.best_accuracy:.1%} (round {cycle.best_round})",
    ]
    if cr.prompt_chars:
        bloat = (
            " — prompt is bloated; favour compression in priority_fix"
            if cr.prompt_chars > _PROMPT_BLOAT_CHARS
            else ""
        )
        lines.append(f"Current prompt size: {cr.prompt_chars} chars{bloat}")
    return "\n".join(lines)


def _section_l1c_anomaly_flags(ctx: LayerContext) -> str:
    if ctx.scoring_result is None or ctx.critique is None:
        return ""
    if not ctx.scoring_result.winner_results or not ctx.critique.anomalies:
        return ""
    return "## ANOMALY FLAGS ({})\n{}".format(
        len(ctx.critique.anomalies),
        "\n".join(f"  {a}" for a in ctx.critique.anomalies),
    )


def _section_l1c_pipeline_health(ctx: LayerContext) -> str:
    if ctx.scoring_result is None:
        return ""
    results = ctx.scoring_result.winner_results
    total = len(results)
    if not total:
        return ""
    web_warning_count = 0
    termination: Counter[str] = Counter()
    error_count = 0
    for r in results:
        pd = r.get("pipeline_data") or {}
        diag = pd.get("diagnostics") or {}
        if diag.get("warnings"):
            web_warning_count += 1
        termination[pd.get("terminated_at", "unknown")] += 1
        if is_error_result(r):
            error_count += 1
    lines = ["## PIPELINE HEALTH"]
    if termination:
        lines.append("Termination distribution:")
        for step, count in termination.most_common():
            lines.append(f"  {step}: {count}/{total}")
    lines.append(f"Step degradation: {web_warning_count / total:.0%} of queries")
    lines.append(f"Error rate: {error_count / total:.0%}")
    return "\n".join(lines)


def _section_l1c_runtime_failures(ctx: LayerContext) -> str:
    if ctx.scoring_result is None:
        return ""
    failures = [
        {
            "candidate_desc": (cs.changes_description or cs.candidate_id)[:60],
            **rf,
        }
        for cs in ctx.scoring_result.candidate_scores
        for rf in cs.runtime_failures
    ]
    if not failures:
        return ""
    by_warning: dict[str, list[dict]] = {}
    for e in failures:
        by_warning.setdefault(str(e.get("dominant_warning", "unknown")), []).append(e)
    lines = ["## RUNTIME FAILURES THIS ROUND (Rail 2 — treat as hard constraints)"]
    for dom in sorted(by_warning):
        lines.append(f"  {dom}:")
        for e in by_warning[dom]:
            rate = float(e.get("degraded_rate", 0.0)) * 100
            dc = e.get("degraded_count", 0)
            tot = e.get("total_scored", 0)
            cfg = e.get("observed_config") or {}
            cfg_bits = ", ".join(f"{k}={cfg[k]}" for k in _RF_CFG_AXES if k in cfg)
            lines.append(
                f"    {e.get('candidate_desc', '?')}: {rate:.0f}% ({dc}/{tot}) @ {cfg_bits}"
            )
    lines.append(
        "  These configurations are broken — do NOT propose the same or similar values next round."
    )
    return "\n".join(lines)


def _section_l1c_rank_analysis(ctx: LayerContext) -> str:
    return ctx.critique.rank_text if ctx.critique else ""


def _section_l1c_round_evolution(ctx: LayerContext) -> str:
    return ctx.critique.evolution_text if ctx.critique else ""


def _section_l1c_query_categories(ctx: LayerContext) -> str:
    if ctx.scoring_result is None:
        return ""
    step_counts: Counter[str] = Counter()
    for r in ctx.scoring_result.winner_results:
        if r.get("hit") or is_error_result(r):
            continue
        pd = r.get("pipeline_data") or {}
        step_counts[pd.get("terminated_at", "unknown")] += 1
    if not step_counts:
        return ""
    lines = ["## QUERY CATEGORIES", "Failures by termination step:"]
    for step, count in step_counts.most_common():
        lines.append(f"  {step}: {count}")
    return "\n".join(lines)


def _section_l1c_failure_details(ctx: LayerContext) -> str:
    if ctx.scoring_result is None or ctx.critique is None:
        return ""
    keys = ctx.critique.candidate_keys or None
    failures = [
        r
        for r in ctx.scoring_result.winner_results
        if not r.get("hit")
        and not is_error_result(r)
        and r.get("query", "") not in ctx.critique.nm_queries
    ]
    if not failures:
        return ""
    lines = [f"## FAILURE DETAILS ({len(failures)} non-near-miss failures)"]
    for r in failures[:8]:
        pd = r.get("pipeline_data") or {}
        gt = r.get("ground_truth", "?")
        rank = find_rank(get_candidates(r, keys), gt)
        rank_str = f"rank {rank}" if rank else "not in candidates"
        diag = pd.get("diagnostics") or {}
        warn = "degraded" if diag.get("warnings") else ""
        diag_str = ""
        if ctx.pipeline_schema:
            sd = extract_sample_diagnostics(r, ctx.pipeline_schema)
            sig_parts = [
                f"{k}={sd[k]}" for k in ("gt_in_source", "gt_in_ranked", "terminated_at") if k in sd
            ]
            if sig_parts:
                diag_str = " | " + ", ".join(sig_parts)
        lines.append(
            f"  MISS  [{pd.get('terminated_at', '?')}]  {r['query'][:70]}\n"
            f"        -> {r.get('predicted', '?')[:70]}\n"
            f"        GT: {gt[:70]}  |  {rank_str}  {warn}{diag_str}"
        )
    return "\n".join(lines)


def _section_l1c_successes(ctx: LayerContext) -> str:
    if ctx.scoring_result is None:
        return ""
    successes = [r for r in ctx.scoring_result.winner_results if r.get("hit")]
    if not successes:
        return ""
    lines = [f"## SUCCESSES ({len(successes)} queries)"]
    for r in successes[:2]:
        pd = r.get("pipeline_data") or {}
        lines.append(
            f"  HIT  [{pd.get('terminated_at', '?')}]  "
            f"{r['query'][:70]} → {r.get('predicted', '?')[:70]}"
        )
    return "\n".join(lines)


def _section_l1c_historical_context(ctx: LayerContext) -> str:
    return (
        format_axis_digest_block(ctx.axis_digest, _L1C_AXIS_LABELS, header="## HISTORICAL CONTEXT")
        if ctx.axis_digest
        else ""
    )


def _section_l1c_this_round(ctx: LayerContext) -> str:
    if ctx.scoring_result is None:
        return ""
    parts: list[str] = []
    sr = ctx.scoring_result
    diff = build_cross_candidate_diff(
        cast(list[dict], sr.winner_results),
        cast("dict[str, list[dict]]", sr.all_candidate_results),
        [cs.to_dict() for cs in sr.candidate_scores],
    )
    trajectory = build_trajectory_report(ctx.cycle.rounds)
    if trajectory and trajectory.classification != "healthy":
        parts.append(f"  [TRAJECTORY] {trajectory.classification}: {trajectory.description}")
    if diff:
        parts.append(f"  MISSED OPPORTUNITIES:\n{diff}")
    return "## THIS ROUND\n" + "\n".join(parts) if parts else ""


def _section_l1c_available_schema_mutations(ctx: LayerContext) -> str:
    if not ctx.pipeline_schema:
        return ""
    cap_lines = [
        f"  {node.name} has mutable output_schema"
        f" (current fields: {', '.join(node.output_schema.fields)})"
        for node in ctx.pipeline_schema.nodes
        if node.output_schema and node.output_schema.fields and "output_schema" in node.param_keys
    ]
    if not cap_lines:
        return ""
    return (
        "## AVAILABLE SCHEMA MUTATIONS\n"
        + "\n".join(cap_lines)
        + "\n  Use output_schema param with +/-/~ mutation tuples"
        " to add/remove/replace fields."
    )


_L1C_SECTIONS: dict[str, Callable[[LayerContext], str]] = {
    "l1c_scoring_summary": _section_l1c_scoring_summary,
    "l1c_anomaly_flags": _section_l1c_anomaly_flags,
    "l1c_pipeline_health": _section_l1c_pipeline_health,
    "l1c_runtime_failures": _section_l1c_runtime_failures,
    "l1c_rank_analysis": _section_l1c_rank_analysis,
    "l1c_round_evolution": _section_l1c_round_evolution,
    "l1c_query_categories": _section_l1c_query_categories,
    "l1c_failure_details": _section_l1c_failure_details,
    "l1c_successes": _section_l1c_successes,
    "l1c_historical_context": _section_l1c_historical_context,
    "l1c_this_round": _section_l1c_this_round,
    "l1c_available_schema_mutations": _section_l1c_available_schema_mutations,
}

_L1C_SECTION_ORDER: tuple[str, ...] = tuple(_L1C_SECTIONS.keys())


# ---------------------------------------------------------------------------
# Layer registry — section name → renderer; per-layer emit sequence.
# ---------------------------------------------------------------------------


_SECTIONS: dict[str, Callable[[LayerContext], str]] = {
    "pipeline_schema_text": _section_pipeline_schema_text,
    "failure_analysis": _section_failure_analysis,
    "axes_l1": _section_axes_l1,
    "task_context": _section_task_context,
    "escalation_probe": _section_escalation_probe,
    "escalation_alert": _section_escalation_alert,
    "l2_directive": _section_l2_directive,
    "l1_critique_text": _section_l1_critique_text,
    "plan": _section_plan,
    "escalation_section": _section_escalation_section,
    "warning_inventory": _section_warning_inventory,
    "validation_failures": _section_validation_failures,
    "runtime_failures": _section_runtime_failures,
    "axes_l2": _section_axes_l2,
    "axes_l3": _section_axes_l3,
    **_L1C_SECTIONS,
}


LAYER_ORDER: dict[Layer, tuple[str, ...]] = {
    Layer.L1_GENERATE: (
        "pipeline_schema_text",
        "failure_analysis",
        "axes_l1",
        "task_context",
        "escalation_probe",
        "escalation_alert",
        "l2_directive",
        "l1_critique_text",
        "plan",
    ),
    Layer.L1_CRITIQUE: _L1C_SECTION_ORDER,
    Layer.L2: (
        "escalation_section",
        "warning_inventory",
        "l1_critique_text",
        "l2_directive",
        "validation_failures",
        "runtime_failures",
        "axes_l2",
    ),
    Layer.L3: ("axes_l3",),
}


def assemble_dispatch_msg(
    layer: Layer,
    cycle: Cycle,
    *,
    round_num: int = 0,
    pipeline_schema_text: str = "",
    candidate_scores: list[dict] | None = None,
    escalation_check_result: dict | None = None,
    pipeline_params: dict | None = None,
    scoring_result: L1ScoringResult | None = None,
    pipeline_schema: PipelineSchema | None = None,
) -> str:
    """Walk the registry for *layer*, render each section, drop empties, join.

    Builds the per-call :class:`LayerContext` once (which pre-fetches the
    layer-appropriate axis digest from ``cycle.axes`` and, on
    L1_CRITIQUE, computes the cross-cutting :class:`CritiqueContext`),
    then hands it to each section in :data:`LAYER_ORDER`.

    On L1_GENERATE the L2 directive supersedes the L1 critique whenever
    both are populated (the directive is L2's digested view of the
    critique — sliding window of 1). Returns ``""`` when no section
    produces content.
    """
    ctx = compile_layer_context(
        layer,
        cycle,
        round_num=round_num,
        pipeline_schema_text=pipeline_schema_text,
        pipeline_schema=pipeline_schema,
        pipeline_params=pipeline_params,
        candidate_scores=candidate_scores,
        escalation_check_result=escalation_check_result,
        scoring_result=scoring_result,
    )
    sections: dict[str, str] = {}
    for name in LAYER_ORDER[layer]:
        if text := _SECTIONS[name](ctx):
            sections[name] = text

    # On L1_GENERATE the L2 directive replaces the critique whenever both are present.
    if layer is Layer.L1_GENERATE and "l2_directive" in sections:
        sections.pop("l1_critique_text", None)

    return "\n\n".join(sections[name] for name in LAYER_ORDER[layer] if name in sections)


async def run_l1_critique(
    cycle: Cycle,
    scoring_result: L1ScoringResult,
    schema: PipelineSchema | None,
    llm_client: LLMClientBase,
    *,
    round_num: int,
    model: str | None = None,
    recorder: RoundRecorder | None = None,
) -> dict:
    """Build critique from pipeline stats + LLM analysis. Returns the raw 6-field LLM dict."""
    dispatch_msg = assemble_dispatch_msg(
        Layer.L1_CRITIQUE,
        cycle,
        round_num=round_num,
        scoring_result=scoring_result,
        pipeline_schema=schema,
    )
    result, prompt = await run_optimizer_node(
        template_name="l1_critique",
        compile_vars={"dispatch_msg": dispatch_msg},
        llm_client=llm_client,
        model=model,
        recorder=recorder,
    )
    logger.info(
        "Rich L1 critique: %d chars prompt, round %d, acc=%.3f",
        len(prompt),
        round_num + 1,
        scoring_result.winner_accuracy,
    )
    return result


def format_l1_critique_for_prompt(critique: dict) -> str:
    """L1 critique dict → compact text for L1/L2 (summary + priority_fix + axes + highlights)."""
    parts = []
    if critique.get("summary"):
        parts.append(critique["summary"])
    if critique.get("priority_fix"):
        parts.append(f"Priority fix: {critique['priority_fix']}")
    if critique.get("suggested_axes"):
        parts.append(f"Suggested axes: {', '.join(critique['suggested_axes'])}")
    highlights = critique.get("failure_highlights", [])
    if highlights:
        parts.append("Key failures:")
        for h in highlights[:5]:
            parts.append(f"  {h}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# L2/L3 transitions — LLM-driven optimizer transitions.
# ---------------------------------------------------------------------------


class TransitionAction(enum.StrEnum):
    """What the feedback cycle should do after an L2/L3 transition."""

    CONTINUE = "continue"
    PROBE = "probe"


@dataclass
class TransitionResult:
    """L2/L3 transition result — new OptSearchPoint plus optional pipeline_params changes."""

    opt_search_point: OptSearchPoint
    pipeline_params: dict | None = None
    task_context: TaskDecomposition | None = None
    l2_directive: str = ""
    action: TransitionAction = TransitionAction.CONTINUE
    debug_prompt: str = ""
    debug_response: dict | None = None


class LayerTransition:
    """Base for an LLM-driven optimizer transition (L2 or L3).

    Subclasses set the four ClassVars (``layer``, ``template_name``,
    ``default_temperature``, ``phase``) and override the five hooks
    consumed by the orchestrator: ``build_compile_vars``,
    ``build_result``, ``apply_side_effects``, ``enter_payload``,
    ``exit_payload``. ``run()`` is the shared LLM-call template.
    """

    layer: ClassVar[Literal["L2", "L3"]]
    template_name: ClassVar[str]
    default_temperature: ClassVar[float]
    phase: ClassVar[CampaignPhase]

    async def run(
        self,
        cycle: Cycle,
        llm_client: LLMClientBase,
        *,
        model: str | None = None,
        temperature: float | None = None,
        pipeline_params: dict | None = None,
        round_num: int = 0,
        escalation_check_result: dict | None = None,
    ) -> TransitionResult:
        compile_vars = self.build_compile_vars(
            cycle,
            pipeline_params=pipeline_params,
            round_num=round_num,
            escalation_check_result=escalation_check_result,
        )
        raw, prompt = await run_optimizer_node(
            template_name=self.template_name,
            compile_vars=compile_vars,
            llm_client=llm_client,
            model=model,
            temperature=self.default_temperature if temperature is None else temperature,
            recorder=cycle.session.state.round_recorder,
        )
        return self.build_result(raw, cycle.opt_sp, prompt, pipeline_params=pipeline_params)

    def build_compile_vars(
        self,
        cycle: Cycle,
        *,
        pipeline_params: dict | None,
        round_num: int,
        escalation_check_result: dict | None,
    ) -> dict:
        raise NotImplementedError

    def build_result(
        self,
        raw: dict,
        opt_sp: OptSearchPoint,
        prompt: str,
        *,
        pipeline_params: dict | None,
    ) -> TransitionResult:
        raise NotImplementedError

    def apply_side_effects(self, cycle: Cycle, result: TransitionResult, round_num: int) -> None:
        raise NotImplementedError

    def enter_payload(self, cycle: Cycle) -> dict[str, Any]:
        raise NotImplementedError

    def exit_payload(self, cycle: Cycle, result: TransitionResult) -> dict[str, Any]:
        raise NotImplementedError


class L2RefineStrategy(LayerTransition):
    """L2: tune ``optimizer_params`` + ``task_context`` + directive (one-round window)."""

    layer: ClassVar[Literal["L2", "L3"]] = "L2"
    template_name: ClassVar[str] = "l2_context"
    default_temperature: ClassVar[float] = 0.3
    phase: ClassVar[CampaignPhase] = CampaignPhase.REFINE_STRATEGY

    def build_compile_vars(
        self,
        cycle: Cycle,
        *,
        pipeline_params: dict | None,
        round_num: int,
        escalation_check_result: dict | None,
    ) -> dict:
        opt_sp = cycle.opt_sp
        task_context_section = ""
        if opt_sp.task_context:
            tc_display = {
                k: v for k, v in opt_sp.task_context.items() if k != "raw_description" and v
            }
            task_context_section = (
                "\n\nTASK CONTEXT (structured domain understanding — refine if inaccurate):\n"
                + json.dumps(tc_display, indent=2)
            )

        candidate_scores = cycle.rounds[-1].candidate_scores if cycle.rounds else []
        dispatch_msg = assemble_dispatch_msg(
            Layer.L2,
            cycle,
            round_num=round_num,
            candidate_scores=candidate_scores,
            escalation_check_result=escalation_check_result,
            pipeline_params=pipeline_params,
        )

        return {
            "current_params": json.dumps(opt_sp.optimizer_params),
            "task_context_section": task_context_section,
            "dispatch_msg": ("\n\n" + dispatch_msg) if dispatch_msg else "",
        }

    def build_result(
        self,
        raw: dict,
        opt_sp: OptSearchPoint,
        prompt: str,
        *,
        pipeline_params: dict | None,
    ) -> TransitionResult:
        changes: dict = {}
        if raw.get("optimizer_params"):
            new_params = {**opt_sp.optimizer_params, **raw["optimizer_params"]}
            changes["optimizer_params"] = new_params
        rationale = raw.get("rationale", "L2 refine_strategy transition")
        changes["changes_description"] = f"L2: {rationale[:80]}"

        new_task_context = None
        if raw.get("task_context") and isinstance(raw["task_context"], dict):
            merged = opt_sp.task_context.merge(raw["task_context"])
            if merged.to_dict() != opt_sp.task_context.to_dict():
                new_task_context = merged

        try:
            action = TransitionAction(raw.get("action", "continue"))
        except ValueError:
            action = TransitionAction.CONTINUE

        l2_directive = raw.get("directive", "")
        if not isinstance(l2_directive, str):
            l2_directive = ""

        logger.debug(
            "L2 refine_strategy: %d param changes, task_context %s, action=%s, directive=%d chars",
            len(raw.get("optimizer_params", {})),
            "updated" if new_task_context else "unchanged",
            action,
            len(l2_directive),
        )

        new_opt_sp = opt_sp.mutate(**changes) if changes else opt_sp
        return TransitionResult(
            opt_search_point=new_opt_sp,
            task_context=new_task_context,
            l2_directive=l2_directive,
            action=action,
            debug_prompt=prompt,
            debug_response=raw,
        )

    def apply_side_effects(self, cycle: Cycle, result: TransitionResult, round_num: int) -> None:
        from promptpotter.application.optimization.cycle import record_decision

        if result.task_context:
            cycle.opt_sp.task_context = result.task_context
        cycle.opt_sp.l2_directive = result.l2_directive
        cycle.escalation.l2.record_entry(cycle.best_accuracy, cycle.best_composite)

        is_probe = result.action == TransitionAction.PROBE
        record_decision(
            cycle.pending_decisions,
            "probe_round_commitment",
            {
                "round_num": round_num,
                "l2_round": cycle.escalation.l2.round,
            },
            is_probe,
            data={
                "action": str(result.action),
                "l2_directive_preview": (result.l2_directive or "")[:200],
                "changes_description": result.opt_search_point.lineage.changes_description or "",
            },
        )
        if is_probe:
            cycle.probe_next_round = True
            logger.debug("L2 requested probe — next round uses warned queries")
        logger.debug(
            "L2 refine_strategy at round %d (l2_round=%d)", round_num, cycle.escalation.l2.round
        )

    def enter_payload(self, cycle: Cycle) -> dict[str, Any]:
        return {
            "l2_round": cycle.escalation.l2.round,
            "l1_stall_count": cycle.escalation.l1_stall_count,
            "current_params": cycle.opt_sp.optimizer_params,
            "current_accuracy": cycle.current_accuracy,
            "best_accuracy": cycle.best_accuracy,
        }

    def exit_payload(self, cycle: Cycle, result: TransitionResult) -> dict[str, Any]:
        warned_count, top_warning = warning_summary(cycle.opt_sp.warning_inventory)
        return {
            "l2_round": cycle.escalation.l2.round,
            "param_changes_count": len(result.opt_search_point.optimizer_params),
            "task_context_changed": result.task_context is not None,
            "changes_description": result.opt_search_point.lineage.changes_description or "",
            "pipeline_params_changed": result.pipeline_params is not None,
            "pipeline_params": result.pipeline_params,
            "action": result.action,
            "warned_queries": warned_count,
            "top_warning": top_warning,
            "l2_prompt": result.debug_prompt,
            "l2_response": result.debug_response,
        }


class L3ModifyPlan(LayerTransition):
    """L3: propose a new strategic plan + optional pipeline_params deltas."""

    layer: ClassVar[Literal["L2", "L3"]] = "L3"
    template_name: ClassVar[str] = "l3_plan"
    default_temperature: ClassVar[float] = 0.5
    phase: ClassVar[CampaignPhase] = CampaignPhase.MODIFY_PLAN

    def build_compile_vars(
        self,
        cycle: Cycle,
        *,
        pipeline_params: dict | None,
        round_num: int,
        escalation_check_result: dict | None,
    ) -> dict:
        opt_sp = cycle.opt_sp
        pipeline_schema = cycle.session.pipeline_schema

        # L3's "l2_history" — synthetic single-entry summary of the most recent
        # L2 round, sourced directly from cycle state.
        l2_history = [
            {
                "l2_round": cycle.escalation.l2.round,
                "optimizer_params": opt_sp.optimizer_params,
                "accuracy_change": cycle.best_composite
                - cycle.escalation.l3.best_composite_at_entry,
            }
        ]
        l2_summary = "\n".join(
            f"  L2 round {rd.get('l2_round', '?')}: "
            f"params={rd.get('parameters', {})}, "
            f"acc_change={rd.get('accuracy_change', 0):+.1%}"
            for rd in l2_history[-3:]
        )

        # Runtime failure trail — patterns L2 couldn't reduce (empty string collapses template).
        runtime_failures_section = format_runtime_failures_for_l3(
            [rf.to_dict() for rf in opt_sp.runtime_failures]
        )

        return {
            "current_plan": opt_sp.plan or "(none — default strategy)",
            "l2_summary": l2_summary,
            "rendered_prompt": opt_sp.render(),
            "pipeline_section": format_pipeline_section(pipeline_params, pipeline_schema),
            "runtime_failures_section": (
                "\n\n" + runtime_failures_section if runtime_failures_section else ""
            ),
            "dispatch_msg": assemble_dispatch_msg(Layer.L3, cycle),
        }

    def build_result(
        self,
        raw: dict,
        opt_sp: OptSearchPoint,
        prompt: str,
        *,
        pipeline_params: dict | None,
    ) -> TransitionResult:
        new_plan = raw.get("plan", opt_sp.plan)
        rationale = raw.get("rationale", "L3 modify_plan transition")

        pp_changes = raw.get("pipeline_params")
        new_pipeline_params: dict | None = None
        if isinstance(pp_changes, dict) and pp_changes:
            merged = dict(pipeline_params or {})
            for key, value in pp_changes.items():
                if isinstance(value, dict) and isinstance(merged.get(key), dict):
                    merged[key] = {**merged[key], **value}
                else:
                    merged[key] = value
            new_pipeline_params = merged

        logger.debug(
            "L3 modify_plan: %s, pipeline_params %s",
            rationale[:100],
            "updated" if new_pipeline_params else "unchanged",
        )

        new_opt_sp = opt_sp.mutate(
            plan=new_plan,
            changes_description=f"L3: {rationale[:80]}",
        )
        return TransitionResult(
            opt_search_point=new_opt_sp,
            pipeline_params=new_pipeline_params,
            debug_prompt=prompt,
            debug_response=raw,
        )

    def apply_side_effects(self, cycle: Cycle, result: TransitionResult, round_num: int) -> None:
        cycle.escalation.l3.record_entry(cycle.best_accuracy, cycle.best_composite)
        cycle.escalation.reset_for_l3(cycle.best_accuracy, cycle.best_composite)
        logger.debug(
            "L3 modify_plan at round %d (l3_round=%d)", round_num, cycle.escalation.l3.round
        )

    def enter_payload(self, cycle: Cycle) -> dict[str, Any]:
        return {
            "l3_round": cycle.escalation.l3.round,
            "l2_stall_count": cycle.escalation.l2.stall_count,
            "current_plan_preview": str(cycle.opt_sp.plan)[:120],
        }

    def exit_payload(self, cycle: Cycle, result: TransitionResult) -> dict[str, Any]:
        return {
            "l3_round": cycle.escalation.l3.round,
            "new_plan_preview": str(result.opt_search_point.plan)[:120],
            "changes_description": result.opt_search_point.lineage.changes_description or "",
            "pipeline_params_changed": result.pipeline_params is not None,
        }

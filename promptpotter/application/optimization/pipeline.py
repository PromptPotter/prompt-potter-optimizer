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
   ``L1GenerateField``, ``L1GenerateSurface``, ``L2Surface``,
   ``compile_l1_surface``, ``compile_l2_surface``,
   ``compile_l1_critique_blob``, ``run_l1_critique``,
   ``format_l1_critique_for_prompt``, ``CritiqueContext``,
   ``OptimizerAction``, ``LayerTransition``, ``L2RefineStrategy``,
   ``L3ModifyPlan``) — the flow: archive → AxisIndex (cached) →
   LayerContext (per-call payload) → sections (pure formatters) →
   surface (OSP overrides applied) → LLM. L1-generate's surface is
   typed (``L1GenerateField`` registry + ``L1GenerateSurface`` payload)
   and owned by L2 via OSP override fields (section visibility, section
   text, whole-template body); L1-critique stays on the legacy blob path
   because nothing external mutates it. L2 fires on stall and writes any
   subset of these fields onto the next OSP.
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
from promptpotter.application.optimization.l2_validators import run_l2_output_validators
from promptpotter.application.scoring.metrics import extract_sample_diagnostics, find_rank
from promptpotter.config.settings import PROMPT_STRING_FIELDS
from promptpotter.domain.opt_search_point import OptSearchPoint, PromptTemplate
from promptpotter.domain.phases import CampaignPhase
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.domain.validators import ValidatorOutcome
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
    "L1_GENERATE_SECTION_FIELDS",
    "CritiqueContext",
    "L1GenerateField",
    "L1GenerateSurface",
    "L2RefineStrategy",
    "L2Surface",
    "L3ModifyPlan",
    "Layer",
    "LayerContext",
    "LayerTransition",
    "OptimizerAction",
    "TrajectoryReport",
    "TransitionResult",
    "build_cross_candidate_diff",
    "build_trajectory_report",
    "candidate_summaries",
    "compile_critique_context",
    "compile_l1_critique_blob",
    "compile_l1_surface",
    "compile_l2_surface",
    "compile_layer_context",
    "compute_optimizer_prompt_hashes",
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
    template: PromptTemplate | None = None,
) -> tuple[Any, str]:
    """Load prompt template, compile, call LLM, parse JSON → (parsed_result, prompt_text).

    When *template* is provided, it overrides the load-from-name path (used
    by L1's ``l1_template_override`` channel — L2 can rewrite L1's prompt
    body by writing ``template_override`` on its OSP). The trace metadata
    still records ``template_name`` so observability stays continuous.
    """
    if template is None:
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


def compute_optimizer_prompt_hashes() -> dict[str, str]:
    """SHA-256 (16-char prefix) of each optimizer prompt's loaded content.

    Hashes the deterministic ``model_dump_json()`` of the loaded
    ``PromptTemplate`` (so the hash reflects what was actually used,
    Langfuse-overridden or local). Persisted to ``index.json::final.prompt_hashes``
    so M10's cross-cycle leaderboard can join cycles by ``l1_generate_hash`` etc.
    """
    out: dict[str, str] = {}
    for name in list_optimizer_prompts():
        tpl = load_optimizer_prompt(name)
        out[name] = hashlib.sha256(tpl.model_dump_json().encode("utf-8")).hexdigest()[:16]
    return out


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
        "  ↳ Treat these as discovered constraints on the search space. "
        "Replan either by changing pipeline_params (switch model, raise a "
        "param floor, swap a node) or by reshaping the plan text. Healing "
        "is gradual — if the constraints persist after this replan, the "
        "loop fires again and you'll have a tighter trail to work from."
    )
    return "\n".join(lines)


def format_l2_output_failures_for_l3(outcomes: list[ValidatorOutcome] | None) -> str:
    """Render L2-output validator outcomes for L3 modify_plan (heal L2)."""
    if not outcomes:
        return ""
    lines = [
        "L2 OUTPUT FAILURES — L2 STRATEGY IS BROKEN",
        "  (deterministic validators caught problems in L2's own output; L3 is L2's nurse-LLM)",
        "",
    ]
    for outcome in outcomes:
        lines.append(f"  • {outcome.validator_id} (score={outcome.score:.2f})")
        evidence = outcome.evidence or {}
        if outcome.validator_id == "l2_cross_field_duplication":
            for d in evidence.get("duplicates", [])[:3]:
                fields = ", ".join(d.get("fields", []))
                first_line = (d.get("block", "") or "").splitlines()[0][:80]
                lines.append(f"      duplicated across [{fields}]: {first_line!r}")
        elif outcome.validator_id == "l2_verbatim_self_repeat":
            preview = (evidence.get("directive", "") or "")[:120]
            lines.append(f"      directive == previous round's directive: {preview!r}")
        elif outcome.validator_id == "l2_catalogue_redundancy":
            for r in evidence.get("redundant_overrides", [])[:3]:
                section = r.get("section", "")
                preview = (r.get("value", "") or "")[:80]
                lines.append(f"      no-op text_overrides[{section!r}]: {preview!r}")
    lines.append("")
    lines.append(
        "  ↳ Healing is gradual. Refine the plan text to nudge L2 toward "
        "novel mutations; you don't need to perfectly diagnose the broken "
        "strategy mode in one pass. If the same validator keeps firing, "
        "the loop retriggers — at which point a structural change "
        "(pipeline composition, task framing) is the right move."
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
        "  ↳ Healing is gradual. Write a directive that shifts L1 toward the "
        "allowed region — pointing at the allowed set is usually enough; "
        "spelling out every forbidden value is not required. If L1 still "
        "proposes invalid values next round, the loop retriggers with the "
        "fresh evidence and you get another pass to refine."
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
        "  ↳ Healing is gradual and self-directed. Update your OWN outputs — "
        "shift the directive, refine task_context, or adjust optimizer_params "
        "to steer L1's search toward a safer region. ACCUMULATED items are "
        "the signal that your last angle didn't take; try a different one. "
        "Don't expect a one-shot fix — if the pattern persists across L2 "
        "attempts, L3 will replan the pipeline itself."
    )
    return "\n".join(lines)


def _section_axes_l2(ctx: LayerContext) -> str:
    return (
        format_axis_digest_block(ctx.axis_digest, _L2_AXIS_LABELS, header="HISTORICAL CONTEXT:")
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
    lines = ["## RUNTIME FAILURES THIS ROUND (steer away from these regions)"]
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
        "  These configurations degraded — shift away from the same or similar regions."
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


def _section_l1c_round_report(ctx: LayerContext) -> str:
    """Round-level stats: scoring, anomalies, pipeline health, ranks, evolution, this-round diff."""
    parts = (
        _section_l1c_scoring_summary(ctx),
        _section_l1c_anomaly_flags(ctx),
        _section_l1c_pipeline_health(ctx),
        _section_l1c_rank_analysis(ctx),
        _section_l1c_round_evolution(ctx),
        _section_l1c_this_round(ctx),
    )
    return "\n\n".join(p for p in parts if p)


def _section_l1c_per_query_report(ctx: LayerContext) -> str:
    """Per-query views: runtime failures, query categories, failure details, successes."""
    parts = (
        _section_l1c_runtime_failures(ctx),
        _section_l1c_query_categories(ctx),
        _section_l1c_failure_details(ctx),
        _section_l1c_successes(ctx),
    )
    return "\n\n".join(p for p in parts if p)


_L1C_SECTIONS: dict[str, Callable[[LayerContext], str]] = {
    "l1c_round_report": _section_l1c_round_report,
    "l1c_per_query_report": _section_l1c_per_query_report,
    "l1c_historical_context": _section_l1c_historical_context,
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
    "plan": _section_plan,
    "escalation_section": _section_escalation_section,
    "warning_inventory": _section_warning_inventory,
    "validation_failures": _section_validation_failures,
    "runtime_failures": _section_runtime_failures,
    "axes_l2": _section_axes_l2,
    **_L1C_SECTIONS,
}


# L1-critique stays on the legacy blob path because nothing external
# mutates it; L1-generate and L2 moved to typed ``L1GenerateSurface`` /
# ``L2Surface`` payloads compiled below.
_L1_CRITIQUE_ORDER: tuple[str, ...] = _L1C_SECTION_ORDER


_ENUM_RENDER_CAP = 12


def _render_schema_text(pipeline_schema: PipelineSchema) -> str:
    """Pipeline schema for L1 context. Enums capped at ``_ENUM_RENDER_CAP``."""
    lines: list[str] = []
    npk = pipeline_schema.node_param_keys()
    if not npk:
        return ""

    lines.append(
        "VALID PIPELINE NODES AND PARAMETERS (only use these — do not invent nodes or params):"
    )
    for node_name, params in npk.items():
        node = pipeline_schema.get_node(node_name)
        descs = node.param_descriptions if node else {}
        enums = node.param_allowed_values if node else {}
        if not params:
            lines.append(f"  {node_name}: (no tunable params)")
            continue
        param_parts: list[str] = []
        for p in sorted(params):
            desc = descs.get(p)
            allowed = enums.get(p)
            suffix_bits: list[str] = []
            if desc:
                suffix_bits.append(desc)
            if allowed:
                shown = list(allowed)[:_ENUM_RENDER_CAP]
                extra = len(allowed) - len(shown)
                suffix = "one of: " + ", ".join(shown)
                if extra > 0:
                    suffix += f" (+{extra} more)"
                suffix_bits.append(suffix)
            param_parts.append(f"{p} ({'; '.join(suffix_bits)})" if suffix_bits else p)
        lines.append(f"  {node_name}: {', '.join(param_parts)}")

    mutable = [n for n in pipeline_schema.nodes if n.output_schema and n.output_schema.fields]
    if mutable:
        lines.append("")
        lines.append(
            "OUTPUT SCHEMA MUTATIONS — use as output_schema param: "
            '["+", name, type, is_array, desc] / ["-", name] / '
            '["~", old, new, type, is_array, desc]'
        )
        for node in mutable:
            assert node.output_schema is not None
            lines.append(f"  {node.name}: {', '.join(node.output_schema.fields)}")

    text = "\n".join(lines)
    if pipeline_schema.available_models:
        text += "\n\nAVAILABLE MODELS (only use these for model overrides):\n"
        text += "\n".join(f"  {m}" for m in pipeline_schema.available_models)
    return text


# ---------------------------------------------------------------------------
# L1-generate surface — closed registry + typed payload owned by L2 via OSP.
# ---------------------------------------------------------------------------


class L1GenerateField(enum.StrEnum):
    """Closed registry of every variable injectable into the L1-generate prompt.

    Section entries (the first 8) are what L2 can toggle off or replace via
    ``OptSearchPoint.l1_section_overrides`` / ``l1_section_overrides_text``
    when it fires. Scalar entries (the last 4) are factual state — always
    rendered, never overridable. Adding a capability = enum entry + render
    function + ``compile_l1_surface`` wiring; removing a capability = enum
    entry deletion (deliberate code change).
    """

    # -- Sections (visibility/text-overridable by L2 when it fires) --
    PIPELINE_SCHEMA_TEXT = "pipeline_schema_text"
    FAILURE_ANALYSIS = "failure_analysis"
    AXES_L1 = "axes_l1"
    TASK_CONTEXT = "task_context"
    ESCALATION_PROBE = "escalation_probe"
    ESCALATION_ALERT = "escalation_alert"
    L2_DIRECTIVE = "l2_directive"
    PLAN = "plan"
    # -- Scalars (factual, always rendered, not overridable) --
    N_VARIANTS = "n_variants"
    ACCURACY_PCT = "accuracy_pct"
    N_QUERIES = "n_queries"
    RENDERED_PROMPT = "rendered_prompt"


L1_GENERATE_SECTION_FIELDS: tuple[L1GenerateField, ...] = (
    L1GenerateField.PIPELINE_SCHEMA_TEXT,
    L1GenerateField.FAILURE_ANALYSIS,
    L1GenerateField.AXES_L1,
    L1GenerateField.TASK_CONTEXT,
    L1GenerateField.ESCALATION_PROBE,
    L1GenerateField.ESCALATION_ALERT,
    L1GenerateField.L2_DIRECTIVE,
    L1GenerateField.PLAN,
)

_L1_GENERATE_SECTION_RENDERERS: dict[L1GenerateField, Callable[[LayerContext], str]] = {
    L1GenerateField.PIPELINE_SCHEMA_TEXT: _section_pipeline_schema_text,
    L1GenerateField.FAILURE_ANALYSIS: _section_failure_analysis,
    L1GenerateField.AXES_L1: _section_axes_l1,
    L1GenerateField.TASK_CONTEXT: _section_task_context,
    L1GenerateField.ESCALATION_PROBE: _section_escalation_probe,
    L1GenerateField.ESCALATION_ALERT: _section_escalation_alert,
    L1GenerateField.L2_DIRECTIVE: _section_l2_directive,
    L1GenerateField.PLAN: _section_plan,
}

_L1_GENERATE_FIELD_DESCRIPTIONS: dict[L1GenerateField, str] = {
    L1GenerateField.PIPELINE_SCHEMA_TEXT: "Target pipeline + active steps + per-node schema.",
    L1GenerateField.FAILURE_ANALYSIS: "Latest round's clustered failure patterns.",
    L1GenerateField.AXES_L1: "AxisIndex digest for L1-generate (failure clusters, top axes).",
    L1GenerateField.TASK_CONTEXT: "Structured domain context — domain, goals, challenges.",
    L1GenerateField.ESCALATION_PROBE: "Probe-round per-query warning block (probe rounds only).",
    L1GenerateField.ESCALATION_ALERT: "Aggregated pipeline-issue alert (non-probe).",
    L1GenerateField.L2_DIRECTIVE: "L2's strategic directive for this round.",
    L1GenerateField.PLAN: "L3's strategic framework.",
    L1GenerateField.N_VARIANTS: "[scalar] How many candidates L1 must produce.",
    L1GenerateField.ACCURACY_PCT: "[scalar] Current accuracy of the parent SearchPoint.",
    L1GenerateField.N_QUERIES: "[scalar] Number of queries the parent was scored on.",
    L1GenerateField.RENDERED_PROMPT: "[scalar] The current prompt being optimized.",
}


@dataclass(frozen=True)
class L1GenerateSurface:
    """Typed payload of every variable injected into the L1-generate prompt.

    Section fields (first 8) carry their own trailing ``\\n\\n`` separator
    when non-empty so the template can concatenate them inertly. Scalar
    fields (last 4) carry plain values. Built by :func:`compile_l1_surface`
    after applying L2's per-section overrides from ``OptSearchPoint``.
    """

    pipeline_schema_text: str = ""
    failure_analysis: str = ""
    axes_l1: str = ""
    task_context: str = ""
    escalation_probe: str = ""
    escalation_alert: str = ""
    l2_directive: str = ""
    plan: str = ""
    # Scalars — factual, always rendered, not overridable.
    n_variants: str = ""
    accuracy_pct: str = ""
    n_queries: str = ""
    rendered_prompt: str = ""

    def to_compile_vars(self) -> dict[str, str]:
        """Map every registry member to its prompt-hole value."""
        return {f.value: getattr(self, f.value) for f in L1GenerateField}


def compile_l1_surface(
    cycle: Cycle,
    *,
    round_num: int = 0,
    n_variants: int,
) -> L1GenerateSurface:
    """Walk :class:`L1GenerateField`, render sections, apply OSP overrides.

    Override application order per section:
    1. ``cycle.opt_sp.l1_section_overrides[name] is False`` → empty string.
    2. ``cycle.opt_sp.l1_section_overrides_text[name]`` set → use that text.
    3. Otherwise → call the registered section renderer.

    Non-empty section outputs gain a trailing ``\\n\\n`` so the template
    body stays inert when sections gate off. The whole-template override
    on ``cycle.opt_sp.l1_template_override`` is applied one layer up
    (in ``l1.l1_generate``) by swapping the loaded template's
    ``problem_description`` body before compile.
    """
    schema = cycle.session.pipeline_schema
    schema_text = _render_schema_text(schema) if schema else ""
    ctx = compile_layer_context(
        Layer.L1_GENERATE,
        cycle,
        round_num=round_num,
        pipeline_schema_text=schema_text,
        pipeline_schema=schema,
    )

    overrides_visible = cycle.opt_sp.l1_section_overrides
    overrides_text = cycle.opt_sp.l1_section_overrides_text

    rendered: dict[str, str] = {}
    for f in L1_GENERATE_SECTION_FIELDS:
        name = f.value
        if overrides_visible.get(name) is False:
            text = ""
        elif name in overrides_text:
            text = overrides_text[name]
        else:
            text = _L1_GENERATE_SECTION_RENDERERS[f](ctx)
        rendered[name] = (text + "\n\n") if text else ""

    return L1GenerateSurface(
        pipeline_schema_text=rendered[L1GenerateField.PIPELINE_SCHEMA_TEXT.value],
        failure_analysis=rendered[L1GenerateField.FAILURE_ANALYSIS.value],
        axes_l1=rendered[L1GenerateField.AXES_L1.value],
        task_context=rendered[L1GenerateField.TASK_CONTEXT.value],
        escalation_probe=rendered[L1GenerateField.ESCALATION_PROBE.value],
        escalation_alert=rendered[L1GenerateField.ESCALATION_ALERT.value],
        l2_directive=rendered[L1GenerateField.L2_DIRECTIVE.value],
        plan=rendered[L1GenerateField.PLAN.value],
        n_variants=str(n_variants),
        accuracy_pct=f"{cycle.current_accuracy:.1%}",
        n_queries=str(len(cycle.current_results)),
        rendered_prompt=cycle.opt_sp.render(),
    )


# ---------------------------------------------------------------------------
# L2 surface — typed payload + L1-generate field catalogue for L2's prompt.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class L2Surface:
    """Typed payload of every variable injected into the L2-context prompt.

    Built by :func:`compile_l2_surface` from cycle state + the L1-generate
    surface (so L2 always sees the catalogue of what L1 is currently
    receiving). All section strings carry their own trailing ``\\n\\n``
    when non-empty. ``l1_generate_field_catalogue`` is the menu of L1's
    surface — built from the closed registry so L2 can never lose track
    of a capability.
    """

    current_params: str = ""
    task_context_section: str = ""
    escalation_section: str = ""
    warning_inventory: str = ""
    l2_directive: str = ""
    validation_failures: str = ""
    runtime_failures: str = ""
    axes_l2: str = ""
    l1_generate_field_catalogue: str = ""

    def to_compile_vars(self) -> dict[str, str]:
        """Map every L2 prompt hole to its rendered value."""
        return {
            "current_params": self.current_params,
            "task_context_section": self.task_context_section,
            "escalation_section": self.escalation_section,
            "warning_inventory": self.warning_inventory,
            "l2_directive": self.l2_directive,
            "validation_failures": self.validation_failures,
            "runtime_failures": self.runtime_failures,
            "axes_l2": self.axes_l2,
            "l1_generate_field_catalogue": self.l1_generate_field_catalogue,
        }


_L2_SECTION_RENDERERS: tuple[tuple[str, Callable[[LayerContext], str]], ...] = (
    ("escalation_section", _section_escalation_section),
    ("warning_inventory", _section_warning_inventory),
    ("l2_directive", _section_l2_directive),
    ("validation_failures", _section_validation_failures),
    ("runtime_failures", _section_runtime_failures),
    ("axes_l2", _section_axes_l2),
)


def _format_l1_generate_field_catalogue(
    l1_surface: L1GenerateSurface,
    overrides_visible: dict[str, bool],
    overrides_text: dict[str, str],
) -> str:
    """Render the L1-generate field catalogue for L2's prompt.

    One line per registry entry: ``[ON]``/``[OFF]`` + name + description
    (+ override-text preview when set). The catalogue is built from
    :class:`L1GenerateField` so L2 always sees every section that exists
    in code — including ones currently toggled off.
    """
    lines: list[str] = []
    for f in L1GenerateField:
        name = f.value
        is_section = f in L1_GENERATE_SECTION_FIELDS
        if not is_section:
            state = "[scalar]"
        elif overrides_visible.get(name) is False:
            state = "[OFF]"
        else:
            state = "[ON]"
        desc = _L1_GENERATE_FIELD_DESCRIPTIONS[f]
        line = f"  {state} {name} — {desc}"
        if name in overrides_text:
            preview = overrides_text[name].strip().splitlines()[0][:80]
            line += f"\n    override: {preview!r}"
        lines.append(line)
    return "\n".join(lines)


def compile_l2_surface(
    cycle: Cycle,
    *,
    round_num: int = 0,
    candidate_scores: list[dict] | None = None,
    escalation_check_result: dict | None = None,
    pipeline_params: dict | None = None,
) -> L2Surface:
    """Build the per-call L2 surface — analysis sections + L1 catalogue.

    The L1-generate field catalogue is computed here (not in the
    prompt template) so L2's view of L1's surface stays code-derived
    and cannot drift from the registry.
    """
    ctx = compile_layer_context(
        Layer.L2,
        cycle,
        round_num=round_num,
        pipeline_schema=cycle.session.pipeline_schema,
        pipeline_params=pipeline_params,
        candidate_scores=candidate_scores,
        escalation_check_result=escalation_check_result,
    )
    rendered = {name: fn(ctx) for name, fn in _L2_SECTION_RENDERERS}
    rendered = {k: (v + "\n\n") if v else "" for k, v in rendered.items()}

    opt_sp = cycle.opt_sp
    current_params = json.dumps(opt_sp.optimizer_params)

    task_context_section = ""
    if opt_sp.task_context:
        tc_display = {k: v for k, v in opt_sp.task_context.items() if k != "raw_description" and v}
        if tc_display:
            task_context_section = (
                "\n\nTASK CONTEXT (structured domain understanding — refine if inaccurate):\n"
                + json.dumps(tc_display, indent=2)
            )

    l1_surface = compile_l1_surface(cycle, round_num=round_num, n_variants=0)
    catalogue = _format_l1_generate_field_catalogue(
        l1_surface,
        opt_sp.l1_section_overrides,
        opt_sp.l1_section_overrides_text,
    )

    return L2Surface(
        current_params=current_params,
        task_context_section=task_context_section,
        escalation_section=rendered["escalation_section"],
        warning_inventory=rendered["warning_inventory"],
        l2_directive=rendered["l2_directive"],
        validation_failures=rendered["validation_failures"],
        runtime_failures=rendered["runtime_failures"],
        axes_l2=rendered["axes_l2"],
        l1_generate_field_catalogue=catalogue,
    )


# ---------------------------------------------------------------------------
# L1-critique blob — last consumer of the legacy register-walk pattern.
# ---------------------------------------------------------------------------


def compile_l1_critique_blob(
    cycle: Cycle,
    scoring_result: L1ScoringResult,
    schema: PipelineSchema | None,
    *,
    round_num: int,
) -> str:
    """Walk L1-critique sections, drop empties, join with blank lines.

    L1-critique stays on the legacy blob format because nothing external
    mutates its surface — there is no L4-style channel that could drop a
    section permanently. L1-generate and L2 graduated to typed surfaces.
    """
    ctx = compile_layer_context(
        Layer.L1_CRITIQUE,
        cycle,
        round_num=round_num,
        pipeline_schema=schema,
        scoring_result=scoring_result,
    )
    sections: dict[str, str] = {}
    for name in _L1_CRITIQUE_ORDER:
        if text := _SECTIONS[name](ctx):
            sections[name] = text
    return "\n\n".join(sections[name] for name in _L1_CRITIQUE_ORDER if name in sections)


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
    dispatch_msg = compile_l1_critique_blob(
        cycle,
        scoring_result,
        schema,
        round_num=round_num,
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


class OptimizerAction(enum.StrEnum):
    """Whether the next round runs as a normal round or as a probe round.

    Probe rounds scope evaluation to warned queries only; normal rounds
    run on the full scoring set.
    """

    NORMAL_ROUND = "normal_round"
    PROBE_ROUND = "probe_round"


@dataclass
class TransitionResult:
    """L2/L3 transition result.

    L2 may write any combination of these fields on the next OSP — they
    are independent. Surface mutations (`scheme_overrides`,
    `text_overrides`, `template_override`) target L1's prompt surface;
    `directive`, `optimizer_params`, and `task_context` are L2's strategic
    levers; `action` controls whether the next round is normal or a probe.
    """

    opt_search_point: OptSearchPoint
    pipeline_params: dict | None = None
    task_context: TaskDecomposition | None = None
    l2_directive: str = ""
    action: OptimizerAction = OptimizerAction.NORMAL_ROUND
    scheme_overrides: dict[str, bool] = field(default_factory=dict)
    text_overrides: dict[str, str] = field(default_factory=dict)
    template_override: str = ""
    l2_output_failures: list[ValidatorOutcome] = field(default_factory=list)
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
    """L2: refine the optimizer's strategy by writing onto ``OptSearchPoint``.

    L2 fires on L1 stall (per ``l1_patience``). When it fires it can write
    any combination of: a strategic directive, optimizer params, task
    context, L1-surface section/text/template overrides. It also chooses
    whether the next round is a normal round or a probe round
    (:class:`OptimizerAction`). Fields not set in L2's output are left
    untouched on the OSP.
    """

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
        candidate_scores = cycle.rounds[-1].candidate_scores if cycle.rounds else []
        surface = compile_l2_surface(
            cycle,
            round_num=round_num,
            candidate_scores=candidate_scores,
            escalation_check_result=escalation_check_result,
            pipeline_params=pipeline_params,
        )
        return surface.to_compile_vars()

    def build_result(
        self,
        raw: dict,
        opt_sp: OptSearchPoint,
        prompt: str,
        *,
        pipeline_params: dict | None,
    ) -> TransitionResult:
        section_names = {f.value for f in L1_GENERATE_SECTION_FIELDS}

        changes: dict[str, Any] = {}
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
            action = OptimizerAction(raw.get("action", "normal_round"))
        except ValueError:
            action = OptimizerAction.NORMAL_ROUND

        l2_directive = raw.get("directive", "")
        if not isinstance(l2_directive, str):
            l2_directive = ""

        scheme_overrides_raw = raw.get("scheme_overrides") or {}
        text_overrides_raw = raw.get("text_overrides") or {}
        scheme_overrides: dict[str, bool] = {}
        text_overrides: dict[str, str] = {}
        if isinstance(scheme_overrides_raw, dict):
            for k, v in scheme_overrides_raw.items():
                if k in section_names:
                    scheme_overrides[k] = bool(v)
                else:
                    logger.warning("L2 scheme_overrides: ignoring unknown section %r", k)
        if isinstance(text_overrides_raw, dict):
            for k, v in text_overrides_raw.items():
                if k in section_names:
                    text_overrides[k] = str(v)
                else:
                    logger.warning("L2 text_overrides: ignoring unknown section %r", k)

        template_override = raw.get("template_override", "")
        if not isinstance(template_override, str):
            template_override = ""

        # Loop 4 — validate L2's parsed output. Build a normalized snapshot
        # so validators see the cleaned, type-checked values (not raw LLM
        # JSON which may contain stray non-string entries that would crash
        # text scans).
        validator_input: dict[str, Any] = {
            "directive": l2_directive,
            "template_override": template_override,
            "text_overrides": dict(text_overrides),
        }
        l2_output_failures = run_l2_output_validators(validator_input, opt_sp)
        if l2_output_failures:
            logger.warning(
                "L2 output failed %d validator(s): %s",
                len(l2_output_failures),
                ", ".join(o.validator_id for o in l2_output_failures),
            )

        logger.debug(
            "L2 refine_strategy: %d param changes, task_context %s, action=%s, "
            "directive=%d chars, scheme_overrides=%d, text_overrides=%d, template_override=%d chars",
            len(raw.get("optimizer_params", {})),
            "updated" if new_task_context else "unchanged",
            action,
            len(l2_directive),
            len(scheme_overrides),
            len(text_overrides),
            len(template_override),
        )

        new_opt_sp = opt_sp.mutate(source="l2_context", **changes) if changes else opt_sp
        return TransitionResult(
            opt_search_point=new_opt_sp,
            task_context=new_task_context,
            l2_directive=l2_directive,
            action=action,
            scheme_overrides=scheme_overrides,
            text_overrides=text_overrides,
            template_override=template_override,
            l2_output_failures=l2_output_failures,
            debug_prompt=prompt,
            debug_response=raw,
        )

    def apply_side_effects(self, cycle: Cycle, result: TransitionResult, round_num: int) -> None:
        from promptpotter.application.optimization.cycle import record_decision
        from promptpotter.domain.run_records import DecisionKind

        if result.task_context:
            cycle.opt_sp.task_context = result.task_context
        cycle.opt_sp.l2_directive = result.l2_directive
        if result.scheme_overrides:
            cycle.opt_sp.l1_section_overrides = {
                **cycle.opt_sp.l1_section_overrides,
                **result.scheme_overrides,
            }
        if result.text_overrides:
            cycle.opt_sp.l1_section_overrides_text = {
                **cycle.opt_sp.l1_section_overrides_text,
                **result.text_overrides,
            }
        if result.template_override:
            cycle.opt_sp.l1_template_override = result.template_override
        cycle.opt_sp.l2_output_failures = list(result.l2_output_failures)
        cycle.escalation.l2.record_entry(cycle.best_accuracy, cycle.best_composite)

        is_probe = result.action == OptimizerAction.PROBE_ROUND
        record_decision(
            cycle.pending_decisions,
            DecisionKind.PROBE_ROUND_COMMITMENT,
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
            "scheme_overrides_count": len(result.scheme_overrides),
            "text_overrides_count": len(result.text_overrides),
            "template_override_changed": bool(result.template_override),
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
        # Loop 4 — validator outcomes on L2's own output (heal L2).
        l2_output_failures_section = format_l2_output_failures_for_l3(
            list(opt_sp.l2_output_failures)
        )

        return {
            "current_plan": opt_sp.plan or "(none — default strategy)",
            "l2_summary": l2_summary,
            "rendered_prompt": opt_sp.render(),
            "pipeline_section": format_pipeline_section(pipeline_params, pipeline_schema),
            "runtime_failures_section": (
                "\n\n" + runtime_failures_section if runtime_failures_section else ""
            ),
            "l2_output_failures_section": (
                "\n\n" + l2_output_failures_section if l2_output_failures_section else ""
            ),
            "axes_digest": format_axis_digest_block(
                cycle.axes.digest_for_l3() if cycle.axes else None,
                _L3_AXIS_LABELS,
                header="HISTORICAL CONTEXT:",
            ),
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
            source="l3_plan",
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

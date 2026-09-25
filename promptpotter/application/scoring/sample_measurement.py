"""Single-sample pipeline execution + scoring; {{var}} interpolation excludes ground_truth."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any, cast

import httpx

from promptpotter.application.optimization.dispatch.llm_call.heartbeat import heartbeat
from promptpotter.application.optimization.pobb.classification import terminal_ranking
from promptpotter.application.run_phase_control import declare_run_phase, pause_requested
from promptpotter.application.scoring.cell_envelope import CellEnvelope
from promptpotter.application.scoring.evaluators import materialize_sample_values
from promptpotter.application.scoring.formula import rescore_results
from promptpotter.application.scoring.formula.compiler import ScoringFormulaError
from promptpotter.application.scoring.row_diagnostics import rank_ground_truth
from promptpotter.config.settings import NO_RESULT
from promptpotter.domain.l4.proxies import (
    INNER_FACT_KEYS,
    PARENT_LEVEL_SE_KEY,
)
from promptpotter.domain.phases import RunPhase
from promptpotter.domain.pipeline_schema import WebSpendBound
from promptpotter.domain.results_health import classify_result, terminal_node
from promptpotter.domain.sample import Sample
from promptpotter.domain.scoring import QueryMeasurement, extract_item_label, is_hit, turn_scalars
from promptpotter.domain.spend import StepTokenUsage, TokenAccount
from promptpotter.infrastructure.llm.pricing import rate_ceiling
from promptpotter.infrastructure.llm.spend_book import FRAMING_TOKENS, Billed, SendBound
from promptpotter.infrastructure.llm.telemetry import _CURRENT_ROUND, emit_token_usage
from promptpotter.shared.errors import (
    CellUnscoreableError,
    ErrorCategory,
    SendRefusedError,
)

if TYPE_CHECKING:
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.intelligence.indexes.axis import AxisIndex
    from promptpotter.domain.pipeline_schema import PipelineSchema

logger = logging.getLogger(__name__)

STALE_DATA_LOAD_PROTOCOL: tuple[str, ...] = ("rerun", "sampleswitch")
"""Step order for handling a degraded cached query. ``execute_stale_data_protocol``
walks this in order; first step that returns a non-degraded result wins."""

RERUN_TRIGGER_COUNT: int = 3
"""Number of degradation sightings (cached + historical) before rerunning."""

SAMPLESWITCH_MIN_DEGRADATION_RATE: float = 0.5
"""Historical degradation rate at which sampleswitch short-circuits to the
cached deprecated answer instead of re-evaluating."""

# TARGET-prompt interpolation — the `{{var}}` slots a dataset row fills on its way to the
# backend. NOT the dispatch-hub `injection_table()` registry, which fills `{{slot}}`s in the
# OPTIMIZER's prompts. Same syntax, two populations, two regexes (the other is
# `dispatch/facade.py`); a signal for an L1/L2/L3 prompt goes there, never here.
_TEMPLATE_VAR_RE = re.compile(r"\{\{(\w+)\}\}")

# Never interpolated into a prompt — answer leakage. Keep it naming the field that carries
# how well a row was answered, which has been renamed under it before.
_EXCLUDED_FIELDS: frozenset[str] = frozenset(
    {
        "ground_truth",
        "fitness",
        "error",
        "source_sheet",
    }
)


def interpolate_prompt(text: str, variables: dict[str, Any]) -> str:
    """Replace {{key}} from variables; skip _EXCLUDED_FIELDS; missing keys left as-is."""
    expected = set(_TEMPLATE_VAR_RE.findall(text))
    if not expected:
        return text

    safe_vars = {k: v for k, v in variables.items() if k not in _EXCLUDED_FIELDS}
    for key in expected:
        if key in safe_vars:
            text = text.replace("{{" + key + "}}", str(safe_vars[key]))
        else:
            logger.debug("Template variable {{%s}} not in sample fields — left as-is", key)
    return text


def interpolate_pipeline_params(
    pipeline_params: dict[str, Any],
    query_data: dict[str, Any],
) -> dict[str, Any]:
    has_templates = False
    for v in pipeline_params.values():
        if isinstance(v, dict) and "prompt" in v:
            prompt = v["prompt"]
            if isinstance(prompt, str) and _TEMPLATE_VAR_RE.search(prompt):
                has_templates = True
                break

    if not has_templates:
        return pipeline_params

    out = dict(pipeline_params)
    for node_name, node_cfg in out.items():
        if not isinstance(node_cfg, dict) or "prompt" not in node_cfg:
            continue
        prompt = node_cfg["prompt"]
        if not isinstance(prompt, str):
            continue
        interpolated = interpolate_prompt(prompt, query_data)
        if interpolated is not prompt:
            out[node_name] = {**node_cfg, "prompt": interpolated}
    return out


__all__ = ["execute_stale_data_protocol", "measure_sample", "needs_rerun"]

# Wire-response keys always kept on pipeline_data, whatever the pipeline schema.
# ``reasoning_trace`` is the task model's chain-of-thought (head-capped at the backend); the
# critique tier reads it to diagnose WHERE a deduction broke.
#
# ``pipeline_params`` is deliberately NOT here: it is constant across a run, and the archive
# already lifts it onto the index entry (`measurement_archive.py::_summary`), which is where
# its one reader takes it from (`intelligence/indexes/axis.py`). Per-sample it was the same
# ~3 KB blob re-stored on every row — 44% of a run file, addressable without it.
_INFRA_KEYS: frozenset[str] = frozenset(
    {
        "step_timings",
        "step_tokens",
        "total_time",
        "diagnostics",
        "reasoning_trace",
        # The cell's conversation, where the backend has one. An infra key like the trace beside
        # it: a dataset does not declare an `observation_mapping` for how its backend talks, and
        # a formula must never read a turn — see `domain/scoring.py::TurnRecord`.
        "turns",
        # Where an episode's wall clock went, by harness phase (`PipelineData.step_phases`).
        "step_phases",
        # L4: the arm's own half of a paired cell difference (`domain/l4/proxies.py`). It rides
        # here rather than as a declared observation because the panel reads it and the scoring
        # formula must not — see the emit site in `runner/inner/spawn.py`.
        PARENT_LEVEL_SE_KEY,
        # L4: what the inner campaign knows about ITSELF (`domain/l4/proxies.py`). Infra keys, so
        # they need no dataset `observation_mapping` — an undeclared observation is dropped here
        # silently — the trap `initialization/wiring.py::_verify_required_observation_keys`
        # exists to catch.
        *INNER_FACT_KEYS,
    }
)


# Which wire keys `_compute_step_tokens` copies onto the entry it re-seeds, and the type each must
# arrive as. DECLARED, and asserted TOTAL over `StepTokenUsage`, so a key added to that type and
# not to this table fails at import rather than vanishing from every row it was reported on.
_WIRE_SEEDED: dict[str, type] = {
    "cost_usd": float,
    "model": str,
    "served_by": str,
    "finish_reason": str,
    "reasoning": int,
    "cache_read": int,
}

# The keys this function answers for ITSELF: the two counts, its own `estimated` verdict, and
# `provider` — the overlay routed the call, so whoever we configured is whoever billed us.
_SEAM_OWNED: frozenset[str] = frozenset({"input", "output", "estimated", "provider"})

assert set(_WIRE_SEEDED) | _SEAM_OWNED == set(StepTokenUsage.__annotations__), (
    "every StepTokenUsage key is either copied from the wire (_WIRE_SEEDED) or answered by "
    "_compute_step_tokens itself (_SEAM_OWNED) — one that is neither is dropped in silence"
)
# The union above is satisfied by a key listed in BOTH, which would read as copied and be
# overwritten. So: disjoint too.
assert _SEAM_OWNED.isdisjoint(_WIRE_SEEDED), (
    f"{sorted(_SEAM_OWNED & set(_WIRE_SEEDED))} is listed as both copied and answered here — "
    f"a key has one source, and the seam's would silently win"
)


def _step_parts(
    step_tokens: Mapping[str, StepTokenUsage], step_timings: Mapping[str, Any]
) -> list[Billed]:
    """One sample's per-node backend usage, one billed part per node that used anything. A step
    with no tokens still counts when it carries a fixed cost — spend is the headline."""
    parts: list[Billed] = []
    for node_name, entry in step_tokens.items():
        cost_usd = entry.get("cost_usd")
        if entry["input"] == 0 and entry["output"] == 0 and not cost_usd:
            continue
        raw_dur = step_timings.get(node_name)
        parts.append(
            Billed(
                TokenAccount.from_step_entry(entry),
                cost_usd,
                served_by=entry.get("served_by"),
                model=entry.get("model"),
                node=str(node_name),
                provider=entry.get("provider"),
                duration_s=float(raw_dur) if isinstance(raw_dur, (int, float)) else 0.0,
            )
        )
    return parts


def emit_replayed_step_tokens(
    step_tokens: Mapping[str, StepTokenUsage], step_timings: Mapping[str, Any]
) -> None:
    """Meter a replayed row's per-node backend usage, flagged ``cached``: it spent no money, but the
    search still made the call, so leaving it unmetered prices our cache history. A fresh cell is
    metered where it was admitted (:func:`cell_billing`)."""
    for part in _step_parts(step_tokens, step_timings):
        emit_token_usage(
            node=part.node or "backend",
            kind="backend",
            usage=part.usage,
            duration_s=part.duration_s or 0.0,
            model=part.model,
            provider=part.provider,
            served_by=part.served_by,
            cost_usd=part.cost_usd,
            cached=True,
        )


def cell_billing(
    pipeline_schema: PipelineSchema, wire_params: dict[str, Any]
) -> Callable[[dict[str, Any]], list[Billed] | None]:
    """What a cell's reply ``data`` says each node billed — ``None`` where it reports no usage at
    all, which is charged the cell's whole bound. A node's guessed usage is no report, so it bills
    nothing here: a backend reports every node it ran (``backend-integration.md``)."""

    def billed(data: dict[str, Any]) -> list[Billed] | None:
        if not isinstance(data.get("step_tokens"), dict):
            return None
        reported = {
            name: entry
            for name, entry in _compute_step_tokens(data, pipeline_schema, wire_params).items()
            if not entry["estimated"]
        }
        parts = _step_parts(reported, data.get("step_timings") or {})
        web = data.get("web_cost")
        usd = web.get("usd") if isinstance(web, dict) else None
        if isinstance(usd, int | float) and not isinstance(usd, bool) and usd:
            searched = next(
                (n.name for n in pipeline_schema.nodes if isinstance(n.spend_bound, WebSpendBound)),
                "web_search",
            )
            parts.append(Billed(TokenAccount(), float(usd), node=searched))
        return parts

    return billed


async def cell_bound(session: Session, wire_params: Mapping[str, Any]) -> SendBound | None:
    """The most one cell can bill: the bound each node's backend serves, priced at the dearest the
    model this configuration runs it on can charge. ``None`` for a backend whose own sends are
    each admitted and that derives no bound for the cell they make up. A backend bounding nothing,
    or an LLM node it serves no bound for, leaves the cell unbounded — and an unbounded cell
    cannot run under a ceiling."""
    client = session.backend_client
    if client.holds_own_sends and not client.derives_spend_bounds:
        return None
    usd = 0.0
    input_tokens = output_tokens = 0
    bounded = priced = True
    served = False
    for node in session.pipeline_schema.nodes:
        raw_cfg = wire_params.get(node.name)
        cfg = raw_cfg if isinstance(raw_cfg, Mapping) else {}
        spend = client.node_spend_bound(node, cfg)
        if spend is None:
            bounded = bounded and not node.is_llm
            continue
        served = True
        if isinstance(spend, WebSpendBound):
            usd += spend.queries * spend.usd_per_query
            continue
        reply = int(cfg.get("max_tokens") or spend.max_tokens)
        reads = spend.input_bytes + FRAMING_TOKENS
        input_tokens += spend.attempts * reads
        output_tokens += spend.attempts * reply
        model, provider = cfg.get("model"), cfg.get("provider")
        ceiling = (
            await rate_ceiling(model, provider, hosts=spend.hosts)
            if isinstance(model, str) and isinstance(provider, str)
            else None
        )
        if ceiling is None:
            priced = False
        else:
            tier = ceiling.at(reads)
            usd += spend.attempts * (ceiling.per_request + reads * tier.input + reply * tier.output)
    if not (served and bounded):
        return SendBound(input_tokens=input_tokens, output_tokens=None, usd=None)
    return SendBound(
        input_tokens=input_tokens, output_tokens=output_tokens, usd=usd if priced else None
    )


def _compute_step_tokens(
    resp_data: dict[str, Any],
    pipeline_schema: PipelineSchema,
    wire_params: dict[str, Any],
) -> dict[str, StepTokenUsage]:
    """Per-LLM-node token counts, seeded from the backend's own ``step_tokens`` and falling back to
    a chars/4 heuristic. Every entry carries the node's ``model`` — the overlay always pinned one."""
    out: dict[str, StepTokenUsage] = {}

    def _configured(node_name: str, key: str) -> str | None:
        """A string the dataset overlay pinned for this node. Neither ``model`` nor ``provider`` is
        guessable downstream: the provider is half of a price, so dropping it bills the wrong vendor."""
        cfg = wire_params.get(node_name)
        value = cfg.get(key) if isinstance(cfg, dict) else None
        return value if isinstance(value, str) else None

    raw = resp_data.get("step_tokens")
    if isinstance(raw, dict):
        for node_name, entry in raw.items():
            if isinstance(entry, dict):
                # The connector's own entry is REBUILT here rather than carried, so a key absent
                # from `_WIRE_SEEDED` is dropped however faithfully the connector reported it.
                seeded: dict[str, Any] = {
                    "input": int(entry.get("input", 0)),
                    "output": int(entry.get("output", 0)),
                    "estimated": False,
                }
                # What each key buys, and what omitting one costs: `backend-integration.md`
                # § Optional `step_tokens` fields.
                for key, kind in _WIRE_SEEDED.items():
                    value = entry.get(key)
                    if isinstance(value, bool) or value is None:
                        continue
                    if kind is str:
                        if isinstance(value, str):
                            seeded[key] = value
                    elif isinstance(value, (int, float)):
                        seeded[key] = kind(value)
                if "model" not in seeded and (model := _configured(node_name, "model")):
                    seeded["model"] = model
                # `_SEAM_OWNED`, so never the wire's — TermNorm's `spend.backend.model` answers a
                # provider slug here.
                provider = _configured(node_name, "provider")
                if provider is not None:
                    seeded["provider"] = provider
                out[node_name] = cast("StepTokenUsage", seeded)

    for node in pipeline_schema.nodes:
        if not node.is_llm or node.name in out:
            continue

        node_cfg = wire_params.get(node.name) or {}
        in_text = node_cfg.get("prompt", "") if isinstance(node_cfg, dict) else ""

        out_text_parts: list[str] = []
        for mapping in node.observation_mappings:
            if not mapping.is_llm:
                continue
            pipeline_val = resp_data.get(mapping.pipeline_key)
            if pipeline_val is None:
                continue
            field = mapping.output_field
            if field and isinstance(pipeline_val, dict):
                picked = pipeline_val.get(field, "")
                out_text_parts.append(str(picked))
            elif field and isinstance(pipeline_val, list):
                for item in pipeline_val:
                    if isinstance(item, dict) and field in item:
                        out_text_parts.append(str(item[field]))
            else:
                out_text_parts.append(str(pipeline_val))
        out_text = " ".join(out_text_parts)

        estimated: StepTokenUsage = {
            "input": len(in_text) // 4,
            "output": len(out_text) // 4,
            "estimated": True,
        }
        model = _configured(node.name, "model")
        if model is not None:
            estimated["model"] = model
        provider = _configured(node.name, "provider")
        if provider is not None:
            estimated["provider"] = provider
        out[node.name] = estimated

    return out


def _error_result(
    sample: Sample,
    error_msg: str,
    *,
    category: ErrorCategory,
) -> QueryMeasurement:
    """``category`` is the typed error channel and owns "this sample errored"; ``error`` is the human
    message. Error rows carry no ``hit``/``score`` — those belong to ``rescore_results`` alone."""
    return QueryMeasurement(
        sample_id=sample.id,
        sample_key=sample.key,
        query=sample.query,
        ground_truth=sample.ground_truth or "",
        predicted="ERROR",
        cached=False,
        error=error_msg or "unknown error",
        error_category=category,
        pipeline_data=None,
    )


def _extract_upstream_detail(exc: httpx.HTTPStatusError) -> str:
    """Pull the structured upstream summary out of a backend error body, so the operator sees what
    the provider actually complained about instead of a bare ``502 Bad Gateway``."""
    body_text = (exc.response.text or "").strip()
    if not body_text:
        return ""
    try:
        body = exc.response.json()
    except Exception:
        return body_text[:300]
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, dict):
        provider = detail.get("upstream_provider") or "?"
        model = detail.get("upstream_model") or "?"
        msg = detail.get("upstream_message") or detail.get("error_code") or "?"
        ustatus = detail.get("upstream_status")
        prefix = f"upstream={provider}:{model}"
        if ustatus is not None:
            prefix = f"{prefix} {ustatus}"
        return f"{prefix} :: {msg}"
    if isinstance(detail, str):
        return detail[:300]
    return body_text[:300]


# TermNorm's typed 422 codes for a model that ANSWERED but left nothing gradeable after its repair
# turns (`llm_providers.py`) — an answer cut at the candidate's own `max_tokens`, or one that is not
# the declared JSON. That is what THIS configuration produced, so it is a measured miss: never a
# hole (a round halts before electing on one) and never a stop for the walk.
_OUTPUT_FAILURE_CODES = frozenset(
    {"json_parse_failed", "schema_validation_failed", "output_truncated"}
)


def _answered_nothing(exc: httpx.HTTPStatusError) -> dict[str, Any] | None:
    """The response ``data`` of a cell whose model answered nothing gradeable — no answer, and the
    tokens it was billed for — or ``None`` where the error is not that."""
    try:
        detail = exc.response.json().get("detail")
    except Exception:
        return None
    if not isinstance(detail, dict) or detail.get("error_code") not in _OUTPUT_FAILURE_CODES:
        return None
    return {"step_tokens": detail.get("step_tokens") or {}}


def _classify_http_error(exc: httpx.HTTPStatusError) -> tuple[ErrorCategory, str]:
    """Never a 429: ``BackendClient.run_query`` answers every one with the run's backpressure, which
    re-sends the cell or refuses the run. A throttle is the provider's load, never a row charged to
    the candidate."""
    code = exc.response.status_code
    upstream = _extract_upstream_detail(exc)
    if 400 <= code < 500:
        tail = f" :: {upstream}" if upstream else ""
        return ErrorCategory.CLIENT, f"HTTP {code} — caller config rejected by backend{tail}"
    tail = f" :: {upstream}" if upstream else ""
    return ErrorCategory.SERVER, f"HTTP {code} — backend transient error{tail}"


async def measure_sample(
    sample: Sample,
    session: Session,
    pipeline_params: dict[str, Any] | None = None,
) -> QueryMeasurement:
    query = sample.query
    # The ONE place a labelless cell becomes a row. `QueryMeasurement.ground_truth` is `str`, and
    # everything downstream of here — the matcher, the rank, the archive — reads it as one; a
    # verifier-graded cell says so by carrying `""`, which no answer matches.
    ground_truth = sample.ground_truth or ""

    pipeline_schema = session.pipeline_schema

    try:
        wire_params = interpolate_pipeline_params(pipeline_params or {}, sample.model_dump())

        client = session.backend_client
        bound = await cell_bound(session, wire_params)
        envelope = CellEnvelope(
            client.cell_envelope_s(query, wire_params), label=f"{sample.id}:{query[:40]}"
        )
        # Created UNCONDITIONALLY and OUTSIDE the envelope scope: this loop carries the envelope's
        # only sighting of a machine sleep, and its teardown must not unwind inside the timeout.
        heartbeat_task = asyncio.create_task(
            heartbeat(
                session.state.ledger,
                call_id=f"scoring:{sample.id}",
                node="backend_scoring",
                round_num=_CURRENT_ROUND.get(),
                start_monotonic=time.monotonic(),
                on_suspend=envelope.on_suspend,
            )
        )
        try:
            async with envelope:
                try:
                    resp = await client.run_query(
                        query,
                        pipeline_params=wire_params,
                        bound=bound,
                        billed=cell_billing(pipeline_schema, wire_params),
                    )
                except httpx.HTTPStatusError as exc:
                    if (answered := _answered_nothing(exc)) is None:
                        raise
                    logger.warning(
                        "measure_sample for %s: answered nothing gradeable (%s) — a miss",
                        query[:60],
                        _extract_upstream_detail(exc),
                    )
                    resp = {"data": answered}
        finally:
            # Cancel whether the query succeeded or raised — an in-flight task survives and
            # keeps appending progress records against a closed call.
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.warning(
                    "heartbeat task for backend scoring raised on teardown",
                    exc_info=True,
                )
        data = resp.get("data", {})

        # The head of the TERMINAL ranker's output, read through the schema rather than a
        # hardcoded key: candidate_ranking when token_matching is terminal, final_ranking when
        # an llm_ranking/llm_only node is.

        ranked = terminal_ranking({"pipeline_data": data}, pipeline_schema)
        # Where the backend DECLARED an answer key, that is the answer — the ranking is not
        # consulted, because two sources for one fact is how they come to disagree. A backend
        # declaring none keeps the ranking as its only source, which is every ranked-label one.
        # Either way an absent answer is `NO_RESULT`, and that sentinel is the honest reading:
        # the pipeline ran and emitted nothing nameable (`domain/results.py`).
        answer_key = session.backend_client.answer_key
        if answer_key is not None:
            predicted = str(data.get(answer_key) or "").strip() or NO_RESULT
        else:
            predicted = extract_item_label(ranked[0]) if ranked else NO_RESULT
        if predicted == "ERROR":
            return _error_result(
                sample,
                "Backend returned ERROR as candidate — pipeline internal failure for this query.",
                category=ErrorCategory.PIPELINE,
            )
        gt_rank, n_candidates = rank_ground_truth(ranked, predicted, ground_truth)

        # `result_ranking` is the canonical derived terminal ranking the scorer + find_gt_rank
        # read; the raw per-node observation keys are copied below for their own diagnostics.
        pd: dict[str, Any] = {"result_ranking": ranked}
        for key in pipeline_schema.observation_keys | _INFRA_KEYS:
            val = data.get(key)
            if val is not None:
                pd[key] = val
        # Here, not in a connector: every backend that emits `turns` earns the same terms.
        pd.update(turn_scalars(pd.get("turns")))
        terminal_node = data.get("terminal_node")
        if terminal_node is None:
            st = pd.get("step_timings") or {}
            for node in pipeline_schema.nodes:
                if st.get(node.name) is not None:
                    terminal_node = node.name
        if terminal_node is not None:
            pd["terminal_node"] = terminal_node

        # The envelope's own final reading, taken AFTER its scope closed. Here and not in a
        # connector: this is the one seam that HOLDS an envelope, so every backend gets the answer.
        if envelope.budget_s is not None:
            pd["unworked_s"] = envelope.unworked

        # The bare question, where the dataset declared one distinct from `query` — banked so a
        # JUDGE can read it, since a judge is handed this row and never the `Sample`. Absent on
        # every dataset where the two are the same string, which is what keeps the judges'
        # fallback to `query` the normal path rather than a special case.
        if sample.question:
            pd["question"] = sample.question

        # Metered where the cell was admitted (`BackendClient.run_query`); a replay of this row
        # meters itself off what is banked here.
        step_tokens = _compute_step_tokens(data, pipeline_schema, wire_params)
        if step_tokens:
            pd["step_tokens"] = step_tokens

        result: dict[str, Any] = {
            "sample_id": sample.id,
            "sample_key": sample.key,
            "query": query,
            "predicted": predicted,
            "ground_truth": ground_truth,
            "cached": False,
            "error": None,
            "error_category": None,
            "n_candidates": n_candidates,
            "ground_truth_rank": gt_rank,
            "pipeline_data": pd,
        }

        # TOP-LEVEL into `pipeline_data`, exactly where a backend's own observation lands — that
        # is what makes a per-sample evaluator addressable from a scoring formula. Nested under an
        # `evaluators` key it was not: `cell_namespace` turns a dict into a `SimpleNamespace`, and
        # the AST allowlist bans attribute access, so the value was materialized into a shape no
        # formula could name. `validate_campaign_evaluator` refuses a name that would collide here.
        #
        # Banked BEFORE `rescore_results`, and that ordering is the contract: the cached-replay
        # path (`query_loop.py::_materialize_cached`) never re-enters this function, so a value
        # not written into the row now is one the formula raises `ScoringTermMissingError` on for
        # every later cache hit. For an LLM-backed evaluator it is also what stops a re-bill —
        # which is per TERM, so a multi-step schema's three gradings are three banked keys.
        pd.update(
            await materialize_sample_values(
                pipeline_schema,
                result,  # type: ignore[arg-type]
                extra=session.scoring.judges,
            )
        )

        assert session.scoring.scorer is not None, "session.scoring.scorer required for measurement"
        try:
            rescore_results([result], session.scoring.scorer)
        except ScoringFormulaError as exc:
            # A formula CONTRACT bug — it raised, or returned a non-finite. Deterministic, so every
            # cell fails it, and the row is marked so the run stops rather than grading a campaign
            # against a broken formula. A judge that merely could not grade never arrives here:
            # `rescore_results` resolves that row to UNSCORED, keeping the paid measurement.
            # The outer catch-all would have banked `pipeline_data=None` and thrown a paid cell away.
            logger.warning("measure_sample could not score %s: %s", query[:60], exc)
            result["error"] = str(exc)
            result["error_category"] = ErrorCategory.PIPELINE
        return result  # type: ignore[return-value]
    except httpx.HTTPStatusError as exc:
        category, error_msg = _classify_http_error(exc)
        logger.warning("measure_sample for %s: %s", query[:60], error_msg)
        return _error_result(sample, error_msg, category=category)
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        error_msg = f"{exc} — Backend may be down or unreachable."
        logger.warning("measure_sample CONNECTION for %s: %s", query[:60], error_msg)
        return _error_result(sample, error_msg, category=ErrorCategory.CONNECTION)
    except (KeyboardInterrupt, asyncio.CancelledError, SendRefusedError):
        # A refused send is refused for every cell after it: a stop, never a row.
        raise
    except CellUnscoreableError as exc:
        # The cell RAN and there is nothing to grade. The exception's own category says WHICH of the
        # two — a cut we made, or a reward the backend never produced — and a repair reads them apart.
        # What it paid was billed where it was admitted: an ungraded cell is not a free one.
        logger.warning("measure_sample %s for %s: %s", exc.category.value, query[:60], exc)
        return _error_result(sample, str(exc), category=exc.category)
    except Exception as exc:
        # Named by TYPE: a bare `TimeoutError()` has no message, and banked as its `str` it read
        # "unknown error" on every surface while the cause sat one attribute away.
        failure = f"{type(exc).__name__}: {exc}"
        logger.warning("measure_sample failed for %s: %s", query[:60], failure, exc_info=True)
        return _error_result(sample, failure, category=ErrorCategory.UNKNOWN)


def find_gt_rank(result: Mapping[str, Any]) -> int | None:
    """Find ground truth rank in the terminal ranking. Returns 1-indexed or None."""
    gt = result.get("ground_truth", "")
    if not gt:
        return None
    pd = result.get("pipeline_data") or {}
    rank, _ = rank_ground_truth(pd.get("result_ranking", []), result.get("predicted") or "", gt)
    return rank


def compare_rerun(
    cached_result: Mapping[str, Any], rerun_result: Mapping[str, Any]
) -> dict[str, Any]:
    cached_hit = is_hit(cached_result.get("fitness"))
    rerun_hit = is_hit(rerun_result.get("fitness"))
    hit_change = f"{'HIT' if cached_hit else 'MISS'}->{'HIT' if rerun_hit else 'MISS'}"

    cached_rank = find_gt_rank(cached_result)
    rerun_rank = find_gt_rank(rerun_result)
    rank_change = (
        f"{cached_rank}->{rerun_rank}"
        if cached_rank is not None and rerun_rank is not None
        else None
    )

    improved = (not cached_hit and rerun_hit) or (
        cached_rank is not None and rerun_rank is not None and rerun_rank < cached_rank
    )

    return {"hit_change": hit_change, "rank_change": rank_change, "improved": improved}


def _rerun_would_repeat_token_budget_failure(
    cached_result: Mapping[str, Any],
    rerun_pipeline_params: dict[str, Any] | None,
) -> bool:
    """Skip the rerun when the cached failure was a binding token budget and the rerun's cap is no
    larger: the ladder exists for TRANSIENT failures, and a config-fundamental one will not recover."""

    # Through the same helper ``classify_result`` stamps its codes with, never re-derived: these
    # membership tests are string matches on ``f"{node}:…"``, so a second spelling of the node
    # makes them MISS silently and the ladder pays for a rerun guaranteed to fail identically.
    node = terminal_node(cached_result)
    cl = classify_result(cached_result)
    budget_exhausted = (
        f"{node}:reasoning_budget_exhausted" in cl.infra_codes
        or f"{node}:output_truncated" in cl.infra_codes
    )
    if not budget_exhausted:
        return False

    cached_step = ((cached_result.get("pipeline_data") or {}).get("step_tokens") or {}).get(
        node
    ) or {}
    cached_completion = int(cached_step.get("output", 0))
    if cached_completion <= 0:
        # No reliable cap signal — be conservative and let the rerun happen.
        return False

    rerun_max_tokens = ((rerun_pipeline_params or {}).get(node) or {}).get("max_tokens")
    if rerun_max_tokens is None:
        # No override, so the rerun takes a backend default that may be larger or smaller
        # than the cached cap. Conservative: let it run.
        return False

    return int(rerun_max_tokens) <= cached_completion


def needs_rerun(row: Mapping[str, Any]) -> bool:
    """Whether the stale-data ladder re-sends a row: an infra or fatal code, never a bare warning.
    A schema repair that went on to answer leaves an advisory and a gradeable row, which replays."""
    return classify_result(row).is_fatal


async def execute_stale_data_protocol(
    protocol_steps: list[str],
    sample: Sample,
    cached_result: dict[str, Any],
    session: Session,
    *,
    pipeline_params: dict[str, Any] | None = None,
    axes: AxisIndex | None = None,
) -> tuple[dict[str, Any], str]:
    """Walk the stale-data ladder for a degraded cached query, returning ``(result, step_taken)``.
    Observation counts come from ``axes.sample_index``, constant within a round — no mutable state."""
    result = cached_result

    for step in protocol_steps:
        if pause_requested(session):
            declare_run_phase(session, RunPhase.PAUSED)
            return {**result, "cached": result.get("cached", False)}, "paused"
        if step == "rerun":
            historical = axes.sample_index.degradation_count(sample.id) if axes else 0
            effective_count = historical + 1
            if effective_count < RERUN_TRIGGER_COUNT:
                return {
                    **cached_result,
                    "cached": cached_result.get("cached", False),
                    "degraded_observed": True,
                    "degraded_obs_count": effective_count,
                    "degraded_obs_threshold": RERUN_TRIGGER_COUNT,
                }, "below_threshold"

            if _rerun_would_repeat_token_budget_failure(cached_result, pipeline_params):
                return {
                    **cached_result,
                    "cached": cached_result.get("cached", False),
                    "config_fundamental_skip": True,
                    "skip_reason": "rerun_max_tokens_le_cached_completion",
                }, "skipped_config_fundamental"

            result = dict(await measure_sample(sample, session, pipeline_params=pipeline_params))
            result["retry_of_degraded"] = True
            result["rerun_comparison"] = compare_rerun(cached_result, result)
            if not needs_rerun(result):
                return result, "rerun"

        elif step == "sampleswitch":
            if (
                axes
                and axes.sample_index.degradation_rate(sample.id)
                >= SAMPLESWITCH_MIN_DEGRADATION_RATE
            ):
                result = {**cached_result, "cached": True, "switched_out": True}
                return result, "sampleswitch"

    return {**result, "persistently_degraded": True}, "exhausted"

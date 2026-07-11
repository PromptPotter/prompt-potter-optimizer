"""Typed records for the run ledger. Frozen Pydantic, `record_type` discriminator for JSON round-trip.

The `CycleRecord` discriminated union lives here so the data shape stays with the domain;
resume-checkpoint policy (gating, helpers, exhaustiveness check) lives in
`application/optimization/resume_and_fork/decisions.py`.
"""

from __future__ import annotations

import enum
from typing import Annotated, Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, model_validator

from promptpotter.domain.pipeline_schema import NodeSearchNarrowing
from promptpotter.shared.clock import utcnow_iso

__all__ = [
    "CommandAckRecord",
    "CommandRecord",
    "ConfigOverrides",
    "CycleRecord",
    "CycleSeed",
    "CycleSeedRecord",
    "DecisionRecord",
    "ErrorRecord",
    "ForkSpec",
    "ForkTrigger",
    "LLMCallProgressRecord",
    "LLMCallRecord",
    "LLMCallStartRecord",
    "OperatorSweepFile",
    "PhaseRecord",
    "ResumeCheckpointKind",
    "ResumeCheckpointRecord",
    "RoundWarningKind",
    "RoundWarningRecord",
    "SnapshotRecord",
    "TokenUsageRecord",
]


class ResumeCheckpointKind(enum.StrEnum):
    """Ledger-decision kinds; adding a member also requires extending `RESUME_CHECKPOINT_GATING` (import-time exhaustiveness check)."""

    ROUND_WINNER = "round_winner"
    ELIMINATION_CUT = "elimination_cut"
    MARGIN_CUT = "margin_cut"
    LEADER_LOCK_IN = "leader_lock_in"
    L2_ESCALATION_TRIGGER = "l2_escalation_trigger"
    L3_ESCALATION_TRIGGER = "l3_escalation_trigger"
    PROBE_ROUND_COMMITMENT = "probe_round_commitment"
    FORK_CUT = "fork_cut"


class DecisionRecord(TypedDict):
    """Wire shape of one recorded decision as it rides ``RoundResult.decisions``.

    The serialized projection of :meth:`ResumeCheckpointRecord.to_dict` — read by
    the divergence-replay walker (``resume_and_fork/replayers.py``). ``kind`` is the
    enum *value* string (not the enum), so a round file round-trips without the
    optimization layer in scope.
    """

    kind: str
    inputs_ref: dict[str, Any]
    outcome: Any
    data: dict[str, Any]


class ResumeCheckpointRecord(BaseModel):
    """One recorded decision: ``inputs_ref`` + ``outcome`` drive divergence; ``data`` is archival."""

    model_config = ConfigDict(frozen=True)

    record_type: Literal["decision"] = "decision"
    kind: ResumeCheckpointKind
    inputs_ref: dict[str, Any] = Field(default_factory=dict)
    outcome: Any = None
    data: dict[str, Any] = Field(default_factory=dict)
    round: int | None = None
    timestamp: str = Field(default_factory=utcnow_iso)

    def to_dict(self) -> DecisionRecord:
        """Wire shape for round_data-JSON ``decisions`` payload."""
        return {
            "kind": self.kind.value,
            "inputs_ref": dict(self.inputs_ref),
            "outcome": self.outcome,
            "data": dict(self.data),
        }


class PhaseRecord(BaseModel):
    """A campaign-phase boundary event (round-start, l2-fired, origin-complete, …)."""

    model_config = ConfigDict(frozen=True)

    record_type: Literal["phase"] = "phase"
    phase: str
    event: str
    round: int | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    # In-memory-only carrier for the live ``RoundResult`` on ``round:display``
    # records. ``exclude=True`` keeps it off the persisted/streamed JSON — the
    # disk copy keeps only the lean ``payload['round_result']`` scalars (round,
    # accuracy, composite_fitness) the SSE→webapp chat reads. The fat per-sample
    # / per-candidate arrays already live in ``round_NNNN.json`` +
    # ``dashboard.json::rounds[]``; the ledger needs no third copy. Live
    # subscribers (LiveDashboardView, LiveDisplay) read this field; no disk
    # re-reader does. ``None`` on every record but ``round:display``.
    live_round_result: Any = Field(default=None, exclude=True, repr=False)
    timestamp: str = Field(default_factory=utcnow_iso)


class SnapshotRecord(BaseModel):
    """In-flight live-state snapshot; `event` discriminates (sample/candidate started/scored)."""

    model_config = ConfigDict(frozen=True)

    record_type: Literal["snapshot"] = "snapshot"
    event: str
    round: int
    candidate_idx: int | None = None
    candidate_total: int | None = None
    sample_idx: int | None = None
    sample_total: int | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=utcnow_iso)


class TokenUsageRecord(BaseModel):
    """LLM token + cost telemetry → `dashboard.json::spend`.

    `kind` splits `backend` (pipeline) vs `loop` (optimizer) for the
    "Backend $X • Loop $Y" line. `cost_usd` from provider wire (OpenRouter
    `usage.cost`) or `shared/spend.py` rate table; tokens always present for fallback.
    """

    model_config = ConfigDict(frozen=True)

    record_type: Literal["token_usage"] = "token_usage"
    kind: Literal["optimizer", "backend"]
    node: str
    model: str | None = None
    input_tokens: int
    output_tokens: int
    duration_s: float = 0.0
    cost_usd: float | None = None
    round: int | None = None
    timestamp: str = Field(default_factory=utcnow_iso)


class LLMCallStartRecord(BaseModel):
    """In-flight marker appended BEFORE the SDK call → `dashboard.json::in_flight`.

    Pairs with `LLMCallRecord` via `call_id` (hex). Keeps a multi-minute call from looking frozen.
    """

    model_config = ConfigDict(frozen=True)

    record_type: Literal["llm_call_start"] = "llm_call_start"
    call_id: str
    node: str
    round: int | None = None
    candidate_idx: int | None = None
    model: str | None = None
    started_at_ms: int
    # Sets the operator's latency expectation on the in-flight line.
    prompt_chars: int = 0
    timestamp: str = Field(default_factory=utcnow_iso)


class LLMCallProgressRecord(BaseModel):
    """Heartbeat every `HEARTBEAT_INTERVAL_S` while the SDK call is blocked → `in_flight.elapsed_s`. Cache replays skip it."""

    model_config = ConfigDict(frozen=True)

    record_type: Literal["llm_call_progress"] = "llm_call_progress"
    call_id: str
    node: str
    round: int | None = None
    elapsed_s: float
    # Optional live sub-status for the tick — the inner-campaign heartbeat
    # (``runner/inner_recursion.py``) sets it to ``"inner rX/Y · best Z%"`` so the
    # outer L4 chat/dashboard stay live while a multi-minute inner cycle runs.
    # ``None`` on ordinary optimizer heartbeats (unchanged behavior).
    detail: str | None = None
    timestamp: str = Field(default_factory=utcnow_iso)


class LLMCallRecord(BaseModel):
    """Full I/O of one optimizer LLM call; ledger-resident so `round_NNNN.json::nodes` is derived.

    `payload_kind='synthesized'` ⇒ replay where messages/response/usage are absent.
    """

    model_config = ConfigDict(frozen=True)

    record_type: Literal["llm_call"] = "llm_call"
    node: str
    round: int | None = None
    candidate_idx: int | None = None
    payload_kind: Literal["llm_call", "synthesized"] = "llm_call"
    call_id: str = ""
    # Opaque action-dict consumed by AuditTrailView — new fields don't churn the schema.
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=utcnow_iso)


class CommandRecord(BaseModel):
    """Inbound HTTP command appended to the canonical ledger.

    Sole writer at the API seam: `CommandDispatcher`. Three target ledgers:

    - Cycle-scoped commands (fork / stop / delete / cleanup-empty) ride the
      target cycle's ledger.
    - Campaign-lifecycle commands (archive / delete / unarchive) ride the
      campaign's root cycle ledger.
    - Workspace-scoped backend commands (register-backend /
      sync-backend-experiments) ride the workspace ledger at
      ``projects/{tenant}/.workspace/events.jsonl`` per the §0 Persistence
      sibling amendment.

    The runner (cycle-scoped, async) or the dispatcher (inline-apply
    workspace-scoped) emits a paired `CommandAckRecord` once applied.
    """

    model_config = ConfigDict(frozen=True)

    record_type: Literal["command"] = "command"
    command_id: str
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str
    expected_version: int | None = None
    issued_by_user_id: str = ""
    client_metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=utcnow_iso)


class CommandAckRecord(BaseModel):
    """Ack of a `CommandRecord` — emitted by the actuator that applied it.

    `status="applied"` ⇒ the mutation landed; `"rejected"` ⇒ the actuator
    refused (capability denied, target gone, etc.). `detail` is operator-
    readable explanation, never load-bearing for clients.

    `effect` is what the applier changed, keyed by domain field, each value a
    `{from, to}` pair — as opposed to `CommandRecord.payload`, which is what was
    asked for. The two diverge whenever the applier gates or infers: a
    `resolve-origin` payload names only the draft, so the fields its LLM turn
    moved are recorded nowhere else. Empty on rejection.
    """

    model_config = ConfigDict(frozen=True)

    record_type: Literal["command_ack"] = "command_ack"
    command_id: str
    status: Literal["applied", "rejected"]
    detail: str = ""
    effect: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=utcnow_iso)


class ErrorRecord(BaseModel):
    """Structured runner failure on ``CRASHED`` / ``RENDER_ERROR`` / ``DIVERGED``.

    Emitted from the runner's three ``except`` sites in
    ``application/runner/{entry,loop}.py`` via the kwargs-only
    :func:`emit_error_record` helper over the ``_CYCLE_LEDGER`` ContextVar.
    Mirrors the ``emit_token_usage`` pattern from ADR-0003.

    Sole writer of ``dashboard.json::error``:
    :class:`~promptpotter.infrastructure.projections.live_dashboard.view.LiveDashboardView._handle_error_record`.
    The launcher reads the trailing record from :class:`CycleResult.error` to
    populate :class:`JobRegistry` ``stop_reason`` / ``status`` without reaching
    into projection state.
    """

    model_config = ConfigDict(frozen=True)

    record_type: Literal["error"] = "error"
    # Exception class name — operator-facing diagnostic, never load-bearing for routing.
    kind: str
    # Operator-readable message picked at the throw site so no downstream
    # layer maps raw ``httpx``/Pydantic exception strings.
    message: str
    # Full Python traceback; set on ``CRASHED`` / ``RENDER_ERROR``, elided
    # on ``DIVERGED`` (resume-divergence is operator-recoverable, the message
    # alone carries the full diagnostic).
    traceback: str | None = None
    stop_reason: Literal["CRASHED", "RENDER_ERROR", "DIVERGED"]
    round: int | None = None
    timestamp: str = Field(default_factory=utcnow_iso)


# The closed set of self-healed degradations `emit_round_warning` can raise —
# one source so the record schema and the emit signature can't drift.
RoundWarningKind = Literal[
    "l1_zero_candidates",
    "l2_validator_soft_reject",
    "injection_budget_overrun",
    "layer_parse_failure",
]


class RoundWarningRecord(BaseModel):
    """Non-fatal, round-scoped degradation the operator must see on every channel.

    Distinct from :class:`ErrorRecord` (a fatal run halt) and from
    ``RoundDiagnostics`` (post-scoring analytics): these are mid-round
    self-heal events that previously logged ONLY to the server's stdout —
    the optimizer LLM returning empty/truncated output and the round
    recording zero candidates, an L2 framing-output validator soft-reject,
    an over-budget injection truncation. The self-healing rails recover and
    the run continues, but the operator never saw it on the dashboard, the
    round file, or the CLI.

    Rides the canonical ledger via :func:`emit_round_warning` over the
    ``_CYCLE_LEDGER`` ContextVar — the same shape as :func:`emit_error_record`
    /:func:`emit_token_usage`. Surfaced by ``LiveDashboardView`` (sole writer
    of ``dashboard.json::recent_loop_warnings``), ``AuditTrailView``
    (``round_NNNN.json::warnings``), and ``LiveDisplay`` (CLI/notebook line).
    ``message`` is composed operator-readable at the emit site so no
    downstream layer reformats it.
    """

    model_config = ConfigDict(frozen=True)

    record_type: Literal["round_warning"] = "round_warning"
    kind: RoundWarningKind
    # `error` = the round produced nothing usable (zero candidates); `warning`
    # = degraded but the round still progressed. Drives dashboard styling.
    severity: Literal["warning", "error"] = "warning"
    message: str
    round: int | None = None
    detail: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=utcnow_iso)


class ForkTrigger(enum.StrEnum):
    """Why a fork was minted — one value per caller of :func:`_mint_fork`."""

    OPERATOR_SWEEP = "operator_sweep"
    OPERATOR_DIAG = "operator_diag"
    OPERATOR_REWIND = "operator_rewind"
    OPERATOR_STEERED = "operator_steered"
    L2_REBASE = "l2_rebase"
    L3_REBASE = "l3_rebase"
    SCORING_DIVERGENCE = "scoring_divergence"


class ConfigOverrides(BaseModel):
    """The fork's `OptimizationConfig` delta — every field optional (absent
    inherits the parent), applied to the fork's snapshot at bootstrap; never
    mutates the parent's frozen config. Three kinds of knob ride here:

    - **Run limits** (`max_rounds` / `spend_budget_usd` / `token_budget` /
      patiences / `pobb_epsilon`) — absolute values the fork-time reconcile
      dialog re-sets ("3 of 6 rounds left" → confirm the fork's own ceiling).
    - **Selection policy** (`per_round_resubset`) — the `mechanisms.selection` toggle.
    - **Search-space policy** (`schema_field_rename`) — unlocks the field-NAME
      lever on the inner `l1_generate`'s output schema.

    The two policy knobs ride here for the same reason: each declares itself
    `Knob(Scope.POLICY, Estimand.SEARCH)` on its `CampaignConfig` field, so
    changing one invalidates search comparability and MUST mint a sibling cycle
    rather than mutate the running one (the operator's "behaviour-knob change →
    sibling cycle" workflow). `schema_field_rename`'s two writers are that
    operator fork and an L2/L3 `fork_proposal`.

    Domain twin of the `ConfigOverrides` wire schema."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_rounds: int | None = None
    spend_budget_usd: float | None = None
    token_budget: int | None = None
    l1_patience: int | None = None
    l2_patience: int | None = None
    l3_patience: int | None = None
    pobb_epsilon: float | None = None
    per_round_resubset: bool | None = None
    schema_field_rename: bool | None = None


class CycleSeed(BaseModel):
    """The chosen starting point a non-root cycle begins from — origin prompt +
    config overlay + reconciled limits. `origin_prompt_fields` is a
    `PromptTemplate.prompt_field_dict()` shape → becomes the origin `OptSearchPoint`
    at bootstrap. `pipeline_overlay` merges ON TOP of the dataset overlay
    (seed > dataset > backend default) for this cycle only — the dataset
    `pipeline.json` stays immutable. `origin_source` stamps the C0 lineage
    provenance: `fork_seed` for an operator-steered fork, `campaign_origin` for a
    fresh campaign minted from a chosen prior origin, and **empty when the cycle
    recovers its origin by replay** — an L2/L3 auto-rebase seeds a config delta,
    never an origin, so it has no C0 provenance to stamp.

    Carried by every `operator_steered` fork (the wire `OperatorForkOverride`
    command payload deserializes into this), written by the mint seam for
    campaign-from-origin, and written by an L2/L3 `fork_proposal` that carries a
    `config_overrides` unlock; sweep + diag triggers carry no seed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    origin_prompt_fields: dict[str, Any] = Field(default_factory=dict)
    pipeline_overlay: dict[str, Any] = Field(default_factory=dict)
    optimizer_narrowing: dict[str, NodeSearchNarrowing] = Field(
        default_factory=dict,
        description="Per-fork search-space lock edits (param-key subset + "
        "allowed-values) — overrides the campaign's mint-time narrowing for this "
        "cycle only, the cycle-level peer of the campaign-wide "
        "`CampaignConfig.optimizer_narrowing`. Empty for an unedited fork or a "
        "campaign-from-origin seed.",
    )
    config_overrides: ConfigOverrides = Field(default_factory=ConfigOverrides)
    origin_source: str = Field(
        default="",
        description=(
            "C0 lineage provenance — 'fork_seed' | 'campaign_origin'; empty when the "
            "seed carries no origin (an L2/L3 rebase replays its own)."
        ),
    )

    @model_validator(mode="after")
    def _origin_needs_provenance(self) -> CycleSeed:
        """A seeded origin MUST name where it came from. `resolve_origin_opt_search_point`
        looks `origin_source` up in `_SEED_ORIGIN_LINEAGE` the moment `origin_prompt_fields`
        is non-empty — an unstamped origin would `KeyError` there, deep inside bootstrap.
        Fail here instead, at the boundary that built the seed."""
        if self.origin_prompt_fields and not self.origin_source:
            raise ValueError("origin_prompt_fields set without an origin_source stamp")
        return self


class CycleSeedRecord(BaseModel):
    """The cycle's read-once starting point (`CycleSeed`) as a ledger record — appended
    at mint / operator-steered fork / check-in flip, re-read at bootstrap. Folds the old
    `.overrides/seed.json` sidecar into the replayable spine: a fork inherits its parent's
    seed record *virtually* (`inherit_from`) but appends its OWN, so a scan of the cycle's
    own ledger file returns that cycle's seed — and `None` for a cycle that carries none
    (sweep / diag). Not a progress event: the SSE tail skips it (not in `ProjectionKind`)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    record_type: Literal["cycle_seed"] = "cycle_seed"
    seed: CycleSeed
    timestamp: str = Field(default_factory=utcnow_iso)


# Discriminated union by `record_type`; keep order alphabetical — hash-keyed snapshots go stale otherwise.
CycleRecord = Annotated[
    ResumeCheckpointRecord
    | CommandAckRecord
    | CommandRecord
    | CycleSeedRecord
    | ErrorRecord
    | LLMCallProgressRecord
    | LLMCallRecord
    | LLMCallStartRecord
    | PhaseRecord
    | RoundWarningRecord
    | SnapshotRecord
    | TokenUsageRecord,
    Field(discriminator="record_type"),
]


# The `issued_by` value an operator fork carries when the client sent no
# identity. One SoT for both the stamp site (`mint_operator_fork`) and the
# lineage suppression that turns it into a *no* "edited by" badge — they must
# agree on the literal or the badge silently breaks.
UNATTRIBUTED_OPERATOR = "operator"


class ForkSpec(BaseModel):
    """Why + what-changed at a fork cut → `FORK_CUT.data.fork` + `index.json::fork`.
    One typed record for every fork; `l1_layout` carries L2/L3 rebase deltas,
    `seed` carries the operator-steered origin override. The single fork-provenance
    model — no free-string `fork.trigger` twin."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    trigger: ForkTrigger
    reason: str
    issued_by: str
    from_round: int | None = None
    from_candidate_id: str | None = None
    l1_layout: dict[str, list[str]] | None = None
    seed: CycleSeed | None = None


class RebaseRequest(BaseModel):
    """In-loop rebase signal stashed by L2/L3 emission on the cycle, resolved
    post-finalize by ``runner.entry`` into a ``_mint_fork`` call + observer
    rebuild + loop re-entry on the new fork. ``trigger`` discriminates the
    audit-trail label (``L2_REBASE`` / ``L3_REBASE`` / ``OPERATOR_REWIND``).

    ``config_overrides`` is the search-policy delta the layer asked to change
    *while* rewinding. A policy change and a rewind are one move, not two: the
    parent keeps its frozen config and its comparability, and the new axis is
    searched only on the sibling. It rides the fork's ``CycleSeed``, so a later
    ``resume`` of that fork reads the same unlock back off disk."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    fork_from_round: int
    trigger: ForkTrigger
    reason: str
    issued_by: str
    config_overrides: ConfigOverrides | None = None


class OperatorSweepFile(BaseModel):
    """Operator JSON under ``datasets/{name}/sweep/``; dispatcher widens to ``ForkSpec(OPERATOR_SWEEP)``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reason: str = ""
    l1_layout: dict[str, list[str]] | None = None

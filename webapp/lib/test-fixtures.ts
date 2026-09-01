// Builders for the round document in tests. `RoundResult` / `ScoredCandidate` are
// GENERATED from the Pydantic models, so every field the server always sends is
// required here too. A test cares about two or three of them; these fill the rest with
// inert values so the fixture stays about what it is testing — without casting through
// `unknown`, which would hand back exactly the "typechecks anything" hole the generated
// types exist to close.

import type {
  CurrentRound,
  DashboardCandidate,
  DashboardSample,
  LiveDashboardState,
  RoundResult,
  RoundSummary,
  RoundSummaryCandidate,
  ScoredCandidate,
} from "@/lib/types";

// One served sample row. `status` defaults to HIT because most tests care about the id or the
// position, not the verdict; the ones that care pass it.
export function sampleRow(over: Partial<DashboardSample> = {}): DashboardSample {
  return {
    qi: 0,
    sample_id: null,
    status: "HIT",
    terminal_node: "",
    cached: false,
    time_s: null,
    predicted: "",
    ground_truth: "",
    query: "",
    input_tokens: null,
    output_tokens: null,
    ...over,
  };
}

// `current_round` is a MODEL now, not a free-form dict, so a test naming one or two of its
// keys still has to produce the whole shape. This fills the rest inert.
export function currentRound(over: Partial<CurrentRound> = {}): CurrentRound {
  return {
    round: 0,
    active_node: null,
    candidates: [],
    nodes: {},
    pobb: { current_id: "", n_samples: 0, leader_prob: 0, posterior_width: 1, top: [] },
    // Null = the round has not elected yet, which is what an inert fixture is.
    overlap: null,
    ...over,
  };
}

// A live `current_round.candidates[]` row. Same field list as `summaryCandidate` below minus
// what only closing settles — they are one model, `DashboardCandidate`, and this is the half
// that has not finished yet.
export function liveRow(over: Partial<DashboardCandidate> = {}): DashboardCandidate {
  return {
    label: "C1.1",
    candidate_id: null,
    accuracy: null,
    composite_fitness: null,
    scored_samples: 0,
    cached_samples: 0,
    expected_samples: null,
    evaluators: {},
    changes_description: "",
    partial_reason: "",
    theta: null,
    theta_se: null,
    theta_caveat: null,
    mean_fitness_ci_lo: null,
    mean_fitness_ci_hi: null,
    matched_parent_accuracy: null,
    matched_parent_composite: null,
    matched_parent_lift: null,
    matched_parent_lift_ci_lo: null,
    matched_parent_lift_ci_hi: null,
    // A live row carries the crown too — the election lands at the end of scoring, not at
    // round close — so the default is "nothing crowned yet", never "this one lost".
    is_winner: false,
    invalid: false,
    ...over,
  };
}

export function scored(over: Partial<ScoredCandidate> = {}): ScoredCandidate {
  return {
    candidate_id: "c",
    label: "C1.1",
    changes_description: "",
    accuracy: 0,
    composite_fitness: 0,
    total: 0,
    evaluators: {},
    sp_hash: "",
    pipeline_params_override: null,
    resolved_pipeline_params: null,
    prompt_fields: {},
    escalation_aborted: false,
    elimination_stopped: false,
    scored_samples: 0,
    expected_samples: 0,
    cached_samples: 0,
    partial_reason: "",
    invalid: false,
    validation_failures: [],
    runtime_failures: [],
    elimination_context: {},
    degradation_context: {},
    matched_parent_accuracy: null,
    matched_parent_composite: null,
    matched_parent_lift: null,
    matched_parent_lift_ci_lo: null,
    matched_parent_lift_ci_hi: null,
    theta: null,
    theta_se: null,
    theta_caveat: null,
    mean_fitness_ci_lo: null,
    mean_fitness_ci_hi: null,
    ...over,
  };
}

export function roundDoc(over: Partial<RoundResult> = {}): RoundResult {
  return {
    round: 0,
    // No ledger behind a fixture, so the round closed at no offset — the same `null` a
    // diagnostic replay writes, never 0, which is a real record.
    closed_at_offset: null,
    label: "C0",
    accuracy: 0,
    composite_fitness: 0,
    total: 0,
    improved: false,
    // No arms, so the round could not be read either way — never `false`, which asserts it
    // measured cleanly and told nothing apart.
    separable: null,
    electable_count: 0,
    p_value: null,
    verdict_reason: null,
    degraded_samples: 0,
    deprecated: 0,
    escalation_signal: null,
    matched_parent_accuracy: null,
    matched_parent_composite: null,
    matched_parent_lift: null,
    matched_parent_lift_ci_lo: null,
    matched_parent_lift_ci_hi: null,
    ability: null,
    prompt_fields: {},
    pipeline_params: null,
    parent_accuracy: 0,
    results: [],
    all_candidate_results: {},
    candidates_scored: 0,
    candidate_scores: [],
    evaluators: {},
    l1_yield: 1,
    l1_n_no_op: 0,
    l1_n_duplicate: 0,
    l1_n_repeat: 0,
    l1_parse_failure: null,
    diagnostics: null,
    critique: null,
    health: null,
    opt_sp: null,
    axis_memory_peaked: [],
    optimizer_prompt_hashes: {},
    status: "",
    overlap: null,
    overlap_results: [],
    round_id: "round_0",
    scoreboard: [],
    ...over,
  };
}

// `dashboard.json` is `LiveDashboardState` — same story as the round document: every
// field the server always sends is required, so a fixture fills the inert ones here.
export function dash(over: Partial<LiveDashboardState> = {}): LiveDashboardState {
  return {
    campaign_id: "ds__000000",
    cycle_id: "cycle_0",
    session_id: "sess_0",
    // A fixture folds no ledger, so it stands at the same pre-first-record offset the
    // projection starts from.
    at_offset: -1,
    langfuse_trace_url: null,
    state: "init",
    state_since: "",
    // Both halves: `declared_phase` is what the runner wrote to disk, `run_phase` what the
    // route derived and served. A fixture that set only one would type-check while
    // describing a body the server never sends.
    declared_phase: "running",
    run_phase: "running",
    stop_reason: null,
    round: 0,
    candidate: "",
    query: "",
    patience: "",
    hearts: null,
    rounds: [],
    best: 0,
    current_acc: 0,
    ability_delta: null,
    composite_fitness_formula: null,
    composite_fitness_weights: null,
    headline_metric: "accuracy",
    degraded_count: 0,
    error_count: 0,
    backend_retry_count: 0,
    recent_backend_warnings: [],
    recent_loop_warnings: [],
    total_queries_scored: 0,
    total_backend_calls: 0,
    current_query_payload: null,
    current_sample_id: null,
    open_sample_ids: [],
    declared_sample_order: [],
    sample_lookahead: 1,
    sample_lookahead_discards: 0,
    sample_lookahead_armed: 1,
    max_cells_in_flight: 1,
    concurrency_arming: "round",
    measured_unit: "sample",
    last_query_elapsed_s: 0,
    wallclock_serialized_at: null,
    n_variants: 0,
    sp_budget_ttest: 0,
    run_limits: null,
    spend: {
      backend: { used_usd: 0, input_tokens: 0, output_tokens: 0, reasoning_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0, rate_known: false, model: null, unpriced_tokens: 0, incurred_usd: 0, incurred_unpriced_tokens: 0 },
      loop: { used_usd: 0, input_tokens: 0, output_tokens: 0, reasoning_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0, rate_known: false, model: null, unpriced_tokens: 0, incurred_usd: 0, incurred_unpriced_tokens: 0 },
      total_used_usd: 0,
      total_incurred_usd: 0,
      total_tokens_used: 0,
      unpriced_tokens: 0,
    },
    in_flight: null,
    backfill_log: [],
    current_round: currentRound(),
    error: null,
    ...over,
  };
}

// The `dash.rounds[]` display row and its candidate — the summary shapes, distinct
// from the `round_NNNN.json` document above. Same rule: every field the server
// always sends is filled inert here so a test names only what it is about.
// What the server stamps on a candidate row (`domain/results.py::candidate_label`) — round 0
// is `C0` for EVERY arm, and only from round 1 does the position get a suffix. Spelled here
// because a fixture stands in for a RESPONSE; production code reads this off the row. Left
// unset, every builder below returns the same default label whatever round it is placed in,
// so an assertion naming the position's label was only ever testing a client re-derivation.
export const servedLabel = (round: number, idx: number) =>
  round === 0 ? "C0" : `C${round}.${idx + 1}`;

export function summaryCandidate(
  over: Partial<RoundSummaryCandidate> = {},
): RoundSummaryCandidate {
  return {
    candidate_id: "c",
    label: "C1.1",
    accuracy: 0,
    composite_fitness: 0,
    scored_samples: 0,
    expected_samples: 0,
    cached_samples: 0,
    is_winner: false,
    invalid: false,
    evaluators: {},
    changes_description: "",
    partial_reason: "",
    theta: null,
    theta_se: null,
    theta_caveat: null,
    mean_fitness_ci_lo: null,
    mean_fitness_ci_hi: null,
    matched_parent_accuracy: null,
    matched_parent_composite: null,
    matched_parent_lift: null,
    matched_parent_lift_ci_lo: null,
    matched_parent_lift_ci_hi: null,
    ...over,
  };
}

export function summaryRound(over: Partial<RoundSummary> = {}): RoundSummary {
  return {
    round: 0,
    accuracy: 0,
    composite_fitness: 0,
    ability: null,
    improved: null,
    electable_count: null,
    verdict_reason: null,
    candidates: [],
    selection: [],
    health: null,
    overlap: null,
    panel_precision: null,
    ...over,
  };
}

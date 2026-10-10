import type {
  ArmElection,
  ArmNode,
  ArmPanel,
  ArmPointer,
  ArmReading,
  CourseNode,
  CurrentRound,
  DashboardCandidate,
  DashboardSample,
  DegradationHealth,
  LiveCandidate,
  OverlapReading,
  PairedReading,
  ProducerReading,
  RoundResult,
  RunStanding,
  ScoredCandidate,
  ServedDashboard,
  ServedRound,
} from "@/lib/api/types";

export function pairedReading(
  lift: number,
  [lo, hi]: [number, number],
  over: { rateA?: number; coverage?: NonNullable<PairedReading["coverage"]>["state"] } = {},
): PairedReading {
  const rateA = over.rateA ?? 0.5;
  const measured = (key: string, unit: "rate" | "score"): PairedReading["beside"][number] => ({
    measurand: { kind: "grade", key, scorer_id: "s", binary: false, unit },
    rate_a: rateA,
    rate_b: rateA + lift,
    estimate: {
      value: lift,
      ci_lo: lo,
      ci_hi: hi,
      side: lo > 0 ? "above" : hi < 0 ? "below" : "spans",
      p_value: 0.5,
      p_floor: 0.5,
    },
    flips: null,
    family: null,
  });
  const member = (id: string): NonNullable<PairedReading["a"]> => ({
    address: { path: [], individual_id: id, arm: null, pass_role: null },
    n: 10,
    bought: 0,
  });
  return {
    state: "read",
    a: member("parent"),
    b: member("arm"),
    cell_set: { name: "reference_cells", basis: "declared", id: "set", dataset_hash: null, size: 10 },
    instrument_id: "i",
    scope: "decision",
    spec: { interval_method: "student_t", alpha: 0.05, null_value: 0 },
    coverage: {
      state: over.coverage ?? "complete",
      shared: 10,
      scored: 10,
      excluded_unscored: 0,
      excluded_faulted: 0,
    },
    headline: measured("fitness", "rate"),
    beside: [measured("objective", "score")],
  };
}

export function unreadOverlap(over: Partial<OverlapReading> = {}): OverlapReading {
  return {
    sample_ids: [],
    lead: {
      state: "not_held",
      a: null,
      b: null,
      cell_set: null,
      instrument_id: null,
      scope: "report",
      spec: { interval_method: "student_t", alpha: 0.05, null_value: 0 },
      coverage: null,
      headline: null,
      beside: [],
    },
    earlier: [],
    advance: "held",
    ...over,
  };
}

export function runStanding(
  round: number,
  label: string,
  over: Partial<RunStanding> = {},
): RunStanding {
  const origin = { round: 0, label: "C0", candidate_id: "c0" };
  return {
    rounds_without_advance: 0,
    stalls_left: null,
    stalls_left_cap: null,
    lives: null,
    selection: round === 0 ? origin : { round, label, candidate_id: label },
    parent: round === 0 ? null : origin,
    vs_origin: {
      ...unreadOverlap().lead,
      state: round === 0 ? "same_individual" : "not_held",
    },
    rounds_closed: round,
    improved: 0,
    advanced: 0,
    spent: null,
    selection_line: `${round === 0 ? "C0" : label} (round ${round}) — served wording`,
    ...over,
  };
}

// Mirrors what the server derives from `state` (`ProducerReading.of`).
export function producerReading(
  state: ProducerReading["state"],
  served: Partial<Omit<ProducerReading, "state">> = {},
): ProducerReading {
  return {
    state,
    label: state === "wedged" ? "Wedged" : state === "claimed" ? "Starting" : "Running",
    label_on_child: "Waiting",
    stepping: state === "live",
    stalled: state === "wedged" ? "no progress recorded" : "",
    alert:
      state === "wedged"
        ? { title: "Run wedged", detail: "heartbeats only" }
        : state === "silent"
          ? {
              title: "Run went silent",
              detail: "its producer is gone — Start relaunches from the last closed round",
            }
          : null,
    attached: state !== "silent" && state !== "absent",
    appending: state === "live" || state === "idle" || state === "wedged",
    silent_for_s: null,
    open_for_s: null,
    call_holds: false,
    ...served,
  };
}

export function health(
  grade: DegradationHealth["grade"],
  over: Partial<DegradationHealth> = {},
): DegradationHealth {
  return {
    grade,
    cause: grade === "healthy" ? null : "degraded",
    samples: 20,
    structural_count: 0,
    transient_count: 5,
    no_result_count: 0,
    hole_count: 0,
    not_attempted: 0,
    unscored: 0,
    last_error: null,
    answer_modal_share: null,
    degraded_rate: 0.25,
    consecutive_degraded_rounds: 1,
    prior_clean_rounds: 5,
    dominant_node: "web_search",
    node_failure_rates: {},
    node_warnings: {},
    suggested_action: null,
    ...over,
  };
}

export function armReading(
  over: Partial<Omit<ArmReading, "arm" | "panel" | "election">> & {
    arm?: Partial<ArmPointer>;
    panel?: Partial<ArmPanel>;
    election?: Partial<ArmElection>;
  } = {},
): ArmReading {
  const { arm, panel, election, ...rest } = over;
  return {
    sp_hash: "",
    changes_description: "",
    outcome: null,
    own: null,
    spend: null,
    ability: null,
    vs_reference: null,
    bench: null,
    verify: null,
    on_origin_panel: null,
    ...rest,
    arm: { round: 1, label: "C1.1", candidate_id: "", ...arm },
    panel: { scored: null, expected: null, cached: null, cached_share: null, cut: false, ...panel },
    election: { held: false, selected: false, leading: false, crown: null, ...election },
  };
}

export function ownLevel(accuracy: number, n = 0): NonNullable<ArmReading["own"]> {
  const banded = { value: accuracy, ci_lo: null, ci_hi: null };
  return { accuracy: banded, composite: banded, n };
}

let mintedRows = 0;

export function armNode(over: Partial<ArmNode> & Pick<ArmNode, "id">, round = 0): ArmNode {
  mintedRows += 1;
  return {
    kind: "candidate",
    row: mintedRows,
    label: over.id,
    parent_ids: [],
    variations: [],
    path: [],
    children: [],
    origin_row: null,
    answers_for_id: true,
    elects_on: "ability",
    reading: armReading({
      arm: { round, label: over.label ?? over.id, candidate_id: over.id },
      election: { held: true },
    }),
    superseded_by: null,
    fork: null,
    verdict: "awaiting",
    stands: false,
    course_winner: false,
    course_latest: false,
    main_line: [],
    lens_value: null,
    lens_rank_move: null,
    sample_set_accuracy: null,
    sample_set_n: null,
    divergence: null,
    divergent: false,
    ...over,
  };
}

export function courseNode(over: Partial<CourseNode> & Pick<CourseNode, "id">): CourseNode {
  return {
    kind: "course",
    label: over.id,
    parent_ids: [],
    path: [],
    children: [],
    origin_row: null,
    elects_on: "ability",
    course_kind: "root",
    run_phase: "terminal",
    status: { label: "—", mark: "unknown" },
    dataset_name: "",
    trigger: "",
    fork_direction: null,
    steered_by: null,
    task: null,
    run_standing: null,
    lens_criterion: null,
    lens_shift: null,
    ...over,
  };
}

export function sampleRow(over: Partial<DashboardSample> = {}): DashboardSample {
  return {
    qi: 0,
    sample_id: null,
    status: "HIT",
    fitness: null,
    terminal_node: null,
    cached: false,
    time_s: null,
    cost_s: null,
    predicted: "",
    ground_truth: "",
    ground_truth_text: "verifier-graded — no label",
    query: "",
    input_tokens: null,
    output_tokens: null,
    cache_read_tokens: null,
    ...over,
  };
}

export function currentRound(over: Partial<CurrentRound> = {}): CurrentRound {
  return {
    round: 0,
    active_node: null,
    measurement_node: null,
    candidates: [],
    nodes: {},
    racing: null,
    ...over,
  };
}

export function liveRow(over: Partial<LiveCandidate> = {}): LiveCandidate {
  return {
    reading: armReading(),
    prompt_fields: null,
    resolved_pipeline_params: null,
    pipeline_overlay: null,
    samples: [],
    validation_failures: [],
    composite_fitness_formula_short: null,
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
    deprecated: 0,
    total: 0,
    evaluators: {},
    sp_hash: "",
    pipeline_overlay: null,
    resolved_pipeline_params: null,
    prompt_fields: {},
    outcome: "measured",
    scored_samples: 0,
    expected_samples: 0,
    cached_samples: 0,
    input_tokens: null,
    output_tokens: null,
    cache_read_tokens: null,
    validation_failures: [],
    runtime_failures: [],
    elimination_context: {},
    elimination_reason: null,
    degradation_context: {},
    vs_reference: null,
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
    // `null` as a diagnostic replay writes, never 0, which is a real record.
    at_offset: null,
    label: "C0",
    leading_label: null,
    accuracy: 0,
    composite_fitness: 0,
    recall_at: {},
    total: 0,
    improved: false,
    elects_on: "ability",
    electable_count: 0,
    reference_rule: null,
    verdict_reason: null,
    degraded_samples: 0,
    not_attempted: 0,
    unscored: 0,
    deprecated: 0,
    ability: null,
    prompt_fields: {},
    pipeline_params: null,
    results: [],
    all_candidate_results: {},
    reference_results: {},
    candidates_scored: 0,
    candidate_scores: [],
    selected_labels: [],
    evaluators: {},
    diagnostics: null,
    health: null,
    opt_sp: null,
    optimizer_state: {
      manifest: "potter",
      population: [],
      prompt_hashes: {},
      payload: {
        memory: {
          wounds: {
            l3_note: "",
            validation_failures: [],
            runtime_failures: [],
            l2_guard_breaches: [],
            l3_guard_breaches: [],
          },
          l1_layout: { persona: [], task_intent: [], thinking_style: [], problem_description: [] },
          l1_overrides: {},
          plan: "",
        },
        critique: null,
        l1_yield: 1,
        l1_parse_failure: null,
        axis_memory_peaked: [],
      },
    },
    optimizer_facts: [],
    generation_only: false,
    overlap: unreadOverlap({ advance: "origin" }),
    overlap_results: {},
    round_id: "round_0",
    scoreboard: [],
    ...over,
  };
}

export function dash(over: Partial<ServedDashboard> = {}): ServedDashboard {
  return {
    campaign_id: "ds__000000",
    cycle_id: "cycle_0",
    // The pre-first-record offset the projection starts from.
    at_offset: -1,
    langfuse_trace_url: null,
    state: "init",
    optimizer_step: null,
    state_since: "",
    run_phase: "running",
    status: { label: "Running", mark: "running" },
    next_step: "",
    stop_reason: null,
    round: 0,
    candidate: "",
    run_standing: null,
    rounds: [],
    overlap_line_round: null,
    overlap_line_n: null,
    bench_score: null,
    bench_pass: null,
    verify_pass: null,
    composite_fitness_formula: null,
    composite_fitness_weights: null,
    composite_fitness_anchors: null,
    display_metric: "accuracy",
    elects_on: "ability",
    degraded_count: 0,
    error_count: 0,
    backend_retry_count: 0,
    recent_backend_warnings: [],
    recent_loop_warnings: [],
    total_queries_scored: 0,
    total_backend_calls: 0,
    current_query_payload: null,
    open_sample_ids: [],
    sample_lookahead: 1,
    sample_lookahead_auto: false,
    sample_lookahead_discards: 0,
    in_flight: 0,
    lookahead_allowed: 0,
    lookahead_most: 0,
    cell_reserve_usd: null,
    lookahead_money_hold: null,
    lookahead_pick_max: 1,
    lookahead_unavailable: "",
    lookahead_explained: "",
    waiting_on: null,
    producer: producerReading("absent"),
    run_admission: {
      offers: "pause",
      refusal: "",
      skip_refusal: "",
      lookahead_refusal: "",
    },
    backpressure: null,
    max_cells_in_flight: 1,
    measured_unit: "sample",
    last_query_elapsed_s: 0,
    wallclock_serialized_at: null,
    arms_per_round: 0,
    sp_budget_round: 0,
    run_limits: { max_rounds: null, ceiling: { usd: null, tokens: null }, optimizer: [] },
    spend_metered: null,
    fork_remainder: null,
    spend_metered_by_round: null,
    catch_up_log: [],
    current_round: currentRound(),
    error: null,
    observed: null,
    walk: null,
    round_axis: { completed: [], live: null, position: null },
    pause: null,
    ...over,
  };
}

// Mirrors `domain/results.py::candidate_label`; fixtures only — production reads the served label.
export const servedLabel = (round: number, idx: number) =>
  round === 0 ? "C0" : `C${round}.${idx + 1}`;

export function summaryCandidate(over: Partial<DashboardCandidate> = {}): DashboardCandidate {
  return {
    reading: armReading({
      arm: { candidate_id: "c" },
      outcome: "measured",
      own: ownLevel(0),
      panel: { scored: 0, expected: 0, cached: 0 },
      election: { held: true },
    }),
    ...over,
  };
}

export function summaryRound(over: Partial<ServedRound> = {}): ServedRound {
  return {
    round: 0,
    leading: null,
    selected: [],
    accuracy: 0,
    composite_fitness: 0,
    total: 0,
    ability: null,
    ability_on_series_ruler: false,
    bench: null,
    improved: null,
    verdict_reason: null,
    candidates: [],
    selection: [],
    health: null,
    health_alert: null,
    overlap: unreadOverlap(),
    overlap_line: [],
    panel_precision: null,
    panel_precision_verdict: null,
    selection_movement: [],
    optimizer_facts: [],
    ...over,
  };
}

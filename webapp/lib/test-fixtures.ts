import type {
  AccountKey,
  CouponStatus,
  LifetimeSpend,
  CourseKind,
  ArmElection,
  ArmName,
  ArmPanel,
  ArmReading,
  CandidateFacts,
  CurrentRound,
  DashboardCandidate,
  SubtreeCampaign,
  HierarchyCandidate,
  SubtreeCourse,
  SubtreeCourseFacts,
  HierarchyRound,
  HierarchyState,
  HierarchyStep,
  HierarchyStat,
  BenchStat,
  HierarchyTag,
  LiveCandidate,
  Measured,
  NodePrompt,
  OverlapReading,
  PairedReading,
  ProducerReading,
  RoundHealth,
  RoundView,
  RunStanding,
  ServedDashboard,
  ServedRound,
  StepFacts,
} from "@/lib/api/types";

// The envelope every rendered level wears, empty; each builder lays its level's own fields over it.
function envelope(): {
  lede: null;
  caveat: null;
  tags: HierarchyTag[];
  stats: (HierarchyStat | BenchStat)[];
  children: never[];
} {
  return { lede: null, caveat: null, tags: [], stats: [], children: [] };
}

export function lifetimeSpend(over: Partial<LifetimeSpend> = {}): LifetimeSpend {
  return {
    billed_usd: 0,
    rate_priced_usd: 0,
    calls_rate_priced: false,
    bill_is_floor: false,
    unpriced_tokens: 0,
    sends_unreported: false,
    unreported_usd: 0,
    ...over,
  };
}

export function accountKey(provider: string, last4: string): AccountKey {
  return { provider, last4, added_at: "2026-01-01T00:00:00Z" };
}

// A fresh non-host account on a $5 face, nothing spent, no key held.
export function couponStatus(over: Partial<CouponStatus> = {}): CouponStatus {
  return {
    host: false,
    face_usd: 5,
    face_tokens: null,
    left_usd: 5,
    left_tokens: null,
    used_share: 0,
    exhausted: false,
    void: false,
    host_key_spend: lifetimeSpend(),
    own_key_spend: lifetimeSpend(),
    keys: [],
    providers: ["anthropic", "openai"],
    ...over,
  };
}

// A step as served: its address is the server's, minted from its course and offset.
export function hierarchyStep(parent: string, facts: StepFacts): HierarchyStep {
  return {
    address: `${parent}/@${facts.offset}`,
    parent,
    level: "step",
    facts,
  };
}

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
    spec: { alpha: 0.05, null_value: 0 },
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
    positions: [],
    lead: {
      state: "not_held",
      a: null,
      b: null,
      cell_set: null,
      instrument_id: null,
      scope: "report",
      spec: { alpha: 0.05, null_value: 0 },
      coverage: null,
      headline: null,
      beside: [],
    },
    earlier: [],
    triggered: [],
    vs_parent: null,
    advance: "held",
    ...over,
  };
}

export function runStanding(
  round: number,
  label: string,
  over: Partial<RunStanding> = {},
): RunStanding {
  const origin = { round: 0, label: "C0", key: "c0" };
  return {
    rounds_without_advance: 0,
    lives: null,
    selection: round === 0 ? origin : { round, label, key: label },
    parent: round === 0 ? null : origin,
    changes: "",
    vs_origin: {
      ...unreadOverlap().lead,
      state: round === 0 ? "same_individual" : "not_held",
    },
    rounds_closed: round,
    improved: 0,
    advanced: 0,
    ...over,
  };
}

// No producer, every reading off: a test sets the served fields its case reads, never derives them.
export function producerReading(over: Partial<ProducerReading> = {}): ProducerReading {
  return {
    state: "absent",
    label: "",
    label_on_child: "",
    stepping: false,
    stalled: "",
    alert: null,
    attached: false,
    appending: false,
    silent_for_s: null,
    open_for_s: null,
    call_holds: false,
    ...over,
  };
}

export function health(
  grade: RoundHealth["grade"],
  over: Partial<RoundHealth> = {},
): RoundHealth {
  return { grade, suggested_action: null, ...over };
}

export function armReading(
  over: Partial<Omit<ArmReading, "arm" | "panel" | "election">> & {
    arm?: Partial<ArmName>;
    panel?: Partial<ArmPanel>;
    election?: Partial<ArmElection>;
  } = {},
): ArmReading {
  const { arm, panel, election, ...rest } = over;
  return {
    role: "panel",
    sp_hash: "",
    changes_description: "",
    state: "measuring",
    end: null,
    walked_short: false,
    own: null,
    tokens: null,
    prefix: null,
    ability: null,
    vs_reference: null,
    bench: null,
    verify: null,
    on_origin_panel: null,
    ...rest,
    arm: { round: 1, label: "C1.1", key: "", ...arm },
    panel: { scored: null, expected: null, cached: null, cached_share: null, cut: false, ...panel },
    election: { held: false, selected: false, leading: false, crown: null, ...election },
  };
}

export function ownLevel(accuracy: number, n = 0): NonNullable<ArmReading["own"]> {
  const banded = { value: accuracy, ci_lo: null, ci_hi: null };
  return { accuracy: banded, composite: banded, n };
}

// A candidate as the subtree read serves it, at `{course}/r{round}/{key}`; its label is its arm's.
export function hierarchyCandidate(
  course: string,
  {
    id,
    round = 0,
    key = id,
    label = id,
    ...facts
  }: Partial<CandidateFacts> & { id: string; round?: number; key?: string; label?: string },
  over: Partial<HierarchyCandidate> = {},
): HierarchyCandidate {
  const reading =
    facts.reading ?? armReading({ arm: { round, label, key }, election: { held: true } });
  const { arm } = reading;
  return {
    ...envelope(),
    address: `${course}/r${arm.round}/${arm.key}`,
    parent: `${course}/r${arm.round}`,
    label: arm.label,
    level: "candidate",
    ...over,
    facts: {
      individual: id,
      parent_ids: [],
      variations: [],
      stands: false,
      course_latest: false,
      answers_for_id: true,
      main_line: [],
      descends_from: null,
      donor: null,
      donated_loci: [],
      gene_sources: [],
      lens_value: null,
      lens_rank_move: null,
      sample_set_accuracy: null,
      sample_set_n: null,
      divergence: null,
      divergent: false,
      ...facts,
      reading,
    },
  };
}

// A course's own ledger: its candidates grouped into rounds, rounds ascending, slot order kept.
export function roundsOf(
  course: string,
  candidates: readonly HierarchyCandidate[],
): HierarchyRound[] {
  const byRound = new Map<number, HierarchyCandidate[]>();
  for (const c of candidates) {
    const r = c.facts.reading.arm.round;
    byRound.set(r, [...(byRound.get(r) ?? []), c]);
  }
  return [...byRound]
    .sort(([x], [y]) => x - y)
    .map(([round, children]) => ({
      address: `${course}/r${round}`,
      parent: course,
      children,
      level: "round",
      facts: { round, elects_on: "ability" },
    }));
}

export function supersededTag(branch: string): HierarchyTag {
  return { kind: "superseded", value: branch, label: "retired" };
}


export function hierarchyState(over: Partial<HierarchyState> = {}): HierarchyState {
  return {
    run_phase: "terminal",
    status: { label: "—", mark: "unknown" },
    attached: false,
    pause: null,
    admission: { offers: null, refusal: "", skip_refusal: "", lookahead_refusal: "" },
    next_step: "",
    intervened: false,
    ...over,
  };
}

export function courseTags(mint: CourseKind, dataset?: string): HierarchyTag[] {
  return [
    ...(dataset === undefined ? [] : [{ kind: "dataset" as const, value: dataset, label: dataset }]),
    { kind: "mint", value: mint, label: mint },
  ];
}

export function subtreeCourse(
  over: Partial<SubtreeCourse> & Pick<SubtreeCourse, "address">,
  facts: Partial<SubtreeCourseFacts> = {},
): SubtreeCourse {
  return {
    ...envelope(),
    parent: null,
    label: over.address,
    tags: courseTags(facts.mint ?? "root"),
    state: hierarchyState(),
    span: { opened_at: "", last_at: "" },
    level: "course",
    children: [],
    facts: {
      mint: "root",
      spawn: null,
      task: null,
      standing: null,
      rounds_closed: 0,
      elects_on: "ability",
      lens_criterion: null,
      lens_shift: null,
      origin: null,
      from_candidate: null,
      ...facts,
    },
    ...over,
  };
}

export function subtreeCampaign(
  over: Partial<SubtreeCampaign> & Pick<SubtreeCampaign, "address">,
): SubtreeCampaign {
  const root = `${over.address}::cycle_root`;
  return {
    ...envelope(),
    parent: null,
    label: over.address,
    state: hierarchyState(),
    span: { opened_at: "", last_at: "" },
    level: "campaign",
    children: [],
    facts: {
      label: null,
      root,
      line: root,
      lifecycle: "active",
      spend_lifetime: lifetimeSpend(),
      runs_with: null,
      deletable_stubs: 0,
    },
    ...over,
  };
}

const NO_FITNESS_BANDS = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0];

export function currentRound(over: Partial<CurrentRound> = {}): CurrentRound {
  return {
    round: 0,
    active_node: null,
    measurement_node: null,
    candidates: [],
    fitness_bands: NO_FITNESS_BANDS,
    nodes: {},
    racing: null,
    ...over,
  };
}

export function liveRow(over: Partial<LiveCandidate> = {}): LiveCandidate {
  return {
    reading: armReading(),
    prompts: null,
    resolved_params: null,
    pipeline_overlay: null,
    samples: [],
    validation_failures: [],
    composite_fitness_formula_short: null,
    ...over,
  };
}

export function measuredArm({
  prompts = {},
  ...over
}: Partial<Measured> & { prompts?: Record<string, NodePrompt> } = {}): Measured {
  return {
    role: "panel",
    slot: null,
    individual: {
      prompts,
      pipeline_params: {},
      lineage: { parent_ids: [], changes_description: "", variations: [], evidence_grounding: null },
    },
    overlay: {},
    validation_failures: [],
    sp_hash: "",
    resolved_params: {},
    expected_samples: 0,
    state: "measured",
    locked_in: null,
    ...over,
  };
}

export function roundDoc(over: Partial<RoundView> = {}): RoundView {
  return {
    round: 0,
    arms: {},
    election: null,
    fitness_bands: NO_FITNESS_BANDS,
    scoreboard: [],
    headline: null,
    ...over,
  };
}

// The scoreboard names each arm, in the order given, as the wire keys `arms` by it.
export function roundOfArms(
  round: number,
  arms: { key: string; label: string; arm: Measured }[],
): RoundView {
  return roundDoc({
    round,
    arms: Object.fromEntries(arms.map(({ key, arm }) => [key, arm])),
    scoreboard: arms.map(({ key, label }, i) => ({
      rank: i + 1,
      reading: armReading({ arm: { round, label, key }, state: "measured" }),
    })),
  });
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
    stop_reason: null,
    round: 0,
    measuring_arm: null,
    rounds: [],
    overlap_line_round: null,
    overlap_line_n: null,
    verification: [],
    composite_fitness_formula: null,
    composite_fitness_weights: null,
    composite_fitness_anchors: null,
    display_metric: "accuracy",
    degraded_count: 0,
    error_count: 0,
    backend_retry_count: 0,
    recent_backend_warnings: [],
    recent_loop_warnings: [],
    total_queries_scored: 0,
    total_backend_calls: 0,
    current_query_payload: null,
    open_positions: [],
    lookahead: {
      depth: 1,
      auto: false,
      ceiling: 1,
      in_flight: 0,
      allowed: 0,
      most: 0,
      scoring_now: false,
      pick_max: 1,
      money_hold: null,
      cell_reserve_usd: null,
      unavailable: "",
      explained: "",
      auto_refusal: "",
      depths: [{ cells: 1, label: "One at a time", fill: null, pickable: true }],
    },
    waiting_on: null,
    producer: producerReading(),
    admission: {
      offers: "pause",
      refusal: "",
      skip_refusal: "",
      lookahead_refusal: "",
    },
    backpressure: null,
    measured_unit: "sample",
    last_query_elapsed_s: 0,
    at_time: "",
    round_cells_ceiling: 0,
    run_limits: { budget: {}, optimizer: [] },
    defaults_drift: null,
    spend_metered: null,
    fork_remainder: null,
    spend_metered_by_round: null,
    campaign_metered_usd: null,
    campaign_metered_tokens: null,
    allotment: null,
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

// Mirrors `domain/arms.py::candidate_label`; fixtures only — production reads the served label.
export const servedLabel = (round: number, idx: number) =>
  round === 0 ? "C0" : `C${round}.${idx + 1}`;

export function summaryCandidate(over: Partial<DashboardCandidate> = {}): DashboardCandidate {
  return {
    reading: armReading({
      arm: { key: "c" },
      state: "measured",
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
    selected: null,
    accuracy: 0,
    composite_fitness: 0,
    total: 0,
    bench: null,
    improved: null,
    verdict_reason: null,
    candidates: [],
    selection: [],
    health: health("healthy"),
    health_alert: null,
    overlap: unreadOverlap(),
    overlap_line: [],
    panel_precision: null,
    panel_precision_verdict: null,
    selection_movement: [],
    level: null,
    series_reading: null,
    optimizer_facts: [],
    ...over,
  };
}

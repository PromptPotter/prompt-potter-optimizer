import type {
  Estimate,
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
  HierarchyTag,
  LiveCandidate,
  MeteredSpend,
  NodeConfigParam,
  OverlapReading,
  PairedReading,
  ProducerReading,
  ProviderRoute,
  RoundHealth,
  RoundView,
  ServedArm,
  RunStanding,
  ServedDashboard,
  ServedRound,
  StepFacts,
  Figure,
  Said,
  VerificationMark,
} from "@/lib/api/types";

// The envelope every rendered level wears, empty; each builder lays its level's own fields over it.
function envelope(): { tags: HierarchyTag[]; figures: Figure[]; lines: Said[]; children: never[] } {
  return { tags: [], figures: [], lines: [], children: [] };
}

const NO_METER: MeteredSpend = {
  meter: "bill",
  metered_usd: 0,
  metered_tokens: 0,
  billed_usd: 0,
  rate_priced_usd: 0,
  calls_rate_priced: false,
  rate_known: true,
  bill_is_floor: false,
  incurred_usd: 0,
  billed_tokens: 0,
  unpriced_tokens: 0,
  kinds: {},
  replay_share: null,
};

export function configParam(over: Partial<NodeConfigParam> & { key: string }): NodeConfigParam {
  return {
    role: "",
    value: null,
    declared: null,
    kind: "number",
    integer: false,
    nullable: false,
    minimum: null,
    exclusive_minimum: null,
    maximum: null,
    exclusive_maximum: null,
    options: [],
    description: "",
    summary: "",
    never_axis: "",
    movable_by: [],
    held: false,
    source: "campaign",
    permitted: [],
    permitted_stated: false,
    pickable: "any",
    ...over,
  };
}

export function lifetimeSpend(over: Partial<LifetimeSpend> = {}): LifetimeSpend {
  return {
    billed_usd: 0,
    rate_priced_usd: 0,
    calls_rate_priced: false,
    bill_is_floor: false,
    unpriced_tokens: 0,
    billed_tokens: 0,
    sends_unreported: false,
    unreported_usd: 0,
    ...over,
  };
}

export function accountKey(provider: string, last4: string): AccountKey {
  return { provider, last4, added_at: "2026-01-01T00:00:00Z" };
}

// A fresh non-host account on a $5 face, nothing drawn, no key held.
// Paid by the account where it holds a key, else by the host.
export function providerRoute(provider: string, key: AccountKey | null = null): ProviderRoute {
  return { provider, payer: key === null ? "host" : "user", key };
}

export function couponStatus(over: Partial<CouponStatus> = {}): CouponStatus {
  return {
    headline: {
      key: "allowance:open",
      tone: "quiet",
      label: "Host allowance",
      value: "$0.00 of $5.00",
      sub: null,
      detail: null,
    },
    used_share: 0,
    host_key_spend: lifetimeSpend(),
    own_key_spend: lifetimeSpend(),
    routes: [providerRoute("anthropic"), providerRoute("openai")],
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

// An `Estimate` as served: its words are the server's, so a fixture spells plain ones.
export function estimate(
  value: number,
  [lo, hi]: [number | null, number | null] = [null, null],
  over: Partial<Estimate> = {},
): Estimate {
  const band = lo === null || hi === null ? null : `[${lo}, ${hi}]`;
  const point = String(value);
  return {
    value,
    ci_lo: lo,
    ci_hi: hi,
    n: null,
    se: null,
    unit: "rate",
    form: "level",
    point,
    band,
    text: band === null ? point : `${point} ${band}`,
    ...over,
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
    estimate: estimate(lift, [lo, hi], { unit, form: "lift" }),
    side: lo > 0 ? "above" : hi < 0 ? "below" : "spans",
    p_value: 0.5,
    p_floor: 0.5,
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
    spec: { alpha: 0.05 },
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
      spec: { alpha: 0.05 },
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
    vs_parent: null,
    rounds_closed: round,
    improved: 0,
    advanced: 0,
    candidates: 0,
    search_usd: null,
    worked_s: null,
    ...over,
  };
}

// No producer, every reading off: a test sets the served fields its case reads, never derives them.
export function producerReading(over: Partial<ProducerReading> = {}): ProducerReading {
  return {
    state: "absent",
    attached: false,
    appending: false,
    silent_for_s: null,
    ...over,
  };
}

// A verify pass on C1.1 that has sent its rows and scored none yet.
export function verificationMark(over: Partial<VerificationMark> = {}): VerificationMark {
  return {
    kind: "verify",
    arm: { key: "a1", round: 1, label: "C1.1" },
    state: "running",
    rows: 8,
    scored: 0,
    level: null,
    failed_as: null,
    ...over,
  };
}

export function health(
  grade: RoundHealth["grade"],
  reading: RoundHealth["reading"] = null,
): RoundHealth {
  return { grade, reading, floor: null };
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
    refused_parameter: null,
    own: null,
    tokens: null,
    prefix: null,
    ability: null,
    vs_reference: null,
    vs_reference_name: null,
    bench: null,
    verify: null,
    on_origin_panel: null,
    fitness_terms: {},
    ...rest,
    arm: { round: 1, label: "C1.1", key: "", ...arm },
    panel: { scored: null, expected: null, cached: null, cached_share: null, cut: false, ...panel },
    election: { held: false, selected: false, leading: false, crown: null, ...election },
  };
}

export function ownLevel(fitness: number, n = 0): NonNullable<ArmReading["own"]> {
  return { fitness: estimate(fitness), composite: estimate(fitness, [null, null], { unit: "score" }), n };

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
      sample_set_fitness: null,
      sample_set_n: null,
      divergence: null,
      divergent: false,
      standing: "proposed",
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
    status: { run_phase: "terminal", stop_reason: null, mark: "unknown" },
    attached: false,
    stop: null,
    admission: {
      offers: null,
      refusal: "",
      skip_refusal: "",
      lookahead_refusal: "",
      payload: {},
      launch_refusal: "",
    },
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
      spend: NO_METER,
      bench: null,
      elects_on: "ability",
      lens_criterion: null,
      lens_shift: null,
      origin: null,
      from_candidate: null,
      ...facts,
    },
    climb: null,
    said: null,
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
      runs_with: null,
      deletable_stubs: 0,
    },
    ...over,
  };
}

export function currentRound(over: Partial<CurrentRound> = {}): CurrentRound {
  return {
    round: 0,
    active_node: null,
    measurement_node: "l1_score",
    inner: null,
    candidates: [],
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
    ...over,
  };
}

export function roundDoc(over: Partial<RoundView> = {}): RoundView {
  return {
    round: 0,
    election: null,
    candidates: [],
    headline: null,
    ...over,
  };
}

// A filed round's arms, in the order given, as the round read serves them.
export function roundOfArms(
  round: number,
  arms: ({ key: string; label: string } & Partial<Omit<ServedArm, "reading">>)[],
): RoundView {
  return roundDoc({
    round,
    candidates: arms.map(({ key, label, ...over }) => ({
      reading: armReading({ arm: { round, label, key }, state: "measured" }),
      prompts: {},
      resolved_params: {},
      validation_failures: [],
      ...over,
    })),
  });
}

export function dash(over: Partial<ServedDashboard> = {}): ServedDashboard {
  return {
    campaign_id: "ds__000000",
    cycle_id: "cycle_0",
    // The pre-first-record offset the projection starts from.
    at_offset: -1,
    state: "init",
    optimizer_step: null,
    run_phase: "running",
    stop_reason: null,
    round: 0,
    measuring_arm: null,
    rounds: [],
    sample_coverage: { measuring_rounds: 0, samples: [] },
    overlap_line_round: null,
    overlap_line_n: null,
    verification: [],
    composite_fitness_formula: null,
    composite_fitness_weights: null,
    composite_fitness_anchors: null,
    display_metric: "fitness",
    degraded_count: 0,
    error_count: 0,
    backend_retry_count: 0,
    recent_backend_warnings: [],
    total_backend_calls: 0,
    current_query_payload: null,
    lookahead: {
      depth: 1,
      auto: false,
      ceiling: 1,
      in_flight: 0,
      allowed: 0,
      most: 0,
      scoring_now: false,
      pick_max: 1,
      affordable: null,
      cell_reserve_usd: null,
      discards: 0,
      auto_refusal: "one_at_a_time",
      depths: [{ cells: 1, fill: null, pickable: true }],
    },
    held_call: null,
    producer: producerReading(),
    admission: {
      offers: "pause",
      refusal: "",
      skip_refusal: "",
      lookahead_refusal: "",
      payload: {},
      launch_refusal: "",
    },
    backpressure: null,
    measured_unit: "sample",
    round_cells_ceiling: 0,
    run_limits: { budget: {}, overshoot: false, optimizer: [] },
    defaults_drift: null,
    backend_revision: null,
    spend_metered: null,
    fork_remainder: null,
    spend_metered_by_round: null,
    metered_line: null,
    cap_lines: [],
    allotment: null,
    overshot: null,
    catch_up_log: [],
    current_round: currentRound(),
    observed: null,
    walk: null,
    round_axis: { completed: [], live: null, position: null, unfiled: null },
    stop: null,
    said: null,
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
    improved: null,
    verdict_reason: null,
    candidates: [],
    selection: [],
    health: health("healthy"),
    overlap: unreadOverlap(),
    panel_precision: null,
    panel_precision_verdict: null,
    selection_movement: [],
    series_reading: null,
    optimizer_facts: [],
    lift_prior: null,
    hold_up: null,
    ...over,
  };
}

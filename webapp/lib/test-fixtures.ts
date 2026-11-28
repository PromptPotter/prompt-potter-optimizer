import type {
  ArmedCaps,
  CapReading,
  Estimate,
  AccountKey,
  CouponStatus,
  LifetimeSpend,
  CycleMint,
  ArmElection,
  ArmName,
  ArmSamples,
  ArmReading,
  CandidateFacts,
  CurrentRound,
  DashboardCandidate,
  Filable,
  SubtreeCampaign,
  HierarchyCampaign,
  HierarchyCandidate,
  SubtreeCycle,
  SubtreeCycleEntryFacts,
  HierarchyRound,
  HierarchyState,
  HierarchyStep,
  HierarchyTag,
  LiveCandidate,
  MeteredSpend,
  NodeConfigParam,
  OverlapReading,
  ProducerReading,
  ReadPair,
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
import { COMMAND_KINDS } from "@/lib/api/types";

// The kinds an owner files (`command_kinds.py::filable` over every capability: every kind): a
// fixture's reader is the owner unless a test says it reads with none, as the exhibit's follower does.
export const OWNER_FILABLE: Filable[] = [...COMMAND_KINDS];

// The envelope every rendered level wears, empty; each builder lays its level's own fields over it.
function envelope(): { tags: HierarchyTag[]; figures: Figure[]; lines: Said[]; children: never[] } {
  return { tags: [], figures: [], lines: [], children: [] };
}

const NO_METER: MeteredSpend = {
  meter: "bill",
  metered_usd: 0,
  billed_usd: 0,
  rate_priced_usd: 0,
  incurred_usd: 0,
  billed_tokens: 0,
  kinds: {},
  replay_share: null,
  bill: "$0.0000",
  rate_priced_line: null,
  replay_line: "The search incurred nothing, so no share of it replayed",
  buckets_line: "billed + priced at our rate: ",
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
    billed_tokens: 0,
    bill: "$0.0000",
    caveats: [],
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

// A step as served: its address is the server's, minted from its cycle and offset.
export function hierarchyStep(parent: string, facts: StepFacts): HierarchyStep {
  return {
    address: `${parent}/@${facts.offset}`,
    parent,
    entry_kind: "step",
    facts,
  };
}

// An `Estimate` as served: its words are the server's, so a fixture spells plain ones.
export function estimate(
  value: number,
  [lo, hi]: [number | null, number | null] = [null, null],
  over: Partial<Estimate> = {},
): Estimate {
  return {
    value,
    ci_lo: lo,
    ci_hi: hi,
    n: null,
    se: null,
    unit: "rate",
    form: "level",
    ...over,
  };
}

export function pairedReading(
  lift: number,
  [lo, hi]: [number, number],
  over: { rateA?: number; coverage?: ReadPair["coverage"]["state"] } = {},
): ReadPair {
  const rateA = over.rateA ?? 0.5;
  const measured = (key: string, unit: "rate" | "score"): ReadPair["beside"][number] => ({
    measurand: { kind: "grade", key, scorer_id: "s", binary: false, unit },
    rate_a: rateA,
    rate_b: rateA + lift,
    estimate: estimate(lift, [lo, hi], { unit, form: "lift" }),
    side: lo > 0 ? "above" : hi < 0 ? "below" : "spans",
    p_value: 0.5,
    p_floor: 0.5,
    flips: { gained: 0, lost: 0, unchanged: 0 },
    family: null,
  });
  const member = (id: string): ReadPair["a"] => ({
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
    },
    earlier: [],
    triggered: [],
    vs_previous_pick: null,
    advance: "held",
    stall: "counts",
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
    changes: "",
    vs_origin: {
      ...unreadOverlap().lead,
      state: round === 0 ? "same_individual" : "not_held",
    },
    vs_previous_pick: null,
    rounds_closed: round,
    improved: 0,
    advanced: 0,
    candidates_tested: 0,
    search_usd: null,
    worked_s: null,
    ...over,
  };
}

// No producer, every reading off: a test sets the served fields its case reads, never derives them.
// Every cap uncapped and nothing read, as a replay serves them; `caps` replaces rows by axis.
export function armedCaps(caps: CapReading[] = []): ArmedCaps {
  const uncapped = (axis: CapReading["axis"]): CapReading => ({
    axis,
    asked: null,
    armed: null,
    used: null,
    meter: axis === "usd" || axis === "tokens" ? "bill" : null,
    count: null,
    fill: null,
    clamped_by: null,
  });
  const axes = ["usd", "tokens", "rounds", "wall_clock_s"] as const;
  return {
    caps: axes.map((axis) => caps.find((cap) => cap.axis === axis) ?? uncapped(axis)),
    said: "",
  };
}

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
  over: Partial<Omit<ArmReading, "arm" | "samples" | "election">> & {
    arm?: Partial<ArmName>;
    samples?: Partial<ArmSamples>;
    election?: Partial<ArmElection>;
  } = {},
): ArmReading {
  const { arm, samples, election, ...rest } = over;
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
    vs_reference: null,
    vs_reference_name: null,
    bench: null,
    verify: null,
    on_origin_panel: null,
    fitness_terms: {},
    ...rest,
    arm: { round: 1, label: "C1.1", key: "", ...arm },
    samples: { scored: null, expected: null, cached: null, cached_share: null, cut: false, ...samples },
    election: { held: false, selected: false, leading: false, crown: null, ...election },
  };
}

export function ownLevel(fitness: number, n = 0): NonNullable<ArmReading["own"]> {
  return { fitness: estimate(fitness), composite: estimate(fitness, [null, null], { unit: "score" }), n };

}

// A candidate as the subtree read serves it, at `{cycle}/r{round}/{key}`; its label is its arm's.
export function hierarchyCandidate(
  cycle: string,
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
    address: `${cycle}/r${arm.round}/${arm.key}`,
    parent: `${cycle}/r${arm.round}`,
    label: arm.label,
    entry_kind: "candidate",
    ...over,
    facts: {
      individual: id,
      variations: [],
      stands: false,
      cycle_latest: false,
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

// A cycle's own ledger: its candidates grouped into rounds, rounds ascending, slot order kept.
export function roundsOf(
  cycle: string,
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
      address: `${cycle}/r${round}`,
      parent: cycle,
      children,
      entry_kind: "round",
      facts: { round, elects_on: "fitness" },
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
    admission: { offers: null, refusal: null, payload: {}, stop_acts: [] },
    intervened: false,
    ...over,
  };
}

export function cycleTags(mint: CycleMint, dataset?: string): HierarchyTag[] {
  return [
    ...(dataset === undefined ? [] : [{ kind: "dataset" as const, value: dataset, label: dataset }]),
    { kind: "mint", value: mint, label: mint },
  ];
}

export function subtreeCycle(
  over: Partial<SubtreeCycle> & Pick<SubtreeCycle, "address">,
  facts: Partial<SubtreeCycleEntryFacts> = {},
): SubtreeCycle {
  return {
    ...envelope(),
    parent: null,
    label: over.address,
    tags: cycleTags(facts.mint ?? "root"),
    state: hierarchyState(),
    span: { opened_at: "", last_at: "" },
    entry_kind: "cycle",
    children: [],
    facts: {
      mint: "root",
      spawn: null,
      task: null,
      standing: null,
      rounds_closed: 0,
      spend: NO_METER,
      bench: null,
      curve: null,
      verify: null,
      elects_on: "fitness",
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

// What a listed campaign's row deletes; a subtree's campaign serves no act of its own.
export const LISTED_DELETE: HierarchyCampaign["delete"] = {
  kind: "delete-campaign",
  says: "Remove a campaign for good.",
  keep_results: null,
};

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
    entry_kind: "campaign",
    children: [],
    facts: {
      label: null,
      short_label: over.address,
      root,
      root_label: "root ·root",
      live_cycle: root,
      lifecycle: "active",
      runs_with: null,
      deletable_stubs: 0,
      filable: OWNER_FILABLE,
      candidates: 0,
    },
    ...over,
  };
}

export function currentRound(over: Partial<CurrentRound> = {}): CurrentRound {
  return {
    round: 0,
    measurement_node: "l1_score",
    inner: null,
    candidates: [],
    nodes: {},
    ...over,
  };
}

export function liveRow(over: Partial<LiveCandidate> = {}): LiveCandidate {
  return {
    reading: armReading(),
    prompts: null,
    resolved_params: null,
    pipeline_overlay: null,
    open_cells: [],
    samples: [],
    validation_failures: [],
    evidence_grounding: null,
    ...over,
  };
}

export function roundDoc(over: Partial<RoundView> = {}): RoundView {
  return {
    round: 0,
    election: null,
    candidates: [],
    headline: null,
    unfiled: false,
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
      evidence_grounding: null,
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
    run_phase: "running",
    stop_reason: null,
    round: 0,
    active: null,
    successor: null,
    line: [],
    line_count: { key: "candidates", tone: "quiet", label: "Candidates", value: "0", sub: null, detail: null },
    rounds: [],
    series_version: "",
    series_held: false,
    sample_coverage: { measuring_rounds: 0, samples: [], first_measured: [] },
    overlap_line_round: null,
    overlap_line_n: null,
    verification: [],
    composite_fitness_formula: null,
    composite_fitness_weights: null,
    composite_fitness_anchors: null,
    display_metric: "fitness",
    recent_backend_warnings: [],
    current_query_payload: null,
    lookahead: {
      depth: 1,
      abreast: 1,
      max_speed: false,
      auto: false,
      ceiling: 1,
      in_flight: 0,
      allowed: 0,
      most: 0,
      scoring_now: false,
      pick_max: 1,
      affordable: null,
      cell_reserve_usd: null,
      auto_refusal: "one_at_a_time",
      depths: [{ cells: 1, fill: null, pickable: true, label: "One at a time", hint: "One at a time" }],
      abreasts: [{ abreast: 1, pickable: true, label: "One after another", hint: "" }],
      pressed: 1,
      shallow: "This backend runs one sample at a time — a single call has no latency to overlap.",
      scope: null,
      flight: "0 of 0 out · depth 1 per candidate · one candidate sends ahead this round · 0 discarded so far.",
    },
    held_call: null,
    producer: producerReading(),
    admission: { offers: "pause", refusal: null, payload: {}, stop_acts: [] },
    pause_waits_on: "starting up",
    skip_refusal: "",
    backpressure: null,
    measured_unit: "sample",
    round_cells_ceiling: 0,
    run_limits: { budget: {}, overshoot: false, optimizer: [] },
    defaults_drift: null,
    backend_revision: null,
    spend_metered: NO_METER,
    fork_remainder: null,
    spend_by_round: { rounds: [], series: [], readings: [], empty: "Nothing billed yet." },
    metered_line: "",
    cap_lines: [],
    caps: armedCaps(),
    overshot: null,
    catch_up_log: [],
    current_round: currentRound(),
    observed: null,
    live: false,
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
      samples: { scored: 0, expected: 0, cached: 0 },
      election: { held: true },
    }),
    ...over,
  };
}

export function summaryRound(over: Partial<ServedRound> = {}): ServedRound {
  return {
    round: 0,
    leading: null,
    ending: null,
    verdict_reason: null,
    candidates: [],
    selection: [],
    health: health("healthy"),
    panel_reading: unreadOverlap(),
    panel_precision: null,
    panel_precision_verdict: null,
    selection_movement: [],
    measured: [],
    optimizer_facts: [],
    hold_up: null,
    ...over,
  };
}

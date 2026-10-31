import type {
  HierarchyCampaign,
  HierarchyCourse,
  HierarchyEntry,
  HierarchyTag,
  ListedCampaign,
  UnstartedCampaign,
} from "@/lib/api";
import type {
  ArmReading,
  BenchScore,
  CourseFacts,
  LifetimeSpend,
  MeteredSpend,
  PairedReading,
  RunStanding,
} from "@/lib/api/types.generated";
import {
  BENCH_STATE_INFO,
  CANDIDATE_STANDING_INFO,
  DISPLAY_METRIC_UNITS,
  READING_STATE_INFO,
  ROUNDS_CAP_WORDS,
} from "@/lib/api/types.generated";
import { fmtInterval, fmtPct1, fmtUnit, fmtUsd, PLACEHOLDER } from "@/lib/format";
import { decodeCyclePath } from "@/lib/ids";
import { livesBar, livesLabel } from "./lives";
import { fmtLift, readPaired } from "./paired-reading";
import { dockPriority } from "./run-phase";
import { billText, ratePricedText } from "./spend";

export interface RootGroup {
  rootCycleId: string;
  campaigns: ListedCampaign[];
}

// The server serves `entries` root-major, so a group is a run of consecutive campaigns. An
// unstarted campaign has no root cycle: it stands alone, keyed by its own address.
export function groupByRoot(entries: readonly ListedCampaign[]): RootGroup[] {
  const groups: RootGroup[] = [];
  for (const campaign of entries) {
    if (campaign.level === "unstarted") {
      groups.push({ rootCycleId: campaign.address, campaigns: [campaign] });
      continue;
    }
    const { root } = campaign.facts;
    const rootCycleId = decodeCyclePath(root)?.at(-1)?.cycleId;
    if (rootCycleId === undefined) throw new Error(`undecodable root ${root}`);
    const last = groups.at(-1);
    if (last?.rootCycleId === rootCycleId) last.campaigns.push(campaign);
    else groups.push({ rootCycleId, campaigns: [campaign] });
  }
  return groups;
}

export function campaignAt(
  byAddress: ReadonlyMap<string, HierarchyEntry>,
  address: string,
): HierarchyCampaign | null {
  const entry = byAddress.get(address);
  return entry?.level === "campaign" ? entry : null;
}

export function runningCourses(entries: readonly HierarchyCampaign[]): HierarchyCourse[] {
  return entries
    .flatMap((campaign) => campaign.children)
    .filter((c) => c.state.attached)
    .sort(
      (a, b) => dockPriority(a.state.status.run_phase) - dockPriority(b.state.status.run_phase),
    );
}

// An entry without the entries under it: what a frozen item or a copy holds of one row.
export function entryAlone<E extends object>(entry: E): Omit<E, "children"> {
  return Object.fromEntries(Object.entries(entry).filter(([k]) => k !== "children")) as Omit<
    E,
    "children"
  >;
}

interface Tagged {
  tags: readonly HierarchyTag[];
}

export function hasTag(entry: Tagged | null, kind: HierarchyTag["kind"]): boolean {
  return entry?.tags.some((t) => t.kind === kind) ?? false;
}

export function tagValue(entry: Tagged | null, kind: HierarchyTag["kind"]): string | null {
  return entry?.tags.find((t) => t.kind === kind)?.value ?? null;
}

/** A course's rounds against its cap: `views/render/primitives.py::rounds_readout`, the same. */
export function roundsText(facts: CourseFacts): string {
  if (facts.rounds_cap === 0) return ROUNDS_CAP_WORDS.origin_only;
  if (facts.rounds_cap === null) return `${facts.rounds_closed} · ${ROUNDS_CAP_WORDS.no_axis}`;
  return `${facts.rounds_closed} of ${facts.rounds_cap}`;
}

/** The bench as one word: the selection's level, else why there is none — the line's alone. */
export function benchWord(bench: BenchScore | null, holdsLine: boolean): string {
  if (!holdsLine) return READING_STATE_INFO.held_elsewhere.label;
  if (bench === null) return PLACEHOLDER;
  const level = bench.selected?.level ?? null;
  return level === null
    ? READING_STATE_INFO[bench.status.state].label
    : fmtUnit(DISPLAY_METRIC_UNITS[bench.headline], level.value);
}

/** The bench's sentence for a hover: the line's state, or why another course reads none. */
export function benchSentence(bench: BenchScore | null, holdsLine: boolean): string | undefined {
  return BENCH_STATE_INFO[holdsLine ? (bench?.status.state ?? "pending") : "held_elsewhere"]
    ?.sentence;
}

/** The ONE text reading of a selection: `RunStanding.selection_line`, the same. */
export function selectionLine(standing: RunStanding | null): string | null {
  if (standing === null) return null;
  const { selection, vs_origin } = standing;
  if (selection === null) return "nothing selected";
  const name = `${selection.label} (round ${selection.round})`;
  const { headline, coverage } = vs_origin;
  if (headline === null || coverage === null) {
    return `${name} — ${READING_STATE_INFO[vs_origin.state].sentence}`;
  }
  return `${name} — origin ${fmtPct1(headline.rate_a)} → ${fmtPct1(headline.rate_b)} on ${coverage.scored} origin-panel cells`;
}

/** The one sentence under an entry's title: a run's selection line, a candidate's standing. */
export function entryLede(entry: HierarchyEntry | UnstartedCampaign): string | null {
  switch (entry.level) {
    case "campaign":
      return selectionLine(entry.facts.line_course?.standing ?? null);
    case "course":
      return selectionLine(entry.facts.standing);
    case "candidate":
      return CANDIDATE_STANDING_INFO[entry.facts.standing];
    default:
      return null;
  }
}

export interface EntryFigure {
  key: string;
  label: string;
  value: string;
  sub: string | null;
}

function spendFigure(
  key: string,
  label: string,
  spent: MeteredSpend | LifetimeSpend,
  unreported: string | null,
): EntryFigure {
  return {
    key,
    label,
    value: billText(spent.billed_usd, spent.bill_is_floor),
    sub: unreported ?? ratePricedText(spent.rate_priced_usd, spent.calls_rate_priced),
  };
}

function lifetimeFigure(spent: LifetimeSpend): EntryFigure {
  const unreported = spent.sends_unreported
    ? `up to ${fmtUsd(spent.unreported_usd)} more unreported`
    : null;
  return spendFigure("spend_lifetime", "Spent", spent, unreported);
}

function liftFigure(
  key: string,
  label: string,
  pair: PairedReading,
  against: string,
  notes: string[],
): EntryFigure {
  const read = readPaired(pair);
  if (!read.read) return { key, label, value: read.label, sub: notes.join(" · ") || null };
  const { estimate } = read.lift;
  return {
    key,
    label,
    value: `${fmtUnit("delta", estimate.value)} ${fmtInterval("delta", estimate.ci_lo, estimate.ci_hi)}`,
    sub: [`${against} ${fmtLift(read.lift, "rates")}`, ...notes].join(" · "),
  };
}

function courseFigures(facts: CourseFacts, holdsLine: boolean): EntryFigure[] {
  const { standing } = facts;
  const figures: EntryFigure[] = [];
  if (facts.bench !== null || !holdsLine) {
    figures.push({
      key: "bench",
      label: "Bench",
      value: benchWord(facts.bench, holdsLine),
      sub: benchSentence(facts.bench, holdsLine) ?? null,
    });
  }
  figures.push({ key: "rounds", label: "Rounds", value: roundsText(facts), sub: null });
  if (standing === null) return figures;
  const onTheta = facts.elects_on === "ability" ? ["rounds are won on θ"] : [];
  figures.push(liftFigure("lift", "Lift vs origin", standing.vs_origin, "origin", onTheta));
  if (standing.lives !== null) {
    figures.push({
      key: "lives",
      label: "Lives",
      value: livesBar(standing.lives),
      sub: livesLabel(standing.lives),
    });
  }
  return figures;
}

function candidateFigures(reading: ArmReading): EntryFigure[] {
  const { panel } = reading;
  const figures: EntryFigure[] = [
    {
      key: "accuracy",
      label: "Accuracy",
      value: fmtUnit("rate", reading.own?.accuracy?.value),
      sub:
        panel.scored === null
          ? null
          : `${panel.scored}${panel.expected === null ? "" : ` of ${panel.expected}`} scored`,
    },
  ];
  const ability = reading.ability;
  if (ability !== null && (ability.theta !== null || ability.caveat !== null)) {
    const theta = ability.theta === null ? PLACEHOLDER : ability.theta.toFixed(2);
    figures.push({
      key: "ability",
      label: "Ability θ",
      value: ability.se === null || ability.theta === null ? theta : `${theta} ± ${ability.se.toFixed(2)}`,
      sub: ability.caveat ? "not ability — see below" : null,
    });
  }
  if (reading.vs_reference !== null) {
    figures.push(liftFigure("lift", "Lift vs parent", reading.vs_reference, "parent", []));
  }
  return figures;
}

/** The numbers a row or a card shows for an entry, headline first, read off its typed `facts`. */
export function entryFigures(
  entry: HierarchyEntry | HierarchyCampaign | HierarchyCourse | UnstartedCampaign,
): EntryFigure[] {
  switch (entry.level) {
    case "unstarted":
      return [lifetimeFigure(entry.facts.spend_lifetime)];
    case "campaign": {
      const line = entry.facts.line_course;
      return [lifetimeFigure(entry.facts.spend_lifetime), ...(line ? courseFigures(line, true) : [])];
    }
    case "course":
      return [
        spendFigure("spend", "Spend", entry.facts.spend, null),
        // A course beside the line has no bench of its own.
        ...courseFigures(entry.facts, entry.facts.bench !== null),
      ];
    case "candidate":
      return candidateFigures(entry.facts.reading);
  }
}

export function tagLabel(entry: Tagged | null, kind: HierarchyTag["kind"]): string | null {
  return entry?.tags.find((t) => t.kind === kind)?.label ?? null;
}

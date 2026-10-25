import { liveCandidate, liveCandidates, roundOf } from "@/lib/poll";
import type { ServedDashboard } from "@/lib/api";
import type { ArmName, ArmReading, CellRow, HierarchyCandidate, NodePrompt } from "@/lib/api/types";
import type { ArmRow, RoundView, SelectedCandidate } from "@/lib/types";
import { armSearchpoint, type ArmSearchpoint } from "./candidateSearchPoint";
import { rowAt } from "./round-candidates";
import { roundReading, type RoundReading } from "./round-reading";

export type ObserveState = "best" | "latest" | "round" | "selected";

const OBSERVE_LABELS: Record<ObserveState, string> = {
  best: "Best",
  latest: "Most recent",
  round: "Round",
  selected: "Selected",
};

export function observeOptions(
  avail: Record<ObserveState, boolean>,
  round: number | null = null,
): { value: ObserveState; label: string }[] {
  const order: ObserveState[] = ["best", "latest", "round", "selected"];
  return order
    .filter((s) => avail[s])
    .map((s) => ({
      value: s,
      label: s === "round" && round != null ? `Round ${round}` : OBSERVE_LABELS[s],
    }));
}

export interface ObserveConfig {
  // `OptSearchPoint.prompts`: per prompt node, its fields and shots.
  prompts: Record<string, NodePrompt>;
  config: Record<string, unknown>;
  // Decorated header ("live — C1.3"); never a join key.
  label: string;
}

// Keys are the served names, so a paste greps against `round_NNNN.json`; cells are ALL of them, never the render cap.
export function searchpointCopyChoices({
  cfg,
  reading,
  cells = [],
}: {
  cfg: ObserveConfig | null;
  reading?: ArmReading | null;
  cells?: readonly CellRow[];
}): { key: string; label: string; data: unknown }[] {
  const label = reading?.arm.label;
  const spec = cfg
    ? {
        // Only a reading's served label greps against the round file; the decorated header rides `shown_as`.
        ...(label !== undefined ? { label } : { shown_as: cfg.label }),
        resolved_params: cfg.config,
        prompts: cfg.prompts,
      }
    : null;
  const scored = reading ? { ...(spec ?? { label }), scored: reading } : null;

  const choices: { key: string; label: string; data: unknown }[] = [];
  if (spec) choices.push({ key: "spec", label: "Searchpoint spec", data: spec });
  if (scored) choices.push({ key: "scored", label: "Spec + scores", data: scored });
  if (scored && cells.length > 0) {
    choices.push({
      key: "full",
      label: `Spec + scores + ${cells.length} cells`,
      data: { ...scored, cells },
    });
  }
  return choices;
}

// NOT the runnable string: the backend renders that per node, and nothing serves it.
export function searchpointText(cfg: ObserveConfig): string {
  const lines: string[] = [];
  for (const [node, p] of Object.entries(cfg.prompts)) {
    for (const [key, v] of Object.entries(p.fields)) {
      if (v.trim()) lines.push(`${node}.${key}:\n${v.trim()}\n`);
    }
    if (p.shots.length > 0) lines.push(`${node}.shots: ${p.shots.join(", ")}\n`);
  }
  if (lines.length === 0) lines.push("(this searchpoint carries no prompt fields)\n");
  lines.push(`pipeline config:\n${JSON.stringify(cfg.config, null, 2)}`);
  return lines.join("\n");
}

// A node asks for the prompt IT runs (absent = none); the whole searchpoint for every node's.
function rowConfig(
  row: ArmSearchpoint | undefined | null,
  label: string,
  nodeId?: string | null,
): ObserveConfig | null {
  if (!row) return null;
  const prompts = row.prompts ?? {};
  const own = nodeId ? prompts[nodeId] : undefined;
  return {
    prompts: nodeId ? (own ? { [nodeId]: own } : {}) : prompts,
    config: row.resolved_params ?? {},
    label,
  };
}

// The served list orders the latest-seeded row last.
export function liveObserveConfig(
  dash: ServedDashboard | null,
  nodeId?: string | null,
): ObserveConfig | null {
  const latest = liveCandidates(dash).at(-1);
  if (!latest) return null;
  return rowConfig(latest, `live — ${latest.reading.arm.label}`, nodeId);
}

export function liveCandidateObserveConfig(
  dash: ServedDashboard | null,
  key: string | null,
  nodeId?: string | null,
): ObserveConfig | null {
  const row = liveCandidate(dash, key);
  return row && rowConfig(row, `live — ${row.reading.arm.label}`, nodeId);
}

export function candidateObserveConfig(
  doc: RoundView | null,
  key: string | null,
  display: string,
  nodeId?: string | null,
): ObserveConfig | null {
  if (!doc || !key) return null;
  return rowConfig(armSearchpoint(doc, key), display, nodeId);
}

export type ObserveTarget = Pick<ArmName, "key" | "round" | "label">;

function bestObserveTarget(dash: ServedDashboard | null): ObserveTarget | null {
  return dash?.run_standing?.selection ?? null;
}

// Served `leading` is the arm the selector read the round off, so a held round still has one.
function roundObserveTarget(dash: ServedDashboard | null, round: number): ObserveTarget | null {
  if (!dash?.round_axis.completed.includes(round)) return null;
  return dash.rounds.find((x) => x.round === round)?.leading ?? null;
}

export interface ObservePoint extends ObserveTarget {
  title: string;
  row: ArmRow | null;
}

function pointAt(dash: ServedDashboard | null, target: ObserveTarget, title: string): ObservePoint {
  return { ...target, title, row: rowAt(dash, target.key) };
}

export function originPoint(
  dash: ServedDashboard | null,
  origin: HierarchyCandidate | null,
): ObservePoint | null {
  if (!origin) return null;
  const { arm } = origin.facts.reading;
  return pointAt(dash, arm, `origin · ${arm.label}`);
}

export interface ObserveSelection {
  candidate: Pick<SelectedCandidate, "round" | "arm" | "label"> | null;
  round: number | null;
  observe: ObserveState | null;
}

export interface ObserveSubject {
  state: ObserveState;
  options: { value: ObserveState; label: string }[];
  point: ObservePoint | null;
  reading: RoundReading | null;
  verdict: RoundReading | null;
  round: number | null;
  live: boolean;
}

function subjectTitle(state: ObserveState, target: ObserveTarget, inFlight: boolean): string {
  switch (state) {
    case "selected":
      return `selected · ${target.label}`;
    case "round":
      return `round ${target.round} · ${target.label}`;
    case "best":
      return `${target.round === 0 ? "origin" : "best"} · ${target.label}`;
    case "latest":
      return inFlight ? `live — ${target.label}` : `latest · ${target.label}`;
  }
}

export function resolveObserveSubject(
  dash: ServedDashboard | null,
  isLive: boolean,
  selection: ObserveSelection,
): ObserveSubject {
  // In flight only while the run is live: `current_round` lingers in `dashboard.json` after a stop.
  const observed = dash?.observed ?? null;
  const latest = observed;
  const inFlight = isLive && latest?.round === roundOf(dash);
  const picked = selection.candidate;
  const targets: Record<ObserveState, ObserveTarget | null> = {
    best: bestObserveTarget(dash),
    latest,
    round: selection.round != null ? roundObserveTarget(dash, selection.round) : null,
    selected: picked ? { key: picked.arm, round: picked.round, label: picked.label } : null,
  };
  const asked = selection.observe && targets[selection.observe] ? selection.observe : null;
  const followsLive = !asked && !targets.selected && !targets.round;
  const state: ObserveState =
    asked ??
    (targets.selected
      ? "selected"
      : targets.round
        ? "round"
        : inFlight
          ? "latest"
          : targets.best
            ? "best"
            : "latest");
  const target = targets[state];
  const liveRound = isLive ? (dash?.round_axis.live ?? null) : null;
  const round = followsLive && liveRound != null ? liveRound : (target?.round ?? null);
  const reading = target ? roundReading(dash, target.round) : null;
  const newestClosed = dash?.round_axis.completed.at(-1);
  return {
    state,
    options: observeOptions(
      {
        best: !!targets.best,
        latest: !!targets.latest,
        round: !!targets.round,
        selected: !!targets.selected,
      },
      selection.round,
    ),
    point: target
      ? pointAt(dash, target, subjectTitle(state, target, state === "latest" && inFlight))
      : null,
    reading,
    verdict:
      state === "latest"
        ? null
        : state === "best"
          ? newestClosed != null
            ? roundReading(dash, newestClosed)
            : null
          : reading,
    round,
    live: round != null && round === liveRound,
  };
}

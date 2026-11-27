import type {
  ServedDashboard,
  ArmName,
  ArmReading,
  CellRow,
  HierarchyCandidate,
  NodePrompt,
  RunStanding,
  ServedArm,
  ServedRound,
} from "@/lib/api/types";
import { OBSERVE_STATES, type ObserveState } from "@/lib/address";

const OBSERVE_LABELS: Record<ObserveState, string> = {
  best: "Best",
  latest: "Most recent",
  round: "Round",
};

export function observeOptions(
  avail: Record<ObserveState, boolean>,
  round: number | null = null,
): { value: ObserveState; label: string }[] {
  return OBSERVE_STATES
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
  // The header its reader names ("selected · C1.3"); never a join key.
  label: string;
}

// The lead of a failed cells read beside a copy: the copy offers no cells.
export const COPY_WITHOUT_CELLS = "Copies carry no cells:";

// Keys are the served names, so a paste greps against `round_NNNN.json`; cells are ALL of them, never the render cap.
export function searchpointCopyChoices({
  cfg,
  reading,
  cells = [],
}: {
  cfg: ObserveConfig | null;
  reading: ArmReading | null;
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
export function armObserveConfig(
  arm: ServedArm | null,
  label: string,
  nodeId?: string | null,
): ObserveConfig | null {
  if (!arm) return null;
  const prompts = arm.prompts ?? {};
  const own = nodeId ? prompts[nodeId] : undefined;
  return {
    prompts: nodeId ? (own ? { [nodeId]: own } : {}) : prompts,
    config: arm.resolved_params ?? {},
    label,
  };
}

type ObserveTarget = Pick<ArmName, "key" | "round" | "label">;

// Served `leading` is the arm the selector read the round off, so a held round still has one.
function roundObserveTarget(dash: ServedDashboard | null, round: number): ObserveTarget | null {
  return dash?.rounds.find((x) => x.round === round)?.leading ?? null;
}

// Names the arm alone: its reading is the round read's (`useRound`), the one source of a round.
export interface ObservePoint extends ObserveTarget {
  title: string;
}

function pointAt(target: ObserveTarget, title: string): ObservePoint {
  return { ...target, title };
}

export function originPoint(origin: HierarchyCandidate | null): ObservePoint | null {
  if (!origin) return null;
  const { arm } = origin.facts.reading;
  return pointAt(arm, `origin · ${arm.label}`);
}

export interface ObserveSelection {
  round: number | null;
  observe: ObserveState | null;
}

export interface ObserveSubject {
  state: ObserveState;
  options: { value: ObserveState; label: string }[];
  point: ObservePoint | null;
  // The round as served; null for the one no file holds yet (`round_axis.unfiled`).
  reading: ServedRound | null;
  verdict: ServedRound | null;
  round: number | null;
  live: boolean;
}

function subjectTitle(state: ObserveState, target: ObserveTarget, inFlight: boolean): string {
  switch (state) {
    case "round":
      return `round ${target.round} · ${target.label}`;
    case "best":
      return `${target.round === 0 ? "origin" : "best"} · ${target.label}`;
    case "latest":
      return inFlight ? `live — ${target.label}` : `latest · ${target.label}`;
  }
}

function closedRound(dash: ServedDashboard | null, round: number): ServedRound | null {
  if (!dash || round === dash.round_axis.unfiled) return null;
  return dash.rounds.find((x) => x.round === round) ?? null;
}

export function resolveObserveSubject(
  dash: ServedDashboard | null,
  standing: RunStanding | null,
  selection: ObserveSelection,
): ObserveSubject {
  // Served `round_axis.live` is the round in flight: null once no producer appends or it closed.
  const latest = dash?.observed ?? null;
  const liveRound = dash?.round_axis.live ?? null;
  const inFlight = latest !== null && latest.round === liveRound;
  const targets: Record<ObserveState, ObserveTarget | null> = {
    best: standing?.selection ?? null,
    latest,
    round: selection.round != null ? roundObserveTarget(dash, selection.round) : null,
  };
  const asked = selection.observe && targets[selection.observe] ? selection.observe : null;
  const followsLive = !asked && !targets.round;
  const state: ObserveState =
    asked ?? (targets.round ? "round" : inFlight ? "latest" : targets.best ? "best" : "latest");
  const target = targets[state];
  const round = followsLive && liveRound != null ? liveRound : (target?.round ?? null);
  const reading = target ? closedRound(dash, target.round) : null;
  const newestClosed = dash?.round_axis.completed.at(-1);
  return {
    state,
    options: observeOptions(
      {
        best: !!targets.best,
        latest: !!targets.latest,
        round: !!targets.round,
      },
      selection.round,
    ),
    point: target
      ? pointAt(target, subjectTitle(state, target, state === "latest" && inFlight))
      : null,
    reading,
    verdict:
      state === "latest"
        ? null
        : state === "best"
          ? newestClosed != null
            ? closedRound(dash, newestClosed)
            : null
          : reading,
    round,
    live: round != null && round === liveRound,
  };
}

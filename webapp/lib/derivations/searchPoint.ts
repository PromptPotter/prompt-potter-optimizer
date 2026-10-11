import { liveCandidate, liveCandidates, roundOf, type DashboardSnapshot } from "@/lib/poll";
import { PROMPT_STRING_FIELDS } from "@/lib/prompt-fields";
import type { ArmNode, ArmReading, LiveCandidate } from "@/lib/api/types";
import type { ArmRow, RoundResult, SampleRow, SelectedCandidate } from "@/lib/types";
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
  // `OptSearchPoint.prompt_field_dict()` shape.
  promptFields: Record<string, unknown>;
  config: Record<string, unknown>;
  // Decorated header ("live — C1.3"); never a join key.
  label: string;
}

// Keys are the served names, so a paste greps against `round_NNNN.json`; samples are ALL of them, never the render cap.
export function searchpointCopyChoices({
  cfg,
  reading,
  samples = [],
}: {
  cfg: ObserveConfig | null;
  reading?: ArmReading | null;
  samples?: readonly SampleRow[];
}): { key: string; label: string; data: unknown }[] {
  const label = reading?.arm.label;
  const spec = cfg
    ? {
        // `label` is the join key, so only a reading answers for it; the decorated header rides `shown_as`.
        ...(label !== undefined ? { label } : { shown_as: cfg.label }),
        resolved_pipeline_params: cfg.config,
        prompt_fields: cfg.promptFields,
      }
    : null;
  const scored = reading ? { ...(spec ?? { label }), scored: reading } : null;

  const choices: { key: string; label: string; data: unknown }[] = [];
  if (spec) choices.push({ key: "spec", label: "Searchpoint spec", data: spec });
  if (scored) choices.push({ key: "scored", label: "Spec + scores", data: scored });
  if (scored && samples.length > 0) {
    choices.push({
      key: "full",
      label: `Spec + scores + ${samples.length} samples`,
      data: { ...scored, samples },
    });
  }
  return choices;
}

// NOT the runnable string: that is backend `PromptTemplate.compile_prompt()`, which nothing serves.
export function searchpointText(cfg: ObserveConfig): string {
  const lines: string[] = [];
  for (const key of PROMPT_STRING_FIELDS) {
    const v = cfg.promptFields[key];
    if (typeof v === "string" && v.trim()) lines.push(`${key}:\n${v.trim()}\n`);
  }
  if (lines.length === 0) lines.push("(this searchpoint carries no prompt fields)\n");
  lines.push(`pipeline config:\n${JSON.stringify(cfg.config, null, 2)}`);
  return lines.join("\n");
}

// promptpotter-self: prompt fields ride per node, and the round file carries only the mutated delta.
function nodePromptFields(
  resolved: Record<string, unknown> | undefined | null,
  nodeId: string | null | undefined,
): Record<string, unknown> {
  if (!nodeId || !resolved) return {};
  const slice = resolved[nodeId];
  if (!slice || typeof slice !== "object") return {};
  const rec = slice as Record<string, unknown>;
  const out: Record<string, unknown> = {};
  for (const k of PROMPT_STRING_FIELDS) {
    if (k in rec) out[k] = rec[k];
  }
  return out;
}

type SearchpointRow = Pick<LiveCandidate, "prompt_fields" | "resolved_pipeline_params">;

function rowConfig(
  row: SearchpointRow | undefined | null,
  label: string,
  nodeId?: string | null,
): ObserveConfig | null {
  if (!row) return null;
  const flat = row.prompt_fields ?? {};
  const hasFlatPrompt = PROMPT_STRING_FIELDS.some((k) => k in flat);
  return {
    promptFields: hasFlatPrompt ? flat : nodePromptFields(row.resolved_pipeline_params, nodeId),
    config: row.resolved_pipeline_params ?? {},
    label,
  };
}

// The served list orders the latest-seeded row last.
export function liveObserveConfig(
  dash: DashboardSnapshot | null,
  nodeId?: string | null,
): ObserveConfig | null {
  const latest = liveCandidates(dash).at(-1);
  if (!latest) return null;
  return rowConfig(latest, `live — ${latest.reading.arm.label}`, nodeId);
}

export function liveCandidateObserveConfig(
  dash: DashboardSnapshot | null,
  label: string,
  nodeId?: string | null,
): ObserveConfig | null {
  return rowConfig(liveCandidate(dash, label), `live — ${label}`, nodeId);
}

// Joined on the label, never `candidate_id`: a resume re-scores C0 under a NEW id, a silent miss.
export function candidateObserveConfig(
  doc: RoundResult | null,
  courseLabel: string,
  display: string,
  nodeId?: string | null,
): ObserveConfig | null {
  if (!doc || !courseLabel) return null;
  const row = doc.candidate_scores.find((c) => c.label === courseLabel);
  return rowConfig(row, display, nodeId);
}

export interface ObserveTarget {
  round: number;
  label: string;
}

function bestObserveTarget(dash: DashboardSnapshot | null): ObserveTarget | null {
  const winner = dash?.run_standing?.selection;
  return winner ? { round: winner.round, label: winner.label } : null;
}

// Served `leading` is the arm the selector read the round off, so a held round still has one.
function roundObserveTarget(dash: DashboardSnapshot | null, round: number): ObserveTarget | null {
  if (!dash?.round_axis.completed.includes(round)) return null;
  const r = dash.rounds.find((x) => x.round === round);
  return r?.leading ? { round: r.leading.round, label: r.leading.label } : null;
}

export interface ObservePoint extends ObserveTarget {
  title: string;
  row: ArmRow | null;
}

function pointAt(dash: DashboardSnapshot | null, target: ObserveTarget, title: string): ObservePoint {
  return { ...target, title, row: rowAt(dash, target) };
}

export function originPoint(
  dash: DashboardSnapshot | null,
  origin: Pick<ArmNode, "reading"> | null,
): ObservePoint | null {
  if (!origin) return null;
  const { round, label } = origin.reading.arm;
  return pointAt(dash, { round, label }, `origin · ${label}`);
}

export interface ObserveSelection {
  candidate: Pick<SelectedCandidate, "round" | "label"> | null;
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
  dash: DashboardSnapshot | null,
  isLive: boolean,
  selection: ObserveSelection,
): ObserveSubject {
  // In flight only while the run is live: `current_round` lingers in `dashboard.json` after a stop.
  const observed = dash?.observed ?? null;
  const latest = observed && { round: observed.round, label: observed.label };
  const inFlight = isLive && latest?.round === roundOf(dash);
  const targets: Record<ObserveState, ObserveTarget | null> = {
    best: bestObserveTarget(dash),
    latest,
    round: selection.round != null ? roundObserveTarget(dash, selection.round) : null,
    selected: selection.candidate
      ? { round: selection.candidate.round, label: selection.candidate.label }
      : null,
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

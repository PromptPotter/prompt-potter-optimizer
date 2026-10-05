// The time-ray shaped for rendering. The server bounds the payload (`RAY_PAYLOAD_FIELDS`); this
// bounds the pixels. Curation rides `projectionToActivity`, the chat's own translator.

import type { RayItem } from "@/lib/api/types";
import { projectionToActivity, type ActivityItem } from "@/lib/chat/activity";
import { candidateLabel } from "@/lib/candidate-label";
import { fmtDuration } from "@/lib/format";
import { encodeCyclePath, type CyclePath } from "@/lib/ids";

// 2.5× the longest legitimately silent-but-progressing wait, `measure_sample`'s 120 s
// QUERY_TIMEOUT. `gate` is excluded (see `rayHead`), and so is a cell the dashboard serves as
// open: a harness or agent cell runs for minutes and is no step until it lands.
export const WEDGED_AFTER_S = 300;

// Nine missed 10 s heartbeats.
const RECENT_S = 90;

export interface RayStep {
  // Never a window index, which shifts when a new cycle is discovered.
  key: string;
  path: CyclePath;
  pathKey: string;
  /** In `path`'s OWN ledger: a step below the viewed course is an address to navigate to, never
   *  a moment to fold this course to. */
  offset: number;
  at: number;
  gapBeforeS: number;
  cluster: number;
  activity: ActivityItem;
  round: number | null;
  /** The minting course's label (`course_label`), never `candidate_id`, which is re-minted per run. */
  candidateLabel: string | null;
}

function rec(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}
function str(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}
function num(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function toPath(item: RayItem): CyclePath {
  return item.path.map((h) => ({ campaignId: h.campaign_id, cycleId: h.cycle_id }));
}

// A `detail` (an L4 inner "rX/Y · best Z%") IS progress; a bare heartbeat proves only attachment.
function isHeartbeat(item: RayItem): boolean {
  return item.kind === "llm_call_progress" && !str(item.payload.detail);
}

// Apart from `projectionToActivity`: that yields a line to read, this an address, and a display
// tweak must not break a click.
function addressOf(item: RayItem): { round: number | null; candidateLabel: string | null } {
  const p = item.payload;
  if (item.kind === "candidate_minted") {
    return { round: num(p.round) ?? null, candidateLabel: str(p.label) ?? null };
  }
  if (item.kind === "snapshot") {
    const round = num(p.round) ?? null;
    const event = str(p.event);
    if (event === "candidate_started") {
      const idx = num(p.candidate_idx);
      return {
        round,
        candidateLabel: idx == null ? null : candidateLabel(round ?? 0, idx),
      };
    }
    if (event === "candidate_scored") {
      return { round, candidateLabel: str(rec(rec(p.payload).scores).label) ?? null };
    }
    return { round, candidateLabel: null };
  }
  if (item.kind === "phase") {
    const rr = rec(rec(p.payload).round_result);
    return { round: num(p.round) ?? num(rr.round) ?? null, candidateLabel: null };
  }
  return { round: num(p.round) ?? null, candidateLabel: null };
}

/** Only steps BELOW `rootPathKey` cluster: the root's own events are the story being told. */
export function raySteps(items: readonly RayItem[], rootPathKey: string): RayStep[] {
  const steps: RayStep[] = [];
  // Across ALL items: a heartbeat resets the silence clock without becoming a step.
  let lastAt: number | null = null;
  // The candidate a held-out pass is grading, per ledger: its row ticks carry no slot, so only
  // the pass's own bracket says whose they are.
  const benchOf = new Map<string, string>();

  for (const item of items) {
    const raw = Date.parse(item.ts);
    // Inherits its predecessor's time, the server clamp's repair: the sequence is the authority.
    const parsed: number = Number.isFinite(raw) ? raw : (lastAt ?? 0);
    const gapBeforeS = lastAt === null ? 0 : Math.max(0, (parsed - lastAt) / 1000);
    lastAt = parsed;

    if (isHeartbeat(item)) continue;
    if (item.kind === "phase" && str(item.payload.phase) === "bench") {
      const key = encodeCyclePath(toPath(item));
      const label = str(rec(rec(item.payload.payload).view).label);
      if (str(item.payload.event) === "enter" && label) benchOf.set(key, label);
      else if (str(item.payload.event) === "exit") benchOf.delete(key);
    }
    const activity = projectionToActivity({
      kind: item.kind,
      sequence: item.offset,
      payload: item.payload,
    });
    if (!activity) continue;

    const path = toPath(item);
    const pathKey = encodeCyclePath(path);
    const prev = steps[steps.length - 1];
    const graded = benchTick(item, benchOf.get(pathKey));
    if (graded && item.kind === "snapshot") {
      activity.label = `${graded} bench ${activity.label.replace(/^scoring /, "")}`;
    }

    // Fold into the newest (furthest-along) step, keeping the gap before the run began.
    if (prev && prev.pathKey === pathKey && pathKey !== rootPathKey) {
      steps[steps.length - 1] = {
        ...prev,
        key: prev.key,
        offset: item.offset,
        at: parsed,
        cluster: prev.cluster + 1,
        activity,
        ...addressOf(item),
        ...(graded ? { candidateLabel: graded } : {}),
      };
      continue;
    }

    steps.push({
      key: `${pathKey}#${item.offset}`,
      path,
      pathKey,
      offset: item.offset,
      at: parsed,
      gapBeforeS,
      cluster: 1,
      activity,
      ...addressOf(item),
      ...(graded ? { candidateLabel: graded } : {}),
    });
  }
  return steps;
}

// Opens and closes `benchOf` on the pass's bracket, and answers the graded candidate's label for
// the bracket's header and for each slotless row tick inside it.
function benchTick(item: RayItem, open: string | undefined): string | null {
  const p = item.payload;
  if (item.kind === "phase" && str(p.phase) === "bench") {
    return str(p.event) === "enter" ? (str(rec(rec(p.payload).view).label) ?? null) : null;
  }
  if (open == null || item.kind !== "snapshot" || str(p.event) !== "sample_scored") return null;
  return (num(p.candidate_idx) ?? 0) < 0 ? open : null;
}

export type RayHeadState =
  | "gate"
  | "running"
  | "waiting"
  | "wedged"
  | "paused"
  | "finished"
  | "detached"
  | "idle";

export interface RayHead {
  state: RayHeadState;
  label: string;
  detail: string;
  target: CyclePath | null;
}

/** Every long await heartbeats (`infrastructure/llm/heartbeat.py`), so a wedged run reads `running`
 *  forever. `wedged` is display-only (I6); a held `gate` heartbeats with no progress legitimately. */
export function rayHead(
  steps: readonly RayStep[],
  items: readonly RayItem[],
  runPhase: string | null | undefined,
  terminalLabel: string,
  nowMs: number,
  rootPathKey: string,
  // `dashboard.json::waiting_on` / `waiting_since`: the open cell the round's next step waits on.
  openCell: { on: string; since: number } | null,
): RayHead {
  const newestStep = steps[steps.length - 1];
  const target = newestStep && newestStep.pathKey !== rootPathKey ? newestStep.path : null;

  if (runPhase === "gate") {
    return { state: "gate", label: "Held", detail: "awaiting your decision", target: null };
  }
  if (runPhase === "terminal") {
    return { state: "finished", label: terminalLabel, detail: "", target: null };
  }
  if (runPhase === "detached") {
    return { state: "detached", label: "Detached", detail: "producer gone", target: null };
  }
  if (runPhase === "paused") {
    return { state: "paused", label: "Paused", detail: "resumable", target: null };
  }
  if (runPhase !== "running") {
    return { state: "idle", label: terminalLabel, detail: "", target: null };
  }

  const sinceProgressS = newestStep ? (nowMs - newestStep.at) / 1000 : Infinity;
  if (openCell && sinceProgressS > RECENT_S) {
    return {
      state: "running",
      label: "Measuring",
      detail: `${openCell.on} · open ${fmtDuration(Math.max(0, nowMs / 1000 - openCell.since))}`,
      target,
    };
  }
  if (sinceProgressS > WEDGED_AFTER_S) {
    const mins = Number.isFinite(sinceProgressS) ? `${Math.round(sinceProgressS / 60)}m` : "";
    return {
      state: "wedged",
      label: "Wedged",
      detail: mins ? `no progress for ${mins}` : "no progress recorded",
      target,
    };
  }

  // Sound within one window: a completion is always appended after its start.
  const rootItems = items.filter(
    (i) => encodeCyclePath(toPath(i)) === rootPathKey && !isHeartbeat(i),
  );
  const openCall = rootItems[rootItems.length - 1];
  const awaitingCall =
    openCall?.kind === "llm_call_start" &&
    !rootItems.some(
      (i) => i.kind === "llm_call" && str(i.payload.call_id) === str(openCall.payload.call_id),
    );
  if (awaitingCall && target) {
    return {
      state: "waiting",
      label: "Waiting",
      detail: newestStep?.activity.label ?? "on a child run",
      target,
    };
  }

  return {
    state: "running",
    label: "Running",
    detail:
      newestStep && sinceProgressS <= RECENT_S ? newestStep.activity.label : "no recent step",
    target,
  };
}

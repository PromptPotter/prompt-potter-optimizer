// The time-ray, shaped for rendering. Pure: RayItem[] in, steps + a head state out.
//
// Two problems kept apart, deliberately. The SERVER bounds the payload (it drops
// `token_usage`/`decision` everywhere and everything but milestones inside an inner run);
// this file bounds the PIXELS (it collapses a run of consecutive inner-cycle steps into one
// and marks silences). Neither can do the other's job — the server cannot know the viewport,
// and the client cannot cheaply page a ledger.
//
// Curation is NOT re-implemented here. Each item goes through `projectionToActivity`, the
// same translator the chat uses, because a RayItem's `kind` + `payload` are byte-identical
// to a `ProjectionEnvelope`'s. One curated event vocabulary, two surfaces.

import type { RayItem } from "@/lib/api/types";
import { projectionToActivity, type ActivityItem } from "@/lib/chat/activity";
import { candidateLabel } from "@/lib/candidate-label";
import { encodeCyclePath, type CyclePath } from "@/lib/ids";

// No progress for this long, while the SERVER still says `running`, is wedged.
//
// The threshold is not taste. With `gate` excluded (see `rayHead`), the longest legitimately
// silent-but-progressing interval in this package is `measure_sample`'s backend query, whose
// QUERY_TIMEOUT is 120 s. An L4 inner heartbeat carries `detail` ("inner rX/Y · best Z%"),
// which IS progress and counts as a non-heartbeat item. 300 s is 2.5× the longest legitimate
// silence.
export const WEDGED_AFTER_S = 300;

// A step older than this is not "now" — the `running` head reads the newest step's text only
// while that step is recent. Same number as the gap threshold, and for the same reason: nine
// missed 10 s heartbeats.
const RECENT_S = 90;

export interface RayStep {
  // Stable across refetches — never a window index, which shifts when a new cycle is
  // discovered.
  key: string;
  path: CyclePath;
  pathKey: string;
  /** Epoch ms of the step's effective timestamp. */
  at: number;
  /** Seconds of silence before this step. 0 = no gap, or nothing precedes it. */
  gapBeforeS: number;
  /** How many consecutive same-cycle steps this one stands for. 1 = just itself. */
  cluster: number;
  activity: ActivityItem;
  /** The round this step is about, for the shared round axis. */
  round: number | null;
  /** The candidate this step is about, by its MINTING course's label — the join the
   *  lineage tree blesses (`course_label`), never `candidate_id`, which is re-minted
   *  per run and would silently miss. */
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

// A BARE heartbeat: `llm_call_progress` with no `detail`. It proves the process was alive
// and nothing else, which is exactly the distinction the whole head-state table turns on —
// freshness proves attachment, never progress.
function isHeartbeat(item: RayItem): boolean {
  return item.kind === "llm_call_progress" && !str(item.payload.detail);
}

// WHICH candidate / round a step is about. Deliberately separate from
// `projectionToActivity`, which answers a different question: that one produces a LINE to
// read, this one produces an ADDRESS to navigate to. Folding them would make the chat carry
// navigation fields it never uses, and would make a display tweak able to break a click.
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

/**
 * The rendered sequence: even steps, explicit gap markers, inner runs clustered.
 *
 * `rootPathKey` names the ray's own course. Only steps BELOW it cluster: the root's own
 * events are the story being told, while a run of events from one inner cycle is a
 * digression that belongs behind one expandable marker.
 */
export function raySteps(items: readonly RayItem[], rootPathKey: string): RayStep[] {
  const steps: RayStep[] = [];
  // Carried across ALL items, not just steps — a heartbeat resets the silence clock without
  // becoming a step. That is what stops a heartbeated 120 s backend query growing a gap.
  let lastAt: number | null = null;

  for (const item of items) {
    const raw = Date.parse(item.ts);
    // An unparseable timestamp inherits its predecessor's, which is the same repair the
    // server's clamp makes and for the same reason: the sequence is the authority, so a
    // record with no readable time still has a place in it.
    const parsed: number = Number.isFinite(raw) ? raw : (lastAt ?? 0);
    const gapBeforeS = lastAt === null ? 0 : Math.max(0, (parsed - lastAt) / 1000);
    lastAt = parsed;

    if (isHeartbeat(item)) continue;
    const activity = projectionToActivity({
      kind: item.kind,
      cycle_id: item.path[item.path.length - 1]?.cycle_id ?? "",
      sequence: item.offset,
      payload: item.payload,
    });
    if (!activity) continue;

    const path = toPath(item);
    const pathKey = encodeCyclePath(path);
    const prev = steps[steps.length - 1];

    // CLUSTER: consecutive steps from the same non-root cycle fold into the newest one. The
    // newest rather than the first because it carries the furthest-along state, and the
    // cluster's gap stays the gap before the run began — folding the interior silences into
    // the marker would invent a pause that never happened.
    if (prev && prev.pathKey === pathKey && pathKey !== rootPathKey) {
      steps[steps.length - 1] = {
        ...prev,
        key: prev.key,
        at: parsed,
        cluster: prev.cluster + 1,
        activity,
        ...addressOf(item),
      };
      continue;
    }

    steps.push({
      key: `${pathKey}#${item.offset}`,
      path,
      pathKey,
      at: parsed,
      gapBeforeS,
      cluster: 1,
      activity,
      ...addressOf(item),
    });
  }
  return steps;
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
  /** Where the newest activity is, when that is somewhere the operator can go. */
  target: CyclePath | null;
}

/**
 * What the head of the ray says. `run_phase` alone cannot express `running` vs `wedged`:
 * every long await heartbeats (`dispatch/llm_call/heartbeat.py`), so freshness proves
 * ATTACHMENT, never progress — a wedged process reads `running` forever. The ray holds the
 * other input: progress = a non-heartbeat ledger append.
 *
 * `wedged` is derived HERE and written nowhere — a display state layered on the one
 * server-owned run state (I6), never a `RunPhase` member. `gate` is excluded from the
 * wedged test (load-bearing): a held gate legitimately heartbeats with zero progress
 * indefinitely — it is blocked on the operator, and it already has a state saying so.
 */
export function rayHead(
  steps: readonly RayStep[],
  items: readonly RayItem[],
  runPhase: string | null | undefined,
  terminalLabel: string,
  nowMs: number,
  rootPathKey: string,
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

  // The server says attached-and-fresh. Whether it is PROGRESSING is this file's question,
  // and a step is the only evidence of it — every step is a non-heartbeat append.
  const sinceProgressS = newestStep ? (nowMs - newestStep.at) / 1000 : Infinity;
  if (sinceProgressS > WEDGED_AFTER_S) {
    const mins = Number.isFinite(sinceProgressS) ? `${Math.round(sinceProgressS / 60)}m` : "";
    return {
      state: "wedged",
      label: "Wedged",
      detail: mins ? `no progress for ${mins}` : "no progress recorded",
      target,
    };
  }

  // WAITING ON A CHILD: the root opened an LLM call that has not returned, and the newest
  // thing in the family happened somewhere below. The pairing is sound within one window by
  // construction — a completion is always appended AFTER its start, so if the start is in the
  // window and its `llm_call` is not, the call genuinely has not completed.
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

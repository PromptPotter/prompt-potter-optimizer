// What a finished run gets to say for itself in the chat thread, captured at the
// moment it stops.
//
// Every field is lifted VERBATIM off `dashboard.json` — a label, a stop reason, a
// count of served rows, and four served numbers. Nothing is computed: the accuracy
// is the champion's own, the floor beside it is `matched_parent_accuracy` (the
// origin restricted to the samples that candidate actually measured, which is the
// only honest "was X" under elimination), and the lift is the served
// `ability_delta` in LOGITS — never a percent, and never `best − origin`.
//
// It is a SNAPSHOT on purpose. The frozen thread item must keep saying what the run
// ended at even after a `resume` moves the live dashboard on, so it holds values
// rather than a pointer to a document that a rewind can rewrite.

import type { DashboardSnapshot } from "@/lib/poll";
import { bestObserveTarget } from "./searchPoint";
import { headlineStats } from "./headline-stats";
import { roundHasCandidates, sortedRounds } from "./round-candidates";
import { readSpend } from "./spend";

export interface RunSummary {
  // Which cycle this describes — the frozen item outlives the address in view.
  cycleId: string;
  stopReason: string | null;
  // Rounds that closed WITH candidates. An L2/L3-terminal round measured nothing,
  // so counting it would claim work the run did not do.
  rounds: number;
  championLabel: string | null;
  // The champion's own measured accuracy, and the PARENT floor it was JUDGED
  // against. `null` where the candidate never covered the parent's panel — an
  // absent floor is not a zero.
  accuracy: number | null;
  parentAccuracy: number | null;
  // Served lift over origin, in logits. Format as θ, never as a percent.
  abilityDelta: number | null;
  usedUsd: number | null;
  // The optimizer's own one-line account of what it changed.
  changes: string;
  // The last closed round's VERDICT, so a champion that is still the origin can say
  // why. Without it the surface shows "origin · C0" after a round of two challengers
  // and looks broken — the reader has no way to tell "nothing has been tried" from
  // "two things were tried and both lost", and those are opposite facts about a run.
  // `improved` is served (`RoundSummary.improved`); round 0 holds no election and
  // reports `null`. `verdictReason` is the engine's own account of the election in θ
  // vocabulary — it rides a tooltip rather than the line, because this surface is read
  // by someone who has not agreed to speak logits.
  lastRound: {
    round: number;
    candidates: number;
    improved: boolean | null;
    verdictReason: string | null;
  } | null;
}

export function runSummary(dash: DashboardSnapshot | null): RunSummary | null {
  if (!dash?.cycle_id) return null;
  const closed = sortedRounds(dash).filter(roundHasCandidates);
  const target = bestObserveTarget(dash);
  const champion = target
    ? closed.find((r) => r.round === target.round)?.candidates[target.idx]
    : undefined;
  const { abilityDelta } = headlineStats(dash);
  const last = closed.at(-1);
  return {
    cycleId: dash.cycle_id,
    stopReason: dash.stop_reason ?? null,
    rounds: closed.length,
    championLabel: target?.courseLabel ?? null,
    accuracy: champion?.accuracy ?? null,
    parentAccuracy: champion?.matched_parent_accuracy ?? null,
    abilityDelta,
    usedUsd: readSpend(dash).usedUsd,
    changes: champion?.changes_description ?? "",
    lastRound: last
      ? {
          round: last.round,
          candidates: last.candidates.length,
          improved: last.improved,
          verdictReason: last.verdict_reason,
        }
      : null,
  };
}

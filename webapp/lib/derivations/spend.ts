// The single reader of served spend: every figure is a `MeteredSpend`, the cap's units beside each
// kind's bill. `dashboard.json` is served verbatim, so a kind may be ABSENT: guard, never annotate.

import type { KindSpend, MeteredSpend } from "@/lib/api/types";
import type { DashboardSnapshot } from "@/lib/poll";
import { fmtPct0, fmtUsd } from "@/lib/format";

// Display order, biggest first, in the operator's words — `evidence.py::_BUCKET_WORD` says the same.
// Must stay total over `domain/spend.py::TokenUsageKind`: an unlisted server kind renders as an
// unexplained gap between the rows and the served total.
export const SPEND_BUCKETS = [
  { key: "backend", label: "Connector" },
  { key: "optimizer", label: "Optimizer" },
  { key: "judge", label: "Judge" },
  { key: "diagnostic", label: "Diagnostic" },
  { key: "bench", label: "Bench" },
] as const;

export type SpendKey = (typeof SPEND_BUCKETS)[number]["key"];

export interface SpendLine {
  key: SpendKey;
  label: string;
  kind: KindSpend;
}

// Each served kind in display order; a kind the wire lacks is dropped, never zeroed.
export function spendLines(m: MeteredSpend): SpendLine[] {
  const kinds: Partial<Record<string, KindSpend>> = m.kinds;
  return SPEND_BUCKETS.flatMap(({ key, label }) => {
    const kind = kinds[key];
    return kind === undefined ? [] : [{ key, label, kind }];
  });
}

export const METER_WORD: Record<MeteredSpend["meter"], string> = {
  bill: "billed",
  search_incurred: "search incurred",
};

// The served replay share as a sentence, its `null` said rather than hidden.
export function replayShareLine(m: MeteredSpend): string {
  return m.replay_share == null
    ? "The search incurred nothing, so no share of it replayed"
    : `${fmtPct0(m.replay_share)} of the search replayed`;
}

// The compact secondary line: the kinds inside the cap, then what is metered beside it.
export function meteredBucketsLine(m: MeteredSpend): string {
  const lines = spendLines(m);
  const part = (l: SpendLine) => `${l.label.toLowerCase()} ${fmtUsd(l.kind.metered_usd)}`;
  const inside = lines.filter((l) => l.kind.counted).map(part).join(" · ");
  const beside = lines.filter((l) => !l.kind.counted).map(part).join(" · ");
  return beside ? `${inside} — beside: ${beside}` : inside;
}

export interface SpendView {
  // What the caps count — the one number set beside a cap. `null` where the route served none.
  metered: MeteredSpend | null;
  lines: SpendLine[];
  // From `run_limits`, the gate's source; `null` = that ceiling is disarmed.
  budgetUsd: number | null;
  budgetTokens: number | null;
}

export function readSpend(dash: DashboardSnapshot | null): SpendView {
  const metered = dash?.spend_metered ?? null;
  const limits = dash?.run_limits;
  return {
    metered,
    lines: metered ? spendLines(metered) : [],
    budgetUsd: typeof limits?.spend_budget_usd === "number" ? limits.spend_budget_usd : null,
    budgetTokens: typeof limits?.token_budget === "number" ? limits.token_budget : null,
  };
}

export interface RoundCost {
  round: number;
  metered: MeteredSpend;
}

// Served in round order, and an object's integer keys enumerate ascending besides.
export function roundCosts(dash: DashboardSnapshot | null): RoundCost[] {
  const by = dash?.spend_metered_by_round;
  if (!by) return [];
  const out: RoundCost[] = [];
  for (const [key, metered] of Object.entries(by)) {
    const round = Number(key);
    if (Number.isInteger(round)) out.push({ round, metered });
  }
  return out;
}

// One bill series per kind, KEYED by kind across rounds: a round lacking a kind is a gap (`null`),
// never another kind's dollars. A kind no round carries draws no series.
export function costSeries(rounds: RoundCost[]): { key: SpendKey; label: string; data: (number | null)[] }[] {
  const perRound = rounds.map((r) => new Map(spendLines(r.metered).map((l) => [l.key, l.kind])));
  return SPEND_BUCKETS.flatMap(({ key, label }) => {
    const data = perRound.map((kinds) => kinds.get(key)?.billed_usd ?? null);
    return data.every((v) => v === null) ? [] : [{ key, label, data }];
  });
}

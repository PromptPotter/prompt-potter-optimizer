import type { KindSpend, MeteredSpend } from "@/lib/api/types";
import { RATE_PRICED_LABEL, SPEND_KINDS, type SpendKind } from "@/lib/api/types.generated";
import type { DashboardSnapshot } from "@/lib/poll";
import { fmtPct0, fmtUsd } from "@/lib/format";

export interface SpendLine {
  key: SpendKind;
  label: string;
  kind: KindSpend;
}

// A kind the wire lacks is dropped, never zeroed.
export function spendLines(m: MeteredSpend): SpendLine[] {
  return SPEND_KINDS.flatMap(({ key, label }) => {
    const kind = m.kinds[key];
    return kind === undefined ? [] : [{ key, label, kind }];
  });
}

// served: `domain/spend.py::bill_is_floor`
export function billText(usd: number, isFloor: boolean): string {
  return `${isFloor ? "≥" : ""}${fmtUsd(usd)}`;
}

// The served bill leads; `metered_usd` is read only beside a cap.
export function spendHeadline(m: MeteredSpend): string {
  return billText(m.billed_usd, m.bill_is_floor);
}

// Never spent, so it sits beside a bill, never inside one (served: `domain/spend.py::calls_rate_priced`).
export function ratePricedText(usd: number, callsRatePriced: boolean): string | null {
  return callsRatePriced ? `${fmtUsd(usd)} ${RATE_PRICED_LABEL}` : null;
}

export function replayShareLine(m: MeteredSpend): string {
  return m.replay_share == null
    ? "The search incurred nothing, so no share of it replayed"
    : `${fmtPct0(m.replay_share)} of the search replayed`;
}

export function meteredBucketsLine(m: MeteredSpend): string {
  const lines = spendLines(m);
  const part = (l: SpendLine) => `${l.label.toLowerCase()} ${fmtUsd(l.kind.metered_usd)}`;
  const inside = lines.filter((l) => l.kind.counted).map(part).join(" · ");
  const beside = lines.filter((l) => !l.kind.counted).map(part).join(" · ");
  return beside ? `${inside} — beside: ${beside}` : inside;
}

export interface SpendView {
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
    budgetUsd: limits?.ceiling.usd ?? null,
    budgetTokens: limits?.ceiling.tokens ?? null,
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

// KEYED by kind across rounds: a round lacking a kind is a gap (`null`), never another kind's dollars.
export function costSeries(
  rounds: RoundCost[],
): { key: SpendKind; label: string; ink: number; data: (number | null)[] }[] {
  const perRound = rounds.map((r) => new Map(spendLines(r.metered).map((l) => [l.key, l.kind])));
  return SPEND_KINDS.flatMap(({ key, label }, ink) => {
    const data = perRound.map((kinds) => kinds.get(key)?.billed_usd ?? null);
    return data.every((v) => v === null) ? [] : [{ key, label, ink, data }];
  });
}

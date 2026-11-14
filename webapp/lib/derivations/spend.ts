import {
  type Budget,
  type KindSpend,
  type LifetimeSpend,
  type MeteredSpend,
  type ServedDashboard,
  RATE_PRICED_LABEL,
  SPEND_KIND_LABELS,
  type TokenUsageKind,
} from "@/lib/api/types";
import { wordRows } from "@/lib/api/words";
import { fmtUnit, fmtUsd } from "@/lib/format";

interface SpendLine {
  key: TokenUsageKind;
  label: string;
  kind: KindSpend;
}

// A kind the wire lacks is dropped, never zeroed.
export function spendLines(m: MeteredSpend): SpendLine[] {
  return wordRows(SPEND_KIND_LABELS).flatMap(([key, label]) => {
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

// What the allowance draws beyond the box's keys' bill, worded once for every surface that reads `CouponStatus`.
export function allowanceCaveats(l: LifetimeSpend): string[] {
  const lines: string[] = [];
  if (l.bill_is_floor) {
    lines.push(
      `${fmtUnit("tokens", l.unpriced_tokens)} billed by a model with no known price: the dollar figure undercounts, so the token unit binds.`,
    );
  }
  const priced = ratePricedText(l.rate_priced_usd, l.calls_rate_priced);
  if (priced) {
    lines.push(`${priced}: calls no provider reported a bill for, drawn at our rate.`);
  }
  if (l.sends_unreported) {
    lines.push(
      `Up to ${fmtUsd(l.unreported_usd)} unreported: sends that ended with no bill (cancelled, timed out, killed), drawn at the most they may have cost.`,
    );
  }
  return lines;
}

export function replayShareLine(m: MeteredSpend): string {
  return m.replay_share == null
    ? "The search incurred nothing, so no share of it replayed"
    : `${fmtUnit("rate", m.replay_share)} of the search replayed`;
}

export function meteredBucketsLine(m: MeteredSpend): string {
  const lines = spendLines(m);
  const part = (l: SpendLine) => `${l.label.toLowerCase()} ${fmtUsd(l.kind.metered_usd)}`;
  const inside = lines.filter((l) => l.kind.counted).map(part).join(" · ");
  const beside = lines.filter((l) => !l.kind.counted).map(part).join(" · ");
  return beside ? `${inside} — beside: ${beside}` : inside;
}

// The served budget is fully laid over, so an axis it does not name has nothing beneath it.
export function budgetAxis(budget: Budget, axis: keyof Budget): number | null {
  const value = budget[axis];
  return value === undefined ? null : value;
}

interface RoundCost {
  round: number;
  metered: MeteredSpend;
}

// Served in round order, and an object's integer keys enumerate ascending besides.
export function roundCosts(dash: ServedDashboard | null): RoundCost[] {
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
): { key: TokenUsageKind; label: string; ink: number; data: (number | null)[] }[] {
  const perRound = rounds.map((r) => new Map(spendLines(r.metered).map((l) => [l.key, l.kind])));
  return wordRows(SPEND_KIND_LABELS).flatMap(([key, label], ink) => {
    const data = perRound.map((kinds) => kinds.get(key)?.billed_usd ?? null);
    return data.every((v) => v === null) ? [] : [{ key, label, ink, data }];
  });
}

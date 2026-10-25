import type { Budget, KindSpend, MeteredSpend } from "@/lib/api/types";
import type { ServedDashboard } from "@/lib/api";
import {
  CEILING_METER_LABELS,
  RATE_PRICED_LABEL,
  SPEND_KINDS,
  type SpendKind,
} from "@/lib/api/types.generated";
import { fmtPct0, fmtTokens, fmtUsd } from "@/lib/format";

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
  // The CAMPAIGN's caps off `run_limits.budget`, the gate's source; `null` = that axis is unbounded.
  budgetUsd: number | null;
  budgetTokens: number | null;
  // What those caps count, every cycle of the campaign; `metered` is this cycle's own spend.
  campaignUsd: number | null;
  campaignTokens: number | null;
  // This cycle's share of the campaign cap, what its run's book binds; `null` = no run holds one.
  shareUsd: number | null;
  shareTokens: number | null;
}

// Campaign spend against the campaign cap, this cycle's beside it: the CLI's `render_run_spend` line.
export function capLines(v: SpendView): string[] {
  const m = v.metered;
  if (!m) return [];
  const counted = CEILING_METER_LABELS[m.meter];
  const lines: string[] = [];
  if (v.budgetUsd != null && v.campaignUsd != null) {
    const share = v.shareUsd != null ? ` of its ${fmtUsd(v.shareUsd)} share` : "";
    lines.push(
      `campaign cap ${fmtUsd(v.campaignUsd)} of ${fmtUsd(v.budgetUsd)} ${counted}, ` +
        `this cycle ${fmtUsd(m.metered_usd)}${share}`,
    );
  }
  if (v.budgetTokens != null && v.campaignTokens != null) {
    const share = v.shareTokens != null ? ` of its ${fmtTokens(v.shareTokens)} share` : "";
    lines.push(
      `campaign cap ${fmtTokens(v.campaignTokens)} of ${fmtTokens(v.budgetTokens)} ${counted}, ` +
        `this cycle ${fmtTokens(m.metered_tokens)}${share}`,
    );
  }
  return lines;
}

// The served budget is fully laid over, so an axis it does not name has nothing beneath it.
export function budgetAxis(budget: Budget, axis: keyof Budget): number | null {
  const value = budget[axis];
  return value === undefined ? null : value;
}

export function readSpend(dash: ServedDashboard | null): SpendView {
  const metered = dash?.spend_metered ?? null;
  const budget = dash?.run_limits.budget;
  const share = dash?.allotment ?? null;
  return {
    metered,
    lines: metered ? spendLines(metered) : [],
    budgetUsd: budget ? budgetAxis(budget, "usd") : null,
    budgetTokens: budget ? budgetAxis(budget, "tokens") : null,
    campaignUsd: dash?.campaign_metered_usd ?? null,
    campaignTokens: dash?.campaign_metered_tokens ?? null,
    shareUsd: share ? budgetAxis(share, "usd") : null,
    shareTokens: share ? budgetAxis(share, "tokens") : null,
  };
}

export interface RoundCost {
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
): { key: SpendKind; label: string; ink: number; data: (number | null)[] }[] {
  const perRound = rounds.map((r) => new Map(spendLines(r.metered).map((l) => [l.key, l.kind])));
  return SPEND_KINDS.flatMap(({ key, label }, ink) => {
    const data = perRound.map((kinds) => kinds.get(key)?.billed_usd ?? null);
    return data.every((v) => v === null) ? [] : [{ key, label, ink, data }];
  });
}

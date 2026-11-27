import {
  type Budget,
  type KindSpend,
  type MeteredSpend,
  SPEND_KIND_LABELS,
  type TokenUsageKind,
} from "@/lib/api/types";
import { wordRows } from "@/lib/api/words";

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

// The served budget is fully laid over, so an axis it does not name has nothing beneath it; a
// lift crosses as the word `unbounded`.
export function budgetAxis(budget: Budget, axis: keyof Budget): number | null {
  const value = budget[axis];
  return typeof value === "number" ? value : null;
}

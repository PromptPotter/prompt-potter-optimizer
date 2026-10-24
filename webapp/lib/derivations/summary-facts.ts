import type { BenchScore, MeteredSpend } from "@/lib/api";
import { CEILING_METER_LABELS, DISPLAY_METRIC_UNITS } from "@/lib/api/types.generated";
import { fmtLevel, fmtUsd } from "@/lib/format";
import { meteredBucketsLine, ratePricedText, spendHeadline } from "./spend";

export interface RowStat {
  label: string;
  value: string;
  sub?: string | null;
  className?: string;
}

// The shape a served hierarchy entry already has, so `SummaryBlock` renders one as it is served.
export interface SummaryFacts {
  label: string;
  // Always a word, never colour alone.
  state?: { status: { label: string } } | null;
  tags?: readonly { label: string }[];
  stats: readonly RowStat[];
}

export const SPEND_STAT_LABEL = "Billed";

export function cycleSpendStat(metered: MeteredSpend): RowStat {
  const priced = ratePricedText(metered.rate_priced_usd, metered.calls_rate_priced);
  return {
    label: SPEND_STAT_LABEL,
    value: spendHeadline(metered),
    sub:
      `${priced ? `${priced} · ` : ""}Incurred ${fmtUsd(metered.incurred_usd)} · ` +
      `Counted here against the campaign cap ${fmtUsd(metered.metered_usd)} ` +
      `${CEILING_METER_LABELS[metered.meter]}: ${meteredBucketsLine(metered)}`,
  };
}

const BENCH_STAT_LABEL = "Bench";

export function benchReading(bench: BenchScore | null | undefined): RowStat {
  if (!bench) return { label: BENCH_STAT_LABEL, value: "—" };
  return {
    label: BENCH_STAT_LABEL,
    value: fmtLevel(DISPLAY_METRIC_UNITS[bench.headline], bench.selected?.level?.value),
    sub: bench.line,
  };
}

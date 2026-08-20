"use client";
// Every pair of selected campaigns, blocked on the cells both scored. Each number here is served;
// nothing on this side computes a difference, an interval or a p.
//
// Two honesty rules the table exists to carry. A raw p read as if it were the only comparison is
// the error the Holm column corrects — so both are shown, never one. And the correction reaches
// the PAIRS only: choosing the metric after seeing the intervals is itself a comparison, and no
// column can price it, so the lede says so instead of implying it away.

import { CardFrame } from "@/components/ui";
import type { MetricReading, MetricSpec, PairwiseComparison } from "@/lib/api/types";
import { cx } from "@/lib/cx";
import { effectTone, fmtMetricInterval, fmtMetricValue, fmtPValue, shortId } from "@/lib/format";

// `nRead` is how many campaigns the read actually opened, not how many are ticked in the picker:
// the empty state has to tell "only one campaign" apart from "two campaigns that share no cell",
// and the shared-cell count alone cannot — it is zero in both. Reading it off `scored_cells`
// blamed the metric for a selection that had simply been measured on different datasets.
export function PairwisePanel({ reading, nRead }: { reading: MetricReading; nRead: number }) {
  const rows = reading.pairwise;
  return (
    <CardFrame title="Pairwise comparisons">
      {rows.length === 0 ? (
        <p className="l4-empty">
          {nRead < 2
            ? "One campaign read — a pairwise comparison needs two."
            : "No two campaigns here share a cell they both scored, so nothing can be paired. Campaigns on different datasets never share one, and a metric only some of them carry leaves the rest with none."}
        </p>
      ) : (
        <>
          <div className="l4-table-wrap">
            <table className="l4-table">
              <thead>
                <tr>
                  <th scope="col">pair</th>
                  <th scope="col">difference · 95% CI</th>
                  <th scope="col">cells</th>
                  <th scope="col">p</th>
                  <th scope="col">p (Holm)</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <Row
                    key={`${row.campaign_a}|${row.campaign_b}`}
                    row={row}
                    unit={reading.spec.unit}
                  />
                ))}
              </tbody>
            </table>
          </div>
          <p className="l4-lede">
            Each difference is <code>b − a</code> over the cells both campaigns scored — pairing
            removes cell difficulty rather than carrying it as noise. Holm corrects across the{" "}
            {reading.n_tests} comparison{reading.n_tests === 1 ? "" : "s"} in this table.
          </p>
          <p className="l4-note">
            It does not correct across metrics. If you tried several and kept the tightest, the
            interval you are reading is optimistic by an amount nothing here can compute.
          </p>
        </>
      )}
    </CardFrame>
  );
}

function Row({ row, unit }: { row: PairwiseComparison; unit: MetricSpec["unit"] }) {
  return (
    <tr className="l4-row">
      <td>
        <code>{shortId(row.campaign_a)}</code> → <code>{shortId(row.campaign_b)}</code>
      </td>
      <td className={cx("l4-effect", effectTone(row.ci_lo, row.ci_hi))}>
        <span className="l4-effect-mean">{fmtMetricValue(unit, row.mean_d)}</span>
        <span className="l4-effect-ci">{fmtMetricInterval(unit, row.ci_lo, row.ci_hi)}</span>
      </td>
      <td className="l4-num">{row.n_cells}</td>
      <td className="l4-num">{fmtPValue(row.p_value)}</td>
      <td className="l4-num">{fmtPValue(row.p_adjusted)}</td>
    </tr>
  );
}

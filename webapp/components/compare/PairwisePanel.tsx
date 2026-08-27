"use client";
// Every pair of selected subjects, blocked on the cells both scored. Each number here is served;
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

// `nRead` is how many subjects the read actually opened, not how many are ticked in the picker:
// the empty state has to tell "only one subject" apart from "two subjects that share no cell",
// and the shared-cell count alone cannot — it is zero in both. Reading it off `scored_cells`
// blamed the metric for a selection that had simply been measured on different datasets.
//
// `names` maps a served subject KEY to what the rest of the pane calls that channel — the pairwise
// rows refer to subjects by key, and a table that printed the raw address would be the only place
// on the page naming them differently.
export function PairwisePanel({
  reading,
  nRead,
  names,
}: {
  reading: MetricReading;
  nRead: number;
  names: ReadonlyMap<string, string>;
}) {
  const rows = reading.pairwise;
  return (
    <CardFrame title="Pairwise comparisons">
      {rows.length === 0 ? (
        <p className="l4-empty">
          {nRead < 2
            ? "One subject read — a pairwise comparison needs two."
            : "No two subjects here share a cell they both scored, so nothing can be paired. Subjects on different datasets never share one, and a metric only some of them carry leaves the rest with none."}
        </p>
      ) : (
        <>
          <div className="l4-table-wrap">
            <table className="l4-table">
              <thead>
                <tr>
                  <th scope="col">pair</th>
                  <th scope="col">shift · 95% CI</th>
                  <th scope="col">cells</th>
                  <th scope="col">p</th>
                  <th scope="col">p (Holm)</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <Row
                    key={`${row.subject_a}|${row.subject_b}`}
                    row={row}
                    unit={reading.spec.unit}
                    names={names}
                  />
                ))}
              </tbody>
            </table>
          </div>
          <p className="l4-lede">
            Each shift is <code>b − a</code> over the cells both subjects scored — pairing removes
            cell difficulty rather than carrying it as noise. Holm corrects across the{" "}
            {reading.n_tests} comparison{reading.n_tests === 1 ? "" : "s"} in this table.
          </p>
          <p className="l4-note">
            It does not correct across metrics. If you tried several and kept the tightest, the
            interval you are reading is optimistic by an amount nothing here can compute.
          </p>
          <p className="l4-note">
            The test is exact, so <code>p</code> stops at what this many paired cells can carry
            instead of borrowing a normal tail — and below a handful of cells no 95% interval exists
            to draw, which reads here as absent rather than as a wide one.
          </p>
        </>
      )}
    </CardFrame>
  );
}

function Row({
  row,
  unit,
  names,
}: {
  row: PairwiseComparison;
  unit: MetricSpec["unit"];
  names: ReadonlyMap<string, string>;
}) {
  return (
    <tr className="l4-row">
      <td>
        <code title={row.subject_a}>{names.get(row.subject_a) ?? shortId(row.subject_a)}</code> →{" "}
        <code title={row.subject_b}>{names.get(row.subject_b) ?? shortId(row.subject_b)}</code>
      </td>
      <td className={cx("l4-effect", effectTone(row.ci_lo, row.ci_hi))}>
        <span className="l4-effect-mean">{fmtMetricValue(unit, row.median_shift)}</span>
        <span className="l4-effect-ci">{fmtMetricInterval(unit, row.ci_lo, row.ci_hi)}</span>
      </td>
      <td className="l4-num">{row.n_cells}</td>
      <td className="l4-num">{fmtPValue(row.p_value)}</td>
      <td className="l4-num">{fmtPValue(row.p_adjusted)}</td>
    </tr>
  );
}

"use client";

import { CardFrame } from "@/components/ui";
import type { MetricReading, MetricSpec, PairwiseComparison } from "@/lib/api/types";
import { cx } from "@/lib/cx";
import { readPaired } from "@/lib/derivations";
import { PairedLift } from "@/components/shell/PairedLift";
import { fmtMetricInterval, fmtMetricValue, fmtPValue, shortId, sideTone } from "@/lib/format";

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
        <p className="note-empty">
          {nRead < 2
            ? "One subject read — a pairwise comparison needs two."
            : "Fewer than two subjects here carry a cell this metric scored, so there is no pair to list. A metric only some of them carry leaves the rest with none."}
        </p>
      ) : (
        <>
          <div className="l4-table-wrap">
            <table className="l4-table">
              <thead>
                <tr>
                  <th scope="col">pair</th>
                  <th scope="col">lift · 95% CI</th>
                  <th scope="col">cells</th>
                  <th scope="col">p</th>
                  <th scope="col">p (Holm)</th>
                  <th scope="col">p floor</th>
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
          <p className="note-lede">
            Each lift is the mean of <code>b − a</code> over the cells both subjects scored —
            pairing removes cell difficulty rather than carrying it as noise. Holm corrects across
            the {reading.n_tests} pair{reading.n_tests === 1 ? "" : "s"} read in this table.
          </p>
          <p className="note-info">
            It does not correct across metrics. If you tried several and kept the tightest, the
            interval you are reading is optimistic by an amount nothing here can compute.
          </p>
          <p className="note-info">
            The interval and <code>p</code> are Student-t on the per-cell differences.{" "}
            <code>p floor</code> is the smallest <code>p</code> an exact sign test can reach on the
            cells that differ: a <code>p</code> below it claims more than those cells can show.
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
  const read = readPaired(row.reading);
  const pair = (
    <td>
      <code title={row.subject_a}>{names.get(row.subject_a) ?? shortId(row.subject_a)}</code> →{" "}
      <code title={row.subject_b}>{names.get(row.subject_b) ?? shortId(row.subject_b)}</code>
    </td>
  );
  if (!read.read) {
    return (
      <tr className="l4-row">
        {pair}
        <td className="l4-dim" colSpan={5}>
          <PairedLift reading={row.reading} unread="sentence" />
        </td>
      </tr>
    );
  }
  const { estimate, family } = read.lift;
  return (
    <tr className="l4-row">
      {pair}
      <td className={cx("l4-effect", sideTone(estimate.side))}>
        <span className="l4-effect-mean">{fmtMetricValue(unit, estimate.value)}</span>
        <span className="l4-effect-ci">
          {fmtMetricInterval(unit, estimate.ci_lo, estimate.ci_hi)}
        </span>
      </td>
      <td className="l4-num">{read.cells}</td>
      <td className="l4-num">{fmtPValue(estimate.p_value)}</td>
      <td className="l4-num">{fmtPValue(family?.p_adjusted ?? null)}</td>
      <td className="l4-num">{estimate.p_floor.toPrecision(2)}</td>
    </tr>
  );
}

"use client";

import { CardFrame, DataTable, Empty, type Column } from "@/components/ui";
import type {
  MeasuredLift,
  MetricReading,
  MetricSpec,
  PairwiseComparison,
} from "@/lib/api/types";
import { cx } from "@/lib/cx";
import { readPaired } from "@/lib/derivations";
import { PairedLift } from "@/components/shell/PairedLift";
import { PLACEHOLDER, fmtInterval, fmtPValue, fmtUnit, shortId, sideTone } from "@/lib/format";

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
        <Empty>
          {nRead < 2
            ? "One subject read — a pairwise comparison needs two."
            : "Fewer than two subjects here carry a cell this metric scored, so there is no pair to list. A metric only some of them carry leaves the rest with none."}
        </Empty>
      ) : (
        <>
          <DataTable
            ariaLabel="Pairwise comparisons"
            columns={pairColumns(reading.spec.unit, names)}
            rows={rows.map(pairRow)}
            getRowId={({ row }) => `${row.subject_a}|${row.subject_b}`}
          />
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

interface PairRow {
  row: PairwiseComparison;
  // Null where the pair was not read; its sentence fills the lift column.
  lift: MeasuredLift | null;
  cells: number | null;
}

function pairRow(row: PairwiseComparison): PairRow {
  const read = readPaired(row.reading);
  return read.read
    ? { row, lift: read.lift, cells: read.cells }
    : { row, lift: null, cells: null };
}

function pairColumns(
  unit: MetricSpec["unit"],
  names: ReadonlyMap<string, string>,
): Column<PairRow>[] {
  const subject = (key: string) => <code title={key}>{names.get(key) ?? shortId(key)}</code>;
  const num = (
    id: string,
    label: string,
    value: (lift: MeasuredLift) => string,
  ): Column<PairRow> => ({
    id,
    label,
    width: "12ch",
    align: "end",
    cell: ({ lift }) => <span className="l4-num">{lift ? value(lift) : PLACEHOLDER}</span>,
  });
  return [
    {
      id: "pair",
      label: "pair",
      width: "minmax(16ch, 1fr)",
      cell: ({ row }) => (
        <>
          {subject(row.subject_a)} → {subject(row.subject_b)}
        </>
      ),
    },
    {
      id: "lift",
      label: "lift · 95% CI",
      width: "minmax(20ch, 1.5fr)",
      wrap: true,
      cell: ({ row, lift }) =>
        lift ? (
          <span className={cx("l4-effect", sideTone(lift.estimate.side))}>
            <span className="l4-effect-mean">{fmtUnit(unit, lift.estimate.value)}</span>
            <span className="l4-effect-ci">
              {fmtInterval(unit, lift.estimate.ci_lo, lift.estimate.ci_hi)}
            </span>
          </span>
        ) : (
          <span className="l4-dim">
            <PairedLift reading={row.reading} unread="sentence" />
          </span>
        ),
    },
    {
      id: "cells",
      label: "cells",
      width: "7ch",
      align: "end",
      cell: ({ cells }) => <span className="l4-num">{cells ?? PLACEHOLDER}</span>,
    },
    num("p", "p", (lift) => fmtPValue(lift.estimate.p_value)),
    num("p-holm", "p (Holm)", (lift) => fmtPValue(lift.family?.p_adjusted ?? null)),
    num("p-floor", "p floor", (lift) => lift.estimate.p_floor.toPrecision(2)),
  ];
}

import { CardFrame, DataTable, Empty, type Column } from "@/components/ui";
import type { MeasuredLift, MetricReading, PairwiseComparison } from "@/lib/api/types";
import { PLACEHOLDER, fmtPValue, shortId } from "@/lib/format";
import { liftColumns, pairedCells, type PairedCells } from "./lift-columns";

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
            columns={pairColumns(names)}
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

type PairRow = PairedCells & { row: PairwiseComparison };

function pairRow(row: PairwiseComparison): PairRow {
  return { row, ...pairedCells(row.reading) };
}

function pairColumns(names: ReadonlyMap<string, string>): Column<PairRow>[] {
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
    ...liftColumns<PairRow>("lift · 95% CI"),
    num("p", "p", (lift) => fmtPValue(lift.estimate.p_value)),
    num("p-holm", "p (Holm)", (lift) => fmtPValue(lift.family?.p_adjusted ?? null)),
    num("p-floor", "p floor", (lift) => lift.estimate.p_floor.toPrecision(2)),
  ];
}

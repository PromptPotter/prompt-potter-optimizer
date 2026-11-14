import type { Column } from "@/components/ui";
import type { MeasuredLift, PairedReading } from "@/lib/api/types";
import { cx } from "@/lib/cx";
import { readPaired } from "@/lib/derivations";
import { PairedLift } from "@/components/searchpoint/PairedLift";
import { PLACEHOLDER, sideTone } from "@/lib/format";

// Null where the pair was not read; its sentence fills the lift column.
export interface PairedCells {
  lift: MeasuredLift | null;
  cells: number | null;
}

export function pairedCells(reading: PairedReading): PairedCells {
  const pair = readPaired(reading);
  return pair.read ? { lift: pair.lift, cells: pair.cells } : { lift: null, cells: null };
}

// The served order's position among the read pairs: an unread one holds no place in it.
export function withReadRank<R extends PairedCells>(rows: readonly R[]): (R & { rank: number | null })[] {
  let read = 0;
  return rows.map((r) => ({ ...r, rank: r.lift === null ? null : (read += 1) }));
}

type LiftRow = PairedCells &{ row: { reading: PairedReading } };

// The paired lift with its 95% CI, then the cells it was read on: one column pair for every table.
export function liftColumns<R extends LiftRow>(label: string): Column<R>[] {
  return [
    {
      id: "lift",
      label,
      width: "minmax(20ch, 1.5fr)",
      wrap: true,
      cell: ({ row, lift }) =>
        lift === null ? (
          <span className="l4-dim">
            <PairedLift reading={row.reading} unread="sentence" />
          </span>
        ) : (
          <span className={cx("l4-effect", sideTone(lift.side))}>{lift.estimate.text}</span>

        ),
    },
    {
      id: "cells",
      label: "cells",
      width: "7ch",
      align: "end",
      cell: ({ cells }) => <span className="l4-num">{cells ?? PLACEHOLDER}</span>,
    },
  ];
}

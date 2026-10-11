"use client";

import { createContext, useContext, useMemo, useState, type ReactNode } from "react";
import type {
  CellCandidate,
  CellRow,
  DatasetItem,
  HardSampleOrder,
  HardSamplesScope,
} from "@/lib/api";
import { useCycleStream } from "@/lib/poll";
import { useCells, type SeriesTotals } from "@/lib/hooks/useCells";
import type { CyclePath } from "@/lib/ids";

interface HardSamples {
  datasetName: string | null;
  items: DatasetItem[];
  measuredCount: number;
  unmeasuredCount: number;
  // `CellRow.candidate` joins `CellCandidate.key`. Served chronologically; bucket, never re-sort.
  candidates: CellCandidate[];
  cells: CellRow[];
  totals: SeriesTotals | null;
  /** The SERVER's echo; `null` while a read is in flight. */
  rankedBy: HardSampleOrder | null;
  /** The control moves on click, while every LABEL names the served order until the rows land. */
  rankedByPick: HardSampleOrder | null;
  setRankedBy: (o: HardSampleOrder) => void;
  /** The optimizer's picker runs on the dataset scope regardless of this toggle. */
  scope: HardSamplesScope;
  setScope: (s: HardSamplesScope) => void;
  stale: boolean;
  /** Consumers MUST render it: an empty roster and a broken read spell `items` the same way. */
  error: string | null;
}

const Ctx = createContext<HardSamples | null>(null);

export function HardSamplesProvider({
  path,
  datasetName,
  children,
}: {
  path: CyclePath | null;
  datasetName: string | null;
  children: ReactNode;
}) {
  const [scope, setScope] = useState<HardSamplesScope>("campaign");
  const [rankedByPick, setRankedBy] = useState<HardSampleOrder | null>(null);
  const { isLive } = useCycleStream();
  const p = useCells(path, datasetName, scope, rankedByPick, isLive);
  // Keyed on the FIELDS, never on `p`, which is a fresh object every render.
  const value = useMemo<HardSamples>(
    () => ({
      datasetName,
      items: p.items,
      measuredCount: p.measuredCount,
      unmeasuredCount: p.unmeasuredCount,
      candidates: p.candidates,
      cells: p.cells,
      totals: p.totals,
      rankedBy: p.order,
      rankedByPick,
      setRankedBy,
      scope,
      setScope,
      stale: p.isStale,
      error: p.error,
    }),
    [
      datasetName,
      p.items,
      p.measuredCount,
      p.unmeasuredCount,
      p.candidates,
      p.cells,
      p.totals,
      p.order,
      p.isStale,
      p.error,
      rankedByPick,
      scope,
    ],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useHardSamples(): HardSamples {
  const v = useContext(Ctx);
  if (!v) throw new Error("useHardSamples must be used inside <HardSamplesProvider>");
  return v;
}

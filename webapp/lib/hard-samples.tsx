"use client";
// The dataset roster for the unit in view, its measured cells, and the scope + ranking controls —
// one context so non-adjacent consumers share one fetch, scope and ranking. The facade over `useCells`.

import { createContext, useContext, useMemo, useState, type ReactNode } from "react";
import type {
  CellCandidate,
  CellRow,
  DatasetItem,
  HardSampleOrder,
  HardSamplesScope,
} from "@/lib/api";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useCells, type SeriesTotals } from "@/lib/hooks/useCells";
import type { CyclePath } from "@/lib/ids";

interface HardSamples {
  datasetName: string | null;
  items: DatasetItem[];
  measuredCount: number;
  unmeasuredCount: number;
  splitTest: number | null;
  // `CellRow.candidate` joins `CellCandidate.key`. Served chronologically; bucket, never re-sort.
  candidates: CellCandidate[];
  cells: CellRow[];
  totals: SeriesTotals | null;
  /** The SERVER's echo; `null` while a read is in flight. Not `order` — `sampleOrder` in this
   *  tree is the scoring WALK. */
  rankedBy: HardSampleOrder | null;
  /** The control moves on click, while every LABEL names the served order until the rows land. */
  rankedByPick: HardSampleOrder | null;
  setRankedBy: (o: HardSampleOrder) => void;
  /** The optimizer's picker runs on the dataset scope regardless of this toggle
   *  (`l1/execute.py` round-subset fit). */
  scope: HardSamplesScope;
  setScope: (s: HardSamplesScope) => void;
  /** A prior (unit, scope) with a fetch in flight — dim it, never blank it. */
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
  // `null` sends no override, so the server resolves the dataset's declared `hard_sample_order`;
  // the browser never restates that default.
  const [rankedByPick, setRankedBy] = useState<HardSampleOrder | null>(null);
  const { isLive } = useDashboard();
  const p = useCells(path, datasetName, scope, rankedByPick, isLive);
  // Keyed on the FIELDS, never on `p`, which is a fresh object every render.
  const value = useMemo<HardSamples>(
    () => ({
      datasetName,
      items: p.items,
      measuredCount: p.measuredCount,
      unmeasuredCount: p.unmeasuredCount,
      splitTest: p.splitTest,
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
      p.splitTest,
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

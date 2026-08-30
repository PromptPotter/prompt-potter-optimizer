"use client";
// The dataset roster for the unit in view, the per-sample measurement history behind
// it, and the two controls that pick which slice that is — scope and ranking.
//
// A CONTEXT because the consumers are not adjacent: the heat-map hangs off the chat
// hero, the table off the run card four hops further down, and what they must agree on
// is SERVED — one fetch, one scope, one ranking. Carried as props it was twelve to
// fourteen of them per hop under four naming schemes, and the invariant was a comment
// on `ChatPane` ("Passed to BOTH consumers, so the pane and the run card cannot name
// different orders") rather than something the shape made true.
//
// `useDatasetPreview` stays the lower-level primitive owning the fetch chain; this is
// the facade the consumers actually want — the same split as `useCycleStream` and
// `useDashboard`.
//
// Nothing here computes a ranking (webapp/CLAUDE.md § Scoring authority).

import { createContext, useContext, useMemo, useState, type ReactNode } from "react";
import type { DatasetItem, HardSampleOrder, HardSamplesScope, SampleSeries } from "@/lib/api";
import { useDatasetPreview, type SeriesTotals } from "@/lib/hooks/useDatasetPreview";
import type { CyclePath } from "@/lib/ids";

interface HardSamples {
  datasetName: string | null;
  items: DatasetItem[];
  measuredCount: number;
  unmeasuredCount: number;
  splitTest: number | null;
  archivePerSample: Map<number, SampleSeries>;
  totals: SeriesTotals | null;
  /** The key the SERVER ranked the roster by, off its echo; `null` while a read is in
   *  flight. Not `order` — `sampleOrder` in this tree is the scoring WALK. */
  rankedBy: HardSampleOrder | null;
  /** The operator's PICK, `null` until they make one. Kept apart from `rankedBy`: the
   *  control moves on click, while every LABEL keeps naming the served order until the
   *  rows land. */
  rankedByPick: HardSampleOrder | null;
  setRankedBy: (o: HardSampleOrder) => void;
  /** This campaign's cycles, or every campaign on the dataset — the real series the
   *  optimizer's picker follows. That picker runs on the dataset scope regardless of
   *  this toggle (see `l1/execute.py` round-subset fit). */
  scope: HardSamplesScope;
  setScope: (s: HardSamplesScope) => void;
  /** The slice on screen is a prior (unit, scope) with a fetch in flight — dim it,
   *  never blank it. */
  stale: boolean;
  /** The roster read FAILED for the unit in view. Consumers MUST render it: an empty
   *  roster and a broken read are different facts that `items` spells the same way. */
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
  // `null` = send no override and let the server resolve the dataset's declared
  // `hard_sample_order`. The browser must never restate that default; what the
  // control DISPLAYS is the served echo, never this.
  const [rankedByPick, setRankedBy] = useState<HardSampleOrder | null>(null);
  const p = useDatasetPreview(path, datasetName, scope, rankedByPick);
  // Keyed on the FIELDS, never on `p`: the hook returns a fresh object every render,
  // so a dep on it would hand every consumer a new value on every poll tick.
  const value = useMemo<HardSamples>(
    () => ({
      datasetName,
      items: p.items,
      measuredCount: p.measuredCount,
      unmeasuredCount: p.unmeasuredCount,
      splitTest: p.splitTest,
      archivePerSample: p.archivePerSample,
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
      p.archivePerSample,
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

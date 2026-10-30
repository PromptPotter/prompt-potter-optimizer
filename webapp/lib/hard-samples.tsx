"use client";

import { createContext, useContext, useMemo, useState, type ReactNode } from "react";
import type {
  CellCandidate,
  CellRow,
  CellStatus,
  DatasetItem,
  HardSampleOrder,
  HardSamplesScope,
} from "@/lib/api";
import { useCycleStream } from "@/lib/poll";
import { useCells, type SeriesTotals } from "@/lib/hooks/useCells";
import type { ReadFailure } from "@/lib/hooks/useRead";
import type { CyclePath } from "@/lib/ids";
import { useWorkspace } from "@/lib/workspace";

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
  failure: ReadFailure | null;
}

const HardSamplesContext = createContext<HardSamples | null>(null);

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
      failure: p.failure,
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
      p.failure,
      rankedByPick,
      scope,
    ],
  );
  return <HardSamplesContext.Provider value={value}>{children}</HardSamplesContext.Provider>;
}

export function useHardSamples(): HardSamples {
  const v = useContext(HardSamplesContext);
  if (!v) throw new Error("useHardSamples must be used inside <HardSamplesProvider>");
  return v;
}

// IDENTITY only: an unset field is the viewed unit's, the shared roster's scope or dataset.
export interface CellsPreset {
  path?: CyclePath;
  datasetName?: string;
  candidateId?: string | undefined;
  round?: number;
  scope?: HardSamplesScope;
}

type PresetCells = Pick<
  HardSamples,
  | "datasetName"
  | "items"
  | "measuredCount"
  | "unmeasuredCount"
  | "candidates"
  | "cells"
  | "totals"
  | "rankedBy"
  | "stale"
  | "failure"
>;

const NO_PRESET: PresetCells = {
  datasetName: null,
  items: [],
  measuredCount: 0,
  unmeasuredCount: 0,
  candidates: [],
  cells: [],
  totals: null,
  rankedBy: null,
  stale: false,
  failure: null,
};

// Every list of measured cells reads here, so a pane and a host naming one preset share one flight.
export function usePresetCells(preset: CellsPreset | null, status?: CellStatus): PresetCells {
  const shared = useHardSamples();
  const { viewedPath } = useWorkspace();
  const { isLive } = useCycleStream();
  const scope = preset?.scope ?? shared.scope;
  const datasetName = preset?.datasetName ?? shared.datasetName;
  // Unfiltered on the viewed unit IS the shared roster — never a second fetch of it.
  const ownRead =
    preset !== null &&
    (preset.path !== undefined ||
      preset.datasetName !== undefined ||
      preset.candidateId !== undefined ||
      preset.round !== undefined ||
      status !== undefined ||
      scope !== shared.scope);
  const own = useCells(
    ownRead ? (preset.path ?? viewedPath) : null,
    datasetName,
    scope,
    shared.rankedByPick,
    // The dashboard's liveness is the VIEWED unit's; a preset on another one does not poll.
    preset?.path === undefined && isLive,
    { candidateId: preset?.candidateId, round: preset?.round, status },
  );
  if (preset === null) return NO_PRESET;
  if (!ownRead) return shared;
  return {
    datasetName,
    items: own.items,
    measuredCount: own.measuredCount,
    unmeasuredCount: own.unmeasuredCount,
    candidates: own.candidates,
    cells: own.cells,
    totals: own.totals,
    rankedBy: own.order,
    stale: own.isStale,
    failure: own.failure,
  };
}

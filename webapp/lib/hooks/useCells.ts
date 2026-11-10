import { cellsRead } from "../api/reads";
import type { CellStatus, CellsResponse, HardSampleOrder, HardSamplesScope } from "@/lib/api/types";
import { CELLS_LIVE_POLL_MS } from "../cadence";
import type { CyclePath } from "../ids";
import { useViewedDashboard } from "../poll";
import type { ReadResult } from "../read-cache";
import { createModuleStore } from "../state/module-store";
import { useMomentAt, useWorkspace } from "../workspace";
import { useLeafDatasetName } from "./useLeafDatasetName";
import { useRead } from "./useRead";

// The roster's controls. `rankedBy` moves on click, while every LABEL names the served `order`
// until the rows land; the optimizer's picker runs on the dataset scope regardless of `scope`.
interface CellsView {
  scope: HardSamplesScope;
  rankedBy: HardSampleOrder | null;
}

const view = createModuleStore<CellsView>({ scope: "campaign", rankedBy: null });

export const setCellsView = view.set;
export const useCellsView = view.useStore;

// IDENTITY only: an unset field is the viewed unit's, the roster's scope or dataset.
export interface CellsPreset {
  path?: CyclePath;
  datasetName?: string;
  candidateId?: string | undefined;
  round?: number;
  scope?: HardSamplesScope;
}

interface Cells {
  datasetName: string | null;
  // `CellRow.candidate` joins `CellCandidate.key`. Served chronologically; bucket, never re-sort.
  // Answered null where no slice is pooled yet. Consumers MUST render its failure: an empty roster
  // and a broken read spell the rows the same way.
  read: ReadResult<CellsResponse | null>;
}

// Every list of measured cells reads here: `useCells({})` is the viewed unit's roster, and a pane
// naming the same preset shares its read.
export function useCells(preset: CellsPreset | null, status?: CellStatus): Cells {
  const { viewed } = useWorkspace();
  const viewedPath = viewed?.path ?? null;
  const { scope, rankedBy } = useCellsView();
  const leafDatasetName = useLeafDatasetName();
  const path = preset?.path ?? viewedPath;
  const datasetName = preset?.datasetName ?? leafDatasetName;
  const at = useMomentAt(path);
  // The dashboard's liveness is the VIEWED unit's; a preset on another one does not poll.
  const { isLive } = useViewedDashboard();
  const intervalMs = preset?.path === undefined && isLive ? CELLS_LIVE_POLL_MS : null;
  const read = useRead(
    preset !== null && path && datasetName
      ? cellsRead(
          datasetName,
          path,
          preset.scope ?? scope,
          rankedBy,
          { candidateId: preset.candidateId, round: preset.round, status },
          at,
          intervalMs,
        )
      : null,
  );
  return { datasetName, read };
}

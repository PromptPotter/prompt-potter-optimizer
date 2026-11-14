import { useMemo } from "react";
import { searchpointCopyChoices, searchpointText } from "@/lib/derivations";
import type { CyclePath } from "@/lib/ids";
import { useCells } from "@/lib/hooks/useCells";
import { failureOf, shownData } from "@/lib/hooks/useRead";
import { useRound } from "@/lib/hooks/useRound";
import type { ReadFailure } from "@/lib/read-cache";

export interface SearchpointIdentity {
  path: CyclePath | null;
  round: number | null;
  arm: string | null;
  label: string;
  // The dataset its cells are filed under; the viewed leaf's when null.
  datasetName: string | null;
}

// The one copy of a searchpoint: a host names WHICH arm, and its spec, scores and cells are read
// here off the round read (`useRound`, the dashboard while the round is unfiled) and the cells read.
export function useSearchpointCopy({ path, round, arm, label, datasetName }: SearchpointIdentity): {
  choices: { key: string; label: string; data: unknown }[];
  cellsFailure: ReadFailure | null;
} {
  const { cfg: cfgOf, reading: readingOf } = useRound(path, round);
  const cellsRead = useCells(
    path && arm ? { path, candidateId: arm, scope: "cycle", ...(datasetName ? { datasetName } : {}) } : null,
  ).read;
  const cells = shownData(cellsRead)?.cells;
  return useMemo(() => {
    const cfg = cfgOf(arm, label);
    const text = cfg ? [{ key: "text", label: "Prompt + config as text", data: searchpointText(cfg) }] : [];
    return {
      choices: [...searchpointCopyChoices({ cfg, reading: readingOf(arm), cells: cells ?? [] }), ...text],
      cellsFailure: failureOf(cellsRead),
    };
  }, [cfgOf, readingOf, arm, label, cells, cellsRead]);
}

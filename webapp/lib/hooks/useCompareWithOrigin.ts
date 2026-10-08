"use client";
// Put the ORIGIN and the point being looked at on the Compare board, alone, and go there. The
// origin is the one the served tree names for the point's timeline (`origin_id`).

import { useCallback, useMemo } from "react";
import { candidateSubject, subjectKey } from "@/lib/api/reads";
import { useCompareSelection, type CompareChannel } from "@/lib/compare-selection";
import { candidateById, indexLineage, originOf, pathOf } from "@/lib/derivations";
import { encodeCyclePath, type CyclePath } from "@/lib/ids";
import { useLineageTree } from "@/lib/lineage";
import { useWorkspace } from "@/lib/workspace";

export interface CompareWithOrigin {
  /** `null` until the tree places both ends: a candidate still scoring has no lineage id. */
  run: (() => void) | null;
  originLabel: string | null;
  /** The pair as `/evidence` subjects, ORIGIN first; one entry while the point IS the origin,
   *  none until the tree places it. A stable identity per pair, so it can key a read. */
  subjects: readonly string[];
}

const NO_SUBJECTS: readonly string[] = [];

export function useCompareWithOrigin(
  path: CyclePath | null,
  // `null` reads the viewed branch itself, at its own head.
  candidateId: string | null,
): CompareWithOrigin {
  const { show } = useCompareSelection();
  const { setTab } = useWorkspace();
  const { root } = useLineageTree(path ?? [], path !== null && path.length > 0);
  const index = useMemo(() => indexLineage(root), [root]);

  const pair = useMemo(() => {
    const top = path?.[0];
    const leaf = path?.at(-1);
    if (!path || !top || !leaf) return null;
    const start =
      candidateId === null
        ? index.get(encodeCyclePath(path))?.candidates[0]
        : candidateById(index, candidateId);
    const origin = originOf(index, start ?? undefined);
    if (!start || !origin) return null;
    const originKey = candidateSubject(pathOf(origin), origin.id);
    const second =
      candidateId === null
        ? subjectKey("course", [leaf.campaignId, leaf.cycleId], path.slice(0, -1))
        : candidateSubject(pathOf(start), start.id);
    if (!originKey || !second) return null;
    const channels: CompareChannel[] = [{ rootCampaignId: top.campaignId, subject: originKey }];
    if (second !== originKey) channels.push({ rootCampaignId: top.campaignId, subject: second });
    return { channels, subjects: channels.map((c) => c.subject), originLabel: origin.label };
  }, [index, path, candidateId]);

  const run = useCallback(() => {
    if (!pair) return;
    show(pair.channels);
    setTab("compare");
  }, [pair, show, setTab]);

  return {
    run: pair ? run : null,
    originLabel: pair?.originLabel ?? null,
    subjects: pair?.subjects ?? NO_SUBJECTS,
  };
}

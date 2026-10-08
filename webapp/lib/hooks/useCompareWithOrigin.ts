"use client";
// Put the ORIGIN and the point being looked at on the Compare board, alone, and go there. The
// origin is found by walking `parent_ids[0]` on the one served tree, so a fork's point reads
// against the campaign root it descends from rather than against its own first round.

import { useCallback, useMemo } from "react";
import { candidateSubject, subjectKey } from "@/lib/api/reads";
import { useCompareSelection, type CompareChannel } from "@/lib/compare-selection";
import { indexLineage, pathOf } from "@/lib/derivations";
import type { LineageNode } from "@/lib/api";
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
    const nodes = new Map<string, LineageNode>();
    for (const { candidates } of index.values()) for (const c of candidates) nodes.set(c.id, c);
    const here = index.get(encodeCyclePath(path))?.candidates ?? [];
    const start = candidateId === null ? here[0] : nodes.get(candidateId);
    if (!start) return null;
    const seen = new Set<string>();
    let origin = start;
    for (;;) {
      seen.add(origin.id);
      const parentId = origin.parent_ids[0];
      const parent = parentId === undefined ? undefined : nodes.get(parentId);
      if (!parent || seen.has(parent.id)) break;
      origin = parent;
    }
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

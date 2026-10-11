"use client";

import { useCallback, useMemo } from "react";
import { candidateSubject } from "@/lib/api/reads";
import { useCompareSelection, type CompareChannel } from "@/lib/compare-selection";
import { armOfId, originAt, originOf, pathOf } from "@/lib/derivations";
import type { CyclePath } from "@/lib/ids";
import { useViewedLineage } from "@/lib/lineage";
import { useWorkspace } from "@/lib/workspace";

export interface CompareWithOrigin {
  run: (() => void) | null;
  originLabel: string | null;
  /** ORIGIN first; one entry while the point IS the origin. Stable per pair, so it can key a read. */
  subjects: readonly string[];
}

const NO_SUBJECTS: readonly string[] = [];

export function useCompareWithOrigin(
  path: CyclePath | null,
  // `null` names no point and reads the origin alone.
  candidateId: string | null,
): CompareWithOrigin {
  const { show } = useCompareSelection();
  const { openView } = useWorkspace();
  const { index } = useViewedLineage();

  const pair = useMemo(() => {
    const top = path?.[0];
    if (!top) return null;
    const point = candidateId === null ? null : armOfId(index, candidateId);
    if (candidateId !== null && !point) return null;
    const origin = point ? originOf(index, point) : originAt(index, path);
    const originKey = origin ? candidateSubject(pathOf(origin), origin.id) : "";
    if (!origin || !originKey) return null;
    const channels: CompareChannel[] = [{ rootCampaignId: top.campaignId, subject: originKey }];
    const pointKey = point ? candidateSubject(pathOf(point), point.id) : "";
    if (pointKey && pointKey !== originKey) {
      channels.push({ rootCampaignId: top.campaignId, subject: pointKey });
    }
    return { channels, subjects: channels.map((c) => c.subject), originLabel: origin.label };
  }, [index, path, candidateId]);

  const run = useCallback(() => {
    if (!pair) return;
    show(pair.channels);
    openView("compare");
  }, [pair, show, openView]);

  return {
    run: pair ? run : null,
    originLabel: pair?.originLabel ?? null,
    subjects: pair?.subjects ?? NO_SUBJECTS,
  };
}

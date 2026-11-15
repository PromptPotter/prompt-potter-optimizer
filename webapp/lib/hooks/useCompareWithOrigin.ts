import { useCallback, useMemo } from "react";
import { candidateSubject } from "@/lib/api/reads";
import { useCompareSelection, type CompareChannel } from "@/lib/state/compare-selection";
import { armAt, originAt, originOf } from "@/lib/derivations";
import type { CyclePath } from "@/lib/ids";
import { useViewedLineage } from "@/lib/state/lineage";
import { useWorkspace } from "@/lib/state/workspace";

interface CompareWithOrigin {
  run: (() => void) | null;
  originLabel: string | null;
  /** ORIGIN first; one entry while the point IS the origin. Stable per pair, so it can key a read. */
  subjects: readonly string[];
}

const NO_SUBJECTS: readonly string[] = [];

export function useCompareWithOrigin(
  path: CyclePath | null,
  // The served `reading.arm.key` at `path`; `null` names no point and reads the origin alone.
  arm: string | null,
): CompareWithOrigin {
  const { show } = useCompareSelection();
  const { open } = useWorkspace();
  const { index } = useViewedLineage();

  const pair = useMemo(() => {
    const top = path?.[0];
    if (!top) return null;
    const placed = arm === null ? null : armAt(index, path, arm);
    // A refused proposal measured no individual: there is no point to compare.
    const point = placed?.facts.reading.end === "invalid" ? null : placed;
    if (arm !== null && !point) return null;
    const origin = point ? originOf(index, point) : originAt(index, path);
    if (!origin) return null;
    const originKey = candidateSubject(origin.address);
    const channels: CompareChannel[] = [{ rootCampaignId: top.campaignId, subject: originKey }];
    const pointKey = point ? candidateSubject(point.address) : "";
    if (pointKey && pointKey !== originKey) {
      channels.push({ rootCampaignId: top.campaignId, subject: pointKey });
    }
    return { channels, subjects: channels.map((c) => c.subject), originLabel: origin.label };
  }, [index, path, arm]);

  const run = useCallback(() => {
    if (!pair) return;
    show(pair.channels);
    open("compare");
  }, [pair, show, open]);

  return {
    run: pair ? run : null,
    originLabel: pair?.originLabel ?? null,
    subjects: pair?.subjects ?? NO_SUBJECTS,
  };
}

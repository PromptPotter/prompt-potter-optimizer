import type { Evidence, PairwiseComparison, SubjectReading } from "@/lib/api/types";
import { armAt, individualOf, type ObservePoint } from "@/lib/derivations";
import { useViewedLineage } from "@/lib/lineage";
import { useViewedDashboard } from "@/lib/poll";
import { useWorkspace } from "@/lib/workspace";
import { useCompareWithOrigin } from "./useCompareWithOrigin";
import { useEvidence, type EvidenceAsk } from "./useEvidence";
import { readyData } from "./useRead";
import type { ReadResult } from "@/lib/read-cache";

const WITH_CONFIG: EvidenceAsk = { config: true };

// `compared` with no keys is the ONLY reading that says "identical"; null is not read yet (the
// read's own status says whether it is loading or failed).
export type OriginChanges = { status: "unrecorded" } | { status: "compared"; keys: string[] } | null;

export interface OriginEvidence {
  origin: SubjectReading | null;
  // Null while the point IS the origin, or the tree has not placed it yet.
  shown: SubjectReading | null;
  vsOrigin: PairwiseComparison | null;
  read: ReadResult<Evidence>;
  changes: OriginChanges;
  compare: (() => void) | null;
}

function recorded(subject: SubjectReading | null): boolean {
  return !!subject?.config && Object.keys(subject.config).length > 0;
}

function changesOf(
  evidence: Evidence | null,
  origin: SubjectReading | null,
  // `undefined`: the point IS the origin.
  shown: SubjectReading | null | undefined,
): OriginChanges {
  if (!evidence?.config_keys) return null;
  if (!recorded(origin) || (shown !== undefined && !recorded(shown))) {
    return { status: "unrecorded" };
  }
  if (shown === undefined) return { status: "compared", keys: [] };
  const { differs, one_sided: oneSided } = evidence.config_keys;
  return { status: "compared", keys: [...differs, ...oneSided] };
}

export function useOriginEvidence(point: ObservePoint | null): OriginEvidence {
  const { viewed } = useWorkspace();
  const viewedPath = viewed?.path ?? null;
  const { index } = useViewedLineage();
  const { dash } = useViewedDashboard();
  const placed = point ? armAt(index, viewedPath, point.key) : null;
  const pair = useCompareWithOrigin(viewedPath, placed ? placed.facts.reading.arm.key : null);
  const read = useEvidence(pair.subjects, dash?.producer.attached === true, WITH_CONFIG);
  const evidence = readyData(read);

  const [originKey, shownKey] = pair.subjects;
  const subject = (key: string | undefined) =>
    key === undefined ? null : (evidence?.subjects.find((s) => s.key === key) ?? null);
  const vsOrigin =
    shownKey === undefined
      ? undefined
      : evidence?.metric.pairwise.find((p) => p.subject_a === originKey && p.subject_b === shownKey);
  const origin = subject(originKey);
  const shown = subject(shownKey);
  return {
    origin,
    shown,
    vsOrigin: vsOrigin ?? null,
    read,
    // A point the tree has not placed reads the origin alone, which is no comparison yet.
    changes:
      point !== null && placed === null
        ? null
        : placed !== null && individualOf(index, placed) === null
          ? { status: "unrecorded" }
          : changesOf(evidence, origin, shownKey === undefined ? undefined : shown),
    compare: pair.run,
  };
}

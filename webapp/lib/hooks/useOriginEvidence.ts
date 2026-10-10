"use client";

import { useEffect } from "react";
import type { Evidence, PairwiseComparison, SubjectReading } from "@/lib/api/types";
import { invalidateReads } from "@/lib/read-cache";
import { armAt, type ObservePoint } from "@/lib/derivations";
import { useViewedLineage } from "@/lib/lineage";
import { useCycleStream } from "@/lib/poll";
import { useWorkspace } from "@/lib/workspace";
import { useCompareWithOrigin } from "./useCompareWithOrigin";
import { useEvidence, type EvidenceAsk } from "./useEvidence";

const LIVE_EVIDENCE_MS = 30000;

const WITH_CONFIG: EvidenceAsk = { config: true };

// `compared` with no keys is the ONLY reading that says "identical".
export type OriginChanges =
  | { status: "pending" }
  | { status: "failed"; message: string }
  | { status: "unrecorded" }
  | { status: "compared"; keys: string[] };

export interface OriginEvidence {
  origin: SubjectReading | null;
  // Null while the point IS the origin, or the tree has not placed it yet.
  shown: SubjectReading | null;
  vsOrigin: PairwiseComparison | null;
  changes: OriginChanges;
  compare: (() => void) | null;
}

function recorded(subject: SubjectReading | null): boolean {
  return !!subject?.config && Object.keys(subject.config).length > 0;
}

function changesOf(
  evidence: Evidence | null,
  error: string | null,
  origin: SubjectReading | null,
  // `undefined`: the point IS the origin.
  shown: SubjectReading | null | undefined,
): OriginChanges {
  if (error) return { status: "failed", message: error };
  if (!evidence?.config_keys) return { status: "pending" };
  if (!recorded(origin) || (shown !== undefined && !recorded(shown))) {
    return { status: "unrecorded" };
  }
  if (shown === undefined) return { status: "compared", keys: [] };
  const { differs, one_sided: oneSided } = evidence.config_keys;
  return { status: "compared", keys: [...differs, ...oneSided] };
}

export function useOriginEvidence(point: ObservePoint | null): OriginEvidence {
  const { viewedPath } = useWorkspace();
  const { index } = useViewedLineage();
  const { dash, isLive } = useCycleStream();
  const placed = point ? armAt(index, viewedPath, point) : null;
  const pair = useCompareWithOrigin(viewedPath, placed?.id ?? null);
  const { evidence, error } = useEvidence(
    pair.subjects,
    isLive ? LIVE_EVIDENCE_MS : null,
    WITH_CONFIG,
  );
  // A closed round and a newly scored cell re-ask at once rather than on the slow beat.
  const rounds = dash?.rounds.length ?? 0;
  const cells = point?.row?.reading.panel.scored ?? 0;
  useEffect(() => {
    if (isLive) invalidateReads("evidence");
  }, [isLive, rounds, cells]);

  const [originKey, shownKey] = pair.subjects;
  const read = (key: string | undefined) =>
    key === undefined ? null : (evidence?.subjects.find((s) => s.key === key) ?? null);
  const vsOrigin =
    shownKey === undefined
      ? undefined
      : evidence?.metric.pairwise.find((p) => p.subject_a === originKey && p.subject_b === shownKey);
  const origin = read(originKey);
  const shown = read(shownKey);
  return {
    origin,
    shown,
    vsOrigin: vsOrigin ?? null,
    // A point the tree has not placed reads the origin alone, which is no comparison yet.
    changes:
      point !== null && placed === null
        ? { status: "pending" }
        : changesOf(evidence, error, origin, shownKey === undefined ? undefined : shown),
    compare: pair.run,
  };
}
